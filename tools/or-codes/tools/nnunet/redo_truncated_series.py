#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从源盘 DICOM 重转被截断的 series nii.gz（默认 dry-run）。

背景
----
L 盘部分 `*_image.nii.gz` 是截断 gzip（第三方导出的 nii 本身坏了，源 nii 与 L 盘同字节）。
重转必须回到源盘 DICOM：`脂肪肉瘤\\脂肪肉瘤CT公卫\\<study>_EmptyResearchID\\<series>\\*.dcm`。

做法
----
pydicom 读全部 dcm（支持中文 UNC），按 ImagePositionPatient 沿法向排序，
堆栈像素并应用 RescaleSlope/Intercept 得 HU；用**参考 nii 的 affine/header** 保存，
保证与既有 mask 同网格（shape / zoom / sform 完全一致，不需重采样）。

校验
----
与参考 nii 的有效层逐层比对（截断件前若干层是好的），任一不一致即判 MISMATCH。

用法
----
    python redo_truncated_series.py --samples zjl_CT_GW_0006,zjl_CT_GW_0025
    python redo_truncated_series.py --samples-file list.txt
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import shutil
import sys

import numpy as np
import nibabel as nib
import pydicom

DCM_SUB = os.path.join("脂肪肉瘤", "脂肪肉瘤CT公卫")
NIFTI_SUB = "脂肪肉瘤公卫NIFTI"


def wp(path: str) -> str:
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC" + path[1:]
    return "\\\\?\\" + path if os.name == "nt" else path


