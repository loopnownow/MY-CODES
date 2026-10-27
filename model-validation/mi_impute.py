"""Multiple imputation + Rubin pooling CLI.

Writes the `m` completed datasets plus a pooling helper. Use this when a reported
estimate needs a confidence interval that accounts for the missing data — a single
imputed table gives a too-small SE and a biased point estimate.

Usage
-----
    # draw m completed datasets
    python model-validation/mi_impute.py draw --input cln.xlsx --group Group -m 20 --out-dir mi/

    # pool one parameter across the draws (long table: term, estimate, variance)
    python model-validation/mi_impute.py pool --table estimates.csv --n-complete 150
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.utils.u_mi import MI_DRAWS, rubin_pool_frame, z_impute_mi  # noqa: E402


def _cmd_draw(args) -> int:
    source = Path(args.input).resolve()
    if not source.is_file():
        print(f"ERROR: not found: {source}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    draws = z_impute_mi(
        str(source),
        m=args.m,
        sheet=args.sheet,
        group_col=args.group,
        method=args.method,
        seed=args.seed,
        max_iter=args.max_iter,
        include_categorical=args.include_categorical,
        verbose=not args.quiet,
    )

    for index, frame in enumerate(draws, start=1):
        target = out_dir / f"imputed_{index:02d}.csv"
        frame.to_csv(target, index=False)
    print(f"wrote {len(draws)} datasets to {out_dir}")
    print(
        "Next: fit your model in each dataset, collect term/estimate/variance rows, "
        "then run `mi_impute.py pool --table <csv> --n-complete <n>`."
    )
    return 0


def _cmd_pool(args) -> int:
    table_path = Path(args.table).resolve()
    if not table_path.is_file():
        print(f"ERROR: not found: {table_path}", file=sys.stderr)
        return 1

    table = pd.read_csv(table_path) if table_path.suffix.lower() == ".csv" else pd.read_excel(table_path)

    counts = table.groupby(args.term_col).size() if args.term_col in table.columns else None
    if counts is not None and int(counts.min()) < 2:
        thin = counts[counts < 2]
        print(
            "ERROR: every term needs at least 2 imputations to pool; found "
            + ", ".join(f"{term}={n}" for term, n in thin.items()),
            file=sys.stderr,
        )
        return 1

    try:
        pooled = rubin_pool_frame(
            table,
            estimate_col=args.estimate_col,
            variance_col=args.variance_col,
            term_col=args.term_col,
            n_complete=args.n_complete,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    target = Path(args.out).resolve() if args.out else table_path.with_name(
        f"{table_path.stem}_pooled.csv"
    )
    pooled.to_csv(target, index=False)

    with pd.option_context("display.width", 140, "display.max_columns", 20):
        print(pooled.round(4).to_string(index=False))
    print(f"\nwrote {target}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multiple imputation draws and Rubin pooling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    draw = sub.add_parser("draw", help="write m completed datasets")
    draw.add_argument("--input", "-i", required=True, help="input xlsx")
    draw.add_argument("--sheet", "-s", default=None, help="sheet name (default: first)")
    draw.add_argument("--group", "-g", default="Group", help="group column")
    draw.add_argument("--method", "-m2", default="iterative", choices=["iterative", "missForest"])
    draw.add_argument("-m", type=int, default=MI_DRAWS, help=f"number of draws (default {MI_DRAWS})")
    draw.add_argument("--seed", type=int, default=66)
    draw.add_argument("--max-iter", type=int, default=10)
    draw.add_argument("--include-categorical", action="store_true")
    draw.add_argument("--out-dir", "-o", required=True, help="directory for imputed_XX.csv")
    draw.add_argument("--quiet", action="store_true")
    draw.set_defaults(func=_cmd_draw)

    pool = sub.add_parser("pool", help="Rubin-pool a long estimates table")
    pool.add_argument("--table", "-t", required=True, help="csv/xlsx with term,estimate,variance")
    pool.add_argument("--term-col", default="term")
    pool.add_argument("--estimate-col", default="estimate")
    pool.add_argument("--variance-col", default="variance")
    pool.add_argument("--n-complete", type=int, default=None, help="complete-data n for Barnard-Rubin df")
    pool.add_argument("--out", "-o", default=None, help="output csv")
    pool.set_defaults(func=_cmd_pool)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
