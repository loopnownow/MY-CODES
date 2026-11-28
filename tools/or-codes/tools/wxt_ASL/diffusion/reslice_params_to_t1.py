# -*- coding: utf-8 -*-
"""
reslice_params_to_t1.py - 用已有 coreg 矩阵把新拟合参数图重采样到 T1 空间
==========================================================================
输入:
  derivatives/Diffusion_Params/sub-XXX/<mod>_<PARAM>.nii.gz   (原生 DWI 网格)
  derivatives/Diffusion_Coreg/sub-XXX/coreg_matrix_sub-XXX_<mod>.mat (G->F voxel)
输出:
  derivatives/Diffusion_Params/sub-XXX/t1_<mod>_<PARAM>.nii.gz (T1 网格)

用法: python reslice_params_to_t1.py [--force] [--subs ...] [--mods ...]
"""
from __future__ import annotations
import os

import argparse
import sys
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.io import loadmat
from scipy.ndimage import affine_transform

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", ""))
RAW_DIR    = STUDY_ROOT / "rawdata"
PARS_ROOT  = STUDY_ROOT / "derivatives" / "Diffusion_Params"
COREG_ROOT = STUDY_ROOT / "derivatives" / "Diffusion_Coreg"
# 模态 -> 参数标签 (与 fit_dwi_params.py 输出一致)
MOD_PARAMS = {
    "dti":  ["FA", "MD", "AD", "RD"],
    "ivim": ["D", "Dstar", "f"],
    "dki":  ["Dapp", "Kapp"],
}
FORCE = False
SUBS_FILTER: tuple = ()
MODS_FILTER = ("dti", "dki", "ivim")
# ====================================================================


def reslice_one(sub: str, mod: str) -> int:
    matf = COREG_ROOT / sub / f"coreg_matrix_{sub}_{mod}.mat"
    t1_path = RAW_DIR / sub / "anat" / f"{sub}_T1w.nii.gz"
    pdir = PARS_ROOT / sub
    if not (matf.exists() and t1_path.exists() and pdir.is_dir()):
        return 0
    aff = np.asarray(loadmat(str(matf))["aff"], dtype=float)   # G->F voxel
    t1 = nib.load(str(t1_path))
    out_shape = t1.shape[:3]
    A = aff[:3, :3]
    t = aff[:3, 3]
    offset = A @ np.ones(3) + t - 1.0        # 与 reslice_derived_maps.py 同约定

    n = 0
    for label in MOD_PARAMS.get(mod, []):
        src = pdir / f"{mod}_{label}.nii.gz"
        if not src.exists():
            continue
        dst = pdir / f"t1_{mod}_{label}.nii.gz"
        if dst.exists() and not FORCE:
            n += 1
            continue
        try:
            data = np.asarray(nib.load(str(src)).dataobj, dtype=np.float64)
            if data.ndim == 4:
                data = data[..., 0]
            out = affine_transform(data, A, offset=offset, output_shape=out_shape,
                                   order=1, mode="constant", cval=0.0)
            nib.save(nib.Nifti1Image(out.astype(np.float32), t1.affine), str(dst))
            n += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [ERR] {sub} {mod} {label}: {e}")
    # b0 也重采样 (用于验证/QC)
    srcb0 = pdir / f"{mod}_b0.nii.gz"
    if srcb0.exists():
        dstb0 = pdir / f"t1_{mod}_b0.nii.gz"
        if not dstb0.exists() or FORCE:
            data = np.asarray(nib.load(str(srcb0)).dataobj, dtype=np.float64)
            out = affine_transform(data, A, offset=offset, output_shape=out_shape,
                                   order=1, mode="constant", cval=0.0)
            nib.save(nib.Nifti1Image(out.astype(np.float32), t1.affine), str(dstb0))
    return n


def main() -> None:
    global FORCE, SUBS_FILTER, MODS_FILTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--subs", type=str, default="")
    ap.add_argument("--mods", type=str, default="")
    args = ap.parse_args()
    FORCE = args.force
    if args.subs:
        SUBS_FILTER = tuple(x.strip() for x in args.subs.split(",") if x.strip())
    if args.mods:
        MODS_FILTER = tuple(x.strip() for x in args.mods.split(",") if x.strip())

    subs = sorted(p.name for p in PARS_ROOT.iterdir()
                  if p.is_dir() and p.name.startswith("sub-"))
    if SUBS_FILTER:
        subs = [s for s in subs if s in SUBS_FILTER]
    total = 0
    for i, sub in enumerate(subs, 1):
        for mod in MODS_FILTER:
            total += reslice_one(sub, mod)
        if i % 10 == 0:
            print(f"  ... {i}/{len(subs)}")
    print(f"resliced param maps total: {total}")


if __name__ == "__main__":
    main()
