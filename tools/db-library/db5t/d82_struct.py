#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""d82_struct.py — D82 收尾：框架表结构列的空 source 记成 `SCHEMA` 句。

家族库骨架表（`meta` / `person` / `qc` / `src` / `id` / `id_number` /
`ref_project` / `ref_study` / `ref_batch`）的列没有源表可指，`dict.source`
空着会被 D82 记债（分级 `other` / `src`）。这些是结构列，按 rules 记
`SCHEMA §2 <table>：结构列（<column>）` 即可，不动任何数据。

只碰两件事：**框架表** + **source 为空**。业务表（`clin` / `_modules_*` /
`*_val`）一行都不动——那些必须追到源列，空着就留债。

用法：
  python -B d82_struct.py --all                        # 干跑，只打印
  DB5T_ROOT=D:/MyProjects/_DataBase python -B d82_struct.py --all
  DB5T_ROOT=D:/MyProjects/_DataBase python -B d82_struct.py --all --go
  python -B d82_struct.py --db D:/MyProjects/_DataBase/HZ --go

`--go`：写前整库 copy 到 `_work/d82_struct_bak/`（同名复用），一家一个事务，
写完重扫并与「写前债 − 本次写入行数」对账，不一致即回滚。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import d82 as D82

BATCH = Path(os.environ.get('DB5T_BATCH') or r'D:\MyProjects\.Del\_by-function\db5t-pilot-20261124')
WORK = Path(os.environ.get('DB5T_WORK') or (BATCH / '_work'))
BACKUP_DIR = WORK / 'd82_struct_bak'

# 框架表：家族库骨架，列不是业务变量，没有源表列可指。
FRAME = ('meta', 'person', 'qc', 'src', 'id', 'id_number', 'dict',
         'ref_project', 'ref_study', 'ref_batch')

SQL_PLAN = """
select coalesce(scope, ''), coalesce(table_name, ''), column_name
  from dict
 where trim(coalesce(source, '')) = ''
   and coalesce(table_name, '') in (%s)
 order by table_name, column_name, scope
""" % ','.join('?' * len(FRAME))

SQL_EMPTY = """
select coalesce(scope, ''), coalesce(table_name, ''), column_name
  from dict
 where trim(coalesce(source, '')) = ''
 order by table_name, column_name, scope
"""


def fam_dbs(root: Path):
    for fam in sorted(root.iterdir()):
        db = fam / '_PHI' / (fam.name + 'db')
        if db.is_file():
            yield fam.name, db


def plan(conn):
    """只返回 D82 真计债的 EMPTY 行（SYS 结构列、非框架表都不算）。"""
    debt_empty = {(t, c, g) for t, c, g, k, _s in D82.scan(conn)['rows']
                  if k == 'EMPTY' and t in FRAME}
    out = []
    for scope, table, column in conn.execute(SQL_EMPTY):
        g = D82.grade_of(table)
        if (table, column, g) not in debt_empty:
            continue
        out.append((scope, table, column, g))
    return out


def sentence(table, column):
    return 'SCHEMA §2 %s：结构列（%s）' % (table, column)


def backup(db_path: Path):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = BACKUP_DIR / db_path.name
    if not dst.exists():
        shutil.copy2(db_path, dst)
    return dst


def write_rows(db_path: Path, rows):
    if not rows:
        return 0
    bak = backup(db_path)
    conn = sqlite3.connect(db_path)
    before = D82.scan(conn)['debt']['total']
    try:
        conn.execute('BEGIN IMMEDIATE')
        for scope, table, column, _g in rows:
            cur = conn.execute(
                "update dict set source=? "
                "where trim(coalesce(source,''))='' and coalesce(scope,'')=? "
                "and coalesce(table_name,'')=? and column_name=?",
                (sentence(table, column), scope, table, column))
            if cur.rowcount != 1:
                raise RuntimeError('%s: %s.%s 命中 %d 行（期望 1）'
                                   % (db_path.name, table, column, cur.rowcount))
        after = D82.scan(conn)['debt']['total']
        if after != before - len(rows):
            raise RuntimeError('%s: 对账失败 写前 %d − %d ≠ 写后 %d'
                               % (db_path.name, before, len(rows), after))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print('  写入 %3d 行，债 %d → %d（备份 %s）'
          % (len(rows), before, after, bak.name))
    return len(rows)


def one(db_path: Path, go: bool):
    conn = sqlite3.connect('file:%s?mode=ro' % db_path.as_posix(), uri=True)
    try:
        rows = plan(conn)
    finally:
        conn.close()
    if not rows:
        return 0
    print('  %-8s 框架表空 source %d 行' % (db_path.parent.parent.name, len(rows)))
    from collections import Counter
    for (t, n) in sorted(Counter(r[1] for r in rows).items(), key=lambda x: -x[1]):
        print('      %-12s %d' % (t, n))
    if not go:
        return len(rows)
    return write_rows(db_path, rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description='D82 框架表结构列记账（SCHEMA 句）')
    ap.add_argument('--all', action='store_true', help='所有家族')
    ap.add_argument('--db', help='单个家族目录（含 _PHI/<FAM>db）')
    ap.add_argument('--go', action='store_true', help='落库')
    args = ap.parse_args(argv)

    if args.all:
        root = Path(os.environ.get('DB5T_ROOT') or r'D:\MyProjects\_DataBase')
        targets = list(fam_dbs(root))
        print('库根 %s 下 %d 家' % (root, len(targets)))
    elif args.db:
        fam = Path(args.db)
        targets = [(fam.name, fam / '_PHI' / (fam.name + 'db'))]
    else:
        ap.error('要么 --all，要么 --db')

    total = 0
    for _fam, db in targets:
        if not db.is_file():
            print('  ! 缺家族库 %s' % db)
            continue
        total += one(db, args.go)
    print('合计 %d 行%s' % (total, '' if args.go else '（干跑；--go 才落库）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
