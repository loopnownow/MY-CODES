#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""d82_backfill.py — D82 B 路线：`dict.source` 回填计划与落库（`--go`）。

默认纯干跑；`--go` 才写 `dict.source`，写前逐家备份到 `_work/d82_bf_bak/`，
写后立刻重扫对账。保守写集：只写能消债且 `will_accept=1` 的行；HEADER 只收
唯一命中（多候选 sheet 一律留债，等按项目核）。

背景（HANDOFF-20261127 第 2–4 节）：28 家 5t 家族库共 22,791 行 dict，其中 20,386 行
source 不是可追形态；A 路线只能机械回填 1,453 行（7.1%），剩下的是「这一格本来就不是某张
表的某一列」。B 路线按根因分类，每类给一条规则句，本工具逐行算出「这一行该写成什么」，
再由人复核后落库。

分类（互斥，按此顺序判：先看列本身的形态，再看 source 里写坏的值）：

  SCHEMA     结构列（`d82.SYS` 24 列）且 source 写成 schema/derived/源表
             → `SCHEMA §2 <table>：结构列（a/b/c）`
  HEADER     列名归一后命中旧合并库 `<FAM>db_6t` 的 `src.header`
             → `blob:<rel_path>#<sheet>:<原列名>`
  NMR        `nmr_val` / `nmr2_val`，或列名形如 `ppm=<float>`（MHE_AN 一家 10,425 行）
  RADI       `<mod>_radi_val` / `target_val` / `rad_val` / `radiomics_val`，或列名是
             PyRadiomics 特征（firstorder/glcm/glrlm/glszm/gldm/ngtdm/shape）
  OLDBLOB    source 字面写成 `old_db_blob`（那是旧库 `src.kind` 的枚举值，被误当 source 用）
  SCALE      量表/化验等非影像组学的 `*_val` 长表，表头对不上
  UNRESOLVED 其余（clin 里 source 只写「ACY zip」「build 脚本映射」一类半截说明的行）

判序的用意：`old_db_blob` 是「source 被写坏」的症状，不是根因；`radi_val` 里 source 写着
old_db_blob 的行仍然是 PyRadiomics 特征，所以先判 NMR/RADI 再判 OLDBLOB（共 2,411 行因此
落到 RADI，而不是 OLDBLOB）。

结构列例外：SYS 列无论落在哪一类，proposal 一律写 `SCHEMA §2`——结构列没有源列可指，
D82 也不把它们计入债务（C 路线），这类行属于顺手修、不是债。所以各类计数按上表口径，
**落库字符串以每行的 proposal 为准**，不以类名为准。

数据从哪来（这一节是踩过的坑，别改）：

  dict       5t 家族库 `<ROOT>/<FAM>/_PHI/<FAM>db`，或迁移前旧树 `<ROOT>/<FAM>/_db/<FAM>db`；
             两种布局 dict 列一致（scope/table_name/column_name/…/source/…），都认。
  表头       只在旧合并库 `<ROOT>/<FAM>/_PHI/<FAM>db_6t` 的 `src` 表：
             `rel_path` + `sheet_name` + `header`，header 用 U+001F 分列、一格一列
             （列名重复时 Excel 会给 `xxx#2`，所以同一个列名可能命中多个 sheet）。
             迁移前的 `stage/<FAM>/_db/<FAM>db` 只有 dict/meta/id/ref_*，**没有 src**，
             所以表头必须回落到 `_PHI/<FAM>db_6t`（冻结只读，没有参与 5t 迁移）。
             25/28 家有 6t；LAA/UGIB/VEGF 的 dict 是空的，不影响。

用法：
  python -B d82_backfill.py --db D:/MyProjects/_DataBase/MHE_AN
  python -B d82_backfill.py --all --db-root D:/MyProjects/_DataBase
  python -B d82_backfill.py --all --db-root D:/MyProjects/_DataBase --out d82_backfill_plan.csv
  DB5T_ROOT=... python -B d82_backfill.py --all          # 只给根、不给 --db-root 时用它
  python -B d82_backfill.py --db <家族库> --old-db <FAM>db_6t   # 手工指旧库
  python -B d82_backfill.py --db D:/MyProjects/_DataBase/MHE_AN --go   # 落库（逐家备份）

