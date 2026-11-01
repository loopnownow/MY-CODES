#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tidy_complete — 统一「补全」入口，三种策略对应原来的三套补全代码。

策略
----
``gate``       零缺失门槛：按变量块取舍行、只留 100% 完整的列、断言零缺失后落表。
               原 `lxj_AN/build_complete.py`（profile ``lxj_AN``）。
               可加 ``--layout cln-rad-qc`` 额外产出 cln/rad/qc 三表（下游 RAD console 约定）。
``impute``     分组独立缺失插补（mice/median/iterative/missForest）+ 小数位对齐 + QC 表，
               插补前原件备份到 0del。原 `ssd/impute_ssd_dti_ivim.py`。
``paper_fill`` 真实值 + 按论文 mean±SD 补缺（带 is_synthetic_* 标记）+ 纯合成队列。
               原 `ssd/clinical_recover.py` 的 Phase 2/3（profile ``ssd``）。

用法::

    python tidy_complete.py --profile lxj_AN                       # gate
    python tidy_complete.py --profile lxj_AN --layout cln-rad-qc
    python tidy_complete.py --profile ssd --strategy impute
    python tidy_complete.py --profile ssd --strategy paper_fill
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 算法库根（`modules` 包所在）：2026-10-23 由 0script\stats-analysis 迁入 _code。
# 环境变量 STATS_ROOT 可覆盖；与 setup_cln_only.py / impute_cln_en.py 同一口径。
STATS_ROOT = Path(os.environ.get(
    "STATS_ROOT") or Path(__file__).resolve().parents[2])

from tidy_io import KEY_FILL, mark_key_column, nz, s, style_header  # noqa: E402
from tidy_paths import backup_to_del, project_root  # noqa: E402
from tidy_profiles import LxjAnProfile, SsdProfile, get_profile  # noqa: E402

__all__ = [
    "gate_complete",
    "impute_workbook",
    "paper_fill",
    "phase2_fill",
    "make_pure_synthetic",
    "exact_mean_sd",
    "main",
]


class ZeroMissingViolation(RuntimeError):
    """零缺失断言失败。"""


# ═════════════════════════════════════════════════════════════════
# 策略 1：gate（零缺失完整表）
# ═════════════════════════════════════════════════════════════════
def _layout_clin_rad_qc(
    profile: LxjAnProfile,
    ordered: Sequence[str],
    matrix: Sequence[Sequence[Any]],
) -> "Dict[str, Tuple[List[str], List[List[Any]]]]":
    """把零缺失表按下游 RAD console 约定切成 cln / rad / qc 三块。

    切法（与原 `lxj_AN_complete.xlsx` 现有结构一致）：
      cln = 人口学 + MMSE + MoCA；rad = 眼动数值指标；qc = 其余（范式名称/参数、时间、姓名等）。
    """
    p = profile
    if not ordered or ordered[0] != p.id_order[0]:
        raise ValueError(
            f"layout=cln-rad-qc 要求 ID 列 {p.id_order[0]!r} 是零缺失表首列；"
            f"当前首列={ordered[0] if ordered else None!r}，共 {len(ordered)} 列")
    eye = [c for c in ordered if c[:2] in p.eye_prefixes]
    eye_qc = [c for c in eye if any(t in c for t in p.layout_qc_extra_contains)]
    eye_rad = [c for c in eye if c not in eye_qc]

    clin_src = [c for c in p.layout_clin_cols if c == p.layout_id_col or c in ordered]
    qc_src = ([c for c in p.layout_qc_pre if c in ordered] + eye_qc
              + [c for c in p.layout_qc_post if c in ordered])
    rad_src = eye_rad

    def take(cols: Sequence[str], id_col: bool = True) -> Tuple[List[str], List[List[Any]]]:
        rename = dict(p.layout_rename)
        header = ([p.layout_id_col] if id_col else []) + [rename.get(c, c) for c in cols]
        body = []
        for row in matrix:
            vals = [row[ordered.index(c)] for c in cols]
            body.append(([row[0]] if id_col else []) + vals)
        return header, body

    clin_rest = [c for c in clin_src if c != p.layout_id_col]
    return {
        "cln": take(clin_rest),
        "rad": take(rad_src),
        "qc": take(qc_src),
    }


