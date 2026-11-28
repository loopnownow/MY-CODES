# -*- coding: utf-8 -*-
"""
fit_dwi_params.py - 从原始 DWI 拟合定量参数 (原生 DWI 空间)
================================================================
模态与参数:
  dti  (1 b0 + N b1000, bvec 有效) -> FA, MD, AD, RD      [dipy 张量]
  ivim (8 个 b 值)                 -> D, Dstar, f         [向量化网格拟合]
  dki  (4 个 b 值, bvec 全零)      -> D_app, K_app        [各向同性 1D 近似]

输出:
  derivatives/Diffusion_Params/sub-XXX/<mod>_<PARAM>.nii.gz   (原生 DWI 网格)
  derivatives/Diffusion_Params/sub-XXX/<mod>_b0.nii.gz        (b0, 供重采样配准用)

用法:
  python fit_dwi_params.py                      # 全部
  python fit_dwi_params.py --subs sub-007 --mods dti,dki
  python fit_dwi_params.py --force
"""
from __future__ import annotations
import os

import argparse
import sys
import time
from pathlib import Path

import nibabel as nib
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
# 配置区 (关键参数置顶)
# ====================================================================
STUDY_ROOT = Path(os.environ.get("STUDY_ROOT", ""))
RAW_DIR    = STUDY_ROOT / "rawdata"
OUT_ROOT   = STUDY_ROOT / "derivatives" / "Diffusion_Params"
MOD_ENTITY = {"dti": "acq-dti", "dki": "acq-dki", "ivim": "acq-ivim"}

# IVIM 网格搜索范围 (mm^2/s)
IVIM_D_GRID   = np.linspace(0.2e-3, 3.0e-3, 15)
IVIM_DSTAR_G  = np.concatenate([np.linspace(2e-3, 20e-3, 10),
                                np.linspace(25e-3, 150e-3, 11)])

# DKI 各向同性 1D 近似网格
DKI_D_GRID = np.linspace(0.2e-3, 3.0e-3, 15)
DKI_K_GRID = np.linspace(0.0, 3.0, 15)

# 掩膜阈值 (相对 b0 的 99 百分位的比例)
MASK_FRAC = 0.10
FORCE     = False
SUBS_FILTER: tuple = ()
MODS_FILTER = ("dti", "dki", "ivim")
# ====================================================================


def read_bvals_bvecs(bval_f: Path, bvec_f: Path):
    bvals = np.array([float(x) for x in bval_f.read_text().split()], dtype=float)
    bvecs = np.array([[float(x) for x in ln.split()] for ln in
                      bvec_f.read_text().splitlines() if ln.strip()], dtype=float)
    if bvecs.shape[0] != 3 and bvecs.shape[1] == 3:
        bvecs = bvecs.T
    return bvals, bvecs


def make_mask(b0: np.ndarray) -> np.ndarray:
    """简单稳健掩膜: b0 > 0.10 * (99 百分位)。"""
    pos = b0[b0 > 0]
    thr = np.percentile(pos, 99) if pos.size else 1.0
    return b0 > (MASK_FRAC * thr)


def save_map(data: np.ndarray, affine, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(np.asarray(data, dtype=np.float32), affine), str(path))



# ---------------------------------------------------------------- DTI ----
def fit_dti(data, bvals, bvecs, mask, affine, out_dir, mod) -> dict:
    """dipy 张量拟合 (WLS) -> FA/MD/AD/RD。"""
    from dipy.core.gradients import gradient_table
    from dipy.reconst.dti import TensorModel

    gtab = gradient_table(bvals, bvecs=bvecs.T, b0_threshold=50)
    ten = TensorModel(gtab, fit_method="WLS")
    tf = ten.fit(data, mask=mask)

    def _masked(arr, scale=1.0):
        out = np.asarray(arr, dtype=np.float32) * scale
        out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
        out[~mask] = 0.0
        return out

    fa = np.clip(_masked(tf.fa), 0, 1)
    md = _masked(tf.md, 1e3)      # -> 1e-3 mm2/s
    ad = _masked(tf.ad, 1e3)
    rd = _masked(tf.rd, 1e3)
    for nm, arr in (("FA", fa), ("MD", md), ("AD", ad), ("RD", rd)):
        save_map(arr, affine, out_dir / f"{mod}_{nm}.nii.gz")
    return {"n_vox": int(mask.sum()),
            "fa_mean": float(fa[mask].mean()) if mask.sum() else 0.0,
            "md_mean": float(md[mask].mean()) if mask.sum() else 0.0}



