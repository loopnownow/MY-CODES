#!/usr/bin/env python3
"""Train-only stratified downsample of negative class (generic CLI).

Belong in ``model-validation`` (queue construction). Project-specific column
names are CLI/env parameters — do not hardcode a study into the library API.

Requires tidy_paths from clean-data (CLEAN_DATA_ROOT / ODOC_CODE). Dry-run is
the default; pass ``--execute`` to write outputs (no source-edit of DRY_RUN).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_STAT = Path(__file__).resolve().parent
_IMPORT = Path(os.environ.get("STATS_ROOT", str(_STAT.parent)))


def _default_clean_data_root() -> Path:
    env = (os.environ.get("CLEAN_DATA_ROOT") or "").strip()
    if env:
        return Path(env)
    odoc = (os.environ.get("ODOC_CODE") or "").strip()
    if odoc:
        return Path(odoc) / "02-data-processing" / "clean-data"
    # lab-paths: {_code}/02-data-processing/clean-data (not .../base/clean-data)
    guess = _IMPORT.parent.parent / "_code" / "02-data-processing" / "clean-data"
    return guess


def _ensure_tidy_import() -> None:
    roots = [_IMPORT, _default_clean_data_root()]
    for p in roots:
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    try:
        import tidy_paths  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "tidy_paths not found. Set CLEAN_DATA_ROOT or ODOC_CODE to "
            ".../02-data-processing/clean-data (see MY-SKILLS lab-paths.yaml "
            "code_clean_data)."
        ) from exc


def patient_key(label: str) -> str:
    """Normalize a row label for patient-level split.

    Strips a trailing whitespace-separated integer token (e.g. lesion index).
    If that digit sequence is part of the true patient identifier, pass IDs
    that do not match this pattern, or pre-normalize upstream — do not assume
    this rule is safe for every ID scheme.
    """
    s = str(label).strip().lower()
    s = re.sub(r"\s+\d+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if s else str(label)



def _is_missing_id(val) -> bool:
    """True for NaN / None / empty / nan / None / <NA> sentinel IDs."""
    if val is None:
        return True
    try:
        if pd.isna(val):
            return True
    except (TypeError, ValueError):
        pass
    s = str(val).strip()
    if not s:
        return True
    return s.lower() in {"nan", "none", "<na>", "nat", "null"}


def _require_outcome_labels(series: pd.Series, pos_label: str, neg_label: str) -> None:
    """Only {pos,neg} allowed; missing or unknown outcome FAIL before execute."""
    allowed = {str(pos_label), str(neg_label)}
    # Treat NA via pandas, and string sentinels via strip compare
    is_na = series.isna()
    as_str = series.astype(object).where(~is_na, other=None)
    bad_missing = []
    bad_unknown = []
    for i, raw in enumerate(as_str.tolist()):
        if raw is None or _is_missing_id(raw):
            bad_missing.append(i)
            continue
        token = str(raw).strip()
        if token not in allowed:
            bad_unknown.append((i, token))
    if bad_missing or bad_unknown:
        parts = []
        if bad_missing:
            parts.append(f"missing outcome rows={len(bad_missing)} (first idx {bad_missing[:5]})")
        if bad_unknown:
            samples = ", ".join(repr(t) for _, t in bad_unknown[:5])
            parts.append(
                f"unknown outcome labels={len(bad_unknown)} not in {sorted(allowed)} "
                f"(examples: {samples})"
            )
        raise RuntimeError(
            "downsample_train outcome FAIL: " + "; ".join(parts)
            + ". Only {pos,neg} labels allowed; refuse silent encode-as-negative."
        )


def _require_patient_ids(series: pd.Series, id_col: str) -> pd.Series:
    """FAIL on NaN/empty/nan/None/<NA> before astype(str) / split."""
    bad = []
    for i, raw in enumerate(series.tolist()):
        if _is_missing_id(raw):
            bad.append(i)
    if bad:
        raise RuntimeError(
            f"downsample_train patient ID FAIL: {len(bad)} missing/empty "
            f"values in {id_col!r} (first idx {bad[:10]}). "
            "Refuse astype(str) of NaN before patient-level split."
        )
    out = series.astype(str).str.strip()
    # Second pass: catch stringified sentinels that survived
    still = [i for i, v in enumerate(out.tolist()) if _is_missing_id(v)]
    if still:
        raise RuntimeError(
            f"downsample_train patient ID FAIL: {len(still)} sentinel IDs "
            f"in {id_col!r} after strip (first idx {still[:10]})."
        )
    return out


def pipeline_split(ids: list[str], y, ratio: float, seed: int):
    """Match modules.stats.data_io.load_data patient-level stratified split."""
    key_to_idx: dict[str, list[int]] = {}
    key_to_y: dict[str, int] = {}
    keys = [patient_key(x) for x in ids]
    for i, k in enumerate(keys):
        key_to_idx.setdefault(k, []).append(i)
        key_to_y[k] = max(key_to_y.get(k, 0), int(y[i]))
    keys_1 = [k for k, yv in key_to_y.items() if yv == 1]
    keys_0 = [k for k, yv in key_to_y.items() if yv == 0]
    rng = np.random.RandomState(seed)
    n_train_1 = int(np.floor(len(keys_1) * ratio))
    n_train_0 = int(np.floor(len(keys_0) * ratio))
    train_keys = set(
        list(rng.choice(keys_1, size=max(n_train_1, 0), replace=False))
        + list(rng.choice(keys_0, size=max(n_train_0, 0), replace=False))
    )
    train_idx = np.sort(
        np.array(
            [i for k, idxs in key_to_idx.items() if k in train_keys for i in idxs],
            dtype=int,
        )
    )
    test_idx = np.setdiff1d(np.arange(len(ids)), train_idx)
    leak = train_keys & {keys[i] for i in test_idx}
    if leak:
        raise RuntimeError(f"Patient-level split leakage: {len(leak)}")
    return train_idx, test_idx, keys


def allocate_strata(counts: dict[str, int], n_keep: int) -> dict[str, int]:
    total = sum(counts.values())
    if n_keep > total:
        raise ValueError(f"n_keep={n_keep} > available {total}")
    raw = {k: n_keep * v / total for k, v in counts.items()}
    base = {k: min(int(np.floor(raw[k])), counts[k]) for k in counts}
    leftover = n_keep - sum(base.values())
    rem_order = sorted(counts, key=lambda k: (raw[k] - np.floor(raw[k]), k), reverse=True)
    guard = 0
    while leftover > 0:
        progressed = False
        for k in rem_order:
            if leftover <= 0:
                break
            if base[k] < counts[k]:
                base[k] += 1
                leftover -= 1
                progressed = True
        guard += 1
        if not progressed or guard > 10000:
            raise RuntimeError(f"Could not allocate {n_keep}; leftover={leftover}")
    return base


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Train-only stratified downsample of the negative class."
    )
    p.add_argument(
        "--project-root",
        default=os.environ.get("PROJECT_ROOT") or os.environ.get("STUDY_ROOT") or "",
        help="Project folder (default: PROJECT_ROOT / STUDY_ROOT / demo_study via tidy_paths).",
    )
    p.add_argument("--src", default="", help="Source xlsx path (default: <project>/<src-name>).")
    p.add_argument("--out", default="", help="Output xlsx path (default: <project>/<out-name>).")
    p.add_argument("--src-name", default=os.environ.get("SRC_XLSX_NAME", "source.xlsx"))
    p.add_argument("--out-name", default=os.environ.get("OUT_XLSX_NAME", "downsampled.xlsx"))
    p.add_argument(
        "--keep-list-name",
        default=os.environ.get("KEEP_LIST_NAME", "keep_list.csv"),
    )
    p.add_argument("--clin-sheet", default="cln")
    p.add_argument("--rad-sheet", default="rad")
    p.add_argument("--id-col", default="Patient_ID")
    p.add_argument(
        "--outcome-col",
        default=os.environ.get("OUTCOME_COL", "Pseudo_progress"),
        help="Outcome column (project-specific; override for non-demo studies).",
    )
    p.add_argument("--pos-label", default=os.environ.get("POS_LABEL", "Positive"))
    p.add_argument("--neg-label", default=os.environ.get("NEG_LABEL", "Negative"))
    p.add_argument(
        "--strata",
        default=os.environ.get("STRATA_COLS", "histology,sex"),
        help="Comma-separated stratum columns for negative sampling.",
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "66666")))
    p.add_argument("--train-ratio", type=float, default=float(os.environ.get("TRAIN_RATIO", "0.7")))
    p.add_argument("--neg-per-pos", type=int, default=int(os.environ.get("NEG_PER_POS", "2")))
    p.add_argument(
        "--execute",
        action="store_true",
        help="Write output xlsx and keep_list (default is dry-run; no files written).",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    _ensure_tidy_import()
    from tidy_paths import project_root  # noqa: E402

    project_dir = Path(args.project_root or str(project_root("demo_study"))).resolve()
    src_xlsx = Path(args.src) if args.src else project_dir / args.src_name
    out_xlsx = Path(args.out) if args.out else project_dir / args.out_name
    keep_list = project_dir / "ref" / args.keep_list_name
    strata_cols = [c.strip() for c in str(args.strata).split(",") if c.strip()]
    dry_run = not bool(args.execute)

    print("=== train stratified downsample ===")
    print(f"SRC      : {src_xlsx}")
    print(f"OUT      : {out_xlsx}")
    print(f"KEEP_LIST: {keep_list}")
    print(f"DRY_RUN  : {dry_run}  (pass --execute to write)")
    print(
        f"SEED={args.seed}  TRAIN_RATIO={args.train_ratio}  "
        f"NEG_PER_POS={args.neg_per_pos}  OUTCOME={args.outcome_col!r}"
    )
    print(f"STRATA   : {strata_cols}")

    if not src_xlsx.exists():
        raise FileNotFoundError(src_xlsx)
    if out_xlsx.resolve() == src_xlsx.resolve():
        raise RuntimeError("Refusing to overwrite source workbook")

    id_col = args.id_col
    # Do not force dtype=str on read: that would turn real NaN into the string "nan"
    # before we can FAIL missing IDs (N3).
    cln = pd.read_excel(src_xlsx, sheet_name=args.clin_sheet)
    rad = pd.read_excel(src_xlsx, sheet_name=args.rad_sheet)

    for col in [args.outcome_col, *strata_cols]:
        if col not in cln.columns:
            raise KeyError(f"Missing clinical column {col!r}")
    if id_col not in cln.columns:
        raise KeyError(f"Missing clinical ID column {id_col!r}")
    if id_col not in rad.columns:
        raise KeyError(f"Missing radiomics ID column {id_col!r}")

    cln[id_col] = _require_patient_ids(cln[id_col], id_col)
    rad[id_col] = _require_patient_ids(rad[id_col], id_col)
    _require_outcome_labels(cln[args.outcome_col], args.pos_label, args.neg_label)

    if cln[id_col].duplicated().any() or rad[id_col].duplicated().any():
        raise RuntimeError("Duplicate Patient_ID in cln or rad")
    if set(cln[id_col]) != set(rad[id_col]):
        raise RuntimeError("cln/rad Patient_ID sets differ")
    if list(cln[id_col]) != list(rad[id_col]):
        rad = rad.set_index(id_col).loc[cln[id_col]].reset_index()
        print("  aligned rad row order to cln")

    y = (cln[args.outcome_col].astype(str) == args.pos_label).astype(int).to_numpy()
    n_pos = int(y.sum())
    n_neg = int((y == 0).sum())
    print(f"source N={len(cln)}  {args.pos_label}={n_pos}  {args.neg_label}={n_neg}")

    train_idx, test_idx, keys = pipeline_split(
        cln[id_col].tolist(), y, args.train_ratio, args.seed
    )
    split = np.array(["test"] * len(cln), dtype=object)
    split[train_idx] = "training"

    train_pos = train_idx[y[train_idx] == 1]
    train_neg = train_idx[y[train_idx] == 0]
    test_pos = test_idx[y[test_idx] == 1]
    test_neg = test_idx[y[test_idx] == 0]
    print(
        f"split    train {len(train_idx)} (pos={len(train_pos)}, neg={len(train_neg)})  "
        f"test {len(test_idx)} (pos={len(test_pos)}, neg={len(test_neg)})"
    )
    n_keep_neg = int(len(train_pos) * args.neg_per_pos)
    print(
        f"target   train neg keep={n_keep_neg} "
        f"(1:{args.neg_per_pos} vs train pos {len(train_pos)})"
    )

    train_neg_df = cln.iloc[train_neg].copy()
    train_neg_df["_stratum"] = (
        train_neg_df[strata_cols].astype(str).agg(" \u00d7 ".join, axis=1)
    )
    counts = train_neg_df["_stratum"].value_counts().to_dict()
    alloc = allocate_strata(counts, n_keep_neg)
    print("strata allocation (train Negative):")
    for k in sorted(alloc):
        print(f"  {k}: keep {alloc[k]} / {counts[k]}")

    rng = np.random.RandomState(args.seed)
    kept_neg_idx: list[int] = []
    for stratum, n_s in alloc.items():
        pool = train_neg_df.index[train_neg_df["_stratum"] == stratum].to_numpy()
        chosen = rng.choice(pool, size=n_s, replace=False)
        kept_neg_idx.extend(int(i) for i in chosen)
    kept_neg_idx = sorted(kept_neg_idx)
    dropped_neg_idx = sorted(set(train_neg.tolist()) - set(kept_neg_idx))

    keep_idx = np.sort(
        np.concatenate(
            [train_pos, np.array(kept_neg_idx, dtype=int), test_pos, test_neg]
        )
    )
    print(
        f"keep     N={len(keep_idx)}  "
        f"train pos={len(train_pos)} train neg={len(kept_neg_idx)}  "
        f"test pos={len(test_pos)} test neg={len(test_neg)}  "
        f"dropped train neg={len(dropped_neg_idx)}"
    )
    if len(kept_neg_idx) != n_keep_neg:
        raise RuntimeError("train-neg keep count mismatch")

    cln_out = cln.iloc[keep_idx].copy()
    rad_out = rad.iloc[keep_idx].copy()
    split_keep = split[keep_idx]
    if "split" in cln_out.columns:
        raise RuntimeError("cln already has split column")
    id_pos = list(cln_out.columns).index(id_col)
    cln_out.insert(id_pos + 1, "split", split_keep)

    if list(cln_out[id_col]) != list(rad_out[id_col]):
        raise RuntimeError("output cln/rad ID order mismatch")
    if cln_out[id_col].duplicated().any():
        raise RuntimeError("output duplicate IDs")

    manifest_cols = {
        id_col: cln[id_col],
        "patient_key": keys,
        args.outcome_col: cln[args.outcome_col].astype(str),
    }
    for c in strata_cols:
        manifest_cols[c] = cln[c].astype(str)
    manifest_cols["split"] = split
    manifest_cols["kept"] = np.isin(np.arange(len(cln)), keep_idx)
    manifest_cols["reason"] = np.where(
        y == 1,
        "all_positive",
        np.where(
            split == "test",
            "all_test_negative",
            np.where(
                np.isin(np.arange(len(cln)), kept_neg_idx),
                "sampled_train_negative",
                "dropped_train_negative",
            ),
        ),
    )
    manifest = pd.DataFrame(manifest_cols)

    print("output outcome counts:")
    print(
        pd.crosstab(cln_out[args.outcome_col], cln_out["split"], margins=True).to_string()
    )

    if dry_run:
        print("\n=== DRY RUN: no files written ===")
        print(f"would write {out_xlsx.name} sheets={args.clin_sheet},{args.rad_sheet}")
        print(f"would write {keep_list.name} rows={len(manifest)}")
        return 0

    keep_list.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(keep_list, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as w:
        cln_out.to_excel(w, sheet_name=args.clin_sheet, index=False)
        rad_out.to_excel(w, sheet_name=args.rad_sheet, index=False)
    print(f"wrote {out_xlsx}  cln={cln_out.shape} rad={rad_out.shape}")
    print(f"wrote {keep_list}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
