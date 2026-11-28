# -*- coding: utf-8 -*-
"""mkdict.py — 28 个家族库的旧 dict（11 列）→ 全局 {RAW}\\Family\\dict.db（14 列）。

用法：
  python -B tools/db-library/db5t/mkdict.py --dry-run            # 只统计，不写盘
  python -B tools/db-library/db5t/mkdict.py                      # 写 DB5T_DICT（默认 _DataBase\\Family\\dict.db）
  python -B tools/db-library/db5t/mkdict.py --out X.db --report X.tsv

做什么（契 3）：
- 旧库 dict 是 11 列：scope / table_name / column_name / role / dtype / aliases / unit /
  source / missing_rule / description / added_on。新库 14 列，补 unit_si / enum_values /
  canonical_id 三列（本批一律 NULL，等 L2 清洗登记时再填）。
- 追加一批 `scope='stage'` 的骨架行：由 schema.sql 反推「所有子库共有的列」——
  家族库 id / id_number / ref_project / ref_study / ref_batch 全列，
  子库 src / person / qc 全列，clin 只取头 11 列 + 15 个 path_*。
  这样 gates 的 D95 对任何家族都能在 proj → family → stage 里找到行。
- 同一个 (scope, table, column) 出现在多个家族库里时去重；内容不一致的打进冲突报告
  （契 3 说三个已知冲突键：id.name / id.id_status / ref_project.period_unit）。

不做什么：不删旧库 dict（那是批 C 的事）；不改旧值语义；role 只做 strip 不做映射。
"""
import argparse
import datetime as dt
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mkschema  # noqa: E402
import paths  # noqa: E402

COLS = ('scope', 'table_name', 'column_name', 'role', 'dtype', 'aliases', 'unit',
        'unit_si', 'enum_values', 'canonical_id', 'source', 'missing_rule',
        'description', 'added_on')
STAGE_CLIN = ('family_uid', 'observed_on', 'period_month', 'cohort', 'group_v', 'subgroup',
              'src_id', 'src_row', 'merged_n', 'unk_seq', 'schema_block')
STAGE_FAMILY = ('id', 'id_number', 'ref_project', 'ref_study', 'ref_batch')
STAGE_PROJECT = ('src', 'person', 'qc')

DDL = """CREATE TABLE dict(
  scope        TEXT NOT NULL,       -- 'family' | 'proj:<slug>' | 'stage'
  table_name   TEXT NOT NULL,
  column_name  TEXT NOT NULL,
  role         TEXT NOT NULL,       -- key|phi|quasi|outcome|feature|covariate|derived|text|aux|skeleton
  dtype        TEXT,                -- int|real|text|date|enum|bool（按值推断，不许一律 text）
  aliases      TEXT,                -- 合并进本列的全部源表头，'|' 分隔
  unit         TEXT,
  unit_si      TEXT,                -- SI 换算后的单位（本批 NULL）
  enum_values  TEXT,                -- 取值清单（本批 NULL）
  canonical_id TEXT,                -- 标准词元 ID（本批 NULL）
  source       TEXT,
  missing_rule TEXT,
  description  TEXT,
  added_on     TEXT NOT NULL,
  PRIMARY KEY (scope, table_name, column_name)
)"""


def family_dbs(root: pathlib.Path):
    for fam in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('_')):
        db = fam / '_PHI' / f'{fam.name}db'
        if db.exists():
            yield fam.name, db


def read_legacy(root: pathlib.Path):
    """返回 [(row_dict, fam)]，旧 11 列补齐到 14 列。"""
    out = []
    for fam, db in family_dbs(root):
        c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        try:
            cols = [r[1] for r in c.execute('PRAGMA table_info(dict)')]
            if not cols:
                print(f'  ! {fam}: 无 dict 表，跳过')
                continue
            for rec in c.execute('select * from dict'):
                d = dict(zip(cols, rec))
                row = {k: (str(d.get(k)).strip() if d.get(k) is not None else None) for k in COLS}
                for k in ('unit_si', 'enum_values', 'canonical_id'):
                    row[k] = None
                row['dtype'] = (row['dtype'] or '').lower() or None
                row['role'] = (row['role'] or '').lower()
                row['added_on'] = row['added_on'] or ''
                out.append((row, fam))
        finally:
            c.close()
    return out


def _decl_dtype(typ: str) -> str:
    t = (typ or '').upper()
    if 'INT' in t:
        return 'int'
    if any(k in t for k in ('REAL', 'FLOA', 'DOUB', 'NUM')):
        return 'real'
    if 'DATE' in t:
        return 'date'
    if 'BOOL' in t:
        return 'bool'
    return 'text'


