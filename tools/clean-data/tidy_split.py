#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tidy_split — 把 `SSD_imputed.xlsx` 拆成三个按模态分离的工作簿。

    1. `jfl_SSD.xlsx`            ← sheet perfusion
    2. `jfl_SSD_DTI_IVIM.xlsx`   ← DTI + IVIM 按 Name 外连接（主表 DTI_IVIM + merge_qc）
    3. `jfl_SSD_DKI.xlsx`        ← sheet DKI

源优先 `0del/SSD_imputed.xlsx`，否则子工程根目录 `SSD_imputed.xlsx`；**不修改源文件**。
合并复用 `tidy_merge.outer_merge_modalities`（原 `ssd/split_ssd_imputed.py`）。

用法::

    python tidy_split.py                    # profile 默认 ssd
    python tidy_split.py --dry-run          # 只打印将要写/删的路径
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tidy_merge import outer_merge_modalities  # noqa: E402
from tidy_paths import project_root  # noqa: E402
from tidy_profiles import SsdProfile, get_profile  # noqa: E402

__all__ = ["run_split", "main"]


def run_split(profile: SsdProfile, dry_run: bool = False) -> dict:
    """执行拆分。返回 {输出名: 路径}。"""
    p = profile
    ssd_root = project_root(p.root_profile)
    dti_root = project_root("ssd_dti_ivim")
    dki_root = project_root("ssd_dki")

    src_candidates = [ssd_root / name for name in p.split_src_names]
    src = next((x for x in src_candidates if x.exists()), None)
    if src is None:
        raise FileNotFoundError(
            "SSD_imputed.xlsx not found in: " + ", ".join(str(x) for x in src_candidates))

    out_perf = ssd_root / "jfl_SSD.xlsx"
    out_dti_ivim = dti_root / "jfl_SSD_DTI_IVIM.xlsx"
    out_dki = dki_root / "jfl_SSD_DKI.xlsx"
    legacy = [project_root(sub) / name for sub, name in p.split_legacy_outs]

    print("source:", src)
    if dry_run:
        print("[dry-run] would write:")
        for x in (out_perf, out_dti_ivim, out_dki):
            print("   ", x)
        print("[dry-run] would remove legacy (if present):")
        for x in legacy:
            print("   ", x, "exists" if x.exists() else "-")
        return {}

    perf = pd.read_excel(src, sheet_name="perfusion")
    dti = pd.read_excel(src, sheet_name="DTI")
    ivim = pd.read_excel(src, sheet_name="IVIM")
    dki = pd.read_excel(src, sheet_name="DKI")

    for df in (perf, dti, ivim, dki):
        if "Name" in df.columns:
            df["Name"] = df["Name"].astype(str).str.strip()

    out_perf.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_perf, engine="openpyxl") as w:
        perf.to_excel(w, sheet_name="perfusion", index=False)
    print(f"[1] {out_perf.name}: n={len(perf)}, cols={perf.shape[1]}, "
          f"Group={perf['Group'].value_counts().to_dict() if 'Group' in perf.columns else {}}")

    merged, qc = outer_merge_modalities(dti, ivim, left_tag="dti", right_tag="ivim",
                                        img_sep=p.split_img_sep)
    out_dti_ivim.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_dti_ivim, engine="openpyxl") as w:
        merged.to_excel(w, sheet_name="DTI_IVIM", index=False)
        qc.to_excel(w, sheet_name="merge_qc", index=False)
    n_img = sum(p.split_img_sep in str(c) for c in merged.columns)
    print(f"[2] {out_dti_ivim.name}: n={len(merged)}, cols={merged.shape[1]}, img={n_img}, "
          f"Group={merged['Group'].value_counts(dropna=False).to_dict()}, "
          f"has_dti={int(merged['has_dti'].sum())}, has_ivim={int(merged['has_ivim'].sum())}, "
          f"merge_qc={len(qc)}")

    out_dki.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_dki, engine="openpyxl") as w:
        dki.to_excel(w, sheet_name="DKI", index=False)
    print(f"[3] {out_dki.name}: n={len(dki)}, cols={dki.shape[1]}, "
          f"Group={dki['Group'].value_counts().to_dict() if 'Group' in dki.columns else {}}")

    for x in legacy:
        if x.exists():
            x.unlink()
            print(f"removed legacy: {x.name}")

    print("done. source untouched:", src)
    return {"perfusion": out_perf, "dti_ivim": out_dti_ivim, "dki": out_dki}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tidy_split", description="SSD_imputed.xlsx → 三个模态工作簿")
    ap.add_argument("--profile", default="ssd")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    profile = get_profile(args.profile)
    run_split(profile, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
