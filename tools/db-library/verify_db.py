# -*- coding: utf-8 -*-
"""家族库只读校验：schema / 计数 / 台账覆盖 / entity 分布。

用法：
  python -B tools/db-library/verify_db.py --db "D:/MyProjects/_DataBase/MHE/_db/MHEdb"
  python -B tools/db-library/verify_db.py --db <path> --ledger <LY-MOVED-*.tsv>

失败（任一 [FAIL]）退出码 1；仅有 [warn] 时退出 0。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

DEFAULT_DEL_ROOT = Path(r"D:\MyProjects\.Del") / "_by-function"
CORE_TABLES = {"meta", "project", "doc", "sheet", "fact", "entity", "sheet_dup"}
CORE_INDEXES = {"ix_fact_sheet", "ix_fact_item", "ix_entity"}
REQUIRED_META = ("short_code", "built_at", "rule")


class Report:
    def __init__(self):
        self.fails = 0
        self.warns = 0

    def ok(self, msg):
        print(f"[ok] {msg}")

    def fail(self, msg):
        print(f"[FAIL] {msg}")
        self.fails += 1

    def warn(self, msg):
        print(f"[warn] {msg}")
        self.warns += 1

    def info(self, msg):
        print(f"[info] {msg}")


def norm_rel(s):
    return str(s).replace("\\", "/").lstrip("./").strip()


def discover_ledgers(del_root, code):
    if not del_root.is_dir():
        return []
    pat = f"{code.upper()}-MOVED-*.tsv" if code else "*-MOVED-*.tsv"
    found = []
    for batch in sorted(p for p in del_root.iterdir() if p.is_dir()):
        for p in sorted(batch.glob(pat)):
            if p.is_file():
                found.append(p)
    return found


def read_ledger(path):
    """返回 [(rel_path, sha256|sha256_12|None, dst|None)]。"""
    lines = [ln.rstrip("\n").split("\t") for ln in path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip()]
    if not lines:
        return []
    header = lines[0]
    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None
    rel_i = col("rel_path", "rel")
    sha_i = col("sha256", "sha256_12")
    dst_i = col("dst")
    if rel_i is None:
        raise ValueError(f"{path.name}: 表头无 rel_path/rel 列")
    rows = []
    for r in lines[1:]:
        if len(r) <= rel_i:
            continue
        rel = norm_rel(r[rel_i])
        sha = r[sha_i] if sha_i is not None and len(r) > sha_i else None
        dst = r[dst_i] if dst_i is not None and len(r) > dst_i else None
        rows.append((rel, sha, dst))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="家族库只读校验")
    ap.add_argument("--db", required=True, type=Path, help="库文件，如 .../<family>/_db/<family>db")
    ap.add_argument("--ledger", action="append", type=Path, metavar="TSV",
                    help="台账；缺省按 meta.short_code 在 --del-root 下自动找")
    ap.add_argument("--del-root", type=Path, default=DEFAULT_DEL_ROOT,
                    help=f"{{DEL}}/_by-function 根（默认 {DEFAULT_DEL_ROOT}）")
    args = ap.parse_args(argv)

    rep = Report()
    db = args.db.expanduser().resolve()
    print(f"== db ==\n{db}")
    if not db.is_file():
        rep.fail(f"库文件不存在: {db}")
        return _finish(rep)
    with db.open("rb") as f:
        if f.read(16) != b"SQLite format 3\x00":
            rep.fail("不是 SQLite 文件（文件头不符）")
            return _finish(rep)

    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as e:
        rep.fail(f"打不开库（只读）: {e}")
        return _finish(rep)

    try:
        # ---- integrity
        row = con.execute("PRAGMA integrity_check").fetchone()
        if row and row[0] == "ok":
            rep.ok("integrity_check = ok")
        else:
            rep.fail(f"integrity_check = {row[0] if row else '无返回'}")

        # ---- schema
        tables = {r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
        missing = CORE_TABLES - tables
        extra = tables - CORE_TABLES
        if missing:
            rep.fail(f"缺表: {', '.join(sorted(missing))}")
            return _finish(rep)
        else:
            rep.ok(f"核心表齐: {', '.join(sorted(CORE_TABLES))}")
        if extra:
            rep.warn(f"额外表: {', '.join(sorted(extra))}")
        indexes = {r[0] for r in con.execute(
            "select name from sqlite_master where type='index' and name not like 'sqlite_%'")}
        miss_idx = CORE_INDEXES - indexes
        if miss_idx:
            rep.fail(f"缺索引: {', '.join(sorted(miss_idx))}")
        else:
            rep.ok(f"核心索引齐: {', '.join(sorted(CORE_INDEXES))}")

        # ---- counts
        doc = con.execute("select count(*) from doc").fetchone()[0]
        sheet = con.execute("select count(*) from sheet").fetchone()[0]
        fact = con.execute("select count(*) from fact").fetchone()[0]
        entity = con.execute("select count(*) from entity").fetchone()[0]
        st = dict(con.execute("select status, count(*) from sheet group by status").fetchall())
        sdup = con.execute("select count(*) from sheet_dup").fetchone()[0]
        ndist = con.execute("select count(distinct rel_path) from doc").fetchone()[0]
        print(f"doc={doc} sheet={sheet} (ingested={st.get('ingested', 0)}"
              f" dup={st.get('dup', 0)} subset={st.get('subset', 0)})"
              f" sheet_dup={sdup} fact={fact} entity={entity}")
        if not (doc and sheet and fact):
            rep.fail(f"计数异常（doc/sheet/fact/entity 不可为 0）: {doc}/{sheet}/{fact}/{entity}")
        else:
            rep.ok("doc/sheet/fact/entity 均非空")
        if sheet != st.get("ingested", 0) + st.get("dup", 0) + st.get("subset", 0):
            rep.fail("sheet 状态合计与总数不符")
        else:
            rep.ok("sheet 状态合计 = sheet 总数")
        if sdup != st.get("dup", 0) + st.get("subset", 0):
            rep.fail(f"sheet_dup 边数 {sdup} != dup+subset {st.get('dup', 0) + st.get('subset', 0)}")
        else:
            rep.ok("sheet_dup 边数 = dup + subset")
        if ndist != doc:
            rep.fail(f"doc.rel_path 有 {doc - ndist} 条重复")
        else:
            rep.ok("doc.rel_path 无重复")

        # ---- meta
        meta = dict(con.execute("select k, v from meta").fetchall())
        print("meta:", ", ".join(f"{k}={v}" for k, v in sorted(meta.items())))
        for k in REQUIRED_META:
            if k in meta and meta[k] != "":
                rep.ok(f"meta.{k} 存在")
            elif k == "short_code":
                rep.fail(f"meta 缺 {k}")
            else:
                rep.warn(f"meta 缺 {k}")
        for k in ("n_doc", "n_sheet", "n_fact", "n_entity"):
            if k in meta:
                actual = {"n_doc": doc, "n_sheet": sheet, "n_fact": fact, "n_entity": entity}[k]
                if str(actual) != str(meta[k]):
                    rep.fail(f"meta.{k}={meta[k]} 与实际 {actual} 不符")
                else:
                    rep.ok(f"meta.{k} = 实际 {actual}")

        # ---- 引用完整性
        orphans = [
            ("sheet.doc_id", con.execute(
                "select count(*) from sheet s where not exists"
                " (select 1 from doc d where d.id=s.doc_id)").fetchone()[0]),
            ("fact.sheet_id", con.execute(
                "select count(*) from fact f where not exists"
                " (select 1 from sheet s where s.id=f.sheet_id)").fetchone()[0]),
            ("fact.entity_id", con.execute(
                "select count(*) from fact f where f.entity_id is not null and not exists"
                " (select 1 from entity e where e.id=f.entity_id)").fetchone()[0]),
            ("entity.project_id", con.execute(
                "select count(*) from entity e where not exists"
                " (select 1 from project p where p.id=e.project_id)").fetchone()[0]),
            ("sheet_dup.dup_sheet_id", con.execute(
                "select count(*) from sheet_dup x where not exists"
                " (select 1 from sheet s where s.id=x.dup_sheet_id)").fetchone()[0]),
        ]
        bad = [f"{name}={n}" for name, n in orphans if n]
        if bad:
            rep.fail("孤儿引用: " + ", ".join(bad))
        else:
            rep.ok("引用完整性（sheet/doc/fact/entity/sheet_dup）")

        nbad = con.execute(
            "select count(*) from doc d where d.n_sheets != "
            "(select count(*) from sheet s where s.doc_id=d.id)").fetchone()[0]
        if nbad:
            rep.fail(f"{nbad} 个 doc.n_sheets 与实际 sheet 数不符")
        else:
            rep.ok("doc.n_sheets 与实际 sheet 数一致")

        # ---- entity 分布
        dist = con.execute(
            "select key_kind, count(*) from entity group by key_kind order by 2 desc, 1").fetchall()
        if dist:
            print("entity keys:", ", ".join(f"{k}={n:,}" for k, n in dist))
        empty = con.execute(
            "select count(*) from entity where key_kind is null or key_kind=''"
            " or key_norm is null or key_norm=''").fetchone()[0]
        if empty:
            rep.fail(f"entity 空键 {empty} 条")
        else:
            rep.ok("entity 键非空")
        dupe = con.execute(
            "select count(*) from (select project_id, key_norm from entity"
            " group by 1, 2 having count(*) > 1)").fetchone()[0]
        if dupe:
            rep.warn(f"entity 同项目同键重复 {dupe} 组（跨 sheet 可接受）")
        else:
            rep.ok("entity 同项目同键唯一")

        # ---- 台账覆盖
        code = meta.get("short_code", "")
        ledgers = args.ledger or discover_ledgers(args.del_root, code)
        if not ledgers:
            rep.fail(f"未找到台账（{args.del_root}/{code.upper()}-MOVED-*.tsv）；可用 --ledger 指定")
        else:
            ledger_rel = {}
            missing_dst = 0
            for lp in ledgers:
                try:
                    rows = read_ledger(lp)
                except (OSError, ValueError) as e:
                    rep.fail(f"读台账失败 {lp}: {e}")
                    continue
                print(f"ledger: {lp}（{len(rows)} 行）")
                for rel, sha, dst in rows:
                    ledger_rel[rel] = sha
                    if dst and not Path(dst).is_file():
                        missing_dst += 1
            doc_rows = con.execute("select rel_path, sha256 from doc").fetchall()
            missing = [(rel, sha) for rel, sha in doc_rows if norm_rel(rel) not in ledger_rel]
            if missing:
                preview = ", ".join(rel for rel, _ in missing[:3])
                rep.fail(f"台账未覆盖 {len(missing)}/{len(doc_rows)} 个 doc.rel_path（如 {preview}）")
            else:
                rep.ok(f"台账覆盖 doc.rel_path {len(doc_rows)}/{len(doc_rows)}")
            extra = sorted(set(ledger_rel) - {norm_rel(rel) for rel, _ in doc_rows})
            if extra:
                rep.warn(f"台账多出 {len(extra)} 条 doc 中不存在的 rel_path（如 {extra[0]}）")
            sha_bad = 0
            for rel, sha in doc_rows:
                led = ledger_rel.get(norm_rel(rel))
                if not led:
                    continue
                if len(led) == 12:
                    sha_bad += (sha or "")[:12] != led
                else:
                    sha_bad += (sha or "") != led
            if sha_bad:
                rep.fail(f"台账 sha256 与 doc 不符 {sha_bad} 条")
            else:
                rep.ok("台账 sha256 与 doc 一致")
            if missing_dst:
                rep.warn(f"台账 dst 指向的原件缺失 {missing_dst} 条")
            else:
                rep.ok("台账 dst 原件均在")
    finally:
        con.close()

    return _finish(rep)


def _finish(rep):
    print(f"== 结果 ==  fail={rep.fails} warn={rep.warns}")
    if rep.fails:
        print("校验失败")
        return 1
    print("校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
