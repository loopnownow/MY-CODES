# -*- coding: utf-8 -*-
"""打平 _share 的 ethics/thesis 子目录：<slug>/<文件> → <slug>__<文件>。

口径：《_DataBase》_share 层级 ≤2，ethics/thesis 不打子目录，档名用
`<slug>__<原文件名>`（嵌套目录用 `__` 连接）。已打平的（直接位于 thesis/ethics 下）
不再处理；目标同名同 sha 视为重复件，移 {DEL} dups；同名不同 sha 报错停下。

用法：
  python -B tools/db-library/flatten_share.py --family MHE_AN            # dry-run
  python -B tools/db-library/flatten_share.py --family MHE_AN --apply    # 落盘
  python -B tools/db-library/flatten_share.py --family MHE_AN --apply --ledger "...\\FLAT.tsv"
退出码：0 正常；1 冲突/失败。sha256 移动前后全验。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_BATCH = "2026-11-db-share"


def plan_moves(fam_dir):
    """返回 [(class_dir, slug_dir, src, target)]。"""
    share = Path(fam_dir) / "_share"
    moves = []
    if not share.is_dir():
        return moves
    for cls in core.SHARE_CLASSES:
        cdir = share / cls
        if not cdir.is_dir():
            continue
        for sub in sorted(p for p in cdir.iterdir() if p.is_dir()):
            for p in sorted(x for x in sub.rglob("*") if x.is_file()):
                rel = p.relative_to(sub).as_posix().replace("/", "__")
                moves.append((cdir, sub, p, cdir / f"{sub.name}__{rel}"))
    return moves


def cleanup_empty_dirs(fam_dir):
    """只 rmdir _share/{thesis,ethics} 下的空子目录（不删文件）。"""
    share = Path(fam_dir) / "_share"
    for cls in core.SHARE_CLASSES:
        cdir = share / cls
        if not cdir.is_dir():
            continue
        dirs = sorted((p for p in cdir.rglob("*") if p.is_dir()),
                      key=lambda p: len(p.parts), reverse=True)
        for d in dirs:
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
    batch = core.batch_dir(args.batch)
    moves = plan_moves(fam_dir)
    if not moves:
        print("无需打平：_share/thesis|ethics 下没有子目录")
        return 0

    conflicts, dups = [], []
    for cdir, sub, src, target in moves:
        if target.exists():
            if sha256(src) == sha256(target):
                dups.append((src, target))
            else:
                conflicts.append((src, target))
    print(f"计划: 打平 {len(moves)} 个文件（其中同名同 sha 重复 {len(dups)} 个）")
    for cdir, sub, src, target in moves:
        kind = "dup " if (src, target) in dups else "move"
        print(f"  [{kind}] {src.relative_to(fam_dir).as_posix()}"
              f" -> {target.relative_to(fam_dir).as_posix()}")
    if conflicts:
        for src, target in conflicts[:20]:
            print(f"! 冲突（同名不同 sha）: {src} -> {target}", file=sys.stderr)
        print(f"FAIL 同名不同 sha 冲突 {len(conflicts)} 个，请人工处理后再跑", file=sys.stderr)
        return 1
    if not args.apply:
        print("dry-run：未落盘（加 --apply 执行）")
        return 0

    ledger = []
    for cdir, sub, src, target in moves:
        src_rel = src.relative_to(fam_dir).as_posix()
        if target.exists():
            dst = batch / "dups" / args.family / src_rel
            h, size = core.move_verified(src, dst)
            ledger.append(("dup", src_rel, dst.as_posix(), size, h, target.relative_to(fam_dir).as_posix()))
        else:
            h, size = core.move_verified(src, target)
            ledger.append(("flatten", src_rel, target.relative_to(fam_dir).as_posix(),
                           size, h, ""))
    cleanup_empty_dirs(fam_dir)
    man = Path(args.ledger) if args.ledger else (
        batch / f"FLATTEN-{args.family}-{datetime.now():%Y%m%d}.tsv")
    core.write_ledger(man, ("kind", "rel_path", "dst", "size", "sha256", "item"), ledger)
    core.append_readme_note(fam_dir / "_share",
                            f"- {datetime.now():%Y-%m-%d} flatten_share：ethics/thesis 打平 {len(moves)} 个文件，台账 {man.name}")
    print(f"完成：{len(ledger)} 行 → {man}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="_share/ethics|thesis 打平（<slug>/<文件> → <slug>__<文件>）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认 dry-run；--apply 落盘并写台账；目标同名同 sha 的源移 {DEL} dups。")
    ap.add_argument("--family", required=True, help="家族名，如 MHE_AN")
    ap.add_argument("--code", default=None, help="短码（缺省读库 meta.short_code）")
    ap.add_argument("--db-root", type=Path, default=core.DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {core.DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--apply", action="store_true", help="落盘（缺省 dry-run）")
    ap.add_argument("--ledger", type=Path, default=None, help="台账路径（缺省 <batch>/FLATTEN-<FAM>-<date>.tsv）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
