"""文本编码：自动识别文本列 → ≤N 等级自动编码 → 可手改 → 套到数据上。

配置
----
TEXT_ENCODE            {列名: {原文: 编码}}，手改/控制台写入；优先级最高
TEXT_ENCODE_AUTO       true=没有手写映射时自动编码（默认 true）
TEXT_ENCODE_MAX_LEVELS 自动编码的等级上限（默认 5）

规则
----
- 只处理"非数值"列；全是数字（含可 parse 的文本数字）的列不碰。
- 等级 ≤ max_levels：自动编码。默认码 = 若所有等级都能当数字则用该数字，
  否则按字典序 1..k（日志打印，可手改）。
- 等级 > max_levels：不自动编码，列不进建模；打印明确原因（不静默丢）。
- 套用时有未编码的取值：写 NaN 并打印该列未编码的取值（不猜）。
- 非法编码（非数字）→ ValueError。

运行: 由 modules.stats.data_io / modules.stats.selectors / 控制台导入。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

MAX_LEVELS_DEFAULT = 5

_ID_LIKE = {"Name", "Patient_ID", "patient_id", "PatientID", "ID", "Group"}


def max_levels(raw: Any = None) -> int:
    """自动编码的等级上限。非法值 raise。"""
    if raw is None:
        from modules import config as cfg

        raw = getattr(cfg, "TEXT_ENCODE_MAX_LEVELS", MAX_LEVELS_DEFAULT)
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TEXT_ENCODE_MAX_LEVELS 须为正整数，当前={raw!r}") from exc
    if n < 2:
        raise ValueError(f"TEXT_ENCODE_MAX_LEVELS 至少为 2，当前={n}")
    return n


def auto_on(raw: Any = None) -> bool:
    """是否自动编码（默认开）。"""
    if raw is None:
        from modules import config as cfg

        raw = getattr(cfg, "TEXT_ENCODE_AUTO", True)
    if isinstance(raw, bool):
        return raw
    tok = str(raw).strip().lower()
    if tok in ("", "true", "1", "yes", "on"):
        return True
    if tok in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"TEXT_ENCODE_AUTO 无法识别: {raw!r}")


def _as_code(v: Any, where: str) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{where} 的编码必须是数字，收到 {v!r}") from exc
    if not np.isfinite(f):
        raise ValueError(f"{where} 的编码必须是有限数字，收到 {v!r}")
    return f


def _is_text_series(s: pd.Series) -> bool:
    if pd.api.types.is_numeric_dtype(s):
        return False
    if pd.api.types.is_bool_dtype(s):
        return False
    return True


def normalize_spec(raw: Any = None) -> Dict[str, Dict[str, float]]:
    """{列: {原文: 编码}} 规范化；非法值 raise，不改写原文。"""
    if raw is None:
        from modules import config as cfg

        raw = getattr(cfg, "TEXT_ENCODE", None)
    if raw in (None, "", []):
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"TEXT_ENCODE 必须是对象 {{列: {{原文: 编码}}}}，收到 {type(raw).__name__}"
        )
    out: Dict[str, Dict[str, float]] = {}
    for col, inner in raw.items():
        c = str(col).strip()
        if not c:
            continue
        if not isinstance(inner, dict):
            raise ValueError(f"TEXT_ENCODE[{c!r}] 必须是对象 {{原文: 编码}}")
        m: Dict[str, float] = {}
        for level, code in inner.items():
            lv = str(level).strip()
            if not lv:
                continue
            m[lv] = _as_code(code, f"TEXT_ENCODE[{c!r}][{lv!r}]")
        if m:
            out[c] = m
    return out


def detect_text_columns(
    frames: Dict[str, pd.DataFrame],
    limit: Optional[int] = None,
    skip: Optional[Iterable[str]] = None,
) -> List[dict]:
    """逐表找出非数值列及其等级。返回 [{frame, column, n_levels, levels, over_cap}]。"""
    cap = max_levels() if limit is None else int(limit)
    skip_set = set(_ID_LIKE) | {str(x).strip() for x in (skip or []) if str(x).strip()}
    out: List[dict] = []
    for fname, df in (frames or {}).items():
        if df is None or getattr(df, "empty", True):
            continue
        for col in df.columns:
            name = str(col)
            if name in skip_set:
                continue
            s = df[col]
            if not _is_text_series(s):
                continue
            vals = [str(v).strip() for v in s.dropna().tolist()]
            levels = sorted({v for v in vals if v})
            if not levels:
                continue
            out.append({
                "frame": str(fname),
                "column": name,
                "n_levels": len(levels),
                "levels": levels,
                "over_cap": len(levels) > cap,
            })
    return out


def default_codes(levels: List[str]) -> Dict[str, float]:
    """默认编码：能整体当数字就用数字，否则字典序 1..k。"""
    nums: List[float] = []
    for lv in levels:
        try:
            f = float(lv)
        except (TypeError, ValueError):
            nums = []
            break
        if not np.isfinite(f):
            nums = []
            break
        nums.append(f)
    if nums and len(set(nums)) == len(nums):
        return {lv: float(n) for lv, n in zip(levels, nums)}
    return {lv: float(i + 1) for i, lv in enumerate(levels)}


def auto_spec(
    detected: List[dict],
    spec: Optional[Dict[str, Dict[str, float]]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Dict[str, float]]:
    """自动补码：手写的码保留，缺的等级按默认顺序补；等级超上限且无手写映射则不补。"""
    cap = max_levels() if limit is None else int(limit)
    out: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in (spec or {}).items()}
    for item in detected:
        col = item["column"]
        levels = list(item["levels"])
        have = dict(out.get(col) or {})
        if item["n_levels"] > cap and not have:
            continue
        codes = default_codes(levels)
        for lv in levels:
            if lv not in have and lv in codes:
                have[lv] = codes[lv]
        if have:
            out[col] = have
    return out


def apply(
    df: pd.DataFrame,
    spec: Dict[str, Dict[str, float]],
    frame_label: str = "",
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """按映射把文本列换成编码。未编码取值 → NaN 并打印。"""
    out = df.copy()
    report: Dict[str, Any] = {"encoded": [], "unmapped": {}, "skipped": []}
    for col, mapping in (spec or {}).items():
        if col not in out.columns:
            report["skipped"].append(col)
            continue
        s = out[col]
        if pd.api.types.is_numeric_dtype(s):
            continue
        key = s.map(lambda v: str(v).strip() if pd.notna(v) else "")
        codes = key.map(lambda k: mapping.get(k, np.nan))
        bad = sorted({k for k in key.unique().tolist() if k and k not in mapping})
        out[col] = pd.to_numeric(codes, errors="coerce")
        report["encoded"].append(col)
        if bad:
            report["unmapped"][col] = bad
    if report["encoded"]:
        print(f"  文本编码 [{frame_label}]: {', '.join(report['encoded'])}", flush=True)
    for col, bad in report["unmapped"].items():
        print(
            f"  ⚠ 文本编码 [{frame_label}] {col!r}: {len(bad)} 个取值没给编码 → NaN: {bad[:8]}",
            flush=True,
        )
    for col in report["skipped"]:
        print(f"  ⚠ 文本编码 [{frame_label}]: 列 {col!r} 不在本表，忽略映射", flush=True)
    return out, report


def prepare_frame(
    df: pd.DataFrame,
    *,
    frame_label: str = "",
    spec: Optional[Dict[str, Dict[str, float]]] = None,
    limit: Optional[int] = None,
    auto: Optional[bool] = None,
) -> pd.DataFrame:
    """一步到位：检测 → 自动补码（开关）→ 套用；超限列打印原因后跳过。"""
    cap = max_levels() if limit is None else int(limit)
    manual = normalize_spec(spec) if spec is not None else normalize_spec()
    detected = detect_text_columns({frame_label or "block": df}, limit=cap)
    if not detected:
        return df
    use_auto = auto_on() if auto is None else bool(auto)
    effective = auto_spec(detected, manual, limit=cap) if use_auto else dict(manual)
    over = [d for d in detected if d["over_cap"] and d["column"] not in effective]
    for item in over:
        print(
            f"  ⚠ 文本编码 [{frame_label}] {item['column']!r}: "
            f"{item['n_levels']} 个等级 > 上限 {cap}，不自动编码，该列不进建模"
            f"（需要时在控制台手写映射）",
            flush=True,
        )
    drop_cols = [i["column"] for i in over if i["column"] in df.columns]
    work = df.drop(columns=drop_cols, errors="ignore") if drop_cols else df
    fixed, _ = apply(work, effective, frame_label=frame_label)
    return fixed
