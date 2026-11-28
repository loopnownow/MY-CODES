#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Batch extract bilateral regional mean values from multi-label ROI masks.

ROI labels (typically 1-6) are auto-named by relative spatial position:
  frontal, parietal, temporal, occipital, basal_ganglia, hippocampus.

Left/right: within each label, split by median world-coordinate x
  (high x = Right, low x = Left).

Usage:
  python extract_data_by_roi.py --list combined.csv --output results.xlsx
  python extract_data_by_roi.py --list combined.csv --dry-run
  python extract_data_by_roi.py --list combined.csv --img-col img_path --roi-col roi_path

All tunable parameters are soft-coded in the CONFIG dict below (edit there first).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import nibabel as nib
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "nibabel is required. Install with: pip install nibabel"
    ) from e


# ======================================================================
# CONFIG -- soft-coded parameters. Edit here instead of hunting magic
# values inside the logic below.
# ======================================================================
CONFIG: dict[str, Any] = {
    # Column-name aliases used to auto-discover columns in the manifest.
    # Matching is case-insensitive and exact (whole-token), so include the
    # full column header as it actually appears in the CSV/XLSX.
    "image_aliases": {
        "image", "img", "cbf", "cbf_path", "image_path", "path_image",
        "img_path", "perf", "perf_path",
    },
    "roi_aliases": {
        "roi", "mask", "roi_path", "path_roi", "label", "seg", "seg_path",
    },
    "id_aliases": {
        "id", "subject", "subject_id", "case", "case_id", "name",
    },

    # Expected number of non-zero ROI labels per mask.
    "expected_num_labels": 6,

    # Canonical region order (also drives output column order).
    "regions": [
        "frontal", "parietal", "temporal",
        "occipital", "basal_ganglia", "hippocampus",
    ],

    # Default output filename (overridable via --output).
    "default_output": "roi_results.xlsx",
}


# ----------------------------------------------------------------------
# Column resolution helpers
# ----------------------------------------------------------------------
def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading/trailing whitespace from every column header."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_column(columns: list[str], aliases: set[str]) -> str | None:
    """Return the original-cased column name whose lowercased form
    exactly matches one of the lowercased aliases. ``None`` if no match.
    """
    lower_map = {c.lower(): c for c in columns}
    for a in aliases:
        if a.lower() in lower_map:
            return lower_map[a.lower()]
    return None


def load_manifest(
    path: Path,
    img_col: str | None = None,
    roi_col: str | None = None,
    id_col: str | None = None,
) -> pd.DataFrame:
    """Load a CSV/XLSX manifest and normalise it to columns
    ``image``, ``roi`` and (optionally) ``id``.

    Column detection order for each logical field:
        1. explicit override passed by the caller (CLI --img-col etc.)
        2. alias-set auto-discovery against CONFIG
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
    elif suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    else:
        raise ValueError(
            f"Unsupported manifest format: {suffix} (use .csv or .xlsx)"
        )

    df = _normalize_columns(df)
    if df.empty:
        raise ValueError("Manifest is empty")

    img_col = img_col or _find_column(list(df.columns), CONFIG["image_aliases"])
    roi_col = roi_col or _find_column(list(df.columns), CONFIG["roi_aliases"])
    id_col = id_col or _find_column(list(df.columns), CONFIG["id_aliases"])

    if img_col is None or roi_col is None:
        raise ValueError(
            "Manifest must contain image and roi path columns. "
            f"Got columns: {list(df.columns)}. "
            f"Use --img-col / --roi-col to override if auto-discovery fails."
        )

    out = pd.DataFrame(
        {
            "image": df[img_col].astype(str).str.strip(),
            "roi": df[roi_col].astype(str).str.strip(),
        }
    )
    if id_col is not None:
        out["id"] = df[id_col].astype(str).str.strip()
    else:
        # Derive an id from the image filename (strip known extensions).
        out["id"] = out["image"].map(
            lambda p: Path(p).name.replace(".nii.gz", "").replace(".nii", "")
        )

    # Drop fully empty / placeholder path rows.
    out = out[out["image"].notna() & out["roi"].notna()]
    out = out[~out["image"].isin(["", "nan", "None"])]
    out = out[~out["roi"].isin(["", "nan", "None"])]
    out = out.reset_index(drop=True)
    return out


# ----------------------------------------------------------------------
# NIfTI helpers
# ----------------------------------------------------------------------
def load_nifti(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (data, affine) as a 3-D float array.

    Singleton dims are squeezed first.  If the result is still 4-D (e.g.
    multi-volume IVIM / perfusion images), the first volume is taken with
    a printed warning so the 4th dimension is never silently mis-used.
    Raises ValueError for any other unexpected dimensionality.
    """
    img = nib.load(str(path))
    data = np.asanyarray(img.dataobj, dtype=np.float64)
    data = np.squeeze(data)

    if data.ndim == 4:
        n_vol = data.shape[-1]
        print(
            f"  WARN: 4-D file detected (shape={data.shape}); "
            f"using first volume out of {n_vol}.",
            flush=True,
        )
        data = data[..., 0]
    elif data.ndim != 3:
        raise ValueError(
            f"Expected a 3-D image after squeeze, got {data.ndim}-D "
            f"(shape={data.shape}) for file {path}"
        )
    return data, np.asarray(img.affine, dtype=np.float64)