def gate_complete(
    profile: LxjAnProfile,
    src_path: Optional[Path] = None,
    out_path: Optional[Path] = None,
    layout: str = "single",
    verbose: bool = True,
) -> Path:
    """派生零缺失完整表。逐行等价于 `lxj_AN/build_complete.py`。"""
    p = profile
    src_path = Path(src_path) if src_path else p.root / p.out_merge
    out_path = Path(out_path) if out_path else p.root / p.out_complete
    if not src_path.exists():
        raise FileNotFoundError(
            f"输入总表不存在: {src_path}；请先跑 `python tidy_merge.py --profile {p.name}`"
            f"（或用 --src 指定）")

    def say(msg: str = "") -> None:
        if verbose:
            print(msg)

    # ── 读取总表 ──
    wb_src = load_workbook(src_path, read_only=True)
    ws = wb_src[p.master_sheet]
    rows = list(ws.iter_rows(values_only=True))
    hdr = [str(h) if h is not None else "" for h in rows[0]]
    raw = [list(r) for r in rows[1:]]
    n_row, n_col = len(raw), len(hdr)
    say("输入 %s: %d 行 x %d 列" % (p.master_sheet, n_row, n_col))
    hidx = {h: i for i, h in enumerate(hdr)}

    # ── 合并受教育程度 ──
    edu_map = dict(p.edu_map)
    edu: List[str] = []
    edu_from_c = 0
    for r in raw:
        a = s(r[hidx[p.edu_a_col]])
        c = s(r[hidx[p.edu_c_col]])
        if a:
            edu.append(edu_map.get(a, a))
        elif c:
            edu.append(edu_map.get(c, c))
            edu_from_c += 1
        else:
            edu.append("")
    say("\n%s: 合并后非空 %d/%d（其中 %d 人取自 C表%s）"
        % (p.edu_out_col, sum(1 for e in edu if e), n_row, edu_from_c, p.edu_c_col))

    # ── 行门槛 ──
    eye = [h for h in hdr if h[:2] in p.eye_prefixes]
    time_cols = [h for h in hdr if h in p.time_cols]
    req = list(p.core_cols) + eye + time_cols
    req_i = [hidx[c] for c in req]
    keep = [i for i, r in enumerate(raw)
            if nz(edu[i]) and all(nz(r[j]) for j in req_i)]
    say("行门槛: 核心变量(%d列)齐全 %d/%d 人" % (len(req), len(keep), n_row))

    drop_core = sum(1 for r in raw
                    if not all(nz(r[hidx[c]]) for c in p.core_cols if c != "是否为右利手"))
    drop_hand = sum(1 for r in raw if not nz(r[hidx["是否为右利手"]]))
    say("   其中: 右利手缺失 %d 人 / 其他核心变量缺失 %d 人（可重叠）" % (drop_hand, drop_core))

    # ── 列筛选 ──
    always_empty = [h for h in hdr if all(not nz(r[hidx[h]]) for r in raw)]
    exclude = set(always_empty) | set(p.always_exclude)
    cand = [h for h in hdr if h not in exclude]
    complete = [h for h in cand if all(nz(raw[i][hidx[h]]) for i in keep)]
    say("\n源数据整列全空 %d 列（已剔除）: %s" % (len(always_empty), ", ".join(always_empty)))
    say("在这些受试者上 100%% 完整的列: %d 列" % len(complete))

    # ── 列排序 ──
    ordered = [h for h in p.id_order if h == p.edu_out_col or h in complete]
    ordered += [h for h in eye + time_cols if h in complete and h not in ordered]
    ordered += [h for h in complete if h not in ordered]

    # ── 构建输出矩阵 ──
    matrix = [[edu[i] if h == p.edu_out_col else raw[i][hidx[h]] for h in ordered] for i in keep]

    viol = [(r + 2, ordered[c]) for r, row in enumerate(matrix)
            for c, v in enumerate(row)
            if not nz(v) or str(v).strip() in ("None", "nan", "NaN")]
    if viol:
        msg = "零缺失断言失败，%d 处：%s" % (len(viol), viol[:10])
        print("\n!! " + msg)
        raise ZeroMissingViolation(msg)

    out_path.parent.mkdir(parents=True, exist_ok=True)

    if layout == "cln-rad-qc":
        blocks = _layout_clin_rad_qc(p, ordered, matrix)
        out = Workbook()
        out.remove(out.active)
        for name, (header, body) in blocks.items():
            w = out.create_sheet(name)
            w.append(header)
            for row in body:
                w.append(row)
            style_header(w, len(header))
            mark_key_column(w, 1, width=15)
        out.save(out_path)
        say("\n输出: %s（layout=cln-rad-qc）" % out_path)
        for name, (header, body) in blocks.items():
            say("   %-4s %d 行 x %d 列" % (name, len(body), len(header)))
        say("   工作表: %s" % out.sheetnames)
        return out_path

    out = Workbook()
    o = out.active
    o.title = p.complete_sheet
    o.append(list(ordered))
    for row in matrix:
        o.append(row)
    style_header(o, len(ordered))
    o.freeze_panes = "B2"
    o.column_dimensions["A"].width = 15
    for col in ("B", "C", "D", "E", "F", "H", "I", "J", "K"):
        o.column_dimensions[col].width = 12
    for r in range(2, o.max_row + 1):
        o.cell(row=r, column=1).fill = KEY_FILL
        o.cell(row=r, column=1).font = Font(bold=True)

    if out.sheetnames != [p.complete_sheet]:
        raise AssertionError(out.sheetnames)
    out.save(out_path)

    say("\n输出: %s" % out_path)
    say("工作表: %s（单表）" % out.sheetnames)
    say("尺寸: %d 行 x %d 列  —— 零缺失 ✓" % (len(matrix), len(ordered)))
    say("\n列清单:")
    for k, h in enumerate(ordered, 1):
        say("   %2d. %s" % (k, h))
    return out_path


