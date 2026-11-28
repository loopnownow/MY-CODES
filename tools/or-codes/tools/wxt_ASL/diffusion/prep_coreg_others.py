# -*- coding: utf-8 -*-
"""
prep_coreg_others.py - 为"其他含 DTI/DKI/IVIM 的受试者"准备 SPM 配准输入
================================================================================
背景
----
现有 derivatives/Diffusion_Coreg/ 已处理 subjects_matrix.tsv 中的 126 名受试者
(T1+ASL 队列)。dingallcases 下还有若干受试者含 DTI/DKI/IVIM 但未处理:
  * A 组: 有原始 4D DWI  -> 基准像 = b0 (b<50 体素均值), 走与 prep_coreg_files.py
          完全相同的算法 (含质心 affine 修正, 因原始 4D 的 sform 世界坐标普遍失真)
  * B 组: 仅有派生参数图 -> 无 b0 可提, 改用该模态的 ADC 图作基准像;
          其 affine 本身正常 (质心偏移仅 3~20mm), 故不做质心修正

本脚本:
  1. 扫描 dingallcases (根目录 + rawnii), 排除 subjects_matrix.tsv 已处理者;
  2. 按模态判定 A/B 组, 解压 T1 / 生成基准像 / 解压派生图 到临时目录;
  3. 输出 manifest_others.tsv 供 coreg_diffusion_to_t1.m 原样复用;
  4. 输出 targets_groups.json 记录每组标签, 供 verify 脚本重命名 A/B 组的基准像。

用法:
  python prep_coreg_others.py
  python prep_coreg_others.py --tmp <dir>
"""
from __future__ import annotations
import os

import argparse
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ====================================================================
# 1. 配置区  ------ 置顶, 按需修改
# ====================================================================
SRC_ROOT   = Path(os.environ.get("SRC_ROOT", ""))          # 数据源
OUT_ROOT   = Path(os.environ.get("OUT_ROOT", ""))
TMP_DIR    = Path(os.environ.get("TMP_DIR", ""))
MATRIX_TSV = Path(r"{lab_root}\_DataBase\SSD\_share\wxt_ASL\subjects_matrix.tsv")   # 表不进 L:

# T1 候选关键词 (按 ^\d+_<kw>\.nii\.gz$ 精确匹配)。同一受试者可能有多个
# 采集且存在"坏采集"(纯噪声, 见 t1_smoothness), 故按平滑度自动优选。
K_T1_KEYS = ["t1_mprage_tra_p2_iso", "spc_ir_cor_p2_iso"]
# 平滑度 r = mean|grad I|^2 / var(I): 真实解剖 ~0.13-0.25, 纯噪声 ~1.0
T1_SMOOTH_MAX = 0.5

# 原始 4D DWI 关键词 (须文件名精确匹配 ^\d+_<kw>\.nii\.gz$ 且同名 .bval 存在)
MOD_RAW = {"dti": "ep2d_diff_mddw_25_p2",
           "dki": "ep2d_diff_dki_tracew",
           "ivim": "ep2d_diff_ivim_tracew"}

# B 组基准像关键词 (该模态的 ADC 派生图)
MOD_REFMAP = {"dti": "ep2d_diff_mddw_25_p2_adc",
              "dki": "ep2d_diff_dki_adc",
              "ivim": "ep2d_diff_ivim_adc"}

# 派生参数图: 模态 -> [(关键词, 输出标签)]
DERIVED_MAP = {
    "dti":  [("ep2d_diff_mddw_25_p2_adc", "ADC"),
             ("ep2d_diff_mddw_25_p2_tracew", "TRACEW"),
             ("ep2d_diff_mddw_25_p2_fa", "FA")],
    "dki":  [("ep2d_diff_dki_adc", "ADC")],
    "ivim": [("ep2d_diff_ivim_adc", "ADC")],
}
MODS = ("dti", "dki", "ivim")
THR_FRAC = 0.2            # 质心阈值 (占最大值比例)
B0_BVAL_MAX = 50          # b0 判定阈值
# ====================================================================


def gunzip_copy(src: Path, dst: Path) -> bool:
    """解压 .nii.gz -> 普通 .nii。"""
    try:
        with gzip.open(str(src), "rb") as f_in, open(str(dst), "wb") as f_out:
            shutil.copyfileobj(f_in, f_out, 1 << 20)
        return True
    except Exception:
        return False


def brain_com_voxel_data(data: np.ndarray, thr_frac: float = THR_FRAC) -> np.ndarray:
    """3D 数组质心 (1-based voxel 坐标, 同 SPM 索引)。"""
    thr = data.max() * thr_frac
    idx = np.argwhere(data > thr)
    if len(idx) == 0:
        return np.array(data.shape, float) / 2.0 + 1.0
    return idx.mean(0) + 1.0