def voxel_world_coords(ijk: np.ndarray, affine: np.ndarray) -> np.ndarray:
    """Convert (N, 3) voxel indices to (N, 3) world coordinates (mm)."""
    ones = np.ones((len(ijk), 1), dtype=np.float64)
    ijk1 = np.hstack([ijk.astype(np.float64), ones])
    return (affine @ ijk1.T).T[:, :3]


# ----------------------------------------------------------------------
# Label auto-naming
# ----------------------------------------------------------------------
def auto_name_labels(label_stats: dict[int, dict[str, float]]) -> dict[int, str]:
    """Map label id -> region name by relative spatial position.

    Requires exactly ``CONFIG['expected_num_labels']`` labels.
    """
    expected = CONFIG["expected_num_labels"]
    if len(label_stats) != expected:
        raise ValueError(
            f"Expected {expected} ROI labels, got {len(label_stats)}: "
            f"{sorted(label_stats)}"
        )

    remaining = set(label_stats.keys())

    # Highest y = frontal; lowest y = occipital.
    frontal = max(remaining, key=lambda lab: label_stats[lab]["y"])
    remaining.remove(frontal)

    occipital = min(remaining, key=lambda lab: label_stats[lab]["y"])
    remaining.remove(occipital)

    # Highest z = parietal.
    parietal = max(remaining, key=lambda lab: label_stats[lab]["z"])
    remaining.remove(parietal)

    last3 = list(remaining)
    # Lowest z, then smallest x-span (more medial) = hippocampus.
    hippocampus = min(
        last3, key=lambda lab: (label_stats[lab]["z"], label_stats[lab]["x_ptp"])
    )
    remaining.remove(hippocampus)

    last2 = list(remaining)
    # Largest x-span = temporal.
    temporal = max(last2, key=lambda lab: label_stats[lab]["x_ptp"])
    remaining.remove(temporal)

    basal_ganglia = remaining.pop()

    mapping = {
        frontal: "frontal",
        parietal: "parietal",
        temporal: "temporal",
        occipital: "occipital",
        basal_ganglia: "basal_ganglia",
        hippocampus: "hippocampus",
    }
    if len(set(mapping.values())) != expected:
        raise RuntimeError(f"Non-unique region mapping: {mapping}")
    return mapping


