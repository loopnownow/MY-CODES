# -*- coding: utf-8 -*-
"""
verify_coreg_others.py - 校验"其他受试者"配准产物并规范化 B 组基准像文件名
================================================================================
1. 核对每个 (受试者, 模态) 的产物齐全 (matrix / ref / .done / QC);
2. B 组基准像并非 b0, 将 coreg_<mod>_b0_to_T1.nii.gz 重命名为 coreg_<mod>_adc_to_T1.nii.gz;
3. 计算重采样基准像与 T1 脑掩膜的重叠 Dice (低值告警), 汇总输出报告。

用法:
  python verify_coreg_others.py
"""
from __future__ import annotations
import os

import json
import sys
from pathlib import Path

import nibabel as nib
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
# 配置区
# ====================================================================
OUT_ROOT    = Path(os.environ.get("OUT_ROOT", ""))
TMP_DIR     = Path(os.environ.get("TMP_DIR", ""))
GROUPS_JSON = TMP_DIR / "targets_groups.json"
T1_FRAC     = 0.15          # T1 脑掩膜阈值 (占最大值比例)
REF_FRAC    = 0.20          # 基准像掩膜阈值
DICE_FLAG   = 0.20          # Dice 低于此值告警
# ====================================================================


def mask_of(img: nib.Nifti1Image, frac: float) -> np.ndarray:
    d = np.asarray(img.dataobj, dtype=np.float32)
    return d > (frac * float(d.max()))


def dice(a: np.ndarray, b: np.ndarray) -> float:
    s = int(a.sum()) + int(b.sum())
    return float(2 * np.logical_and(a, b).sum() / s) if s else 0.0



def main() -> None:
    if not GROUPS_JSON.exists():
        print("缺少 %s (请先运行 prep_coreg_others.py)" % GROUPS_JSON)
        raise SystemExit(1)
    groups = json.loads(GROUPS_JSON.read_text(encoding="utf-8"))

    print("=" * 78)
    print("%-22s %-5s %-4s %-7s %s" % ("subject", "mod", "grp", "dice", "files"))
    print("-" * 78)
    n_ok = n_bad = 0
    flagged = []
    for tag in sorted(groups):
        for mod, grp in sorted(groups[tag].items()):
            d = OUT_ROOT / tag
            mat = d / ("coreg_matrix_%s_%s.mat" % (tag, mod))
            ref_b0 = d / ("coreg_%s_b0_to_T1.nii.gz" % mod)
            ref_adc = d / ("coreg_%s_adc_to_T1.nii.gz" % mod)
            done = d / (".done_%s" % mod)
            qc = d / ("qc_coreg_%s_%s.png" % (tag, mod))
            t1 = TMP_DIR / ("%s_T1w.nii" % tag)

            # B 组基准像并非 b0 -> 规范化文件名 (覆盖上一轮可能存在的 _adc_)
            if grp == "B" and ref_b0.exists():
                ref_b0.replace(ref_adc)
            ref = ref_adc if ref_adc.exists() else ref_b0

            # 规范化 params 文件名: MATLAB 输出 coreg_<mod>_<tag>_<mod>_<LABEL>
            # -> 与现有 126 人一致的 coreg_<mod>_<LABEL> (覆盖旧文件)
            pdir = d / "params"
            if pdir.is_dir():
                pfx = "coreg_%s_%s_%s_" % (mod, tag, mod)
                for f in list(pdir.glob(pfx + "*.nii.gz")):
                    label = f.name[len(pfx):]
                    f.replace(pdir / ("coreg_%s_%s" % (mod, label)))

            missing = [p.name for p in (mat, done, qc, ref) if not p.exists()]
            dz = float("nan")
            if not missing and t1.exists():
                dz = dice(mask_of(nib.load(str(t1)), T1_FRAC),
                          mask_of(nib.load(str(ref)), REF_FRAC))
            if missing:
                n_bad += 1
                print("%-22s %-5s %-4s %7s  MISS:%s"
                      % (tag, mod, grp, "-", ",".join(missing)))
            else:
                n_ok += 1
                if dz < DICE_FLAG:
                    flagged.append((tag, mod, dz))
                print("%-22s %-5s %-4s %7.3f  OK" % (tag, mod, grp, dz))
    print("-" * 78)
    print("产物齐全: %d   缺失: %d" % (n_ok, n_bad))
    if flagged:
        print("Dice<%.2f 需人工看 QC:" % DICE_FLAG)
        for t, m, v in flagged:
            print("   %s %s dice=%.3f" % (t, m, v))
    else:
        print("Dice 无低值告警。")
    print("=" * 78)


if __name__ == "__main__":
    main()
