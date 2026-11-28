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
- transform 列（可空）：
    const:<值>  目标列写固定值（如 period_month='UNK'、id_status='label_only'）
    mint:<茎>   目标列写顺序号；茎 `<前缀>-<起始号>`，发 `<前缀>-<起始号+n:06d>`
                （如 `mint:MCI-0` → MCI-000001；`mint:MCI-900000` → MCI-900001，9 段占位）
                同一源值只发一个号，跨 sheet 复用（person 先发，clin 拿到同一个）
- missing_rule 列（可空）：这一列里哪些值算「缺」，写成 | 或 , 分隔的标记，命中即写 NULL。
    留空 = 只把空串当缺（默认，不动其它文本）；`NA|NULL|.` = 这三个标记也当缺；
    `keep` = 不判缺，源值原样照写（空串也保留）；`stop` = 空就停（契 6，整组不写、rc=1）。
- 目标列不在库里时必须先有 dict 行（proj:<slug> 或 stage），否则这一步只报不做（契 2）
- 回填只写 person / clin / _modules_* / *_value / src / qc，不碰 meta（meta 归 newdb）
- 同一文库重跑：先按 src_id 删除旧行再写（幂等），不累积

返回码：0 成功；1 有「该做没做」的缺列；2 参数/契约问题。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import re
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


class Mint:
    """`transform=mint:<茎>` 的发号器：同一源值只发一次号，跨 sheet 复用。

    根目录数据（xlsx）常常连 family_uid 都没有，只能按行序发号；行序跟源表一致，
    所以重跑同一个源得到的号也一致（幂等靠这个性质）。
    """

    def __init__(self):
        self.seq = {}
        self.issued = {}

    def uid(self, stem: str, key) -> str:
        """stem 形如 `MCI-0` / `MCI-900000`：发 `<前缀>-<起始号+n, 6 位>`。

        起始号写在 stem 里，是为了让「段位」可声明：0 段正常身份、9 段占位身份
        （schema 的 CHECK 要求 dash 后正好 6 位，所以不能靠字符串拼接凑位数）。
        """
        k = (stem, str(key))
        if k not in self.issued:
            pre, _, base = stem.rpartition('-')
            pre = pre or stem
            start = int(base or 0)
            n = self.seq.get(stem, 0) + 1
            self.seq[stem] = n
            self.issued[k] = f'{pre}-{start + n:06d}'
        return self.issued[k]


class MissingValue(Exception):
    """missing_rule=stop 命中：按契 6「不确定就停」，整组不写。"""


def parse_rule(rule) -> tuple[str, set[str]]:
    """missing_rule → (mode, tokens)。

    mode 三档：
      default 只把空串当缺（留空时的默认）
      keep    不判缺，源值原样照写（空串也保留）
      stop    空串或标记命中就停（契 6：不确定就停）
      tokens  指定标记（`NA|NULL|.`）与空串都当缺
    """
    rule = (rule or '').strip()
    low = rule.lower()
    if low in ('keep', 'raw', 'asis', 'as-is'):
        return 'keep', set()
    if low in ('stop', 'halt'):
        return 'stop', {''}
    if not rule:
        return 'default', {''}
    toks = {t.strip().lower() for t in re.split(r'[|,;]', rule) if t.strip()}
    return 'tokens', toks | {''}


