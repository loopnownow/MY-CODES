"""Console setting labels / tips / defaults (extracted from console.py).

Live HTTP server and path guards stay in ``console.py``; this module is the
editable SSOT for panel copy so console.py stays smaller.
"""
from __future__ import annotations

from typing import Any

# Re-export shells filled by console.py at import time if needed; tips below
# are the ones edited for QC / medical notes.

# ICC 面板提示（组学稳定性整块）；与 console_schema.js 的 icc 组 hint 保持同一文案。
ICC_PANEL_HINT: str = (
    "组学特征稳定性过滤：intra 与 inter 都≥阈值才保留。"
    "在 TRAIN 上、交叉验证前执行，不使用分析结局（outcome-blind）。"
)

ICC_KEY_TIPS: dict[str, str] = {
    "DO_ICC_FILTER": (
        "intra 与 inter 都≥阈值才保留该组学特征。"
        "在 TRAIN 上、交叉验证前执行，不使用分析结局（outcome-blind）。"
    ),
    "ICC_THRESHOLD": "ICC 保留阈值，常用 0.75。",
    "ICC_FEATURE_COL": "ICC 表里的特征名列。",
    "ICC_INTRA_COL": "观察者内 ICC 列名。",
    "ICC_INTER_COL": "观察者间 ICC 列名。",
    "ICC_REQUIRE_ROW": "开=ICC 表没有该特征就丢掉；关=没有行则保留。",
}


def merge_icc_tips(tips: dict[str, Any]) -> dict[str, Any]:
    out = dict(tips)
    out.update(ICC_KEY_TIPS)
    return out