# --------------------------------------------------------------- IVIM ----
def fit_ivim(data, bvals, mask, affine, out_dir, mod) -> dict:
    """向量化 IVIM: 网格 (D, D*) + 闭式线性解 (A=S0(1-f), B=S0*f)。"""
    order = np.argsort(bvals)
    b = bvals[order]
    vox = data[..., order][mask].astype(np.float64)      # (V, nB)
    if vox.shape[0] == 0:
        return {"n_vox": 0}
    nB = b.shape[0]
    best_res = np.full(vox.shape[0], np.inf)
    best = np.zeros((vox.shape[0], 3))                    # D, D*, f
    for D in IVIM_D_GRID:
        eD = np.exp(-b * D)
        for Ds in IVIM_DSTAR_G:
            eS = np.exp(-b * Ds)
            M = np.stack([eD, eS], axis=1)               # (nB,2)
            coef, *_ = np.linalg.lstsq(M, vox.T, rcond=None)   # (2,V)
            A, B = coef[0], coef[1]
            r = ((vox.T - M @ coef) ** 2).sum(0)
            better = (r < best_res) & (A >= 0) & (B >= 0)
            if np.any(better):
                best_res[better] = r[better]
                tot = A[better] + B[better] + 1e-12
                best[better, 0] = D
                best[better, 1] = Ds
                best[better, 2] = B[better] / tot
    Dm = np.zeros(mask.shape, np.float32); Dst = np.zeros(mask.shape, np.float32)
    fm = np.zeros(mask.shape, np.float32); rm = np.zeros(mask.shape, np.float32)
    Dm[mask] = best[:, 0] * 1e3
    Dst[mask] = best[:, 1] * 1e3
    fm[mask] = best[:, 2]
    rm[mask] = np.sqrt(best_res / nB)
    save_map(Dm, affine, out_dir / f"{mod}_D.nii.gz")
    save_map(Dst, affine, out_dir / f"{mod}_Dstar.nii.gz")
    save_map(fm, affine, out_dir / f"{mod}_f.nii.gz")
    save_map(rm, affine, out_dir / f"{mod}_fitresid.nii.gz")
    return {"n_vox": int(mask.sum()), "f_mean": float(fm[mask].mean()),
            "D_mean": float(Dm[mask].mean())}


# ---------------------------------------------- DKI (isotropic only) ----
def fit_dki_iso(data, bvals, mask, affine, out_dir, mod) -> dict:
    """各向同性 1D 近似 S(b)=S0*exp(-bD+b^2 D^2 K/6)。⚠️ 非标准 MK。"""
    vox = data[mask].astype(np.float64)                   # (V,4)
    if vox.shape[0] == 0:
        return {"n_vox": 0}
    b = bvals.astype(np.float64)[None, :]                 # (1,4)
    best_res = np.full(vox.shape[0], np.inf)
    best = np.zeros((vox.shape[0], 2))                    # D, K
    for D in DKI_D_GRID:
        for K in DKI_K_GRID:
            model = np.exp(-b * D + (b ** 2) * (D ** 2) * K / 6.0)   # (1,4)
            s0 = (vox * model).sum(1) / np.maximum((model ** 2).sum(1), 1e-12)
            r = ((vox - s0[:, None] * model) ** 2).sum(1)
            better = (r < best_res) & (s0 > 0)
            if np.any(better):
                best_res[better] = r[better]
                best[better, 0] = D
                best[better, 1] = K
    Dm = np.zeros(mask.shape, np.float32); Km = np.zeros(mask.shape, np.float32)
    rm = np.zeros(mask.shape, np.float32)
    Dm[mask] = best[:, 0] * 1e3
    Km[mask] = best[:, 1]
    rm[mask] = np.sqrt(best_res / 4)
    save_map(Dm, affine, out_dir / f"{mod}_Dapp.nii.gz")
    save_map(Km, affine, out_dir / f"{mod}_Kapp.nii.gz")
    save_map(rm, affine, out_dir / f"{mod}_fitresid.nii.gz")
    return {"n_vox": int(mask.sum()), "Dapp_mean": float(Dm[mask].mean()),
            "Kapp_mean": float(Km[mask].mean())}