def value_of(map_row, src_row, mint: Mint):
    """一格的值：先判缺（missing_rule），再 transform（const / mint），最后取源列原值。"""
    tr = (map_row.get('transform') or '').strip()
    scol = map_row.get('src_column')
    mode, toks = parse_rule(map_row.get('missing_rule'))
    v = src_row.get(scol)
    is_missing = v is None or (isinstance(v, str) and not v.strip())
    if not is_missing and isinstance(v, str) and mode == 'tokens' and v.strip().lower() in toks:
        is_missing = True
    if tr.startswith('const:'):
        return tr.split(':', 1)[1] or None
    if tr.startswith('mint:'):
        # 发号必须有键：空键发号会把所有空值并成一个人，比不建更坏
        if is_missing:
            raise MissingValue(f'{scol} 为空，不能发号')
        return mint.uid(tr.split(':', 1)[1], v.strip() if isinstance(v, str) else v)
    if tr.startswith('map:'):
        # map:Male=M|Female=F —— 把源侧字面值译成库内受控词；未登记的键按原值保留（由门禁 D62 抓）
        table = {}
        for pair in tr[len('map:'):].split('|'):
            if not pair.strip():
                continue
            k, _, val = pair.partition('=')
            table[k.strip()] = val.strip() or None
        if is_missing:
            return None
        key = v.strip() if isinstance(v, str) else v
        return table.get(str(key), key)
    if mode == 'keep':
        return v.strip() if isinstance(v, str) else v
    if is_missing:
        if mode == 'stop':
            raise MissingValue(f'{scol} 为空')
        return None
    return v.strip() if isinstance(v, str) else v


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
        if self.kind == 'sheet' and path.suffix.lower() in ('.csv', '.tsv'):
            self.kind = 'csv'

    def load(self, sheet: str, header_row: int):
        if self.kind == 'sqlite':
            c = sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)
            cur = c.execute(f'SELECT rowid, * FROM "{sheet}"')
            cols = [d[0] for d in cur.description][1:]
            rows = [dict(zip(cols, r[1:]), _row=r[0]) for r in cur]
            c.close()
            return cols, rows
        if self.kind == 'csv':
            # 清洗副本按 CSV 交付（源文件不动）：sheet 名忽略，表头行与 xlsx 同义
            delim = '\t' if self.path.suffix.lower() == '.tsv' else ','
            with self.path.open(encoding='utf-8-sig', newline='') as fh:
                it = csv.reader(fh, delimiter=delim)
                for _ in range(max(header_row - 1, 0)):
                    next(it, None)
                header = [h.strip() for h in next(it)]
                rows = []
                for i, vals in enumerate(it, start=header_row + 1):
                    rows.append({h: v for h, v in zip(header, vals) if h} | {'_row': i})
            return header, rows
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
            order = ([t for t in tabs if t.endswith('_value') or t.endswith('_val')]
                     + [t for t in tabs if t.startswith('_modules')]
                     + [t for t in ('clin', 'qc', 'person', 'src') if t in tabs])
            for t in order:
                conn.execute(f'DELETE FROM "{t}"')
            conn.commit()
        src_total = {}
        for i, (key, rec) in enumerate(sorted(src_reg.items()), start=1):
            skind = Source(rec['path']).kind
            kind = 'old_db_blob' if skind == 'sqlite' else 'workbook'
            counts = {sheet: sheet_rows[(key[0], sheet)] for sheet in rec['sheets']}
            merged = sum(n for sheet, n in counts.items() if (key[0], sheet) in clin_sheets)
            # map 里只要有 clin 目标，src.rows 就只记「并入 clin 的行数」；
            # 全库没有 clin 目标时（纯模块库）才退回总行数，否则 D50 会拿非 clin 源凑数。
            total = merged if clin_sheets else sum(counts.values())
            h = sha256(rec['path'])
            rel = rec['path'].name
            note = (f'{a.family}/{a.slug} 试点回填；sheets='
                    + ','.join(f'{s}:{n}' for s, n in counts.items()))
            if skind == 'csv':
                note += '；cleaned_copy=1（源文件见 dict.source/目录说明）'
            print(f'src {i}: {rel}  kind={kind}  sha256={h[:12]}…  rows={total}  {note}')
            if not a.dry_run:
                conn.execute(f'INSERT INTO src({",".join(SRC_COLS)}) VALUES ({",".join("?" * len(SRC_COLS))})',
                             (i, kind, 'active', rel, h, total, note))
            src_total[key] = total
            rec['src_id'] = i
        src_id_of = {f: r['src_id'] for (f,), r in src_reg.items()}
        mint = Mint()   # 跨 sheet 共用：person 先发号，clin 用同一源值拿到同一个 family_uid

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
            # FK 顺序：src → person → clin → _modules_* → *_value（表内 FK 是 REFERENCES person/src）
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
                src_cols = [(r['target_column'], r['src_column'], r['transform'], r['missing_rule'])
                            for r in items]
                missing_src = [s for _, s, tr, _ in src_cols
                               if s not in cols and not (tr or '').strip().startswith('const:')]
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
                    for tcol, scol, tr, mr in src_cols:
                        try:
                            rec[tcol] = value_of({'src_column': scol, 'transform': tr,
                                                  'missing_rule': mr}, s, mint)
                        except MissingValue as e:
                            print(f'STOP {table}: missing_rule=stop 命中'
                                  f'（源 {src_file}#{sheet} 第 {s["_row"]} 行：{e}）')
                            rc = 1
                            return rc
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
