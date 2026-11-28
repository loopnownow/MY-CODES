# -*- coding: utf-8 -*-
"""导出每个病例的「最大病灶层面 + ROI」JPG，供人工逐例审阅。

为什么需要
    胸科（XK）211 例没有手工金标准，模型在 5 mm 重建上给出 ROI；能否用只能靠人看。
    一例一张图，取病灶面积最大的轴位层面，左panel全层、右panel病灶区放大，红线圈 ROI。

规则
    - 空掩膜：不跳例，取体积中间层并标注 EMPTY，便于确认「确实没找到病灶」
    - 掩膜触及 z 首/末层：标 EDGE，提示病灶可能被视野截断
    - 文件名 = <case>.jpg（case 即 <pid>_pre / <pid>_post），与 predTs 同名
    - 断点续跑：已存在的 jpg 默认跳过；index.csv 每次重写（全量统计）

用法
    python render_roi_review_jpg.py                     # 默认 dry-run 统计（不写图）
    python render_roi_review_jpg.py --execute           # 写 jpg + index.csv
    python render_roi_review_jpg.py --execute --limit 5 # 试跑 5 例
    python render_roi_review_jpg.py --execute --window mediastinal

输入   CT   nnUNet/inference/imagesTs/<case>_0000.nii.gz
       ROI  nnUNet/inference/predTs/<case>.nii.gz
输出   nnUNet/inference/qc_roi_jpg/<case>.jpg
       nnUNet/inference/qc_roi_jpg/roi_review_index.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import SimpleITK as sitk

CONFIG = {
    "CT_DIR": os.environ.get("CONFIG", ""),
    "PRED_DIR": os.environ.get("CONFIG_2", ""),
    "OUT_DIR": os.environ.get("CONFIG_3", ""),
    "INDEX_CSV": os.environ.get("CONFIG_4", ""),
    "WINDOW": "lung",     # lung | mediastinal
    "DPI": 110,
    "PAD_MM": 20.0,       # 放大 panel 的边距
    "JPG_QUALITY": 88,
}

WINDOWS = {
    "lung": (-600.0, 1500.0),          # (level, width)
    "mediastinal": (40.0, 400.0),
}

INDEX_COLS = ["case", "exam", "n_slices", "slice_idx", "area_mm2", "vol_mm3",
              "n_components", "flag", "jpg"]


def apply_window(arr: np.ndarray, level: float, width: float) -> np.ndarray:
    lo, hi = level - width / 2.0, level + width / 2.0
    out = np.clip(arr, lo, hi)
    out = (out - lo) / max(1e-6, (hi - lo))
    return (out * 255.0).astype(np.uint8)


def load_pair(ct_path: str, pr_path: str):
    ct_img = sitk.ReadImage(ct_path)
    pr_img = sitk.ReadImage(pr_path)
    ct = sitk.GetArrayFromImage(ct_img).astype(np.float32)
    pr = sitk.GetArrayFromImage(pr_img) > 0
    if ct.shape != pr.shape:
        raise ValueError("shape mismatch ct%s pred%s" % (ct.shape, pr.shape))
    sp = pr_img.GetSpacing()  # (x, y, z)
    return ct, pr, sp


def pick_slice(pr: np.ndarray, sp):
    """返回 (最大病灶层面索引, 该层面积 mm2, 是否空掩膜)。"""
    area = pr.reshape(pr.shape[0], -1).sum(axis=1)
    if area.max() <= 0:
        return pr.shape[0] // 2, 0.0, True
    idx = int(np.argmax(area))
    return idx, float(area[idx]) * float(sp[0]) * float(sp[1]), False


def count_components(pr: np.ndarray) -> int:
    if not pr.any():
        return 0
    f = sitk.ConnectedComponentImageFilter()
    f.SetFullyConnected(True)
    lab = sitk.GetArrayFromImage(f.Execute(sitk.GetImageFromArray(pr.astype(np.uint8))))
    return int(len(np.unique(lab)) - 1)


def crop_box(mask2d: np.ndarray, pad: int):
    ys, xs = np.where(mask2d)
    h, w = mask2d.shape
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(h - 1, y1 + pad), min(w - 1, x1 + pad)
    return y0, y1, x0, x1



def render_one(case: str, ct: np.ndarray, pr: np.ndarray, sp, out_path: str,
               level: float, width: float, pad_mm: float, dpi: int, quality: int):
    """渲染一例：左全层、右病灶放大，红线圈 ROI。返回索引行。"""
    idx, area_mm2, empty = pick_slice(pr, sp)
    n_comp = count_components(pr)
    vox_mm3 = float(sp[0]) * float(sp[1]) * float(sp[2])
    vol_mm3 = float(pr.sum()) * vox_mm3

    flags = []
    if empty:
        flags.append("EMPTY")
    if pr.any() and (pr[0].any() or pr[-1].any()):
        flags.append("EDGE_Z")
    if n_comp >= 2:
        flags.append("MULTI%d" % n_comp)
    flag = ";".join(flags)

    img = apply_window(ct[idx], level, width)
    colors = ["red", "orange", "cyan", "magenta", "yellow", "lime", "deepskyblue", "white"]
    lab = None
    if not empty:
        f = sitk.ConnectedComponentImageFilter()
        f.SetFullyConnected(True)
        lab = sitk.GetArrayFromImage(f.Execute(sitk.GetImageFromArray(pr.astype(np.uint8))))
        present = [c for c in np.unique(lab) if c > 0]
        order = sorted(present, key=lambda c: -(lab == c).sum())
        vols = [float((lab == c).sum()) * vox_mm3 for c in order]

    if empty:
        n = pr.shape[0]
        picks = sorted({max(0, n // 4), n // 2, min(n - 1, (3 * n) // 4)})
        fig, axes = plt.subplots(1, len(picks), figsize=(5.0 * len(picks), 5.2), squeeze=False)
        axes = axes.ravel()
        for ax, z in zip(axes, picks):
            ax.imshow(apply_window(ct[z], level, width), cmap="gray", interpolation="nearest")
            ax.set_title("slice %d/%d" % (z + 1, n), fontsize=9)
            ax.axis("off")
        info = "%s | EMPTY prediction (no lesion found) | vol 0 mm3" % case
    else:
        fig, axes = plt.subplots(1, 2, figsize=(11.0, 5.4))
        axes[0].imshow(img, cmap="gray", interpolation="nearest")
        for k, c in enumerate(order):
            axes[0].contour((lab[idx] == c).astype(float), levels=[0.5],
                            colors=colors[k % len(colors)], linewidths=1.1)
        axes[0].set_title("%s  slice %d/%d" % (case, idx + 1, pr.shape[0]), fontsize=9)
        axes[0].axis("off")

        pad = max(2, int(round(pad_mm / max(1e-6, float(sp[0])))))
        y0, y1, x0, x1 = crop_box(pr[idx], pad)
        axes[1].imshow(img[y0:y1 + 1, x0:x1 + 1], cmap="gray", interpolation="nearest")
        for k, c in enumerate(order):
            sub = (lab[idx] == c)[y0:y1 + 1, x0:x1 + 1].astype(float)
            if sub.any():
                axes[1].contour(sub, levels=[0.5], colors=colors[k % len(colors)], linewidths=1.3)
        axes[1].set_title("zoom", fontsize=9)
        axes[1].axis("off")

        top = ", ".join("%.0f" % v for v in vols[:3])
        info = ("vol %.0f mm3 | slice area %.0f mm2 | comps %d [%s]"
                % (vol_mm3, area_mm2, n_comp, top))
        if flag:
            info += " | " + flag

    fig.suptitle(info, fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=dpi, format="jpg", pil_kwargs={"quality": quality})
    plt.close(fig)

    exam = "pre" if case.endswith("_pre") else ("post" if case.endswith("_post") else "")
    return {"case": case, "exam": exam, "n_slices": pr.shape[0], "slice_idx": idx + 1,
            "area_mm2": round(area_mm2, 1), "vol_mm3": round(vol_mm3, 1),
            "n_components": n_comp, "flag": flag, "jpg": out_path}


def load_index(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("case"):
                out[r["case"]] = r
    return out


def write_index(path: str, rows: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_COLS)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in INDEX_COLS})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="render max-slice ROI review jpg")
    ap.add_argument("--ct-dir", default=CONFIG["CT_DIR"])
    ap.add_argument("--pred-dir", default=CONFIG["PRED_DIR"])
    ap.add_argument("--out-dir", default=CONFIG["OUT_DIR"])
    ap.add_argument("--index-csv", default=CONFIG["INDEX_CSV"])
    ap.add_argument("--window", default=CONFIG["WINDOW"], choices=sorted(WINDOWS))
    ap.add_argument("--dpi", type=int, default=CONFIG["DPI"])
    ap.add_argument("--pad-mm", type=float, default=CONFIG["PAD_MM"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-render even if jpg exists")
    args = ap.parse_args(argv)

    preds = sorted(glob.glob(os.path.join(args.pred_dir, "*.nii.gz")))
    if not preds:
        print("[stop] no prediction in " + args.pred_dir)
        return 1
    if args.limit:
        preds = preds[: args.limit]

    level, width = WINDOWS[args.window]
    rows = load_index(args.index_csv) if args.execute else {}
    done = skipped = failed = 0
    n_empty = n_multi = n_edge = 0

    for i, p in enumerate(preds, 1):
        case = os.path.basename(p)[: -len(".nii.gz")]
        out_path = os.path.join(args.out_dir, case + ".jpg")
        if args.execute and not args.force and os.path.exists(out_path) and case in rows:
            skipped += 1
            continue
        ct_path = os.path.join(args.ct_dir, case + "_0000.nii.gz")
        if not os.path.exists(ct_path):
            print("[warn] ct missing: " + case)
            failed += 1
            continue
        try:
            ct, pr, sp = load_pair(ct_path, p)
            if args.execute:
                os.makedirs(args.out_dir, exist_ok=True)
                row = render_one(case, ct, pr, sp, out_path, level, width,
                                 args.pad_mm, args.dpi, CONFIG["JPG_QUALITY"])
                rows[case] = row
                done += 1
                n_empty += int("EMPTY" in row["flag"])
                n_multi += int(row["n_components"] >= 2)
                n_edge += int("EDGE_Z" in row["flag"])
            else:
                _, _, empty = pick_slice(pr, sp)
                n_empty += int(empty)
                n_multi += int(count_components(pr) >= 2)
                n_edge += int(pr.any() and (pr[0].any() or pr[-1].any()))
        except Exception as exc:
            print("[fail] %s : %s" % (case, exc))
            failed += 1
        if i % 20 == 0:
            print("  [%d/%d] rendered %d skipped %d failed %d"
                  % (i, len(preds), done, skipped, failed), flush=True)

    print("\n--- summary (%d cases, window=%s) ---" % (len(preds), args.window))
    print("rendered %d ; skipped %d ; failed %d" % (done, skipped, failed))
    print("empty %d ; multi-component %d ; touches z edge %d"
          % (n_empty, n_multi, n_edge))
    if not args.execute:
        print("[dry-run] nothing written. add --execute to write %s" % args.out_dir)
        return 0
    write_index(args.index_csv, rows)
    print("[write] %s  %d rows" % (args.index_csv, len(rows)))
    print("[write] %s" % args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())

