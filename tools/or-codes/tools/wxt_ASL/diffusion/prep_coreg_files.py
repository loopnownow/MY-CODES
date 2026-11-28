# -*- coding: utf-8 -*-
"""
prep_coreg_files.py - 为 SPM 配准准备临时 .nii 文件
==========================================================
对 rawdata 每个受试者每个弥散模态:
  1. 解压 T1w -> <tmp>/<sub>_T1w.nii
  2. 从 4D DWI 提取 b0 均值 -> <tmp>/<sub>_<mod>_b0.nii
  3. 解压衍生参数图 (FA/ADC/TRACEW) -> <tmp>/<sub>_<mod>_<label>.nii
  4. 输出 manifest.tsv 供 MATLAB 配准脚本使用

用法:
  python prep_coreg_files.py                # 全部
  python prep_coreg_files.py --subs sub-007 --mods dki
  python prep_coreg_files.py --tmp <dir>    # 指定临时目录
"""
from __future__ import annotations
import os

import argparse
import gzip
import shutil
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
# 1. 配置区
# ====================================================================
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", ""))
RAW_DIR    = STUDY_ROOT / "rawdata"
TMP_DIR    = STUDY_ROOT / "derivatives" / ".tmp_coreg_nii"
MODS       = ("dti", "dki", "ivim")
MOD_ENTITY = {"dti": "acq-dti", "dki": "acq-dki", "ivim": "acq-ivim"}
DERIVED_MAP = {
    "dti":  [("ep2d_diff_mddw_25_p2_adc", "ADC"),
             ("ep2d_diff_mddw_25_p2_tracew", "TRACEW"),
             ("ep2d_diff_mddw_25_p2_fa", "FA")],
    "dki":  [("ep2d_diff_dki_adc", "ADC")],
    "ivim": [("ep2d_diff_ivim_adc", "ADC")],
}
# ====================================================================


def gunzip_copy(src: Path, dst: Path) -> bool:
    """解压 .nii.gz -> 普通 .nii (硬链接过度处理, 直接解压到临时目录)。"""
    try:
        with gzip.open(str(src), "rb") as f_in:
            with open(str(dst), "wb") as f_out:
                shutil.copyfileobj(f_in, f_out, 1 << 20)
        return True
    except Exception:
        return False


def brain_com_voxel_data(data: np.ndarray, thr_frac: float = 0.2):
    """3D 数组质心 (1-based voxel 坐标, 同 SPM 索引)。"""
    thr = data.max() * thr_frac
    mask = data > thr
    idx = np.argwhere(mask)
    if len(idx) == 0:
        return np.array(data.shape, float) / 2.0 + 1.0
    return idx.mean(0) + 1.0


def brain_com_voxel(img: nib.Nifti1Image, thr_frac: float = 0.2):
    """图像质心 (1-based voxel 坐标, 同 SPM 索引)。"""
    return brain_com_voxel_data(img.get_fdata(), thr_frac)


def fit_affine(t1_img: nib.Nifti1Image, b0_data: np.ndarray,
               b0_aff: np.ndarray) -> np.ndarray:
    """基于质心修正 b0 affine: 使 b0 质心(世界)对齐 T1 质心(世界)。
    保留 b0 的体素尺寸与方向, 仅平移 sform origin。
    b0_data: 3D 数组 (非 4D!); 使用齐次坐标 4x4 @ (4,) 避免广播。"""
    c_b0 = brain_com_voxel_data(b0_data)        # (3,) 1-based voxel
    c_t1 = brain_com_voxel(t1_img)
    h_b0 = np.append(c_b0, 1.0)
    h_t1 = np.append(c_t1, 1.0)
    w_b0 = (b0_aff @ h_b0)[:3]                  # (3,) world mm
    w_t1 = (t1_img.affine @ h_t1)[:3]
    delta = w_t1 - w_b0
    new_aff = b0_aff.copy()
    new_aff[:3, 3] += delta
    return new_aff


def prep_subject(sub_dir: Path, tmp_dir: Path, mods) -> list[dict]:
    """为单个受试者准备文件, 返回 manifest 行。"""
    rows = []
    t1_gz = sub_dir / "anat" / f"{sub_dir.name}_T1w.nii.gz"
    t1_nii = tmp_dir / f"{sub_dir.name}_T1w.nii"
    if t1_gz.exists() and not t1_nii.exists():
        if not gunzip_copy(t1_gz, t1_nii):
            return rows
    t1_img = nib.load(str(t1_nii)) if t1_nii.exists() else None

    for mod in mods:
        ent = MOD_ENTITY[mod]
        dwi_gz = sub_dir / "dwi" / f"{sub_dir.name}_{ent}_dwi.nii.gz"
        bval_f = sub_dir / "dwi" / f"{sub_dir.name}_{ent}_dwi.bval"
        if not dwi_gz.exists():
            continue
        b0_nii = tmp_dir / f"{sub_dir.name}_{mod}_b0.nii"
        if not b0_nii.exists() and t1_img is not None:
            img = nib.load(str(dwi_gz))
            data = img.get_fdata()
            bvals = None
            if bval_f.exists():
                bvals = np.array([float(x) for x in bval_f.read_text().split()])
                if len(bvals) != data.shape[3]:
                    bvals = None
            if bvals is None:
                idx = [0]
            else:
                idx = list(np.where(bvals < 50)[0]) or [0]
            b0 = np.mean(data[..., idx], axis=-1)
            b0_aff = fit_affine(t1_img, b0, img.affine)
            out = nib.Nifti1Image(b0.astype(np.float32), b0_aff)
            nib.save(out, str(b0_nii))

        row = {"subject": sub_dir.name, "mod": mod,
               "t1": str(t1_nii), "b0": str(b0_nii), "params": ""}
        param_list = []
        for kw, label in DERIVED_MAP.get(mod, []):
            src = None
            for f in (sub_dir / "dwi").iterdir():
                if kw in f.name and f.name.endswith(".nii.gz"):
                    src = f
                    break
            if src is None:
                continue
            dst = tmp_dir / f"{sub_dir.name}_{mod}_{label}.nii"
            if not dst.exists():
                gunzip_copy(src, dst)
            param_list.append(f"{label}={dst}")
        row["params"] = ",".join(param_list)
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subs", type=str, default="")
    ap.add_argument("--mods", type=str, default="")
    ap.add_argument("--tmp", type=Path, default=TMP_DIR)
    args = ap.parse_args()

    subs_f = tuple(x.strip() for x in args.subs.split(",") if x.strip())
    mods = tuple(x.strip() for x in args.mods.split(",") if x.strip()) or MODS
    tmp_dir = args.tmp or TMP_DIR
    tmp_dir.mkdir(parents=True, exist_ok=True)

    subs = sorted(p for p in RAW_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("sub-"))
    if subs_f:
        subs = [p for p in subs if p.name in subs_f]
    print(f"受试者={len(subs)} 模态={mods} 临时目录={tmp_dir}")

    rows = []
    for s in subs:
        rows.extend(prep_subject(s, tmp_dir, mods))

    manifest = tmp_dir / "manifest.tsv"
    with manifest.open("w", encoding="utf-8") as fh:
        fh.write("subject\tmod\tt1\tb0\tparams\n")
        for r in rows:
            fh.write(f"{r['subject']}\t{r['mod']}\t{r['t1']}\t{r['b0']}"
                     f"\t{r['params']}\n")
    print(f"manifest 行数: {len(rows)}")
    for r in rows[:5]:
        print("  ", r["subject"], r["mod"], Path(r["b0"]).name)


if __name__ == "__main__":
    main()