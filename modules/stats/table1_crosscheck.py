"""Dev-only cross-check of Table 1 P values against the `tableone` package.

配置
----
DISPLAY_DIGITS   与 Table 1 一致的显示位数（<0.001 例外）
DEFAULT_ALPHA    正态性判定阈值（与本仓 `_build_table1` 一致）
REASON_YATES     2×2 表的 Yates 连续性校正差异标签
REASON_MW_KW     非正态连续变量：本仓 Mann-Whitney vs tableone Kruskal-Wallis

运行
----
    python -B -m modules.stats.table1_crosscheck --xlsx <file> --sheet cln --group Group \
        [--categorical Sex,Smoking] [--nonnormal Size_mm] [--out report.md]

本模块**只做校验**：不进入 pipeline，不改任何主路径数字。需要可选依赖 `tableone`。
"""
from __future__ import annotations

import argparse
from typing import Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd
from scipy import stats
from tableone import TableOne

from modules.stats.clinical import _categorical_p

DISPLAY_DIGITS = 3
DEFAULT_ALPHA = 0.05
MIN_GROUP_N = 3
REASON_YATES = "yates (2x2 continuity correction)"
REASON_MW_KW = "mann-whitney vs kruskal-wallis"


def _fmt(p: float) -> str:
    if p is None or not np.isfinite(p):
        return "NA"
    return "<0.001" if p < 0.001 else f"{p:.{DISPLAY_DIGITS}f}"


def _ours_continuous(x: pd.Series, i1: np.ndarray, i0: np.ndarray, yates: bool = False):
    x1 = np.asarray(x.iloc[i1], dtype=float)
    x0 = np.asarray(x.iloc[i0], dtype=float)
    x1 = x1[np.isfinite(x1)]
    x0 = x0[np.isfinite(x0)]
    if len(x0) < MIN_GROUP_N or len(x1) < MIN_GROUP_N:
        return float("nan"), "insufficient n"
    p1 = stats.shapiro(x1)[1] if 3 <= len(x1) <= 5000 else 0.0
    p0 = stats.shapiro(x0)[1] if 3 <= len(x0) <= 5000 else 0.0
    if p0 > DEFAULT_ALPHA and p1 > DEFAULT_ALPHA:
        return float(stats.ttest_ind(x0, x1, equal_var=False).pvalue), "welch t"
    return float(stats.mannwhitneyu(x0, x1, alternative="two-sided").pvalue), "mann-whitney"


def _ours_categorical(x: pd.Series, i1: np.ndarray, i0: np.ndarray, yates: bool = False):
    levels = sorted(pd.unique(x.dropna()), key=lambda z: str(z))
    if len(levels) < 2:
        return float("nan"), "single level"
    table = np.array(
        [[int((x.iloc[i0] == lev).sum()), int((x.iloc[i1] == lev).sum())] for lev in levels]
    )
    table = table[table.sum(axis=1) > 0]
    if table.shape[0] < 2:
        return float("nan"), "single level"
    if yates and table.shape == (2, 2):
        return float(stats.chi2_contingency(table, correction=True)[1]), "chi2 yates"
    return float(_categorical_p(table)), "chi2/fisher/mc"


def _tableone_p(tbl: TableOne, variable: str) -> tuple[float, str]:
    frame = tbl.tableone
    pcols = [c for c in frame.columns if isinstance(c, tuple) and c[-1] == "P-Value"]
    if not pcols:
        return float("nan"), "NA"
    pcol = pcols[0]
    for key in frame.index:
        name, _level = key if isinstance(key, tuple) else (key, "")
        if str(name).startswith(variable + ","):
            val = frame.loc[key, pcol]
            if pd.notna(val):
                text = str(val).strip()
                if text.startswith("<"):
                    return 0.0, text
                return float(text), text
    return float("nan"), "NA"


def crosscheck(
    df: pd.DataFrame,
    group_col: str,
    columns: Sequence[str],
    categorical: Sequence[str],
    nonnormal: Optional[Sequence[str]] = None,
    ttest_equal_var: bool = False,
) -> pd.DataFrame:
    """逐变量比对：本仓 `_build_table1` 的判据 vs `tableone`（同配置）。"""
    cats = [c for c in categorical if c in columns]
    nonnormal = list(nonnormal or [])
    tbl = TableOne(
        df,
        columns=list(columns),
        categorical=cats or None,
        groupby=group_col,
        nonnormal=nonnormal,
        pval=True,
        isnull=False,
        ttest_equal_var=ttest_equal_var,
        rename=None,
    )
    i1 = np.flatnonzero(df[group_col] == 1)
    i0 = np.flatnonzero(df[group_col] == 0)
    rows = []
    for var in columns:
        x = df[var]
        if var in cats:
            p_ours, how = _ours_categorical(x, i1, i0)
            reason = REASON_YATES if len(pd.unique(x.dropna())) == 2 else ""
        else:
            p_ours, how = _ours_continuous(x, i1, i0)
            reason = REASON_MW_KW if how == "mann-whitney" else ""
        _p_lib, lib_text = _tableone_p(tbl, var)
        ours_text = _fmt(p_ours)
        verdict = "MATCH" if ours_text == lib_text else "DIFF"
        rows.append(
            {
                "Variable": var,
                "type": "categorical" if var in cats else "continuous",
                "ours_test": how,
                "ours_p": p_ours,
                "ours_display": ours_text,
                "tableone_display": lib_text,
                "verdict": verdict,
                "reason_if_diff": "" if verdict == "MATCH" else reason,
            }
        )
    return pd.DataFrame(rows)


def render(report: pd.DataFrame) -> str:
    lines = [
        "| Variable | type | ours_test | ours_p | ours | tableone | verdict | reason |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for _, r in report.iterrows():
        lines.append(
            f"| {r['Variable']} | {r['type']} | {r['ours_test']} | {r['ours_p']:.10g} | "
            f"{r['ours_display']} | {r['tableone_display']} | {r['verdict']} | {r['reason_if_diff']} |"
        )
    return "\n".join(lines) + "\n"


def _split(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [t.strip() for t in str(value).split(",") if t.strip()]


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Table 1 P values: this repo vs tableone (dev-only)")
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--sheet", default="cln")
    ap.add_argument("--group", default="Group")
    ap.add_argument("--columns", default=None,
                    help="comma separated; defaults to every column except --group")
    ap.add_argument("--categorical", default=None)
    ap.add_argument("--nonnormal", default=None)
    ap.add_argument("--pos-label", default=None,
                    help="raw label that means positive; omit when the group column is already 0/1")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(list(argv) if argv is not None else None)

    df = pd.read_excel(args.xlsx, sheet_name=args.sheet)
    if args.pos_label is not None:
        df = df.copy()
        df[args.group] = (df[args.group].astype(str) == str(args.pos_label)).astype(int)
    else:
        levels = set(pd.unique(df[args.group].dropna()))
        if not levels.issubset({0, 1, 0.0, 1.0}):
            raise ValueError(
                f"group column {args.group!r} has levels {sorted(map(str, levels))}; "
                "pass --pos-label to binarise it"
            )
        df[args.group] = df[args.group].astype(int)
    cols = _split(args.columns) or [c for c in df.columns if c != args.group]
    report = crosscheck(df, args.group, cols, _split(args.categorical), _split(args.nonnormal))
    text = render(report)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("# Table 1 cross-check (this repo vs tableone)\n\n" + text)
        print(f"report written: {args.out}")
    return 1 if int((report["verdict"] != "MATCH").sum()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
