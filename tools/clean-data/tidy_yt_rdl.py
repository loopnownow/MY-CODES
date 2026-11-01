# -*- coding: utf-8 -*-
"""yt_RDL 课题路径与 cln 读表辅助（扁平，非子工程包）。

路径唯一出处：本模块 + ``tidy_paths``（profile ``yt_RDL``）。
临时文件 → ``0del/harvested/yt_RDL``；说明/CSV → ``<project>/ref``；主表在课题根。
"""
from __future__ import annotations

from pathlib import Path

from tidy_paths import DEL_ROOT, ensure_dir, project_root, source_dir

PROFILE = "yt_RDL"

PROJECT_ROOT: Path = project_root(PROFILE)
REF: Path = source_dir(PROFILE)
WORK: Path = DEL_ROOT / "harvested" / "yt_RDL"  # t1490u
BACKUP: Path = WORK / "backup"

INPUT_XLS = [REF / f"{n}.xls" for n in ("20", "24", "26")]
RESULTS_7Z = REF / "Results.7z"
RESULTS_TXT = WORK / "Results"
JINSHAN_CSV = WORK / "jinshan_extracted_data.csv"
YT_XLSX = PROJECT_ROOT / "yt_RDL.xlsx"

CAND_XLSX = REF / "yt_RDL_脂肪肉瘤候选_合并表.xlsx"
FU_XLSX = PROJECT_ROOT / "yt_RDL_脂肪肉瘤候选_合并表_随访.xlsx"
SCREEN_MD = REF / "yt_RDL_脂肪肉瘤筛选说明.md"
FU_MD = REF / "yt_RDL_复发随访变量说明.md"
CAND_CSV = REF / "yt_RDL_脂肪肉瘤候选_患者级.csv"
FU_CSV = REF / "yt_RDL_脂肪肉瘤候选_合并表_随访_患者级.csv"

# 本课题判读列——回填脚本追加的患者行这些列全空
STUDY_DATA_COLS = [
    "Dedifferentiated",
    "Recurrence",
    "KI67",
    "CDK4",
    "MDM2",
    "Surrounding involving",
    "Tumor density",
    "Satellite nodules",
]


def ensure_dirs() -> None:
    for p in (REF, WORK, BACKUP):
        ensure_dir(p)


def read_yt_cln(cohort_only: bool = True):
    """读 yt_RDL.xlsx 的 cln 表。

    ``cohort_only=True`` 只返回本课题已判读的原始队列（去掉回填追加行）。
    """
    import pandas as pd

    df = pd.read_excel(YT_XLSX, sheet_name="cln")
    df["Patient_ID"] = df["Patient_ID"].astype(str).str.strip()
    if cohort_only:
        cols = [c for c in STUDY_DATA_COLS if c in df.columns]
        if cols:
            df = df[df[cols].notna().any(axis=1)]
    return df.reset_index(drop=True)
