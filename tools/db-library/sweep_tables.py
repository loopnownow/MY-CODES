# -*- coding: utf-8 -*-
"""叶内散表入库：`<family>\\<leaf>\\**\\*.xlsx|xls|csv` → 库，原件移 {DEL} originals。

口径：叶内只留 `目录说明.md`；表格入库后原件按
`{DEL}/_by-function/<batch>/originals/<code>/<family>/<leaf>/<叶内相对路径>`
归位（保留相对路径）。档内表请用 `build_code_db.py --share-archives` 或
`archive_ingest.py`；叶内残留压缩档本 CLI 只告警，不处理。

用法：
  python -B tools/db-library/sweep_tables.py --family MHE_AN            # dry-run
  python -B tools/db-library/sweep_tables.py --family MHE_AN --apply    # 入库+移原件
退出码：0 正常；1 失败。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import db_core as core

DEFAULT_BATCH = "2026-11-db-share"


def plan_tables(fam_dir):
    """[(leaf_path, table_path)]：叶内全部散表。"""
    out = []
    for leaf in core.leaf_dirs(fam_dir):
        for p in core.table_files(leaf):
            out.append((leaf, p))
    return out


def find_leaf_archives(fam_dir):
    out = []
    for leaf in core.leaf_dirs(fam_dir):
        for p in sorted(leaf.rglob("*")):
            if p.is_file() and core.is_archive(p.name):
                out.append(p)
    return out


def cleanup_empty_dirs(leaf):
    leaf = Path(leaf)
    for d in sorted((p for p in leaf.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def main(args):
    db_root = Path(args.db_root)
    fam_dir = db_root / args.family
    if not fam_dir.is_dir():
        print(f"FAIL 家族目录不存在: {fam_dir}", file=sys.stderr)
        return 1
    db = Path(args.db) if args.db else core.family_db(db_root, args.family)
    if not db.is_file():
        print(f"FAIL 库不存在: {db}（先跑 build_code_db.py）", file=sys.stderr)
        return 1
    code = args.code or core.read_code(db_root, args.family) or args.family
    batch = core.batch_dir(args.batch)
    tables = plan_tables(fam_dir)
    arcs = find_leaf_archives(fam_dir)
    for a in arcs:
        print(f"! 叶内残留压缩档: {a.relative_to(fam_dir).as_posix()}"
              f"（请先跑 archive_to_share.py）", file=sys.stderr)
    if not tables:
        print("无需入库：叶内没有散表")
        return 0

    print(f"计划: 入库 {len(tables)} 张散表，原件移 originals/<code>/<family>/<leaf>/")
    for leaf, p in tables:
        rel = p.relative_to(leaf).as_posix()
        print(f"  [{leaf.name}] {p.relative_to(fam_dir).as_posix()}"
              f" -> {code}/{args.family}/{leaf.name}/{rel}")
    if not args.apply:
        print("dry-run：未落盘（加 --apply 执行）")
        return 0

    con = core.open_db(db)
    try:
        ing = core.Ingestor(con, code, mask_phi=args.mask_phi)
    except ValueError as e:
        con.close()
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    ledger = []
    ns = 0
    try:
        for leaf, p in tables:
            rel = leaf.name + "/" + p.relative_to(leaf).as_posix()
            _, added = ing.ingest(p, rel, args.family, leaf.name, leaf.name, incremental=True)
            ns += added
            dst = batch / "originals" / code / args.family / leaf.name / p.relative_to(leaf)
            h, size = core.move_verified(p, dst)
            ledger.append(("table", rel, dst.as_posix(), size, h, ""))
            print(f"  + {rel}  (新增 sheet {added})")
        core.refresh_meta_counts(con)
        con.commit()
    finally:
        con.close()
    for leaf, _p in tables:
        cleanup_empty_dirs(leaf)

    man = Path(args.ledger) if args.ledger else (
        batch / f"SWEEP-TABLES-{args.family}-{datetime.now():%Y%m%d}.tsv")
    core.write_ledger(man, ("kind", "rel_path", "dst", "size", "sha256", "item"), ledger)
    core.append_readme_note(fam_dir / "_share",
                            f"- {datetime.now():%Y-%m-%d} sweep_tables：入库 {len(tables)} 张散表、"
                            f"新增 {ns} 张 sheet，台账 {man.name}")
    print(f"完成：{len(ledger)} 行 → {man}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="叶内散表入库，原件移 {DEL} originals/<code>/<family>/<leaf>/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认 dry-run；--apply 入库并移原件、写台账。档内表请用 --share-archives/archive_ingest。")
    ap.add_argument("--family", required=True, help="家族名，如 MHE_AN")
    ap.add_argument("--db", type=Path, default=None, help="库路径（缺省 <root>/<family>/_db/<family>db）")
    ap.add_argument("--code", default=None, help="短码（缺省读库 meta.short_code）")
    ap.add_argument("--db-root", type=Path, default=core.DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {core.DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--apply", action="store_true", help="落盘（缺省 dry-run）")
    ap.add_argument("--mask-phi", action="store_true",
                    help="fact 层 身份证|电话|手机|邮箱|地址 值假名化（RDL 等家族口径）")
    ap.add_argument("--ledger", type=Path, default=None,
                    help="台账路径（缺省 <batch>/SWEEP-TABLES-<FAM>-<date>.tsv）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
