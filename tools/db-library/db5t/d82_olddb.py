#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D82 认旧库：把「只剩占位符 / 自由文本」的 clin 债写成熟「旧库迁移」句。

依据（2026-11-27 主人裁定，rules workspace-layout「clin 可追口径」）：
- 家族确有一份迁移前旧库快照（`_work\\<batch>\\ledger8\\backup\\20261124-181106\\<FAM>db`）；
- 该列来源只能是占位词（`源表` / `source` / `旧库` / `blob` / 自由文本），不含具体工作簿名；
- 写入句：`派生：旧库迁移——家族自 <FAM>db@2026-11-24 迁入，列级原件未登记（待回原件核对）`。
  d82.PAT_DERIVED_OLDDB 认这个形态，clin 只报数不记债。
- 不碰：source 里点名了具体工作簿的（交给 d82_complete / 人工）、已是可追/派生/SCHEMA 的。

用法：
  python -B d82_olddb.py --all          # 干跑
  python -B d82_olddb.py --all --go     # 逐家备份后写
"""
import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import d82 as D82  # noqa: E402

ROOT = Path(os.environ.get('DB5T_ROOT') or r'D:\MyProjects\_DataBase')
WORK = Path(os.environ.get('D82_WORK') or r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124\_work')
SNAP = Path(os.environ.get('D82_SNAP') or r'D:\MyProjects\.Del\_by-function\t2250u-db-merge\ledger8\backup\20261124-181106')
SNAP_DATE = '2026-11-24'
BACKUP_DIR = WORK / 'd82_olddb_bak'
PLAN_CSV = WORK / 'd82_olddb_plan.csv'

RE_HAS_FILE = re.compile(r'[^\s#|!]+?\.(?:xlsx|xls|csv|XLSX|XLS|CSV)(?:#|$|\s|\|)')
TPL = ('派生：旧库迁移——家族自 {db}@{date} 迁入，列级原件未登记（待回原件核对）')


def fam_db(fam):
    return fam / '_PHI' / (fam.name + 'db')


def plan(conn, fam_name, snap_db):
    st = D82.scan(conn)
    out = []
    for table, column, grade, kind, source in st['rows']:
        if kind != 'BAD' or grade != 'clin':
            continue  # 旧库迁移句只对 clin 生效（其它分级不收派生句）
        s = (source or '').strip()
        if RE_HAS_FILE.search(s):
            continue  # 点名了工作簿：交给 complete 或人工，不冒充旧库迁移
        sent = TPL.format(db=snap_db, date=SNAP_DATE)
        scopes = [r[0] for r in conn.execute(
            "select coalesce(scope,'') from dict where coalesce(table_name,'')=?"
            " and column_name=? and coalesce(source,'')=?", (table, column, source))]
        for sc in (scopes or ['']):
            out.append({'fam': fam_name, 'table': table, 'column': column,
                        'scope': sc, 'source': sent, 'orig': source})
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
    print('  写入 %3d 行，债 %d → %d' % (len(rows), before, after))
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
    targets = families() if a.all else [Path(a.db)]
    all_rows, total, skipped = [], 0, []
    for fam in targets:
        snap_db = fam.name + 'db'
        if not (SNAP / snap_db).is_file():
            skipped.append(fam.name)
            continue
        db = fam_db(fam)
        rows = plan(sqlite3.connect('file:%s?mode=ro' % db, uri=True), fam.name, snap_db)
        all_rows.extend(rows)
        if rows:
            print('%s: 认旧库 %d 行' % (fam.name, len(rows)))
            if a.go:
                total += write_rows(db, rows)
    write_plan(all_rows)
    if skipped:
        print('无快照跳过：%s' % '、'.join(skipped))
    print('合计认旧库 %d 行；计划 %s' % (len(all_rows), PLAN_CSV))
    print('（干跑；加 --go 才落库）' if not a.go else '合计写入 %d 行' % total)
    return 0


if __name__ == '__main__':
    sys.exit(main())
