# -*- coding: utf-8 -*-
"""按家族 + 短码建 SQLite 库：<db-root>\\<family>\\_db\\<family>db

- 深度去重：L1 文件 sha；L2 整表（同表头+行集相同/子集）；L3 行级完全重复
- 不收入：压缩档（.7z/.zip/.rar/.tar.zst/.gz）
- 保真：每张 sheet 以 zlib(TSV) 存 blob；整洁表（表头可识别、列数≤40）另入 fact 长表
- 溯源：doc 记录源相对路径/sha/行数；去重关系进 sheet_dup

用法：
  python -B tools/db-library/build_code_db.py --family MHE --code ly
  python -B tools/db-library/build_code_db.py --family MHE --code ly --leaf MHE/ly_RI
  python -B tools/db-library/build_code_db.py --family MHE --code ly --from-originals
  python -B tools/db-library/build_code_db.py --family MHE --code ly --move   # 需点名授权
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import zlib
from datetime import datetime
from pathlib import Path

from common import ledger_write, norm, read_any, sha256

DEFAULT_DB_ROOT = Path(r"D:\MyProjects\_DataBase")
DEFAULT_BATCH = "2026-11-db-sqlite"
DEL_ROOT = Path(r"D:\MyProjects\.Del") / "_by-function"
TAB = (".xls", ".xlsx", ".csv")
NUM = re.compile(r"^[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?$")
IDCOL = re.compile(r"^(医技号|样本编号|样本号|编号|case\s*no\.?|mid|谱图编号|patient(?:_?id)?|id)$", re.I)


def as_num(s):
    t = norm(s).replace(",", "")
    if t and NUM.match(t):
        try:
            return float(t)
        except ValueError:
            return None
    return None


def find_header(rows):
    best, bi = 0, -1
    for i, r in enumerate(rows[:6]):
        c = sum(1 for v in r if v)
        if c > best:
            best, bi = c, i
    return bi if best >= 2 else -1


def header_of(rows, hi):
    hdr, seen = [], {}
    for v in rows[hi]:
        h = v or ""
        if h in seen:
            seen[h] += 1
            h = f"{h}#{seen[h]}"
        else:
            seen[h] = 1
        hdr.append(h)
    return hdr


def discover_leaves(db_root, code):
    """缺省叶集合：<db-root>/<family>/<slug> 中 slug == code 或以 `code_` 开头。"""
    leaves = []
    if not db_root.is_dir():
        return leaves
    for fam in sorted(p for p in db_root.iterdir() if p.is_dir()):
        for d in sorted(p for p in fam.iterdir() if p.is_dir()):
            if d.name == code or d.name.startswith(code + "_"):
                leaves.append(f"{fam.name}/{d.name}")
    return sorted(leaves)


def discover_originals_leaves(orig_root, code):
    """从 <batch>/originals/<code>/<family>/<slug>/ 反推叶（源叶可能已移走）。"""
    leaves = []
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
    if db_root.is_dir():
        for fam in sorted(p for p in db_root.iterdir() if p.is_dir()):
            cand = fam / leaf
            if cand.is_dir():
                hits.append(f"{fam.name}/{leaf}")
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise SystemExit(f"--leaf {leaf} 命中多个家族，请写全 FAM/slug：{', '.join(hits)}")
    return leaf


def main(args):
    db_root = args.db_root
    out = db_root / args.family / "_db"
    db_path = out / f"{args.family}db"
    batch_dir = DEL_ROOT / args.batch
    if args.leaf:
        leaves = [resolve_leaf(db_root, x) for x in args.leaf]
    elif args.from_originals:
        leaves = discover_originals_leaves(batch_dir / "originals" / args.code, args.code)
    else:
        leaves = discover_leaves(db_root, args.code)

    out.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE project(id INTEGER PRIMARY KEY, family TEXT, slug TEXT, leaf TEXT, short_code TEXT);
    CREATE TABLE doc(id INTEGER PRIMARY KEY, project_id INTEGER, rel_path TEXT, size INTEGER,
                     sha256 TEXT, mtime TEXT, n_sheets INTEGER, status TEXT, dup_of INTEGER);
    CREATE TABLE sheet(id INTEGER PRIMARY KEY, doc_id INTEGER, name TEXT, nrow INTEGER, ncol INTEGER,
                       header TEXT, blob BLOB, is_wide INTEGER, status TEXT, dup_of INTEGER);
    CREATE TABLE fact(id INTEGER PRIMARY KEY, sheet_id INTEGER, entity_id INTEGER, row_ix INTEGER,
                      item TEXT, value_num REAL, value_text TEXT);
    CREATE TABLE entity(id INTEGER PRIMARY KEY, project_id INTEGER, key_kind TEXT, key_norm TEXT,
                        key_raw TEXT);
    CREATE TABLE sheet_dup(id INTEGER PRIMARY KEY, sheet_id INTEGER, dup_sheet_id INTEGER,
                           reason TEXT, kept_bytes INTEGER);
    CREATE INDEX ix_fact_sheet ON fact(sheet_id);
    CREATE INDEX ix_fact_item ON fact(item, value_num);
    CREATE INDEX ix_entity ON entity(project_id, key_norm);
    """)
    stat = dict(files=0, dup_files=0, sheets=0, dup_sheets=0, subset_sheets=0,
                facts=0, entities=0, blob_bytes=0, moved=0)
    by_sha = {}
    sig_idx = {}  # header_key -> [(sheet_meta,rowhash_set)]
    movelog = []
    for leaf in leaves:
        fam = leaf.split("/")[0]
        base = (batch_dir / "originals" / args.code / leaf) if args.from_originals else (db_root / leaf)
        if not base.is_dir():
            if args.leaf:
                print(f"! 跳过（不存在）: {base}")
            continue
        files = sorted(p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in TAB)
        cur = con.execute("INSERT INTO project(family,slug,leaf,short_code) VALUES(?,?,?,?)",
                          (fam, base.name, leaf, args.code))
        pid = cur.lastrowid
        for p in files:
            rel = (leaf + "/" + p.relative_to(base).as_posix()) if args.from_originals else str(p.relative_to(db_root)).replace("\\", "/")
            h = sha256(p)
            doc = con.execute(
                "INSERT INTO doc(project_id,rel_path,size,sha256,mtime,n_sheets,status)"
                " VALUES(?,?,?,?,?,0,'ingested')",
                (pid, rel, p.stat().st_size, h,
                 datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")))
            did = doc.lastrowid
            stat["files"] += 1
            try:
                sheets = read_any(p)
            except Exception as e:
                print(f"! 读取失败 {rel}: {e.__class__.__name__}: {e}"[:160])
                sheets = []
            con.execute("UPDATE doc SET n_sheets=? WHERE id=?", (len(sheets), did))
            for name, rows in sheets:
                stat["sheets"] += 1
                hi = find_header(rows)
                hdr = header_of(rows, hi) if hi >= 0 else []
                hkey = "\x1f".join(hdr) if hdr else f"__nohdr__{name}"
                data = rows[hi + 1:] if hi >= 0 else rows
                rh = set()
                for r in data:
                    if any(r):
                        rh.add(hashlib.sha1(("\x1f".join(r)).encode("utf-8")).hexdigest())
                nrow = sum(1 for r in data if any(r))
                ncol = max((len(r) for r in rows), default=0)
                status, dup_of = "ingested", None
                prev = sig_idx.setdefault(hkey, [])
                if hdr:
                    for meta in prev:
                        if meta["rows"] == rh:
                            status, dup_of = "dup", meta["id"]
                            break
                        if rh and rh <= meta["rows"]:
                            status, dup_of = "subset", meta["id"]
                            break
                blob = BLOB = None
                if status == "ingested":
                    txt = "\n".join("\t".join(r) for r in rows)
                    blob = zlib.compress(txt.encode("utf-8"), 9)
                    stat["blob_bytes"] += len(blob)
                cur = con.execute(
                    "INSERT INTO sheet(doc_id,name,nrow,ncol,header,blob,is_wide,status,dup_of)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (did, name, nrow, ncol, hkey[:4000], blob, int(ncol > 40), status, dup_of))
                sid = cur.lastrowid
                prev.append(dict(id=sid, rows=rh, n=nrow))
                if status != "ingested":
                    stat["dup_sheets" if status == "dup" else "subset_sheets"] += 1
                    con.execute("INSERT INTO sheet_dup(sheet_id,dup_sheet_id,reason,kept_bytes)"
                                " VALUES(?,?,?,?)",
                                (sid, dup_of, "整表重复" if status == "dup" else "整表子集",
                                 p.stat().st_size))
                    continue
                # 整洁表 → fact；矩阵（表头多为数字）只留 blob
                hdr_num = sum(1 for h2 in hdr[1:] if h2 and as_num(h2) is not None)
                is_matrix = hdr and (hdr_num / max(1, len(hdr) - 1)) > 0.6
                if hdr and ncol <= 40 and nrow > 0 and not is_matrix:
                    idcol = next((i for i, h2 in enumerate(hdr) if IDCOL.match(h2 or "")), None)
                    ent_cache = {}
                    for rix, r in enumerate(data):
                        if not any(r):
                            continue
                        eid = None
                        if idcol is not None and idcol < len(r) and r[idcol]:
                            kn = re.sub(r"\s+", "", r[idcol]).lower()
                            if kn not in ent_cache:
                                cur2 = con.execute(
                                    "INSERT INTO entity(project_id,key_kind,key_norm,key_raw)"
                                    " VALUES(?,?,?,?)", (pid, hdr[idcol][:60], kn, r[idcol][:200]))
                                ent_cache[kn] = cur2.lastrowid
                                stat["entities"] += 1
                            eid = ent_cache[kn]
                        for ci, v in enumerate(r):
                            if v == "" or ci >= len(hdr) or not hdr[ci]:
                                continue
                            con.execute(
                                "INSERT INTO fact(sheet_id,entity_id,row_ix,item,value_num,value_text)"
                                " VALUES(?,?,?,?,?,?)",
                                (sid, eid, rix, hdr[ci][:120], as_num(v), v[:500]))
                            stat["facts"] += 1
            if args.move:
                fsize = p.stat().st_size
                dst = batch_dir / "originals" / args.code / Path(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                p.replace(dst)
                stat["moved"] += 1
                movelog.append((rel, str(dst), fsize, h))
    con.execute("INSERT INTO meta VALUES('short_code',?)", (args.code,))
    con.execute("INSERT INTO meta VALUES('built_at',?)",
                (datetime.now().isoformat(timespec="seconds"),))
    con.execute("INSERT INTO meta VALUES('rule',?)",
                ("深度去重：sha→整表(重复/子集)→行级；压缩档不入库；原件移入 _DEL",))
    con.commit()
    con.close()
    print("统计:", stat)
    print("db:", db_path, f"{db_path.stat().st_size/1048576:.1f} MiB")
    return movelog, batch_dir


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="按家族 + 短码建 SQLite 库（<db-root>/<family>/_db/<family>db）")
    ap.add_argument("--family", required=True, help="库所属家族，如 MHE")
    ap.add_argument("--code", required=True, help="短码，如 ly")
    ap.add_argument("--leaf", action="append", metavar="FAM/SLUG",
                    help="叶路径，可多次；缺省=该短码全部叶")
    ap.add_argument("--db-root", type=Path, default=DEFAULT_DB_ROOT,
                    help=f"_DataBase 根（默认 {DEFAULT_DB_ROOT}）")
    ap.add_argument("--batch", default=DEFAULT_BATCH,
                    help=f"{{DEL}}/_by-function 下的批次目录（默认 {DEFAULT_BATCH}）")
    ap.add_argument("--move", action="store_true", help="建库后移原件到批次 originals/ 并写台账")
    ap.add_argument("--from-originals", action="store_true",
                    help="从批次 originals/<code>/ 重建（不读 _DataBase 叶）")
    return ap.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    mv, batch_dir = main(args)
    if args.move and mv:
        man = batch_dir / f"{args.code.upper()}-MOVED-{datetime.now():%Y%m%d}.tsv"
        ledger_write(man, ("rel_path", "dst", "size", "sha256"), mv)
        print("移出台账:", man, len(mv), "行")