# ----------------------------------------------------------------------
# Per-case extraction
# ----------------------------------------------------------------------
def extract_one(
    image_path: Path,
    roi_path: Path,
    case_id: str,
    swap_lr: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any], None] | tuple[None, None, str]:
    """Extract per-region left/right/bilat means for one image+roi pair.

    Returns (long_rows, summary_row, error_message).
    On success error_message is None; on failure long/summary are None.
    """
    if not image_path.exists():
        return None, None, f"Image not found: {image_path}"
    if not roi_path.exists():
        return None, None, f"ROI not found: {roi_path}"

    try:
        img, img_aff = load_nifti(image_path)
        roi, _ = load_nifti(roi_path)
    except Exception as e:
        return None, None, f"Failed to load NIfTI: {e}"

    if img.shape != roi.shape:
        return None, None, (
            f"Shape mismatch: image {img.shape} vs roi {roi.shape}"
        )

    # Integer labels.
    roi_round = np.rint(roi).astype(np.int32)
    labels = sorted(int(x) for x in np.unique(roi_round) if int(x) != 0)
    if len(labels) != CONFIG["expected_num_labels"]:
        return None, None, (
            f"Expected {CONFIG['expected_num_labels']} non-zero ROI labels, "
            f"got {len(labels)}: {labels}"
        )

    affine = img_aff  # image and roi share geometry (shape already verified)
    label_stats: dict[int, dict[str, float]] = {}
    label_voxels: dict[int, np.ndarray] = {}

    for lab in labels:
        idx = np.argwhere(roi_round == lab)  # (N, 3)
        if idx.size == 0:
            return None, None, f"Empty label {lab}"
        xyz = voxel_world_coords(idx, affine)
        label_voxels[lab] = idx
        label_stats[lab] = {
            "y": float(xyz[:, 1].mean()),
            "z": float(xyz[:, 2].mean()),
            "x": float(xyz[:, 0].mean()),
            "x_ptp": float(np.ptp(xyz[:, 0])),
            "n": float(len(idx)),
        }

    try:
        lab_to_region = auto_name_labels(label_stats)
    except Exception as e:
        return None, None, f"Auto-naming failed: {e}"

    long_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "id": case_id,
        "image": str(image_path),
        "roi": str(roi_path),
        "status": "ok",
        "note": "",
    }
    notes: list[str] = []

    for lab, region in sorted(
        lab_to_region.items(), key=lambda kv: CONFIG["regions"].index(kv[1])
    ):
        idx = label_voxels[lab]
        xyz = voxel_world_coords(idx, affine)
        med_x = float(np.median(xyz[:, 0]))
        high_x = xyz[:, 0] >= med_x  # Right
        low_x = ~high_x  # Left

        if swap_lr:
            high_x, low_x = low_x, high_x

        idx_r = idx[high_x]
        idx_l = idx[low_x]

        def _mean_at(sel_idx: np.ndarray) -> float:
            if len(sel_idx) == 0:
                return float("nan")
            return float(np.mean(img[sel_idx[:, 0], sel_idx[:, 1], sel_idx[:, 2]]))

        mean_l = _mean_at(idx_l)
        mean_r = _mean_at(idx_r)
        mean_bilat = float(np.mean(img[idx[:, 0], idx[:, 1], idx[:, 2]]))
        n_l, n_r = int(len(idx_l)), int(len(idx_r))

        if n_l == 0 or n_r == 0:
            notes.append(f"{region}: empty side (n_L={n_l}, n_R={n_r})")

        long_rows.append(
            {
                "id": case_id,
                "region": region,
                "label": lab,
                "mean_L": mean_l,
                "mean_R": mean_r,
                "mean_bilat": mean_bilat,
                "n_L": n_l,
                "n_R": n_r,
                "centroid_x": label_stats[lab]["x"],
                "centroid_y": label_stats[lab]["y"],
                "centroid_z": label_stats[lab]["z"],
                "image": str(image_path),
                "roi": str(roi_path),
            }
        )

        summary[f"{region}_L"] = mean_l
        summary[f"{region}_R"] = mean_r
        summary[f"{region}_bilat"] = mean_bilat

    if notes:
        summary["note"] = "; ".join(notes)
        summary["status"] = "ok_with_warning"

    summary["_mapping"] = {
        str(lab): region for lab, region in lab_to_region.items()
    }
    return long_rows, summary, None


# ----------------------------------------------------------------------
# Output builders
# ----------------------------------------------------------------------
def build_summary_df(summaries: list[dict[str, Any]]) -> pd.DataFrame:
    value_cols: list[str] = []
    for r in CONFIG["regions"]:
        value_cols.extend([f"{r}_L", f"{r}_R", f"{r}_bilat"])

    base_cols = ["id"] + value_cols + ["status", "note", "image", "roi"]
    rows = [{c: s.get(c, np.nan) for c in base_cols} for s in summaries]
    return pd.DataFrame(rows, columns=base_cols)


# ----------------------------------------------------------------------
# Dry-run validation (no output file written)
# ----------------------------------------------------------------------
def dry_run(
    list_path: Path,
    img_col: str | None,
    roi_col: str | None,
    id_col: str | None,
) -> tuple[int, int]:
    """Validate every row of the manifest without writing output.

    Returns (n_ok, n_error).
    """
    manifest = load_manifest(list_path, img_col, roi_col, id_col)
    n_ok, n_err = 0, 0
    rows: list[dict[str, Any]] = []

    print(f"\n[dry-run] manifest: {list_path}")
    print(f"[dry-run] rows to process: {len(manifest)}")
    print("-" * 90)

    for i, row in manifest.iterrows():
        case_id = str(row["id"])
        image_path = Path(str(row["image"]))
        roi_path = Path(str(row["roi"]))

        err = None
        if not image_path.exists():
            err = f"Image not found: {image_path}"
        elif not roi_path.exists():
            err = f"ROI not found: {roi_path}"
        else:
            try:
                img, img_aff = load_nifti(image_path)
                roi, _ = load_nifti(roi_path)
                if img.shape != roi.shape:
                    err = f"Shape mismatch: {img.shape} vs {roi.shape}"
                else:
                    roi_round = np.rint(roi).astype(np.int32)
                    labels = sorted(
                        int(x)
                        for x in np.unique(roi_round)
                        if int(x) != 0
                    )
                    if len(labels) != CONFIG["expected_num_labels"]:
                        err = (
                            f"Expected {CONFIG['expected_num_labels']} labels, "
                            f"got {len(labels)}: {labels}"
                        )
            except Exception as e:
                err = f"NIfTI load error: {e}"

        rows.append(
            {
                "id": case_id,
                "image": str(image_path),
                "roi": str(roi_path),
                "status": "ok" if err is None else err,
            }
        )
        if err is None:
            n_ok += 1
        else:
            n_err += 1

    report = pd.DataFrame(rows)
    # Truncate long paths for display.
    disp = report.copy()
    for col in ("image", "roi"):
        disp[col] = disp[col].map(lambda p: Path(p).name if p else p)
    print(disp.to_string(index=False))
    print("-" * 90)
    print(f"[dry-run] OK: {n_ok}  ERROR: {n_err}")
    if n_err:
        print(
            "[dry-run] Errors found - output file would NOT be valid. "
            "Fix issues before running without --dry-run."
        )
    else:
        print("[dry-run] All rows pass validation - no output file written.")

    return n_ok, n_err


