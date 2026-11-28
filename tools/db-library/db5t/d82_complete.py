#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D82 补路径：把 `dict.source` 里的「只有文件名 / 文件名#sheet」补成可点击地址。

地址形态（与已入库的 blob 句一致）：
- 普通文件：`blob:<相对 _DataBase 的 rel_path>#<sheet>:<列名>`
- zip 成员：`blob:<zip rel_path>!<成员路径>#<sheet>:<列名>`

判据（宁可不动）：
- 只处理 kind=BAD 的债行，source 形如 `<文件>.<ext>` 或 `<文件>.<ext>#<sheet>`；
- 按 basename 在 `_DataBase` 下的普通文件与 zip 成员里找，必须唯一命中；
- sheet 名对得上（或未给 sheet 但只有一个 sheet 含该列），且表头里逐字有该列名；
- 多候选、找不到、表头不含该列的，一律不写（留给「认旧库」或人工）。

用法：
  python -B d82_complete.py --all            # 干跑，打印计划
  python -B d82_complete.py --all --go       # 逐家备份后写库
  DB5T_ROOT=... python -B d82_complete.py --db <家族目录> [--go]
"""
import argparse
import csv
import io
import os
import re
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d82 as D82  # noqa: E402

ROOT = Path(os.environ.get('DB5T_ROOT') or r'D:\MyProjects\_DataBase')
WORK = Path(os.environ.get('D82_WORK') or r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124\_work')
BACKUP_DIR = WORK / 'd82_complete_bak'
PLAN_CSV = WORK / 'd82_complete_plan.csv'

RE_FILE = re.compile(r'^(?P<name>[^#/\\!]+?\.(?:xlsx|xls|csv|XLSX|XLS|CSV))'
                     r'(?:#(?P<sheet>[^#/\\!]+))?$')
BOOK_EXT = {'.xlsx', '.xls', '.csv'}


# ---------------------------------------------------------------- 索引
def _scan_disk():
    """basename -> [('file', path) | ('zip', zippath, member)]"""
    idx = {}
    for p in ROOT.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in BOOK_EXT:
            continue
        idx.setdefault(p.name.lower(), []).append(('file', p))
    for z in ROOT.rglob('*.zip'):
        try:
            with zipfile.ZipFile(z) as zf:
                for m in zf.namelist():
                    if Path(m).suffix.lower() not in BOOK_EXT:
                        continue
                    base = Path(m.replace('\\', '/')).name.lower()
                    idx.setdefault(base, []).append(('zip', z, m))
        except (zipfile.BadZipFile, OSError):
            continue
    return idx


_HDR_CACHE = {}


def _open_book(kind, *args):
    if kind == 'file':
        path = args[0]
        if path.suffix.lower() == '.csv':
            for enc in ('utf-8-sig', 'gbk', 'utf-8'):
                try:
                    return ('csv', path.read_text(encoding=enc, errors='strict'))
                except UnicodeDecodeError:
                    continue
            return ('csv', path.read_text(encoding='utf-8', errors='replace'))
        return ('xls', path)
    z, m = args
    with zipfile.ZipFile(z) as zf:
        data = zf.read(m)
    if Path(m).suffix.lower() == '.csv':
        return ('csv', data.decode('utf-8-sig', 'replace'))
    return ('xls-bytes', data)


def _headers(kind, payload):
    """返回 [(sheet_name, set(header_cells))]"""
    if kind == 'csv':
        import csv as _csv
        rows = list(_csv.reader(payload.splitlines()[:10]))
        cols = set()
        for r in rows:
            cols.update(c.strip() for c in r if c and c.strip())
        return [('', cols)]
    if kind == 'xls':
        import openpyxl
        wb = openpyxl.load_workbook(str(payload), read_only=True, data_only=True)
        out = []
        for ws in wb.worksheets:
            cols = set()
            for i, row in enumerate(ws.iter_rows(max_row=10, values_only=True)):
                cols.update(str(c).strip() for c in row if c is not None and str(c).strip())
            out.append((ws.title, cols))
        wb.close()
        return out
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        cols = set()
        for row in ws.iter_rows(max_row=10, values_only=True):
            cols.update(str(c).strip() for c in row if c is not None and str(c).strip())
        out.append((ws.title, cols))
    wb.close()
    return out


def _source_for(kind, column, sheet, args):
    """返回可写 source，或 None。"""
    key = (kind, column, sheet, str(args))
    if key in _HDR_CACHE:
        return _HDR_CACHE[key]
    res = None
    try:
        opened = _open_book(kind, *args)
        sheets = _headers(*opened)
    except Exception:
        sheets = []
    hits = []
    for title, cols in sheets:
        if column not in cols:
            continue
        if sheet and title.lower() != sheet.lower():
            continue
        hits.append(title)
    if len(hits) == 1:
        title = hits[0]
        if kind == 'file':
            rel = args[0].relative_to(ROOT).as_posix()
            res = 'blob:%s#%s:%s' % (rel, title, column)
        else:
            z, m = args
            rel = z.relative_to(ROOT).as_posix()
            res = 'blob:%s!%s#%s:%s' % (rel, m, title, column)
    _HDR_CACHE[key] = res
    return res


# ---------------------------------------------------------------- 计划/写入
def fam_db(fam):
    return fam / '_PHI' / (fam.name + 'db')


def plan(conn, idx, fam_name):
    st = D82.scan(conn)
    out = []
    for table, column, _g, kind, source in st['rows']:
        if kind != 'BAD':
            continue
        m = RE_FILE.match(source)
        if not m:
            continue
        base = m.group('name').lower()
        sheet = m.group('sheet') or ''
        cands = idx.get(base, [])
        if not cands:
            continue
        found = set()
        for c in cands:
            if c[0] == 'file':
                s = _source_for('file', column, sheet, (c[1],))
            else:
                s = _source_for('zip', column, sheet, (c[1], c[2]))
            if s:
                found.add(s)
        if len(found) != 1:
            continue
        src = found.pop()
        scopes = [r[0] for r in conn.execute(
            "select coalesce(scope,'') from dict where coalesce(table_name,'')=?"
            " and column_name=? and coalesce(source,'')=?", (table, column, source))]
        for sc in (scopes or ['']):
            out.append({'fam': fam_name, 'table': table, 'column': column,
                        'scope': sc, 'source': src, 'orig': source})
    seen, uniq = set(), []
    for r in out:
        k = (r['table'], r['column'], r['scope'], r['orig'])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def write_plan(rows):
    cols = ['fam', 'table', 'column', 'scope', 'source']
    with PLAN_CSV.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def backup_db(db_path):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / db_path.name
    if not dst.exists():
        shutil.copy2(db_path, dst)


def write_rows(db_path, rows):
    conn = sqlite3.connect(db_path)
    try:
        before = D82.scan(conn)['debt']['total']
        backup_db(db_path)
        conn.execute('BEGIN IMMEDIATE')
        for r in rows:
            cur = conn.execute(
                "update dict set source=? where coalesce(table_name,'')=?"
                " and column_name=? and coalesce(scope,'')=? and coalesce(source,'')=?",
                (r['source'], r['table'], r['column'], r['scope'], r['orig']))
            if cur.rowcount != 1:
                raise SystemExit('命中 ≠1：%s.%s（%d）' % (r['table'], r['column'], cur.rowcount))
        after = D82.scan(conn)['debt']['total']
        if after != before - len(rows):
            raise SystemExit('对账失败：%d → %d，写 %d' % (before, after, len(rows)))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print('  写入 %3d 行，债 %d → %d（备份 %s）' % (len(rows), before, after, db_path.name))
    return len(rows)


def families():
    return [p for p in sorted(ROOT.iterdir()) if p.is_dir() and fam_db(p).is_file()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--db')
    ap.add_argument('--go', action='store_true')
    a = ap.parse_args(argv)
    if not a.all and not a.db:
        raise SystemExit('给 --all 或 --db <家族目录>')
    idx = _scan_disk()
    print('磁盘索引：%d 个文件名（含 zip 成员）' % len(idx))
    targets = families() if a.all else [Path(a.db)]
    all_rows, total = [], 0
    for fam in targets:
        db = fam_db(fam)
        rows = plan(sqlite3.connect('file:%s?mode=ro' % db, uri=True), idx, fam.name)
        all_rows.extend(rows)
        if rows:
            print('%s: 可补 %d 行' % (fam.name, len(rows)))
            for r in rows[:3]:
                print('    %s.%s → %s' % (r['table'], r['column'], r['source']))
            if a.go:
                total += write_rows(db, rows)
    write_plan(all_rows)
    print('合计可补 %d 行；计划写入 %s' % (len(all_rows), PLAN_CSV))
    print('（干跑；加 --go 才落库）' if not a.go else '合计写入 %d 行' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
