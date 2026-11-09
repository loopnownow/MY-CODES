# -*- coding: utf-8 -*-
"""叶内 raw（非表、非归档）按单元压档上收到 `_share/<leaf>/`。

口径：叶内只留 `目录说明.md`；零散原始件（图片、原始导出、文档等）按「单元」打包：
单元 = 叶下第一层子目录名，直属松散文件归 `loose`。档名
`<code>__<leaf>__<unit>-raw-<YYYY-MM-DD>.tar.zst`，落 `_share/<leaf>/`；
逐件 sha256 记台账，原件移 {DEL} originals 备查，叶内 目录说明.md 记 raw→档 映射。

用法：
  python -B tools/db-library/leaf_raw_pack.py --family MHE_AN            # dry-run
  python -B tools/db-library/leaf_raw_pack.py --family MHE_AN --apply    # 打包+上收
退出码：0 正常；1 失败。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_BATCH = "2026-11-db-share"


def is_skipped(name):
    return name == core.LEAF_README or core.is_table(name) or core.is_archive(name)


def collect_raw(leaf):
    """返回 {unit: [(rel_to_leaf, path)]}。"""
    groups = {}
    for p in sorted(leaf.rglob("*")):
        if not p.is_file() or is_skipped(p.name):
            continue
        rel = p.relative_to(leaf)
        unit = rel.parts[0] if len(rel.parts) > 1 else "loose"
        groups.setdefault(unit, []).append((rel.as_posix(), p))
    return groups


def pack_name(code, leaf_name, unit, stamp):
    return f"{code}__{leaf_name}__{unit}-raw-{stamp}.tar.zst"


def main(args):
    db_root = Path(args.db_root)
    fam_dir = db_root / args.family
    if not fam_dir.is_dir():
        print(f"FAIL 家族目录不存在: {fam_dir}", file=sys.stderr)
        return 1
    code = args.code or core.read_code(db_root, args.family) or args.family
    batch = core.DEL_ROOT / args.batch
    stamp = args.stamp or f"{datetime.now():%Y-%m-%d}"
    plans = []
    for leaf in core.leaf_dirs(fam_dir):
        for unit, items in sorted(collect_raw(leaf).items()):
            plans.append((leaf, unit, items))
    if not plans:
        print("无需打包：叶内没有 raw 文件")
        return 0

    total = sum(len(items) for _l, _u, items in plans)
    print(f"计划: {len(plans)} 个单元 / {total} 个 raw 文件 → _share/<leaf>/")
    for leaf, unit, items in plans:
        arc = fam_dir / "_share" / leaf.name / pack_name(code, leaf.name, unit, stamp)
        print(f"  [{leaf.name}/{unit}] {len(items)} 文件 -> "
              f"{arc.relative_to(fam_dir).as_posix()}")
    if not args.apply:
        print("dry-run：未落盘（加 --apply 执行）")
        return 0

    ledger = []
    for leaf, unit, items in plans:
        arc = fam_dir / "_share" / leaf.name / pack_name(code, leaf.name, unit, stamp)
        entries = [rel for rel, p in items]
        if arc.exists():
            if set(core.tar_list(arc)) != set(entries):
                raise RuntimeError(f"同日档已存在但成员不一致: {arc}")
            print(f"  = 已存在，跳过打包: {arc.name}")
        else:
            core.pack_tar_zst(entries, leaf, arc)
            got = core.tar_list(arc)
            if set(got) != set(entries):
                raise RuntimeError(f"打包成员校验失败: {arc}")
        arc_sha = sha256(arc)
        for rel, p in items:
            h, size = sha256(p), p.stat().st_size
            dst = batch / "originals" / code / args.family / leaf.name / rel
            core.move_verified(p, dst, expect_sha=h)
            ledger.append((unit, arc.relative_to(fam_dir).as_posix(), rel, size, h, arc_sha))
        core.append_readme_note(
            leaf,
            f"- {stamp} raw 单元 `{unit}`（{len(items)} 件）→ `_share/{leaf.name}/{arc.name}`")
        print(f"  + {arc.name}: {len(items)} 件，档 sha256 {arc_sha[:12]}")
    for leaf in {leaf for leaf, _u, _i in plans}:
        for d in sorted((p for p in leaf.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass

    man = Path(args.ledger) if args.ledger else (
        batch / f"RAW-PACK-{args.family}-{datetime.now():%Y%m%d}.tsv")
    core.write_ledger(man, ("unit", "arc", "rel_path", "size", "sha256", "arc_sha256"), ledger)
    core.append_readme_note(fam_dir / "_share",
                            f"- {datetime.now():%Y-%m-%d} leaf_raw_pack：打包 {len(plans)} 个 raw 单元，台账 {man.name}")
    print(f"完成：{len(ledger)} 行 → {man}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="叶内 raw 按单元压档（tar.zst）上收到 _share/<leaf>/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认 dry-run；--apply 打包、逐件 sha256 记台账、原件移 {DEL}。")
    ap.add_argument("--family", required=True, help="家族名，如 MHE_AN")
    ap.add_argument("--code", default=None, help="短码（缺省读库 meta.short_code）")
    ap.add_argument("--db-root", type=Path, default=core.DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {core.DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--stamp", default=None, help="档名日期（缺省今天，YYYY-MM-DD）")
    ap.add_argument("--apply", action="store_true", help="落盘（缺省 dry-run）")
    ap.add_argument("--ledger", type=Path, default=None,
                    help="台账路径（缺省 <batch>/RAW-PACK-<FAM>-<date>.tsv）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
