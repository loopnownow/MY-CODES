#!/usr/bin/env python3
"""Train-only 1:2 downsample of Pseudo_progress-Negative.

归属 ``0doc/GitHub/stats-analysis/model-validation``（建模队列构建，非 tidy 清洗）。
Replicates patient-level split (data_io.load_data), then keeps:
  all Positive, all test Negative, and a histology×sex-stratified
  sample of train Negative at 1:2 vs train Positive.

Writes cln+rad only. Does not touch lxf_LG.xlsx, qc, icc, or lxf_js.xlsx.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_STAT = Path(__file__).resolve().parent
_IMPORT = Path(os.environ.get("STATS_ROOT", r"D:\0Grok\0doc\GitHub\stats-analysis"))
_TIDY = Path(os.environ.get(
    "CLEAN_DATA_ROOT",
    str(_IMPORT.parent.parent / "02-data-processing" / "base" / "clean-data"),
))
for p in (_IMPORT, _TIDY):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from tidy_paths import project_root  # noqa: E402

# ==================== CONFIG ====================
PROJECT_DIR = Path(os.environ.get("LXF_PP_ROOT", str(project_root("lxf_PP"))))
SRC_XLSX = PROJECT_DIR / "lxf_LG.xlsx"
OUT_XLSX = PROJECT_DIR / "lxf_PP.xlsx"
KEEP_LIST = PROJECT_DIR / "ref" / "lxf_PP_keep_list.csv"
CLIN_SHEET = "cln"
RAD_SHEET = "rad"
ID_COL = "Patient_ID"
OUTCOME_COL = "Pseudo_progress"
POS_LABEL = "Positive"
NEG_LABEL = "Negative"
STRATA_COLS = ["histology", "sex"]
SEED = 66666
TRAIN_RATIO = 0.7
NEG_PER_POS = 2  # train Negative : train Positive
DRY_RUN = True
# ================================================


def patient_key(label: str) -> str:
    s = str(label).strip().lower()
    s = re.sub(r"\s+\d+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if s else str(label)


def pipeline_split(ids: list[str], y: np.ndarray, ratio: float, seed: int):
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


def main() -> None:
    print("=== PP train 1:2 downsample ===")
    print(f"SRC      : {SRC_XLSX}")
    print(f"OUT      : {OUT_XLSX}")
    print(f"KEEP_LIST: {KEEP_LIST}")
    print(f"DRY_RUN  : {DRY_RUN}")
    print(f"SEED={SEED}  TRAIN_RATIO={TRAIN_RATIO}  NEG_PER_POS={NEG_PER_POS}")

    if not SRC_XLSX.exists():
        raise FileNotFoundError(SRC_XLSX)
    if OUT_XLSX.resolve() == SRC_XLSX.resolve():
        raise RuntimeError("Refusing to overwrite source workbook")

    cln = pd.read_excel(SRC_XLSX, sheet_name=CLIN_SHEET, dtype={ID_COL: str})
    rad = pd.read_excel(SRC_XLSX, sheet_name=RAD_SHEET, dtype={ID_COL: str})
    cln[ID_COL] = cln[ID_COL].astype(str).str.strip()
    rad[ID_COL] = rad[ID_COL].astype(str).str.strip()

    if cln[ID_COL].duplicated().any() or rad[ID_COL].duplicated().any():
        raise RuntimeError("Duplicate Patient_ID in cln or rad")
    if set(cln[ID_COL]) != set(rad[ID_COL]):
        raise RuntimeError("cln/rad Patient_ID sets differ")
    if list(cln[ID_COL]) != list(rad[ID_COL]):
        rad = rad.set_index(ID_COL).loc[cln[ID_COL]].reset_index()
        print("  aligned rad row order to cln")

    y = (cln[OUTCOME_COL].astype(str) == POS_LABEL).astype(int).to_numpy()
    n_pos = int(y.sum())
    n_neg = int((y == 0).sum())
    print(f"source N={len(cln)}  {POS_LABEL}={n_pos}  {NEG_LABEL}={n_neg}")

    train_idx, test_idx, keys = pipeline_split(
        cln[ID_COL].tolist(), y, TRAIN_RATIO, SEED
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
    n_keep_neg = int(len(train_pos) * NEG_PER_POS)
    print(f"target   train neg keep={n_keep_neg} (1:{NEG_PER_POS} vs train pos {len(train_pos)})")

    train_neg_df = cln.iloc[train_neg].copy()
    train_neg_df["_stratum"] = (
        train_neg_df[STRATA_COLS].astype(str).agg(" × ".join, axis=1)
    )
    counts = train_neg_df["_stratum"].value_counts().to_dict()
    alloc = allocate_strata(counts, n_keep_neg)
    print("strata allocation (train Negative):")
    for k in sorted(alloc):
        print(f"  {k}: keep {alloc[k]} / {counts[k]}")

    rng = np.random.RandomState(SEED)
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
    id_pos = list(cln_out.columns).index(ID_COL)
    cln_out.insert(id_pos + 1, "split", split_keep)

    if list(cln_out[ID_COL]) != list(rad_out[ID_COL]):
        raise RuntimeError("output cln/rad ID order mismatch")
    if cln_out[ID_COL].duplicated().any():
        raise RuntimeError("output duplicate IDs")

    manifest = pd.DataFrame(
        {
            ID_COL: cln[ID_COL],
            "patient_key": keys,
            OUTCOME_COL: cln[OUTCOME_COL].astype(str),
            "histology": cln["histology"].astype(str),
            "sex": cln["sex"].astype(str),
            "split": split,
            "kept": np.isin(np.arange(len(cln)), keep_idx),
            "reason": np.where(
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
            ),
        }
    )

    print("output PP counts:")
    print(
        pd.crosstab(cln_out[OUTCOME_COL], cln_out["split"], margins=True).to_string()
    )
    print("output histology × sex × PP (training only):")
    tr = cln_out[cln_out["split"] == "training"]
    print(pd.crosstab([tr["histology"], tr["sex"]], tr[OUTCOME_COL]).to_string())

    if DRY_RUN:
        print("\n=== DRY RUN: no files written ===")
        print(f"would write {OUT_XLSX.name} sheets={CLIN_SHEET},{RAD_SHEET}")
        print(f"would write {KEEP_LIST.name} rows={len(manifest)}")
        return

    KEEP_LIST.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(KEEP_LIST, index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        cln_out.to_excel(w, sheet_name=CLIN_SHEET, index=False)
        rad_out.to_excel(w, sheet_name=RAD_SHEET, index=False)
    print(f"wrote {OUT_XLSX}  cln={cln_out.shape} rad={rad_out.shape}")
    print(f"wrote {KEEP_LIST}")


if __name__ == "__main__":
    main()