# ----------------------------------------------------------------------
# Main batch runner (writes output)
# ----------------------------------------------------------------------
def run(
    list_path: Path,
    output_path: Path,
    swap_lr: bool = False,
    img_col: str | None = None,
    roi_col: str | None = None,
    id_col: str | None = None,
) -> Path:
    manifest = load_manifest(list_path, img_col, roi_col, id_col)

    long_all: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for i, row in manifest.iterrows():
        case_id = str(row["id"])
        image_path = Path(str(row["image"]))
        roi_path = Path(str(row["roi"]))
        print(f"[{i + 1}/{len(manifest)}] {case_id} ...", flush=True)

        long_rows, summary, err = extract_one(
            image_path, roi_path, case_id, swap_lr=swap_lr
        )
        if err is not None:
            print(f"  ERROR: {err}", flush=True)
            errors.append(
                {
                    "id": case_id,
                    "image": str(image_path),
                    "roi": str(roi_path),
                    "error": err,
                }
            )
            continue

        assert long_rows is not None and summary is not None
        long_all.extend(long_rows)
        mapping = summary.pop("_mapping", {})
        summaries.append(summary)
        for lab_str, region in sorted(mapping.items(), key=lambda kv: int(kv[0])):
            mapping_rows.append(
                {
                    "id": case_id,
                    "label": int(lab_str),
                    "region": region,
                }
            )
        print(f"  OK  mapping={mapping}", flush=True)

    summary_df = build_summary_df(summaries)
    long_df = pd.DataFrame(long_all)
    if not long_df.empty:
        long_df["region"] = pd.Categorical(
            long_df["region"], categories=CONFIG["regions"], ordered=True
        )
        long_df = long_df.sort_values(["id", "region"]).reset_index(drop=True)
        long_df["region"] = long_df["region"].astype(str)

    mapping_df = pd.DataFrame(mapping_rows)
    errors_df = pd.DataFrame(errors, columns=["id", "image", "roi", "error"])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        long_df.to_excel(writer, sheet_name="long", index=False)
        mapping_df.to_excel(writer, sheet_name="mapping_check", index=False)
        errors_df.to_excel(writer, sheet_name="errors", index=False)

    print(f"\nDone. Wrote: {output_path}")
    print(f"  success: {len(summaries)}  failed: {len(errors)}")
    return output_path


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Extract bilateral regional means from multi-label ROIs."
    )
    p.add_argument(
        "--list", required=True,
        help="Input CSV/XLSX manifest with image & roi path columns.",
    )
    p.add_argument(
        "--output", default=CONFIG["default_output"],
        help=f"Output Excel path (default: {CONFIG['default_output']})",
    )
    p.add_argument(
        "--img-col", default=None,
        help="Override image-path column name (skip auto-discovery).",
    )
    p.add_argument(
        "--roi-col", default=None,
        help="Override roi-path column name (skip auto-discovery).",
    )
    p.add_argument(
        "--id-col", default=None,
        help="Override subject-id column name (skip auto-discovery).",
    )
    p.add_argument(
        "--swap-lr", action="store_true",
        help="Swap left/right if your NIfTI x-axis is inverted.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Validate manifest + files only; write no output.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.dry_run:
        try:
            n_ok, n_err = dry_run(
                Path(args.list),
                args.img_col,
                args.roi_col,
                args.id_col,
            )
        except Exception as e:
            print(f"Fatal: {e}", file=sys.stderr)
            return 1
        return 0 if n_err == 0 else 1

    try:
        run(
            list_path=Path(args.list),
            output_path=Path(args.output),
            swap_lr=args.swap_lr,
            img_col=args.img_col,
            roi_col=args.roi_col,
            id_col=args.id_col,
        )
    except Exception as e:
        print(f"Fatal: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
