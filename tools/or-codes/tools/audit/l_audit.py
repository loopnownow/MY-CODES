# -*- coding: utf-8 -*-
"""l_audit.py — L 盘（原始影像盘）结构与路径清洁度体检，只读。

判定目标布局（主人 20261121 定案）：
    L:\\<family>\\<project>\\img|roi\\<subject>\\[<series>]
      * family  = L:\\_diseases.csv 的 family 列
      * project = 论文工程 prefix_suffix（末段在基码表）或队列池 suffix_pool（首段在基码表）
      * 层深上限 5（family 记 1 层）
      * image 放原始影像与影像伴生件；roi 放掩膜；派生图像与结果件一律外迁 G: / D:

检查项（TSV 的 check 列）：
  NAME      工程名形态不合上述两种
  CHARS     工程名含空格 / 中文 / = ( ) + , 等非 [A-Za-z0-9._-] 字符
  CHARS_SUBJECT 受试者/子目录名含空格或中文（PHI 口径保留，仅 INFO）
  EXTNAME   目录名以影像扩展名结尾（如 <姓名>.nii 实为目录）
  DUP       目录名含 65535 / -1 / _1 / (n) 等重复标记
  JUNKDIR   copy / new / old / temp / test / 副本 等可疑目录
  RESDIR    __pycache__ / VBM_* / *results* / *分析结果* 等结果目录
  LAYOUT    工程根除 image / roi 外还有其它夹；缺 image 记 LAYOUT_NO_IMAGE
  LOOSE     工程根或家族根散文件
  OFFLOAD   应外迁的文本 / 模型 / 打包件（xlsx csv docx pdf py m r mat npz pkl html log rar zip）
  RASTER    位图（png jpg bmp tif）计数，供病理图与截图人工分流
  DEEP      目录深度超过 5
  INDEX     _index.csv 漂移（登记路径不存在 / 工程未登记）

用法（默认只读；报告写 --out，不触碰数据）：
  python l_audit.py --family CC,RDL --out L:\\_Del\\_by-function\\L-struct-20261121
  python l_audit.py --all --max-files 50000 --out <dir>
  python l_audit.py --all --check-index --out <dir>
  python l_audit.py --all --out <dir> --resume          # 跳过报告里已出现的家族

退出码：0 = 无 FAIL；1 = 有 FAIL；2 = 参数或前置缺失。
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime

# ---- 配置（改这里，不改下面的逻辑）----
ROOT = os.environ.get("ROOT", "")
DISEASES = os.environ.get("DISEASES", "")
INDEX_CSV = os.environ.get("INDEX_CSV", "")
SKIP_TOP = {"_Del", "System Volume Information", "$RECYCLE.BIN", "DPABI", "INCOMING"}
MAX_DEPTH = 5                 # 目标层深上限（family 记 1 层）；DICOM 序列 UID 子层不计 FAIL
MAX_WALK = 14                 # 实际遍历上限（DICOM 树常见 10 层）
MAX_FILES_DEFAULT = 200_000   # 单工程遍历文件上限（exFAT 慢，超出记 TRUNC）

NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
EXT_TAIL = re.compile(r"(?i)\.(nii|nrrd|dcm|ima|mitk|nii\.gz)$")
DUP_MARK = re.compile(r"(65535|(?<![A-Za-z0-9])-\d+$|_\d{1,2}$|\(\d+\)$|\bcopy\b)", re.I)
JUNK_DIR = re.compile(r"(?i)^(copy|copy\d*|.*[-_ ]copy|副本.*|new|old|test|tmp|temp|backup|bak)$")
RES_DIR = re.compile(r"(?i)^(__pycache__|vbm.*|.*results?|.*分析结果.*|.*统计.*|.*stats.*)$")

OFFLOAD_EXT = {".xlsx", ".xls", ".csv", ".docx", ".doc", ".pdf", ".py", ".m", ".r",
               ".mat", ".npz", ".pkl", ".pickle", ".html", ".htm", ".tsv", ".log",
               ".rar", ".zip", ".7z", ".pyc", ".bak", ".bk"}
RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".gif"}

FAIL_CHECKS = {"NAME", "CHARS", "EXTNAME", "LAYOUT_NO_IMAGE", "INDEX"}

rows: list[dict] = []
stats = {"families": 0, "projects": 0, "files": 0, "truncated": 0, "fails": 0}


def add(check: str, family: str, project: str, path: str, count: int = 1, detail: str = "") -> None:
    rows.append({"check": check, "family": family, "project": project,
                 "path": path, "count": count, "detail": detail})
    if check in FAIL_CHECKS:
        stats["fails"] += 1


def load_diseases(path: str) -> tuple[set[str], set[str]]:
    """返回 (基码集合, 家族集合)；缺表直接报错，不静默回退。"""
    if not os.path.isfile(path):
        print(f"[FAIL] 家族表缺失：{path}")
        sys.exit(2)
    suffixes, families = set(), set()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            suf = (row.get("suffix") or "").strip()
            fam = (row.get("family") or "").strip()
            if suf:
                suffixes.add(suf.upper())
            families.add((fam or suf).upper())
    families.discard("")
    return suffixes, families


def name_state(name: str, suffixes: set[str]) -> str:
    """工程名形态：prefix_suffix / suffix_pool / bad。"""
    parts = name.split("_")
    if len(parts) < 2:
        return "bad"
    if parts[-1].upper() in suffixes:
        return "prefix_suffix"
    if parts[0].upper() in suffixes:
        return "suffix_pool"
    return "bad"


def scan_project(family: str, project: str, project_path: str,
                 suffixes: set[str], max_files: int) -> None:
    stats["projects"] += 1
    if name_state(project, suffixes) == "bad":
        add("NAME", family, project, project_path, detail="既非 prefix_suffix 也非 suffix_pool")

    if not NAME_OK.match(project):
        bad = []
        if " " in project:
            bad.append("space")
        if CJK.search(project):
            bad.append("cjk")
        bad += sorted({c for c in project if not (c.isalnum() or c in "._-")})
        add("CHARS", family, project, project_path, detail=",".join(bad) or "charset")
    if EXT_TAIL.search(project):
        add("EXTNAME", family, project, project_path)
    if DUP_MARK.search(project):
        add("DUP", family, project, project_path)

    top_dirs, top_files = [], []
    try:
        with os.scandir(project_path) as it:
            for e in it:
                (top_dirs if e.is_dir() else top_files).append(e.name)
    except OSError as exc:
        add("READ_ERR", family, project, project_path, detail=str(exc))
        return

    for f in top_files:
        add("LOOSE", family, project, os.path.join(project_path, f))

    for d in top_dirs:
        if d.lower() in ("image", "roi"):
            continue
        full = os.path.join(project_path, d)
        if RES_DIR.match(d):
            add("RESDIR", family, project, full)
        elif JUNK_DIR.match(d):
            add("JUNKDIR", family, project, full)
        elif EXT_TAIL.search(d):
            add("EXTNAME", family, project, full)
        else:
            add("LAYOUT", family, project, full, detail="工程根多余夹")
        if not NAME_OK.match(d):
            add("CHARS_SUBJECT", family, project, full, detail="受试者/子目录名含空格或非 ASCII（PHI 口径保留，INFO）")

    has_image = any(d.lower() == "image" for d in top_dirs)
    has_roi = any(d.lower() == "roi" for d in top_dirs)
    if not has_image:
        add("LAYOUT_NO_IMAGE", family, project, project_path,
            detail="roi_only" if has_roi else "no_image_no_roi")

    counts = {"files": 0, "offload": {}, "raster": 0, "deep": 0, "deep_sample": ""}
    stack = [(project_path, 2)]
    while stack:
        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except OSError as exc:
            add("READ_ERR", family, project, cur, detail=str(exc))
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                child = depth + 1
                if child > MAX_DEPTH:
                    counts["deep"] += 1
                    if not counts["deep_sample"]:
                        counts["deep_sample"] = e.path
                if child > MAX_WALK:
                    continue
                if RES_DIR.match(e.name):
                    add("RESDIR", family, project, e.path)
                elif JUNK_DIR.match(e.name):
                    add("JUNKDIR", family, project, e.path)
                if EXT_TAIL.search(e.name):
                    add("EXTNAME", family, project, e.path)
                stack.append((e.path, child))
            else:
                counts["files"] += 1
                stats["files"] += 1
                low = e.name.lower()
                ext = ".nii.gz" if low.endswith(".nii.gz") else os.path.splitext(low)[1]
                if ext in OFFLOAD_EXT:
                    counts["offload"][ext] = counts["offload"].get(ext, 0) + 1
                elif ext in RASTER_EXT:
                    counts["raster"] += 1
        if counts["files"] >= max_files:
            stats["truncated"] += 1
            add("TRUNC", family, project, project_path,
                detail=f"files>{max_files}，遍历截断（改小 --project 重跑）")
            break

    if counts["deep"]:
        add("DEEP", family, project, counts["deep_sample"], count=counts["deep"],
            detail=f"深度>{MAX_DEPTH} 的目录数")

    for ext, n in sorted(counts["offload"].items(), key=lambda kv: -kv[1]):
        add("OFFLOAD", family, project, project_path, count=n, detail=ext)
    if counts["raster"]:
        add("RASTER", family, project, project_path, count=counts["raster"], detail="位图，人工分流")


def check_index(root: str) -> None:
    """_index.csv 漂移：登记路径不存在 / 实盘工程未登记。"""
    if not os.path.isfile(INDEX_CSV):
        add("INDEX", "-", "-", INDEX_CSV, detail="台账缺失")
        return
    registered = set()
    with open(INDEX_CSV, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            rel = (row.get("path") or "").strip().replace("/", "\\")
            if not rel:
                continue
            registered.add(rel.lower())
            if not os.path.exists(os.path.join(root, rel)):
                add("INDEX", row.get("family", "-"), rel, os.path.join(root, rel),
                    detail="登记路径不存在")
    for fam in sorted(os.listdir(root)):
        fp = os.path.join(root, fam)
        if not os.path.isdir(fp) or fam in SKIP_TOP or fam.startswith("."):
            continue
        for proj in sorted(os.listdir(fp)):
            pp = os.path.join(fp, proj)
            if not os.path.isdir(pp):
                continue
            if f"{fam}\\{proj}".lower() not in registered:
                add("INDEX", fam, proj, pp, detail="实盘未登记")


def main() -> int:
    ap = argparse.ArgumentParser(description="L 盘结构体检（只读）")
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--diseases", default=DISEASES)
    ap.add_argument("--family", default="", help="逗号分隔家族名")
    ap.add_argument("--all", action="store_true", help="扫全部家族")
    ap.add_argument("--project", default="", help="只扫某工程（配合 --family）")
    ap.add_argument("--max-files", type=int, default=MAX_FILES_DEFAULT)
    ap.add_argument("--check-index", action="store_true")
    ap.add_argument("--out", default="", help="报告目录；缺省系统临时目录 l_audit-<date>（不写 {DEL}）")
    ap.add_argument("--resume", action="store_true", help="跳过报告里已完成的家族")
    args = ap.parse_args()

    suffixes, _families = load_diseases(args.diseases)
    root = args.root
    if not os.path.isdir(root):
        print(f"[FAIL] 根目录不存在：{root}")
        return 2

    out = args.out or os.path.join(os.environ.get("TEMP", "."),
                                   "l_audit-" + datetime.now().strftime("%Y%m%d"))
    os.makedirs(out, exist_ok=True)
    tsv = os.path.join(out, "audit.tsv")

    done = set()
    if args.resume and os.path.isfile(tsv):
        with open(tsv, encoding="utf-8-sig", newline="") as fh:
            done = {r["family"] for r in csv.DictReader(fh) if r.get("family")}

    families = sorted(d for d in os.listdir(root)
                      if os.path.isdir(os.path.join(root, d))
                      and d not in SKIP_TOP and not d.startswith("."))
    targets = [f.strip() for f in args.family.split(",") if f.strip()] if args.family else families
    if not (args.all or args.family or args.check_index):
        print("需指定 --all、--family 或 --check-index；现有家族：")
        print(", ".join(families))
        return 0

    for fam in (targets if (args.all or args.family) else []):
        if fam in done:
            print(f"[skip] {fam}（resume）")
            continue
        fp = os.path.join(root, fam)
        if not os.path.isdir(fp):
            add("INDEX", fam, "-", fp, detail="家族目录不存在")
            continue
        stats["families"] += 1
        for e in os.scandir(fp):
            if e.is_file():
                add("LOOSE", fam, "-", e.path, detail="家族根散文件")
        projects = ([args.project] if args.project else
                    [e.name for e in os.scandir(fp) if e.is_dir()])
        for proj in sorted(projects):
            pp = os.path.join(fp, proj)
            if os.path.isdir(pp):
                scan_project(fam, proj, pp, suffixes, args.max_files)
        print(f"[done] {fam}: projects={len(projects)} files_total={stats['files']}")

    if args.check_index:
        check_index(root)

    mode = "a"
    new_file = not os.path.isfile(tsv)
    enc = "utf-8-sig" if new_file else "utf-8"
    with open(tsv, mode, encoding=enc, newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["check", "family", "project", "path", "count", "detail"],
                           delimiter="\t")
        if new_file:
            w.writeheader()
        w.writerows(rows)

    summary: dict[tuple[str, str], int] = {}
    with open(tsv, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            key = (r["check"], r["detail"] if r["check"] == "OFFLOAD" else "")
            summary[key] = summary.get(key, 0) + int(r["count"] or 0)
    with open(os.path.join(out, "summary.tsv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("check\tdetail\tcount\n")
        for (check, detail), n in sorted(summary.items(), key=lambda kv: (kv[0][0], -kv[1])):
            fh.write(f"{check}\t{detail}\t{n}\n")

    print(f"== RESULT == families={stats['families']} projects={stats['projects']} "
          f"files={stats['files']} truncated={stats['truncated']} issues={len(rows)} "
          f"fails={stats['fails']}")
    print(f"report: {tsv}")
    return 1 if stats["fails"] else 0


if __name__ == "__main__":
    sys.exit(main())
