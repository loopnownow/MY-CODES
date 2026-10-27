#!/usr/bin/env python3
"""Group-stratified missing-value imputation CLI.

Wraps ``modules.utils.u_impute.z_impute``: backup, per-sheet run, decimal
alignment, and an ``impute_qc`` sheet. Library code stays in
``modules/utils/u_impute.py``; this file is only the command line.

Usage
-----
    python model-validation/impute_excel.py --input data.xlsx --sheet cln --group Group --method mice
    python model-validation/impute_excel.py --input data.xlsx --all-sheets --method median --output out.xlsx
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.utils.u_impute import mean_decimal_places, z_impute  # noqa: E402

QC_COLUMNS = ["column", "n_miss_before", "n_miss_after", "n_filled", "ndigits_mean_obs"]


def _resolve_sheets(path: Path, sheet: Optional[str], all_sheets: bool) -> List[str]:
    names = list(pd.ExcelFile(path).sheet_names)
    if all_sheets:
        return names
    if sheet is None:
        return names[:1]
    if sheet.isdigit():
        return [names[int(sheet)]]
    if sheet not in names:
        raise ValueError(f"sheet {sheet!r} not in {names}")
    return [sheet]


def _qc_frame(original: pd.DataFrame, imputed: pd.DataFrame, group_col: str) -> pd.DataFrame:
    rows = []
    for column in original.columns:
        if column == group_col or not pd.api.types.is_numeric_dtype(original[column]):
            continue
        before = int(original[column].isna().sum())
        after = int(imputed[column].isna().sum()) if column in imputed.columns else -1
        if before == 0 and after == 0:
            continue
        rows.append(
            {
                "column": column,
                "n_miss_before": before,
                "n_miss_after": after,
                "n_filled": before - after if after >= 0 else None,
                "ndigits_mean_obs": mean_decimal_places(original[column]),
            }
        )
    if not rows:
        return pd.DataFrame(columns=QC_COLUMNS)
    return (
        pd.DataFrame(rows)
        .sort_values(["n_miss_before", "column"], ascending=[False, True])
        .reset_index(drop=True)
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Group-stratified missing-value imputation")
    parser.add_argument("--input", "-i", required=True, help="input Excel path")
    parser.add_argument("--output", "-o", default=None, help="output Excel (default: overwrite input)")
    parser.add_argument("--sheet", "-s", default=None, help="sheet name or 0-based index (default: first)")
    parser.add_argument("--all-sheets", action="store_true", help="impute every sheet that has the group column")
    parser.add_argument("--group", "-g", default="Group", help="group column name")
    parser.add_argument(
        "--method",
        "-m",
        default="mice",
        choices=["median", "mean", "mice", "missForest"],
        help="imputation method",
    )
    parser.add_argument("--seed", type=int, default=66)
    parser.add_argument("--max-iter", type=int, default=10)
    parser.add_argument("--mf-trees", type=int, default=30, help="missForest n_estimators")
    parser.add_argument("--n-nearest", type=int, default=32, help="n_nearest_features; 0 = all")
    parser.add_argument("--no-align-decimals", action="store_true", help="disable decimal alignment")
    parser.add_argument("--include-categorical", action="store_true")
    parser.add_argument("--backup", default=None, help="backup path (default: <stem>_pre_impute beside input)")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--no-qc", action="store_true", help="do not write impute_qc sheet(s)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.input).resolve()
    if not source.is_file():
        print(f"ERROR: not found: {source}", file=sys.stderr)
        return 1

    output = Path(args.output).resolve() if args.output else source
    sheets = _resolve_sheets(source, args.sheet, args.all_sheets)
    n_nearest = None if args.n_nearest == 0 else args.n_nearest
    verbose = not args.quiet

    if not args.no_backup:
        backup = (
            Path(args.backup)
            if args.backup
            else source.with_name(f"{source.stem}_pre_impute{source.suffix}")
        )
        if backup.exists():
            if verbose:
                print(f"backup exists (kept) -> {backup}")
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
            if verbose:
                print(f"backup -> {backup}")

    original_sheets = {name: pd.read_excel(source, sheet_name=name) for name in pd.ExcelFile(source).sheet_names}

    written: dict[str, pd.DataFrame] = {}
    qc_frames: dict[str, pd.DataFrame] = {}
    for sheet in sheets:
        frame = original_sheets[sheet]
        if args.group not in frame.columns:
            if verbose:
                print(f"skip sheet {sheet!r}: no group column {args.group!r}")
            continue
        n_missing = int(frame.isna().sum().sum())
        if n_missing == 0:
            if verbose:
                print(f"skip sheet {sheet!r}: no missing cells")
            written[sheet] = frame
            continue
        if verbose:
            print(f"\n--- sheet {sheet!r} miss_cells={n_missing} ---")
        imputed = z_impute(
            frame,
            group_col=args.group,
            method=args.method,
            seed=args.seed,
            max_iter=args.max_iter,
            mf_n_trees=args.mf_trees,
            n_nearest_features=n_nearest,
            align_decimals=not args.no_align_decimals,
            include_categorical=args.include_categorical,
            # --group is an explicit request: honor it instead of letting the
            # library pool the strata because the name looks outcome-like.
            allow_outcome_stratify=True,
            verbose=verbose,
        )
        if imputed is None:
            print(f"ERROR: z_impute returned None for sheet {sheet}", file=sys.stderr)
            return 2
        written[sheet] = imputed
        if not args.no_qc:
            qc_frames[sheet] = _qc_frame(frame, imputed, args.group)

    final = dict(original_sheets)
    final.update(written)

    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in final.items():
            frame.to_excel(writer, sheet_name=str(name)[:31], index=False)
        if qc_frames and len(qc_frames) == 1:
            next(iter(qc_frames.values())).to_excel(writer, sheet_name="impute_qc", index=False)
        elif qc_frames:
            for sheet, qc in qc_frames.items():
                qc.to_excel(writer, sheet_name=f"qc_{sheet}"[:31], index=False)

    if verbose:
        print(f"\nDone -> {output}")
        for sheet, frame in written.items():
            print(f"  {sheet}: shape={frame.shape}, miss={int(frame.isna().sum().sum())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
