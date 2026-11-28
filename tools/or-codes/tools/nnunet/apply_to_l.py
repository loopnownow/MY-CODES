#!/usr/bin/env python
"""Copy selected ZS predictions into the L-drive roi tree, with geometry assertions.

Inputs
  ZS-PHASE-MAP.tsv        which series is primary / check
  predTs_zs/*.nii.gz      nnUNet predictions (one per staged series)
  CROSS-DICE-*.tsv        per-case Dice of primary vs check
  dice-threshold.json     threshold derived from the measured distribution

Output
  L:\RDL\zjl_RDL\roi\<sample>\<SEQ>_label.nii.gz   (primary series only)
  ref\code-gates\ROI-LEDGER.tsv                    (append, one row per written mask)

Per-case flags
  OK            geometry matches, Dice >= threshold (or NO_CHECK)
  DICE_LOW      Dice below threshold, still written (primary stays primary)
  NO_CHECK      single series, no cross-check possible
  EMPTY         prediction has no foreground, nothing written
  VOL_OUTLIER   volume outside the GW p05-p95 band (written, flagged)
  RESAMPLED     prediction geometry differed from the image, resampled to image grid
  CONFLICT      target exists with a different sha256, left untouched

Default is dry-run; --execute writes.
"""
from __future__ import annotations
import os

import argparse
import csv
import hashlib
import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

