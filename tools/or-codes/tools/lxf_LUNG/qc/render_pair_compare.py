# -*- coding: utf-8 -*-
"""Render a pre/post CT pair with both ROI contours, for error adjudication.

Why
    A rejected mask can only be explained by looking at the pair: the lesion that
    the model latched onto, and what the same patient looked like at the other time
    point. The per-exam review JPG (render_roi_review_jpg.py) shows one exam only.

Layout (2 rows x 3 cols)
    pre row   full slice | zoom on ROI | kept mask
    post row  full slice | zoom on ROI | kept mask
    red contour  = raw nnU-Net prediction (predTs)
    green contour= kept components after index-lesion pruning (predTs_primary)

Input
    5mm_nii/<pid>/{pre,post}.nii.gz        CT
    predTs/<pid>_{pre,post}.nii.gz         raw prediction
    predTs_primary/<pid>_{pre,post}.nii.gz kept mask

Usage
    python render_pair_compare.py --cases 1001_pre,1002_post --out-dir D:\\out
    python render_pair_compare.py --cases-file err_cases.txt --out-dir D:\\out --dpi 120
    python render_pair_compare.py --cases ... --hash-title     # title shows hash only
"""
from __future__ import annotations

import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import SimpleITK as sitk

CONFIG = {
    "CT_DIR": os.environ.get("CONFIG", ""),
    "RAW_DIR": os.environ.get("CONFIG_2", ""),
    "KEEP_DIR": os.environ.get("CONFIG_3", ""),
    "WINDOW": "lung",
    "DPI": 110,
    "PAD_MM": 20.0,
    "JPG_QUALITY": 88,
}

WINDOWS = {"lung": (-600.0, 1500.0), "mediastinal": (40.0, 400.0)}


def apply_window(arr: np.ndarray, level: float, width: float) -> np.ndarray:
    lo, hi = level - width / 2.0, level + width / 2.0
    out = np.clip(arr, lo, hi)
    return ((out - lo) / max(1e-6, hi - lo) * 255.0).astype(np.uint8)


def read_img(path: str):
    if not os.path.exists(path):
        return None, None
    img = sitk.ReadImage(path)
    return sitk.GetArrayFromImage(img).astype(np.float32), img