# ═════════════════════════════════════════════════════════════════
# 策略 2：impute（分组独立插补）
# ═════════════════════════════════════════════════════════════════
def impute_workbook(
    profile: SsdProfile,
    src: Optional[Path] = None,
    cln_sheet: Optional[str] = None,
    rad_sheet: Optional[str] = None,
    group_col: Optional[str] = None,
    method: Optional[str] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """按 Group 分组插补 cln 表并写回，rad 原样保留，附 impute_qc。

    等价于 `ssd/impute_ssd_dti_ivim.py`（含备份到 0del、观测值不变校验）。
    """
    p = profile
    cln_sheet = cln_sheet or p.impute_cln_sheet
    rad_sheet = rad_sheet or p.impute_rad_sheet
    group_col = group_col or p.impute_group_col
    method = method or p.impute_method
    src = Path(src) if src else project_root(p.impute_profile) / p.impute_file
    if not src.exists():
        raise FileNotFoundError(src)

    # 依赖模块（懒加载：不做插补时无需安装 sklearn）
    if str(STATS_ROOT) not in sys.path:
        sys.path.insert(0, str(STATS_ROOT))
    sys.path.insert(0, str(src.parent))
    try:
        from modules.utils.u_impute import mean_decimal_places, z_impute
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            f"cannot import modules.utils.u_impute from {STATS_ROOT}: {e}") from e

    xl = pd.ExcelFile(src)
    if verbose:
        print("source sheets:", xl.sheet_names)
    cln = pd.read_excel(src, sheet_name=cln_sheet)
    rad = pd.read_excel(src, sheet_name=rad_sheet) if rad_sheet in xl.sheet_names else None

    backup = backup_to_del(src, name=p.impute_backup_name)
    if verbose:
        print(f"backup → {backup}" if backup else
              f"backup exists (kept): {Path(p.impute_backup_name)}")

    before_miss = cln.isna().sum()
    before_miss = before_miss[before_miss > 0]
    if verbose:
        print(f"cln shape={cln.shape}, {group_col}={cln[group_col].value_counts().to_dict()}")
        print(f"columns with missing before: {len(before_miss)}")

    imputed = z_impute(cln, group_col=group_col, method=method, verbose=verbose,
                       include_categorical=False, align_decimals=True)
    if imputed is None:
        raise RuntimeError("z_impute returned None")

    num_cols = [c for c in cln.columns
                if pd.api.types.is_numeric_dtype(cln[c]) and c != group_col]
    rows = []
    for c in num_cols:
        n_before = int(cln[c].isna().sum())
        n_after = int(imputed[c].isna().sum())
        if n_before == 0 and n_after == 0:
            continue
        rows.append({
            "column": c, "n_miss_before": n_before, "n_miss_after": n_after,
            "n_filled": n_before - n_after,
            "ndigits_mean_obs": mean_decimal_places(cln[c]),
        })
    if not rows and len(before_miss):
        for c in before_miss.index:
            rows.append({
                "column": c, "n_miss_before": int(before_miss[c]),
                "n_miss_after": int(imputed[c].isna().sum()) if c in imputed.columns else -1,
                "n_filled": int(before_miss[c]) - (
                    int(imputed[c].isna().sum()) if c in imputed.columns else 0),
                "ndigits_mean_obs": mean_decimal_places(cln[c])
                if c in cln.columns and pd.api.types.is_numeric_dtype(cln[c]) else None,
            })
    qc = pd.DataFrame(rows)
    if qc.empty:
        rem = imputed.isna().sum()
        rem = rem[rem > 0]
        qc = pd.DataFrame({
            "column": rem.index.astype(str),
            "n_miss_before": [int(before_miss.get(c, 0)) for c in rem.index],
            "n_miss_after": rem.values.astype(int),
            "n_filled": [int(before_miss.get(c, 0)) - int(rem[c]) for c in rem.index],
            "ndigits_mean_obs": [
                mean_decimal_places(cln[c])
                if c in cln.columns and pd.api.types.is_numeric_dtype(cln[c]) else None
                for c in rem.index],
        })
    else:
        seen = set(qc["column"])
        extra = []
        for c, n in before_miss.items():
            if c in seen:
                continue
            n_after = int(imputed[c].isna().sum()) if c in imputed.columns else -1
            extra.append({
                "column": c, "n_miss_before": int(n), "n_miss_after": n_after,
                "n_filled": int(n) - (n_after if n_after >= 0 else 0),
                "ndigits_mean_obs": mean_decimal_places(cln[c])
                if pd.api.types.is_numeric_dtype(cln[c]) else None,
            })
        if extra:
            qc = pd.concat([qc, pd.DataFrame(extra)], ignore_index=True)
        qc = qc.sort_values(["n_miss_before", "column"], ascending=[False, True]).reset_index(drop=True)

    # 完整性：已观测单元格不得改变
    num_check = [c for c in num_cols if c in imputed.columns]
    n_changed = 0
    for c in num_check:
        mask = cln[c].notna()
        if not mask.any():
            continue
        a = pd.to_numeric(cln.loc[mask, c], errors="coerce")
        b = pd.to_numeric(imputed.loc[mask, c], errors="coerce")
        n_changed += int(((a - b).abs() > 1e-12).sum())
    if verbose:
        print(f"observed cells changed (should be 0): {n_changed}")

    after_total = int(imputed[num_check].isna().sum().sum()) if num_check else 0
    before_total = int(cln[num_check].isna().sum().sum()) if num_check else 0
    if verbose:
        print(f"numeric missing cells: {before_total} → {after_total}")

    with pd.ExcelWriter(src, engine="openpyxl") as w:
        imputed.to_excel(w, sheet_name=cln_sheet, index=False)
        if rad is not None:
            rad.to_excel(w, sheet_name=rad_sheet, index=False)
        qc.to_excel(w, sheet_name=p.impute_qc_sheet, index=False)

    if verbose:
        print(f"written → {src}")
        print(f"impute_qc rows: {len(qc)}")
        if len(qc):
            print(qc.head(10).to_string(index=False))
    return {"path": src, "qc": qc, "n_changed": n_changed,
            "missing_before": before_total, "missing_after": after_total, "backup": backup}


