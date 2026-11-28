# -*- coding: utf-8 -*-
"""
warp_prep_finish.py - 为 SPM 归一化准备/收尾 (解压与压缩 .nii)
================================================================
stage=prep   : t1_*.nii.gz -> t1_*.nii ; y_T1.nii.gz -> y_T1.nii  (未压缩)
stage=finish : SPM 输出的 mni_*.nii -> mni_*.nii.gz ; 清理 t1_*.nii

用法:
  python warp_prep_finish.py --stage prep
  python warp_prep_finish.py --stage finish
"""
from __future__ import annotations
import os

import argparse
import gzip
import shutil
import sys
from pathlib import Path

import nibabel as nib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", ""))
PARS_ROOT  = STUDY_ROOT / "derivatives" / "Diffusion_Params"
EA_ROOT    = STUDY_ROOT / "derivatives" / "ExploreASL"
# ====================================================================


def decompress(gz: Path, out: Path) -> bool:
    """原始 gzip 字节解压 .nii.gz -> .nii (与 SPM 读取兼容, 不做任何重编码)。"""
    if out.exists():
        return True
    try:
        with gzip.open(str(gz), "rb") as fi, open(str(out), "wb") as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] decompress {gz.name}: {e}")
        return False


def do_prep(subs: list[str]) -> None:
    n_y = n_p = 0
    for sub in subs:
        ygz = EA_ROOT / f"{sub}_1" / "y_T1.nii.gz"
        if ygz.exists() and decompress(ygz, EA_ROOT / f"{sub}_1" / "y_T1.nii"):
            n_y += 1
        pdir = PARS_ROOT / sub
        if not pdir.is_dir():
            continue
        for gz in sorted(pdir.glob("t1_*.nii.gz")):
            if decompress(gz, pdir / gz.name[:-3]):
                n_p += 1
    print(f"prep: y_T1 {n_y} | t1 params {n_p}")


def do_finish(subs: list[str]) -> None:
    n_m = n_c = 0
    for sub in subs:
        pdir = PARS_ROOT / sub
        if not pdir.is_dir():
            continue
        for nii in sorted(pdir.glob("mni_*.nii")):
            gz = nii.with_suffix(".nii.gz")
            if not gz.exists():
                with open(nii, "rb") as fi, gzip.open(gz, "wb") as fo:
                    shutil.copyfileobj(fi, fo, 1 << 20)
                n_m += 1
            nii.unlink()
        for nii in sorted(pdir.glob("t1_*.nii")):
            nii.unlink()
            n_c += 1
        ytmp = EA_ROOT / f"{sub}_1" / "y_T1.nii"
        if ytmp.exists():
            ytmp.unlink()
    print(f"finish: mni gz {n_m} | t1 temp removed {n_c}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["prep", "finish"], required=True)
    ap.add_argument("--subs", type=str, default="")
    args = ap.parse_args()
    subs = sorted(p.name for p in PARS_ROOT.iterdir()
                  if p.is_dir() and p.name.startswith("sub-"))
    if args.subs:
        keep = {x.strip() for x in args.subs.split(",") if x.strip()}
        subs = [s for s in subs if s in keep]
    if args.stage == "prep":
        do_prep(subs)
    else:
        do_finish(subs)


if __name__ == "__main__":
    main()