只读：所有连接都 `mode=ro`；唯一落盘是 `--out` 的 CSV（强制写在 `{DEL}\\_work\\` 下）。
`--go`：写 `dict` 前把整库 copy 到 `_work/d82_bf_bak/<FAM>db`（已存在则复用），逐行
`update dict set source=? where scope=? and table_name=? and column_name=?`，一家一个事务；
写完重扫该库并与「写前债 − 本次写入行数」对账，不一致即回滚。保守写集只收
`debt=1 and will_accept=1` 的行，且 HEADER 只收唯一命中（多候选 sheet 留债待核）。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

import d82 as D82

BATCH = Path(os.environ.get('DB5T_BATCH') or r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124')
WORK = Path(os.environ.get('DB5T_WORK') or (BATCH / '_work'))
DATA_ROOT = Path(os.environ.get('DB5T_DATA_ROOT') or r'D:\MyProjects\_DataBase')

CLASSES = ('SCHEMA', 'HEADER', 'NMR', 'RADI', 'OLDBLOB', 'SCALE', 'UNRESOLVED')

# HANDOFF-20261127 第 2 节的分类规模，用于自检对齐（口径差异见 print_totals）。
HANDOFF = {'TOTAL': 20386, 'SCHEMA': 181, 'HEADER': 1272, 'REST': 18933}

# HANDOFF 第 4 节的规则句模板（2026-11-27 主人定稿：四句照此写，不再改）。
TPL = {
    'SCHEMA': 'SCHEMA §2 {table}：结构列（{cols}）',
    'HEADER': 'blob:{rel}#{sheet}:{col}',
    'NMR': '派生：NMR 谱按 ppm 轴分箱（步长 0.001，列名即 ppm 值），原始谱见 src.rel_path',
    'RADI': '派生：PyRadiomics 特征提取（类见列名前缀），ROI 见 src.rel_path',
    'OLDBLOB': '派生：旧库合并表 {table}（db_6t.src 无此列，来源链断于此）',
    'SCALE': '派生：{table} 计分项（由原列名重编码）',
    'UNRESOLVED': '',
}

# 表名 → 影像组学长表；列名 → PyRadiomics 特征类（EC/LC 是 `ADC_firstorder_…` 写法）。
RE_RADI_TABLE = re.compile(r'(^|_)(radi|rad|radiomics|target)(_val)?$', re.I)
RE_RADI_COL = re.compile(r'(^|_)(firstorder|glcm|glrlm|glszm|gldm|ngtdm|shape|sobel|logsigma)_', re.I)
RE_PPM = re.compile(r'^ppm\s*=')


def norm_text(s) -> str:
    """列名归一：与 d82_feasibility.py 逐字一致（去空格/下划线/连字符/全半角括号，转小写）。"""
    return re.sub(r'[\s_\-（）()]', '', str(s)).lower()


def open_ro(path) -> sqlite3.Connection:
    """只读连接；text_factory 保证坏字节不会把整趟扫描炸掉。"""
    conn = sqlite3.connect('file:%s?mode=ro' % Path(path).as_posix(), uri=True)
    conn.text_factory = lambda b: b.decode('utf-8', 'replace')
    return conn


def family_of(db_path) -> str:
    """`…/MHE_AN/_PHI/MHE_ANdb` → MHE_AN。"""
    name = Path(db_path).name
    if name.endswith('db'):
        return name[:-2]
    return Path(db_path).parent.parent.name


def find_family_db(target):
    """--db 的三种写法：家族目录 / 家族库文件 / 带 _PHI|_db 的路径。"""
    p = Path(target)
    if p.is_file():
        return p
    if p.is_dir():
        for bucket in ('_PHI', '_db'):
            cand = p / bucket / f'{p.name}db'
            if cand.exists():
                return cand
    return None


def old_db_for(db_path, family: str, override=None):
    """旧合并库（表头来源）：同目录 `<stem>_6t` → 家族 `_PHI/<stem>_6t` → 数据根 `_PHI`。"""
    if override:
        p = Path(override)
        return p if p.exists() else None
    name = Path(db_path).name
    cands = [Path(db_path).with_name(name + '_6t')]
    cands.append(Path(db_path).parent.parent / '_PHI' / (name + '_6t'))
    cands.append(DATA_ROOT / family / '_PHI' / (name + '_6t'))
    for cand in cands:
        if cand.exists():
            return cand
    return None


def has_src_table(old_db) -> bool:
    """6t 里有 src 表才算合并库；ACY/PAH/SLE/WML 的 6t 是老布局副本，没有 src。"""
    conn = open_ro(old_db)
    try:
        return bool(conn.execute(
            "select 1 from sqlite_master where type='table' and name='src'").fetchone())
    finally:
        conn.close()


def header_index(old_db) -> dict:
    """旧合并库 `src.header` → {归一列名: [(rel_path, sheet_name, 原列名), …]}。

    header 用 U+001F 分列；同一列名常出现在多个 sheet（LC 2,463 个列名里 1,062 个重名），
    所以值是列表，由 pick_header 做确定性首选、并把候选数写进 CSV 供复核。
    """
    idx: dict[str, list[tuple[str, str, str]]] = {}
    conn = open_ro(old_db)
    try:
        cur = conn.execute(
            "select coalesce(rel_path,''), coalesce(sheet_name,''), coalesce(header,'') from src")
        for rel, sheet, header in cur:
            for cell in str(header).split('\x1f'):
                cell = cell.strip()
                if not cell:
                    continue
                idx.setdefault(norm_text(cell), []).append((rel, sheet, cell))
    except sqlite3.Error as exc:
        print(f'  ! 读 {Path(old_db).name}.src 失败：{exc}', file=sys.stderr)
    finally:
        conn.close()
    return idx


def pick_header(cands):
    """多 sheet 命中时取确定性的一个：rel_path 短的优先，再按 rel/sheet/列名字典序。"""
    return sorted(cands, key=lambda c: (len(c[0]), c[0], c[1], c[2]))[0]


def source_hint(source: str) -> str:
    """UNRESOLVED 行的 source 长什么样，给复核人一句提示。"""
    v = (source or '').strip()
    if not v:
        return 'source 为空'
    if v in ('源表', 'schema', 'derived'):
        return '结构列写法，但列不是 SYS 结构列'
    if re.search(r'\.(zip|xlsx?|xls|csv)$', v, re.I) or '/' in v or '\\' in v:
        return '只写到包名/文件，缺 #sheet:列'
    if v.startswith(('build', '5tables', 'patient_', '结构列（')):
        return '构建脚本口径，不是源列'
    return '说明性文字，非可追形态'


def plan(conn, heads=None, family: str = '', old_db=None, has_src=None) -> dict:
    """读一个家族库的 dict，返回待回填清单（不改库）。

    conn 可以是 5t 家族库（`_PHI`）或迁移前旧树（`_db`）的只读连接；两者 dict 列一致。
    heads 为 header_index() 的结果；缺省时 HEADER 类全部回落 UNRESOLVED（并计数提示）。
    has_src=False 表示该家族 6t 里没有 src 表（ACY/PAH/SLE/WML），old_db_blob 的来源链断掉。
    """
    if has_src is None:
        has_src = heads is not None
    rows = list(conn.execute(
        "select coalesce(scope,''), coalesce(table_name,''), column_name, coalesce(source,'') "
        "from dict"))
    stats = D82.scan(conn)

    # 每张表的结构列全集：写 SCHEMA §2 时要把同一张表的结构列一起列出来。
    # dict 里同一 (table, column) 会有多行（不同 scope），所以按表去重、保首次出现顺序。
    struct_cols: dict[str, list[str]] = {}
    seen_struct: dict[str, set[str]] = {}
    for _scope, table, column, _ in rows:
        if column in D82.SYS:
            got = seen_struct.setdefault(table, set())
            if column not in got:
                got.add(column)
                struct_cols.setdefault(table, []).append(column)

    out = {
        'family': family,
        'dict_rows': len(rows),
        'untrace': 0,
        'counts': {c: 0 for c in CLASSES},
        'debt_counts': {c: 0 for c in CLASSES},
        'debt': 0,
        'struct': 0,
        'struct_blocked_header': 0,
        'strict_schema': 0,
        'strict_derived_debt': 0,
        'drops': 0,
        'not_drop': 0,
        'by_table': {},
        'rows': [],
        'no_heads': heads is None,
    }

    for scope, table, column, source in rows:
        kind = D82.classify(source)
        grade = D82.grade_of(table)
        col = column or ''
        sv = (source or '').strip()
        is_struct = col in D82.SYS
        is_debt = (not is_struct) and kind not in D82.ACCEPT[grade]
        if is_debt:
            out['debt'] += 1
            if kind == 'DERIVED':
                # 17 行：`派生：` 词形可追，但 clin/其它 分级不收——不在 20,386 清单里，单独提。
                out['strict_derived_debt'] += 1
        traceable = kind not in ('BAD', 'EMPTY')
        if not traceable:
            out['untrace'] += 1

        # 结构列无论写坏成什么，动作都是 SCHEMA §2；先把命中信息记下来。
        if is_struct:
            out['struct'] += 1
            if heads and norm_text(col) in heads:
                out['struct_blocked_header'] += 1
        if traceable:
            continue  # 已经可追的行不进计划

        n_cand = 0
        note = ''
        if is_struct and sv in ('schema', 'derived', '源表'):
            cls = 'SCHEMA'
            out['strict_schema'] += (sv == 'schema')
            proposal = TPL['SCHEMA'].format(
                table=table, cols='/'.join(struct_cols.get(table, [col])))
        elif (not is_struct) and heads and norm_text(col) in heads:
            cls = 'HEADER'
            cands = heads[norm_text(col)]
            n_cand = len(cands)
            rel, sheet, cell = pick_header(cands)
            proposal = TPL['HEADER'].format(rel=rel, sheet=sheet, col=cell)
            if n_cand > 1:
                alt = next(c for c in cands if c != (rel, sheet, cell))
                note = '表头命中 %d 处，首选 %s#%s；备选 %s#%s' % (
                    n_cand, rel, sheet, alt[0], alt[1])
            if not sheet:
                note = (note + '；' if note else '') + 'sheet 名为空'
        elif table in ('nmr_val', 'nmr2_val') or RE_PPM.match(col):
            cls = 'NMR'
            proposal = TPL['NMR']
        elif (table.endswith('_val') and RE_RADI_TABLE.search(table)) or RE_RADI_COL.search(col):
            cls = 'RADI'
            m = RE_RADI_COL.search(col)
            proposal = TPL['RADI'].format(cls=(m.group(2) if m else 'unknown'))
        elif sv == 'old_db_blob':
            cls = 'OLDBLOB'
            if has_src:
                proposal = TPL['OLDBLOB'].format(table=table)
            else:
                # 该家族 6t 里没有 src（ACY/PAH/SLE/WML），模板里的 `.src` 是个空指针。
                proposal = TPL['OLDBLOB'].format(table=table).replace(
                    '（db_6t.src 无此列，来源链断于此）',
                    '（该家族 db_6t 无 src，无法反查 rel_path）')
                note = 'db_6t 无 src 表，来源链断在这里'
        elif table.endswith('_val'):
            cls = 'SCALE'
            proposal = TPL['SCALE'].format(table=table)
        else:
            cls = 'UNRESOLVED'
            proposal = ''
            note = '' if is_struct else source_hint(sv)

        # 结构列一律写 SCHEMA §2（覆盖上面按列形态得到的派生句）。
        if is_struct:
            proposal = TPL['SCHEMA'].format(
                table=table, cols='/'.join(struct_cols.get(table, [col])))
            note = (note + '；' if note else '') + '结构列（SYS），D82 不计债'

        out['counts'][cls] += 1
        if is_debt:
            out['debt_counts'][cls] += 1
        # 这份 proposal 写进去能不能消债：clin/other 分级只收 TRACE/SCHEMA，`派生：` 到那里
        # 仍然算债（HANDOFF 的 4 条规则句只对 *_val 表成立）。逐行算出来，别让人白改。
        new_kind = D82.classify(proposal) if proposal else kind
        will_accept = 1 if new_kind in D82.ACCEPT[grade] else 0
        if is_debt:
            if will_accept:
                out['drops'] += 1
            else:
                out['not_drop'] += 1
                if proposal:
                    note = (note + '；' if note else '') + \
                        '%s 级不收 %s，需改 blob: 指针或 SCHEMA 说明' % (grade, new_kind)
                else:
                    note = (note + '；' if note else '') + '无模板（需逐行人工）'
        key = (cls, table)
        out['by_table'][key] = out['by_table'].get(key, 0) + 1
        out['rows'].append({
            'family': family,
            'scope': scope,
            'cls': cls,
            'table_name': table,
            'column_name': column,
            'source': sv,
            'grade': grade,
            'kind': kind,
            'debt': 1 if is_debt else 0,
            'struct': 1 if is_struct else 0,
            'proposal': proposal,
            'will_accept': will_accept if is_debt else '',
            'note': note,
            'n_cand': n_cand,
        })

    # 与 d82.scan 对账：本工具逐行算的债必须与门禁口径一致。
    scan_debt = stats['debt'].get('total', 0)
    out['debt'] = scan_debt
    out['debt_counts'] = {c: 0 for c in CLASSES}
    for r in out['rows']:
        if r['debt']:
            out['debt_counts'][r['cls']] += 1
    return out


def fmt_row(res) -> str:
    c = res['counts']
    return ('%-10s %7d %7d %8d %7d %6d %8d %6d %7d %7d'
            % (res['family'], res['dict_rows'], res['untrace'], c['SCHEMA'], c['HEADER'],
               c['NMR'], c['RADI'], c['OLDBLOB'], c['SCALE'], c['UNRESOLVED']))


HEADER_LINE = ('%-10s %7s %7s %8s %7s %6s %8s %6s %7s %7s'
               % ('family', 'dict', 'untrace', 'SCHEMA', 'HEADER', 'NMR', 'RADI',
                  'OLDBLOB', 'SCALE', 'UNRES'))


def total_line(label, agg) -> str:
    c = agg['counts']
    return ('%-10s %7d %7d %8d %7d %6d %8d %6d %7d %7d'
            % (label, agg['dict_rows'], agg['untrace'], c['SCHEMA'], c['HEADER'],
               c['NMR'], c['RADI'], c['OLDBLOB'], c['SCALE'], c['UNRESOLVED']))


def add_into(agg, res, keep_rows=False):
    agg['dict_rows'] += res['dict_rows']
    agg['untrace'] += res['untrace']
    agg['debt'] += res['debt']
    agg['struct'] += res['struct']
    agg['struct_blocked_header'] += res['struct_blocked_header']
    agg['strict_schema'] += res['strict_schema']
    agg['strict_derived_debt'] += res['strict_derived_debt']
    agg['drops'] += res['drops']
    agg['not_drop'] += res['not_drop']
    for key in CLASSES:
        agg['counts'][key] += res['counts'][key]
        agg['debt_counts'][key] += res['debt_counts'][key]
    for key, n in res['by_table'].items():
        agg['by_table'][key] = agg['by_table'].get(key, 0) + n
    if keep_rows:
        agg['rows'].extend(res['rows'])
    return agg


def new_agg(family='合计'):
    return {'family': family, 'dict_rows': 0, 'untrace': 0, 'debt': 0, 'struct': 0,
            'struct_blocked_header': 0, 'strict_schema': 0, 'strict_derived_debt': 0,
            'drops': 0, 'not_drop': 0,
            'counts': {c: 0 for c in CLASSES},
            'debt_counts': {c: 0 for c in CLASSES}, 'by_table': {}, 'rows': [],
            'no_heads': False}


def print_family_report(res, examples=2):
    total = res['untrace'] or 1
    print('\n%s：dict %d 行，非可追 %d 行，D82 债 %d 行'
          % (res['family'], res['dict_rows'], res['untrace'], res['debt']))
    for cls in CLASSES:
        n = res['counts'][cls]
        if not n:
            continue
        tpl = TPL[cls] or '（无模板，逐行判）'
        print('  %-10s %7d  %5.1f%%  %s' % (cls, n, 100.0 * n / total, tpl))
    shown = {c: 0 for c in CLASSES}
    for r in res['rows']:
        if shown[r['cls']] >= examples:
            continue
        shown[r['cls']] += 1
        target = r['proposal'] or ('（%s）' % r['note'])
        print('    e.g. %-10s %s.%s → %s' % (r['cls'], r['table_name'],
                                             r['column_name'][:28], target[:78]))


def print_totals(agg, handoff=False):
    total = agg['untrace'] or 1
    print('\n分类合计（%d 行非可追，D82 债 %d 行）' % (agg['untrace'], agg['debt']))
    for cls in CLASSES:
        n = agg['counts'][cls]
        print('  %-10s %7d  %5.1f%%   D82 债 %6d'
              % (cls, n, 100.0 * n / total, agg['debt_counts'][cls]))
    print('  SYS 结构列 %d 行 source 不可追（D82 不计债）；其中 %d 行归 SCHEMA 类，'
          '%d 行恰好命中旧表头（结构列不走 blob，所以 HEADER 少这几个）'
          % (agg['struct'], agg['counts']['SCHEMA'], agg['struct_blocked_header']))
    print('  按计划真能消债 %d 行；消不掉 %d 行（clin/other 分级不收 `派生：`，且 UNRESOLVED 无模板）'
          % (agg['drops'], agg['not_drop']))

    if not handoff:
        print('\n（HANDOFF 的 181/1,272/18,933 是 28 家合计；单家干跑只看逐类口径。）')
        return

    print('\nHANDOFF 对齐：SCHEMA %d / HEADER %d / 其余 %d（合计 %d）'
          % (HANDOFF['SCHEMA'], HANDOFF['HEADER'], HANDOFF['REST'], HANDOFF['TOTAL']))
    rest = agg['untrace'] - agg['counts']['SCHEMA'] - agg['counts']['HEADER']
    print('本工具分类： SCHEMA %d / HEADER %d / 其余 %d（合计 %d）'
          % (agg['counts']['SCHEMA'], agg['counts']['HEADER'], rest, agg['untrace']))
    print('差异：SCHEMA %+d = 结构列 source 写作 derived/源表（HANDOFF 只数 source=="schema" 的 %d 行）；'
          % (agg['counts']['SCHEMA'] - HANDOFF['SCHEMA'], agg['strict_schema']))
    over = HANDOFF['HEADER'] - agg['counts']['HEADER']
    print('      HEADER %+d = 结构列命中旧表头共 %d 行，HANDOFF 把其中 %d 行算进 blob，'
          '结构列一律写 SCHEMA §2；其余 %+d = 两项相抵。'
          % (-over, agg['struct_blocked_header'], over, rest - HANDOFF['REST']))
    if agg['strict_derived_debt']:
        print('      另有 %d 行 `派生：` 落在 clin/其它（词形可追、分级不收），不在本清单，'
              '改法同 blob/SCHEMA。' % agg['strict_derived_debt'])


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ['family', 'scope', 'cls', 'table_name', 'column_name', 'source', 'grade', 'kind',
              'debt', 'struct', 'proposal', 'will_accept', 'n_cand', 'note']
    with path.open('w', newline='', encoding='utf-8-sig') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})
    print('CSV 已写：%s（%d 行）' % (path, len(rows)))


