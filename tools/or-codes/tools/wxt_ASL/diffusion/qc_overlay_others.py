# -*- coding: utf-8 -*-
"""
qc_overlay_others.py - 生成"其他受试者"配准的真叠加 QC 图 (T1 灰 + 基准像红)
================================================================================
MATLAB 内置 QC 图并非叠加, 无法直观判断对齐。本脚本将重采样后的基准像
(coreg_<mod>_<b0|adc>_to_T1.nii.gz) 以红色叠加到 T1 灰底上, 输出多层面 PNG。

用法:
  python qc_overlay_others.py            # 全部
  python qc_overlay_others.py Sub4_ZHU_MIN_HUI
"""
from __future__ import annotations
import os

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import nibabel as nib
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
OUT_ROOT    = Path(os.environ.get("OUT_ROOT", ""))
TMP_DIR     = Path(os.environ.get("TMP_DIR", ""))
GROUPS_JSON = TMP_DIR / "targets_groups.json"
QC_DIR      = Path(os.environ.get("QC_DIR", ""))
N_SLICES    = 6
# ====================================================================


def norm(d: np.ndarray) -> np.ndarray:
    """稳健归一化到 0..1 (1%~99% 分位)。"""
    lo, hi = np.percentile(d, 1), np.percentile(d, 99)
    return np.clip((d - lo) / max(1e-9, hi - lo), 0, 1)


def overlay(tag: str, mod: str, ref_path: Path):
    t1 = nib.load(str(TMP_DIR / ("%s_T1w.nii" % tag)))
    rf = nib.load(str(ref_path))
    a = np.asarray(t1.dataobj, dtype=np.float32)
    b = np.asarray(rf.dataobj, dtype=np.float32)
    if a.shape != b.shape:
        print("  [skip] %s %s shape mismatch %s vs %s" % (tag, mod, a.shape, b.shape))
        return
    an, bn = norm(a), norm(b)
    # 以基准像质心层为中心取层
    idx = np.argwhere(bn > 0.2)
    cz = int(idx[:, 2].mean()) if len(idx) else a.shape[2] // 2
    zs = np.linspace(max(0, cz - a.shape[2] // 5),
                     min(a.shape[2] - 1, cz + a.shape[2] // 5),
                     N_SLICES).astype(int)
    fig, axes = plt.subplots(2, N_SLICES, figsize=(3 * N_SLICES, 6))
    for i, z in enumerate(zs):
        axes[0, i].imshow(np.rot90(an[:, :, z]), cmap="gray", vmin=0, vmax=1)
        axes[0, i].set_title("T1 z=%d" % z, fontsize=8)
        rgb = np.zeros((*np.rot90(an[:, :, z]).shape, 3), dtype=np.float32)
        rgb[..., 1] = np.rot90(an[:, :, z])            # T1 -> 绿
        rgb[..., 0] = np.rot90(bn[:, :, z])            # ref -> 红
        axes[1, i].imshow(np.clip(rgb, 0, 1))
        axes[1, i].set_title("overlay", fontsize=8)
        for r in (0, 1):
            axes[r, i].axis("off")
    fig.suptitle("%s  %s   (green=T1, red=%s)" % (tag, mod, ref_path.name), fontsize=11)
    fig.tight_layout()
    QC_DIR.mkdir(parents=True, exist_ok=True)
    out = QC_DIR / ("qc_overlay_%s_%s.png" % (tag, mod))
    fig.savefig(str(out), dpi=70)
    plt.close(fig)
    print("  saved %s" % out)


def main() -> None:
    groups = json.loads(GROUPS_JSON.read_text(encoding="utf-8"))
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    for tag in sorted(groups):
        if filt and filt not in tag:
            continue
        for mod, grp in sorted(groups[tag].items()):
            d = OUT_ROOT / tag
            cand = [d / ("coreg_%s_%s_to_T1.nii.gz" % (mod, s))
                    for s in ("adc", "b0")]
            ref = next((p for p in cand if p.exists()), None)
            if ref is None:
                print("  [miss] %s %s" % (tag, mod))
                continue
            overlay(tag, mod, ref)


if __name__ == "__main__":
    main()
