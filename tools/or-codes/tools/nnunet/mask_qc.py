#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""RDL 源盘 mask 质量分档（只读，输出台账 TSV）。

用途
----
本队列的 ROI 多数只勾了"最大层面附近几层"。本脚本逐 mask 计算 z 方向剖面，
按首末层/峰值比、内部断层、碎片层数分三档：

  A  完整：首末层都 <= --trunc-ratio*峰值，碎片层少
  B  截断：首末层有一端或两端 > --trunc-ratio*峰值（需模型补全）
  C  低质：空 mask / 碎片层过多（需人工重勾）

配对来源
--------
`<img-root>\\<sample>\\_series.tsv` 的 `src_dir` 列是源 series 目录的 UNC 绝对路径，
mask 就在该目录内（`<SEQ>_<n>_mask.nii.gz`）。一个目录恰一个 mask 才算配对成功。

用法
----
    python mask_qc.py --group zjl_MRI_GW --out MASK-QC.tsv
    python mask_qc.py --group all --out MASK-QC.tsv --workers 6
    python mask_qc.py --group all --out MASK-QC.tsv --resume
    python mask_qc.py --group zjl_CT_ZS --max-seconds 25 --out X.tsv

不给 --out 时只把汇总打到屏幕，不写任何文件。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import nibabel as nib

GROUPS = ("zjl_CT_GW", "zjl_CT_ZS", "zjl_MRI_GW", "zjl_MRI_ZS", "Lroi_CT_GW")

FIELDS = [
    "group", "sample_id", "seq", "mask_file", "src_dir",
    "n_slices", "n_nz", "coverage", "peak_area", "peak_slice",
    "first_ratio", "last_ratio", "n_gap", "n_lowfrag",
    "verdict", "note",
]


def wp(path: str) -> str:
    """长路径前缀（UNC 走 \\\\?\\UNC\\）。"""
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC" + path[1:]
    return "\\\\?\\" + path if os.name == "nt" else path


def profile(mask_path: str, lowfrag_ratio: float, trunc_ratio: float, lowfrag_abs: int) -> dict:
    """算一个 mask 的 z 方向剖面并给 verdict。"""
    a = np.asanyarray(nib.load(wp(mask_path)).dataobj)
    if a.ndim == 4:
        a = (a > 0).any(axis=3)          # (x, y, z, t/b) 先按层求并集
    else:
        a = a > 0
    if a.ndim < 3:
        a = a[:, :, None]
    n_slices = int(a.shape[2])
    ar = a.sum(axis=(0, 1)).astype(np.int64)
    idx = np.where(ar > 0)[0]
    out = {
        "n_slices": n_slices, "n_nz": 0, "coverage": 0.0,
        "peak_area": 0, "peak_slice": -1,
        "first_ratio": 0.0, "last_ratio": 0.0, "n_gap": 0, "n_lowfrag": 0,
        "verdict": "C", "note": "empty",
    }
    if len(idx) == 0:
        return out
    peak = int(ar[idx].max())
    peak_slice = int(idx[int(np.argmax(ar[idx]))])
    nz = ar[idx]
    first_ratio = float(nz[0]) / peak
    last_ratio = float(nz[-1]) / peak
    n_gap = int((np.diff(idx) > 1).sum())
    n_lowfrag = int((nz < lowfrag_ratio * peak).sum())
    out.update({
        "n_nz": int(len(idx)),
        "coverage": round(len(idx) / n_slices, 4) if n_slices else 0.0,
        "peak_area": peak,
        "peak_slice": peak_slice,
        "first_ratio": round(first_ratio, 3),
        "last_ratio": round(last_ratio, 3),
        "n_gap": n_gap,
        "n_lowfrag": n_lowfrag,
    })
    notes = []
    trunc = first_ratio > trunc_ratio or last_ratio > trunc_ratio
    if trunc:
        side = "both" if (first_ratio > trunc_ratio and last_ratio > trunc_ratio) else (
            "head" if first_ratio > trunc_ratio else "tail")
        notes.append("truncated(%s)" % side)
    if n_gap:
        notes.append("gap%d" % n_gap)
    if n_lowfrag >= lowfrag_abs and n_lowfrag > 0.3 * len(idx):
        notes.append("fragment%d" % n_lowfrag)
    if any(x.startswith("fragment") for x in notes):
        out["verdict"] = "C"
    elif trunc:
        out["verdict"] = "B"
    else:
        out["verdict"] = "A"
    out["note"] = ";".join(notes) if notes else "ok"
    return out