# ═════════════════════════════════════════════════════════════════
# 策略 3：paper_fill（论文感知补缺 + 纯合成）
# ═════════════════════════════════════════════════════════════════
def exact_mean_sd(n: int, mean: float, sd: float, rng: np.random.RandomState,
                  low: Optional[float] = None, high: Optional[float] = None) -> np.ndarray:
    """生成样本均值/SD（ddof=1）精确等于给定值的 n 个样本。"""
    if n <= 0:
        return np.array([])
    if n == 1:
        return np.array([mean], dtype=float)
    x = rng.normal(0, 1, size=n)
    x = (x - x.mean()) / (x.std(ddof=1) if x.std(ddof=1) > 1e-12 else 1.0)
    x = x * sd + mean
    if low is not None or high is not None:
        for _ in range(20):
            if low is not None:
                x = np.maximum(x, low)
            if high is not None:
                x = np.minimum(x, high)
            if x.std(ddof=1) > 1e-12:
                x = (x - x.mean()) / x.std(ddof=1) * sd + mean
            else:
                x = np.full(n, mean)
        if low is not None:
            x = np.maximum(x, low)
        if high is not None:
            x = np.minimum(x, high)
    return x


def target_moments_for_group(profile: SsdProfile, group: str, var: str,
                             real_sub: pd.Series) -> Tuple[float, float, str]:
    """缺失补全的目标矩：优先论文2 → 论文1 → 组内实测 → 兜底先验。"""
    g = group if group in profile.valid_cohorts else None
    if g and var in profile.paper2[g]:
        m, sd = profile.paper2[g][var]
        return float(m), float(sd), "paper2_table1_1"
    if g and var in profile.paper1[g]:
        m, sd = profile.paper1[g][var]
        return float(m), float(sd), "paper1_table1"
    vals = pd.to_numeric(real_sub, errors="coerce").dropna()
    if len(vals) >= 3:
        return float(vals.mean()), float(max(vals.std(ddof=1), 1e-3)), "within_group_real_moments"
    m, sd = profile.fallback(var)
    return m, sd, "fallback_prior"


