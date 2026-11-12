# -*- coding: utf-8 -*-
"""校验短码库：结构 / 计数 / 引用 / 台账 / 新口径（_share + 叶内只留说明）。

分级：P0 阻断（exit 1）；P1 应修；P2 提示（其中台账门输出降为 info 级）。
校验项（括号内为分级）：
  D1  完整性 integrity_check（P0）
  D2  schema 核心表 / 索引（P0）
  D3  计数与去重一致性（sheet 状态合计、sheet_dup 边、doc.rel_path 唯一）（P0）
  D4  meta（short_code/built_at/rule；n_doc/n_sheet/n_fact/n_entity 若存在则比对）（P1）
  D5  孤儿引用（doc/sheet/fact/entity/sheet_dup）（P0）
  D6  doc.n_sheets 与实际 sheet 数一致（P1）
  D7  entity 分布 / 空键 / 同项目同键重复（P1；重复键覆盖 fact >5% 时升 P0）
  D8  台账覆盖（按 <code>+<family> 精确归属台账；rel 或 sha256 命中）与 dst 存在（P2，info 级）
  D9  新口径：叶内只留 目录说明.md（PHI 例外白名单 warn）、归档只在 _share、
      _share 层级 ≤2 且无子目录、archive→member 映射存在（P2）
  D19 库内不收纳伦理材料：扫 doc.rel_path / project.slug / project.leaf / sheet.name，
      命中 伦理|ethics|知情同意|批件|审查申请 即 P0；不扫 fact.item（P0）
  D20 sheet.header 必须取自 blob 前 6 个候选行（防「表头取错行」→ item 全错）（P1）
  D21 fact 层明文 PHI 信号：11 位手机号 / 18 位身份证（P1，去重计数）

每条发现输出 `[P级][FAIL|WARN|INFO][D号] ...`；
汇总行给出 P0/P1/P2 与 fail/warn/info 计数；exit 1 仅当 P0>0。

用法：
  python -B tools/db-library/verify_db.py --db "D:\\MyProjects\\_DataBase\\MHE_AN\\_db\\MHE_ANdb"
退出码：P0>0 → 1（P1/P2 不影响退出码）。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import zlib
from pathlib import Path

from common import sha256
import db_core as core

DEFAULT_DB_ROOT = core.DEFAULT_DB_ROOT
DEFAULT_DEL_ROOT = None
CORE_TABLES = {"meta", "project", "doc", "sheet", "fact", "entity", "sheet_dup"}
CORE_INDEXES = {"ix_fact_sheet", "ix_fact_item", "ix_entity"}
REQUIRED_META = ("short_code", "built_at", "rule")
# D19 禁伦理材料：仅扫元数据/路径字段，不扫 fact.item（数据表里的「伦理批号」列不算）
ETHICS_PAT = re.compile(r"伦理|ethics|知情同意|批件|审查申请", re.I)
ETHICS_FIELDS = (
    ("doc.rel_path", "SELECT rel_path FROM doc"),
    ("project.slug", "SELECT slug FROM project"),
    ("project.leaf", "SELECT leaf FROM project"),
    ("sheet.name", "SELECT name FROM sheet"),
)


class Report:
    """分级报告：P0 阻断 / P1 应修 / P2 提示。

    发现一律打印 `[P级][FAIL|WARN|INFO][D号] ...`；ok / 进度 info 在 --quiet 下隐藏。
    """

    LEVELS = ("FAIL", "WARN", "INFO")

    def __init__(self, quiet=False):
        self.n_fail = 0
        self.n_warn = 0
        self.n_info = 0
        self.n_p0 = 0
        self.n_p1 = 0
        self.n_p2 = 0
        self.quiet = quiet

    def _finding(self, level, prio, code, msg):
        assert level in self.LEVELS, level
        assert prio in (0, 1, 2), prio
        if level == "FAIL":
            self.n_fail += 1
        elif level == "WARN":
            self.n_warn += 1
        else:
            self.n_info += 1
        if prio == 0:
            self.n_p0 += 1
        elif prio == 1:
            self.n_p1 += 1
        else:
            self.n_p2 += 1
        # 发现不静音：--quiet 只隐藏 ok / 进度 info
        print(f"[P{prio}][{level}][{code}] {msg}")

    def ok(self, code, msg):
        if not self.quiet:
            print(f"[ok][{code}] {msg}")

    def info(self, msg, code=None):
        """进度信息（非发现），--quiet 下隐藏，不计入汇总。"""
        if not self.quiet:
            tag = f"[info][{code}]" if code else "[info]"
            print(f"{tag} {msg}")

    def fail(self, code, prio, msg):
        self._finding("FAIL", prio, code, msg)

    def warn(self, code, prio, msg):
        self._finding("WARN", prio, code, msg)

    def info_finding(self, code, prio, msg):
        """info 级发现（如 D8 台账门）：作为发现照常打印并计数。"""
        self._finding("INFO", prio, code, msg)

    def exit_code(self):
        return 1 if self.n_p0 else 0


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
        report.info("叶目录：无（家族下只剩 _db/_share）", "D9")
        return
    bad = 0
    for leaf in leaves:
        files = [p for p in leaf.rglob("*") if p.is_file() and p.name != core.LEAF_README]
        if not files:
            continue
        key = f"{family}/{leaf.name}"
        shown = ", ".join(p.relative_to(fam_dir).as_posix() for p in files[:5])
        if key in core.LEAF_EXCEPTIONS:
            report.warn("D9", 2,
                        f"叶内残留（白名单例外 {key}，未入库 PHI/模板）：{len(files)} 个，如 {shown}")
        else:
            report.fail("D9", 2,
                        f"叶内残留：{key} 有 {len(files)} 个非 {core.LEAF_README} 文件，如 {shown}")
            bad += 1
    if not bad:
        report.ok("D9", f"叶内只留 {core.LEAF_README}（{len(leaves)} 叶）")


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
        report.fail("D9", 2,
                    f"归档不在 _share：{len(outside)} 个，如 " + ", ".join(outside[:5]))
    else:
        report.ok("D9", "归档全部位于 _share")


def check_share_depth(report, fam_dir):
    share = fam_dir / "_share"
    if not share.is_dir():
        report.warn("D9", 2, "_share 目录不存在")
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
        report.fail("D9", 2,
                    f"_share 有子目录（应打平，层级 ≤2）：{len(deep_dirs)} 个，如 "
                    + ", ".join(deep_dirs[:5]))
    if deep_files:
        report.fail("D9", 2,
                    f"_share 文件层级 >2：{len(deep_files)} 个，如 " + ", ".join(deep_files[:5]))
    if not deep_dirs and not deep_files:
        report.ok("D9", f"_share 层级 ≤2（{sum(1 for p in share.rglob('*') if p.is_file())} 文件）")


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
        report.fail("D9", 2,
                    f"archive→member 映射缺失（档名不在 _share）：{len(strict_miss)} 条，如 "
                    + ", ".join(strict_miss[:3]))
    if legacy_ok:
        report.ok("D9", f"archive→member 映射（旧解包树启发式命中）：{legacy_ok} 条")
    if legacy_miss:
        report.warn("D9", 2,
                    f"archive→member 无法映射（旧解包树，仅提示）：{len(legacy_miss)} 条，如 "
                    + ", ".join(legacy_miss[:3]))
    if matched:
        report.ok("D9", f"archive→member 映射（档名直配）：{matched} 条")


def check_rule(report, meta):
    rule = meta.get("rule", "")
    missing = [k for k in core.RULE_KEYWORDS if k not in rule]
    if missing:
        report.fail("D4", 1, f"meta.rule 与现行口径不一致（缺 {missing}）：{rule}")
    else:
        report.ok("D4", "meta.rule 与现行口径一致")


def check_ethics_material(report, con):
    """D19（P0）：库内不得收纳伦理材料。

    仅扫 doc.rel_path / project.slug / project.leaf / sheet.name；
    不扫 fact.item（数据表里的「伦理批号」列不算伦理材料）。
    """
    hits = []
    for label, sql in ETHICS_FIELDS:
        for (v,) in con.execute(sql):
            if v and ETHICS_PAT.search(str(v)):
                hits.append(f"{label}={v}")
    if hits:
        shown = "；".join(hits[:5])
        extra = f"（仅列前 5/{len(hits)}）" if len(hits) > 5 else ""
        report.fail("D19", 0,
                    f"库内收纳伦理材料（禁入库）：{len(hits)} 处{extra}，如 {shown}")
    else:
        report.ok("D19", "库内未收纳伦理材料（doc.rel_path/project.slug/project.leaf/sheet.name）")


def _norm_header_row(cells):
    """表头行归一：去掉 header_of 的重复后缀 `#n`（逐单元格）。"""
    return "\x1f".join(re.sub(r"#\d+$", "", str(c or "")) for c in cells)


def check_header_consistency(report, con):
    """D20（P1）：sheet.header 必须取自 blob 的候选行。

    候选行 = blob 前 6 行 + meta 里登记的 ``note.header_line.<sheet_id>``（1-based）。
    入库时表头行与 blob 同源；若 header 不在候选行里，说明表头被写错（典型：
    把数据首行/元数据键值行当表头），fact.item 随之全错。仪器导出表把真表头
    压在前若干行元数据之后时，用 ``note.header_line.<sid>`` 显式登记行号。
    ``__nohdr__*`` 头（建库时判定无表头）放行。
    """
    hline = {}
    for k, v in con.execute("SELECT k, v FROM meta"
                            " WHERE k LIKE 'note.header_line.%'").fetchall():
        try:
            hline[int(k.rsplit(".", 1)[1])] = int(str(v).strip())
        except (ValueError, IndexError):
            pass
    bad, checked = [], 0
    for sid, name, header, blob in con.execute(
            "SELECT id, name, header, blob FROM sheet"
            " WHERE status='ingested' AND blob IS NOT NULL"):
        checked += 1
        h = header or ""
        if h.startswith("__nohdr__"):
            continue
        try:
            rows = [ln.split("	") for ln in
                    zlib.decompress(blob).decode("utf-8", "replace").splitlines()]
        except Exception:
            continue
        cands = {_norm_header_row(r) for r in rows[:6]}
        ln = hline.get(sid)
        if ln and 1 <= ln <= len(rows):
            cands.add(_norm_header_row(rows[ln - 1]))
        if _norm_header_row(h.split("")) in cands:
            continue
        bad.append((sid, name))
    if bad:
        report.warn("D20", 1, f"sheet.header 不在 blob 候选行内 {len(bad)}/{checked} 张，"
                              f"如 {bad[:5]}")
    else:
        report.ok("D20", f"sheet.header 均来自 blob 表头行（{checked} 张）")


PHI_SIGNALS = (
    ("手机号", 11, re.compile(r"1[3-9]\d{9}")),
    ("身份证", 18, re.compile(r"\d{17}[\dXx]")),
)


def check_phi_plaintext(report, con):
    """D21（P1）：fact 层明文 PHI 信号（11 位手机号 / 18 位身份证，去重计数）。"""
    for label, width, pat in PHI_SIGNALS:
        rows = con.execute(
            "SELECT value_text, count(*) FROM fact"
            " WHERE value_text IS NOT NULL AND length(value_text)=?"
            " AND value_text GLOB '[0-9]*' GROUP BY value_text", (width,)).fetchall()
        hits = [(v, c) for v, c in rows if pat.fullmatch(v or "")]
        total = sum(c for _, c in hits)
        if hits:
            sample = "、".join(v[:3] + "***" + v[-2:] for v, _ in hits[:3])
            report.warn("D21", 1, f"fact 层明文{label} {len(hits)} 个值 / {total} 行，如 {sample}")
        else:
            report.ok("D21", f"fact 层无明文{label}")


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
        # 1 完整性（P0）
        res = con.execute("PRAGMA integrity_check").fetchone()[0]
        if res != "ok":
            report.fail("D1", 0, f"integrity_check: {res}")
        else:
            report.ok("D1", "integrity_check")

        # 2 schema（P0）
        tabs = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        miss_t = sorted(CORE_TABLES - tabs)
        extra_t = sorted(tabs - CORE_TABLES - {"sqlite_sequence"})
        idx = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        miss_i = sorted(CORE_INDEXES - idx)
        if miss_t or miss_i:
            report.fail("D2", 0, f"schema 缺表 {miss_t} 缺索引 {miss_i}")
        else:
            report.ok("D2", f"schema 核心表 {len(CORE_TABLES)} + 索引 {len(CORE_INDEXES)}")
        if extra_t:
            # 额外表不属 D2 的「齐」判定；只提示，按 P2 计（不改变 exit）
            report.warn("D2", 2, f"schema 额外表: {extra_t}")

        # 3 计数（P0）
        q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
        n_doc, n_sheet = q("SELECT count(*) FROM doc"), q("SELECT count(*) FROM sheet")
        n_fact, n_entity = q("SELECT count(*) FROM fact"), q("SELECT count(*) FROM entity")
        report.info(f"计数: doc={n_doc} sheet={n_sheet} fact={n_fact} entity={n_entity}", "D3")
        st = dict(con.execute("SELECT status, count(*) FROM sheet GROUP BY status"))
        if sum(st.values()) != n_sheet:
            report.fail("D3", 0, f"sheet 状态合计 {sum(st.values())} != sheet 总数 {n_sheet}")
        else:
            report.ok("D3", f"sheet 状态: {st}")
        n_dup = q("SELECT count(*) FROM sheet_dup")
        n_dup_sheet = st.get("dup", 0) + st.get("subset", 0)
        if n_dup != n_dup_sheet:
            report.fail("D3", 0, f"sheet_dup 边数 {n_dup} != dup/subset sheet 数 {n_dup_sheet}")
        else:
            report.ok("D3", f"sheet_dup 边数 {n_dup}")
        dup_rel = con.execute(
            "SELECT rel_path FROM doc GROUP BY rel_path HAVING count(*)>1").fetchall()
        if dup_rel:
            report.fail("D3", 0,
                        f"doc.rel_path 重复 {len(dup_rel)} 组，如 {[r[0] for r in dup_rel[:3]]}")
        else:
            report.ok("D3", "doc.rel_path 唯一")

        # 4 meta（P1）
        meta = core.get_meta(con)
        for k in REQUIRED_META:
            if k not in meta:
                report.fail("D4", 1, f"meta 缺 {k}")
        if all(k in meta for k in REQUIRED_META):
            report.ok("D4", f"meta: short_code={meta['short_code']} built_at={meta.get('built_at')}")
        if not code:
            code = meta.get("short_code", "")
        for k, v in (("n_doc", n_doc), ("n_sheet", n_sheet), ("n_fact", n_fact),
                     ("n_entity", n_entity)):
            if k in meta and str(meta[k]) != str(v):
                report.fail("D4", 1, f"meta.{k}={meta[k]} 与实际 {v} 不一致")
        check_rule(report, meta)

        # 5 孤儿引用（P0）
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
            report.fail("D5", 0, f"孤儿引用: {bad_ref}")
        else:
            report.ok("D5", "引用完整（7 组）")

        # 6 doc.n_sheets（P1）
        bad_n = con.execute(
            "SELECT d.id, d.rel_path, d.n_sheets, count(s.id) FROM doc d"
            " LEFT JOIN sheet s ON s.doc_id=d.id GROUP BY d.id"
            " HAVING d.n_sheets != count(s.id) LIMIT 5").fetchall()
        if bad_n:
            report.fail("D6", 1,
                        f"doc.n_sheets 与 sheet 数不符 {len(bad_n)} 个，如 "
                        + str([(r[1], r[2], r[3]) for r in bad_n[:3]]))
        else:
            report.ok("D6", "doc.n_sheets 一致")

        # 7 entity（P1；重复键覆盖 fact >5% 升 P0，见标准 §3.4①）
        empty_kn = q("SELECT count(*) FROM entity WHERE key_norm IS NULL OR key_norm=''")
        if empty_kn:
            report.fail("D7", 1, f"entity 空 key_norm: {empty_kn}")
        else:
            report.ok("D7", "entity key_norm 无空值")
        total_kn = q("SELECT count(*) FROM entity")
        dup_kn = con.execute(
            "SELECT project_id, key_norm, count(*) c FROM entity"
            " GROUP BY project_id, key_norm HAVING c>1").fetchall()
        if dup_kn:
            n_dup_rows = sum(c for _, _, c in dup_kn)
            non_null = q("SELECT count(*) FROM fact WHERE entity_id IS NOT NULL")
            bad_fact = q(
                "SELECT count(*) FROM fact f JOIN entity e ON e.id=f.entity_id"
                " WHERE (e.project_id, e.key_norm) IN ("
                "  SELECT project_id, key_norm FROM entity"
                "  GROUP BY project_id, key_norm HAVING count(*)>1)")
            pct = (bad_fact / non_null * 100) if non_null else 0.0
            msg = (f"同项目同 key_norm 重复 {len(dup_kn)} 组（覆盖 entity {n_dup_rows}/{total_kn} 行），"
                   f"{bad_fact}/{non_null}（{pct:.1f}%）fact 指向重复键")
            if pct > 5:
                report.fail("D7", 0, msg + "，超过 5% 阈值 → 实体键不可用")
            else:
                report.warn("D7", 1, msg + f"，如 {dup_kn[:3]}")
        else:
            report.ok("D7", "entity 同项目 key_norm 唯一")
        if n_entity:
            dist = con.execute(
                "SELECT project_id, count(*) c FROM entity GROUP BY project_id"
                " ORDER BY c DESC LIMIT 3").fetchall()
            report.info(f"entity 分布（前 3 项目）: {dist}", "D7")

        # 8 台账覆盖（P2，输出降为 info 级）
        ledgers, rows = discover_ledgers(args.del_root or core.ledger_roots(),
                                         code.upper(), family)
        report.info(f"台账归属 {family}/{code}: {len(ledgers)} 个，{len(rows)} 行", "D8")
        if not rows:
            report.info_finding("D8", 2,
                                f"未发现归属 {family}/{code} 的台账"
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
                report.info_finding("D8", 2,
                                    f"台账覆盖不足：{len(uncovered)}/{n_doc} 条 doc 无 rel/sha 命中，如 "
                                    + ", ".join(uncovered[:3]))
            else:
                report.ok("D8", f"台账覆盖 {n_doc}/{n_doc}（rel {matched_rel} / sha {matched_sha}）")
            if dst_missing:
                report.info_finding("D8", 2,
                                    f"台账 dst 不存在: {len(dst_missing)} 个，如 {dst_missing[:3]}")
            else:
                report.ok("D8", "台账 dst 全部存在")

        # 9 新口径（P2）
        if fam_dir.is_dir():
            check_leaf_clean(report, fam_dir, family)
            check_archives(report, fam_dir)
            check_share_depth(report, fam_dir)
            check_archive_mapping(report, con, fam_dir)
        else:
            report.warn("D9", 2, f"家族目录不存在，跳过结构校验: {fam_dir}")

        # D19 库内不收纳伦理材料（P0）
        check_ethics_material(report, con)
        # D20 表头与 blob 一致性（P1）；D21 fact 层明文 PHI 信号（P1）
        check_header_consistency(report, con)
        check_phi_plaintext(report, con)
    finally:
        con.close()

    print(f"== 结果 == P0={report.n_p0} P1={report.n_p1} P2={report.n_p2}"
          f" (fail={report.n_fail} warn={report.n_warn} info={report.n_info})")
    return report.exit_code()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="校验短码库（结构/计数/引用/台账/新口径/禁伦理材料；P0/P1/P2 分级）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("台账按 <code>+<family> 精确归属：默认扫 {DEL}/harvested 与 {DEL}/_by-function 下 MOVED/新 CLI 台账，\n"
                "按文件名 token 与行内路径证据筛，再以 rel 精确或 sha256 命中做覆盖。\n"
                "新口径：叶内只留 目录说明.md（PHI 例外 warn）、归档只在 _share、\n"
                "_share 层级 ≤2 无子目录、meta.rule 现行口径、archive→member 映射存在。"))
    ap.add_argument("--db", required=True, type=Path, help="库文件，如 ...\\MHE_AN\\_db\\MHE_ANdb")
    ap.add_argument("--code", default=None, help="短码（缺省读 meta.short_code）")
    ap.add_argument("--del-root", type=Path, default=DEFAULT_DEL_ROOT,
                    help="台账根目录（缺省同时扫 {DEL}/harvested 与 {DEL}/_by-function）")
    ap.add_argument("--quiet", action="store_true",
                    help="只打印发现（含 INFO 级发现）与结果行，隐藏 ok/进度信息")
    return ap.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main(parse_args()))
