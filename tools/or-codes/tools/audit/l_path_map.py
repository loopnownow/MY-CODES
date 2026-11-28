# -*- coding: utf-8 -*-
"""l_path_map.py — 提取代码/文档里写死的 L 盘路径，判存活并给出改后提案（只读）。

为什么：L 盘已多次重整，脚本里的 `L:\\...` 仍有大量指向已不存在的目录。
改名（批 3/4）之前先把这些指向收敛到工程 `ref/settings.ini [paths]` 或 CLI 参数。

输出（都写在 --out 目录，**不修改任何源文件**）：
  path-map.tsv        全量命中：action / status / src_file / line / raw / proposal / note
  batch1-worklist.md  批 1 清单（按文件分组：可直接改 / 需人工定名）
  batch1-worklist.tsv 同上，机器可读

列说明：
  action       edit（改代码/配置）/ doc（改文档）/ manual（需人工定名）
  status       exists / dead（原文路径当前是否可解析）
  proposal     按 PROJECT_MAP、SUB_MAP 与实盘工程索引推出的新路径；推不出写 MANUAL

用法：
  python l_path_map.py                       # 默认扫描根与输出目录
  python l_path_map.py --out <dir>
  python l_path_map.py --roots {or_codes},L:\\目录说明.md

退出码：0 正常；2 前置缺失。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

# ---- 配置 ----
# Public copy (t2312u, C1): lab-absolute scan roots and the 20261121
# project-rename map stay in the source tree; they are not vendored here.
SCAN_ROOTS = []
EXTS = {".py", ".m", ".ini", ".bat", ".md", ".json", ".yaml", ".yml"}
DEFAULT_OUT = os.path.join(os.environ.get("TEMP", "."), "l_path_map")
PROJECT_MAP = {}
# 目录级改名（image/roi 分层）
SUB_MAP = [
    (r"\CAC_org", r"\image"),
    (r"\CAC_roi", r"\roi"),
    (r"\img", r"\image"),
    (r"\01_原始DICOM(25例)", r"\image"),
    (r"\01_原始IMA(26例)", r"\image"),
]
MANUAL_SUB = [r"\A-ROI", r"\B-ROI", r"\D-ROI", r"\Red-House-ROI",
              r"\ROI_solid", r"\ROI_whole tumor", r"\g", r"\ng",
              r"\T1", r"\tirm", r"\Flair", r"\补充"]

PATH_RE = re.compile(r"[Ll]:\\[^\s\"'<>|,;()`{}\]]+")
PROSE_RE = re.compile(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]")  # 中文/全角：多半是正文片段
STOP_SUB = {"nii", "img", "roi", "dicom", "data", "images", "derivatives", "logs",
            "rawdata", "atlases", "pic", "result", "results", "tmp", "temp", "test"}

# 已撤销的旧根（首段不是家族名，但仍要抓出来改指向）
LEGACY_ROOTS = {"00USED", "0HABITAT", "0GL", "HE_DCM_LY", "HE_NII_LY", "SCH_CONTROL",
                "SCH_TUMOR", "SCH_KIDNEY", "SCH_HYPERSPLENISM", "SCH_NII_DONE", "SCH_COPD",
                "MCI_LYA", "DPABI", "INCOMING", "大家的数据资料"}


def load_families(diseases: str = r"L:\_diseases.csv") -> set[str]:
    """家族桶白名单（含 _Del）：用于过滤掉正文里被截断的伪路径。"""
    fams = {"_DEL"}
    if os.path.isfile(diseases):
        with open(diseases, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                fam = (row.get("family") or row.get("suffix") or "").strip()
                if fam:
                    fams.add(fam.upper())
    return fams


def plausible(raw: str, fams: set[str]) -> bool:
    """要求 L:\\<家族>\\<至少一层>（或已撤销的旧根）；用于滤掉正文里被截断的伪路径。"""
    body = raw[3:].strip("\\")
    parts = [p for p in body.split("\\") if p]
    if not parts:
        return False
    head = parts[0].upper()
    if head in LEGACY_ROOTS:
        return True
    return len(parts) >= 2 and head in fams


def build_live_index(root: str = "L:\\") -> dict[str, str]:
    """实盘索引：小写 工程名 / family\\project / 第三层名 -> 绝对路径。"""
    idx: dict[str, str] = {}
    for fam in os.listdir(root):
        fp = os.path.join(root, fam)
        if not os.path.isdir(fp) or fam in {"_Del", "$RECYCLE.BIN", "System Volume Information"}:
            continue
        for proj in os.listdir(fp):
            pp = os.path.join(fp, proj)
            if not os.path.isdir(pp):
                continue
            idx.setdefault(proj.lower(), pp)
            idx[(fam + "\\" + proj).lower()] = pp
            try:
                for sub in os.listdir(pp):
                    if len(sub) < 4 or sub.lower() in STOP_SUB:
                        continue   # 通用子目录名不进索引，避免 "nii"/"img" 误撞
                    idx.setdefault(sub.lower(), os.path.join(pp, sub))
            except OSError:
                continue
    return idx


def resolve_legacy(raw: str, idx: dict[str, str]) -> tuple[str, str]:
    """旧根 / 扁平根：按首个能对上实盘工程名的路径段拼接。"""
    body = raw[3:].strip("\\")
    parts = [p for p in body.split("\\") if p]
    for i, seg in enumerate(parts):
        key = seg.lower()
        if key in idx:
            tail = "\\" + "\\".join(parts[i + 1:]) if parts[i + 1:] else ""
            return idx[key] + tail, f"leaf:{seg}"
    if parts and "_" in parts[0]:
        fam, _, rest = parts[0].partition("_")
        for head in (fam, fam.upper()):
            cand = os.path.join("L:\\", head, rest)
            if os.path.isdir(cand):
                tail = "\\" + "\\".join(parts[1:]) if parts[1:] else ""
                return cand + tail, f"flat:{parts[0]}"
    if len(parts) >= 2 and not parts[0].startswith("_"):
        for head in (parts[0], parts[0].upper()):
            cand = os.path.join("L:\\", head, parts[1])
            if os.path.isdir(cand):
                tail = "\\" + "\\".join(parts[2:]) if parts[2:] else ""
                return cand + tail, f"flat:{head}"
    return "MANUAL", "未在实盘工程表中"


def is_runtime_out(path: str) -> bool:
    """运行时才生成的输出目录（derivatives / logs / .tmp），不存在属正常。"""
    low = path.lower()
    return "\\derivatives\\" in low or "\\logs\\" in low or "\\.tmp" in low


def action_of(src_file: str) -> str:
    ext = os.path.splitext(src_file)[1].lower()
    if ext in {".py", ".m", ".ini", ".bat"}:
        return "edit"
    if ext in {".md", ".json", ".yaml", ".yml"}:
        return "doc"
    return "manual"


def write_worklist(recs: list[dict], out_dir: str, tag: str = "") -> tuple[str, str]:
    """批 1 清单：按文件分组给 action，不修改任何源文件。tag='a' 为改名前的指向修复批。"""
    md = os.path.join(out_dir, f"batch1{tag}-worklist.md")
    tsv = os.path.join(out_dir, f"batch1{tag}-worklist.tsv")
    order = {"edit": 0, "doc": 1, "manual": 2, "noop": 3}
    seen = set()
    uniq_recs = []
    for r in recs:
        key = (r["src_file"], r["line"], r["raw"], r["action"])
        if key in seen:
            continue
        seen.add(key)
        uniq_recs.append(r)
    recs = sorted(uniq_recs, key=lambda r: (order[r["action"]], r["src_file"].lower(), r["line"]))
    with open(tsv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["action", "status", "src_file", "line",
                                           "raw", "proposal", "note"], delimiter="\t")
        w.writeheader()
        w.writerows(recs)
    by_file: dict[str, list[dict]] = {}
    for r in recs:
        by_file.setdefault(r["src_file"], []).append(r)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# 批 1 清单：L 盘写死路径改指向\n\n")
        fh.write("口径：先出清单不改文件；`edit` 改代码/配置，`doc` 改文档，`manual` 需人工定名。\n")
        fh.write("注意：`改为` 只解决「指向哪个工程」；表/报告类路径仍按口径落 D:，不要把 CSV/PDF 指到 L:。\n\n")
        fh.write(f"合计 {len(recs)} 处命中 / {len(by_file)} 个文件。\n\n")
        fh.write("## 一、可直接改（edit / doc）\n\n")
        for f in sorted(by_file):
            rows = [r for r in by_file[f] if r["action"] in ("edit", "doc")]
            if not rows:
                continue
            fh.write(f"### `{f}`\n\n| 行 | 现值 | 改为 | 依据 |\n|---:|---|---|---|\n")
            for r in rows:
                fh.write(f"| {r['line']} | `{r['raw']}` | `{r['proposal']}` | {r['note']} |\n")
            fh.write("\n")
        fh.write("## 二、需人工定名（manual）\n\n")
        for f in sorted(by_file):
            rows = [r for r in by_file[f] if r["action"] == "manual"]
            if not rows:
                continue
            fh.write(f"### `{f}`\n\n| 行 | 现值 | 提案 | 说明 |\n|---:|---|---|---|\n")
            for r in rows:
                fh.write(f"| {r['line']} | `{r['raw']}` | {r['proposal']} | {r['note']} |\n")
            fh.write("\n")
        noop = [r for r in recs if r["action"] == "noop"]
        if noop:
            fh.write("## 三、无需改动（noop）\n\n")
            fh.write(f"{len(noop)} 处原文已是现行路径，仅登记：\n\n")
            for r in noop:
                fh.write(f"- `{r['src_file']}` 第 {r['line']} 行：`{r['raw']}`\n")
            fh.write("\n")
    return md, tsv


def apply_project_map(raw: str) -> tuple[str | None, str | None]:
    """PROJECT_MAP + SUB_MAP 改写；不命中返回 (None, None)。"""
    low = raw.lower()
    for old in PROJECT_MAP:
        if low == old.lower() or low.startswith(old.lower() + "\\"):
            new = PROJECT_MAP[old] + raw[len(old):]
            for m in MANUAL_SUB:
                if new.lower().startswith((PROJECT_MAP[old] + m).lower()):
                    return "MANUAL", f"子目录 {m} 需人工定名（ROI 变体/序列名）"
            for old_sub, new_sub in SUB_MAP:
                new = re.sub(re.escape(old_sub) + r"(?=\\|$)", lambda _m, s=new_sub: s,
                             new, count=1, flags=re.I)
            return new, f"project:{old}"
    return None, None


def propose(raw: str, idx: dict[str, str], rename: bool = True) -> tuple[str, str]:
    """返回 (提案路径, 说明)；推不出写 MANUAL。

    rename=True  : 同时按 PROJECT_MAP 改成目标工程名（改名后使用）
    rename=False : 只把失效旧根解析回**当前**实存路径（改名前的安全批）
    """
    if rename:
        prop, note = apply_project_map(raw)
        if prop:
            return prop, note
    resolved, note = resolve_legacy(raw, idx)
    if resolved == "MANUAL":
        return "MANUAL", note
    if rename:
        prop2, note2 = apply_project_map(resolved)
        if prop2:
            return prop2, f"{note} -> {note2}"
    return resolved, note


def iter_files(roots: list[str]):
    skip = {"l_path_map.py"}   # 自身含映射表字面，避免自证
    for root in roots:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__", ".Del"}]
            for fn in filenames:
                if fn in skip:
                    continue
                if os.path.splitext(fn)[1].lower() in EXTS:
                    yield os.path.join(dirpath, fn)


def main() -> int:
    ap = argparse.ArgumentParser(description="L 盘写死路径盘点与改名提案（只读）")
    ap.add_argument("--roots", default="", help="逗号分隔扫描根")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-rename", action="store_true",
                    help="只把失效旧根解析回当前实存路径（改名前的批 1）；缺省给出改名后的目标名")
    args = ap.parse_args()

    roots = [r.strip() for r in args.roots.split(",") if r.strip()] or SCAN_ROOTS
    missing = [r for r in roots if not os.path.exists(r)]
    if missing:
        print("[FAIL] 扫描根不存在：" + ", ".join(missing))
        return 2
    os.makedirs(args.out, exist_ok=True)

    recs = []
    fams = load_families()
    idx = build_live_index()
    for path in iter_files(roots):
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for raw in PATH_RE.findall(line):
                raw = raw.rstrip(".,*`")
                if not plausible(raw, fams):
                    continue
                if raw[2:4] == "\\\\":
                    continue   # 文档里的转义双反斜杠，不是真路径
                if raw.endswith("\\"):
                    continue   # 被尖括号/空格截断的半截路径
                if PROSE_RE.search(raw) and not os.path.exists(raw):
                    continue   # 正文片段/中文说明，不是真路径
                if any(k in line for k in ("已撤", "已不存在")) or "retired" in line.lower():
                    continue   # 说明"已撤销"的报错文案，保留原文
                prop, note = propose(raw, idx, rename=not args.no_rename)
                if prop != "MANUAL" and prop.lower() == raw.lower():
                    if os.path.exists(prop):
                        act, note = "noop", "已是现行路径"
                    elif is_runtime_out(prop):
                        act, note = "noop", "运行时生成的输出目录"
                    else:
                        act, note = "manual", "解析结果仍不存在（子夹已不在该叶）"
                else:
                    act = "manual" if prop == "MANUAL" else action_of(path)
                recs.append({
                    "action": act,
                    "status": "exists" if os.path.exists(raw) else "dead",
                    "src_file": path,
                    "line": no,
                    "raw": raw,
                    "proposal": prop,
                    "note": note,
                })

    tsv = os.path.join(args.out, "path-map.tsv" if not args.no_rename else "path-map-a.tsv")
    with open(tsv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["action", "status", "src_file", "line",
                                           "raw", "proposal", "note"], delimiter="\t")
        w.writeheader()
        w.writerows(recs)
    md, wl = write_worklist(recs, args.out, tag="a" if args.no_rename else "")

    uniq = {(r["raw"], r["proposal"]) for r in recs}
    dead = {r["raw"] for r in recs if r["status"] == "dead"}
    manual = {(r["raw"], r["proposal"]) for r in recs if r["proposal"] == "MANUAL"}
    edit = {r["src_file"] for r in recs if r["action"] == "edit"}
    doc = {r["src_file"] for r in recs if r["action"] == "doc"}
    print(f"hits={len(recs)} unique={len(uniq)} dead_unique={len(dead)} manual_unique={len(manual)}")
    print(f"files_edit={len(edit)} files_doc={len(doc)} worklist={wl}")
    print(f"report: {tsv}")
    print(f"worklist_md: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