def load_map(map_tsv: str) -> dict:
    out = {}
    with open(map_tsv, "r", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            out[(r["sample_id"], r["seq"])] = r
    return out


def read_series(dcm_dir: str) -> tuple:
    """读一个 series（容错：跳过坏 dcm），返回 (volume[H,W,Z] int16, info)。"""
    files = sorted(f for f in os.listdir(dcm_dir) if f.lower().endswith(".dcm"))
    if not files:
        raise RuntimeError("no_dcm")
    metas = []
    skipped = 0
    for f in files:
        try:
            ds = pydicom.dcmread(os.path.join(dcm_dir, f), stop_before_pixels=True, force=True)
            metas.append((f, ds))
        except Exception:
            skipped += 1
    if not metas:
        raise RuntimeError("no_readable_dcm")
    d0 = metas[0][1]
    iop = np.array(d0.ImageOrientationPatient, dtype=float)
    nrm = np.cross(iop[:3], iop[3:])
    pos = np.array([float(np.dot(np.array(d.ImagePositionPatient, dtype=float), nrm)) for _f, d in metas])
    order = np.argsort(pos)[::-1]
    vols = []
    kept = []
    for i in order:
        f, _ = metas[i]
        try:
            ds = pydicom.dcmread(os.path.join(dcm_dir, f), force=True)
            arr = ds.pixel_array.astype(np.float32)
        except Exception:
            skipped += 1
            continue
        slope = float(getattr(ds, "RescaleSlope", 1) or 1)
        inter = float(getattr(ds, "RescaleIntercept", 0) or 0)
        vols.append(arr * slope + inter)
        kept.append(float(pos[i]))
    if not vols:
        raise RuntimeError("no_pixels")
    vol = np.rint(np.stack(vols, axis=-1)).astype(np.int16)
    info = {
        "n_slices": len(vols),
        "n_files": len(files),
        "skipped": skipped,
        "rows": int(d0.Rows), "cols": int(d0.Columns),
        "pixsp": [float(x) for x in d0.PixelSpacing],
        "thick": float(getattr(d0, "SliceThickness", 0) or 0),
        "z_from": round(float(pos.min()), 2), "z_to": round(float(pos.max()), 2),
        "series_no": str(getattr(d0, "SeriesNumber", "")),
        "desc": str(getattr(d0, "SeriesDescription", ""))[:40],
    }
    return vol, np.array(kept, dtype=float), info

def load_ref_slices(nii_path: str) -> np.ndarray:
    """读参考 nii（容忍截断；读不出返回空数组）。"""
    try:
        return np.asanyarray(nib.load(wp(nii_path)).dataobj)
    except Exception:
        return np.array([])


def main() -> int:
    ap = argparse.ArgumentParser(description="从 DICOM 重转截断 nii.gz")
    ap.add_argument("--map-tsv", default=r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ingest-map.tsv")
    ap.add_argument("--samples", default="", help="逗号分隔 sample_id")
    ap.add_argument("--samples-file", default="", help="每行一个 sample_id")
    ap.add_argument("--seq", default="CT_plain")
    ap.add_argument("--staging", default=r"E:\_models\nnUNet\zjl_RDL\_staging\redo_nii")
    ap.add_argument("--ledger", default=r"{lab_root}\drafting\zjl_RDL\ref\code-gates\REDO-NII-20261126.tsv")
    ap.add_argument("--execute", action="store_true", help="写回 L 盘（先备份旧件）")
    ap.add_argument("--backup-root", default=r"L:\_Del\_by-function\ct-gzip-redo-20261126")
    args = ap.parse_args()

    m = load_map(args.map_tsv)
    if args.samples_file:
        with open(args.samples_file, "r", encoding="utf-8-sig") as fh:
            ids = [x.strip() for x in fh if x.strip()]
    elif args.samples:
        ids = [x.strip() for x in args.samples.split(",") if x.strip()]
    else:
        print("[fail] give --samples or --samples-file")
        return 2

    os.makedirs(args.staging, exist_ok=True)
    rows = []
    for sid in ids:
        r = m.get((sid, args.seq))
        rec = {"sample_id": sid, "seq": args.seq, "n_dcm": 0, "shape": "", "dz": "",
               "valid": 0, "n_unique": 0, "match": 0, "status": "", "note": ""}
        if not r:
            rec.update(status="NO_MAP")
            rows.append(rec)
            continue
        dcm_dir = os.path.dirname(r["img_src"]).replace(NIFTI_SUB, DCM_SUB)
        if not os.path.isdir(dcm_dir):
            rec.update(status="NO_DCM_DIR", note=dcm_dir[-60:])
            rows.append(rec)
            continue
        try:
            vol, zpos, info = read_series(dcm_dir)
        except Exception as exc:  # noqa: BLE001
            rec.update(status="READ_FAIL", note=str(exc)[:60])
            rows.append(rec)
            continue
        rec["n_dcm"] = info["n_slices"]
        rec["dz"] = info["thick"]

        ref = nib.load(wp(r["img_dst"]))
        nref = int(ref.shape[2])
        zref = ref.affine[2, 2] * np.arange(nref) + ref.affine[2, 3]
        pick = np.array([int(np.argmin(np.abs(zpos - z))) for z in zref])
        vol = vol[:, :, pick]
        rec["shape"] = "%dx%dx%d" % vol.shape
        rec["n_unique"] = int(len(np.unique(pick)))

        old = load_ref_slices(r["img_dst"])
        n_valid = n_match = 0
        if old.ndim == 3 and old.shape == vol.shape:
            nz = np.where((old != 0).any(axis=(0, 1)))[0]
            n_valid = int(nz.max() + 1) if len(nz) else 0
            n_match = sum(1 for z in range(n_valid) if np.array_equal(old[:, :, z], vol[:, :, z]))
        rec["valid"], rec["match"] = n_valid, n_match
        if n_valid == 0:
            rec["status"] = "NO_REF"
        elif n_match == n_valid:
            rec["status"] = "OK"
        else:
            rec["status"] = "MISMATCH_%d_%d" % (n_match, n_valid)

        out = os.path.join(args.staging, sid + "_" + args.seq + ".nii.gz")
        if rec["status"] in ("OK", "NO_REF"):
            img = nib.Nifti1Image(vol, ref.affine, ref.header)
            img.set_data_dtype(np.int16)
            nib.save(img, wp(out))
            rec["note"] = out
            if args.execute:
                try:
                    dst = r["img_dst"]
                    rel = os.path.relpath(dst, "L:" + os.sep)
                    bk = os.path.join(args.backup_root, rel)
                    os.makedirs(os.path.dirname(bk), exist_ok=True)
                    if os.path.exists(dst):
                        shutil.copy2(wp(dst), wp(bk))
                    shutil.copy2(wp(out), wp(dst))
                    rec["status"] = rec["status"] + "+APPLIED"
                    rec["note"] = "applied"
                except Exception as exc:
                    rec["note"] = "apply_fail:" + str(exc)[:50]
        rows.append(rec)
        print("%-16s dcm=%3d shape=%s dz=%s valid=%3d match=%3d %s" % (
            sid, info["n_slices"], rec["shape"], rec["dz"], n_valid, n_match, rec["status"]))

    with open(args.ledger, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    ok = sum(1 for r in rows if r["status"] in ("OK", "NO_REF"))
    print("\n[summary] rows %d convertible %d execute=%s" % (len(rows), ok, args.execute))
    print("[ledger] %s" % args.ledger)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())



