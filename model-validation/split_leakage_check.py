#!/usr/bin/env python3
"""Detect patient-level leakage across train/validation/test splits.

Empty patient IDs or empty split labels FAIL (missing counts reported).
Empty sentinels include None/nan/NA/"None"/"nan"/<NA>/null (unified).
Leaked IDs are hashed by default (pseudonymized, not anonymized);
pass --show-raw-ids to print originals. For external sharing prefer HMAC-with-key.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

# Unified empty sentinels for CSV / DictReader / Excel-ish string cells (N7).
_EMPTY_SENTINELS = {
    "",
    "none",
    "nan",
    "na",
    "<na>",
    "nat",
    "null",
    "n/a",
    "-",
}


def is_empty_field(val) -> bool:
    """True for None, blank, and common string/NaN sentinels (case-insensitive)."""
    if val is None:
        return True
    try:
        if val != val:  # NaN
            return True
    except Exception:
        pass
    s = str(val).strip()
    if not s:
        return True
    return s.lower() in _EMPTY_SENTINELS


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether patient IDs appear in multiple splits."
    )
    parser.add_argument("csv_path", help="CSV file containing patient and split columns.")
    parser.add_argument("--patient-col", default="patient_id", help="Patient ID column name.")
    parser.add_argument("--split-col", default="split", help="Split column name.")
    parser.add_argument(
        "--show-raw-ids",
        action="store_true",
        help="Print original patient IDs in the leak table (default: pseudonymized hash).",
    )
    return parser.parse_args(argv)


def pseudonymize_id(patient: str) -> str:
    """Short SHA256 hex prefix for reports (pseudonymized, not anonymized).

    Predictable IDs can be dictionary-enumerated under plain SHA256. For any
    report shared outside the lab, prefer HMAC-with-key (e.g. HMAC-SHA256 with
    a secret not stored in the repo) instead of this unsalted digest.
    """
    digest = hashlib.sha256(patient.encode("utf-8")).hexdigest()[:12]
    return f"id_{digest}"


def anonymize_id(patient: str) -> str:
    """Back-compat alias for pseudonymize_id."""
    return pseudonymize_id(patient)


def check_rows(
    rows: list[dict],
    *,
    patient_col: str,
    split_col: str,
    show_raw_ids: bool = False,
) -> tuple[int, str]:
    """Return (exit_code, report_text). 0=ok, 1=leak, 2=data FAIL."""
    if not rows:
        return 2, "No rows found."

    missing_cols = [c for c in [patient_col, split_col] if c not in rows[0]]
    if missing_cols:
        return 2, f"Missing column(s): {', '.join(missing_cols)}"

    n = len(rows)
    empty_patient = 0
    empty_split = 0
    patient_splits: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        raw_patient = row.get(patient_col, "")
        raw_split = row.get(split_col, "")
        patient_empty = is_empty_field(raw_patient)
        split_empty = is_empty_field(raw_split)
        if patient_empty:
            empty_patient += 1
        if split_empty:
            empty_split += 1
        if patient_empty or split_empty:
            continue
        patient = str(raw_patient).strip()
        split = str(raw_split).strip().lower()
        patient_splits[patient].add(split)

    lines = [
        "# Split Leakage Check",
        "",
        f"- Rows: {n}",
        f"- Patients with non-empty ID+split: {len(patient_splits)}",
        f"- Empty patient ID rows: {empty_patient} ({100.0 * empty_patient / n:.1f}%)",
        f"- Empty split rows: {empty_split} ({100.0 * empty_split / n:.1f}%)",
    ]

    if empty_patient or empty_split:
        lines.extend(
            [
                "",
                "FAIL: empty patient ID and/or empty split present.",
                "Cannot certify absence of patient-level leakage when IDs or splits are missing.",
            ]
        )
        return 2, "\n".join(lines)

    leaked = {p: sorted(s) for p, s in patient_splits.items() if len(s) > 1}
    lines.append(f"- Patients in multiple splits: {len(leaked)}")
    lines.append("")

    if leaked:
        id_header = "Patient" if show_raw_ids else "Patient (pseudonymized)"
        lines.append(f"| {id_header} | Splits |")
        lines.append("|---|---|")
        for patient, splits in sorted(leaked.items()):
            shown = patient if show_raw_ids else pseudonymize_id(patient)
            lines.append(f"| {shown} | {', '.join(splits)} |")
        lines.append("")
        lines.append(
            "Note: default IDs are pseudonymized (short SHA256), not anonymized. "
            "If sharing this report externally, use HMAC-with-key instead of unsalted SHA256."
        )
        return 1, "\n".join(lines)

    lines.append("No patient-level split leakage detected.")
    return 0, "\n".join(lines)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    path = Path(args.csv_path)
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))
    code, report = check_rows(
        rows,
        patient_col=args.patient_col,
        split_col=args.split_col,
        show_raw_ids=args.show_raw_ids,
    )
    if code == 2 and (
        report.startswith("No rows") or report.startswith("Missing") or report.startswith("File")
    ):
        print(report, file=sys.stderr)
    else:
        print(report)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