def brain_com_voxel(img: nib.Nifti1Image, thr_frac: float = THR_FRAC) -> np.ndarray:
    return brain_com_voxel_data(img.get_fdata(), thr_frac)


def fit_affine(t1_img: nib.Nifti1Image, ref_data: np.ndarray,
               ref_aff: np.ndarray) -> np.ndarray:
    """基于质心修正基准像 affine: 使基准像质心(世界)对齐 T1 质心(世界)。
    保留体素尺寸与方向, 仅平移 origin。仅用于原始 4D 的 b0 (sform 失真)。"""
    c_ref = brain_com_voxel_data(ref_data)
    c_t1 = brain_com_voxel(t1_img)
    w_ref = (ref_aff @ np.append(c_ref, 1.0))[:3]
    w_t1 = (t1_img.affine @ np.append(c_t1, 1.0))[:3]
    new_aff = ref_aff.copy()
    new_aff[:3, 3] += (w_t1 - w_ref)
    return new_aff


def name_key(tag: str) -> str:
    """与 bids_converter._name_key 一致: 去掉编号前缀, 大写。"""
    if "_" in tag:
        return tag.split("_", 1)[1].replace("_", " ").strip().upper()
    return tag.upper()


def discover_candidates(src_root: Path) -> list:
    """扫描根目录(须含 rawnii 子目录) + rawnii 子目录; 按 name_key 去重(根优先)。"""
    seen = {}

    def add(tag: str, raw_dir: Path) -> None:
        k = name_key(tag)
        if k not in seen:
            seen[k] = (tag, raw_dir)

    if src_root.is_dir():
        for d in sorted(p for p in src_root.iterdir()
                        if p.is_dir() and p.name != "rawnii"):
            rp = d / "rawnii"
            if rp.is_dir():
                add(d.name, rp)
    rr = src_root / "rawnii"
    if rr.is_dir():
        for d in sorted(p for p in rr.iterdir() if p.is_dir()):
            add(d.name, d)
    return list(seen.values())


def load_processed() -> set:
    """subjects_matrix.tsv 第 3 列 source_dir (已处理受试者 tag)。"""
    if not MATRIX_TSV.exists():
        return set()
    out = set()
    for line in MATRIX_TSV.read_text(encoding="utf-8").splitlines()[1:]:
        f = line.split("\t")
        if len(f) >= 3 and f[2].strip():
            out.add(f[2].strip())
    return out


def exact_hit(files: list, kw: str):
    r"""文件名精确匹配 ^\d+_<kw>\.nii\.gz$。"""
    pat = re.compile(r"^\d+_" + re.escape(kw) + r"\.nii\.gz$")
    for f in files:
        if pat.match(f):
            return f
    return None


def substr_hit(files: list, kw: str):
    """文件名包含 <kw> 且以 .nii.gz 结尾。"""
    for f in files:
        if kw in f and f.endswith(".nii.gz"):
            return f
    return None


def t1_smoothness(path: Path) -> float:
    """T1 采集质量指标 r = mean|grad I|^2 / var(I)。真实解剖 <0.5, 纯噪声 ~1.0。"""
    a = np.asarray(nib.load(str(path)).dataobj, dtype=np.float32)
    var = float(a.var())
    if var <= 0:
        return 1e9
    g2 = sum(float((x * x).mean()) for x in np.gradient(a))
    return g2 / var


def pick_t1(base: Path):
    """在候选 T1 中选平滑度最低者 (自动排除纯噪声的坏采集)。
    返回 (文件名, 平滑度r, 候选数) 或 (None, None, 0)。"""
    pats = [re.compile(r"^\d+_" + re.escape(kw) + r"\.nii\.gz$") for kw in K_T1_KEYS]
    hits = [f for f in sorted(base.iterdir())
            if f.is_file() and any(p.match(f.name) for p in pats)]
    if not hits:
        return None, None, 0
    scored = sorted((t1_smoothness(f), f.name) for f in hits)
    return scored[0][1], scored[0][0], len(hits)


