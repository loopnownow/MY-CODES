# -*- coding: utf-8 -*-
r"""l_layout_map.py — 给 L 盘工程出 `image\` / `roi\` 分层映射建议（只读）。

判定（只看名称与文件类型，不读影像内容）：
  roi    目录名命中 roi/mask/seg/label 类，或文件为 .nrrd/.nii 且同目录另有同名影像
  image  目录名命中 img/image/org/nii/dcm/dicom/raw/ima
  subject 名字像受试者（含数字/下划线，且不是上面的关键词）
  keep    IDs / 清单 / 说明 等元数据目录
  unknown 判不出（需人工）

输出：
  layout-map.tsv   每个工程每个顶层条目一行：project,item,kind,proposal,confidence,reason
  layout-map.md    按 confidence 分组，便于人工过目

用法：
  python l_layout_map.py --out L:\_Del\_by-function\naming-20261121
  python l_layout_map.py --family CC,LC --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

ROOT = os.environ.get("ROOT", "")
SKIP_TOP = {"_Del", "System Volume Information", "$RECYCLE.BIN", "DPABI", "INCOMING"}

ROI_DIR = re.compile(r"(?i)^(roi|rois|mask|masks|seg|segs|label|labels|roi_.*|.*_roi|.*-roi|.*roi)$")
IMG_DIR = re.compile(r"(?i)^(img|imgs|image|images|org|orig|nii|niis|dcm|dicom|raw|raws|ima|src|source|flair"
                     r"|.*[-_](img|imgs|image|images|org|orig|nii|niis|dcm|dicom|raw|ima)|.*flair.*)$")
KEEP_DIR = re.compile(r"(?i)^(ids|id|info|meta|metadata|list|query|doc|docs|log|logs|script|scripts|code"
                      r"|utili|util|utility)$")
IMAGE_EXT = {".nii", ".nrrd", ".dcm", ".ima", ".mitk", ".nii.gz"}
ROI_EXT = {".nrrd", ".nii", ".nii.gz", ".mitk"}


def classify_dir(name: str) -> tuple[str, str, str]:
    if ROI_DIR.match(name):
        return "roi", "high", "目录名命中 ROI 关键词"
    if IMG_DIR.match(name):
        return "image", "high", "目录名命中影像关键词"
    if KEEP_DIR.match(name):
        return "keep", "high", "元数据/清单目录，建议留在工程根"
    if re.search(r"\d", name) or "_" in name:
        return "subject", "medium", "疑似受试者目录：应挂到 image\\ 下"
    return "unknown", "low", "名称无关键词、无数字/下划线"


def classify_file(name: str) -> tuple[str, str, str]:
    low = name.lower()
    if low.endswith(".nii.gz"):
        return "image", "medium", "影像文件：默认归 image"
    ext = os.path.splitext(low)[1]
    if ext in {".dcm", ".ima", ".mitk"}:
        return "image", "high", "DICOM/mitk 属原始影像"
    if ext == ".nii":
        return "image", "medium", "nii 默认归 image（ROI 也是 nii，需人工确认）"
    if ext == ".nrrd":
        return "unknown", "low", "nrrd 可能是影像也可能是 ROI"
    if ext in {".txt", ".json", ".yaml", ".yml", ".csv", ".xlsx", ".md"}:
        return "keep", "medium", "伴生/表格类，随影像留在 image"
    return "unknown", "low", "未识别类型"


def scan_project(family: str, project: str) -> list[dict]:
    base = os.path.join(ROOT, family, project)
    rows = []
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError as exc:
        return [{"project": f"{family}\\{project}", "item": "<读取失败>", "kind": "error",
                 "proposal": "", "confidence": "low", "reason": str(exc)}]
    for e in entries:
        if e.is_dir(follow_symlinks=False):
            kind, conf, why = classify_dir(e.name)
        else:
            kind, conf, why = classify_file(e.name)
        proposal = {"roi": "roi\\", "image": "image\\", "subject": "image\\<subject>",
                    "keep": "留在工程根"}.get(kind, "待定")
        rows.append({"project": f"{family}\\{project}", "item": e.name,
                     "kind": kind, "proposal": proposal, "confidence": conf, "reason": why})
    return rows


def write_out(rows: list[dict], out_dir: str) -> tuple[str, str]:
    tsv = os.path.join(out_dir, "layout-map.tsv")
    md = os.path.join(out_dir, "layout-map.md")
    with open(tsv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["project", "item", "kind", "proposal",
                                           "confidence", "reason"], delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    by_conf: dict[str, list[dict]] = {}
    for r in rows:
        by_conf.setdefault(r["confidence"], []).append(r)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("# L 盘 image/roi 分层映射建议\n\n")
        fh.write(f"共 {len(rows)} 条（工程顶层条目）。high 可直接执行；medium 需抽查；low 需人工定。\n\n")
        for conf in ("high", "medium", "low"):
            items = by_conf.get(conf, [])
            if not items:
                continue
            fh.write(f"## {conf}（{len(items)}）\n\n| 工程 | 条目 | 判为 | 建议 | 依据 |\n|---|---|---|---|---|\n")
            for r in items:
                fh.write(f"| `{r['project']}` | `{r['item']}` | {r['kind']} | {r['proposal']} | {r['reason']} |\n")
            fh.write("\n")
    return tsv, md


def main() -> int:
    ap = argparse.ArgumentParser(description="L 盘 image/roi 分层建议（只读）")
    ap.add_argument("--family", default="", help="逗号分隔家族；缺省扫全部")
    ap.add_argument("--project", default="", help="只扫某工程（配合 --family）")
    ap.add_argument("--out", default=r"L:\_Del\_by-function\naming-20261121")
    args = ap.parse_args()

    fams = ([f.strip() for f in args.family.split(",") if f.strip()] if args.family else
            sorted(d for d in os.listdir(ROOT)
                   if os.path.isdir(os.path.join(ROOT, d)) and d not in SKIP_TOP
                   and not d.startswith((".", "_"))))
    rows = []
    for fam in fams:
        fp = os.path.join(ROOT, fam)
        if not os.path.isdir(fp):
            continue
        projects = [args.project] if args.project else [
            e.name for e in os.scandir(fp) if e.is_dir()]
        for proj in sorted(projects):
            rows += scan_project(fam, proj)
    os.makedirs(args.out, exist_ok=True)
    tsv, md = write_out(rows, args.out)
    import collections
    print(f"rows={len(rows)} by_kind={dict(collections.Counter(r['kind'] for r in rows))}")
    print(f"by_conf={dict(collections.Counter(r['confidence'] for r in rows))}")
    print(f"tsv: {tsv}")
    print(f"md : {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
