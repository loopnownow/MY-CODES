# -*- coding: utf-8 -*-
"""按家族 + 短码建 SQLite 库：<db-root>\\<family>\\_db\\<family>db

新口径（2026-11-16family 起，见 _Docs\\GitHub\\MY-RULES\\db-library 专题）：
- 表格入 <family>/_db/<family>db；_db 内只放库 + 目录说明.md
- 压缩档只在 <family>/_share/（层级 ≤2，ethics/thesis 打平为 <slug>__<name>）
- 档内表格：`--share-archives` 解到 staging 后入库，
  doc.rel_path = <FAM>/_share[/<类>]/<档名>/<成员>，台账另记 archive/member
- 叶内只留 目录说明.md；`--strict-leaf` 入库后校验残留
- 原件移 {DEL}/_by-function/<batch>/originals/<code>/<family>/<leaf>/
- 去重三层：sha → 整表（重复/子集）→ 行级；保真：每张 sheet zlib(TSV) 存 blob；
  整洁表（表头可识别、列数 ≤40）另入 fact 长表
- 溯源：doc 记源相对路径/sha/行数；去重关系进 sheet_dup

用法：
  python -B tools/db-library/build_code_db.py --family MHE_AN --code ly
  python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --leaf MHE_AN/lxf_ABCMRS
  python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --from-originals
  python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --share-archives
  python -B tools/db-library/build_code_db.py --family MHE_AN --code ly --strict-leaf --move
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_DB_ROOT = core.DEFAULT_DB_ROOT
DEFAULT_BATCH = "2026-11-db-sqlite"


def discover_leaves(db_root, code, family=None):
    """<db-root>/<family>/<slug> 中 slug == code 或以 `code_` 开头；family 给定时限定家族。"""
    leaves = []
    db_root = Path(db_root)
    if not db_root.is_dir():
        return leaves
    fams = sorted(p for p in db_root.iterdir() if p.is_dir())
    if family:
        fams = [p for p in fams if p.name == family]
    for fam in fams:
        for d in sorted(p for p in fam.iterdir() if p.is_dir()
                        and not p.name.startswith("_")):
            if d.name == code or d.name.startswith(code + "_"):
                leaves.append(f"{fam.name}/{d.name}")
    return sorted(leaves)


def discover_originals_leaves(orig_root, code):
    """从 <batch>/originals/<code>/<family>/<slug>/ 反推叶（源叶可能已移走）。"""
    leaves = []
    orig_root = Path(orig_root)
    if not orig_root.is_dir():
        return leaves
    for fam in sorted(p for p in orig_root.iterdir() if p.is_dir()):
        for d in sorted(p for p in fam.iterdir() if p.is_dir()):
            if d.name == code or d.name.startswith(code + "_"):
                leaves.append(f"{fam.name}/{d.name}")
    return sorted(leaves)


def resolve_leaf(db_root, leaf):
    """接受 `FAM/slug` 或裸 `slug`（裸 slug 时在 db-root 一层家族下定位）。"""
    leaf = leaf.replace("\\", "/").strip("/")
    if "/" in leaf:
        return leaf
    hits = []
    db_root = Path(db_root)
    if db_root.is_dir():
        for fam in sorted(p for p in db_root.iterdir() if p.is_dir()):
            if (fam / leaf).is_dir():
                hits.append(f"{fam.name}/{leaf}")
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit(f"--leaf {leaf} 命中多个家族，请写全 FAM/slug：{', '.join(hits)}")
    return leaf


def check_strict_leaf(db_root, leaves):
    """叶内除 目录说明.md 外有残留 → 收集；返回 (errors, warnings)。"""
    errors, warnings = [], []
    for leaf in leaves:
        base = Path(db_root) / leaf
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.name == core.LEAF_README:
                continue
            rel = p.relative_to(db_root).as_posix()
            (warnings if leaf in core.LEAF_EXCEPTIONS else errors).append(rel)
    return errors, warnings


def ingest_share_archives(con, ing, args, fam_dir, batch_dir, movelog):
    """解 _share 归档 → 档内表格入库；--move 时解包树移 {DEL} 并写 archive-member 台账。"""
    stat = dict(archives=0, members=0, tables=0, skipped=0, unpack_moved=0)
    for arc in core.share_archives(fam_dir):
        if core.is_raw_pack(arc.name):
            stat["skipped"] += 1
            print(f"[skip] raw 打包产物（不是源归档）: {arc.name}")
            continue
        leaf, slug = core.share_arc_leaf(fam_dir, arc)
        share_rel = arc.relative_to(fam_dir)          # _share/thesis/xxx.7z
        stage = batch_dir / "staging" / "share-unpack" / args.family / share_rel
        if stage.exists():
            # staging 是临时区：上一次的非 --move 残留改名留档，不删
            prev = stage.with_name(f"{stage.name}.prev-{datetime.now():%Y%m%d%H%M%S}")
            core.make_dirs(prev.parent)
            shutil.move(str(stage), str(prev))
            print(f"! staging 残留改名: {prev}")
        members = core.extract_archive(arc, stage)
        stat["archives"] += 1
        stat["members"] += len(members)
        tabs = [m for m in members if core.is_table(m)]
        stat["skipped"] += len(members) - len(tabs)
        for m in tabs:
            rel = core.rel_for_archive_member(fam_dir, arc, m)
            ing.ingest(stage / m, rel, args.family, slug, leaf, incremental=True)
            stat["tables"] += 1
        if args.move and members:
            dst_tree = batch_dir / "unpack" / args.family / share_rel
            if dst_tree.exists():
                raise SystemExit(f"解包树目标已存在，拒绝覆盖: {dst_tree}")
            recs = []
            for m in members:
                src = stage / m
                recs.append((core.rel_for_archive_member(fam_dir, arc, m),
                             str(dst_tree / Path(m)), src.stat().st_size, sha256(src), m))
            core.make_dirs(dst_tree.parent)
            shutil.move(str(stage), str(dst_tree))
            if not dst_tree.is_dir():
                raise RuntimeError(f"解包树移动失败: {dst_tree}")
            for rel, d, size, h, m in recs:
                movelog.append(("archive-member", rel, d, size, h, arc.name, m))
            stat["unpack_moved"] += 1
            # 清掉 staging 空父目录（只删空目录，不删文件）
            for d in sorted((batch_dir / "staging" / "share-unpack" / args.family).rglob("*"),
                            reverse=True):
                if d.is_dir() and not any(d.iterdir()):
                    try:
                        d.rmdir()
                    except OSError:
                        pass
    return stat


def main(args):
    db_root = Path(args.db_root)
    fam_dir = db_root / args.family
    out = fam_dir / "_db"
    db_path = out / f"{args.family}db"
    batch_dir = core.find_batch(args.batch)
    if args.leaf:
        leaves = [resolve_leaf(db_root, x) for x in args.leaf]
    elif args.from_originals:
        leaves = discover_originals_leaves(batch_dir / "originals" / args.code, args.code)
    else:
        leaves = discover_leaves(db_root, args.code, args.family)
    if not leaves and not (args.share_archives and fam_dir.is_dir()):
        raise SystemExit(f"未发现叶（family={args.family} code={args.code}），未改动现有库")

    def base_of(leaf):
        return (batch_dir / "originals" / args.code / leaf) if args.from_originals else (db_root / leaf)

    if leaves:
        missing = [x for x in leaves if not base_of(x).is_dir()]
        if missing:
            if args.leaf:
                for x in missing:
                    print(f"! 跳过（不存在）: {base_of(x)}")
            leaves = [x for x in leaves if x not in missing]
        if not leaves and not (args.share_archives and fam_dir.is_dir()):
            raise SystemExit("所有叶路径均不存在，未改动现有库")
    if args.from_originals and args.move:
        raise SystemExit("--from-originals 与 --move 不能同时使用（源已在 originals/）")

    con = core.create_db(db_path)
    ing = core.Ingestor(con, args.code)
    movelog = []  # (kind, rel_path, dst, size, sha256, archive, member)
    for leaf in leaves:
        fam, slug = leaf.split("/", 1)
        base = base_of(leaf)
        files = core.table_files(base)
        for p in files:
            rel_inside = p.relative_to(base).as_posix()
            rel = (leaf + "/" + rel_inside) if args.from_originals \
                else p.relative_to(db_root).as_posix()
            ing.ingest(p, rel, fam, slug, leaf)
            if args.move:
                dst = batch_dir / "originals" / args.code / leaf / rel_inside
                h, size = core.move_verified(p, dst)
                movelog.append(("table", rel, str(dst), size, h, "", ""))

    arch_stat = None
    if args.share_archives:
        arch_stat = ingest_share_archives(con, ing, args, fam_dir, batch_dir, movelog)

    # meta / 计数
    counts = {
        "n_doc": con.execute("SELECT count(*) FROM doc").fetchone()[0],
        "n_sheet": con.execute("SELECT count(*) FROM sheet").fetchone()[0],
        "n_fact": con.execute("SELECT count(*) FROM fact").fetchone()[0],
        "n_entity": con.execute("SELECT count(*) FROM entity").fetchone()[0],
    }
    core.set_meta(con, short_code=args.code, built_at=core.now_iso(), rule=core.RULE_TEXT,
                  **counts)
    con.commit()
    con.close()

    if args.strict_leaf and not args.from_originals:
        errors, warnings = check_strict_leaf(db_root, leaves)
        for w in warnings:
            print(f"! --strict-leaf 例外（未入库 PHI/模板，见 LEAF_EXCEPTIONS）: {w}")
        if errors:
            for x in errors[:50]:
                print(f"! 叶内残留: {x}")
            sys.stderr.write(f"--strict-leaf: 叶内除 {core.LEAF_README} 外残留 {len(errors)} 个文件"
                             f"（已入库，需清理后重跑）\n")
            raise SystemExit(2)

    print("统计:", ing.stat)
    if arch_stat is not None:
        print("归档:", arch_stat)
    print("db:", db_path, f"{db_path.stat().st_size/1048576:.1f} MiB")
    return movelog, batch_dir


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="按家族 + 短码建 SQLite 库（<db-root>/<family>/_db/<family>db）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("台账：<batch>/<CODE>-MOVED-<FAM>-<YYYYMMDD>.tsv\n"
                "列：kind rel_path dst size sha256 archive member\n"
                "kind=table 叶内表原件；kind=archive-member 档内成员（解包树移 {DEL}/<batch>/unpack/）"))
    ap.add_argument("--family", required=True, help="库所属家族，如 MHE_AN")
    ap.add_argument("--code", required=True, help="短码，如 ly")
    ap.add_argument("--leaf", action="append", metavar="FAM/SLUG",
                    help="叶路径，可多次；缺省=该短码全部叶")
    ap.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--move", action="store_true",
                    help="建库后移原件到批次 originals/<code>/<family>/<leaf>/ 并写台账")
    ap.add_argument("--from-originals", action="store_true",
                    help="从批次 originals/<code>/ 重建（不读 _DataBase 叶）")
    ap.add_argument("--share-archives", action="store_true",
                    help="解 <family>/_share/ 下压缩档，档内表格入库（原档留 _share；"
                         "配 --move 时解包树移批次 unpack/）")
    ap.add_argument("--strict-leaf", action="store_true",
                    help=f"入库后校验叶内除 {core.LEAF_README} 外无残留（有则退出码 2）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    mv, batch_dir = main(args)
    if args.move and mv:
        man = batch_dir / f"{args.code.upper()}-MOVED-{args.family}-{datetime.now():%Y%m%d}.tsv"
        core.write_ledger(man, ("kind", "rel_path", "dst", "size", "sha256",
                                "archive", "member"), mv)
        print("移出台账:", man, len(mv), "行")
