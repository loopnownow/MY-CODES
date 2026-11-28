# -*- coding: utf-8 -*-
"""
merge_0results.py —— 把 0results 里过多的表精简为 2 张 Excel 表。

用途（2026-10-05 用户要求）：
  - 只保留 2 张最终结果表：1 张 ASL 表 + 1 张 diffusion 表。
  - 表格类结果统一保存为 .xlsx（规则 6.11）。
  - 能合并的合并（规则 6.12）。

输入（全部从 derivatives 原件读取，不依赖 0results 的副本）：
  ASL 每例表（ExploreASL Population）：
    .../Population/Stats/mean_qCBF_StandardSpace_*_PVC*.tsv    (4)
    .../Population/Stats/median_qCBF_StandardSpace_*_PVC*.tsv  (2)
    .../Population/Stats/CoV_qCBF_StandardSpace_*_PVC*.tsv     (4)
    .../Population/Stats/RegistrationTC.tsv                    (1)
    .../Population/DICOMparameters/QuantificationParameters_*.tsv (2)
  diffusion 每例长表（Diffusion_ROI）：
    .../Diffusion_ROI/roi_long.tsv
    .../Diffusion_ROI/roi_qc.tsv

输出：
  0results/asl_subject_summary.xlsx   —— 一行一例的 ASL 汇总宽表
  0results/diffusion_roi_long.xlsx    —— 一行一例×ROI 的 diffusion 长表

设计要点：
  - Stats 的 qCBF 表含 ExploreASL 标签行（participant_id == "StudyID"），必须删除。
  - QuantificationParameters 表尾部含 mean/SD/CV/threshold 汇总行，必须删除。
  - 统一规则：只保留 participant_id 以 "sub-" 开头的行。
"""
import os

import re
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 路径配置（软编码，置顶）
ROOT = Path(os.environ.get("ROOT", ""))
STATS_DIR = ROOT / "derivatives" / "ExploreASL" / "Population" / "Stats"
DICOMPARAMS_DIR = ROOT / "derivatives" / "ExploreASL" / "Population" / "DICOMparameters"
DIFFUSION_ROI_DIR = ROOT / "derivatives" / "Diffusion_ROI"
OUT_DIR = ROOT / "0results"

OUT_ASL = OUT_DIR / "asl_subject_summary.xlsx"
OUT_DIFF = OUT_DIR / "diffusion_roi_long.xlsx"

# ---------------------------------------------------------------- 结构常量（软编码，置顶）
KEY = ["participant_id", "session"]           # 逐受试者表的连接键
DESC_COLS = [                                 # qCBF 表共用描述列（合并后只留 1 份）
    "LongitudinalTimePoint", "SubjectNList", "Site",
    "GM_vol", "WM_vol", "CSF_vol",
    "GM_ICVRatio", "GMWM_ICVRatio", "MeanMotion",
]
SUBJECT_RE = re.compile(r"^sub-\d+")          # 合法受试者行（过滤标签行/汇总行）
STATS_NAME_RE = re.compile(                   # mean_qCBF_StandardSpace_TotalGM_n=141_05-Oct-2026_PVC0.tsv
    r"^(?P<metric>mean_qCBF|median_qCBF|CoV_qCBF)_StandardSpace_(?P<region>TotalGM|DeepWM)"
    r"_n=\d+_[0-9]{2}-[A-Za-z]{3}-[0-9]{4}_(?P<pvc>PVC\d)\.tsv$"
)
METRIC_ORDER = {"mean_qCBF": 0, "median_qCBF": 1, "CoV_qCBF": 2}   # 输出列排序
REGION_ORDER = {"TotalGM": 0, "DeepWM": 1}
PVC_ORDER = {"PVC0": 0, "PVC2": 1}

# diffusion 长表输出列顺序（nvox_in_atlas 与 nvox 全等，已按精简原则去掉）
DIFF_COLS = [
    "subject", "group", "modality", "parameter", "roi_index", "roi_abbr", "roi_name",
    "nvox", "mean", "median", "sd", "flag",
]


def read_subject_table(path: Path) -> pd.DataFrame:
    """读 TSV，删除标签行/汇总行，只留受试者行。"""
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    df = df[df["participant_id"].str.match(SUBJECT_RE)].copy()
    return df


