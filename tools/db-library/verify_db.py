# -*- coding: utf-8 -*-
"""校验短码库：结构 / 计数 / 引用 / 台账 / 新口径（_share + 叶内只留说明）。

校验项：
  1 完整性 integrity_check
  2 schema（核心表 / 索引）
  3 计数与去重一致性（sheet 状态合计、sheet_dup 边、doc.rel_path 唯一）
  4 meta（short_code/built_at/rule；n_doc/n_sheet/n_fact/n_entity 若存在则比对）
  5 孤儿引用（doc/sheet/fact/entity/sheet_dup）
  6 doc.n_sheets 与实际 sheet 数一致
  7 entity 分布 / 空键 / 同项目同键重复
  8 台账覆盖（按 <code>+<family> 精确归属台账；rel 或 sha256 命中）与 dst 存在
  9 新口径：叶内只留 目录说明.md（PHI 例外白名单 warn）、归档只在 _share、
     _share 层级 ≤2 且无子目录、meta.rule 与现行口径一致、archive→member 映射存在

用法：
  python -B tools/db-library/verify_db.py --db "D:\\MyProjects\\_DataBase\\MHE_AN\\_db\\MHE_ANdb"
退出码：fail>0 → 1。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_DB_ROOT = core.DEFAULT_DB_ROOT
DEFAULT_DEL_ROOT = None
CORE_TABLES = {"meta", "project", "doc", "sheet", "fact", "entity", "sheet_dup"}
CORE_INDEXES = {"ix_fact_sheet", "ix_fact_item", "ix_entity"}
REQUIRED_META = ("short_code", "built_at", "rule")


class Report:
    def __init__(self, quiet=False):
        self.n_fail = 0
        self.n_warn = 0
        self.quiet = quiet

    def _emit(self, level, msg):
        if not self.quiet or level in ("FAIL", "WARN"):
            print(f"[{level}] {msg}")

    def ok(self, msg):
        self._emit("ok", msg)

    def info(self, msg):
        self._emit("info", msg)

    def warn(self, msg):
        self.n_warn += 1
        self._emit("WARN", msg)

    def fail(self, msg):
        self.n_fail += 1
        self._emit("FAIL", msg)


def norm_rel(s):
    return core.norm_path(s)


# ---------------------------------------------------------------------------
# 台账归属：扫 {DEL}/_by-function 下全部 tsv 台账，按 (code,family) 证据筛
# ---------------------------------------------------------------------------

def _rel_candidates(row, code):
    """一行台账里可当 rel 比较的候选（含 src/dst 路径折算）。"""
    out = []
    for key in ("rel", "src", "dst"):
        v = row.get(key) or ""
        if not v:
            continue
        out.append(core.rel_from_path(v, code))
        out.append(core.rel_from_path(v))
    return out


def discover_ledgers(del_root, code, family):
    """返回 (ledgers, rows)：只保留归属 <code>+<family> 的台账及其行。

    Args:
        del_root: 单个根目录，或根目录列表（默认见 ``core.ledger_roots()``）。
        code: 短码。
        family: 家族名。

    Returns:
        (台账路径列表, 台账行列表)。
    """
    roots = [Path(p) for p in (del_root if isinstance(del_root, (list, tuple)) else [del_root])]
    fam = family.upper()
    cod = code.upper()
    if not any(r.is_dir() for r in roots):
        return [], []
    ledgers, all_rows = [], []
    pats = ("*/*MOVED*.tsv", "*/*SWEEP-TABLES*.tsv", "*/*ARCHIVE-TO-SHARE*.tsv",
            "*/*ARCHIVE-INGEST*.tsv", "*/*FLATTEN*.tsv", "*/*RAW-PACK*.tsv")
    paths = sorted({p for r in roots if r.is_dir() for pat in pats for p in r.glob(pat)})
    for p in paths:
        if p.stat().st_size > 20 * 1024 * 1024:
            print(f"! 跳过超大台账（>20MiB）{p}", file=sys.stderr)
            continue
        try:
            rows = core.ledger_read(p)
        except Exception as e:  # noqa: BLE001 - 台账格式异常不应中断校验
            print(f"! 跳过不可读台账 {p}: {e}", file=sys.stderr)
            continue
        if not rows:
            continue
        tokens = {t for t in re.split(r"[-_.\s]+", p.stem.upper()) if t}
        name_rel = False
        if fam in tokens or cod in tokens:
            name_rel = True
        elif any(t and len(t) >= 3 and fam.startswith(t) and t != fam for t in tokens):
            name_rel = True  # 旧短家族名（MHE 覆盖 MHE_AN）
        fam_rows = 0
        for r in rows:
            joined = " ".join(_rel_candidates(r, code)).replace("\\", "/").upper()
            if re.search(r"(^|/)" + re.escape(fam) + r"(/|$)", joined):
                fam_rows += 1
            elif re.search(r"(^|/)" + re.escape(fam) + r"_", joined):
                fam_rows += 1
        if name_rel or fam_rows:
            ledgers.append(p)
            all_rows.extend(rows)
    return ledgers, all_rows


def coverage_match(doc_rel, doc_sha, rows, code):
    """返回命中该 doc 的台账行（rel 精确优先，sha 全长/12 位回退）。"""
    rel = norm_rel(doc_rel)
    sha = (doc_sha or "").lower()
    rel_hits, sha_hits = [], []
    for r in rows:
        for cand in _rel_candidates(r, code):
            c = norm_rel(cand)
            if c == rel or c.endswith("/" + rel) or rel.endswith("/" + c):
                rel_hits.append(r)
                break
            # 档内成员：台账记的是归档本身（kind=archive），doc.rel 以档名为前缀
            if c and rel.startswith(c.rstrip("/") + "/") and core.is_archive(c):
                rel_hits.append(r)
                break
        else:
            rs = (r.get("sha") or "").lower()
            if rs and sha and (rs == sha or sha.startswith(rs) or rs.startswith(sha[:12])):
                sha_hits.append(r)
    return (rel_hits or sha_hits)


# ---------------------------------------------------------------------------
# 新口径结构校验
# ---------------------------------------------------------------------------

def check_leaf_clean(report, fam_dir, family):
    leaves = core.leaf_dirs(fam_dir)
    if not leaves:
        report.info("叶目录：无（家族下只剩 _db/_share）")
        return
    bad = 0
    for leaf in leaves:
        files = [p for p in leaf.rglob("*") if p.is_file() and p.name != core.LEAF_README]
        if not files:
            continue
        key = f"{family}/{leaf.name}"
        shown = ", ".join(p.relative_to(fam_dir).as_posix() for p in files[:5])
        if key in core.LEAF_EXCEPTIONS:
            report.warn(f"叶内残留（白名单例外 {key}，未入库 PHI/模板）：{len(files)} 个，如 {shown}")
        else:
            report.fail(f"叶内残留：{key} 有 {len(files)} 个非 {core.LEAF_README} 文件，如 {shown}")
            bad += 1
    if not bad:
        report.ok(f"叶内只留 {core.LEAF_README}（{len(leaves)} 叶）")


def check_archives(report, fam_dir):
    share = fam_dir / "_share"
    outside = []
    if fam_dir.is_dir():
        for p in fam_dir.rglob("*"):
            if not p.is_file() or not core.is_archive(p.name):
                continue
            try:
                inside = share in p.parents
            except OSError:
                inside = False
            if not inside:
                outside.append(p.relative_to(fam_dir).as_posix())
    if outside:
        report.fail(f"归档不在 _share：{len(outside)} 个，如 " + ", ".join(outside[:5]))
    else:
        report.ok("归档全部位于 _share")


def check_share_depth(report, fam_dir):
    share = fam_dir / "_share"
    if not share.is_dir():
        report.warn("_share 目录不存在")
        return
    deep_files, deep_dirs = [], []
    for p in share.rglob("*"):
        rel = p.relative_to(share).as_posix()
        depth = len(rel.split("/"))
        if p.is_dir():
            if depth >= 2:
                deep_dirs.append(rel)
        elif depth > 2:
            deep_files.append(rel)
    if deep_dirs:
        report.fail(f"_share 有子目录（应打平，层级 ≤2）：{len(deep_dirs)} 个，如 "
                    + ", ".join(deep_dirs[:5]))
    if deep_files:
        report.fail(f"_share 文件层级 >2：{len(deep_files)} 个，如 " + ", ".join(deep_files[:5]))
    if not deep_dirs and not deep_files:
        report.ok(f"_share 层级 ≤2（{sum(1 for p in share.rglob('*') if p.is_file())} 文件）")


def share_archive_names(fam_dir):
    share = fam_dir / "_share"
    names = set()
    if share.is_dir():
        for p in share.rglob("*"):
            if p.is_file() and core.is_archive(p.name):
                names.add(p.name)
    return names


def check_archive_mapping(report, con, fam_dir):
    """doc.rel_path 中 `.../<档名>/<成员>`：档名须在 _share 下真实存在。

    旧解包树（无档名段）用打平名 `a__b` 启发式映射，映射不上只 warn。
    """
    names = share_archive_names(fam_dir)
    strict_miss, legacy_miss, legacy_ok, matched = [], [], 0, 0
    for (rel,) in con.execute("SELECT rel_path FROM doc WHERE rel_path LIKE '%_share/%'"):
        rel = norm_rel(rel)
        parts = [p for p in rel.split("/") if p]
        arc_seg = next((p for p in parts if core.is_archive(p)), None)
        if arc_seg is None and core.is_table(parts[-1]):
            # _share 下的松散共享表（非档内成员），无需映射归档
            matched += 1
            continue
        if arc_seg:
            if arc_seg in names:
                matched += 1
            else:
                strict_miss.append(rel)
            continue
        # 旧解包树：找相邻对 (a, b) → _share 下 a__b.* 是否存在
        ok = False
        for i in range(len(parts) - 1):
            a, b = parts[i], parts[i + 1]
            if any(n.startswith(f"{a}__{b}") for n in names):
                ok = True
                break
        if ok:
            legacy_ok += 1
        else:
            legacy_miss.append(rel)
    if strict_miss:
        report.fail(f"archive→member 映射缺失（档名不在 _share）：{len(strict_miss)} 条，如 "
                    + ", ".join(strict_miss[:3]))
    if legacy_ok:
        report.ok(f"archive→member 映射（旧解包树启发式命中）：{legacy_ok} 条")
    if legacy_miss:
        report.warn(f"archive→member 无法映射（旧解包树，仅提示）：{len(legacy_miss)} 条，如 "
                    + ", ".join(legacy_miss[:3]))
    if matched:
        report.ok(f"archive→member 映射（档名直配）：{matched} 条")


def check_rule(report, meta):
    rule = meta.get("rule", "")
    missing = [k for k in core.RULE_KEYWORDS if k not in rule]
    if missing:
        report.fail(f"meta.rule 与现行口径不一致（缺 {missing}）：{rule}")
    else:
        report.ok("meta.rule 与现行口径一致")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(args):
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"FAIL 库不存在: {db_path}", file=sys.stderr)
        return 1
    family = core.family_of_db(db_path)
    fam_dir = db_path.parent.parent
    code = args.code
    report = Report(quiet=args.quiet)

    con = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        # 1 完整性
        res = con.execute("PRAGMA integrity_check").fetchone()[0]
        if res != "ok":
            report.fail(f"integrity_check: {res}")
        else:
            report.ok("integrity_check")

        # 2 schema
        tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        miss_t = sorted(CORE_TABLES - tabs)
        extra_t = sorted(tabs - CORE_TABLES - {"sqlite_sequence"})
        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        miss_i = sorted(CORE_INDEXES - idx)
        if miss_t or miss_i:
            report.fail(f"schema 缺表 {miss_t} 缺索引 {miss_i}")
        else:
            report.ok(f"schema 核心表 {len(CORE_TABLES)} + 索引 {len(CORE_INDEXES)}")
        if extra_t:
            report.warn(f"schema 额外表: {extra_t}")

        # 3 计数
        q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
        n_doc, n_sheet = q("SELECT count(*) FROM doc"), q("SELECT count(*) FROM sheet")
        n_fact, n_entity = q("SELECT count(*) FROM fact"), q("SELECT count(*) FROM entity")
        report.info(f"计数: doc={n_doc} sheet={n_sheet} fact={n_fact} entity={n_entity}")
        st = dict(con.execute("SELECT status, count(*) FROM sheet GROUP BY status"))
        if sum(st.values()) != n_sheet:
            report.fail(f"sheet 状态合计 {sum(st.values())} != sheet 总数 {n_sheet}")
        else:
            report.ok(f"sheet 状态: {st}")
        n_dup = q("SELECT count(*) FROM sheet_dup")
        n_dup_sheet = st.get("dup", 0) + st.get("subset", 0)
        if n_dup != n_dup_sheet:
            report.fail(f"sheet_dup 边数 {n_dup} != dup/subset sheet 数 {n_dup_sheet}")
        else:
            report.ok(f"sheet_dup 边数 {n_dup}")
        dup_rel = con.execute(
            "SELECT rel_path FROM doc GROUP BY rel_path HAVING count(*)>1").fetchall()
        if dup_rel:
            report.fail(f"doc.rel_path 重复 {len(dup_rel)} 组，如 {[r[0] for r in dup_rel[:3]]}")
        else:
            report.ok("doc.rel_path 唯一")

        # 4 meta
        meta = core.get_meta(con)
        for k in REQUIRED_META:
            if k not in meta:
                report.fail(f"meta 缺 {k}")
        if all(k in meta for k in REQUIRED_META):
            report.ok(f"meta: short_code={meta['short_code']} built_at={meta.get('built_at')}")
        if not code:
            code = meta.get("short_code", "")
        for k, v in (("n_doc", n_doc), ("n_sheet", n_sheet), ("n_fact", n_fact),
                     ("n_entity", n_entity)):
            if k in meta and str(meta[k]) != str(v):
                report.fail(f"meta.{k}={meta[k]} 与实际 {v} 不一致")
        check_rule(report, meta)

        # 5 孤儿引用
        orphans = [
            ("doc.project_id", "SELECT count(*) FROM doc WHERE project_id NOT IN (SELECT id FROM project)"),
            ("sheet.doc_id", "SELECT count(*) FROM sheet WHERE doc_id NOT IN (SELECT id FROM doc)"),
            ("fact.sheet_id", "SELECT count(*) FROM fact WHERE sheet_id NOT IN (SELECT id FROM sheet)"),
            ("fact.entity_id", "SELECT count(*) FROM fact WHERE entity_id IS NOT NULL"
                               " AND entity_id NOT IN (SELECT id FROM entity)"),
            ("entity.project_id", "SELECT count(*) FROM entity WHERE project_id NOT IN (SELECT id FROM project)"),
            ("sheet_dup.sheet_id", "SELECT count(*) FROM sheet_dup WHERE sheet_id NOT IN (SELECT id FROM sheet)"),
            ("sheet_dup.dup_sheet_id", "SELECT count(*) FROM sheet_dup WHERE dup_sheet_id NOT IN (SELECT id FROM sheet)"),
        ]
        bad_ref = [(n, q(s), s) for n, s in orphans]
        bad_ref = [(n, c) for n, c, _ in bad_ref if c]
        if bad_ref:
            report.fail(f"孤儿引用: {bad_ref}")
        else:
            report.ok("引用完整（7 组）")

        # 6 doc.n_sheets
        bad_n = con.execute(
            "SELECT d.id, d.rel_path, d.n_sheets, count(s.id) FROM doc d"
            " LEFT JOIN sheet s ON s.doc_id=d.id GROUP BY d.id"
            " HAVING d.n_sheets != count(s.id) LIMIT 5").fetchall()
        if bad_n:
            report.fail(f"doc.n_sheets 与 sheet 数不符 {len(bad_n)} 个，如 "
                        + str([(r[1], r[2], r[3]) for r in bad_n[:3]]))
        else:
            report.ok("doc.n_sheets 一致")

        # 7 entity
        empty_kn = q("SELECT count(*) FROM entity WHERE key_norm IS NULL OR key_norm=''")
        if empty_kn:
            report.fail(f"entity 空 key_norm: {empty_kn}")
        else:
            report.ok("entity key_norm 无空值")
        dup_kn = con.execute(
            "SELECT project_id, key_norm, count(*) c FROM entity"
            " GROUP BY project_id, key_norm HAVING c>1 LIMIT 5").fetchall()
        if dup_kn:
            report.warn(f"同项目同 key_norm 重复 {len(dup_kn)} 组，如 {dup_kn[:3]}")
        else:
            report.ok("entity 同项目 key_norm 唯一")
        if n_entity:
            dist = con.execute(
                "SELECT project_id, count(*) c FROM entity GROUP BY project_id"
                " ORDER BY c DESC LIMIT 3").fetchall()
            report.info(f"entity 分布（前 3 项目）: {dist}")

        # 8 台账覆盖
        ledgers, rows = discover_ledgers(args.del_root or core.ledger_roots(),
                                         code.upper(), family)
        report.info(f"台账归属 {family}/{code}: {len(ledgers)} 个，{len(rows)} 行")
        if not rows:
            report.fail(f"未发现归属 {family}/{code} 的台账"
                        f"（{args.del_root or core.ledger_roots()}）")
        else:
            uncovered, matched_rel, matched_sha, dst_missing = [], 0, 0, []
            for did, rel, sha in con.execute("SELECT id, rel_path, sha256 FROM doc"):
                hits = coverage_match(rel, sha, rows, code)
                if not hits:
                    uncovered.append(rel)
                    continue
                if any(norm_rel(h.get("rel") or "") == norm_rel(rel) for h in hits):
                    matched_rel += 1
                else:
                    matched_sha += 1
                for h in hits:
                    d = (h.get("dst") or "").strip()
                    if d and (len(d) > 2 and d[1] == ":"):
                        if not Path(core.norm_path(d)).exists():
                            dst_missing.append(d)
            if uncovered:
                report.fail(f"台账覆盖不足：{len(uncovered)}/{n_doc} 条 doc 无 rel/sha 命中，如 "
                            + ", ".join(uncovered[:3]))
            else:
                report.ok(f"台账覆盖 {n_doc}/{n_doc}（rel {matched_rel} / sha {matched_sha}）")
            if dst_missing:
                report.warn(f"台账 dst 不存在: {len(dst_missing)} 个，如 {dst_missing[:3]}")
            else:
                report.ok("台账 dst 全部存在")

        # 9 新口径
        if fam_dir.is_dir():
            check_leaf_clean(report, fam_dir, family)
            check_archives(report, fam_dir)
            check_share_depth(report, fam_dir)
            check_archive_mapping(report, con, fam_dir)
        else:
            report.warn(f"家族目录不存在，跳过结构校验: {fam_dir}")
    finally:
        con.close()

    print(f"== 结果 ==  fail={report.n_fail} warn={report.n_warn}")
    return 1 if report.n_fail else 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="校验短码库（结构/计数/引用/台账/新口径）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("台账按 <code>+<family> 精确归属：默认扫 {DEL}/harvested 与 {DEL}/_by-function 下 MOVED/新 CLI 台账，\n"
                "按文件名 token 与行内路径证据筛，再以 rel 精确或 sha256 命中做覆盖。\n"
                "新口径：叶内只留 目录说明.md（PHI 例外 warn）、归档只在 _share、\n"
                "_share 层级 ≤2 无子目录、meta.rule 现行口径、archive→member 映射存在。"))
    ap.add_argument("--db", required=True, type=Path, help="库文件，如 ...\\MHE_AN\\_db\\MHE_ANdb")
    ap.add_argument("--code", default=None, help="短码（缺省读 meta.short_code）")
    ap.add_argument("--del-root", type=Path, default=DEFAULT_DEL_ROOT,
                    help="台账根目录（缺省同时扫 {DEL}/harvested 与 {DEL}/_by-function）")
    ap.add_argument("--quiet", action="store_true", help="只打印 WARN/FAIL 与结果行")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
