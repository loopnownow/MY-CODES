# -*- coding: utf-8 -*-
"""pictologics_extract.py — pictologics（IBSI）并行影像组学特征提取（综合版）。

综合自两版同源脚本，共享 PIPELINE_STEPS 与提取逻辑，差异只在输入发现与 label 来源：
  * tools\\brain_nii_ZJL\\codes\\pictologics_run.py  → --mode csv
        CSV 列 0=image（DICOM 目录或 NIfTI），1=ROI（.nii/.nii.gz），2=label（可选）
  * tools\\HE_nii_ly\\utili\\pictologics_LST.py      → --mode scan
        扫描目录，ROI 前缀（如 ples_lpa_m）配同文件夹同名 image，label=子文件夹名

依赖：pictologics>=0.4.1、numpy、pandas。

用法：
  python pictologics_extract.py --mode csv  --csv pairs.csv --output result.csv
  python pictologics_extract.py --mode scan --input L:\\SSD\\SSD_dyf\\Flair \\
      --roi-prefix ples_lpa_m --roi-suffix .nii --output result.csv
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

# 特征配置（镜像原 Params.yaml）：1 mm 重采样 / 最大连通域 / FBS bin_width=25 /
# intensity,morphology,texture(6 矩阵),histogram,ivh
PIPELINE_STEPS = [
    {"step": "resample",
     "params": {"new_spacing": (1, 1, 1), "interpolation": "linear",
                "mask_interpolation": "nearest", "mask_threshold": 0.5}},
    {"step": "keep_largest_component", "params": {"apply_to": "both"}},
    {"step": "discretise", "params": {"method": "FBS", "bin_width": 25}},
    {"step": "extract_features",
     "params": {"families": ["intensity", "morphology", "texture", "histogram", "ivh"]}},
]

NUM_WORKERS = min(os.cpu_count() or 4, 8)   # 上限 8，避免内存压力
MAX_RETRIES = 2


def _build_pipeline():
    """每个 worker 新建一个 RadiomicsPipeline（跨进程不可共享）。"""
    os.environ.setdefault("PICTOLOGICS_DISABLE_WARMUP", "1")
    from pictologics import RadiomicsPipeline
    pipeline = RadiomicsPipeline(deduplicate=False, load_standard=False)
    pipeline.add_config(name="standard", steps=PIPELINE_STEPS, source_mode="full_image")
    return pipeline


def _load_image_and_mask_fixed(image_path, mask_path):
    """读 image(可 DICOM 目录或 NIfTI) 与 mask，强制 mask 几何对齐 image。

    DICOM(LPS) 与 NIfTI(RAS) 坐标不同、mask 可能是子体积，pictologics 会做几何校验；
    这里把 mask 的 origin/spacing/direction 强制设成 image 的值（mask 数组本就在 image 体素网格内）。
    """
    from pictologics import Image as PlgImage
    from pictologics.loader import load_image

    img = load_image(image_path)
    mask = load_image(mask_path)
    mask_binary = (mask.array > 0).astype(np.uint8)
    mask_fixed = PlgImage(array=mask_binary, spacing=img.spacing, origin=img.origin,
                          direction=img.direction, modality=mask.modality)
    return img, mask_fixed


def process_single_pair(args):
    """提取一对 image/ROI 的特征 → (idx, label, feature_dict|None, error|None)。"""
    idx, image_path, roi_path, label = args
    if not Path(image_path).exists():
        return (idx, label, None, f"Image not found: {image_path}")
    if not Path(roi_path).exists():
        return (idx, label, None, f"ROI not found: {roi_path}")

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            pipeline = _build_pipeline()
            img_obj, mask_obj = _load_image_and_mask_fixed(image_path, roi_path)
            results = pipeline.run(image=img_obj, mask=mask_obj, config_names=["standard"])
            from pictologics import format_results
            feature_dict = format_results(
                results, fmt="wide",
                meta={"image_path": image_path, "roi_path": roi_path},
                output_type="dict",
            )
            return (idx, label, feature_dict, None)
        except Exception as exc:  # noqa: BLE001
            last_error = f"Attempt {attempt + 1}: {exc}\n{traceback.format_exc()}"
            if attempt < MAX_RETRIES:
                time.sleep(1.0)
    return (idx, label, None, last_error)


def build_tasks_csv(csv_path: Path):
    """CSV 配对模式：返回 (tasks, skipped_count)。"""
    df = pd.read_csv(csv_path)
    print(f"Reading CSV: {csv_path}  ({len(df)} rows)")
    if len(df.columns) < 2:
        sys.exit("ERROR: CSV 至少两列（image_path, roi_path）")
    col_img, col_roi = df.columns[0], df.columns[1]
    col_label = df.columns[2] if len(df.columns) > 2 else None
    tasks, skipped = [], 0
    for idx, row in df.iterrows():
        img, roi = str(row[col_img]).strip(), str(row[col_roi]).strip()
        if not img or not roi or img.lower() == "nan" or roi.lower() == "nan":
            skipped += 1
            continue
        label = str(row[col_label]) if col_label else str(idx)
        tasks.append((idx, img, roi, label))
    return tasks, skipped


def _find_image(roi_file: Path, roi_prefix: str):
    """目录扫描模式：在同文件夹找去掉 ROI 前缀的同名 image（.nii / .nii.gz）。"""
    body = roi_file.name[len(roi_prefix):]
    if body.endswith(".gz"):
        body = body[:-3]
    if body.endswith(".nii"):
        body = body[:-4]
    for cand in (body + ".nii", body + ".nii.gz"):
        p = roi_file.parent / cand
        if p.is_file():
            return p
    return None


def build_tasks_scan(input_dir: Path, roi_prefix: str, roi_suffix: str):
    """目录扫描模式：返回 (tasks, skipped_list)。"""
    print(f"Scanning: {input_dir}   ROI pattern: {roi_prefix}*{roi_suffix}")
    roi_files = sorted(input_dir.rglob(f"{roi_prefix}*{roi_suffix}"))
    tasks, skipped = [], []
    for idx, roi_file in enumerate(roi_files):
        img_file = _find_image(roi_file, roi_prefix)
        if img_file is None:
            skipped.append({"roi_path": str(roi_file),
                            "reason": f"No matching image in {roi_file.parent}"})
            continue
        tasks.append((idx, str(img_file), str(roi_file), roi_file.parent.name))
    return tasks, skipped


def main():
    ap = argparse.ArgumentParser(description="pictologics 并行影像组学特征提取（csv/scan）")
    ap.add_argument("--mode", choices=["csv", "scan"], required=True)
    ap.add_argument("--csv", help="csv 模式：配对 CSV")
    ap.add_argument("--input", help="scan 模式：扫描根目录")
    ap.add_argument("--roi-prefix", default="ples_lpa_m", help="scan 模式 ROI 前缀")
    ap.add_argument("--roi-suffix", default=".nii", help="scan 模式 ROI 后缀")
    ap.add_argument("--output", default="pictologics_result.csv", help="输出 CSV")
    ap.add_argument("--error-log", default="pictologics_errors.csv", help="错误 CSV")
    args = ap.parse_args()

    print("=" * 60)
    print("pictologics 并行影像组学特征提取")
    print("=" * 60)

    skipped_list = []
    if args.mode == "csv":
        if not args.csv:
            ap.error("--mode csv 需提供 --csv")
        tasks, skipped = build_tasks_csv(Path(args.csv))
        print(f"  Valid pairs: {len(tasks)} (skipped {skipped} rows)")
    else:
        if not args.input:
            ap.error("--mode scan 需提供 --input")
        inp = Path(args.input)
        if not inp.is_dir():
            sys.exit(f"ERROR: 输入目录不存在: {inp}")
        tasks, skipped_list = build_tasks_scan(inp, args.roi_prefix, args.roi_suffix)
        print(f"  Valid pairs: {len(tasks)}")
        for s in skipped_list:
            print(f"    [SKIP] {s['roi_path']} → {s['reason']}")

    if not tasks:
        sys.exit("未找到有效的 image-ROI 配对。")

    print(f"\nLaunching {NUM_WORKERS} workers …")
    t0 = time.perf_counter()
    feature_rows, errors, done, total = [], [], 0, len(tasks)
    os.environ.setdefault("PICTOLOGICS_DISABLE_WARMUP", "1")
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as pool:
        futures = {pool.submit(process_single_pair, t): t for t in tasks}
        for fut in as_completed(futures):
            idx, label, feat_dict, err = fut.result()
            done += 1
            if err:
                errors.append({"index": idx, "label": label, "error": err})
            else:
                feature_rows.append(feat_dict)
            if done % 20 == 0 or done == total:
                elapsed = time.perf_counter() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  [{done:4d}/{total}]  err:{len(errors):3d}  {rate:.1f} pairs/s  {elapsed:.0f}s")

    dt = time.perf_counter() - t0
    print(f"\nDone in {dt:.0f}s ({dt / 60:.1f} min).  Success: {len(feature_rows)}  Failed: {len(errors)}")

    if feature_rows:
        df_out = pd.DataFrame(feature_rows)
        df_out.to_csv(args.output, index=False)
        print(f"  Features → {args.output}  (shape {df_out.shape})")
    if errors:
        pd.DataFrame(errors).to_csv(args.error_log, index=False)
        print(f"  Errors → {args.error_log}  ({len(errors)} entries)")
    if skipped_list:
        pd.DataFrame(skipped_list).to_csv("pictologics_skipped.csv", index=False)


if __name__ == "__main__":
    main()