# ------------------------------------------------------------- 主流程 ----
def process_one(sub: str, mod: str, force: bool) -> dict | None:
    """处理单个受试者单模态。返回 QC 行。"""
    ent = MOD_ENTITY[mod]
    dwi_f = RAW_DIR / sub / "dwi" / f"{sub}_{ent}_dwi.nii.gz"
    bval_f = RAW_DIR / sub / "dwi" / f"{sub}_{ent}_dwi.bval"
    bvec_f = RAW_DIR / sub / "dwi" / f"{sub}_{ent}_dwi.bvec"
    if not (dwi_f.exists() and bval_f.exists()):
        return None
    out_dir = OUT_ROOT / sub
    stamp = out_dir / f".done_{mod}"
    if stamp.exists() and not force:
        return {"subject": sub, "mod": mod, "status": "skip"}

    t0 = time.time()
    img = nib.load(str(dwi_f))
    data = np.asarray(img.dataobj, dtype=np.float32)
    affine = img.affine
    bvals, bvecs = read_bvals_bvecs(bval_f, bvec_f)
    if len(bvals) != data.shape[3]:
        print(f"  [SKIP] {sub} {mod}: bval 数({len(bvals)}) != 卷数({data.shape[3]})")
        return None
    b0_idx = np.where(bvals < 50)[0]
    b0 = data[..., b0_idx].mean(-1) if b0_idx.size else data[..., 0]
    mask = make_mask(b0)
    save_map(b0, affine, out_dir / f"{mod}_b0.nii.gz")

    if mod == "dti":
        info = fit_dti(data, bvals, bvecs, mask, affine, out_dir, mod)
    elif mod == "ivim":
        info = fit_ivim(data, bvals, mask, affine, out_dir, mod)
    elif mod == "dki":
        info = fit_dki_iso(data, bvals, mask, affine, out_dir, mod)
    else:
        return None

    stamp.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    row = {"subject": sub, "mod": mod, "status": "ok",
           "sec": round(time.time() - t0, 1)}
    row.update(info)
    return row


def main() -> None:
    global FORCE, SUBS_FILTER, MODS_FILTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--subs", type=str, default="")
    ap.add_argument("--mods", type=str, default="")
    args = ap.parse_args()
    FORCE = FORCE or args.force
    if args.subs:
        SUBS_FILTER = tuple(x.strip() for x in args.subs.split(",") if x.strip())
    if args.mods:
        MODS_FILTER = tuple(x.strip() for x in args.mods.split(",") if x.strip())

    subs = sorted(p.name for p in RAW_DIR.iterdir()
                  if p.is_dir() and p.name.startswith("sub-"))
    if SUBS_FILTER:
        subs = [s for s in subs if s in SUBS_FILTER]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for i, sub in enumerate(subs, 1):
        for mod in MODS_FILTER:
            r = process_one(sub, mod, FORCE)
            if r:
                rows.append(r)
                print(f"[{i}/{len(subs)}] {sub} {mod}: {r.get('status')} "
                      f"{r.get('sec','')}s {r.get('n_vox','')}")
        # 增量写日志 (防中断丢失)
        log = OUT_ROOT / "fit_log.tsv"
        with log.open("w", encoding="utf-8") as fh:
            keys = sorted({k for r in rows for k in r})
            fh.write("\t".join(keys) + "\n")
            for r in rows:
                fh.write("\t".join(str(r.get(k, "")) for k in keys) + "\n")
    print(f"完成: {len(rows)} 条记录 -> {OUT_ROOT / 'fit_log.tsv'}")


if __name__ == "__main__":
    main()