BACKUP_DIR = WORK / 'd82_bf_bak'


def eligible(row) -> bool:
    """保守写集：只收能消债、且门禁会接受的 proposal；HEADER 只收唯一命中。

    多候选 sheet 的 blob 指针是启发式首选，除了「可能指错」，还会让「可追」变成
    「看着可追」，所以一律留债，等按项目核过再补。
    """
    if row.get('debt') != 1 or row.get('will_accept') != 1 or not row.get('proposal'):
        return False
    if row.get('cls') == 'HEADER' and int(row.get('n_cand') or 0) > 1 \
            and not row.get('picked'):
        return False
    return True


def backup_db(db_path: Path) -> Path:
    """整库备份（同名只留第一次的那份，便于回滚）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / db_path.name
    if not dst.exists():
        shutil.copy2(db_path, dst)
    return dst


def write_rows(db_path: Path, rows) -> int:
    """写一家：一个事务；写后重扫对账，不一致即回滚。返回写入行数。"""
    todo = [r for r in rows if eligible(r)]
    if not todo:
        print('  写集为空，跳过')
        return 0
    bak = backup_db(db_path)
    before = D82.scan(open_ro(db_path))['debt']['total']
    conn = sqlite3.connect(str(db_path))
    n = 0
    try:
        conn.execute('BEGIN IMMEDIATE')
        for r in todo:
            cur = conn.execute(
                "update dict set source=? where coalesce(scope,'')=? "
                "and coalesce(table_name,'')=? and column_name=?",
                (r['proposal'], r['scope'], r['table_name'], r['column_name']))
            if cur.rowcount != 1:
                raise SystemExit('! %s：%s.%s（scope=%s）命中 %d 行，回滚'
                                 % (db_path.name, r['table_name'], r['column_name'],
                                    r['scope'], cur.rowcount))
            n += 1
        after = D82.scan(conn)['debt']['total']
        if after != before - n:
            raise SystemExit('! %s：写后债 %d ≠ 期望 %d（写 %d），回滚'
                             % (db_path.name, after, before - n, n))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print('  写入 %d 行，债 %d → %d（备份 %s）'
          % (n, before, before - n, bak.name))
    return n


def read_picks(path) -> dict:
    """读人工挑选清单 TSV：fam/table/column/rel_path/sheet/cell → (rel, sheet, cell)。

    由 _work\\gen_d82_pick_html.py 导出；同一 (fam, table, column) 在多个 scope 下共用一次挑选。
    """
    picks = {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f, delimiter='\t'):
            base = ((row.get('fam') or '').strip(), (row.get('table') or '').strip(),
                    (row.get('column') or '').strip())
            scope = (row.get('scope') or '').strip()
            rel = (row.get('rel_path') or '').strip()
            if not all(base) or not rel:
                continue
            val = (rel, (row.get('sheet') or '').strip(), (row.get('cell') or '').strip())
            picks[(base, scope)] = val
            # 3 元组的兜底：没有 scope 行时，同列的各 scope 共用一次挑选。
            picks.setdefault(base, val)
    return picks


def apply_picks(res, picks: dict) -> int:
    """把人工挑选结果盖到 HEADER 多候选行上：source 直接写成 blob:rel#sheet:col。"""
    n = 0
    for r in res['rows']:
        if r['cls'] != 'HEADER' or (r['n_cand'] or 0) <= 1:
            continue
        base = (r['family'], r['table_name'], r['column_name'])
        got = picks.get((base, r['scope'] or '')) or picks.get(base)
        if not got:
            continue
        rel, sheet, cell = got
        r['proposal'] = TPL['HEADER'].format(rel=rel, sheet=sheet, col=cell)
        r['will_accept'] = 1
        r['picked'] = 1
        r['note'] = '人工挑选：%s#%s' % (rel, sheet)
        n += 1
    return n


