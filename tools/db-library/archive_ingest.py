# -*- coding: utf-8 -*-
"""把 `_share` 下压缩档里的表格解出入库（原档留 `_share`，解包树校验后移 {DEL}）。

口径：压缩档留在 `<family>\\_share\\<leaf>\\`；档内表格解包后按同一建库函数入库，
doc.rel_path = `<FAM>/_share/<leaf>/<档名>/<成员>`；解包树在 {DEL} 留档备查，
台账写 archive→member 映射（kind=archive-member）。

用法：
  python -B tools/db-library/archive_ingest.py --family MHE_AN                    # dry-run
  python -B tools/db-library/archive_ingest.py --family MHE_AN --apply            # 入库
  python -B tools/db-library/archive_ingest.py --db "...\\MHE_AN\\_db\\MHE_ANdb" --apply
  python -B tools/db-library/archive_ingest.py --family ACN --archive 217 --apply  # 只处理含 217 的档
退出码：0 正常；1 失败。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_BATCH = "2026-11-db-share"


def resolve_db(args):
    if args.db:
        db = Path(args.db)
        if not db.is_file():
            raise SystemExit(f"FAIL 库不存在: {db}")
        return db
    db = core.family_db(args.db_root, args.family)
    if not Path(db).is_file():
        raise SystemExit(f"FAIL 库不存在: {db}（先跑 build_code_db.py）")
    return Path(db)


def main(args):
    db = resolve_db(args)
    fam_dir = core.family_dir(args.db_root, args.family)
    if not fam_dir.is_dir():
        print(f"FAIL 家族目录不存在: {fam_dir}", file=sys.stderr)
        return 1
    code = args.code or core.read_code(args.db_root, args.family) or args.family
    batch = core.DEL_ROOT / args.batch
    arcs = core.share_archives(fam_dir)
    src_arcs = [a for a in arcs if not core.is_raw_pack(a.name)]
    for a in arcs:
        if core.is_raw_pack(a.name):
            print(f"[skip] raw 打包产物（不是源归档）: {a.name}")
    arcs = src_arcs
    if args.archive:
        arcs = [a for a in arcs if args.archive in a.name]
    if not arcs:
        print("无需处理：_share 下没有匹配的压缩档")
        return 0

    plans = []
    for arc in arcs:
        leaf, slug = core.share_arc_leaf(fam_dir, arc)
        try:
            members = core.archive_members(arc)
        except Exception as e:  # noqa: BLE001 - 单档失败不阻断其余档的盘点
            print(f"! 列档失败 {arc.name}: {e}", file=sys.stderr)
            return 1
        tables = [m for m in members if core.is_table(m)]
        plans.append((arc, leaf, slug, members, tables))
        print(f"  [{leaf}] {arc.relative_to(fam_dir).as_posix()}: "
              f"成员 {len(members)}，其中表格 {len(tables)}")
        for t in tables[:8]:
            print(f"      + {t}")
        if len(tables) > 8:
            print(f"      … 另 {len(tables) - 8} 个表格")
    if not args.apply:
        print("dry-run：未落盘（加 --apply 执行）")
        return 0

    con = core.open_db(db)
    ing = core.Ingestor(con, code)
    ledger = []
    ns = 0
    try:
        for arc, leaf, slug, members, tables in plans:
            rel_dir = core.share_rel_dir(fam_dir, arc)
            stage = batch / "staging" / "ingest" / args.family / rel_dir / arc.name
            if stage.exists():
                backup = stage.with_name(f"{arc.name}.prev-{datetime.now():%Y%m%d%H%M%S}")
                shutil.move(str(stage), str(backup))
            core.make_dirs(stage)
            got = core.extract_archive(arc, stage)
            if set(got) != set(members):
                raise RuntimeError(f"解包成员与列档不一致: {arc.name}")
            for m in members:
                p = stage / m
                if not p.is_file():
                    raise RuntimeError(f"解包缺成员: {arc.name}/{m}")
                if core.is_table(m):
                    rel = core.rel_for_archive_member(fam_dir, arc, m)
                    _, added = ing.ingest(p, rel, args.family, slug, leaf, incremental=True)
                    ns += added
                h = sha256(p)
                ledger.append(("archive-member", rel_dir + "/" + arc.name + "/" + m,
                               arc.name, p.stat().st_size, h, m))
            core.refresh_meta_counts(con)
            con.commit()
            if args.keep_unpack:
                print(f"  ! 解包树保留: {stage}")
                continue
            out = batch / "unpack" / args.family / rel_dir / arc.name
            if out.exists():
                raise RuntimeError(f"解包树目标已存在: {out}")
            core.make_dirs(out.parent)
            shutil.move(str(stage), str(out))
            print(f"  解包树 -> {out}")
    finally:
        con.close()

    man = Path(args.ledger) if args.ledger else (
        batch / f"ARCHIVE-INGEST-{args.family}-{datetime.now():%Y%m%d}.tsv")
    core.write_ledger(man, ("kind", "rel_path", "archive", "size", "sha256", "member"), ledger)
    core.append_readme_note(fam_dir / "_share",
                            f"- {datetime.now():%Y-%m-%d} archive_ingest：入库 {len(plans)} 个档、"
                            f"新增 {ns} 张表，台账 {man.name}")
    print(f"完成：{len(plans)} 个档，新增 {ns} 张表，台账 {len(ledger)} 行 → {man}")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="_share 压缩档内的表格解出入库（原档留 _share，解包树移 {DEL}）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="默认 dry-run；--apply 解包入库并写 archive→member 台账。")
    ap.add_argument("--family", required=True, help="家族名，如 MHE_AN")
    ap.add_argument("--db", type=Path, default=None, help="库路径（缺省 <root>/<family>/_db/<family>db）")
    ap.add_argument("--archive", default=None, help="只处理档名含该子串的归档")
    ap.add_argument("--code", default=None, help="短码（缺省读库 meta.short_code）")
    ap.add_argument("--db-root", type=Path, default=core.DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {core.DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--apply", action="store_true", help="落盘（缺省 dry-run）")
    ap.add_argument("--ledger", type=Path, default=None,
                    help="台账路径（缺省 <batch>/ARCHIVE-INGEST-<FAM>-<date>.tsv）")
    ap.add_argument("--keep-unpack", action="store_true",
                    help="保留解包树在 staging（默认移到 {DEL}/<batch>/unpack）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
