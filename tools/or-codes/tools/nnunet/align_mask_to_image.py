#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mask 与 image 的几何核对 / 最近邻重采样（默认 dry-run）。

为什么需要
----------
源盘 mask 由 DICOM 序列直接生成，L 盘 nii 由另一条转换路径生成，两者层数可能差 1-4 层
（实测：CT_ZS 45% 不一致、MRI_ZS 17%、MRI_GW 0%）。nnUNet 要求 label 与 image 同网格，
不一致必须先最近邻重采样。

关键实现
--------
不用 SimpleITK（打不开中文路径）。用 nibabel + nibabel.processing.resample_from_to(order=0)，
全程 Python 文件 API，中文 UNC 路径可读。

用法
----
    python align_mask_to_image.py --group zjl_CT_ZS --out ALIGN.tsv
    python align_mask_to_image.py --group all --out ALIGN.tsv --resume
    python align_mask_to_image.py --group zjl_CT_ZS --out ALIGN.tsv --execute --dst-root D:\\tmp\\aligned

默认只核对并写台账，不产生任何影像文件。
"""

from __future__ import annotations

import argparse
import csv
import glob
import gzip
import os
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import nibabel as nib
from nibabel.processing import resample_from_to

GROUPS = ("zjl_CT_GW", "zjl_CT_ZS", "zjl_MRI_GW", "zjl_MRI_ZS", "Lroi_CT_GW")
FIELDS = ["group", "sample_id", "seq", "status", "img_shape", "mask_shape",
          "img_zsp", "mask_zsp", "dz_layers", "action", "img_file", "mask_file"]


def wp(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC" + path[1:]
    return "\\\\?\\" + path if os.name == "nt" else path


def hdr(path: str) -> dict:
    """只解压 nii.gz 前 348 字节取 header，不下载整个文件。"""
    with gzip.open(wp(path), "rb") as f:
        h = f.read(348)
    dim = struct.unpack("<8h", h[40:56])
    pix = struct.unpack("<8f", h[76:108])
    nd = dim[0]
    sform_code = struct.unpack("<h", h[254:256])[0]
    srow = tuple(round(float(x), 2) for x in struct.unpack("<12f", h[280:328])) if sform_code > 0 else None
    return {
        "shape": tuple(int(x) for x in dim[1:1 + nd]),
        "zooms": tuple(round(float(x), 3) for x in pix[1:1 + nd]),
        "srow": srow,
    }


def compare(hi: dict, hm: dict, atol_aff: float = 0.5, atol_zoom: float = 0.02) -> tuple:
    """返回 (status, dz_layers)。"""
    if hi["shape"] != hm["shape"]:
        dz = abs(hi["shape"][2] - hm["shape"][2]) if len(hi["shape"]) > 2 and len(hm["shape"]) > 2 else 0
        return "DIFF", dz
    if not np.allclose(hi["zooms"], hm["zooms"], atol=atol_zoom):
        return "DIFF", 0
    if hi["srow"] and hm["srow"] and not np.allclose(hi["srow"], hm["srow"], atol=atol_aff):
        return "DIFF", 0
    return "OK", 0


def do_resample(img_path: str, mask_path: str, dst_path: str) -> None:
    """最近邻重采样 mask 到 image 网格并落盘（ASCII 目标路径）。"""
    tgt = nib.load(wp(img_path))
    src = nib.load(wp(mask_path))
    res = resample_from_to(src, tgt, order=0)
    data = (np.asanyarray(res.dataobj) > 0.5).astype(np.uint8)
    out = nib.Nifti1Image(data, tgt.affine)
    out.set_data_dtype(np.uint8)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    nib.save(out, wp(dst_path))


def load_done(out_path: str) -> set:
    if not out_path or not os.path.exists(out_path):
        return set()
    done = set()
    with open(out_path, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            done.add((row.get("sample_id", ""), row.get("seq", "")))
    return done



def main() -> int:
    ap = argparse.ArgumentParser(description="mask 与 image 几何核对 / 最近邻重采样")
    ap.add_argument("--img-root", default=r"L:\RDL\zjl_RDL\img", help="L 叶 img 根")
    ap.add_argument("--group", default="all", help="%s / all" % "|".join(GROUPS))
    ap.add_argument("--out", default="", help="台账 TSV（不给则只打印汇总）")
    ap.add_argument("--l-roi", default="", help="L 盘 roi 根（扫 *_label.nii.gz，用于无 _series.tsv 的批次）")
    ap.add_argument("--ref-tsv", default="", help="只处理该 ALIGN 台账里 status==DIFF 的条目（避免重跑全量）")
    ap.add_argument("--dst-root", default="", help="--execute 时重采样输出根（须 ASCII）")
    ap.add_argument("--execute", action="store_true", help="真正写重采样文件（默认只核对）")
    ap.add_argument("--resume", action="store_true", help="跳过台账已有 (sample_id, seq)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-seconds", type=float, default=0.0)
    args = ap.parse_args()

    groups = GROUPS if args.group == "all" else (args.group,)
    for g in groups:
        if g not in GROUPS:
            print("[fail] unknown group %s" % g)
            return 2
    if args.execute and not args.dst_root:
        print("[fail] --execute requires --dst-root")
        return 2

    done = load_done(args.out) if args.resume else set()
    ref_diff = None
    if args.ref_tsv and os.path.exists(args.ref_tsv):
        ref_diff = set()
        with open(args.ref_tsv, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                if row.get("status") == "DIFF":
                    ref_diff.add((row.get("sample_id", ""), row.get("seq", "")))
        print("[ref] DIFF entries: %d" % len(ref_diff))
    t0 = time.time()
    stats = {g: {"OK": 0, "DIFF": 0, "ERR": 0, "fixed": 0, "nomask": 0} for g in groups}
    rows = []

    writer = None
    fh_out = None
    if args.out:
        exists = os.path.exists(args.out) and args.resume
        fh_out = open(args.out, "a" if exists else "w", encoding="utf-8-sig", newline="")
        writer = csv.DictWriter(fh_out, fieldnames=FIELDS, delimiter="\t")
        if not exists:
            writer.writeheader()
            fh_out.flush()

    def handle(g, sample, seq, img_path, mask_path):
        rec = {"group": g, "sample_id": sample, "seq": seq, "status": "ERR",
               "img_shape": "", "mask_shape": "", "img_zsp": "", "mask_zsp": "",
               "dz_layers": 0, "action": "none",
               "img_file": os.path.basename(img_path), "mask_file": os.path.basename(mask_path)}
        try:
            hi = hdr(img_path)
            hm = hdr(mask_path)
        except Exception as exc:  # noqa: BLE001
            rec["action"] = "read_err:%s" % str(exc)[:50]
            return rec
        status, dz = compare(hi, hm)
        rec.update({
            "status": status,
            "img_shape": "x".join(str(x) for x in hi["shape"]),
            "mask_shape": "x".join(str(x) for x in hm["shape"]),
            "img_zsp": hi["zooms"][2] if len(hi["zooms"]) > 2 else "",
            "mask_zsp": hm["zooms"][2] if len(hm["zooms"]) > 2 else "",
            "dz_layers": dz,
        })
        if status == "OK":
            rec["action"] = "skip(same_geometry)"
            return rec
        if not args.execute:
            rec["action"] = "would_resample"
            return rec
        dst = os.path.join(args.dst_root, sample, seq + "_label.nii.gz")
        try:
            do_resample(img_path, mask_path, dst)
            rec["action"] = "resampled"
        except Exception as exc:  # noqa: BLE001
            rec["action"] = "resample_err:%s" % str(exc)[:50]
        return rec

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
                nii = os.path.join(args.img_root, sample, seq + "_image.nii.gz")
                if not os.path.isfile(wp(nii)):
                    stats[g]["nomask"] += 1
                    continue
                tasks.append((g, sample, seq, nii, lab))
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
                nii = os.path.join(case_dir, r.get("nii_file", ""))
                if not os.path.isfile(wp(nii)):
                    continue
                if not os.path.isdir(wp(src)):
                    stats[g]["nomask"] += 1
                    continue
                try:
                    names = os.listdir(wp(src))
                except OSError:
                    stats[g]["nomask"] += 1
                    continue
                masks = [x for x in names if "mask" in x.lower() and x.lower().endswith(".nii.gz")]
                if not masks:
                    stats[g]["nomask"] += 1
                    continue
                tasks.append((g, sample, seq, nii, os.path.join(src, masks[0])))

        print("[scan] %-12s pairs=%d" % (g, len(tasks)))
        chunk = max(8, args.workers * 4)
        stop = False
        for i in range(0, len(tasks), chunk):
            batch = tasks[i:i + chunk]
            if ref_diff is not None:
                batch = [t for t in batch if (t[1], t[2]) in ref_diff]
            if not batch:
                continue
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
                for rec in ex.map(lambda t: handle(*t), batch):
                    rows.append(rec)
                    st = rec["status"]
                    if st == "DIFF" and rec["action"] == "resampled":
                        stats[g]["fixed"] += 1
                    stats[g][st] = stats[g].get(st, 0) + 1
                    if writer:
                        writer.writerow(rec)
            if fh_out:
                fh_out.flush()
            if args.max_seconds and (time.time() - t0) > args.max_seconds:
                stop = True
                break
        if stop:
            print("[stop] max-seconds reached; %d rows (use --resume)" % len(rows))
            break

    if fh_out:
        fh_out.close()

    print("\n[summary] elapsed %.1fs rows %d execute=%s" % (time.time() - t0, len(rows), args.execute))
    print("%-12s %6s %5s %5s %5s %6s" % ("group", "OK", "DIFF", "ERR", "fixed", "nomask"))
    for g in groups:
        s = stats[g]
        print("%-12s %6d %5d %5d %5d %6d" % (g, s["OK"], s["DIFF"], s["ERR"], s["fixed"], s["nomask"]))
    print("[out] %s" % (args.out if args.out else "(none, dry)"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

