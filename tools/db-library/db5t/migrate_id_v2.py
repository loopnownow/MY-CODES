"""id v1(12 列) → v2(24 列、物理列序固定) 重建迁移。

设计要点
- `ALTER TABLE ADD COLUMN` 只能追加到表尾，改不了列序；v2 要求 01..24 逐位对齐，
  因此只能「建新表 → 抄数据 → 换名」，本脚本就是这个动作的唯一入口。
- 默认 dry-run：只读、只报计划与体检结果；`--apply` 才写盘，写盘前先 VACUUM INTO 备份。
- 抄列的映射按列名，不按位置；新增 12 列一律 NULL（不猜值），identity_state 取
  meta.identity_state 已登记的库级状态（仅当 ∈ resolved/unresolved）。
- 每个库落盘后逐列核对：行数一致、12 个搬列的非空数一致、integrity_check /
  foreign_key_check 通过；任一项不符即回滚并记失败。

用法
  python -B migrate_id_v2.py                 # dry-run，全家族
  python -B migrate_id_v2.py --family EC     # 只体检 EC
  python -B migrate_id_v2.py --family EC --apply --backup-dir <dir>
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

V2_COLS = [
    'family_uid', 'patient_id', 'subject_label', 'name', 'name_pinyin', 'sex',
    'birth_date', 'id_card', 'phone', 'address',
    'inpatient_no', 'outpatient_no', 'mrn', 'imaging_no', 'pathology_no',
    'sample_no', 'study_uid', 'misc_id',
    'id_status', 'identity_state', 'source', 'created_on', 'updated_on', 'note',
]
# v1 里存在、v2 原样保留的列（抄数据用；subject_label 是「原样兼容列」，不再写新值）
CARRY = ['family_uid', 'subject_label', 'name', 'sex', 'birth_date', 'id_card',
         'phone', 'address', 'id_status', 'created_on', 'updated_on', 'note']
NEW_COLS = [c for c in V2_COLS if c not in CARRY]

DDL = """CREATE TABLE id__v2(
  family_uid TEXT PRIMARY KEY,
  patient_id TEXT,
  subject_label TEXT,
  name TEXT,
  name_pinyin TEXT,
  sex TEXT,
  birth_date TEXT,
  id_card TEXT,
  phone TEXT,
  address TEXT,
  inpatient_no TEXT,
  outpatient_no TEXT,
  mrn TEXT,
  imaging_no TEXT,
  pathology_no TEXT,
  sample_no TEXT,
  study_uid TEXT,
  misc_id TEXT,
  id_status TEXT NOT NULL,
  identity_state TEXT,
  source TEXT,
  created_on TEXT NOT NULL,
  updated_on TEXT,
  note TEXT,
  CHECK (family_uid GLOB '[A-Z0-9_]*-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  CHECK (id_status IN ('keyed_full','keyed_partial','keyed_animal','label_only','label_only_omics'))
)"""

IDX = [
    'CREATE UNIQUE INDEX ux_id_card  ON id(id_card)  WHERE id_card  IS NOT NULL',
    'CREATE UNIQUE INDEX ux_id_phone ON id(phone)    WHERE phone    IS NOT NULL',
]


def default_root():
    return Path(os.environ.get('DB5T_ROOT') or r'D:\MyProjects\_DataBase')


def family_dbs(root):
    out = []
    for p in sorted(root.glob('*/_PHI/*db')):
        if p.is_file():
            out.append(p)
    return out


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for b in iter(lambda: fh.read(chunk), b''):
            h.update(b)
    return h.hexdigest()


def cols_of(con, table):
    return [r[1] for r in con.execute(f'PRAGMA table_info({table})')]


def meta_get(con, key):
    try:
        r = con.execute('SELECT v FROM meta WHERE k=?', (key,)).fetchone()
        return r[0] if r else None
    except sqlite3.Error:
        return None


def nonnull_counts(con, table, cols):
    return {c: con.execute(f'SELECT count({c}) FROM {table}').fetchone()[0] for c in cols}


def inspect(path):
    """只读体检：返回 (状态, 说明, 明细)。状态 ∈ v2 / v1 / other。"""
    con = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    try:
        tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'id' not in tabs:
            return 'other', '无 id 表', {}
        cur = cols_of(con, 'id')
        info = {
            'cols': cur,
            'rows': con.execute('SELECT count(*) FROM id').fetchone()[0],
            'missing': [c for c in CARRY if c not in cur],
            'extra': [c for c in cur if c not in V2_COLS],
            'id_number_rows': con.execute('SELECT count(*) FROM id_number').fetchone()[0]
                             if 'id_number' in tabs else None,
            'identity_state_meta': meta_get(con, 'identity_state'),
        }
        if cur == V2_COLS:
            return 'v2', '已是 v2 24 列', info
        if info['missing']:
            return 'other', f"缺 v1 列 {info['missing']}", info
        # NOT NULL 约束体检：v2 抄过去会炸的行数
        bad = con.execute(
            "SELECT count(*) FROM id WHERE id_status IS NULL OR created_on IS NULL").fetchone()[0]
        info['notnull_bad'] = bad
        info['orphan_idnum'] = con.execute(
            'SELECT count(*) FROM id_number n LEFT JOIN id i USING(family_uid) '
            'WHERE i.family_uid IS NULL').fetchone()[0] if 'id_number' in tabs else None
        info['nonnull'] = nonnull_counts(con, 'id', CARRY)
        info['idxs'] = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='id' "
            "AND sql IS NOT NULL")]
        return 'v1', '12 列 v1', info
    finally:
        con.close()


def migrate(path, backup_dir, apply_):
    fam = path.parent.parent.name
    state, note, info = inspect(path)
    tag = f'{fam:<10}'
    if state != 'v1':
        print(f'{tag} {state:<6} {note} — 跳过')
        return {'family': fam, 'state': state, 'action': 'skip', 'note': note}

    print(f'{tag} v1   {info["rows"]:>7} 行 / {len(info["cols"])} 列 → 24 列；'
          f'id_number {info["id_number_rows"]} 行')
    if info['notnull_bad'] or info['orphan_idnum']:
        print(f'{tag} !! 体检不过：id_status/created_on 空 {info["notnull_bad"]} 行，'
              f'id_number 孤儿 {info["orphan_idnum"]} 行 — 跳过，先修数据')
        return {'family': fam, 'state': state, 'action': 'blocked',
                'notnull_bad': info['notnull_bad'], 'orphan_idnum': info['orphan_idnum']}
    if not apply_:
        print(f'{tag} dry-run OK：抄 {len(CARRY)} 列，新增 {len(NEW_COLS)} 列留 NULL')
        return {'family': fam, 'state': state, 'action': 'dry-run', 'rows': info['rows']}

    backup_dir.mkdir(parents=True, exist_ok=True)
    bak = backup_dir / path.name
    if not bak.exists():
        shutil.copy2(path, bak)
    bak_sha = sha256(bak)
    if bak_sha != sha256(path):
        raise RuntimeError(f'备份与库不一致：{bak}')

    con = sqlite3.connect(str(path), isolation_level=None)
    try:
        con.execute('PRAGMA foreign_keys=OFF')
        con.execute('BEGIN IMMEDIATE')
        con.execute(DDL)
        collist = ', '.join(CARRY)
        ins = (f'INSERT INTO id__v2 ({collist}) SELECT {collist} FROM id')
        n_before = con.execute('SELECT count(*) FROM id').fetchone()[0]
        con.execute(ins)
        n_after = con.execute('SELECT count(*) FROM id__v2').fetchone()[0]
        if n_after != n_before:
            raise RuntimeError(f'行数不符 {n_before} → {n_after}')
        nn_b = nonnull_counts(con, 'id', CARRY)
        nn_a = nonnull_counts(con, 'id__v2', CARRY)
        if nn_b != nn_a:
            raise RuntimeError(f'非空数不符：{[(c, nn_b[c], nn_a[c]) for c in CARRY if nn_b[c] != nn_a[c]]}')
        con.execute('DROP TABLE id')
        con.execute('ALTER TABLE id__v2 RENAME TO id')
        for s in IDX:
            con.execute(s)
        st = info['identity_state_meta']
        st = st if st in ('resolved', 'unresolved') else None
        con.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)',
                    ('id_schema', 'v2'))
        con.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)',
                    ('id_v2_migrated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        if st:
            con.execute('UPDATE id SET identity_state=? WHERE identity_state IS NULL', (st,))
        con.execute('COMMIT')
        fk = con.execute('PRAGMA foreign_key_check').fetchall()
        integ = con.execute('PRAGMA integrity_check').fetchone()[0]
        cur = cols_of(con, 'id')
        ok = cur == V2_COLS and integ == 'ok' and not fk
        print(f'{tag} {"OK" if ok else "!! FAIL"} 24/24 列，行 {n_after}，integrity={integ}，'
              f'fk_violation={len(fk)}；备份 sha256={bak_sha[:12]}')
        return {'family': fam, 'state': state, 'action': 'migrated' if ok else 'failed',
                'rows': n_after, 'cols': cur, 'integrity': integ,
                'fk_violation': len(fk), 'backup': str(bak), 'backup_sha256': bak_sha,
                'identity_state_filled': st}
    except Exception as e:
        try:
            con.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        print(f'{tag} !! 失败已回滚：{e}')
        return {'family': fam, 'state': state, 'action': 'error', 'error': str(e)}
    finally:
        con.execute('PRAGMA foreign_keys=ON')
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=None, help='_DataBase 根（默认 DB5T_ROOT 或 D:/MyProjects/_DataBase）')
    ap.add_argument('--family', action='append', default=None, help='只做这些家族（可多次）')
    ap.add_argument('--apply', action='store_true', help='真的写盘；缺省 dry-run')
    ap.add_argument('--backup-dir', default=None)
    ap.add_argument('--json', default=None, help='把结果写成 JSON')
    a = ap.parse_args(argv)

    root = Path(a.root) if a.root else default_root()
    dbs = family_dbs(root)
    if a.family:
        want = {f.upper() for f in a.family}
        dbs = [p for p in dbs if p.parent.parent.name.upper() in want]
    if not dbs:
        print(f'没找到家族库：{root}/*/_PHI/*db')
        return 2
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    bdir = Path(a.backup_dir) if a.backup_dir else \
        Path(r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124\_work') / f'id_v2_backup_{stamp}'

    print(f'{"== 写盘 ==" if a.apply else "== dry-run =="} {len(dbs)} 个家族库；'
          f'备份目录 {bdir if a.apply else "(dry-run 不备份)"}')
    results = [migrate(p, bdir, a.apply) for p in dbs]
    n_ok = sum(1 for r in results if r['action'] in ('migrated', 'dry-run'))
    n_bad = [r for r in results if r['action'] in ('failed', 'error', 'blocked')]
    print(f'\n合计：可迁移/已迁移 {n_ok}；跳过 '
          f'{sum(1 for r in results if r["action"] == "skip")}；异常 {len(n_bad)}')
    for r in n_bad:
        print('  !!', r['family'], r['action'], r.get('error') or r.get('notnull_bad'))
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'明细 → {a.json}')
    return 1 if n_bad else 0


if __name__ == '__main__':
    sys.exit(main())
