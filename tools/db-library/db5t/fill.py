# -*- coding: utf-8 -*-
"""fill.py — 按 map.csv 回填（阶段 5）：一格一来源（src_id + src_row），契约先于数据。

用法：
  python -B tools/db-library/db5t/fill.py --map <_work\\map.csv> --root <stage> \\
      --family MM --slug zy_MM --dry-run
  python -B tools/db-library/db5t/fill.py --map ... --root ... --family MM --slug zy_MM

约定（与 db-build-contract.md 对应）：
- map.csv 13 列：src_file / src_sheet / src_header_row / src_header / src_column /
  target_table / target_column / dtype / unit / enum_values / missing_rule / transform / expect_rows
- 源：.xlsx/.xls 按 sheet + header_row 定位；.db 按表名（src_sheet 当表名，src_header_row 忽略）
- 目标列不在库里时必须先有 dict 行（proj:<slug> 或 stage），否则这一步只报不做（契 2）
- 回填只写 person / clin / _modules_* / *_val / src / qc，不碰 meta（meta 归 newdb）
- 同一文库重跑：先按 src_id 删除旧行再写（幂等），不累积

返回码：0 成功；1 有「该做没做」的缺列；2 参数/契约问题。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402

MAP_COLS = ['src_file', 'src_sheet', 'src_header_row', 'src_header', 'src_column',
            'target_table', 'target_column', 'dtype', 'unit', 'enum_values',
            'missing_rule', 'transform', 'expect_rows']
SRC_COLS = ['src_id', 'kind', 'status', 'rel_path', 'sha256', 'rows', 'note']
# 源里带着的溯源/结构列：目标库由 fill 重新生成，不算「被排除的业务列」
STRUCT_EXCLUDE = {'src_id', 'src_row', 'merged_n', 'unk_seq', 'month_ix',
                  'period_month', 'period_source', 'period_origin', 'observed_on',
                  'family_uid', 'exam_uid', 'feature', 'value', 'unit'}
DATA_TABLES = ('person', 'clin', 'src', 'qc')


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def read_map(path: pathlib.Path):
    rows = list(csv.DictReader(path.open(encoding='utf-8-sig', newline='')))
    if not rows:
        raise SystemExit(f'{path} 是空表')
    missing = [c for c in MAP_COLS if c not in rows[0]]
    if missing:
        raise SystemExit(f'map.csv 缺列：{missing}（应有 13 列，见契 1）')
    return rows


def dict_index(conn: sqlite3.Connection, slug: str):
    """目标列 → dict 行（proj:<slug> 优先，再 stage）；没有行 = 未登记列。"""
    out = {}
    for scope in (f'proj:{slug}', 'stage'):
        for table, column, role, dtype in conn.execute(
                'SELECT table_name, column_name, role, dtype FROM dict WHERE scope=?', (scope,)):
            out.setdefault((table, column), {'role': role, 'dtype': dtype, 'scope': scope})
    return out


class Source:
    """一种源文件：xlsx 按 sheet，sqlite 按表。"""

    def __init__(self, path: pathlib.Path):
        self.path = path
        head = path.open('rb').read(16)
        # 家族库文件名不带后缀（<slug>db），按魔数认，别按扩展名猜
        self.kind = 'sqlite' if head.startswith(b'SQLite format 3') else 'sheet'

    def load(self, sheet: str, header_row: int):
        if self.kind == 'sqlite':
            c = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
            cur = c.execute(f'SELECT rowid, * FROM "{sheet}"')
            cols = [d[0] for d in cur.description][1:]
            rows = [dict(zip(cols, r[1:]), _row=r[0]) for r in cur]
            c.close()
            return cols, rows
        import openpyxl
        wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)
        ws = wb[sheet]
        it = ws.iter_rows(values_only=True)
        for _ in range(max(header_row - 1, 0)):
            next(it, None)
        header = [str(h).strip() if h is not None else '' for h in next(it)]
        rows = []
        for i, vals in enumerate(it, start=header_row + 1):
            rec = {h: v for h, v in zip(header, vals) if h}
            rec['_row'] = i
            rows.append(rec)
        wb.close()
        return header, rows


def plan(rows, root: pathlib.Path, family: str, slug: str):
    """把 map 行按 (源, 目标表) 分组，返回分组与列计划。"""
    fam_db = root / family / '_PHI' / f'{family}db'
    proj_db = root / family / slug / f'{slug}db'
    groups = {}
    for r in rows:
        key = (r['src_file'], r['src_sheet'], int(r['src_header_row'] or 0))
        groups.setdefault(key, []).append(r)
    return fam_db, proj_db, groups


def ensure_columns(conn: sqlite3.Connection, table: str, want: list[str], idx: dict) -> list[str]:
    have = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')}
    added = []
    for col in want:
        if col in have:
            continue
        info = idx.get((table, col))
        if not info:
            raise KeyError(f'{table}.{col} 不在库里也没有 dict 行（契 2：dict 先有行再 ALTER）')
        typ = {'int': 'INTEGER', 'real': 'REAL', 'date': 'TEXT', 'text': 'TEXT',
               'enum': 'TEXT', 'bool': 'INTEGER', 'phi': 'TEXT'}.get((info['dtype'] or 'text').lower(), 'TEXT')
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {typ}')
        added.append(f'{table}.{col} {typ}')
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按 map.csv 回填')
    ap.add_argument('--map', type=pathlib.Path, required=True)
    ap.add_argument('--root', type=pathlib.Path, default=paths.STAGE)
    ap.add_argument('--family', required=True)
    ap.add_argument('--slug', required=True)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--src-root', type=pathlib.Path, default=None,
                    help='src_file 的相对根（默认 map.csv 所在目录）')
    a = ap.parse_args(argv)

    rows = read_map(a.map)
    src_root = a.src_root or a.map.parent
    fam_db, proj_db, groups = plan(rows, a.root, a.family, a.slug)
    if not proj_db.exists():
        raise SystemExit(f'{proj_db} 不存在：先跑 newdb.py 建空库')
    print(f'root   = {a.root}')
    print(f'proj   = {proj_db}')
    print(f'map    = {len(rows)} 行 / {len(groups)} 源组')

    dict_db = paths.DICT
    if not dict_db.exists():
        raise SystemExit(f'{dict_db} 不存在：先跑 mkdict.py')
    dconn = sqlite3.connect(f'file:{dict_db}?mode=ro', uri=True)
    idx = dict_index(dconn, a.slug)
    dconn.close()

    conn = sqlite3.connect(proj_db)
    conn.execute('PRAGMA foreign_keys=ON')
    rc = 0
    try:
        # src 登记：一个源文件一行。
        # `src.rows` 的口径是 D96 的守恒式 `Σ clin.merged_n = Σ src.rows(active)`，所以它记
        # 「这份源并入 clin 的行数」；没有 clin 目标列的源（纯 person / 纯 val）退回归档总行数，
        # 各 sheet 的明细写在 note 里（`sheets=clin:185,person:250,…`）——
        # 2026-11-28 首跑把 4 个 sheet 合计 6,637 写进 rows，D96 立刻报 185≠6,637。
        src_reg = {}
        for (src_file, sheet, hdr) in sorted(groups):
            src_path = src_root / src_file
            if not src_path.exists():
                raise SystemExit(f'源文件不存在：{src_path}')
            key = (src_file,)
            rec = src_reg.setdefault(key, {'path': src_path, 'sheets': []})
            if sheet not in rec['sheets']:
                rec['sheets'].append(sheet)
        clin_sheets = {(r['src_file'], r['src_sheet']) for r in rows if r['target_table'] == 'clin'}
        sheet_rows = {}
        for (src_file, sheet, hdr) in sorted(groups):
            if (src_file, sheet) not in sheet_rows:
                _, srows = Source(src_root / src_file).load(sheet, hdr)
                sheet_rows[(src_file, sheet)] = len(srows)
        if not a.dry_run:
            # 重跑前清库：按依赖倒序删（值 → 模态 → clin/qc → person → src）
            tabs = [r[0] for r in conn.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
            order = ([t for t in tabs if t.endswith('_val')]
                     + [t for t in tabs if t.startswith('_modules')]
                     + [t for t in ('clin', 'qc', 'person', 'src') if t in tabs])
            for t in order:
                conn.execute(f'DELETE FROM "{t}"')
            conn.commit()
        src_total = {}
        for i, (key, rec) in enumerate(sorted(src_reg.items()), start=1):
            kind = 'old_db_blob' if Source(rec['path']).kind == 'sqlite' else 'sheet'
            counts = {sheet: sheet_rows[(key[0], sheet)] for sheet in rec['sheets']}
            merged = sum(n for sheet, n in counts.items() if (key[0], sheet) in clin_sheets)
            total = merged or sum(counts.values())
            h = sha256(rec['path'])
            rel = rec['path'].name
            note = (f'{a.family}/{a.slug} 试点回填；sheets='
                    + ','.join(f'{s}:{n}' for s, n in counts.items()))
            print(f'src {i}: {rel}  kind={kind}  sha256={h[:12]}…  rows={total}  {note}')
            if not a.dry_run:
                conn.execute(f'INSERT INTO src({",".join(SRC_COLS)}) VALUES ({",".join("?" * len(SRC_COLS))})',
                             (i, kind, 'active', rel, h, total, note))
            src_total[key] = total
            rec['src_id'] = i
        src_id_of = {f: r['src_id'] for (f,), r in src_reg.items()}

        def _rank(t: str) -> int:
            return {'src': 0, 'person': 1, 'clin': 2}.get(t, 3 if t.startswith('_modules') else 4)

        # 跨组也要按依赖走：person 组必须先于 clin / _modules / val
        for (src_file, sheet, hdr), grp in sorted(
                groups.items(), key=lambda kv: min(_rank(r['target_table']) for r in kv[1])):
            src_path = src_root / src_file
            cols, srows = Source(src_path).load(sheet, hdr)
            tables = {}
            for r in grp:
                tables.setdefault(r['target_table'], []).append(r)
            # FK 顺序：src → person → clin → _modules_* → *_val（表内 FK 是 REFERENCES person/src）
            for table, items in sorted(tables.items(), key=lambda kv: (_rank(kv[0]), kv[0])):
                want = [r['target_column'] for r in items]
                try:
                    added = ensure_columns(conn, table, want, idx) if not a.dry_run else []
                except KeyError as e:
                    print(f'STOP {e}')
                    rc = 1
                    return rc
                for line in added:
                    print(f'ALTER {line}')
                src_cols = [(r['target_column'], r['src_column']) for r in items]
                missing_src = [s for _, s in src_cols if s not in cols]
                if missing_src:
                    print(f'STOP {table}: 源列不存在 {missing_src}')
                    rc = 1
                    return rc
                sid = src_id_of[src_file]
                have_cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{table}")')}
                records = []
                for s in srows:
                    rec = {}
                    if 'src_id' in have_cols:
                        rec['src_id'] = sid
                    if 'src_row' in have_cols:
                        # 源自带 src_row（旧库/回填过）就沿用，保住原表行号；否则用源行号
                        sr = s.get('src_row')
                        rec['src_row'] = sr if sr is not None else s['_row']
                    if 'merged_n' in have_cols:
                        rec['merged_n'] = 1  # 一行源行 → 一行目标行；多源合并时再累加
                    for tcol, scol in src_cols:
                        v = s.get(scol)
                        if isinstance(v, str):
                            v = v.strip() or None
                        rec[tcol] = v
                    # 源里若也带 src_id（旧库回放常见），那是「原登记的源编号」，
                    # 不是本次登记的行；溯源一律以 src 表为准，覆盖回去。
                    if 'src_id' in have_cols:
                        rec['src_id'] = sid
                    records.append(rec)
                if a.dry_run:
                    print(f'DRY  {table}: +{len(records)} 行 × {len(src_cols)} 列（源 {src_file}#{sheet}）')
                    continue
                if 'src_id' in have_cols:
                    conn.execute(f'DELETE FROM "{table}" WHERE src_id=?', (sid,))
                tcols = list(records[0].keys()) if records else []
                ph = ','.join('?' * len(tcols))
                conn.executemany(
                    f'INSERT INTO "{table}"({",".join(chr(34) + c + chr(34) for c in tcols)}) VALUES ({ph})',
                    [tuple(r[c] for c in tcols) for r in records])
                print(f'FILL {table}: {len(records)} 行 × {len(src_cols)} 列（源 {src_file}#{sheet}，src_id={sid}）')
        if not a.dry_run:
            # D50 / D52：这两个数必须写进 meta —— 下一个人问「源有多少条记录、哪些列没进来」
            # 时必须能从库里回答，而不是回读 map.csv（map.csv 收口后随库，但也只是台账）。
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)',
                         ('src_records_n', str(sum(t for t in src_total.values()))))
            excluded = []
            for (src_file, sheet, hdr), grp in sorted(groups.items()):
                cols, _ = Source(src_root / src_file).load(sheet, hdr)
                mapped = {r['src_column'] for r in grp}
                for c in cols:
                    # 溯源/结构列由新流水线重新生成，不算「被排除的业务列」；
                    # 否则 D52「被排除列不得出现在库里」会把这些必然存在的列判成违规。
                    if c not in mapped and c.lower() not in STRUCT_EXCLUDE:
                        excluded.append({'column': c, 'sheet': sheet,
                                         'reason': 'map.csv 未映射（源列未纳入本批 5t 库）'})
            conn.execute('INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)',
                         ('excluded_columns', json.dumps(excluded, ensure_ascii=False)))
            if excluded:
                print(f'meta.excluded_columns = {len(excluded)} 列（'
                      + '、'.join(sorted({e["column"] for e in excluded})[:6]) + '…）')
            conn.commit()
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    finally:
        conn.close()
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