def load_done(out_path: str) -> set:
    if not out_path or not os.path.exists(out_path):
        return set()
    done = set()
    with open(out_path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            done.add((row.get("sample_id", ""), row.get("seq", "")))
    return done




def main() -> int:
    ap = argparse.ArgumentParser(description="RDL 源盘 mask 质量分档（只读）")
    ap.add_argument("--img-root", default=r"L:\RDL\zjl_RDL\img",
                    help="L 叶 img 根（含 <sample>\\_series.tsv）")
    ap.add_argument("--group", default="all", help="%s / all" % "|".join(GROUPS))
    ap.add_argument("--out", default="", help="台账 TSV；不给则只打印汇总")
    ap.add_argument("--l-roi", default="", help="L 盘 roi 根（扫 *_label.nii.gz，用于无 _series.tsv 的批次）")
    ap.add_argument("--resume", action="store_true", help="跳过台账里已有的 (sample_id, seq)")
    ap.add_argument("--workers", type=int, default=6, help="并发读线程数")
    ap.add_argument("--max-seconds", type=float, default=0.0, help="到时停止（>0 用于分批）")
    ap.add_argument("--lowfrag-ratio", type=float, default=0.2, help="碎片层判定：低于峰值该比例")
    ap.add_argument("--lowfrag-abs", type=int, default=5, help="碎片层绝对数下限")
    ap.add_argument("--trunc-ratio", type=float, default=0.2, help="截断判定：首/末层高于峰值该比例")
    args = ap.parse_args()

    groups = GROUPS if args.group == "all" else (args.group,)
    for g in groups:
        if g not in GROUPS:
            print("[fail] unknown group %s" % g)
            return 2

    done = load_done(args.out) if args.resume else set()
    if done:
        print("[resume] already done: %d" % len(done))

    t0 = time.time()
    rows = []
    stats = {g: {"A": 0, "B": 0, "C": 0, "pairs": 0, "nomask": 0, "multi": 0} for g in groups}
    stop = False

    writer = None
    fh_out = None
    if args.out:
        exists = os.path.exists(args.out) and args.resume
        fh_out = open(args.out, "a" if exists else "w", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(fh_out, fieldnames=FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
            fh_out.flush()

    def handle(meta, mask_path):
        try:
            prof = profile(mask_path, args.lowfrag_ratio, args.trunc_ratio, args.lowfrag_abs)
        except Exception as exc:  # noqa: BLE001
            return dict(meta, verdict="C", note="read_err:%s" % str(exc)[:60],
                        n_slices=0, n_nz=0, coverage=0.0, peak_area=0, peak_slice=-1,
                        first_ratio=0.0, last_ratio=0.0, n_gap=0, n_lowfrag=0)
        return dict(meta, **prof)

    import glob
    for g in groups:
        tasks = []
        if g == "Lroi_CT_GW":
            if not args.l_roi:
                continue
            for lab in sorted(glob.glob(os.path.join(args.l_roi, "*", "*_label.nii.gz"))):
                sample = os.path.basename(os.path.dirname(lab))
                seq = os.path.basename(lab)[:-len("_label.nii.gz")]
                if (sample, seq) in done:
                    continue
                meta = {"group": g, "sample_id": sample, "seq": seq,
                        "mask_file": os.path.basename(lab), "src_dir": os.path.dirname(lab)}
                tasks.append((meta, lab))
        for case_dir in (sorted(glob.glob(os.path.join(args.img_root, g + "_*")))
                         if g != "Lroi_CT_GW" else []):
            sample = os.path.basename(case_dir)
            ts = os.path.join(case_dir, "_series.tsv")
            if not os.path.exists(ts):
                continue
            with open(ts, "r", encoding="utf-8-sig", newline="") as fh:
                srows = list(csv.DictReader(fh, delimiter="\t"))
            for r in srows:
                seq = r.get("seq", "")
                if (sample, seq) in done:
                    continue
                src = r.get("src_dir", "")
                if not os.path.isdir(wp(src)):
                    continue
                try:
                    names = os.listdir(wp(src))
                except OSError:
                    continue
                masks = [x for x in names if "mask" in x.lower() and x.lower().endswith(".nii.gz")]
                meta = {"group": g, "sample_id": sample, "seq": seq, "mask_file": "", "src_dir": src}
                if not masks:
                    stats[g]["nomask"] += 1
                    continue
                if len(masks) > 1:
                    stats[g]["multi"] += 1
                meta["mask_file"] = masks[0]
                tasks.append((meta, os.path.join(src, masks[0])))

        print("[scan] %-12s pairs=%d" % (g, len(tasks)))
        chunk = max(8, args.workers * 4)
        for i in range(0, len(tasks), chunk):
            batch = tasks[i:i + chunk]
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
                for row in ex.map(lambda t: handle(*t), batch):
                    rows.append(row)
                    stats[g]["pairs"] += 1
                    stats[g][row["verdict"]] = stats[g].get(row["verdict"], 0) + 1
                    if writer:
                        writer.writerow(row)
            if fh_out:
                fh_out.flush()
            if args.max_seconds and (time.time() - t0) > args.max_seconds:
                stop = True
                break
        if stop:
            print("[stop] max-seconds reached; %d rows this run (use --resume)" % len(rows))
            break

    if fh_out:
        fh_out.close()

    print("\n[summary] elapsed %.1fs rows %d" % (time.time() - t0, len(rows)))
    print("%-12s %6s %5s %5s %5s %8s %6s" % ("group", "pairs", "A", "B", "C", "nomask", "multi"))
    for g in groups:
        s = stats[g]
        print("%-12s %6d %5d %5d %5d %8d %6d" % (g, s["pairs"], s["A"], s["B"], s["C"],
                                                 s["nomask"], s["multi"]))
    print("[out] %s" % (args.out if args.out else "(none, dry)"))

    bad = [r for r in rows if r["verdict"] != "A"][:8]
    if bad:
        print("\n[examples]")
        for r in bad:
            print("  %-10s %-10s %-12s %s" % (r["sample_id"][-10:], r["seq"], r["verdict"], r["note"]))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

