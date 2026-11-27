#!/usr/bin/env python
"""d82_debt.py — 把 D82 债务基线写进家族库 `meta.d82_debt_rows`（C 路线记账）。

背景：D82 从「只报数」改为「按表分级」——clin/src 要求可追，`*_val` 长表允许 `派生：`，
结构列不参与。剩下的不可追行是既有债，逐家记进 meta，门禁只在「高于基线」时判红。
判据只有一份，在 `d82.py`；本脚本只负责算账与落账。

布局：5t 家族库在 `<fam>/_PHI/<fam>db`；迁移前的旧树在 `<fam>/_db/<fam>db`，两种都认
（旧树的 dict 同样参与分级，先落基线，迁移后不必重算）。

用法：
  python -B d82_debt.py --all              # 干跑：打印每家债务与基线差异
  python -B d82_debt.py --all --go         # 写 meta.d82_debt_rows
  python -B d82_debt.py MHE_AN LC --go     # 只点名几家
  DB5T_ROOT=D:/MyProjects/_DataBase ...    # 默认对批次 stage 跑；切库后要指真库

只改家族库自身 meta，不动子库内容，因此不需要重跑 fix_sha.py。
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
from pathlib import Path

import paths as P
import d82 as D82

STAGE = Path(os.environ.get('DB5T_ROOT') or P.STAGE)


def fam_db(fam):
    """家族库路径：优先 5t 的 `_PHI`，回落迁移前旧树的 `_db`；找不到返回 None。"""
    for bucket in ('_PHI', '_db'):
        p = STAGE / fam / bucket / f'{fam}db'
        if p.exists():
            return p
    return None


def families():
    out = []
    for p in sorted(STAGE.iterdir()):
        if not p.is_dir() or p.name.startswith(('_', '.')):
            continue
        if fam_db(p.name) is not None:
            out.append(p.name)
    return out


def one(fam, go, day):
    fam_db_p = fam_db(fam)
    conn = sqlite3.connect(f'file:{fam_db_p.as_posix()}?mode=ro', uri=True)
    try:
        stats = D82.scan(conn)
        base = D82.load_baseline(conn)
    finally:
        conn.close()
    d = stats['debt']
    cur = d.get('total', 0)
    if base is None:
        delta = '基线 -'
    else:
        old = base.get('total') or 0
        delta = '一致' if old == cur else f'{old}→{cur}（{cur - old:+d}）'
    print(f'{fam}: 债 {cur}（clin {d.get("clin", 0)} / src {d.get("src", 0)} / '
          f'*_val {d.get("val", 0)} / 其它 {d.get("other", 0)}） {delta}'
          f'{"  [已写]" if go else ""}')
    if go:
        D82.write_baseline(fam_db_p, stats, day)


def main(argv):
    go = '--go' in argv
    args = [a for a in argv if a != '--go']
    fams = families() if (not args or '--all' in args) else args
    day = datetime.date.today().isoformat()
    print(f'库根 {STAGE.as_posix()}  日期 {day}  {"写库" if go else "干跑（--go 才写）"}')
    for fam in fams:
        if fam_db(fam) is None:
            print(f'{fam}: 家族库不存在（_PHI / _db 都没有），跳过')
            continue
        one(fam, go, day)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