def to_numeric(df: pd.DataFrame, cols) -> pd.DataFrame:
    """把指定列转数值；'n/a' 等非数字 -> NaN。"""
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _sort_key(prefix: str):
    """按固定顺序排列指标块：mean -> median -> CoV -> RegistrationTC -> qp_*。"""
    parts = prefix.split("_")
    if prefix.startswith(("mean_", "median_", "CoV_")):
        if prefix.startswith("CoV_"):
            metric, region, pvc = "CoV_qCBF", parts[-2], parts[-1]
        else:
            metric, region, pvc = "_".join(parts[:2]), parts[-2], parts[-1]
        return (0, METRIC_ORDER[metric], REGION_ORDER[region], PVC_ORDER[pvc])
    if prefix == "RegistrationTC":
        return (1, 0, 0, 0)
    return (2, 0, 0, 0)      # qp_ASL4D / qp_M0


def build_asl_summary() -> pd.DataFrame:
    """合并 13 张 ASL 每例表 -> 一行一例的宽表。"""
    blocks = {}          # prefix -> 该指标的值列 DataFrame（含 KEY）

    # ① qCBF 指标表（mean / median / CoV）
    stats_desc = None
    for p in STATS_DIR.glob("*.tsv"):
        m = STATS_NAME_RE.match(p.name)
        if not m:
            continue     # RegistrationTC.tsv 走单独分支
        df = read_subject_table(p)
        if stats_desc is None:
            stats_desc = df[KEY + DESC_COLS].copy()     # 描述列只取 1 份
        vcols = [c for c in df.columns if c not in KEY + DESC_COLS]
        region = m["region"]
        prefix = f"{m['metric']}_{region}_{m['pvc']}"
        # 值列名形如 TotalGM_B / DeepWM_L；去掉重复的区域前缀 -> B / L / R
        rename = {c: f"{prefix}_{c[len(region) + 1:] if c.startswith(region + '_') else c}"
                  for c in vcols}
        df = to_numeric(df, vcols)
        blocks[prefix] = df[KEY + vcols].rename(columns=rename)

    # ② RegistrationTC
    reg = read_subject_table(STATS_DIR / "RegistrationTC.tsv")
    reg = to_numeric(reg, ["TC_ASL2T1w_Perc"])
    blocks["RegistrationTC"] = reg[KEY + ["TC_ASL2T1w_Perc"]]

    # ③ QuantificationParameters（ASL4D / M0）
    for p in DICOMPARAMS_DIR.glob("QuantificationParameters_*.tsv"):
        tag = p.stem.replace("QuantificationParameters_", "")   # ASL4D / M0
        df = read_subject_table(p)
        vcols = [c for c in df.columns if c not in KEY]
        blocks[f"qp_{tag}"] = df[KEY + vcols].rename(
            columns={c: f"qp_{tag}_{c}" for c in vcols}
        )

    # ④ 按固定顺序外连接所有块
    out = stats_desc.copy()
    for prefix in sorted(blocks, key=_sort_key):
        out = out.merge(blocks[prefix], on=KEY, how="outer", validate="one_to_one")

    return out.sort_values(KEY).reset_index(drop=True)


def build_diffusion_long() -> pd.DataFrame:
    """合并 roi_long + roi_qc -> 一行一例×ROI 的长表。"""
    kkey = ["subject", "modality", "parameter", "roi_index"]
    long = pd.read_csv(DIFFUSION_ROI_DIR / "roi_long.tsv", sep="\t",
                       dtype=str, keep_default_na=False)
    qc = pd.read_csv(DIFFUSION_ROI_DIR / "roi_qc.tsv", sep="\t",
                     dtype=str, keep_default_na=False)
    out = long.merge(qc[kkey + ["flag"]], on=kkey, how="left", validate="one_to_one")
    out = out[DIFF_COLS]
    out = to_numeric(out, ["roi_index", "nvox", "mean", "median", "sd"])
    return out


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    asl = build_asl_summary()
    print(f"[ASL]  rows={len(asl)}  cols={len(asl.columns)}")
    print(f"[ASL]  columns={list(asl.columns)}")
    asl.to_excel(OUT_ASL, index=False)
    print(f"[ASL]  -> {OUT_ASL}")

    diff = build_diffusion_long()
    print(f"[DIFF] rows={len(diff)}  cols={len(diff.columns)}")
    print(f"[DIFF] columns={list(diff.columns)}")
    diff.to_excel(OUT_DIFF, index=False)
    print(f"[DIFF] -> {OUT_DIFF}")


if __name__ == "__main__":
    main()
