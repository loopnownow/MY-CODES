#!/usr/bin/env python
"""fix_counts.py — 用 SQL 重算 ref_project 四计数并回填（D35 镜像门的前置修复）。

四计数一律现算，禁止「总数 − 已知数」构造：
  person_count   = count(*) from person
  clin_person_n  = count(distinct family_uid) from clin
  longitudinal_n = 有 >=2 个不同非 UNK period_month 的人数
  multi_record_n = clin 行数 >1 的人数（不论有无日期）

用法：
  python -B fix_counts.py <FAM> [<FAM> ...] [--go]
  python -B fix_counts.py --all [--go]
默认干跑，只打印差异行；--go 才写库。写完请接着跑 fix_sha.py。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import paths as P

SQL = {
    "person_count": "select count(*) from person",
    "clin_person_n": "select count(distinct family_uid) from clin",
    "longitudinal_n": (
        "select count(*) from (select family_uid from clin "
        "where period_month is not null and period_month <> 'UNK' "
        "group by family_uid having count(distinct period_month) >= 2)"
    ),
    "multi_record_n": (
        "select count(*) from (select family_uid from clin "
        "group by family_uid having count(*) > 1)"
    ),
}


def ro(p: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)


def one(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return conn.execute(sql).fetchone()[0] or 0
    except sqlite3.Error:
        return 0


def plan(fam: str) -> list[tuple[str, dict, dict]]:
    fam_db = P.ROOT / fam / "_PHI" / f"{fam}db"
    if not fam_db.exists():
        return []
    out = []
    with ro(fam_db) as c:
        rows = c.execute(
            "select slug, person_count, clin_person_n, longitudinal_n, multi_record_n from ref_project"
        ).fetchall()
    for slug, *cur in rows:
        proj = P.ROOT / fam / slug / f"{slug}db"
        if not proj.exists():
            continue
        with ro(proj) as pc:
            new = {k: one(pc, s) for k, s in SQL.items()}
        old = dict(zip(SQL.keys(), cur))
        if any(new[k] != old.get(k) for k in SQL):
            out.append((slug, old, new))
    return out


def apply(fam: str, changes: list[tuple[str, dict, dict]]) -> None:
    fam_db = P.ROOT / fam / "_PHI" / f"{fam}db"
    conn = sqlite3.connect(fam_db.as_posix())
    try:
        for slug, _, new in changes:
            conn.execute(
                "update ref_project set person_count=?, clin_person_n=?, "
                "longitudinal_n=?, multi_record_n=? where slug=?",
                (new["person_count"], new["clin_person_n"],
                 new["longitudinal_n"], new["multi_record_n"], slug),
            )
        conn.commit()
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    go = "--go" in argv
    args = [a for a in argv if a != "--go"]
    fams = ([d.name for d in P.ROOT.iterdir() if (d / "_PHI").is_dir()]
            if not args or "--all" in args else args)
    total = 0
    for fam in fams:
        changes = plan(fam)
        if not changes:
            continue
        total += len(changes)
        if go:
            apply(fam, changes)
        for slug, old, new in changes:
            diff = ", ".join(f"{k} {old.get(k)}→{new[k]}" for k in SQL if old.get(k) != new[k])
            print(f"{fam}/{slug}: {diff}{'  [已写]' if go else ''}")
    print(f"\n{'已写' if go else '干跑'}：{total} 行需回填；写后请跑 fix_sha.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
