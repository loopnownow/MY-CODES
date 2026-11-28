#!/usr/bin/env python
"""Verify an nnU-Net v2 raw dataset built by build_dataset.py.

Checks
  1  imagesTr / labelsTr one-to-one, naming <stem>_0000.nii.gz / <stem>.nii.gz
  2  per-case geometry: shape, zooms, affine equal between image and label
  3  label values inside {0,1} and foreground non-empty
  4  dataset.json: numTraining matches, single CT channel, background/tumor labels
  5  splits_final.json: covers every case exactly once, no patient crosses folds
  6  optional --sha256: re-hash every file against the build manifest

Exit code 1 when any FAIL is recorded (fail loud, never silent).

Usage
  python check_pairs.py                             # full check, no hashing
  python check_pairs.py --sha256                    # also verify hashes (slow)
  python check_pairs.py --dataset-dir <path> --out <tsv>
"""
from __future__ import annotations
import os

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

CONFIG = {
    "dataset_dir": os.environ.get("CONFIG", ""),
    "manifest": os.environ.get("CONFIG_2", ""),
    "out_dir": os.environ.get("CONFIG_3", ""),
    "file_ending": ".nii.gz",
    "geom_atol": 1e-3,
}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            out[row["dst_stem"]] = row
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify nnU-Net raw dataset")
    ap.add_argument("--dataset-dir", default=CONFIG["dataset_dir"])
    ap.add_argument("--manifest", default=CONFIG["manifest"])
    ap.add_argument("--out-dir", default=CONFIG["out_dir"])
    ap.add_argument("--file-ending", default=CONFIG["file_ending"])
    ap.add_argument("--sha256", action="store_true", help="re-hash files against the manifest")
    args = ap.parse_args(argv)

    import nibabel as nib

    ds = Path(args.dataset_dir)
    it, lt = ds / "imagesTr", ds / "labelsTr"
    if not it.is_dir() or not lt.is_dir():
        print(f"[FAIL] missing imagesTr/labelsTr under {ds}")
        return 1

    imgs = {p.name[:-len("_0000" + args.file_ending)]: p
            for p in it.glob("*_0000" + args.file_ending)}
    lbls = {p.name[:-len(args.file_ending)]: p
            for p in lt.glob("*" + args.file_ending)}
    print(f"[info] imagesTr {len(imgs)}  labelsTr {len(lbls)}")

    rows: list[list] = []
    fails: list[str] = []

    def rec(stem: str, check: str, verdict: str, detail: str = "") -> None:
        rows.append([stem, check, verdict, detail])
        if verdict == "FAIL":
            fails.append(f"{stem}:{check}:{detail}")

    only_img = sorted(set(imgs) - set(lbls))
    only_lbl = sorted(set(lbls) - set(imgs))
    for s in only_img:
        rec(s, "pair", "FAIL", "image without label")
    for s in only_lbl:
        rec(s, "pair", "FAIL", "label without image")

    t0 = time.time()
    stems = sorted(set(imgs) & set(lbls))
    for n, stem in enumerate(stems, 1):
        im = nib.load(str(imgs[stem]))
        la = nib.load(str(lbls[stem]))
        if tuple(im.shape) != tuple(la.shape):
            rec(stem, "shape", "FAIL", f"{im.shape} vs {la.shape}")
        z, lz = im.header.get_zooms()[:3], la.header.get_zooms()[:3]
        if not np.allclose(z, lz, rtol=CONFIG["geom_atol"], atol=CONFIG["geom_atol"]):
            rec(stem, "zooms", "FAIL", f"{z} vs {lz}")
        if not np.allclose(im.affine, la.affine, rtol=0, atol=CONFIG["geom_atol"]):
            rec(stem, "affine", "FAIL", "affine differs")
        arr = np.asanyarray(la.dataobj)
        vals = [float(v) for v in np.unique(arr)]
        bad = [v for v in vals if v not in (0.0, 1.0)]
        if bad:
            rec(stem, "labels", "FAIL", f"values {bad[:5]}")
        n_vox = int((arr > 0).sum())
        if n_vox == 0:
            rec(stem, "foreground", "FAIL", "empty label")
        rows.append([stem, "geom", "OK",
                     f"shape={tuple(im.shape)} zooms={tuple(round(float(x),3) for x in z)} n_vox={n_vox}"])
        if n % 50 == 0 or n == len(stems):
            print(f"[check] {n}/{len(stems)}  elapsed {time.time()-t0:.0f}s", flush=True)

    # ---- dataset.json
    dj_path = ds / "dataset.json"
    if not dj_path.exists():
        rec("-", "dataset.json", "FAIL", "missing")
    else:
        dj = json.loads(dj_path.read_text(encoding="utf-8"))
        if dj.get("numTraining") != len(stems):
            rec("-", "numTraining", "FAIL", f"{dj.get('numTraining')} vs {len(stems)}")
        ch = dj.get("channel_names") or dj.get("modality")
        if not ch or len(ch) != 1:
            rec("-", "channel_names", "FAIL", f"expected 1 channel, got {ch}")
        lab = dj.get("labels") or {}
        if not isinstance(lab, dict) or lab.get("background") != 0 or lab.get("tumor") != 1:
            rec("-", "labels", "FAIL", f"{lab}")
        if dj.get("file_ending") != args.file_ending:
            rec("-", "file_ending", "FAIL", f"{dj.get('file_ending')}")
        if not fails:
            rec("-", "dataset.json", "OK",
                f"numTraining={dj.get('numTraining')} channels={list(ch)} labels={lab}")

    # ---- splits_final.json
    sp_path = ds / "splits_final.json"
    manifest = load_manifest(Path(args.manifest))
    if not sp_path.exists():
        rec("-", "splits", "FAIL", "missing splits_final.json")
    else:
        folds = json.loads(sp_path.read_text(encoding="utf-8"))
        seen: dict[str, int] = {}
        for k, fo in enumerate(folds):
            for c in fo["val"]:
                seen[c] = seen.get(c, 0) + 1
        dup = [c for c, v in seen.items() if v > 1]
        not_covered = sorted(set(stems) - set(seen))
        unknown = sorted(set(seen) - set(stems))
        if dup:
            rec("-", "splits.dup", "FAIL", f"{len(dup)} case(s) in >1 val fold")
        if not_covered:
            rec("-", "splits.cover", "FAIL", f"{len(not_covered)} case(s) never validated")
        if unknown:
            rec("-", "splits.unknown", "FAIL", f"{len(unknown)} unknown case(s)")
        # patient leakage: a patient must not appear in both train and val of one fold
        if manifest:
            leaked = 0
            for fo in folds:
                tr = {manifest[c]["patient_hash"] for c in fo["train"] if c in manifest}
                va = {manifest[c]["patient_hash"] for c in fo["val"] if c in manifest}
                leaked += len(tr & va)
            if leaked:
                rec("-", "splits.leak", "FAIL", f"{leaked} patient(s) in train and val")
            else:
                rec("-", "splits", "OK",
                    f"{len(folds)} folds, no patient leakage, all cases validated once")
        else:
            rec("-", "splits", "WARN", "manifest missing, leakage not checked")

    # ---- optional sha256 against manifest
    if args.sha256 and manifest:
        t1 = time.time()
        for n, stem in enumerate(stems, 1):
            row = manifest.get(stem)
            if not row:
                rec(stem, "manifest", "FAIL", "case not in manifest")
                continue
            h_img = sha256_file(imgs[stem])
            h_lbl = sha256_file(lbls[stem])
            if h_img != row["sha256_img"]:
                rec(stem, "sha256_img", "FAIL", "differs from manifest")
            if h_lbl != row["sha256_label"]:
                rec(stem, "sha256_label", "FAIL", "differs from manifest")
            if n % 50 == 0 or n == len(stems):
                print(f"[sha256] {n}/{len(stems)}  elapsed {time.time()-t1:.0f}s", flush=True)
        if not any(r[1].startswith("sha256") and r[2] == "FAIL" for r in rows):
            rec("-", "sha256", "OK", f"{len(stems)} case(s) match manifest")

    out = Path(args.out_dir) / f"CHECK-PAIRS-{time.strftime('%Y%m%d')}.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["stem", "check", "verdict", "detail"])
        w.writerows(rows)

    n_ok = sum(1 for r in rows if r[2] == "OK")
    print(f"[done] rows {len(rows)}  OK {n_ok}  FAIL {len(fails)}  WARN "
          f"{sum(1 for r in rows if r[2] == 'WARN')}")
    print(f"[done] report -> {out}")
    if fails:
        print("[FAIL] first 10:")
        for f_ in fails[:10]:
            print("  " + f_)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
