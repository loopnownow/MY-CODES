#!/usr/bin/env python
"""Build an nnU-Net v2 dataset from an L-drive leaf (img/roi pairs).

Default is dry-run; --execute writes.

Usage:
  python build_dataset.py                      # dry-run, prints plan only
  python build_dataset.py --execute            # copy imagesTr/labelsTr + dataset.json + splits
  python build_dataset.py --splits-only        # rebuild splits_final.json from existing cases

Design notes
- Case stem = <dst_prefix>_NNNN taken from the source sample key (zjl_CT_GW_0001 -> RLPS_GW_0001).
- Excluded cases come from the ROI/IMAGE QC table (verdict == BLANK by default), so the
  exclusion list stays in sync with the QC ledger instead of being hard-coded here.
- Splits are grouped by patient_hash (one patient may contribute two studies);
  nnUNet splits_final.json is a list of {"train": [...], "val": [...]}.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ config
# Absolute defaults; the CLI overrides the entries marked (cli).
CONFIG = {
    "src_root": os.environ.get("CONFIG", ""),                       # (cli) leaf root containing img/ and roi/
    "seq": "CT_plain",                                   # (cli) sequence tag used inside img/ and roi/
    "study_index": r"{lab_root}\drafting\zjl_RDL\ref\data\zjl_RDL_CT_GW_study-index.csv",
    "qc_table": r"{lab_root}\drafting\zjl_RDL\ref\tmp\ctplain\ROI-IMAGE-QC.tsv",
    "qc_exclude_verdict": "BLANK",
    "out_root": os.environ.get("nnUNet_raw", os.environ.get("CONFIG_3", "")),  # (cli)
    "qc_out_dir": os.environ.get("CONFIG_2", ""),      # (cli)
    "dataset_dirname": "Dataset002_RLPS_CT",             # (cli)
    "dst_prefix": "RLPS_GW",                             # (cli)
    "include_table": "",                                 # (cli) MASK-QC TSV, verdict==A 白名单
    "flat_root": "",                                     # (cli) 平铺源根（img/*.nii + roi/*.nii，如 yt_RDL）
    "flat_prefix": "RLPS_YT",                            # (cli) 平铺源的目标 case 前缀
    "n_folds": 5,                                        # (cli)
    "seed": 12345,                                       # (cli)
    "channel_name": "CT",
    "label_map": {"background": 0, "tumor": 1},
    "file_ending": ".nii.gz",
    "geom_atol": 1e-3,
}


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def is_gzip(path: Path) -> bool:
    """Cheap format probe: gzip streams start with 1f 8b."""
    with open(path, "rb") as f:
        return f.read(2) == b"\x1f\x8b"


def write_nii(dst: Path, src: Path) -> None:
    """Copy src to dst; gzip-normalise when dst is .nii.gz but src is a plain .nii.

    Flat sources hold uncompressed .nii (L:\\RDL\\yt_RDL: 118/118 files start with 5c 01),
    so a raw copy would put plain bytes under a .nii.gz name. nnU-Net's SimpleITKIO sniffs
    the magic and still reads it, but every nibabel-based gate downstream (check_pairs,
    mask_qc, align_mask_to_image) fails with "is not a gzip file". Normalise on write and
    verify by decompressing back to the source byte count.
    """
    if dst.name.endswith(".nii.gz") and not is_gzip(src):
        with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        shutil.copystat(src, dst)
        with gzip.open(dst, "rb") as f:
            back = len(f.read())
        expect = src.stat().st_size
        if back != expect:
            raise RuntimeError(f"gzip round-trip mismatch on {dst.name}: {back} != {expect}")
        return
    shutil.copy2(src, dst)


def read_qc_excluded(csv_path: Path, verdict: str) -> set[str]:
    """Read the ROI/IMAGE QC table. The file is named .tsv but is comma separated."""
    if not csv_path.is_file():
        print(f"[warn] QC table missing: {csv_path}")
        return set()
    out: set[str] = set()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("verdict") or "").strip().upper() == verdict.upper():
                out.add(row["patient"].strip())
    return out


def read_patient_map(csv_path: Path) -> dict[str, str]:
    """sample_id -> patient_hash (already pseudonymised in the project table)."""
    out: dict[str, str] = {}
    if not csv_path.exists():
        print(f"[warn] study index missing: {csv_path}")
        return out
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            sid = (row.get("sample_id") or "").strip()
            ph = (row.get("patient_hash") or "").strip()
            if sid:
                out[sid] = ph or sid
    return out


def check_pair(img: Path, lbl: Path, atol: float) -> dict:
    """Geometry + label value check. Returns a dict with ok/msg/n_vox/vol_mm3."""
    import nibabel as nib

    im = nib.load(str(img))
    la = nib.load(str(lbl))
    res = {"ok": True, "msg": [], "shape": tuple(im.shape), "zooms": None,
           "n_vox": 0, "vol_mm3": 0.0}
    if tuple(im.shape) != tuple(la.shape):
        res["ok"] = False
        res["msg"].append(f"shape mismatch {im.shape} vs {la.shape}")
    zooms = tuple(float(x) for x in im.header.get_zooms()[:3])
    lz = tuple(float(x) for x in la.header.get_zooms()[:3])
    res["zooms"] = zooms
    if not np.allclose(zooms, lz, rtol=atol, atol=atol):
        res["ok"] = False
        res["msg"].append(f"zooms mismatch {zooms} vs {lz}")
    if not np.allclose(im.affine, la.affine, rtol=0, atol=atol):
        res["ok"] = False
        res["msg"].append("affine mismatch")
    arr = np.asanyarray(la.dataobj)
    vals = np.unique(arr)
    bad = [float(v) for v in vals if float(v) not in (0.0, 1.0)]
    if bad:
        res["ok"] = False
        res["msg"].append(f"label values outside {{0,1}}: {bad[:6]}")
    n_vox = int((arr > 0).sum())
    res["n_vox"] = n_vox
    if n_vox == 0:
        res["ok"] = False
        res["msg"].append("empty label")
    res["vol_mm3"] = n_vox * zooms[0] * zooms[1] * zooms[2]
    return res


def make_folds(patient_of_case: dict[str, str], n_folds: int, seed: int) -> list[dict]:
    """Group-aware k-fold: shuffle patients once, then split the patient list."""
    patients = sorted({p for p in patient_of_case.values()})
    rng = random.Random(seed)
    rng.shuffle(patients)
    buckets: list[list[str]] = [[] for _ in range(n_folds)]
    for i, p in enumerate(patients):
        buckets[i % n_folds].append(p)
    folds = []
    for k in range(n_folds):
        val_p = set(buckets[k])
        train = sorted(c for c, p in patient_of_case.items() if p not in val_p)
        val = sorted(c for c, p in patient_of_case.items() if p in val_p)
        folds.append({"train": train, "val": val})
    return folds


def dst_stem(prefix: str, sample_id: str) -> str:
    """zjl_CT_GW_0001 -> RLPS_GW_0001；yt_0007 -> RLPS_YT_0007。"""
    if sample_id.startswith("yt_"):
        return f"RLPS_YT_{sample_id.split('_', 1)[1]}"
    return f"{prefix}_{sample_id.rsplit('_', 1)[-1]}"


def read_include_whitelist(path: Path, seq: str, verdict: str = "A") -> set:
    """MASK-QC TSV -> {(sample_id, seq)}，只留 verdict 匹配的行（默认 A 档）。"""
    out: set = set()
    if not path.is_file():
        print(f"[warn] include table missing: {path}")
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if (row.get("verdict") or "").strip().upper() == verdict.upper():
                sid = (row.get("sample_id") or "").strip()
                sq = (row.get("seq") or "").strip()
                if sid and (not seq or sq == seq):
                    out.add((sid, sq))
    return out


def collect_flat_cases(flat_root: Path, atol: float) -> tuple:
    """平铺源（img/*.nii + roi/*.nii）：配对规则 精确名 -> 去尾部 'i' -> 去 '_1'，并做几何过滤。"""
    import nibabel as nib

    img_dir, roi_dir = flat_root / "img", flat_root / "roi"
    if not img_dir.is_dir() or not roi_dir.is_dir():
        print(f"[warn] flat source incomplete: {flat_root}")
        return [], []
    roi_names = {p.name for p in roi_dir.glob("*.nii")}
    pairs, miss = [], []
    for img in sorted(img_dir.glob("*.nii")):
        name = img.name
        stem = name[:-4]
        cands = [name]
        if stem.endswith("i"):
            cands.append(stem[:-1] + ".nii")
        if "_1" in stem:
            cands.append(stem.replace("_1", "", 1) + ".nii")
        lbl = next((roi_dir / c for c in cands if c in roi_names), None)
        if lbl is None:
            miss.append((name, "no_roi"))
            continue
        try:
            im, la = nib.load(str(img)), nib.load(str(lbl))
            if tuple(im.shape) != tuple(la.shape):
                miss.append((name, "shape %s vs %s" % (im.shape, la.shape)))
                continue
            if not np.allclose(im.affine, la.affine, rtol=0, atol=atol):
                miss.append((name, "affine"))
                continue
        except Exception as exc:  # noqa: BLE001
            miss.append((name, "read_err:%s" % str(exc)[:30]))
            continue
        pairs.append((img, lbl))
    return pairs, miss


def collect_cases(cfg: dict, excluded: set[str]) -> tuple[dict, list, int]:
    src = Path(cfg["src_root"])
    img_root, roi_root = src / "img", src / "roi"
    cases: dict[str, tuple[Path, Path]] = {}
    missing: list[tuple] = []
    skipped = 0
    for d in sorted(img_root.iterdir()):
        if not d.is_dir() or not d.name.startswith("zjl_"):
            continue
        sid = d.name
        if sid in excluded:
            continue
        img = d / f"{cfg['seq']}_image.nii.gz"
        lbl = roi_root / sid / f"{cfg['seq']}_label.nii.gz"
        if not img.exists():
            skipped += 1          # other centres / modalities have no plain series: expected
            continue
        if not lbl.exists():
            missing.append((sid, True, False))
            continue
        cases[sid] = (img, lbl)
    return cases, missing, skipped


def header_check(img: Path, lbl: Path, atol: float) -> tuple[bool, str]:
    """Header-only check (fast). Full voxel check lives in check_pairs.py."""
    import nibabel as nib

    im = nib.load(str(img))
    la = nib.load(str(lbl))
    msgs = []
    if tuple(im.shape) != tuple(la.shape):
        msgs.append(f"shape {im.shape} vs {la.shape}")
    if not np.allclose(im.header.get_zooms()[:3], la.header.get_zooms()[:3], rtol=atol, atol=atol):
        msgs.append("zooms")
    if not np.allclose(im.affine, la.affine, rtol=0, atol=atol):
        msgs.append("affine")
    return (not msgs), ";".join(msgs)


def write_splits(cfg: dict, ds: Path, patient_of_case: dict[str, str], stems: list[str]) -> dict:
    folds = make_folds(patient_of_case, cfg["n_folds"], cfg["seed"])
    with open(ds / "splits_final.json", "w", encoding="utf-8") as f:
        json.dump(folds, f, indent=4)
    summary = {
        "dataset": cfg["dataset_dirname"],
        "n_cases": len(stems),
        "n_patients": len({patient_of_case[s] for s in stems}),
        "seed": cfg["seed"],
        "folds": [
            {"fold": i,
             "n_train": len(fo["train"]),
             "n_val": len(fo["val"]),
             "n_val_patients": len({patient_of_case[c] for c in fo["val"]})}
            for i, fo in enumerate(folds)
        ],
    }
    qc = Path(cfg["qc_out_dir"])
    qc.mkdir(parents=True, exist_ok=True)
    with open(qc / "splits_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build nnU-Net v2 dataset from img/roi leaf")
    ap.add_argument("--src-root", default=CONFIG["src_root"])
    ap.add_argument("--seq", default=CONFIG["seq"])
    ap.add_argument("--study-index", default=CONFIG["study_index"])
    ap.add_argument("--qc-table", default=CONFIG["qc_table"])
    ap.add_argument("--out-root", default=CONFIG["out_root"])
    ap.add_argument("--qc-out-dir", default=CONFIG["qc_out_dir"])
    ap.add_argument("--dataset-dirname", default=CONFIG["dataset_dirname"])
    ap.add_argument("--dst-prefix", default=CONFIG["dst_prefix"])
    ap.add_argument("--folds", type=int, default=CONFIG["n_folds"])
    ap.add_argument("--seed", type=int, default=CONFIG["seed"])
    ap.add_argument("--execute", action="store_true", help="write files (default: dry-run)")
    ap.add_argument("--splits-only", action="store_true", help="rebuild splits from existing dataset dir")
    ap.add_argument("--allow-existing", action="store_true", help="write into a non-empty dataset dir")
    ap.add_argument("--include-table", default=CONFIG["include_table"],
                    help="MASK-QC TSV；只收 verdict==A 的 (sample_id, seq)")
    ap.add_argument("--flat-root", default=CONFIG["flat_root"],
                    help="额外平铺源根（img/*.nii + roi/*.nii，如 yt_RDL）")
    ap.add_argument("--flat-prefix", default=CONFIG["flat_prefix"], help="平铺源的目标 case 前缀")
    args = ap.parse_args(argv)

    cfg = dict(CONFIG)
    cfg.update({
        "src_root": args.src_root, "seq": args.seq, "study_index": args.study_index,
        "qc_table": args.qc_table, "out_root": args.out_root, "qc_out_dir": args.qc_out_dir,
        "dataset_dirname": args.dataset_dirname, "dst_prefix": args.dst_prefix,
        "n_folds": args.folds, "seed": args.seed,
        "include_table": args.include_table, "flat_root": args.flat_root,
        "flat_prefix": args.flat_prefix,
    })
    ds = Path(cfg["out_root"]) / cfg["dataset_dirname"]

    excluded = read_qc_excluded(Path(cfg["qc_table"]), cfg["qc_exclude_verdict"])
    patient_map = read_patient_map(Path(cfg["study_index"]))
    print(f"[info] excluded by QC verdict '{cfg['qc_exclude_verdict']}': {len(excluded)}")
    print(f"[info] patient map rows: {len(patient_map)}")

    if args.splits_only:
        raise SystemExit("[FAIL] --splits-only needs a rebuilt patient map; "
                         "re-run the full build instead")

    cases, missing, skipped = collect_cases(cfg, excluded)
    if missing:
        print(f"[FAIL] image present but label missing on {len(missing)} case(s):")
        for m in missing[:20]:
            print(f"  {m[0]}")
        return 2
    print(f"[info] usable pairs: {len(cases)}  "
          f"(skipped no-image {skipped}, excluded {len(excluded)})")

    if args.include_table:
        wl = read_include_whitelist(Path(args.include_table), cfg["seq"])
        before = len(cases)
        cases = {k: v for k, v in cases.items() if (k, cfg["seq"]) in wl}
        print(f"[info] whitelist verdict=A: {len(wl)} rows -> kept {len(cases)} of {before}")

    if args.flat_root:
        fpairs, fmiss = collect_flat_cases(Path(args.flat_root), CONFIG["geom_atol"])
        for i, (fi, fl) in enumerate(fpairs, 1):
            cases[f"yt_{i:04d}"] = (fi, fl)
        print(f"[info] flat source: +{len(fpairs)} pairs (rejected {len(fmiss)})")
        for nm, why in fmiss[:12]:
            print(f"   - {nm}: {why}")

    stems = {sid: dst_stem(cfg["dst_prefix"], sid) for sid in cases}
    total_bytes = sum(img.stat().st_size + lbl.stat().st_size for img, lbl in cases.values())
    print(f"[info] payload: {total_bytes / 1024**3:.2f} GiB")

    bad = []
    for sid, (img, lbl) in cases.items():
        ok, msg = header_check(img, lbl, CONFIG["geom_atol"])
        if not ok:
            bad.append((sid, msg))
    if bad:
        print(f"[FAIL] header geometry mismatch on {len(bad)} case(s):")
        for sid, msg in bad[:20]:
            print(f"  {sid}: {msg}")
        return 2

    pom = {stems[sid]: patient_map.get(sid, sid) for sid in cases}
    folds = make_folds(pom, cfg["n_folds"], cfg["seed"])
    print("[plan] folds (patient-grouped): train / val (val patients)")
    for i, fo in enumerate(folds):
        nvp = len({pom[c] for c in fo["val"]})
        print(f"  fold {i}: {len(fo['train'])} / {len(fo['val'])} ({nvp} patients)")

    if not args.execute:
        print("[dry-run] nothing written. Re-run with --execute.")
        return 0

    if ds.exists() and any(ds.iterdir()) and not args.allow_existing:
        print(f"[FAIL] dataset dir not empty: {ds}\n       use --allow-existing to write into it")
        return 2
    (ds / "imagesTr").mkdir(parents=True, exist_ok=True)
    (ds / "labelsTr").mkdir(parents=True, exist_ok=True)

    qc = Path(cfg["qc_out_dir"])
    qc.mkdir(parents=True, exist_ok=True)
    manifest = qc / f"BUILD-MANIFEST-{time.strftime('%Y%m%d')}.tsv"
    rows = []
    t0 = time.time()
    for n, (sid, (img, lbl)) in enumerate(sorted(cases.items()), 1):
        stem = stems[sid]
        dst_img = ds / "imagesTr" / f"{stem}_0000{cfg['file_ending']}"
        dst_lbl = ds / "labelsTr" / f"{stem}{cfg['file_ending']}"
        write_nii(dst_img, img)
        write_nii(dst_lbl, lbl)
        rows.append([sid, stem, pom[stem], str(img), sha256_file(dst_img),
                     str(lbl), sha256_file(dst_lbl),
                     dst_img.stat().st_size, dst_lbl.stat().st_size])
        if n % 25 == 0 or n == len(cases):
            print(f"[copy] {n}/{len(cases)}  elapsed {time.time()-t0:.0f}s", flush=True)

    with open(manifest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sample_id", "dst_stem", "patient_hash", "src_img", "sha256_img",
                    "src_label", "sha256_label", "bytes_img", "bytes_label"])
        w.writerows(rows)

    dataset_json = {
        "channel_names": {"0": cfg["channel_name"]},
        "labels": cfg["label_map"],
        "numTraining": len(cases),
        "file_ending": cfg["file_ending"],
        "name": cfg["dataset_dirname"],
        "description": "RLPS CT tumor segmentation (%d GW + %d YT)" % (
            sum(1 for s in cases if not s.startswith("yt_")),
            sum(1 for s in cases if s.startswith("yt_"))),
    }
    with open(ds / "dataset.json", "w", encoding="utf-8") as f:
        json.dump(dataset_json, f, indent=4)

    summary = write_splits(cfg, ds, pom, sorted(pom))
    print(f"[done] {len(cases)} cases -> {ds}")
    print(f"[done] manifest -> {manifest}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())