def phase2_fill(real_df: pd.DataFrame, profile: SsdProfile) -> pd.DataFrame:
    """真实值 + 论文感知合成补缺（原 ``phase2_fill``）。"""
    df = real_df.copy()
    rng = np.random.RandomState(profile.seed)
    if "hamd" not in df.columns:
        df["hamd"] = np.nan

    for var in tuple(profile.fill_cont_vars) + tuple(profile.fill_cat_vars):
        df[f"is_synthetic_{var}"] = 0
    df["fill_method"] = ""

    # 性别：按论文2 各组比例
    for gname in profile.valid_cohorts:
        mask_g = df["cohort_final"] == gname
        miss_sex = mask_g & df["sex"].isna()
        n_miss = int(miss_sex.sum())
        if n_miss > 0:
            male_n = profile.paper2[gname]["male"]
            female_n = profile.paper2[gname]["female"]
            p_male = male_n / (male_n + female_n)
            fills = rng.choice(["male", "female"], size=n_miss, p=[p_male, 1 - p_male])
            df.loc[miss_sex, "sex"] = fills
            df.loc[miss_sex, "is_synthetic_sex"] = 1
            df.loc[miss_sex, "fill_method"] = (
                df.loc[miss_sex, "fill_method"].astype(str) + ";sex:paper2_sex_ratio")

    mask_u = df["cohort_final"] == "unknown"
    miss_sex_u = mask_u & df["sex"].isna()
    if miss_sex_u.any():
        fills = rng.choice(["male", "female"], size=int(miss_sex_u.sum()))
        df.loc[miss_sex_u, "sex"] = fills
        df.loc[miss_sex_u, "is_synthetic_sex"] = 1
        df.loc[miss_sex_u, "fill_method"] = (
            df.loc[miss_sex_u, "fill_method"].astype(str) + ";sex:unknown_50_50")

    for var in profile.fill_cont_vars:
        low, high = profile.bound(var)
        for gname in tuple(profile.valid_cohorts) + ("unknown",):
            mask_g = df["cohort_final"] == gname
            real_vals = pd.to_numeric(df.loc[mask_g, var], errors="coerce")
            miss = mask_g & real_vals.isna()
            n_miss = int(miss.sum())
            if n_miss == 0:
                continue
            if gname == "unknown":
                pool = pd.to_numeric(
                    df.loc[df["cohort_final"].isin(profile.valid_cohorts), var], errors="coerce")
                mean, sd, method = target_moments_for_group(profile, "SSD", var, pool)
                method = "unknown_" + method
            else:
                mean, sd, method = target_moments_for_group(profile, gname, var, real_vals)

            gen = exact_mean_sd(n_miss, mean, sd, rng, low=low, high=high)
            if var in set(profile.int_like_vars) | set(profile.one_decimal_vars):
                gen = np.round(gen, 0 if var in profile.int_like_vars else 1)
                if low is not None:
                    gen = np.maximum(gen, low)
                if high is not None:
                    gen = np.minimum(gen, high)
            else:
                gen = np.round(gen, 2)

            df.loc[miss, var] = gen
            df.loc[miss, f"is_synthetic_{var}"] = 1
            prev = df.loc[miss, "fill_method"].astype(str)
            df.loc[miss, "fill_method"] = prev + f";{var}:{method}"

    df["fill_method"] = (df["fill_method"].astype(str)
                         .str.replace(r"^;+", "", regex=True)
                         .replace({"nan": "", "None": ""}))

    syn_cols = [c for c in df.columns if c.startswith("is_synthetic_")]
    df["any_synthetic"] = ((df[syn_cols].sum(axis=1) > 0).astype(int) if syn_cols else 0)
    core_syn = [f"is_synthetic_{v}" for v in profile.core_fill_vars
                if f"is_synthetic_{v}" in df.columns]
    df["any_synthetic_core"] = ((df[core_syn].sum(axis=1) > 0).astype(int) if core_syn else 0)
    df["real_core_complete"] = (df["any_synthetic_core"] == 0).astype(int)

    p1_ok = pd.Series(True, index=df.index)
    for v in ("age", "sex", "phq15", "sss"):
        sc = f"is_synthetic_{v}"
        if sc in df.columns:
            p1_ok &= df[sc] == 0
        p1_ok &= df[v].notna()
    df["real_paper1_scales"] = p1_ok.astype(int)
    return df


