# -*- coding: utf-8 -*-
"""原发灶（index lesion）后处理：连通域分解 -> 取最大块 -> 剔碎片。

为什么需要
    nnU-Net 训练标签是单类 tumor=1，手工 ROI 基本每人一个主灶（抽样 29/30 为单连通块）；
    但模型输出常碎成 2 到 9 块（卫星灶 / 假阳），需要一层后处理把口径拉回「单个索引病灶」，
    与 RECIST 的 index lesion 对齐。pre 与 post 必须用同一规则，否则 delta 会混入后处理伪影。

两种模式
    评估模式：给定 --gt-dir，只算 raw / LCC / prune 三组 Dice，不写任何掩膜。
    正式模式：给定 --pred-dir，默认 dry-run 只统计；加 --execute 才写 predTs_primary 与组件表。

用法
    # 评估（读已有验证预测，不写盘）
    python select_primary_lesion.py --pred-dir <validation> --gt-dir <gt_segmentations>

    # 小样本快速自测
    python select_primary_lesion.py --pred-dir <validation> --gt-dir <gt> --limit 3

    # 正式：先 dry-run 看统计，再 --execute 落盘
    python select_primary_lesion.py --pred-dir <predTs>
    python select_primary_lesion.py --pred-dir <predTs> --execute

规则
    1. 3D 连通域分解（26 邻接）
    2. 保留最大组件作为索引病灶
    3. 体积 < 最大块 min-share（默认 5%）的组件丢弃
    4. 其余组件若 >= 最大块 review-share（默认 25%），标 multi_review=1 供人工复核，不静默丢

边界
    不覆盖原始预测；输出路径与输入路径相同即中止。
    运行期输出用英文，避免 Windows 控制台对中文的乱码。
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np
import SimpleITK as sitk

CONFIG = {
    # 正式模式默认路径（课题现行落点）
    "PRED_DIR": os.environ.get("CONFIG", ""),
    "OUT_DIR": os.environ.get("CONFIG_2", ""),
    "COMP_CSV": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\roi_components.csv",
    "MIN_SHARE": 0.05,
    "REVIEW_SHARE": 0.25,
    # 混合策略：最大块占比 >= 该值时取 LCC，否则保留全部大块（避免最大块是假阳时丢真病灶）
    "HYBRID_SHARE": 0.50,
}

COMP_COLS = ["case", "comp_id", "voxels", "vol_mm3", "bbox_diag_mm",
             "centroid_z_mm", "centroid_y_mm", "centroid_x_mm",
             "share_of_largest", "kept", "multi_review"]


def list_cases(folder: str) -> list:
    if not os.path.isdir(folder):
        raise SystemExit("[stop] pred dir not found: " + folder)
    files = sorted(glob.glob(os.path.join(folder, "*.nii.gz")))
    if not files:
        raise SystemExit("[stop] no .nii.gz in " + folder)
    return files


def load_mask(path: str):
    img = sitk.ReadImage(path)
    arr = sitk.GetArrayFromImage(img) > 0
    sp = img.GetSpacing()  # (x, y, z)
    return arr, sp, img


def split_components(arr: np.ndarray):
    """返回 (label 数组, 组件 id 按体积降序, 对应体素数)。全空时返回空。"""
    if not arr.any():
        return None, np.array([], dtype=int), np.array([], dtype=float)
    f = sitk.ConnectedComponentImageFilter()
    f.SetFullyConnected(True)
    lab = sitk.GetArrayFromImage(f.Execute(sitk.GetImageFromArray(arr.astype(np.uint8))))
    ids, counts = np.unique(lab, return_counts=True)
    ids, counts = ids[1:], counts[1:].astype(float)
    order = np.argsort(counts)[::-1]
    return lab, ids[order], counts[order]


def dice(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(a.sum() + b.sum())
    if denom <= 0:
        return float("nan")
    return 2.0 * float((a & b).sum()) / denom


def decide(counts: np.ndarray, min_share: float, review_share: float, lcc_share: float = 1.01):
    """返回 (keep_mask, review_mask)。

    最大块占比 >= lcc_share 时退化为只留最大块（LCC）；否则保留所有 >= min_share 的块。
    review_mask 标出 >= review_share 的次级块，供人工复核（不静默丢）。
    """
    n = len(counts)
    keep = np.zeros(n, dtype=bool)
    review = np.zeros(n, dtype=bool)
    if n == 0:
        return keep, review
    total = float(counts.sum())
    largest = float(counts[0])
    use_lcc = total > 0 and (largest / total) >= lcc_share
    keep[0] = True
    for i in range(1, n):
        share = float(counts[i]) / largest
        if share >= review_share:
            review[i] = True
        if not use_lcc and share >= min_share:
            keep[i] = True
    return keep, review


def component_rows(case, lab, ids, counts, keep_mask, sp, voxel_mm3, review_mask):
    rows = []
    zs, ys, xs = sp[2], sp[1], sp[0]
    largest = float(counts[0]) if len(counts) else 0.0
    for i, (cid, cnt) in enumerate(zip(ids, counts)):
        coord = np.argwhere(lab == cid)
        z0, y0, x0 = coord.min(axis=0)
        z1, y1, x1 = coord.max(axis=0)
        centroid = coord.mean(axis=0)
        diag = float(np.linalg.norm([(z1 - z0) * zs, (y1 - y0) * ys, (x1 - x0) * xs]))
        rows.append({
            "case": case,
            "comp_id": int(i + 1),
            "voxels": int(cnt),
            "vol_mm3": round(float(cnt) * voxel_mm3, 1),
            "bbox_diag_mm": round(diag, 1),
            "centroid_z_mm": round(float(centroid[0]) * zs, 1),
            "centroid_y_mm": round(float(centroid[1]) * ys, 1),
            "centroid_x_mm": round(float(centroid[2]) * xs, 1),
            "share_of_largest": round(float(cnt) / largest, 4) if largest else 0.0,
            "kept": int(keep_mask[i]),
            "multi_review": int(review_mask[i]),
        })
    return rows



def run_eval(args) -> int:
    """评估模式：raw / LCC / prune 三组 Dice 对照，不写盘。"""
    preds = list_cases(args.pred_dir)
    if args.limit:
        preds = preds[: args.limit]
    gt_dir = args.gt_dir
    if not os.path.isdir(gt_dir):
        raise SystemExit("[stop] gt dir not found: " + gt_dir)

    SHARES = (0.50, 0.85, 0.95)
    raw_d, lcc_d, prune_d = [], [], []
    n_comp, n_review, skipped = [], 0, 0
    hyb_d = {s: [] for s in SHARES}

    for i, p in enumerate(preds, 1):
        case = os.path.basename(p)[: -len(".nii.gz")]
        g = os.path.join(gt_dir, os.path.basename(p))
        if not os.path.exists(g):
            skipped += 1
            continue
        P, _, _ = load_mask(p)
        G, _, _ = load_mask(g)
        if P.shape != G.shape:
            skipped += 1
            continue
        lab, ids, counts = split_components(P)
        if lab is None or G.sum() == 0:
            skipped += 1
            continue
        keep, review = decide(counts, args.min_share, args.review_share)
        lcc = lab == ids[0]
        pruned = np.isin(lab, ids[keep])
        share0 = float(counts[0]) / float(counts.sum()) if counts.sum() else 0.0
        raw_d.append(dice(P, G))
        lcc_d.append(dice(lcc, G))
        prune_d.append(dice(pruned, G))
        for s in SHARES:
            hyb_d[s].append(dice(lcc if share0 >= s else pruned, G))
        n_comp.append(len(ids))
        n_review += int(review.any())
        if i % 20 == 0:
            print("  [%d/%d] done" % (i, len(preds)), flush=True)

    if not raw_d:
        print("[stop] no comparable case")
        return 1

    raw_d, lcc_d, prune_d = map(np.array, (raw_d, lcc_d, prune_d))

    def line(name, arr):
        return ("%-9s mean %.4f  median %.4f  dice0 %d  <0.3 %d  <0.5 %d"
                % (name, arr.mean(), np.median(arr),
                   int((arr == 0).sum()), int((arr < 0.3).sum()), int((arr < 0.5).sum())))

    def cmp(name, arr):
        b = int((arr > raw_d + 1e-9).sum())
        w = int((arr < raw_d - 1e-9).sum())
        print("%-9s vs raw: better %d, worse %d, same %d" % (name, b, w, len(raw_d) - b - w))

    print("\n--- eval (%d cases, skipped %d) ---" % (len(raw_d), skipped))
    print(line("raw", raw_d))
    print(line("LCC", lcc_d))
    print(line("prune", prune_d))
    for s in SHARES:
        print(line("hyb%.2f" % s, np.array(hyb_d[s])))
    cmp("LCC", lcc_d)
    cmp("prune", prune_d)
    for s in SHARES:
        cmp("hyb%.2f" % s, np.array(hyb_d[s]))
    print("components per case: median %.0f  max %d ; multi_review cases: %d"
          % (np.median(n_comp), max(n_comp), n_review))
    return 0


def run_apply(args) -> int:
    """正式模式：默认 dry-run 统计，--execute 才写掩膜与组件表。"""
    preds = list_cases(args.pred_dir)
    if args.limit:
        preds = preds[: args.limit]

    rows = []
    n_empty = n_single = n_multi = n_review = 0
    for i, p in enumerate(preds, 1):
        case = os.path.basename(p)[: -len(".nii.gz")]
        arr, sp, img = load_mask(p)
        lab, ids, counts = split_components(arr)
        if lab is None:
            n_empty += 1
            rows.append({"case": case, "comp_id": 0, "voxels": 0, "vol_mm3": 0.0,
                         "bbox_diag_mm": 0.0, "centroid_z_mm": 0.0, "centroid_y_mm": 0.0,
                         "centroid_x_mm": 0.0, "share_of_largest": 0.0,
                         "kept": 0, "multi_review": 0})
            if args.execute:
                dst = os.path.join(args.out_dir, os.path.basename(p))
                if os.path.abspath(dst) == os.path.abspath(p):
                    raise SystemExit("[stop] output equals input: " + dst)
                os.makedirs(args.out_dir, exist_ok=True)
                sitk.WriteImage(img, dst)
            continue

        keep, review = decide(counts, args.min_share, args.review_share, args.hybrid_share)
        if args.mode == "raw":
            keep = np.ones(len(ids), dtype=bool)
        elif args.mode == "prune":
            keep, review = decide(counts, args.min_share, args.review_share, 1.01)
        elif args.mode == "lcc":
            keep, review = decide(counts, args.min_share, args.review_share, 0.0)
        n_single += int(len(ids) == 1)
        n_multi += int(len(ids) > 1)
        n_review += int(review.any())
        voxel_mm3 = float(sp[0]) * float(sp[1]) * float(sp[2])
        rows.extend(component_rows(case, lab, ids, counts, keep, sp, voxel_mm3, review))

        if args.execute:
            kept = np.isin(lab, ids[keep]).astype(np.uint8)
            out = sitk.GetImageFromArray(kept)
            out.CopyInformation(img)
            dst = os.path.join(args.out_dir, os.path.basename(p))
            if os.path.abspath(dst) == os.path.abspath(p):
                raise SystemExit("[stop] output equals input: " + dst)
            os.makedirs(args.out_dir, exist_ok=True)
            sitk.WriteImage(out, dst)
        if i % 50 == 0:
            print("  [%d/%d] done" % (i, len(preds)), flush=True)

    print("\n--- summary (%d cases) ---" % len(preds))
    print("empty pred %d ; single blob %d ; multi blob %d ; multi_review cases %d"
          % (n_empty, n_single, n_multi, n_review))
    vols = np.array([r["vol_mm3"] for r in rows if r["kept"] == 1], dtype=float)
    if vols.size:
        print("kept lesion volume (mm3): median %.0f  mean %.0f  p10 %.0f  p90 %.0f"
              % (np.median(vols), vols.mean(),
                 np.percentile(vols, 10), np.percentile(vols, 90)))

    if not args.execute:
        print("[dry-run] nothing written. add --execute to write %s and %s"
              % (args.out_dir, args.comp_csv))
        return 0

    import csv
    os.makedirs(os.path.dirname(args.comp_csv), exist_ok=True)
    with open(args.comp_csv, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COMP_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("[write] %s  %d rows" % (args.comp_csv, len(rows)))
    print("[write] %s" % args.out_dir)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="index lesion post-processing (default dry-run)")
    ap.add_argument("--pred-dir", default=CONFIG["PRED_DIR"])
    ap.add_argument("--out-dir", default=CONFIG["OUT_DIR"])
    ap.add_argument("--comp-csv", default=CONFIG["COMP_CSV"])
    ap.add_argument("--gt-dir", default="", help="evaluation mode: compare against GT, write nothing")
    ap.add_argument("--mode", default="prune", choices=["raw", "prune", "lcc", "hybrid"])
    ap.add_argument("--min-share", type=float, default=CONFIG["MIN_SHARE"])
    ap.add_argument("--review-share", type=float, default=CONFIG["REVIEW_SHARE"])
    ap.add_argument("--hybrid-share", type=float, default=CONFIG["HYBRID_SHARE"],
                    help="largest blob share >= this -> keep only largest (LCC)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    if args.gt_dir:
        return run_eval(args)
    return run_apply(args)


if __name__ == "__main__":
    sys.exit(main())

