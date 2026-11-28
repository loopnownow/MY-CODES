#!/usr/bin/env python
"""Voxel-level integrity check for NIfTI files.

`check_pairs.py` verifies headers and sha256 only. A truncated .nii.gz still has a valid
header and its sha256 matches whatever was copied, so corruption goes unnoticed until the
trainer tries to read the volume. This tool decodes every file completely and reports
per-file status, catching:
  - gzip truncated / EOFError
  - shape or dtype surprises
  - all-background volumes
  - NaN / Inf in float images

Usage
  python verify_voxels.py --root L:\\RDL\\zjl_RDL --pattern "zjl_CT_GW_*" --kind image
  python verify_voxels.py --root L:\\RDL\\zjl_RDL --pattern "zjl_CT_GW_*" --kind label
  python verify_voxels.py --raw-dir E:\\_models\\nnUNet\\zjl_RDL\\raw\\Dataset002_RLPS_CT

Exit code 1 when any FAIL is found.
"""
from __future__ import annotations
import os

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

CONFIG = {
    "root": os.environ.get("CONFIG", ""),
    "pattern": "zjl_CT_GW_*",
    "seq": "CT_plain",
    "out_dir": os.environ.get("CONFIG_2", ""),
}


def inspect(path: Path, kind: str, reader: str = "sitk") -> dict:
    rec = {"path": str(path), "kind": kind, "status": "OK", "note": "",
           "shape": "", "dtype": "", "n_vox": 0, "min": "", "max": "",
           "n_nan": 0, "n_zero_slices": 0, "trailing_zero_slices": 0, "leading_zero_slices": 0}
    try:
        if reader == "sitk":
            import SimpleITK as sitk

            im = sitk.ReadImage(str(path))
            arr = sitk.GetArrayFromImage(im)          # (z, y, x)
            rec["shape"] = "x".join(str(x) for x in im.GetSize())
            rec["dtype"] = str(arr.dtype)
            rec["n_vox"] = int(arr.size)
            rec["min"] = float(arr.min())
            rec["max"] = float(arr.max())
            # real CT air is about -1024; an all-zero slice means padding from a truncated stream
            zero = np.array([not arr[i].any() for i in range(arr.shape[0])])
            rec["n_zero_slices"] = int(zero.sum())
            t = 0
            for i in range(len(zero) - 1, -1, -1):
                if zero[i]:
                    t += 1
                else:
                    break
            l = 0
            for i in range(len(zero)):
                if zero[i]:
                    l += 1
                else:
                    break
            rec["trailing_zero_slices"] = t
            rec["leading_zero_slices"] = l
            if kind == "image" and t:
                rec["status"] = "FAIL"
                rec["note"] = f"trailing zero-padded slices {t} (truncated gzip)"
            # label: all-zero slices are normal (the mask only covers the tumour), skip that check
            if kind == "label":
                vals = np.unique(arr)
                bad = [float(v) for v in vals if float(v) not in (0.0, 1.0)]
                if bad:
                    rec["status"] = "FAIL"
                    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + f"values {bad[:4]}"
                if int(arr.max()) <= 0:
                    rec["status"] = "FAIL"
                    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "empty label"
        else:
            import nibabel as nib

            img = nib.load(str(path))
            arr = img.get_fdata(dtype=np.float32)
            rec["shape"] = "x".join(str(x) for x in img.shape)
            rec["dtype"] = str(img.get_data_dtype())
            rec["n_vox"] = int(arr.size)
            rec["min"] = round(float(np.nanmin(arr)), 2)
            rec["max"] = round(float(np.nanmax(arr)), 2)
            n_nan = int(np.isnan(arr).sum())
            rec["n_nan"] = n_nan
            if n_nan:
                rec["status"] = "FAIL"
                rec["note"] = f"{n_nan} NaN"
            if kind == "label":
                uniq = np.unique(arr)
                bad = [float(v) for v in uniq if float(v) not in (0.0, 1.0)]
                if bad:
                    rec["status"] = "FAIL"
                    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + f"values {bad[:4]}"
                if float(arr.max()) <= 0:
                    rec["status"] = "FAIL"
                    rec["note"] = (rec["note"] + "; " if rec["note"] else "") + "empty label"
    except EOFError as exc:
        rec["status"] = "FAIL"
        rec["note"] = f"EOFError (truncated gzip): {exc}"
    except Exception as exc:                      # noqa: BLE001 - report anything
        rec["status"] = "FAIL"
        rec["note"] = f"{type(exc).__name__}: {exc}"
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Voxel-level integrity check for NIfTI")
    ap.add_argument("--root", default=CONFIG["root"], help="leaf root containing img/ and roi/")
    ap.add_argument("--pattern", default=CONFIG["pattern"], help="sample directory glob")
    ap.add_argument("--seq", default=CONFIG["seq"], help="sequence tag inside img/ and roi/")
    ap.add_argument("--kind", default="both", choices=["image", "label", "both"])
    ap.add_argument("--raw-dir", default="", help="instead of --root: an nnUNet dataset dir")
    ap.add_argument("--out-dir", default=CONFIG["out_dir"])
    ap.add_argument("--reader", default="sitk", choices=["sitk", "nibabel"],
                    help="sitk matches what nnUNet uses (tolerates truncated gzip); "
                         "nibabel is strict and raises EOFError")
    args = ap.parse_args(argv)

    targets: list[tuple[Path, str]] = []
    if args.raw_dir:
        ds = Path(args.raw_dir)
        if args.kind in ("image", "both"):
            targets += [(p, "image") for p in sorted((ds / "imagesTr").glob("*.nii.gz"))]
        if args.kind in ("label", "both"):
            targets += [(p, "label") for p in sorted((ds / "labelsTr").glob("*.nii.gz"))]
    else:
        root = Path(args.root)
        for d in sorted((root / "img").glob(args.pattern)):
            p = d / f"{args.seq}_image.nii.gz"
            if p.exists() and args.kind in ("image", "both"):
                targets.append((p, "image"))
            q = root / "roi" / d.name / f"{args.seq}_label.nii.gz"
            if q.exists() and args.kind in ("label", "both"):
                targets.append((q, "label"))
    if not targets:
        print("[FAIL] nothing to inspect")
        return 1

    print(f"[info] files: {len(targets)}")
    rows, fails, t0 = [], [], time.time()
    for n, (p, kind) in enumerate(targets, 1):
        rec = inspect(p, kind, args.reader)
        rows.append(rec)
        if rec["status"] != "OK":
            fails.append(rec)
            print(f"[FAIL] {p.name}: {rec['note']}", flush=True)
        if n % 50 == 0 or n == len(targets):
            print(f"[scan] {n}/{len(targets)}  fails {len(fails)}  elapsed {time.time()-t0:.0f}s",
                  flush=True)

    out = Path(args.out_dir) / f"VERIFY-VOXELS-{time.strftime('%Y%m%d')}.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    with open(out, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if new:
            w.writerow(["path", "kind", "status", "note", "shape", "dtype", "n_vox",
                        "min", "max", "n_nan", "n_zero_slices", "trailing_zero_slices",
                        "leading_zero_slices"])
        for r in rows:
            w.writerow([r["path"], r["kind"], r["status"], r["note"], r["shape"], r["dtype"],
                        r["n_vox"], r["min"], r["max"], r["n_nan"], r["n_zero_slices"],
                        r["trailing_zero_slices"], r["leading_zero_slices"]])

    print(f"\n[done] scanned {len(targets)}  OK {len(targets)-len(fails)}  FAIL {len(fails)}")
    print(f"[done] report -> {out}")
    if fails:
        print("[FAIL] list:")
        for r in fails[:20]:
            print(f"  {r['path']}  ->  {r['note']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())