def make_pure_synthetic(profile: SsdProfile, paper: Dict[str, Any], tag: str) -> pd.DataFrame:
    """整队列纯合成（原 ``make_pure_synthetic``）。仅用于方法演示，须标注 SYNTHETIC。"""
    rng = np.random.RandomState(profile.seed + (1 if "paper1" in tag else 2))
    rows = []
    for gname in profile.valid_cohorts:
        info = paper[gname]
        n = info["n"]
        sex = ["male"] * info["male"] + ["female"] * info["female"]
        if len(sex) < n:
            sex += ["female"] * (n - len(sex))
        sex = sex[:n]
        rng.shuffle(sex)

        block: Dict[str, Any] = {
            "subject_id": [f"SYN_{tag}_{gname}_{i + 1:03d}" for i in range(n)],
            "name_en": [f"SYN_{tag}_{gname}_{i + 1:03d}" for i in range(n)],
            "cohort": gname,
            "cohort_final": gname,
            "sex": sex,
            "SYNTHETIC_DEMO": 1,
            "paper_source": paper["name"],
        }
        for var, moments in info.items():
            if var in {"n", "male", "female"}:
                continue
            if isinstance(moments, tuple) and len(moments) == 2:
                m, sd = moments
                low, high = profile.bound(var)
                arr = exact_mean_sd(n, m, sd, rng, low=low, high=high)
                if var in profile.int_like_vars:
                    arr = np.round(arr)
                elif var == "education_years":
                    arr = np.round(arr, 1)
                else:
                    arr = np.round(arr, 2)
                block[var] = arr
        rows.append(pd.DataFrame(block))
    return pd.concat(rows, ignore_index=True)