def classify(base: Path):
    """判定各模态分组。返回 {mod: {...}}。"""
    files = [f.name for f in base.iterdir() if f.is_file()]
    mods = {}
    for mod in MODS:
        raw_kw = MOD_RAW[mod]
        raw_nii = exact_hit(files, raw_kw)
        raw_bval = ("%s.bval" % raw_nii[:-7]) if raw_nii else None
        has_raw = bool(raw_nii) and raw_bval in files
        ref_map = substr_hit(files, MOD_REFMAP[mod])
        if not has_raw and not ref_map:
            continue
        derived = []
        for kw, label in DERIVED_MAP[mod]:
            hit = substr_hit(files, kw)
            if hit:
                derived.append((label, hit))
        mods[mod] = {
            "group": "A" if has_raw else "B",
            "raw_nii": raw_nii if has_raw else None,
            "raw_bval": raw_bval if has_raw else None,
            "ref_map": ref_map,
            "derived": derived,
        }
    return mods


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", type=Path, default=TMP_DIR)
    args = ap.parse_args()
    tmp_dir = args.tmp
    tmp_dir.mkdir(parents=True, exist_ok=True)

    processed = load_processed()
    cands = discover_candidates(SRC_ROOT)

    rows = []
    groups = {}          # tag -> {mod: "A"/"B"}
    for tag, base in cands:
        if tag in processed:
            continue
        mods = classify(base)
        t1_name, t1_r, n_t1 = pick_t1(base)
        if t1_name is None or not mods:
            continue
        if n_t1 > 1 or (t1_r or 0) > T1_SMOOTH_MAX:
            print("  [T1] %s: 候选 %d 个 -> 采用 %s (平滑度 %.3f)"
                  % (tag, n_t1, t1_name, t1_r))
        if t1_r is not None and t1_r > T1_SMOOTH_MAX:
            print("  [WARN] %s: T1 平滑度 %.3f > %.2f, 疑为坏采集"
                  % (tag, t1_r, T1_SMOOTH_MAX))
        # T1 解压 (始终重建: 上游 T1 优选结果变化时避免读到陈旧缓存)
        t1_nii = tmp_dir / ("%s_T1w.nii" % tag)
        if not gunzip_copy(base / t1_name, t1_nii):
            print("[ERR] gunzip T1: %s" % (base / t1_name))
            continue
        t1_img = nib.load(str(t1_nii))

        for mod, info in mods.items():
            # 基准像始终重建 (依赖 T1 的质心修正, T1 变化时必须刷新)
            ref_nii = tmp_dir / ("%s_%s_ref.nii" % (tag, mod))
            if info["group"] == "A":            # b0 = b<50 体素均值 (原始 4D)
                img = nib.load(str(base / info["raw_nii"]))
                data = img.get_fdata()
                bvals = None
                bval_f = base / info["raw_bval"]
                if bval_f.exists():
                    bvals = np.array([float(x) for x in bval_f.read_text().split()])
                    if bvals.size != data.shape[3]:
                        bvals = None
                idx = list(np.where(bvals < B0_BVAL_MAX)[0]) if bvals is not None else [0]
                idx = idx or [0]
                ref_data = np.mean(data[..., idx], axis=-1)
                ref_aff = fit_affine(t1_img, ref_data, img.affine)   # 质心修正
            else:                               # B 组: 派生 ADC 图, 原生 affine
                img = nib.load(str(base / info["ref_map"]))
                ref_data = img.get_fdata()
                if ref_data.ndim == 4:
                    ref_data = ref_data[..., 0]
                ref_aff = img.affine.copy()     # affine 正常, 不修正
            nib.save(nib.Nifti1Image(ref_data.astype(np.float32), ref_aff),
                     str(ref_nii))

            param_list = []
            for label, src_name in info["derived"]:
                dst = tmp_dir / ("%s_%s_%s.nii" % (tag, mod, label))
                if not dst.exists() and not gunzip_copy(base / src_name, dst):
                    continue
                param_list.append("%s=%s" % (label, dst))
            rows.append({"subject": tag, "mod": mod, "group": info["group"],
                         "t1": str(t1_nii), "ref": str(ref_nii),
                         "params": ",".join(param_list)})
            groups.setdefault(tag, {})[mod] = info["group"]

    manifest = tmp_dir / "manifest_others.tsv"
    with manifest.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("subject\tmod\tt1\tb0\tparams\n")
        for r in rows:
            fh.write("%s\t%s\t%s\t%s\t%s\n"
                     % (r["subject"], r["mod"], r["t1"], r["ref"], r["params"]))
    (tmp_dir / "targets_groups.json").write_text(
        json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")

    n_a = sum(1 for r in rows if r["group"] == "A")
    n_b = sum(1 for r in rows if r["group"] == "B")
    ppl_a = {t for t, g in groups.items() if "A" in g.values()}
    ppl_b = {t for t, g in groups.items() if "B" in g.values()}
    print("=" * 64)
    print("发现待处理受试者: %d   manifest 行(受试者x模态): %d" % (len(groups), len(rows)))
    print("  A 组(原始4D->b0): %d 人 / %d 模态" % (len(ppl_a), n_a))
    print("  B 组(仅派生图->ADC): %d 人 / %d 模态" % (len(ppl_b), n_b))
    print("-" * 64)
    for r in rows:
        npar = len(r["params"].split(",")) if r["params"] else 0
        print("  %s  %-22s %-5s params=%d" % (r["group"], r["subject"], r["mod"], npar))
    print("-" * 64)
    print("manifest : %s" % manifest)
    print("groups   : %s" % (tmp_dir / "targets_groups.json"))


if __name__ == "__main__":
    main()
