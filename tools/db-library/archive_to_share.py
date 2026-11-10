# -*- coding: utf-8 -*-
"""把叶内 / 家族根的压缩档归位到 `<family>\\_share\\<leaf>\\`。

口径：压缩档只放 `_share`；叶内只留 `目录说明.md`。本 CLI 把错位的归档
（叶内 `<leaf>/**/*.7z|zip|rar|tar.zst|…`、家族根直属归档）移到
`_share/<leaf>/<档名>`（家族根直属 → `_share/<档名>`）。已存在同 sha 目标视为重复件，
源移 {DEL} dups；同名不同 sha 则报错停下（不落盘）。

用法：
  python -B tools/db-library/archive_to_share.py --family MHE_AN            # dry-run
  python -B tools/db-library/archive_to_share.py --family MHE_AN --apply    # 落盘
退出码：0 正常；1 冲突/失败。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_BATCH = "2026-11-db-share"


def find_archives(fam_dir):
    """[(src, leaf_name or None)]：叶内归档 + 家族根直属归档。"""
    items = []
    for leaf in core.leaf_dirs(fam_dir):
        for p in sorted(leaf.rglob("*")):
            if p.is_file() and core.is_archive(p.name):
                items.append((p, leaf.name))
    for p in sorted(fam_dir.iterdir()):
        if p.is_file() and core.is_archive(p.name):
            items.append((p, None))
    return items


def target_of(fam_dir, arc, leaf_name):
    if leaf_name is None:
        return fam_dir / "_share" / arc.name
    return fam_dir / "_share" / leaf_name / arc.name


def main(args):
    db_root = Path(args.db_root)
    fam_dir = db_root / args.family
    if not fam_dir.is_dir():
        print(f"FAIL 家族目录不存在: {fam_dir}", file=sys.stderr)
        return 1
    batch = core.batch_dir(args.batch)
    items = find_archives(fam_dir)
    if not items:
        print("无需归位：叶内与家族根没有压缩档")
        return 0

    conflicts, dups, todo = [], [], []
    for src, leaf in items:
        target = target_of(fam_dir, src, leaf)
        if target.exists():
            if sha256(src) == sha256(target):
                dups.append((src, leaf, target))
            else:
                conflicts.append((src, leaf, target))
        else:
            todo.append((src, leaf, target))
    print(f"计划: 归位 {len(todo)} 个归档，同名同 sha 重复 {len(dups)} 个")
    for src, leaf in items:
        target = target_of(fam_dir, src, leaf)
        mark = "dup " if any(src == d[0] for d in dups) else "move"
        print(f"  [{mark}] {src.relative_to(fam_dir).as_posix()}"
              f" -> {target.relative_to(fam_dir).as_posix()}")
    if conflicts:
        for src, leaf, target in conflicts[:20]:
            print(f"! 冲突（同名不同 sha）: {src} vs {target}", file=sys.stderr)
        print(f"FAIL 同名不同 sha 冲突 {len(conflicts)} 个，请人工处理后再跑", file=sys.stderr)
        return 1
    if not args.apply:
        print("dry-run：未落盘（加 --apply 执行）")
        return 0

    ledger = []
    for src, leaf, target in todo + dups:
        src_rel = src.relative_to(fam_dir).as_posix()
        if (src, leaf, target) in dups:
            dst = batch / "dups" / args.family / src_rel
            h, size = core.move_verified(src, dst)
            ledger.append(("dup", src_rel, dst.as_posix(), size, h,
                           target.relative_to(fam_dir).as_posix()))
        else:
            h, size = core.move_verified(src, target)
            ledger.append(("archive", src_rel, target.relative_to(fam_dir).as_posix(),
                           size, h, leaf or args.family))
    man = Path(args.ledger) if args.ledger else (
        batch / f"ARCHIVE-TO-SHARE-{args.family}-{datetime.now():%Y%m%d}.tsv")
    core.write_ledger(man, ("kind", "rel_path", "dst", "size", "sha256", "leaf"), ledger)
    core.append_readme_note(fam_dir / "_share",
                            f"- {datetime.now():%Y-%m-%d} archive_to_share：归位归档 {len(todo)} 个，台账 {man.name}")
    print(f"完成：{len(ledger)} 行 → {man}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="族内错位压缩档归位到 _share（叶内/家族根 → _share/<leaf>/）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认 dry-run；--apply 落盘并写台账；同名同 sha 的源移 {DEL} dups。")
    ap.add_argument("--family", required=True, help="家族名，如 MHE_AN")
    ap.add_argument("--db-root", type=Path, default=core.DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {core.DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--apply", action="store_true", help="落盘（缺省 dry-run）")
    ap.add_argument("--ledger", type=Path, default=None,
                    help="台账路径（缺省 <batch>/ARCHIVE-TO-SHARE-<FAM>-<date>.tsv）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
