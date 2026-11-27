"""clin v1 头 → v2 头（11 列定序）+ 空病理列的重建迁移。

设计要点（与 migrate_id_v2.py 同一套路）
- `ALTER TABLE ADD COLUMN` 只能追加到表尾，v2 要求头 11 列逐位对齐，因此只能
  「CREATE TABLE clin__v2 → INSERT … SELECT → DROP/RENAME → 重建索引」。
- 默认 dry-run：原库只读（`mode=ro`）做体检，并在**副本**上把整套迁移真跑一遍
  （CREATE/INSERT/索引/meta/提交后体检全走），副本写在 DEL 下、跑完即删，原库
  一个字节都不动；`--no-rehearse` 只体检不演练。`--apply` 才写原库，写盘前
  `shutil.copy2` 备份并校验 sha256，落盘后逐列核对，任一项不符则回滚；已提交
  但事后体检不过的，用备份覆盖回去并记 `failed_restored`。
- 列序：头 11 列（V2_HEAD）→ 旧头里 v2 未收录的 3 个时点列（month_ix /
  period_source / period_origin，原序保留，属「旧列不删」）→ 原有变量列按原顺序
  原名保留（不改名、不合并不删列）→ 表尾 15 个空病理列（只建列不填值）。
  `group_v` / `subgroup` 留 NULL；`schema_block` 每行写 'clin-v2'。
- `period_origin` 处理：**保留为列**（信息保真优先），并在 meta 记
  `clin_v2_legacy_cols` 说明它不在 v2 头内。选择保留而不是「记 meta 后丢弃」的
  理由：① 80 库中 5052 行的值（'0'）不在枚举内，丢弃等于消掉唯一证据；
  ② `month_ix` 是 `ix_clin_month` 索引与被 D29 判读的列，丢弃会连带删索引；
  ③ 规则本轮明确「不删列」。
- 约束：头列 NOT NULL 按 v2 口径（family_uid / period_month / merged_n /
  unk_seq）；变量列与 legacy 列沿用原 NOT NULL / DEFAULT。v1 的 CHECK 只在该库
  全部行都通过时才重建（逐库实测），否则跳过并记 warning —— 这样既不把脏数据
  洗白，也不因旧约束把可迁移库判死。
- 索引：迁移前从 `sqlite_master` 全量 dump `type='index' and sql is not null` 的
  DDL，换名后原样执行；`sqlite_autoindex_*`（PK 自建）不重建。触发器按同样方式
  dump/重建；视图只记录（DROP TABLE 后同名重建即可让视图重新可解析）。

用法
  python -B migrate_clin_v2.py                     # dry-run，全库
  python -B migrate_clin_v2.py --family EC         # 只体检 EC
  python -B migrate_clin_v2.py --json out.json     # dry-run 明细
  python -B migrate_clin_v2.py --apply             # 真的写盘（本轮不要跑）
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

V2_HEAD = ['family_uid', 'observed_on', 'period_month', 'cohort', 'group_v',
           'subgroup', 'src_id', 'src_row', 'merged_n', 'unk_seq', 'schema_block']
V1_HEAD = ['family_uid', 'period_month', 'month_ix', 'period_source', 'period_origin',
           'unk_seq', 'observed_on', 'merged_n', 'src_id', 'src_row', 'cohort']
V1_H10 = [c for c in V1_HEAD if c != 'period_origin']
# v2 头未收录、但本轮保留在头之后的旧时点列（原列序）
LEGACY = ['month_ix', 'period_source', 'period_origin']
# 本轮只建列不填值；类型按规则：path_on=DATE，计数列 INTEGER，其余 TEXT
PATH_COLS = [('path_kind', 'TEXT'), ('path_on', 'DATE'), ('path_histology', 'TEXT'),
             ('path_grade', 'TEXT'), ('path_mi', 'TEXT'), ('path_mi_mm', 'INTEGER'),
             ('path_lvsi', 'TEXT'), ('path_csi', 'TEXT'), ('path_ln_pos', 'INTEGER'),
             ('path_ln_total', 'INTEGER'), ('path_lnm', 'TEXT'), ('path_eu', 'TEXT'),
             ('path_figo', 'TEXT'), ('path_dx', 'TEXT'), ('path_other', 'TEXT')]
HEAD_DEF = {
    'family_uid': 'TEXT NOT NULL',
    'observed_on': 'TEXT',
    'period_month': 'TEXT NOT NULL',
    'cohort': 'TEXT',
    'group_v': 'TEXT',
    'subgroup': 'TEXT',
    'src_id': 'INTEGER',
    'src_row': 'INTEGER',
    'merged_n': 'INTEGER NOT NULL DEFAULT 1',
    'unk_seq': 'INTEGER NOT NULL DEFAULT 0',
    'schema_block': "TEXT NOT NULL DEFAULT 'clin-v2'",
}
# 只有新列：插入时不从源表取
NEW_ONLY = {'group_v', 'subgroup'}
# 旧 v1 的 CHECK 语义（逐条实测，通过的才重建）
CHECKS = [
    ('pm_fmt', "period_month='UNK' OR period_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'"),
    ('ps_enum', "period_source IN ('exact','approx','unknown')"),
    ('origin_enum', "period_origin IS NULL OR period_origin IN "
                    "('exam_date','surgery_date','admission_date','report_date','derived','unknown')"),
    ('unk_time', "period_month<>'UNK' OR (month_ix IS NULL AND period_source='unknown')"),
    ('dated_has_month_ix', "period_month='UNK' OR month_ix IS NOT NULL"),
    ('unk_seq_ge0', "period_month<>'UNK' OR unk_seq>=0"),
    ('dated_unk_seq0', "period_month='UNK' OR unk_seq=0"),
]


def _qi(name):
    """SQL 标识符加引号：中文 / 大写 / 空格列名不加引号会被当成表达式。"""
    return '"%s"' % str(name).replace('"', '""')


def default_root():
    return Path(os.environ.get('DB5T_ROOT') or r'D:/MyProjects/_DataBase')


def proj_dbs(root):
    """_DataBase/<FAM>/<slug>/<slug>db；排除家族库 _PHI。"""
    out = []
    for p in sorted(root.glob('*/*/*db')):
        if p.is_file() and p.parent.name != '_PHI':
            out.append(p)
    return out


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(chunk), b''):
            h.update(b)
    return h.hexdigest()


def cols_of(con, table):
    return [r[1] for r in con.execute('PRAGMA table_info(%s)' % _qi(table))]


def table_info(con, table):
    return list(con.execute('PRAGMA table_info(%s)' % _qi(table)))


def table_names(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def fk_defs(con, tables):
    """按 PRAGMA foreign_key_list 重建 FK 子句（引用表不存在则整条丢并回报）。"""
    groups = {}
    for r in con.execute('PRAGMA foreign_key_list(%s)' % _qi('clin')):
        groups.setdefault(r[0], []).append(r)
    defs, dropped = [], []
    for _id, rows in groups.items():
        tab = rows[0][2]
        if tab not in tables:
            dropped.append(tab)
            continue
        cols = [r[3] for r in rows]
        refs = [r[4] for r in rows]
        s = 'FOREIGN KEY (%s) REFERENCES %s (%s)' % (
            ', '.join(_qi(c) for c in cols), _qi(tab), ', '.join(_qi(c) for c in refs))
        od, ou = rows[0][6], rows[0][5]
        if od and od != 'NO ACTION':
            s += ' ON DELETE ' + od
        if ou and ou != 'NO ACTION':
            s += ' ON UPDATE ' + ou
        defs.append(s)
    return defs, dropped


def coldef(name, typ, notnull, dflt):
    s = _qi(name) + (' ' + typ if typ else '')
    if notnull:
        s += ' NOT NULL'
    if dflt is not None:
        s += ' DEFAULT ' + dflt
    return s


def build_ddl(info, legacy, vars_, checks_pass, fks):
    lines = []
    for c in V2_HEAD:
        lines.append('  ' + _qi(c) + ' ' + HEAD_DEF[c])
    ti = info['type'], info['notnull'], info['dflt']
    for c in legacy + vars_:
        i = info['order'].index(c)
        lines.append('  ' + coldef(c, ti[0][i], ti[1][i], ti[2][i]))
    for c, t in PATH_COLS:
        lines.append('  ' + _qi(c) + ' ' + t)
    lines.append('  PRIMARY KEY (%s)' % ', '.join(_qi(c) for c in
                 ('family_uid', 'period_month', 'unk_seq')))
    for _n, expr in checks_pass:
        lines.append('  CHECK (' + expr + ')')
    lines.extend('  ' + f for f in fks)
    return 'CREATE TABLE clin__v2 (\n' + ',\n'.join(lines) + '\n)'


def inspect(path):
    """只读体检。返回 (state, info, blockers, warnings)。state ∈ v2 / v1 / other。"""
    con = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True)
    blocks, warns = [], []
    try:
        tabs = table_names(con)
        if 'clin' not in tabs:
            return 'other', {'has_clin': False}, ['无 clin 表'], warns
        ti = table_info(con, 'clin')
        cols = [r[1] for r in ti]
        info = {
            'has_clin': True,
            'cols': cols,
            'rows': con.execute('SELECT count(*) FROM clin').fetchone()[0],
            'order': cols,
            'type': [(r[2] or '') for r in ti],
            'notnull': [bool(r[3]) for r in ti],
            'dflt': [r[4] for r in ti],
            'pk': [r[1] for r in ti if r[5]],
            'integrity': con.execute('PRAGMA integrity_check').fetchone()[0],
            'idx': [(r[0], r[1]) for r in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='clin'")],
            'trg': [(r[0], r[1]) for r in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='clin'")],
            'views': [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='view'")],
            'meta': 'meta' in tabs,
        }
        info['views'] = [n for n in info['views']
                         if re.search(r'\bclin\b', con.execute(
                             "SELECT sql FROM sqlite_master WHERE name=?", (n,)
                         ).fetchone()[0] or '')]
        old_low = [c.lower() for c in cols]
        info['legacy'] = [c for c in LEGACY if c in cols]
        info['vars'] = [c for c in cols if c not in V1_HEAD and c not in LEGACY]
        info['carry'] = len(cols)  # 本轮不丢任何旧列
        info['target'] = V2_HEAD + info['legacy'] + info['vars'] + [c for c, _ in PATH_COLS]
        info['n_new'] = len(info['target'])
        info['head_kind'] = ('v2' if cols[:11] == V2_HEAD else
                             'v1' if cols[:11] == V1_HEAD else
                             'v1-h10' if cols[:10] == V1_H10 else 'other')
        info['has_clin_v2'] = 'clin__v2' in tabs
        # 冲突：本轮新加的列（group_v/subgroup/schema_block/path_*）若已被旧表占用，无解
        for n in list(NEW_ONLY) + ['schema_block'] + [c for c, _ in PATH_COLS]:
            if n.lower() in old_low:
                blocks.append('列名冲突：新列 %s 已被旧表占用' % n)
        dup = sorted({c for c in old_low if old_low.count(c) > 1})
        if dup:
            blocks.append('旧表列名大小写重复：%s' % dup)
        if info['has_clin_v2']:
            blocks.append('残留表 clin__v2')
        if info['head_kind'] == 'other':
            blocks.append('旧头 11 列非 v1/v1-h10：%s' % cols[:11])
        if not info['meta']:
            blocks.append('缺 meta 表')
    finally:
        con.close()
    return info.get('head_kind', 'other'), info, blocks, warns


def precheck(path, info):
    """迁移前置数据体检；返回 (blockers, warnings, checks_pass)。"""
    con = sqlite3.connect('file:%s?mode=ro' % path.as_posix(), uri=True)
    blocks, warns, checks_pass = [], [], []
    try:
        cols = set(info['cols'])
        # 1) 新表 NOT NULL 列现有的空值数
        req = ['family_uid', 'period_month', 'merged_n', 'unk_seq']
        req += [c for c in info['legacy'] + info['vars']
                if info['notnull'][info['order'].index(c)]]
        for c in req:
            n = con.execute('SELECT count(*) FROM clin WHERE %s IS NULL' % _qi(c)).fetchone()[0]
            if n:
                blocks.append('%s 空值 %d 行（新表 NOT NULL）' % (c, n))
        # 2) PK 唯一（不要用别名做 having：旧表可能真有一列叫 c/n）
        dup = con.execute('SELECT count(*) FROM (SELECT family_uid, period_month, unk_seq '
                          'FROM clin GROUP BY 1,2,3 HAVING count(*)>1)').fetchone()[0]
        if dup:
            blocks.append('(family_uid, period_month, unk_seq) 重复 %d 组' % dup)
        # 3) 外键孤儿
        if info['integrity'] != 'ok':
            blocks.append('当前 integrity_check=%s' % info['integrity'])
        tabs = table_names(con)
        if 'person' in tabs:
            n = con.execute('SELECT count(*) FROM clin c LEFT JOIN person p '
                            'USING(family_uid) WHERE p.family_uid IS NULL').fetchone()[0]
            if n:
                blocks.append('family_uid 不在 person：%d 行' % n)
        else:
            warns.append('无 person 表，family_uid 外键无法核')
        if 'src' in tabs and 'src_id' in cols:
            n = con.execute('SELECT count(*) FROM clin c LEFT JOIN src s USING(src_id) '
                            'WHERE c.src_id IS NOT NULL AND s.src_id IS NULL').fetchone()[0]
            if n:
                blocks.append('src_id 孤儿：%d 行' % n)
        # 4) v1 CHECK 语义：通过的重建，不通过的跳过并记 warning
        for name, expr in CHECKS:
            if name.startswith(('pm_', 'ps_')) and not {'period_month', 'period_source'} <= cols:
                continue
            if name.startswith('origin') and 'period_origin' not in cols:
                continue
            if 'month_ix' in expr and 'month_ix' not in cols:
                continue
            n = con.execute('SELECT count(*) FROM clin WHERE NOT (%s)' % expr).fetchone()[0]
            if n:
                warns.append('v1 CHECK %s 未通过 %d 行 → 不重建该约束' % (name, n))
            else:
                checks_pass.append((name, expr))
        checks_pass.append(('schema_block', "schema_block='clin-v2'"))
        # 5) 辅助：旧头列是否全在
        miss = [c for c in V1_H10 if c not in cols]
        if miss:
            blocks.append('旧头缺列 %s' % miss)
    finally:
        con.close()
    return blocks, warns, checks_pass


def rehearse(path, info, checks_pass, base, tmpdir):
    """在副本上真跑一遍迁移（DDL/INSERT/索引/meta/体检全走），原库只读不改。"""
    tmpdir.mkdir(parents=True, exist_ok=True)
    tmp = tmpdir / (base['family'] + '__' + base['slug'] + '.db')
    try:
        shutil.copy2(path, tmp)
        b = dict(base)
        b['warnings'] = list(base.get('warnings') or [])
        r = do_migrate(tmp, info, checks_pass, tmpdir, b, make_backup=False, quiet=True)
        r['rehearsal'] = True
        if r.get('action') != 'migrated':
            r['action'] = 'rehearsal-failed'
        return r
    finally:
        for suf in ('', '-journal', '-wal', '-shm'):
            p = Path(str(tmp) + suf)
            if p.exists():
                p.unlink()


def run_one(path, backup_dir, apply_, rehearse_dir=None):
    fam, slug = path.parent.parent.name, path.parent.name
    tag = '%s/%s' % (fam, slug)
    state, info, blocks, warns = inspect(path)
    if not info.get('has_clin'):
        return {'family': fam, 'slug': slug, 'path': str(path), 'action': 'skip',
                'note': '无 clin 表'}
    if state == 'v2':
        return {'family': fam, 'slug': slug, 'path': str(path), 'action': 'skip',
                'note': '已是 clin v2 头', 'rows': info.get('rows')}
    if state == 'other':
        return {'family': fam, 'slug': slug, 'path': str(path), 'action': 'blocked',
                'blockers': blocks, 'warnings': warns}
    b2, w2, checks_pass = precheck(path, info)
    blocks += b2
    warns += w2
    base = {'family': fam, 'slug': slug, 'path': str(path), 'rows': info['rows'],
            'cols_old': len(info['cols']), 'cols_new': info['n_new'],
            'carry_cols': info['carry'], 'head_kind': info['head_kind'],
            'integrity': info['integrity'], 'legacy': info['legacy'],
            'n_vars': len(info['vars']), 'indexes': len([1 for _n, s in info['idx'] if s]),
            'triggers': len(info['trg']), 'views': len(info['views']),
            'checks_kept': [n for n, _ in checks_pass],
            'warnings': warns,
            'target_cols': info['target']}
    if blocks:
        base.update(action='blocked', blockers=blocks)
        print('%-34s !! 阻塞 %s' % (tag, '；'.join(blocks)[:160]))
        return base
    if not apply_:
        base['action'] = 'dry-run'
        if rehearse_dir is not None:
            rr = rehearse(path, info, checks_pass, base, rehearse_dir)
            base['rehearsal'] = rr.get('action')
            base['checks_kept'] = rr.get('checks_kept') or base['checks_kept']
            base['warnings'] += [w for w in rr.get('warnings') or []
                                 if w not in base['warnings']]
            if rr.get('action') != 'migrated':
                base['action'] = 'blocked'
                base['blockers'] = ['副本演练失败：%s' % rr.get('error')]
                print('%-34s !! 演练失败：%s' % (tag, rr.get('error')))
                return base
        print('%-34s dry-run OK  %6d 行 %3d→%3d 列 搬 %d 列 头=%s idx=%d trg=%d view=%d%s'
              % (tag, info['rows'], len(info['cols']), info['n_new'], info['carry'],
                 info['head_kind'], base['indexes'], base['triggers'], base['views'],
                 ('  warn=' + ';'.join(base['warnings'])[:80]) if base['warnings'] else ''))
        return base
    return do_migrate(path, info, checks_pass, backup_dir, base)


def do_migrate(path, info, checks_pass, backup_dir, base, make_backup=True, quiet=False):
    tag = '%s/%s' % (base['family'], base['slug'])
    bak, bak_sha = None, None
    if make_backup:
        backup_dir.mkdir(parents=True, exist_ok=True)
        bak = backup_dir / (base['family'] + '__' + base['slug'] + 'db')
        if not bak.exists():
            shutil.copy2(path, bak)
        bak_sha = sha256(bak)
        if bak_sha != sha256(path):
            raise RuntimeError('备份与库不一致：%s' % bak)

    con = sqlite3.connect(str(path), isolation_level=None)
    committed = False
    try:
        fks, fk_dropped = fk_defs(con, table_names(con))
        if fk_dropped:
            base['warnings'].append('引用表缺失，未重建 FK：%s' % fk_dropped)
        ddl = build_ddl(info, info['legacy'], info['vars'], checks_pass, fks)
        con.execute('PRAGMA foreign_keys=OFF')          # 必须在 BEGIN 之前
        con.execute('BEGIN IMMEDIATE')
        con.execute(ddl)
        carry = [c for c in info['cols']]               # 旧列全搬
        ins_cols = [c for c in info['target'] if c in set(info['cols'])]
        sel = ', '.join(_qi(c) for c in ins_cols)
        lit = ", 'clin-v2'" if 'schema_block' not in ins_cols else ''
        n_before = con.execute('SELECT count(*) FROM clin').fetchone()[0]
        con.execute('INSERT INTO clin__v2 (%s%s) SELECT %s%s FROM clin'
                    % (', '.join(_qi(c) for c in ins_cols),
                       ', ' + _qi('schema_block') if 'schema_block' not in ins_cols else '',
                       sel, lit))
        n_after = con.execute('SELECT count(*) FROM clin__v2').fetchone()[0]
        if n_after != n_before:
            raise RuntimeError('行数不符 %d → %d' % (n_before, n_after))
        # 逐列非空数（搬的全部列）+ 新列必须全空
        bad = []
        for c in ins_cols:
            a = con.execute('SELECT count(%s) FROM clin' % _qi(c)).fetchone()[0]
            b = con.execute('SELECT count(%s) FROM clin__v2' % _qi(c)).fetchone()[0]
            if a != b:
                bad.append((c, a, b))
        if bad:
            raise RuntimeError('非空数不符：%s' % bad[:6])
        for c in ('group_v', 'subgroup') + tuple(c for c, _ in PATH_COLS):
            n = con.execute('SELECT count(%s) FROM clin__v2' % _qi(c)).fetchone()[0]
            if n:
                raise RuntimeError('新列 %s 应为空，实有 %d' % (c, n))
        sb = con.execute("SELECT count(*) FROM clin__v2 WHERE schema_block<>'clin-v2'"
                         ).fetchone()[0]
        if sb:
            raise RuntimeError('schema_block 非 clin-v2：%d 行' % sb)
        # 换名 + 索引/触发器
        idxs = [s for _n, s in info['idx'] if s]
        trgs = [s for _n, s in info['trg'] if s]
        con.execute('DROP TABLE clin')
        con.execute('ALTER TABLE clin__v2 RENAME TO clin')
        for s in idxs:
            m = re.match(r'CREATE\s+(UNIQUE\s+)?INDEX\s+(IF\s+NOT\s+EXISTS\s+)?'
                         r'("[^"]+"|\S+)\s', s, re.I)
            if m:
                con.execute('DROP INDEX IF EXISTS ' + m.group(3))
            con.execute(s)
        for s in trgs:
            con.execute(s)
        # meta
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        kv = [('clin_schema', 'v2'), ('clin_v2_migrated_at', now),
              ('clin_v2_legacy_cols', ','.join(info['legacy'])),
              ('clin_v2_prev_cols_n', str(len(info['cols']))),
              ('clin_v2_cols_n', str(info['n_new']))]
        for k, v in kv:
            con.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', (k, v))
        if con.execute("SELECT 1 FROM meta WHERE k='clin_cols_n'").fetchone():
            con.execute("UPDATE meta SET v=? WHERE k='clin_cols_n'", (str(info['n_new']),))
        con.execute('COMMIT')
        committed = True
        integ = con.execute('PRAGMA integrity_check').fetchone()[0]
        fkv = con.execute('PRAGMA foreign_key_check').fetchall()
        cur = cols_of(con, 'clin')
        n_fin = con.execute('SELECT count(*) FROM clin').fetchone()[0]
        ok = (cur == info['target'] and integ == 'ok' and not fkv and n_fin == n_before)
        base.update(action='migrated' if ok else 'failed', rows=n_fin,
                    cols_new=len(cur), integrity=integ, fk_violation=len(fkv),
                    backup=str(bak), backup_sha256=bak_sha,
                    migrated_at=now, checks_kept=[n for n, _ in checks_pass])
        if not ok:
            raise RuntimeError('事后体检不过：cols=%d/%d integrity=%s fk=%d rows=%d/%d'
                               % (len(cur), len(info['target']), integ, len(fkv),
                                  n_fin, n_before))
        if not quiet:
            print('%-34s OK  %6d 行 %3d 列，integrity=%s fk=%d，备份 %s'
                  % (tag, n_fin, len(cur), integ, len(fkv), (bak_sha or '-')[:12]))
        return base
    except Exception as e:
        if not committed:
            try:
                con.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            base.update(action='error', error=str(e))
            if not quiet:
                print('%-34s !! 失败已回滚：%s' % (tag, e))
            return base
        # 已提交但事后体检失败：有备份就用备份覆盖回去（副本演练没有备份，直接报错）
        con.close()
        con = None
        if bak is not None:
            shutil.copy2(bak, path)
            ok_sha = sha256(path) == bak_sha
            base.update(action='failed_restored', error=str(e), restored_sha256=ok_sha)
            if not quiet:
                print('%-34s !! 已提交但体检不过，已用备份覆盖（sha256 一致=%s）：%s'
                      % (tag, ok_sha, e))
        else:
            base.update(action='failed', error=str(e))
            if not quiet:
                print('%-34s !! 已提交但体检不过（无备份可回滚）：%s' % (tag, e))
        return base
    finally:
        if con is not None:
            try:
                con.execute('PRAGMA foreign_keys=ON')
            except sqlite3.Error:
                pass
            con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=None)
    ap.add_argument('--family', action='append', default=None)
    ap.add_argument('--apply', action='store_true', help='真的写盘；缺省 dry-run')
    ap.add_argument('--backup-dir', default=None)
    ap.add_argument('--rehearse-dir', default=None,
                    help='dry-run 在副本上真跑一遍迁移；副本写到该目录（跑完即删）')
    ap.add_argument('--no-rehearse', action='store_true', help='只体检，不跑副本演练')
    ap.add_argument('--json', default=None)
    a = ap.parse_args(argv)

    root = Path(a.root) if a.root else default_root()
    dbs = proj_dbs(root)
    if a.family:
        want = {f.upper() for f in a.family}
        dbs = [p for p in dbs if p.parent.parent.name.upper() in want]
    if not dbs:
        print('没找到子库：%s/*/*/*db' % root)
        return 2
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    bdir = Path(a.backup_dir) if a.backup_dir else (
        Path(r'D:/MyProjects/.Del/_by-function/db5t-clin-v2-20261127')
        / ('clin_v2_backup_' + stamp))
    rdir = None
    if not a.apply and not a.no_rehearse:
        rdir = Path(a.rehearse_dir) if a.rehearse_dir else (
            Path(r'D:/MyProjects/.Del/_by-function/db5t-clin-v2-20261127')
            / ('rehearse_' + stamp))

    print('== %s == %d 个子库；备份目录 %s；演练副本 %s'
          % ('写盘' if a.apply else 'dry-run', len(dbs),
             bdir if a.apply else '(dry-run 不备份)', rdir or '无'))
    res = [run_one(p, bdir, a.apply, rdir) for p in dbs]
    with_clin = [r for r in res if r.get('rows') is not None or r.get('blockers')]
    migr = [r for r in res if r['action'] == 'dry-run']
    done = [r for r in res if r['action'] == 'migrated']
    blk = [r for r in res if r['action'] in ('blocked', 'error', 'failed_restored')]
    skip = [r for r in res if r['action'] == 'skip']
    summary = {
        'root': str(root),
        'apply': a.apply,
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'dbs_total': len(res),
        'dbs_with_clin': len([r for r in res if 'rows' in r]),
        'rows_total': sum(r.get('rows') or 0 for r in res if 'rows' in r),
        'migratable': len(migr) + len(done),
        'migrated': len(done),
        'blocked': len(blk),
        'skipped': len(skip),
        'max_cols_old': max([r.get('cols_old', 0) for r in res] or [0]),
        'max_cols_new': max([r.get('cols_new', 0) for r in res] or [0]),
        'max_cols_db': (max([r for r in res if 'cols_new' in r],
                            key=lambda r: r['cols_new'], default={})
                        .get('slug')),
    }
    print('\n合计：可迁移 %d；已迁移 %d；阻塞/异常 %d；跳过 %d；clin 行 %d'
          % (summary['migratable'], summary['migrated'], summary['blocked'],
             summary['skipped'], summary['rows_total']))
    for r in blk:
        print('  !! %s/%s %s %s' % (r['family'], r['slug'], r['action'],
                                    (r.get('error') or '；'.join(r.get('blockers') or []))[:200]))
    for r in skip:
        if r.get('note') != '无 clin 表':
            print('  · 跳过 %s/%s：%s' % (r['family'], r['slug'], r['note']))
    if a.json:
        Path(a.json).write_text(json.dumps({'summary': summary, 'dbs': res},
                                           ensure_ascii=False, indent=1), encoding='utf-8')
        print('明细 → %s' % a.json)
    return 1 if blk else 0


if __name__ == '__main__':
    sys.exit(main())
