"""Recalculate every formula in an .xlsx with headless LibreOffice.

openpyxl writes formulas as strings and leaves no cached value, so a workbook the
lab hands to Excel opens with blanks until Excel recalculates. This runs the real
engine and then scans every cell for Excel error literals, so a broken reference is
reported here instead of by the user.

Only useful when the workbook actually contains formulas. The lab's analysis writers
(`modules/stats/*`, `modules/utils/u_impute.py`) emit values, not formulas, so this is
for hand-built workbooks — a template, a derived table, a submission workbook with a
SUM row — not for the pipeline output.

Usage
-----
    python model-validation/recalc_xlsx.py book.xlsx
    python model-validation/recalc_xlsx.py book.xlsx --timeout 30 --json

Requires LibreOffice (`soffice`) on PATH. Fails loudly if it is missing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

from openpyxl import load_workbook

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from modules.utils.soffice_env import run_soffice  # noqa: E402

ERROR_LITERALS = ("#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NULL!", "#NUM!")
FORMULA_PREFIX = "="


def _recalculate(path: Path, timeout: int) -> int:
    result = run_soffice(
        [
            "--headless",
            "--calc",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(path.parent),
            str(path),
        ]
    )
    if result.returncode != 0:
        print(
            f"ERROR: LibreOffice exited {result.returncode}: "
            f"{(result.stderr or result.stdout or '').strip()[:300]}",
            file=sys.stderr,
        )
    return result.returncode


def _scan(path: Path) -> dict:
    """Count formulas, collect error literals, and flag formulas with no cached value.

    A formula cell with no cached result has never been calculated by a real engine
    (openpyxl writes strings only). Reporting that as "success" would be a false
    pass, so uncached cells are counted separately and force a non-success status.
    """
    workbook = load_workbook(path, data_only=True)
    formula_book = load_workbook(path, data_only=False)

    n_formulas = 0
    n_uncached = 0
    errors: dict[str, list[str]] = {}

    for name in formula_book.sheetnames:
        sheet = formula_book[name]
        values = workbook[name]
        for row in sheet.iter_rows():
            for cell in row:
                raw = cell.value
                computed = values[cell.coordinate].value

                if isinstance(raw, str) and raw.startswith(FORMULA_PREFIX):
                    n_formulas += 1
                    if computed is None:
                        n_uncached += 1
                    elif isinstance(computed, str) and computed in ERROR_LITERALS:
                        errors.setdefault(computed, []).append(f"{name}!{cell.coordinate}")
                    continue

                # A literal error string (typed by hand or pasted from a failed calc)
                # shows up identically to a formula error and must be caught too.
                if isinstance(raw, str) and raw in ERROR_LITERALS:
                    errors.setdefault(raw, []).append(f"{name}!{cell.coordinate}")

    if errors:
        status = "errors_found"
    elif n_uncached:
        status = "uncalculated"
    else:
        status = "success"

    return {
        "status": status,
        "total_errors": sum(len(v) for v in errors.values()),
        "total_formulas": n_formulas,
        "total_uncached": n_uncached,
        "error_summary": {
            literal: {"count": len(cells), "locations": cells[:20]}
            for literal, cells in sorted(errors.items())
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Recalculate an .xlsx with LibreOffice and scan for errors")
    parser.add_argument("xlsx", help="workbook to recalculate (rewritten in place)")
    parser.add_argument("--timeout", type=int, default=60, help="LibreOffice timeout in seconds")
    parser.add_argument("--json", action="store_true", help="print the JSON report only")
    parser.add_argument("--check-only", action="store_true", help="scan without recalculating")
    args = parser.parse_args(argv)

    path = Path(args.xlsx).resolve()
    if not path.is_file():
        print(f"ERROR: not found: {path}", file=sys.stderr)
        return 1

    if not args.check_only:
        rc = _recalculate(path, args.timeout)
        if rc != 0:
            return rc

    report = _scan(path)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"status: {report['status']}")
        print(f"formulas: {report['total_formulas']}  errors: {report['total_errors']}")
        if report["total_uncached"]:
            print(
                f"  {report['total_uncached']} formula cell(s) have no cached value - "
                "run without --check-only so LibreOffice computes them"
            )
        for literal, detail in report["error_summary"].items():
            print(f"  {literal} x{detail['count']}: {', '.join(detail['locations'][:5])}")

    if report["status"] == "success":
        return 0
    return 2 if report["status"] == "errors_found" else 3


if __name__ == "__main__":
    raise SystemExit(main())