CONFIG = {
    "phase_map": r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ZS-PHASE-MAP.tsv",
    "pred_dir": os.environ.get("CONFIG", ""),
    "cross_dice": os.environ.get("CONFIG_2", ""),
    "dice_threshold": os.environ.get("CONFIG_3", ""),
    "img_root": os.environ.get("CONFIG_4", ""),
    "roi_root": os.environ.get("CONFIG_5", ""),
    "ledger": r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ROI-LEDGER.tsv",
    "combined_out": r"{lab_root}\drafting\zjl_RDL\habitat_pipeline\combined_zs.csv",
    "vol_p05_ml": 98.4,
    "vol_p95_ml": 3807.5,
    "geom_atol": 1e-3,
}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_dice(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    if not path.exists():
        print(f"[warn] cross-dice missing: {path}")
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[row["sample"]] = float(row["dice"])
    return out


def load_selected(path: Path) -> dict[str, dict]:
    """sample -> primary row (pick_primary == 1)."""
    out: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if str(row.get("pick_primary")) == "1":
                out[row["sample"]] = row
    return out


def write_combined(rows: list[dict], path: Path) -> None:
    """Derived pairing table for the ZS external cohort (does not touch combined.csv)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["patient_id", "label", "sequence", "modality", "center",
                    "img_path", "roi_path", "is_reference"])
        for r in sorted(rows, key=lambda x: x["sample"]):
            w.writerow([r["sample"], "", r["seq"], "CT", "ZS", r["src_img"], r["roi_path"], 1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write selected ZS masks into the L roi tree")
    ap.add_argument("--phase-map", default=CONFIG["phase_map"])
    ap.add_argument("--pred-dir", default=CONFIG["pred_dir"])
    ap.add_argument("--cross-dice", default=CONFIG["cross_dice"])
    ap.add_argument("--dice-threshold", default=CONFIG["dice_threshold"])
    ap.add_argument("--img-root", default=CONFIG["img_root"])
    ap.add_argument("--roi-root", default=CONFIG["roi_root"])
    ap.add_argument("--ledger", default=CONFIG["ledger"])
    ap.add_argument("--combined-out", default=CONFIG["combined_out"])
    ap.add_argument("--model-tag", default="Dataset002_RLPS_CT/3d_fullres")
    ap.add_argument("--folds", default="0", help="fold list written into the ledger")
    ap.add_argument("--execute", action="store_true", help="write to L (default: dry-run)")
    args = ap.parse_args(argv)

    import nibabel as nib

    primary = load_selected(Path(args.phase_map))
    dice = load_dice(Path(args.cross_dice))
    thr_path = Path(args.dice_threshold)
    threshold = None
    if thr_path.exists():
        threshold = json.loads(thr_path.read_text(encoding="utf-8")).get("threshold")
    print(f"[info] cases {len(primary)}  dice rows {len(dice)}  threshold {threshold}")

    pred_dir, img_root, roi_root = Path(args.pred_dir), Path(args.img_root), Path(args.roi_root)
    rows, flags = [], Counter()
    total_bytes = 0
    for sample in sorted(primary):
        r = primary[sample]
        seq = r["seq"]
        pred = pred_dir / f"{sample}_{seq}.nii.gz"
        img = img_root / sample / f"{seq}_image.nii.gz"
        if not pred.exists():
            flags["MISSING_PRED"] += 1
            continue
        if not img.exists():
            flags["MISSING_IMG"] += 1
            continue

        pi, ii = nib.load(str(pred)), nib.load(str(img))
        same_geom = (tuple(pi.shape) == tuple(ii.shape)
                     and np.allclose(pi.header.get_zooms()[:3], ii.header.get_zooms()[:3],
                                     rtol=CONFIG["geom_atol"], atol=CONFIG["geom_atol"])
                     and np.allclose(pi.affine, ii.affine, rtol=0, atol=CONFIG["geom_atol"]))
        flag_list: list[str] = []
        if not same_geom:
            flag_list.append("RESAMPLED")
        arr = np.asanyarray(pi.dataobj) if same_geom else None
        if same_geom:
            n_vox = int((arr > 0).sum())
        else:
            import SimpleITK as sitk
            ref = sitk.ReadImage(str(img))
            mov = sitk.ReadImage(str(pred))
            res = sitk.Resample(mov, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0,
                                sitk.sitkUInt8)
            arr = sitk.GetArrayFromImage(res)
            n_vox = int((arr > 0).sum())
        if n_vox == 0:
            flags["EMPTY"] += 1
            continue

        z = ii.header.get_zooms()[:3]
        vol_ml = n_vox * float(z[0]) * float(z[1]) * float(z[2]) / 1000.0
        if not (CONFIG["vol_p05_ml"] <= vol_ml <= CONFIG["vol_p95_ml"]):
            flag_list.append("VOL_OUTLIER")
        d = dice.get(sample)
        if d is None:
            flag_list.append("NO_CHECK")
        elif threshold is not None and d < threshold:
            flag_list.append("DICE_LOW")
        if not flag_list:
            flag_list.append("OK")

        dst = roi_root / sample / f"{seq}_label.nii.gz"
        action = "write"
        if dst.exists():
            if sha256_file(dst) == sha256_file(pred) and same_geom:
                action = "skip_identical"
            else:
                action = "conflict"
                flag_list.append("CONFLICT")
        flags[flag_list[0] if flag_list else "OK"] += 1
        if action == "write":
            total_bytes += pred.stat().st_size
        rows.append({
            "sample": sample, "seq": seq, "src_img": str(img), "sha256_img": sha256_file(img),
            "roi_path": str(dst), "n_vox": n_vox, "vol_mm3": round(vol_ml, 1),
            "dice_cross_phase": "" if d is None else round(d, 4),
            "model": args.model_tag, "fold": args.folds,
            "flag": "+".join(flag_list), "action": action, "pred": str(pred),
        })

    n_write = sum(1 for r in rows if r["action"] == "write")
    n_skip = sum(1 for r in rows if r["action"] == "skip_identical")
    n_conf = sum(1 for r in rows if r["action"] == "conflict")
    print(f"[plan] write {n_write}  skip_identical {n_skip}  conflict {n_conf}  "
          f"payload {total_bytes/1024**2:.1f} MiB")
    print(f"[flags] {dict(flags)}")
    for k in ("MISSING_PRED", "MISSING_IMG", "EMPTY", "CONFLICT"):
        if flags.get(k):
            print(f"[warn] {k}: {flags[k]}")

    if not args.execute:
        print("[dry-run] nothing written. Re-run with --execute after review.")
        return 0

    import SimpleITK as sitk

    t0 = time.time()
    ledger_rows = []
    for n, r in enumerate([x for x in rows if x["action"] == "write"], 1):
        dst = Path(r["roi_path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        pred = Path(r["pred"])
        if "RESAMPLED" in r["flag"]:
            ref = sitk.ReadImage(r["src_img"])
            mov = sitk.ReadImage(str(pred))
            out = sitk.Resample(mov, ref, sitk.Transform(), sitk.sitkNearestNeighbor, 0,
                                sitk.sitkUInt8)
            sitk.WriteImage(out, str(dst))
        else:
            shutil.copy2(pred, dst)
        ledger_rows.append([r["sample"], r["seq"], r["src_img"], r["sha256_img"], str(dst),
                            sha256_file(dst), r["n_vox"], r["vol_mm3"], r["dice_cross_phase"],
                            r["model"], r["fold"], r["flag"],
                            time.strftime("%Y-%m-%d %H:%M:%S")])
        if n % 20 == 0 or n == n_write:
            print(f"[write] {n}/{n_write}  elapsed {time.time()-t0:.0f}s", flush=True)

    led = Path(args.ledger)
    led.parent.mkdir(parents=True, exist_ok=True)
    new_file = not led.exists()
    with open(led, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        if new_file:
            w.writerow(["sample", "seq", "src_img", "sha256_img", "roi_path", "sha256_roi",
                        "n_vox", "vol_mm3", "dice_cross_phase", "model", "fold", "flag", "at"])
        w.writerows(ledger_rows)
    print(f"[done] {len(ledger_rows)} masks -> {args.roi_root}")
    print(f"[done] ledger -> {led}")
    written = [x for x in rows if x["action"] == "write"]
    comb = Path(args.combined_out)
    write_combined(written, comb)
    print(f"[done] combined ({len(written)} rows) -> {comb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