def best_slice(mask: np.ndarray):
    """Slice index with the largest in-plane mask area; middle slice if empty."""
    if mask is None or not mask.any():
        n = 0 if mask is None else mask.shape[0]
        return (n // 2) if n else 0, True
    area = mask.reshape(mask.shape[0], -1).sum(axis=1)
    return int(np.argmax(area)), False


def crop_box(mask2d: np.ndarray, pad: int):
    ys, xs = np.where(mask2d)
    if len(ys) == 0:
        return None
    h, w = mask2d.shape
    return (max(0, ys.min() - pad), min(h - 1, ys.max() + pad),
            max(0, xs.min() - pad), min(w - 1, xs.max() + pad))


def render_patient(pid: str, out_path: str, level: float, width: float, pad_mm: float,
                   dpi: int, quality: int, hash_title: bool) -> dict:
    """Render one patient (pre row + post row). Returns a summary row."""
    fig, axes = plt.subplots(2, 3, figsize=(16.5, 10.4))
    info = {}
    for r, tag in enumerate(("pre", "post")):
        case = pid + "_" + tag
        ct, ct_img = read_img(os.path.join(CONFIG["CT_DIR"], pid, tag + ".nii.gz"))
        raw, _ = read_img(os.path.join(CONFIG["RAW_DIR"], case + ".nii.gz"))
        keep, _ = read_img(os.path.join(CONFIG["KEEP_DIR"], case + ".nii.gz"))
        raw = (raw > 0) if raw is not None else None
        keep = (keep > 0) if keep is not None else None
        for c in range(3):
            axes[r][c].axis("off")
        if ct is None:
            axes[r][0].set_title("%s  CT missing" % case, fontsize=9)
            info[tag] = (0.0, 0.0, True)
            continue

        sp = ct_img.GetSpacing()
        vox_mm3 = float(sp[0]) * float(sp[1]) * float(sp[2])
        idx, empty = best_slice(keep if keep is not None else raw)
        idx = max(0, min(idx, ct.shape[0] - 1))
        img2d = apply_window(ct[idx], level, width)
        raw2d = raw[idx] if (raw is not None and idx < raw.shape[0]) else np.zeros(ct[idx].shape, bool)
        keep2d = keep[idx] if (keep is not None and idx < keep.shape[0]) else np.zeros(ct[idx].shape, bool)
        vol_raw = float(raw.sum()) * vox_mm3 if raw is not None else 0.0
        vol_keep = float(keep.sum()) * vox_mm3 if keep is not None else 0.0

        axes[r][0].imshow(img2d, cmap="gray", interpolation="nearest")
        if raw2d.any():
            axes[r][0].contour(raw2d.astype(float), levels=[0.5], colors=["red"], linewidths=1.0)
        if keep2d.any():
            axes[r][0].contour(keep2d.astype(float), levels=[0.5], colors=["lime"], linewidths=1.2)
        axes[r][0].set_title("%s  slice %d/%d  raw %.0f  kept %.0f mm3%s"
                             % (case, idx + 1, ct.shape[0], vol_raw, vol_keep,
                                "  EMPTY" if empty else ""), fontsize=9)

        pad = max(2, int(round(pad_mm / max(1e-6, float(sp[0])))))
        box = crop_box(keep2d if keep2d.any() else raw2d, pad)
        if box is None:
            axes[r][1].imshow(img2d, cmap="gray", interpolation="nearest")
            axes[r][1].set_title("zoom: no mask", fontsize=9)
            axes[r][2].imshow(np.zeros_like(img2d), cmap="gray", vmin=0, vmax=1)
            axes[r][2].set_title("kept mask: empty", fontsize=9)
        else:
            y0, y1, x0, x1 = box
            axes[r][1].imshow(img2d[y0:y1 + 1, x0:x1 + 1], cmap="gray", interpolation="nearest")
            if raw2d.any():
                axes[r][1].contour(raw2d[y0:y1 + 1, x0:x1 + 1].astype(float), levels=[0.5],
                                   colors=["red"], linewidths=1.0)
            if keep2d.any():
                axes[r][1].contour(keep2d[y0:y1 + 1, x0:x1 + 1].astype(float), levels=[0.5],
                                   colors=["lime"], linewidths=1.3)
            axes[r][1].set_title("zoom (%.0f mm pad)" % pad_mm, fontsize=9)
            axes[r][2].imshow(keep2d[y0:y1 + 1, x0:x1 + 1].astype(np.uint8),
                              cmap="gray", vmin=0, vmax=1, interpolation="nearest")
            axes[r][2].set_title("kept mask", fontsize=9)
        for c in range(3):
            axes[r][c].axis("off")
        info[tag] = (vol_raw, vol_keep, empty)

    pre_v = info.get("pre", (0.0, 0.0, True))
    post_v = info.get("post", (0.0, 0.0, True))
    ratio = ("%.3f" % (post_v[1] / pre_v[1])) if pre_v[1] > 0 else "NA"
    title = pid if not hash_title else "case " + pid[-4:]
    fig.suptitle("%s  |  pre kept %.0f  post kept %.0f mm3  ratio %s  "
                 "|  red = raw pred, green = kept (prune)" % (title, pre_v[1], post_v[1], ratio),
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_path, dpi=dpi, format="jpg", pil_kwargs={"quality": quality})
    plt.close(fig)
    return {"patient": pid, "pre_vol": round(pre_v[1], 1), "post_vol": round(post_v[1], 1),
            "ratio": ratio, "jpg": out_path}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="render pre/post pair with raw + kept ROI contours")
    ap.add_argument("--cases", default="", help="comma separated case ids, e.g. 1001_pre,1002_post")
    ap.add_argument("--cases-file", default="", help="one case id per line")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--window", default=CONFIG["WINDOW"], choices=sorted(WINDOWS))
    ap.add_argument("--dpi", type=int, default=CONFIG["DPI"])
    ap.add_argument("--pad-mm", type=float, default=CONFIG["PAD_MM"])
    ap.add_argument("--hash-title", action="store_true", help="title without the full MRN")
    args = ap.parse_args(argv)

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    if args.cases_file:
        with open(args.cases_file, encoding="utf-8-sig") as fh:
            cases += [ln.strip() for ln in fh if ln.strip()]
    pids = []
    for c in cases:
        pid = c.rsplit("_", 1)[0] if c.endswith(("_pre", "_post")) else c
        if pid not in pids:
            pids.append(pid)
    if not pids:
        print("[stop] no case given")
        return 1

    os.makedirs(args.out_dir, exist_ok=True)
    level, width = WINDOWS[args.window]
    print("[render] %d patients, window=%s, dpi=%d -> %s"
          % (len(pids), args.window, args.dpi, args.out_dir))
    for i, pid in enumerate(pids, 1):
        out = os.path.join(args.out_dir, pid + "_pair.jpg")
        row = render_patient(pid, out, level, width, args.pad_mm, args.dpi,
                             CONFIG["JPG_QUALITY"], args.hash_title)
        print("  [%2d/%2d] %s  pre %.0f  post %.0f  ratio %s"
              % (i, len(pids), pid, row["pre_vol"], row["post_vol"], row["ratio"]))
    print("[done ] %d jpg written" % len(pids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
