#!/usr/bin/env python
"""Stage and run nnU-Net v2 inference for the ZS (Zhongshan) CT series.

Reads ZS-PHASE-MAP.tsv, stages the selected series as <sample>_<seq>_0000.nii.gz, then calls
nnUNetv2_predict. Output lands as <sample>_<seq>.nii.gz in the prediction dir.

Default is dry-run: prints how many series / bytes would be staged and the exact command.
--execute copies and runs.

TTA is OFF by default: with TTA (mirroring) inference cost is ~8x, which turns a 5-fold run of
429 series into days. Turn it on only for a final report run.

Usage
  python predict_zs.py                            # dry-run, folds 0
  python predict_zs.py --folds 0 --execute        # single-fold quick pass
  python predict_zs.py --folds 0 1 2 3 4 --execute
"""
from __future__ import annotations
import os

import argparse
import csv
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

CONFIG = {
    "phase_map": r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ZS-PHASE-MAP.tsv",
    "img_root": os.environ.get("CONFIG", ""),
    "stage_dir": os.environ.get("CONFIG_2", ""),
    "pred_dir": os.environ.get("CONFIG_3", ""),
    "dataset_id": "2",
    "configuration": "3d_fullres",
    "checkpoint": "checkpoint_best.pth",
    "predict_exe": os.environ.get("CONFIG_4", ""),
    "env_script": os.environ.get("CONFIG_5", ""),
}


def load_selected(phase_map: Path) -> list[dict]:
    """Rows with pick_primary or pick_check == 1."""
    out = []
    with open(phase_map, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if str(row.get("pick_primary")) == "1" or str(row.get("pick_check")) == "1":
                out.append(row)
    return out


def stage(rows: list[dict], img_root: Path, stage_dir: Path, execute: bool) -> tuple[int, int, int]:
    """Copy selected images into the nnUNet input folder. Returns (copied, skipped, bytes)."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    copied = skipped = nbytes = 0
    for r in rows:
        src = img_root / r["sample"] / f"{r['seq']}_image.nii.gz"
        dst = stage_dir / f"{r['sample']}_{r['seq']}_0000.nii.gz"
        if not src.exists():
            print(f"[warn] missing source: {src}")
            continue
        nbytes += src.stat().st_size
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            skipped += 1
            continue
        if execute:
            shutil.copy2(src, dst)
        copied += 1
    return copied, skipped, nbytes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Stage + run nnU-Net inference for ZS series")
    ap.add_argument("--phase-map", default=CONFIG["phase_map"])
    ap.add_argument("--img-root", default=CONFIG["img_root"])
    ap.add_argument("--stage-dir", default=CONFIG["stage_dir"])
    ap.add_argument("--pred-dir", default=CONFIG["pred_dir"])
    ap.add_argument("--dataset-id", default=CONFIG["dataset_id"])
    ap.add_argument("--configuration", default=CONFIG["configuration"])
    ap.add_argument("--checkpoint", default=CONFIG["checkpoint"])
    ap.add_argument("--folds", nargs="+", default=["0"], help="fold list, e.g. --folds 0 1 2 3 4")
    ap.add_argument("--tta", action="store_true", help="enable mirroring TTA (8x slower)")
    ap.add_argument("--staging-only", action="store_true", help="stage files, skip prediction")
    ap.add_argument("--execute", action="store_true", help="copy + run (default: dry-run)")
    args = ap.parse_args(argv)

    rows = load_selected(Path(args.phase_map))
    if not rows:
        print("[FAIL] no selected series in phase map")
        return 1

    by_pick = Counter("primary" if str(r.get("pick_primary")) == "1" else "check" for r in rows)
    by_period = Counter(r.get("period", "") for r in rows)
    cases = len({r["sample"] for r in rows})
    print(f"[info] cases {cases}  series {len(rows)}  {dict(by_pick)}")
    print(f"[info] periods: {dict(by_period)}")

    stage_dir, pred_dir = Path(args.stage_dir), Path(args.pred_dir)
    copied, skipped, nbytes = stage(rows, Path(args.img_root), stage_dir, args.execute)
    print(f"[stage] to_copy {copied}  already {skipped}  payload {nbytes/1024**3:.2f} GiB  -> {stage_dir}")

    cmd = [CONFIG["predict_exe"], "-i", str(stage_dir), "-o", str(pred_dir),
           "-d", str(args.dataset_id), "-c", args.configuration,
           "-f", *[str(f) for f in args.folds], "-chk", args.checkpoint]
    if not args.tta:
        cmd.append("--disable_tta")
    print("[cmd] " + " ".join(cmd))

    est = len(rows) * len(args.folds)
    print(f"[plan] forward passes: {est} ({len(rows)} series x {len(args.folds)} fold(s))")

    if not args.execute:
        print("[dry-run] nothing written. Re-run with --execute.")
        return 0

    missing_env = [k for k in ("nnUNet_raw", "nnUNet_preprocessed", "nnUNet_results")
                   if not __import__("os").environ.get(k)]
    if missing_env:
        print(f"[FAIL] missing env vars {missing_env}; dot-source {CONFIG['env_script']} first")
        return 2
    if not Path(CONFIG["predict_exe"]).exists():
        print(f"[FAIL] predict exe not found: {CONFIG['predict_exe']}")
        return 2
    if args.staging_only:
        print("[done] staging only")
        return 0

    pred_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    proc = subprocess.run(cmd)
    print(f"[done] exit={proc.returncode}  elapsed {(time.time()-t0)/60:.1f} min")
    n_pred = len(list(pred_dir.glob("*.nii.gz")))
    print(f"[done] predictions on disk: {n_pred}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