def paper_fill(profile: SsdProfile, out_dir: Optional[Path] = None,
               real_path: Optional[Path] = None, verbose: bool = True) -> Dict[str, Any]:
    """读真值合并表 → 论文感知补缺 + 纯合成队列（原 Phase 2/3）。"""
    out = Path(out_dir) if out_dir else profile.out
    real_path = Path(real_path) if real_path else out / profile.out_real
    if not real_path.exists():
        raise FileNotFoundError(
            f"{real_path} 不存在；请先跑 `python tidy_merge.py --profile ssd`")
    out.mkdir(parents=True, exist_ok=True)

    real_df = pd.read_excel(real_path, sheet_name=profile.main_sheet_name)
    conflict_df = pd.DataFrame()
    try:
        raw_conf = pd.read_excel(real_path, sheet_name="conflict_log")
        if list(raw_conf.columns) != ["note"]:
            conflict_df = raw_conf
    except (ValueError, KeyError):
        pass
    try:
        meta_df = pd.read_excel(real_path, sheet_name="meta")
    except (ValueError, KeyError):
        meta_df = pd.DataFrame([{"key": "n_rows", "value": len(real_df)}])

    filled = phase2_fill(real_df, profile)
    export_filled = filled.drop(columns=["_name_key"], errors="ignore")

    with pd.ExcelWriter(out / profile.out_filled, engine="openpyxl") as xw:
        export_filled.to_excel(xw, sheet_name=profile.main_sheet_name, index=False)
        if len(conflict_df):
            conflict_df.to_excel(xw, sheet_name="conflict_log", index=False)
        meta_df.to_excel(xw, sheet_name="meta", index=False)
        filled[filled["any_synthetic_core"] == 0].to_excel(
            xw, sheet_name="real_core_subset", index=False)
        filled[filled["real_paper1_scales"] == 1].to_excel(
            xw, sheet_name="real_paper1_scales", index=False)

    summary = []
    for var in tuple(profile.fill_cont_vars) + tuple(profile.fill_cat_vars):
        col = f"is_synthetic_{var}"
        if col in filled.columns:
            summary.append({
                "variable": var,
                "n_synthetic": int(filled[col].sum()),
                "n_real_nonnull": int(((filled[col] == 0) & filled[var].notna()).sum()),
            })
    pd.DataFrame(summary).to_csv(out / profile.out_fill_summary, index=False, encoding="utf-8-sig")

    if verbose:
        print(f"  filled any_synthetic(incl hamd)={int(filled['any_synthetic'].sum())}/{len(filled)} "
              f"any_synthetic_core={int(filled['any_synthetic_core'].sum())}/{len(filled)} "
              f"real_core_subset={int((filled['any_synthetic_core'] == 0).sum())}")

    syn1 = make_pure_synthetic(profile, profile.paper1, "P1")
    syn2 = make_pure_synthetic(profile, profile.paper2, "P2")
    syn1.to_excel(out / profile.out_syn1, index=False)
    syn2.to_excel(out / profile.out_syn2, index=False)
    if verbose:
        print(f"  syn1={syn1.shape} syn2={syn2.shape}")

    return {"out": out, "filled": filled, "syn1": syn1, "syn2": syn2,
            "filled_path": out / profile.out_filled}


# ═════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════
DEFAULT_STRATEGY = {"lxj_AN": "gate", "ssd": "impute"}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="tidy_complete",
        description="统一补全入口：gate(lxj_AN) / impute(ssd 影像) / paper_fill(ssd 临床)",
    )
    ap.add_argument("--profile", required=True, help="lxj_AN | ssd")
    ap.add_argument("--strategy", choices=["auto", "gate", "impute", "paper_fill"], default="auto")
    ap.add_argument("--src", help="gate：输入总表工作簿（默认 <root>/lxj_AN.xlsx）")
    ap.add_argument("--out", help="gate：输出工作簿（默认 <root>/lxj_AN_complete.xlsx）")
    ap.add_argument("--layout", choices=["single", "cln-rad-qc"], default="single",
                    help="gate：单表，或额外按 cln/rad/qc 三表输出")
    ap.add_argument("--file", help="impute：目标工作簿（默认 SSD_DTI_IVIM 根下 jfl_SSD_DTI_IVIM.xlsx）")
    ap.add_argument("--sheet", help="impute：cln 表名（默认 cln）")
    ap.add_argument("--rad-sheet", help="impute：原样保留的表名（默认 rad）")
    ap.add_argument("--group-col", help="impute：分组列（默认 Group）")
    ap.add_argument("--method", help="impute：median | mean | iterative | mice | missForest")
    ap.add_argument("--out-dir", help="paper_fill：输出目录")
    ap.add_argument("--input", help="paper_fill：真值合并表（默认 <out_dir>/clinical_real_merged.xlsx）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    profile = get_profile(args.profile)
    strategy = args.strategy
    if strategy == "auto":
        strategy = DEFAULT_STRATEGY.get(profile.name, "gate")
    verbose = not args.quiet

    if strategy == "gate":
        gate_complete(profile, Path(args.src) if args.src else None,
                      Path(args.out) if args.out else None,
                      layout=args.layout, verbose=verbose)
        return 0

    if strategy == "impute":
        impute_workbook(profile, Path(args.file) if args.file else None,
                        args.sheet, args.rad_sheet, args.group_col, args.method,
                        verbose=verbose)
        return 0

    paper_fill(profile, Path(args.out_dir) if args.out_dir else None,
               Path(args.input) if args.input else None, verbose=verbose)
    print("DONE (paper_fill)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ZeroMissingViolation:
        raise SystemExit(1) from None
