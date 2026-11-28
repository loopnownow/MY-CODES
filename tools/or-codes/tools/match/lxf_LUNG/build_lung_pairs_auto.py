# -*- coding: utf-8 -*-
"""生成 delta-habitat 配对表 lung_pairs_auto.csv（lung 211 例）。

为什么需要
    delta_habitat_pipeline/data_loader.load_patient_pairs 只认四列：
    roi_path / img_path / name / exam（exam: 1=治疗前, 2=治疗后）。
    nnU-Net 输出的是 predTs/<pid>_pre.nii.gz 与 _post.nii.gz，需要拼成该格式。
    本脚本只写一张 CSV，不改名、不复制、不压缩任何影像。

输入
    CONFIG["PAIR_CSV"]  final_pairing_5mm.csv（211 行）
    CONFIG["PRED_DIR"]  nnUNet/inference/predTs
输出
    CONFIG["OUT_CSV"]   UTF-8 无 BOM
    （有 BOM 会把首列表头读成 \ufeffname，配对会静默失败，故强制无 BOM）

用法
    python build_lung_pairs_auto.py                      # dry-run：只统计，不写盘
    python build_lung_pairs_auto.py --execute            # 落盘
    python build_lung_pairs_auto.py --execute --force    # 允许覆盖已有输出

边界
    本工程尚未 INI 化（delta 流水线读 config.py），故未走 habitat_core.tool_ini；
    将来并入 settings.ini 时把 CONFIG 换成 _tk.get() 即可，逻辑不动。
"""
from __future__ import annotations

import argparse
import os
import sys

import pandas as pd

CONFIG = {
    "PAIR_CSV": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\final_pairing_5mm.csv",
    # 只收人工复核通过的 150 例（300 个掩膜）；被否的 mask 不在该目录，天然排除
    "PRED_DIR": os.environ.get("CONFIG", ""),
    "OUT_CSV":  r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\lung_pairs_auto.csv",
    # 前后同日（interval_days=0）的例：照常出表，打 exclude_delta 由下游决定
    "EXCLUDE_PATIENTS": ["1003665962"],
}

EXAMS = (("pre", 1), ("post", 2))


def _as_id(v) -> str:
    """患者 ID 统一成去空白的字符串（10 位数字不能被读成 float）。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s


def load_pairs(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        raise SystemExit("配对表不存在: " + path)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    need = {"patient_id", "pre_nii", "post_nii"}
    miss = need - set(df.columns)
    if miss:
        raise SystemExit("配对表缺列: " + ", ".join(sorted(miss)))
    df["patient_id"] = df["patient_id"].map(_as_id)
    df = df[df["patient_id"] != ""].copy()
    dup = sorted(set(df["patient_id"][df["patient_id"].duplicated()].tolist()))
    if dup:
        raise SystemExit("配对表内 patient_id 重复: " + ", ".join(dup))
    return df


def scan_preds(pred_dir: str) -> dict:
    if not os.path.isdir(pred_dir):
        raise SystemExit("预测目录不存在，先跑 nnUNetv2_predict: " + pred_dir)
    found = {}
    for fn in os.listdir(pred_dir):
        if not fn.endswith(".nii.gz"):
            continue
        stem = fn[: -len(".nii.gz")]
        for tag, exam in EXAMS:
            if stem.endswith("_" + tag):
                pid = _as_id(stem[: -len(tag) - 1])
                found[(pid, exam)] = os.path.join(pred_dir, fn)
    return found
def build(df: pd.DataFrame, preds: dict):
    records, issues = [], []
    for r in df.itertuples(index=False):
        pid = _as_id(r.patient_id)
        excluded = 1 if pid in CONFIG["EXCLUDE_PATIENTS"] else 0
        for tag, exam in EXAMS:
            img = str(getattr(r, tag + "_nii", "")).strip()
            roi = preds.get((pid, exam), "")
            if not img or not os.path.exists(img):
                issues.append((pid, tag, "image_missing", img))
                continue
            if not roi:
                issues.append((pid, tag, "pred_missing", ""))
                continue
            records.append({
                "name": pid,
                "exam": exam,
                "img_path": img,
                "roi_path": roi,
                "patient_id": pid,
                "archive": str(getattr(r, "archive", "")).strip(),
                "interval_days": str(getattr(r, "interval_days", "")).strip(),
                "exclude_delta": excluded,
            })
    return records, issues


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 delta-habitat 配对表（默认 dry-run）")
    ap.add_argument("--pairs", default=CONFIG["PAIR_CSV"])
    ap.add_argument("--pred-dir", default=CONFIG["PRED_DIR"])
    ap.add_argument("--out", default=CONFIG["OUT_CSV"])
    ap.add_argument("--execute", action="store_true", help="真正写盘；默认只统计")
    ap.add_argument("--force", action="store_true", help="允许覆盖已有输出")
    args = ap.parse_args(argv)

    df = load_pairs(args.pairs)
    preds = scan_preds(args.pred_dir)
    records, issues = build(df, preds)

    seen = {}
    for r in records:
        seen.setdefault(r["patient_id"], set()).add(r["exam"])
    both = sum(1 for v in seen.values() if v == {1, 2})

    print("[input ] 患者 %d 例，预测文件 %d 个" % (len(df), len(preds)))
    print("[output] 可写 %d 行，双期齐全患者 %d 例（目标 422 行 / 211 例）" % (len(records), both))
    if issues:
        print("[issues] %d 条（最多列 20 条）：" % len(issues))
        for pid, tag, kind, extra in issues[:20]:
            print("   %-12s %-4s %-14s %s" % (pid, tag, kind, extra))
    if both < len(df):
        miss = sorted(set(_as_id(p) for p in df["patient_id"]) - set(seen.keys()))
        if miss:
            print("[warn  ] 完全没有预测的患者 %d 例：%s" % (len(miss), ", ".join(miss[:10])))

    if not records:
        print("[stop  ] 没有可用配对，未写盘。")
        return 1
    if not args.execute:
        print("[dry-run] 未写盘。加 --execute 落盘到 " + args.out)
        return 0
    if os.path.exists(args.out) and not args.force:
        print("[stop  ] 输出已存在，未覆盖：" + args.out + "（要覆盖加 --force）")
        return 1

    out = pd.DataFrame(records, columns=[
        "name", "exam", "img_path", "roi_path",
        "patient_id", "archive", "interval_days", "exclude_delta",
    ])
    out.to_csv(args.out, index=False, encoding="utf-8")
    print("[write ] %s  %d 行" % (args.out, len(out)))
    print("[next  ] 把 delta 流水线的 DATA_TABLE_PATH 指到该表，再跑 01-05")
    return 0


if __name__ == "__main__":
    sys.exit(main())