def run_one(db_path, old_db=None, examples=2, verbose=True):
    fam = family_of(db_path)
    conn = open_ro(db_path)
    try:
        if not conn.execute(
                "select 1 from sqlite_master where type='table' and name='dict'").fetchone():
            raise SystemExit(f'{db_path} 没有 dict 表，不是家族库')
        old = old_db_for(db_path, fam, old_db)
        has_src = False
        heads = None
        if old is None:
            print(f'  ! {fam}: 找不到 {db_path.name}_6t，表头不可判（HEADER 会全部落到 UNRESOLVED）',
                  file=sys.stderr)
        else:
            has_src = has_src_table(old)
            if has_src:
                heads = header_index(old)
            else:
                print(f'  ! {fam}: {old.name} 没有 src 表（不是合并库），表头不可判',
                      file=sys.stderr)
        res = plan(conn, heads=heads, family=fam, old_db=old, has_src=has_src)
        res['old_db'] = str(old) if old else ''
    finally:
        conn.close()
    if verbose:
        print_family_report(res, examples=examples)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='D82 B 路线：dict.source 回填计划（纯干跑，不写库）')
    ap.add_argument('--db', help='家族库路径，或家族目录（D:/MyProjects/_DataBase/MHE_AN）')
    ap.add_argument('--all', action='store_true', help='遍历库根下所有家族')
    ap.add_argument('--db-root', default=os.environ.get('DB5T_ROOT') or str(DATA_ROOT),
                    help='库根（含 <FAM>/_PHI/<FAM>db）')
    ap.add_argument('--old-db', help='手工指定旧合并库 <FAM>db_6t（表头来源）')
    ap.add_argument('--out', help='写 CSV（只允许落在 {DEL}\\_work\\，只取文件名）')
    ap.add_argument('--examples', type=int, default=2, help='每类打印几条例行（默认 2）')
    ap.add_argument('--top', type=int, default=5, help='打印债务最大的几家（默认 5）')
    ap.add_argument('--picks', help='人工挑选清单 TSV（gen_d82_pick_html.py 导出）')
    ap.add_argument('--go', action='store_true',
                    help='落库：写 dict.source（保守写集；写前逐家备份，写后对账）')
    args = ap.parse_args(argv)

    if not args.db and not args.all:
        ap.print_help()
        return 2

    print('库根 %s' % args.db_root)
    agg = new_agg()
    results = []
    pairs = []

    if args.db:
        db = find_family_db(args.db)
        if db is None:
            print(f'找不到家族库：{args.db}', file=sys.stderr)
            return 1
        res = run_one(db, args.old_db, examples=args.examples)
        results.append(res)
        pairs.append((db, res))
        add_into(agg, res, keep_rows=True)
        print_totals(agg)
    else:
        root = Path(args.db_root)
        if not root.is_dir():
            print(f'库根不存在：{root}', file=sys.stderr)
            return 1
        fams = []
        for fam in sorted(root.iterdir()):
            if not fam.is_dir() or fam.name.startswith(('_', '.')):
                continue
            db = find_family_db(fam)
            if db is not None:
                fams.append(db)
        print('%s 下 %d 家' % (root, len(fams)))
        print(HEADER_LINE)
        for db in fams:
            res = run_one(db, args.old_db, examples=0, verbose=False)
            results.append(res)
            pairs.append((db, res))
            add_into(agg, res, keep_rows=True)
            print(fmt_row(res))
        print('-' * len(HEADER_LINE))
        print(total_line('合计', agg))
        print_totals(agg, handoff=True)

        top = sorted(results, key=lambda r: -r['debt'])[:args.top]
        print('\n债务最大的 %d 家（D82 口径）' % len(top))
        for r in top:
            c = r['counts']
            print('  %-8s 债 %6d  SCHEMA %4d  HEADER %4d  NMR %5d  RADI %4d  OLD %4d  '
                  'SCALE %4d  UNRES %4d'
                  % (r['family'], r['debt'], c['SCHEMA'], c['HEADER'], c['NMR'],
                     c['RADI'], c['OLDBLOB'], c['SCALE'], c['UNRESOLVED']))

    if args.picks:
        picks = read_picks(args.picks)
        hit = sum(apply_picks(res, picks) for res in results)
        print('\n人工挑选 %d 条，命中 %d 行（按 fam/table/column 对齐）' % (len(picks), hit))

    if args.go:
        total = 0
        for db, res in pairs:
            print('\n== 写库 %s' % db)
            total += write_rows(db, res['rows'])
        print('\n合计写入 %d 行（一家一个事务，写后逐家对账）' % total)

    if args.out:
        # 写盘只允许 _work/：只取文件名，防止顺手写到别处。
        out = WORK / Path(args.out).name
        write_csv(out, agg['rows'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