def stage_rows(schema_sql: str):
    """从 schema.sql 反推骨架行：家族表全列 + 子库公共表列。"""
    fam_sql, proj_sql = mkschema_split(schema_sql)
    rows = []

    def add(conn, table, names=None):
        for _cid, name, typ, notnull, _dflt, pk in conn.execute(f'PRAGMA table_info({table})'):
            if names is not None and name not in names:
                continue
            if table == 'clin':
                role = 'key' if name in ('family_uid',) else 'skeleton'
            else:
                role = 'key' if (pk or name.endswith(('_id', '_uid'))) and name != 'src_row' else 'skeleton'
            rows.append({'scope': 'stage', 'table_name': table, 'column_name': name,
                         'role': role, 'dtype': _decl_dtype(typ), 'aliases': None, 'unit': None,
                         'unit_si': None, 'enum_values': None, 'canonical_id': None,
                         'source': 'schema.sql', 'missing_rule': None,
                         'description': None, 'added_on': ''})

    fam = sqlite3.connect(':memory:')
    fam.executescript(fam_sql)
    for t in STAGE_FAMILY:
        add(fam, t)
    fam.close()

    proj = sqlite3.connect(':memory:')
    proj.executescript(proj_sql)
    for t in STAGE_PROJECT:
        add(proj, t)
    clin_cols = STAGE_CLIN + tuple(
        r[1] for r in proj.execute('PRAGMA table_info(clin)') if r[1].startswith('path_'))
    add(proj, 'clin', clin_cols)
    proj.close()
    return rows


def mkschema_split(sql: str):
    fam, proj = sql.split(mkschema.MARK_PROJ, 1)
    return fam.split(mkschema.MARK_FAM, 1)[1], proj


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='旧家族 dict → 全局 dict.db')
    ap.add_argument('--root', type=pathlib.Path, default=paths.DICT.parents[1],
                    help='库根（默认由 DB5T_DICT 反推：_DataBase）')
    ap.add_argument('--out', type=pathlib.Path, default=paths.DICT)
    ap.add_argument('--schema', type=pathlib.Path, default=HERE / 'schema.sql')
    ap.add_argument('--report', type=pathlib.Path, default=None)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)

    legacy = read_legacy(a.root)
    stage = stage_rows(a.schema.read_text(encoding='utf-8'))
    print(f'legacy={len(legacy)} 行（{len({f for _r, f in legacy})} 个家族库）  stage={len(stage)} 行')

    merged, origins, conflicts = {}, {}, []
    for row, fam in legacy + [(r, 'stage') for r in stage]:
        key = (row['scope'], row['table_name'], row['column_name'])
        prev = merged.get(key)
        if prev is None:
            merged[key] = row
            origins[key] = [fam]
            continue
        origins[key].append(fam)
        for f in ('role', 'dtype', 'description', 'unit'):
            if (prev.get(f) or '') != (row.get(f) or ''):
                conflicts.append((key, f, prev.get(f), row.get(f), ','.join(origins[key])))
                break
        # 描述取最长的那条（各家写法详略不同，最长的一条信息最全），其余字段按首次出现
        if len(row.get('description') or '') > len(prev.get('description') or ''):
            merged[key] = row

    print(f'merged={len(merged)} 行  dup={len(legacy) + len(stage) - len(merged)}  conflicts={len(conflicts)}')
    for (key, f, a1, b1, where) in conflicts[:10]:
        print(f'  ~ {key[0]}/{key[1]}.{key[2]}  {f}: {a1!r} ≠ {b1!r}  [{where}]')

    if a.dry_run:
        print(f'DRY  将写 {a.out}（{len(merged)} 行）')
        return 0

    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix('.tmp')
    if tmp.exists():
        tmp.unlink()
    c = sqlite3.connect(tmp)
    try:
        c.execute(DDL)
        c.executemany(
            f'INSERT OR REPLACE INTO dict({",".join(COLS)}) VALUES ({",".join("?" * len(COLS))})',
            [tuple(merged[k][col] for col in COLS) for k in sorted(merged)])
        c.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL)')
        meta = {
            'schema_version': '5t/1',
            'built_at': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'builder': 'tools/db-library/db5t/mkdict.py',
            'roots': str(a.root),
            'family_dbs': str(len({f for _r, f in legacy})),
            'rows': str(len(merged)),
            'stage_rows': str(len(stage)),
            'conflicts': str(len(conflicts)),
            'contract': 'db-build-contract.md 契 3',
        }
        c.executemany('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)', sorted(meta.items()))
        c.commit()
    finally:
        c.close()
    if a.out.exists():
        a.out.unlink()
    tmp.replace(a.out)
    size = a.out.stat().st_size
    print(f'WROTE {a.out}  {size:,} B  rows={len(merged)}  conflicts={len(conflicts)}')
    if a.report:
        a.report.write_text(
            'scope\ttable\tcolumn\tfield\tfirst\tsecond\tsources\n' + ''.join(
                f'{k[0]}\t{k[1]}\t{k[2]}\t{f}\t{a1 or ""}\t{b1 or ""}\t{where}\n'
                for (k, f, a1, b1, where) in conflicts),
            encoding='utf-8')
        print(f'WROTE {a.report}  ({len(conflicts)} 行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
