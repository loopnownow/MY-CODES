#!/usr/bin/env python
"""Cross-series Dice consistency for ZS predictions.

For every case with both a primary and a check series, resample the check prediction onto the
primary grid (nearest neighbour, same geometry as the primary image) and compute Dice.

Groups are reported separately because the check series belongs to a different period:
  plain-venous / plain-unknown / plain-plain / plain-arterial
The main threshold is the p10 of the largest group (plain-venous). plain-plain is also checked
against a hard 0.9 floor (same-period repeats should agree almost perfectly).

Writes CROSS-DICE-<date>.tsv and dice-threshold.json into --out-dir. Read-only over images.

Usage
  python cross_dice.py --pred-dir <dir>                # report + write tsv
  python cross_dice.py --pred-dir <dir> --stats        # print only
"""
from __future__ import annotations
import os

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

CONFIG = {
    "pred_dir": os.environ.get("CONFIG", ""),
    "phase_map": r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ZS-PHASE-MAP.tsv",
    "img_root": os.environ.get("CONFIG_2", ""),
    "out_dir": os.environ.get("CONFIG_3", ""),
    "main_group": "plain-venous",
    "main_percentile": 10,
    "plain_plain_floor": 0.9,
}


def dice_from_arrays(a: np.ndarray, b: np.ndarray) -> float:
    a = a > 0
    b = b > 0
    na, nb = int(a.sum()), int(b.sum())
    if na == 0 and nb == 0:
        return float("nan")
    if na == 0 or nb == 0:
        return 0.0
    return 2.0 * float((a & b).sum()) / (na + nb)


def resample_to(ref_path: Path, mov_path: Path) -> np.ndarray:
    """Nearest-neighbour resample mov onto ref grid; returns binary array on ref geometry."""
    import SimpleITK as sitk

    ref = sitk.ReadImage(str(ref_path))
    mov = sitk.ReadImage(str(mov_path))
    if ref.GetSize() == mov.GetSize() and np.allclose(ref.GetSpacing(), mov.GetSpacing()) \
            and np.allclose(ref.GetOrigin(), mov.GetOrigin()) and np.allclose(ref.GetDirection(), mov.GetDirection()):
        arr = sitk.GetArrayFromImage(mov)
    else:
        out = sitk.Resample(mov, ref, sitk.Transform(), sitk.sitkNearestNeighbor,
                            0, sitk.sitkUInt8)
        arr = sitk.GetArrayFromImage(out)
    return arr


def load_phase_map(path: Path) -> dict[str, dict[str, dict]]:
    """sample -> seq -> row"""
    out: dict[str, dict[str, dict]] = defaultdict(dict)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[row["sample"]][row["seq"]] = row
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-series Dice for ZS predictions")
    ap.add_argument("--pred-dir", default=CONFIG["pred_dir"])
    ap.add_argument("--phase-map", default=CONFIG["phase_map"])
    ap.add_argument("--out-dir", default=CONFIG["out_dir"])
    ap.add_argument("--stats", action="store_true", help="print only, do not write")
    args = ap.parse_args(argv)

    pred_dir = Path(args.pred_dir)
    if not pred_dir.is_dir():
        print(f"[FAIL] prediction dir missing: {pred_dir}")
        return 1
    pmap = load_phase_map(Path(args.phase_map))

    rows_out: list[list] = []
    groups: dict[str, list[float]] = defaultdict(list)
    missing: list[str] = []
    t0 = time.time()
    done = 0
    for sample, seqs in sorted(pmap.items()):
        prim = [s for s, r in seqs.items() if str(r.get("pick_primary")) == "1"]
        chk = [s for s, r in seqs.items() if str(r.get("pick_check")) == "1"]
        if not prim or not chk:
            continue
        ps, cs = prim[0], chk[0]
        p_pred = pred_dir / f"{sample}_{ps}.nii.gz"
        c_pred = pred_dir / f"{sample}_{cs}.nii.gz"
        if not p_pred.exists() or not c_pred.exists():
            missing.append(f"{sample} ({ps}/{cs})")
            continue
        a = resample_to(p_pred, p_pred)
        b = resample_to(p_pred, c_pred)
        d = dice_from_arrays(a, b)
        p_period = seqs[ps].get("period", "")
        c_period = seqs[cs].get("period", "")
        grp = f"{p_period}-{c_period}"
        groups[grp].append(d)
        rows_out.append([sample, ps, cs, p_period, c_period, grp,
                         int((a > 0).sum()), int((b > 0).sum()), round(d, 4)])
        done += 1
        if done % 50 == 0:
            print(f"[dice] {done} cases  elapsed {time.time()-t0:.0f}s", flush=True)

    if missing:
        print(f"[warn] {len(missing)} case(s) missing predictions: {missing[:8]}")

    print(f"[info] computed {done} case(s)")
    summary = {}
    for grp, vals in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        v = np.array([x for x in vals if not np.isnan(x)])
        if v.size == 0:
            continue
        pct = {p: round(float(np.percentile(v, p)), 4) for p in (5, 10, 25, 50, 75, 90)}
        summary[grp] = {"n": int(v.size), "min": round(float(v.min()), 4),
                        "max": round(float(v.max()), 4), "mean": round(float(v.mean()), 4),
                        "percentiles": pct}
        print(f"[grp] {grp:22s} n={v.size:4d}  p10={pct[10]:.3f}  median={pct[50]:.3f}  "
              f"min={v.min():.3f}  max={v.max():.3f}")

    main_grp = CONFIG["main_group"]
    threshold = None
    source = None
    if main_grp in summary and summary[main_grp]["n"] >= 5:
        threshold = summary[main_grp]["percentiles"][CONFIG["main_percentile"]]
        source = f"{main_grp}.p{CONFIG['main_percentile']}"
    elif summary:
        biggest = max(summary.items(), key=lambda kv: kv[1]["n"])[0]
        threshold = summary[biggest]["percentiles"][CONFIG["main_percentile"]]
        source = f"{biggest}.p{CONFIG['main_percentile']} (fallback: {main_grp} too small)"
    print(f"[threshold] {threshold}  source={source}")

    if "plain-plain" in summary:
        pp = summary["plain-plain"]
        if pp["percentiles"][50] < CONFIG["plain_plain_floor"]:
            print(f"[WARN] plain-plain median {pp['percentiles'][50]:.3f} < "
                  f"{CONFIG['plain_plain_floor']}: duplicate plain series may be misaligned")

    if args.stats:
        print("[stats] nothing written")
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    tsv = out_dir / f"CROSS-DICE-{stamp}.tsv"
    with open(tsv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample", "seq_primary", "seq_check", "period_primary", "period_check",
                    "group", "n_vox_primary", "n_vox_check", "dice"])
        w.writerows(rows_out)
    thr = out_dir / "dice-threshold.json"
    with open(thr, "w", encoding="utf-8") as f:
        json.dump({"threshold": threshold, "source": source, "groups": summary,
                   "plain_plain_floor": CONFIG["plain_plain_floor"],
                   "computed_cases": done, "missing": len(missing)}, f, indent=2)
    print(f"[done] {len(rows_out)} rows -> {tsv}")
    print(f"[done] threshold -> {thr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
