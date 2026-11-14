#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""restore_raw -- 从 re.xlsx 汇总统计表还原受试者级合成原始数据（meta 分析用）。

用法::

    python -B restore_raw.py            # dry-run：只打印可行性预检与计划，不写盘
    python -B restore_raw.py --execute  # 生成并写出 OUT_XLSX

数据契约（城城 2026-11-11 裁定）:
  - 同一批受试者：Full 队列 23/13/20（Sheet2, mFA/mADC/Age/BMI），
    Sheet1 的 19/12/20（GA/SINBAD/mT2/mT1 + Age/BMI 子集矩）是其子集；Control 同为 20 人。
  - 三档匹配：A=mean/SD/n 精确到表内显示精度；B=AUC/sens/spec 命中最近数学可达值；
    C=P 值由矩决定、只报告实测（Welch/pooled/MW/KW 都算）。
  - 个体值按源表显示位数落盘（age/BMI/GA/mT2/mT1 1 位，mFA/mADC 3 位，SINBAD 整数）；
    所有行带 SYNTHETIC=1 标记。
  - 无回退：任何不可行直接抛错或如实记入 Verify sheet，不静默降级。

流水线: stage1 矩构造（解析补集分解 + 显示舍入窗口网格）→ stage2 ROC 形态调参
（scipy.optimize.minimize Powell + 矩不变三元移动）→ stage3 相关配对（组内 swap 退火，
不改边际分布）→ stage4 核验（重算全部统计量，目标 vs 实测）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

# ══════════════════════════════════════════════════════════════════
# 配置区（仅此处可改；逻辑区不写死路径/参数）
# ══════════════════════════════════════════════════════════════════
# 输入 / 交付默认落本机 Downloads（RESTORE_SRC / RESTORE_OUT 可覆盖）；公开仓不写死用户名。
_DOWNLOADS = Path.home() / "Downloads"
SRC_XLSX = Path(os.environ.get("RESTORE_SRC") or _DOWNLOADS / "re.xlsx")          # 输入汇总表（只读）
OUT_XLSX = Path(os.environ.get("RESTORE_OUT") or _DOWNLOADS / "re_restored_raw_V4.xlsx")  # 交付（V4=表内位数落盘）
SEED = 20261111          # 固定种子，全程可复现
TOL = 0.05               # mean/SD 显示舍入窗口（表内 1 位小数 → ±半个显示单位）
GRID_STEP = 0.01         # 窗口网格步长
EPS_MOM = 1e-6           # 矩精确判定容差
MICRO_REPAIR_ITERS = 40_000   # 模拟精度舍入后计数微修的预算（只动临界值）

# 源表报告位数（由字面量定），(mean_dp, sd_dp)：
#   mean_dp = 均值位数 → 个体值落盘位数（例 age 71.2 → 1 位）
#   sd_dp   = SD 位数   → 模拟/比对精度（例 age 8.4 → 1 位）
# 两表里二者逐变量相同；若不同，个体值落在较粗的那个网格上。
REPORT_DP = {"age": (1, 1), "BMI": (1, 1), "GA": (1, 1), "SINBAD": (1, 1),
             "mT2": (1, 1), "mT1": (1, 1), "mFA": (3, 3), "mADC": (3, 3)}
MEAN_DP = {v: dp[0] for v, dp in REPORT_DP.items()}
SD_DP = {v: dp[1] for v, dp in REPORT_DP.items()}

GROUPS = ("Positive", "Negative", "Control")

# 目标矩 MOM[var][cohort][group] = (n, mean, sd)；cohort S=子集 / F=全队列
MOM = {
    "age": {"S": {"Positive": (19, 71.2, 8.4), "Negative": (12, 63.1, 12.9),
                  "Control": (20, 66.4, 6.3)},
            "F": {"Positive": (23, 70.1, 10.1), "Negative": (13, 63.6, 11.5),
                  "Control": (20, 66.4, 6.3)}},
    "BMI": {"S": {"Positive": (19, 22.4, 1.5), "Negative": (12, 23.4, 1.4),
                  "Control": (20, 22.3, 2.1)},
            "F": {"Positive": (23, 22.1, 1.5), "Negative": (13, 23.1, 1.8),
                  "Control": (20, 22.3, 2.1)}},
    "GA": {"S": {"Positive": (19, 22.8, 2.6), "Negative": (12, 23.8, 6.5),
                 "Control": (20, 13.9, 1.4)}},
    "SINBAD": {"S": {"Positive": (19, 4.4, 1.5), "Negative": (12, 0.4, 0.7)}},
    "mT2": {"S": {"Positive": (19, 90.4, 24.7), "Negative": (12, 51.2, 10.1),
                  "Control": (20, 36.9, 4.8)}},
    "mT1": {"S": {"Positive": (19, 2125.0, 231.9), "Negative": (12, 1794.1, 345.5),
                  "Control": (20, 1539.2, 241.8)}},
    "mFA": {"F": {"Positive": (23, 0.418, 0.029), "Negative": (13, 0.490, 0.051),
                  "Control": (20, 0.651, 0.096)}},
    "mADC": {"F": {"Positive": (23, 2.022, 0.170), "Negative": (13, 1.696, 0.281),
                   "Control": (20, 1.240, 0.300)}},
}

# P 值目标（C 档：只报告实测；"<x" 为阈值型，须命中同一区间）
# P1=Pos vs Neg, P2=Pos vs Ctrl, P3=Neg vs Ctrl（已用 age/mT1 行数值反推验证此顺序）
PV = {
    ("S", "age"): {"overall": 0.06, "P1": 0.055, "P2": 0.055, "P3": 0.35},
    ("S", "BMI"): {"overall": 0.248, "P1": 0.102, "P2": 0.801, "P3": 0.144},
    ("S", "GA"): {"overall": "<0.001", "P1": 0.552, "P2": "<0.001", "P3": "<0.001"},
    ("S", "SINBAD"): {"P1": "<0.001"},  # Control="/" → overall/P2/P3 不可拟合（模板残留）
    ("S", "mT2"): {"overall": "<0.001", "P1": "<0.001", "P2": "<0.001", "P3": "<0.01"},
    ("S", "mT1"): {"overall": "<0.001", "P1": "<0.01", "P2": "<0.001", "P3": "<0.05"},
    ("F", "age"): {"overall": 0.153, "P1": 0.111, "P2": 0.174, "P3": 0.392},
    ("F", "BMI"): {"overall": 0.375, "P1": 0.128, "P2": 0.754, "P3": 0.323},
    ("F", "mFA"): {"overall": "<0.0001", "P1": "<0.01", "P2": "<0.0001", "P3": "<0.0001"},
    ("F", "mADC"): {"overall": "<0.0001", "P1": "<0.01", "P2": "<0.0001", "P3": "<0.0001"},
}

# ROC 目标 (var, cohort, gA, gB, auc, cutoff, sens, spec, direction)
# direction: higher_pos=值越高越倾向 gA(Positive)；lower_pos 相反（mFA）
ROC = [
    ("mT2", "S", "Positive", "Negative", 0.948, 62.0, 0.889, 0.90, "higher_pos"),
    ("mT2", "S", "Negative", "Control", 0.910, 43.6, 0.80, 0.95, "higher_pos"),
    ("mT1", "S", "Positive", "Negative", 0.789, 2064.0, 0.667, 0.80, "higher_pos"),
    ("mT1", "S", "Negative", "Control", 0.735, 1596.7, 0.80, 0.65, "higher_pos"),
    ("mFA", "F", "Positive", "Negative", 0.882, 0.451, 0.909, 0.818, "lower_pos"),
    ("mFA", "F", "Negative", "Control", 0.927, 0.566, 0.909, 0.85, "lower_pos"),
    ("mADC", "F", "Positive", "Negative", 0.860, 1.863, 0.864, 0.818, "higher_pos"),
    ("mADC", "F", "Negative", "Control", 0.873, 1.473, 0.727, 0.70, "higher_pos"),
]

# 相关目标 (x, y, cohort, group_scope, r_target, p_reported)
# cohort "S"=子集 51 人；"OV"=重叠（mFA/mADC 在 F、GA/SINBAD 在 S → 交集=S；Pos 内=19）
CORR = [
    ("mT2", "GA", "S", "all", 0.592, "<0.0001"),
    ("mT1", "GA", "S", "all", 0.415, 0.003),
    ("mT2", "SINBAD", "S", "Positive", 0.866, "<0.0001"),
    ("mT1", "SINBAD", "S", "Positive", -0.281, 0.259),
    ("mFA", "GA", "OV", "all", -0.627, "<0.0001"),
    ("mADC", "GA", "OV", "all", 0.622, "<0.0001"),
    ("mFA", "SINBAD", "OV", "Positive", 0.003, 0.990),
    ("mADC", "SINBAD", "OV", "Positive", -0.117, 0.605),
]

# RawData 列定义与落盘显示位（显示位与源表一致：1 位→"0.0"，3 位→"0.000"，整数评分→"0"）
RAW_COLS = (("age", "age_y"), ("BMI", "BMI_kg_m2"), ("GA", "GA_pct"),
            ("SINBAD", "SINBAD"), ("mT2", "mT2_ms"), ("mT1", "mT1_ms"),
            ("mFA", "mFA"), ("mADC", "mADC_e3_mm2_s"))
RAW_FMT = {"age_y": "0.0", "BMI_kg_m2": "0.0", "GA_pct": "0.0", "SINBAD": "0",
           "mT2_ms": "0.0", "mT1_ms": "0.0", "mFA": "0.000", "mADC_e3_mm2_s": "0.000"}
if set(RAW_FMT) != {col for _v, col in RAW_COLS}:
    raise ValueError("RAW_FMT 与 RAW_COLS 不匹配（显示位映射必须覆盖全部变量列）")

# 生理边界（生成硬约束；apply_bounds 内迭代重标准化，不收敛则抛错）
BOUNDS = {"age": (30.0, 95.0), "BMI": (14.0, 40.0), "GA": (5.0, 45.0),
          "SINBAD": (0.0, 6.0), "mT2": (5.0, 400.0), "mT1": (500.0, 4000.0),
          "mFA": (0.05, 0.95), "mADC": (0.3, 3.5)}
INT_VARS = ("SINBAD",)
# 模拟精度 SIM_DP：带 ROC/AUC 目标的变量取 4 位（表内 AUC/CI 报 3 位 → 需更细网格命中
# k/(nA·nB)）；其余取 SD 的报告位数（例 age 8.4 → 1 位）。
ROC_VARS = tuple(dict.fromkeys(t[0] for t in ROC))
SIM_DP = {v: (4 if v in ROC_VARS else SD_DP[v]) for v in REPORT_DP}
# 落盘精度 LAND_DP：取均值的报告位数（例 mT2 90.4 → 1 位；mFA 0.418 → 3 位；SINBAD 整数）。
LAND_DP = {v: (0 if v in INT_VARS else MEAN_DP[v]) for v in REPORT_DP}

# stage2/3 预算（CPU 即可：n<=56，单次 loss 评估 ~3k flops；GPU 零收益反增依赖）
STAGE2_RESTARTS = 40
STAGE2_MAXITER = 4000
REPAIR_ITERS = 30_000
STAGE3_ITERS = 250_000
STAGE3_POLISH_ITERS = 80_000
STAGE3_POLISH_STALE = 15_000
R_TOL = 0.0015           # 相关命中容差（≈3 位小数最近值）

# re.xlsx 转录锚点：执行前逐一断言存在于源表，防手工转录错
ANCHOR_STRINGS = ("90.4(24.7)", "2125.0(231.9)", "0.418(0.029)", "2.022(0.170)",
                  "0.651(0.096)", "0.948", "43.6", "2064", "1596.7", "0.451",
                  "0.566", "1.863", "1.473", "0.866", "0.415", "0.622", "0.592")
ANCHOR_NUMBERS = (0.055, 0.248, 0.102, 0.801, 0.144, 0.153, 0.111, 0.174,
                  0.392, 0.375, 0.128, 0.754, 0.323, 0.552, 0.015, 0.06)

NS = {"Positive": 19, "Negative": 12, "Control": 20}   # Sheet1 子集队列
NF = {"Positive": 23, "Negative": 13, "Control": 20}   # Sheet2 全队列


# ══════════════════════════════════════════════════════════════════
# 基础函数
# ══════════════════════════════════════════════════════════════════
def check_anchors(path: Path) -> None:
    """断言 re.xlsx 内含全部转录锚点；缺失即抛错（防手工转录错，无回退）。"""
    if not path.exists():
        raise FileNotFoundError(f"输入不存在: {path}")
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    strs, nums = [], []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for c in row:
                if c is None:
                    continue
                if isinstance(c, (int, float)):
                    nums.append(float(c))
                else:
                    strs.append(str(c))
    blob = "\n".join(strs)
    num_blob = "\n".join(str(v) for v in nums)
    missing = [a for a in ANCHOR_STRINGS
               if a not in blob and a not in num_blob]
    missing += [a for a in ANCHOR_NUMBERS
                if not any(abs(v - a) < 1e-9 for v in nums)]
    if missing:
        raise ValueError(f"锚点缺失（转录可能有误）: {missing}")


def std_base(rng: np.random.RandomState, n: int) -> np.ndarray:
    """n 个标准正态抽样，精确标准化到 mean=0, sd(ddof=1)=1。"""
    z = rng.normal(size=n)
    return (z - z.mean()) / z.std(ddof=1)


def shape_std(z: np.ndarray, a: float, b: float) -> np.ndarray:
    """形态变换（偏度 a、尾权 b）后再精确标准化；变换退化和→0 时抛错。"""
    y = z + a * (z ** 2 - 1.0) + b * (z ** 3 - 3.0 * z) / 3.0
    s = y.std(ddof=1)
    if not np.isfinite(s) or s < 1e-9:
        raise RuntimeError(f"形态变换退化: a={a}, b={b}")
    return (y - y.mean()) / s


def apply_bounds(y: np.ndarray, m: float, sd: float,
                 low: float, high: float) -> np.ndarray:
    """缩放到 (m, sd) 并夹到 [low, high]；夹后重标准化迭代，不收敛抛错。"""
    x = y * sd + m
    for _ in range(60):
        xc = np.clip(x, low, high)
        if np.allclose(xc, x, atol=1e-12):
            return xc
        s = xc.std(ddof=1)
        if s < 1e-12:
            raise RuntimeError(f"边界塌缩: m={m}, sd={sd}, low={low}, high={high}")
        x = (xc - xc.mean()) / s * sd + m
    raise RuntimeError(f"边界+矩不收敛: m={m}, sd={sd}, low={low}, high={high}")


def make_vec(rng: np.random.RandomState, n: int, m: float, sd: float,
             var: str, a: float = 0.0, b: float = 0.0) -> np.ndarray:
    """生成 mean/SD(ddof=1) 精确=(m, sd) 的 n 个值（同 tidy_complete.exact_mean_sd 口径，
    另加形态参数 a/b）。整数变量走 build_sinbad。"""
    low, high = BOUNDS[var]
    return apply_bounds(shape_std(std_base(rng, n), a, b), m, sd, low, high)


def moments_ok(x: np.ndarray, m: float, sd: float, tol: float = EPS_MOM) -> bool:
    """均值/SD 与目标差 <= tol（默认 1e-6，即显示精度下精确）。"""
    return abs(x.mean() - m) <= tol and abs(x.std(ddof=1) - sd) <= tol


def build_sinbad(rng: np.random.RandomState, n: int, m_t: float, sd_t: int | float,
                 m_dp: int = 1, sd_dp: int = 1) -> np.ndarray:
    """SINBAD 整数多重集：穷举计数向量 (c0..c6)（带剪枝），显示位 mean/SD 命中目标。

    枚举 Σc=n 的全部整数解；叶节点实测 round(mean)==round(m_t) 且
    round(sd)==round(sd_t)。解集非空时按 rng 确定性地取一解并洗牌返回；
    解集为空抛 RuntimeError（目标在整数域不可行，无回退）。
    """
    lo, hi = int(BOUNDS["SINBAD"][0]), int(BOUNDS["SINBAD"][1])
    m_round = round(float(m_t), m_dp)
    sd_round = round(float(sd_t), sd_dp)
    unit = 10.0 ** (-m_dp)
    sum_lo = int(math.ceil((m_round - unit / 2) * n - 1e-9))
    sum_hi = int(math.floor((m_round + unit / 2) * n + 1e-9))
    sols: list = []
    counts = [0] * (hi + 1)

    def rec(v: int, left: int, ssum: int, ssq: int) -> None:
        if v > hi:
            if left == 0 and sum_lo <= ssum <= sum_hi:
                m = ssum / n
                var = (ssq - n * m * m) / (n - 1)
                if var >= 0 and round(m, m_dp) == m_round and \
                        round(math.sqrt(var), sd_dp) == sd_round:
                    sols.append(tuple(counts))
            return
        for c in range(left + 1):
            s2 = ssum + v * c
            q2 = ssq + v * v * c
            rest = left - c
            nxt = v + 1 if v < hi else v
            if s2 + hi * rest < sum_lo:       # 最大可能 sum 都不够；c 越大越小 → 剪枝
                break
            if s2 + nxt * rest > sum_hi:      # 最小可能 sum 超限；c 越大越小 → 继续
                continue
            counts[v] = c
            rec(v + 1, rest, s2, q2)
            counts[v] = 0

    rec(lo, n, 0, 0)
    if not sols:
        raise RuntimeError(f"SINBAD 整数解不存在: n={n}, mean={m_t}, sd={sd_t}")
    pick = sols[int(rng.randint(len(sols)))]
    out = np.repeat(np.arange(lo, hi + 1, dtype=float),
                    np.array(pick[lo:hi + 1], dtype=int))
    if len(out) != n:
        raise RuntimeError(f"SINBAD 计数向量长度异常: {pick}")
    rng.shuffle(out)
    return out




# ══════════════════════════════════════════════════════════════════
# stage1 矩构造：子集⊂全集解析分解 + 显示舍入窗口网格
# ══════════════════════════════════════════════════════════════════
def complement_moments(nS: int, mS: float, sdS: float,
                       nF: int, mF: float, sdF: float) -> dict:
    """由平方和恒等式解析求补集矩：SS_F = SS_S + SS_C。

    nC==1 时补集为单值 x=mC；nC>=2 时给 (mC, sdC|None)（varC<=0 → sdC=None）。
    """
    nC = nF - nS
    if nC <= 0:
        raise ValueError(f"nC={nC} 非法（nF 必须 > nS）")
    sumC = nF * mF - nS * mS
    ssF = (nF - 1) * sdF ** 2 + nF * mF ** 2
    ssS = (nS - 1) * sdS ** 2 + nS * mS ** 2
    ssC = ssF - ssS
    mC = sumC / nC
    out = {"nC": nC, "mC": mC, "ssC": ssC, "sdC": None}
    if nC >= 2:
        varC = (ssC - nC * mC ** 2) / (nC - 1)
        if varC > 0:
            out["sdC"] = math.sqrt(varC)
    return out


def solve_subset_superset(var: str, group: str) -> dict:
    """在显示舍入窗口(±TOL)内网格搜可行的 (子集矩, 全集矩, 补集矩)。

    - nC==0（Control）：S=F 同批人，两表目标须一致，直接精确。
    - nC==1（Negative）：sdF 视为派生量 sdF_implied（由 SS 恒等式反解），
      搜 (dmS, dsS, dmF) 使 sdF_implied 落窗口且 x 落边界。
    - nC>=2（Positive）：四参全自由，需 varC>0 且补集矩生理可行。
    - 窗口内无解 → "relaxed" 模式：S 与 mF 精确，sdF 取恒等式隐含值并打
      INCOMPATIBLE 标记（如实上报，不静默）。完全无解才抛 RuntimeError。
    返回 {"mS","sdS","mF","sdF","comp","relaxed":str|None}。
    """
    nS, mS_t, sdS_t = MOM[var]["S"][group]
    if "F" not in MOM[var]:               # 仅 S 队列的变量：精确显示值
        return {"mS": mS_t, "sdS": sdS_t, "mF": None, "sdF": None,
                "comp": None, "relaxed": None}
    nF, mF_t, sdF_t = MOM[var]["F"][group]
    if nF == nS:                          # Control：两表目标必须一致
        if (mS_t, sdS_t) != (mF_t, sdF_t):
            raise ValueError(f"{var}/{group}: S=F 但两表目标不一致 "
                             f"{(mS_t, sdS_t)} vs {(mF_t, sdF_t)}")
        return {"mS": mS_t, "sdS": sdS_t, "mF": mF_t, "sdF": sdF_t,
                "comp": None, "relaxed": None}
    low, high = BOUNDS[var]
    offs = np.round(np.arange(-TOL, TOL + 1e-9, GRID_STEP), 4)
    best = None
    nC = nF - nS
    for dmS in offs:
        mS = mS_t + dmS
        for dsS in offs:
            sdS = sdS_t + dsS
            if sdS <= 0:
                continue
            for dmF in offs:
                mF = mF_t + dmF
                if nC == 1:
                    x = nF * mF - nS * mS
                    if not (low <= x <= high):
                        continue
                    ssS = (nS - 1) * sdS ** 2 + nS * mS ** 2
                    varF = (ssS + x * x - nF * mF ** 2) / (nF - 1)
                    if varF <= 0:
                        continue
                    sdF = math.sqrt(varF)          # 派生量
                    if abs(sdF - sdF_t) > TOL:
                        continue
                    comp = {"nC": 1, "mC": x, "sdC": None}
                    cost = dmS ** 2 + dsS ** 2 + dmF ** 2 + (sdF - sdF_t) ** 2
                else:
                    for dsF in offs:
                        sdF = sdF_t + dsF
                        if sdF <= 0:
                            continue
                        comp = complement_moments(nS, mS, sdS, nF, mF, sdF)
                        if comp["sdC"] is None or not (low < comp["mC"] < high):
                            continue
                        pen = 0.0
                        if comp["mC"] - 1.5 * comp["sdC"] < low - TOL or \
                                comp["mC"] + 1.5 * comp["sdC"] > high + TOL:
                            pen = 0.05
                        if comp["sdC"] > 4 * sdF_t:
                            pen += 0.02
                        cost = (dmS ** 2 + dsS ** 2 + dmF ** 2 + dsF ** 2 + pen)
                        if best is None or cost < best[0]:
                            best = (cost, mS, sdS, mF, sdF, comp, None)
                    continue
                if best is None or cost < best[0]:
                    best = (cost, mS, sdS, mF, sdF, comp, None)
    if best is None:
        # relaxed：窗口内数学不可行（跨表不相容）。S 与 mF 保持精确，
        # sdF 取恒等式隐含值 → Verify sheet 打 INCOMPATIBLE，响亮上报。
        x = nF * mF_t - nS * mS_t
        if not (low <= x <= high):
            raise RuntimeError(f"{var}/{group}: 窗口与 relaxed 模式均无可行解")
        ssS = (nS - 1) * sdS_t ** 2 + nS * mS_t ** 2
        varF = (ssS + x * x - nF * mF_t ** 2) / (nF - 1)
        if varF <= 0:
            raise RuntimeError(f"{var}/{group}: relaxed 模式 varF<=0，无解")
        sdF_imp = math.sqrt(varF)
        best = (0.0, mS_t, sdS_t, mF_t, sdF_imp,
                {"nC": 1, "mC": x, "sdC": None},
                f"INCOMPATIBLE: sdF 目标 {sdF_t} vs 隐含 {sdF_imp:.4f}"
                f"（Sheet1/Sheet2 该行跨表不相容，超出显示舍入）")
    _, mS, sdS, mF, sdF, comp, relaxed = best
    return {"mS": mS, "sdS": sdS, "mF": mF, "sdF": sdF,
            "comp": comp, "relaxed": relaxed}



# ══════════════════════════════════════════════════════════════════
# master 数据容器与视图
# ══════════════════════════════════════════════════════════════════
ALL_VARS = ("age", "BMI", "GA", "SINBAD", "mT2", "mT1", "mFA", "mADC")


def stage1_build(rng: np.random.RandomState) -> dict:
    """stage1：解析每 (var,group) 的矩方案，抽基础 z 向量；返回状态容器。"""
    st: dict = {"sol": {}, "bases": {}, "moms": {}, "shapes": {},
                "sinbad": {}, "relaxed": []}
    for g in GROUPS:
        for var in MOM:
            in_s = var != "SINBAD" or g != "Control"
            hasS = in_s and "S" in MOM[var] and g in MOM[var]["S"]
            hasF = "F" in MOM[var] and g in MOM[var]["F"]
            if not (hasS or hasF):
                continue
            if var in INT_VARS:
                n, m_t, sd_t = MOM[var]["S"][g]
                st["sinbad"][(var, g)] = build_sinbad(rng, n, m_t, sd_t)
                continue
            if hasS and hasF:
                s = solve_subset_superset(var, g)
                st["sol"][(var, g)] = s
                if s["relaxed"]:
                    st["relaxed"].append((var, g, s["relaxed"]))
                nS = MOM[var]["S"][g][0]
                st["bases"][(var, g, "S")] = draw_base(
                    rng, var, nS, s["mS"], s["sdS"])
                st["moms"][(var, g, "S")] = (s["mS"], s["sdS"])
                if s["comp"] is not None and s["comp"]["nC"] >= 2:
                    st["bases"][(var, g, "C")] = draw_base(
                        rng, var, s["comp"]["nC"], s["comp"]["mC"], s["comp"]["sdC"])
                    st["moms"][(var, g, "C")] = (s["comp"]["mC"], s["comp"]["sdC"])
                st["moms"][(var, g, "F")] = (s["mF"], s["sdF"])
            elif hasS:
                n, m_t, sd_t = MOM[var]["S"][g]
                st["bases"][(var, g, "S")] = draw_base(rng, var, n, m_t, sd_t)
                st["moms"][(var, g, "S")] = (m_t, sd_t)
            else:
                n, m_t, sd_t = MOM[var]["F"][g]
                st["bases"][(var, g, "F")] = draw_base(rng, var, n, m_t, sd_t)
                st["moms"][(var, g, "F")] = (m_t, sd_t)
            st["shapes"][(var, g)] = (0.0, 0.0)
    return st


def render(st: dict, var: str, g: str, cohort: str) -> np.ndarray:
    """按当前形态参数渲染 (var,g,cohort) 值向量；mean/SD 精确=方案矩。"""
    a, b = st["shapes"][(var, g)]
    z = st["bases"][(var, g, cohort)]
    m, sd = st["moms"][(var, g, cohort)]
    return apply_bounds(shape_std(z, a, b), m, sd, *BOUNDS[var])


def render_safe(st: dict, var: str, g: str, cohort: str):
    """优化器内用：渲染失败返回 None（调用方给罚值）；最终应用仍走 render 抛错。"""
    try:
        return render(st, var, g, cohort)
    except RuntimeError:
        return None



def assemble_master(st: dict) -> dict:
    """拼装 master[(var, group)] → 长度 NF[g] 的数组（S-only 变量非子集位 NaN）。"""
    master: dict = {}
    for g in GROUPS:
        nF, nS = NF[g], NS[g]
        for var in ALL_VARS:
            arr = np.full(nF, np.nan)
            if var in INT_VARS:
                if (var, g) in st["sinbad"]:
                    arr[:nS] = st["sinbad"][(var, g)]
                master[(var, g)] = arr
                continue
            hasS = (var, g, "S") in st["moms"]
            hasF = (var, g, "F") in st["moms"]
            if var in ("age", "BMI"):                 # S + 补集 → 全长
                svals = render(st, var, g, "S")
                s = st["sol"][(var, g)]
                if s["comp"] is None:
                    full = svals
                elif s["comp"]["nC"] == 1:
                    full = np.concatenate([svals, [s["comp"]["mC"]]])
                else:
                    full = np.concatenate([svals, render(st, var, g, "C")])
                arr[:] = full
            elif hasS:                                 # GA/mT2/mT1：仅子集位
                arr[:nS] = render(st, var, g, "S")
            elif hasF:                                 # mFA/mADC：全长
                arr[:] = render(st, var, g, "F")
            else:
                raise RuntimeError(f"{var}/{g}: 无矩方案（配置错误）")
            master[(var, g)] = arr
    return master


def refresh_master(st: dict, master: dict, var: str, g: str) -> None:
    """stage2 形态参数变化后重渲染该 (var,g) 进 master。"""
    if var in ("mT2", "mT1"):
        master[(var, g)][:NS[g]] = render(st, var, g, "S")
    elif var in ("mFA", "mADC"):
        master[(var, g)][:] = render(st, var, g, "F")
    else:
        raise ValueError(f"stage2 不应改动 {var}")


def roc_view(master: dict, t: dict) -> tuple:
    """ROC 行的两组值视图：cohort S → 子集位；F → 全长。含 NaN 抛错。"""
    if t["coh"] == "S":
        va = master[(t["var"], t["gA"])][:NS[t["gA"]]]
        vb = master[(t["var"], t["gB"])][:NS[t["gB"]]]
    else:
        va = master[(t["var"], t["gA"])]
        vb = master[(t["var"], t["gB"])]
    if np.isnan(va).any() or np.isnan(vb).any():
        raise RuntimeError(f"ROC 视图含 NaN: {t['var']} {t['gA']}/{t['gB']}")
    return va, vb


def auc_mw(va: np.ndarray, vb: np.ndarray, direction: str) -> float:
    """Mann-Whitney AUC（0.5 平局修正）；direction 决定阳性方向。"""
    sgn = 1.0 if direction == "higher_pos" else -1.0
    d = sgn * (va[:, None] - vb[None, :])
    return (float(np.sum(d > 0)) + 0.5 * float(np.sum(d == 0))) / (len(va) * len(vb))


def roc_counts(va: np.ndarray, vb: np.ndarray, cut: float, direction: str) -> tuple:
    """(sens 计数, spec 计数)：higher_pos → sens=#(va>=cut), spec=#(vb<cut)。"""
    if direction == "higher_pos":
        return int(np.sum(va >= cut)), int(np.sum(vb < cut))
    return int(np.sum(va <= cut)), int(np.sum(vb > cut))


def roc_targets_discrete() -> list:
    """把 ROC 目标换算为最近数学可达值：AUC→k/(nA·nB)，sens/spec→k/n。"""
    out = []
    for (var, coh, gA, gB, auc_t, cut, sens_t, spec_t, direction) in ROC:
        nA = (NS if coh == "S" else NF)[gA]
        nB = (NS if coh == "S" else NF)[gB]
        k = int(round(auc_t * nA * nB))
        cnt_a = int(round(sens_t * nA))
        cnt_b = int(round(spec_t * nB))
        out.append({"var": var, "coh": coh, "gA": gA, "gB": gB,
                    "direction": direction, "cut": cut, "nA": nA, "nB": nB,
                    "auc_t": auc_t, "auc_d": k / (nA * nB), "auc_k": k,
                    "sens_t": sens_t, "cnt_a_t": cnt_a, "sens_d": cnt_a / nA,
                    "spec_t": spec_t, "cnt_b_t": cnt_b, "spec_d": cnt_b / nB})
    return out


# ══════════════════════════════════════════════════════════════════
# stage2 ROC 形态调参（scipy.optimize.minimize Powell，矩保持精确）
# ══════════════════════════════════════════════════════════════════
SHAPE_KEYS = [(v, g) for (v, _c) in (("mT2", "S"), ("mT1", "S"),
                                      ("mFA", "F"), ("mADC", "F"))
              for g in GROUPS]


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def _theta_apply(st: dict, theta: np.ndarray) -> None:
    for i, key in enumerate(SHAPE_KEYS):
        st["shapes"][key] = (float(theta[2 * i]), float(theta[2 * i + 1]))


def _theta_sync_master(st: dict, master: dict, theta: np.ndarray) -> bool:
    """应用 theta 并重渲染；任一渲染失败返回 False（优化器罚值路径）。"""
    _theta_apply(st, theta)
    for (var, g) in SHAPE_KEYS:
        try:
            refresh_master(st, master, var, g)
        except RuntimeError:
            return False
    return True


def stage2_soft_loss(theta: np.ndarray, st: dict, master: dict, RT: list,
                     tau_f: float) -> float:
    """软损失：logistic 平滑 AUC + 截断值计数，逼近离散可达目标。

    参数顺序按 scipy.optimize.minimize(fun, x0, args=...) 约定：x 在前。
    """
    if not _theta_sync_master(st, master, theta):
        return 1e6
    loss = 0.0
    for t in RT:
        va, vb = roc_view(master, t)
        sgn = 1.0 if t["direction"] == "higher_pos" else -1.0
        sd_pool = math.sqrt((float(va.var()) + float(vb.var())) / 2.0) or 1.0
        tau = max(tau_f * sd_pool, 1e-9)
        d = sgn * (va[:, None] - vb[None, :]) / tau
        auc_s = float(_expit(d).mean())
        loss += 10.0 * (auc_s - t["auc_d"]) ** 2
        sa = float(_expit(sgn * (va - t["cut"]) / tau).sum())
        sb = float(_expit(-sgn * (vb - t["cut"]) / tau).sum())
        loss += (sa - t["cnt_a_t"]) ** 2 / t["nA"]
        loss += (sb - t["cnt_b_t"]) ** 2 / t["nB"]
    return loss


def roc_discrete(master: dict, RT: list) -> tuple:
    """离散 ROC 损失与逐行明细：AUC 偏差按 U 步数计，计数偏差按个数计。"""
    total = 0.0
    rows = []
    for t in RT:
        va, vb = roc_view(master, t)
        auc = auc_mw(va, vb, t["direction"])
        ca, cb = roc_counts(va, vb, t["cut"], t["direction"])
        eA = abs(round(auc * t["nA"] * t["nB"]) - t["auc_k"])
        eS = abs(ca - t["cnt_a_t"])
        eP = abs(cb - t["cnt_b_t"])
        total += 4.0 * eA ** 2 + eS ** 2 + eP ** 2
        rows.append({**t, "auc": auc, "ca": ca, "cb": cb,
                     "eA": eA, "eS": eS, "eP": eP})
    return total, rows


def stage2_run(st: dict, master: dict, RT: list,
               rng: np.random.RandomState) -> float:
    """退火 Powell + 随机重启，最小化软损失；返回最终离散损失。"""
    n_par = 2 * len(SHAPE_KEYS)
    theta = np.zeros(n_par)
    for tau_f in (0.5, 0.2, 0.08):
        res = optimize.minimize(stage2_soft_loss, theta, args=(st, master, RT, tau_f),
                                method="Powell",
                                options={"maxiter": STAGE2_MAXITER,
                                         "xtol": 1e-4, "ftol": 1e-7})
        theta = res.x
    disc, _ = roc_discrete(master, RT)
    best_theta, best_disc = theta.copy(), disc
    for _ in range(STAGE2_RESTARTS):
        if best_disc == 0:
            break
        cand = best_theta + rng.normal(0.0, 0.4, size=n_par)
        for tau_f in (0.2, 0.08):
            res = optimize.minimize(stage2_soft_loss, cand,
                                    args=(st, master, RT, tau_f), method="Powell",
                                    options={"maxiter": STAGE2_MAXITER,
                                             "xtol": 1e-4, "ftol": 1e-7})
            cand = res.x
        if not _theta_sync_master(st, master, cand):
            continue
        d, _ = roc_discrete(master, RT)
        if d < best_disc:
            best_theta, best_disc = cand.copy(), d
    if not _theta_sync_master(st, master, best_theta):
        raise RuntimeError("stage2 最优 theta 渲染失败（边界不收敛）")
    return best_disc


# ══════════════════════════════════════════════════════════════════
# repair：矩不变三元移动（sum 与 sumsq 双守恒 → mean/SD 精确不动）
# ══════════════════════════════════════════════════════════════════
def ternary_move(x: np.ndarray, idx: np.ndarray,
                 rng: np.random.RandomState, scale: float):
    """在 idx 位置上做一次 (δ,ε,ζ) 三元移动；返回新数组或 None（无实根/越界）。

    守恒推导：ζ=-δ-ε；ε 满足 ε²+ε(x_j-x_k+δ)+δ(x_i-x_k+δ)=0。
    """
    i, j, k = rng.choice(idx, size=3, replace=False)
    xi, xj, xk = float(x[i]), float(x[j]), float(x[k])
    delta = float(rng.uniform(-scale, scale))
    B = xj - xk + delta
    C = delta * (xi - xk + delta)
    D = B * B - 4.0 * C
    if D < 0:
        return None
    eps = (-B + math.sqrt(D)) / 2.0 if rng.rand() < 0.5 else (-B - math.sqrt(D)) / 2.0
    zeta = -delta - eps
    y = x.copy()
    y[i] += delta
    y[j] += eps
    y[k] += zeta
    return y


def repair_run(master: dict, RT: list, rng: np.random.RandomState) -> float:
    """离散退火爬山：三元移动微调 ROC 计数/AUC；mean/SD 全程精确不变。

    前 2/3 预算带 Metropolis 温度（逃逸局部最优），后 1/3 纯爬山；
    全程跟踪最优快照，结束时恢复最优（不静默接受劣化解）。
    舍入翻转由 micro_repair 在其后处理（见该函数）。
    """
    cur, _ = roc_discrete(master, RT)
    if cur == 0:
        return 0.0
    keys = sorted({(t["var"], t["gA"]) for t in RT} |
                  {(t["var"], t["gB"]) for t in RT})
    best = cur
    best_master = {k: v.copy() for k, v in master.items()}
    anneal_until = REPAIR_ITERS * 2 // 3
    for it in range(REPAIR_ITERS):
        if best == 0:
            break
        var, g = keys[int(rng.randint(len(keys)))]
        arr = master[(var, g)]
        idx = np.flatnonzero(~np.isnan(arr))
        if len(idx) < 3:
            continue
        scale = 0.08 * float(np.nanstd(arr, ddof=1))
        y = ternary_move(arr, idx, rng, scale)
        if y is None:
            continue
        low, high = BOUNDS[var]
        sub = y[idx]
        if sub.min() < low or sub.max() > high:
            continue
        # 守恒自检（浮点容差内），破坏即拒绝——不静默接受漂移
        if abs(sub.sum() - arr[idx].sum()) > 1e-6 * max(1.0, abs(arr[idx].sum())):
            continue
        ss_new = float((sub ** 2).sum())
        ss_old = float((arr[idx] ** 2).sum())
        if abs(ss_new - ss_old) > 1e-6 * max(1.0, ss_old):
            continue
        old = arr.copy()
        master[(var, g)] = y
        new, _ = roc_discrete(master, RT)
        T = 2.0 * (1.0 - it / anneal_until) if it < anneal_until else 0.0
        if new <= cur or (T > 0.0 and rng.rand() < math.exp(-(new - cur) / T)):
            cur = new
            if cur < best:
                best = cur
                best_master = {k: v.copy() for k, v in master.items()}
        else:
            master[(var, g)] = old
    for k, v in best_master.items():
        master[k] = v
    return best


# ══════════════════════════════════════════════════════════════════
# stage3 相关配对（组内 swap 退火；边际分布不变 → 矩/AUC/P/计数全不动）
# ══════════════════════════════════════════════════════════════════
def corr_masks(master: dict) -> list:
    """预计算每个相关目标的受试者掩码（重叠 = 两变量均非 NaN，且组域限定）。"""
    out = []
    for (x, y, coh, scope, r_t, p_t) in CORR:
        groups = GROUPS if scope == "all" else (scope,)
        masks = []
        for g in groups:
            ax, ay = master[(x, g)], master[(y, g)]
            m = (~np.isnan(ax)) & (~np.isnan(ay))
            if m.sum() >= 3:
                masks.append((g, m))
        n_tot = int(sum(m.sum() for _, m in masks))
        out.append({"x": x, "y": y, "r_t": r_t, "p_t": p_t,
                    "masks": masks, "n": n_tot})
    return out


def corr_achieved(master: dict, ct: dict) -> tuple:
    """按当前配对计算合并 Pearson r 与 t 检验 p；返回 (r, p)。"""
    xs, ys = [], []
    for g, m in ct["masks"]:
        xs.append(master[(ct["x"], g)][m])
        ys.append(master[(ct["y"], g)][m])
    xv = np.concatenate(xs)
    yv = np.concatenate(ys)
    r = float(np.corrcoef(xv, yv)[0, 1])
    n = len(xv)
    if n < 4 or abs(r) >= 1.0:
        return r, float("nan")
    tstat = r * math.sqrt((n - 2) / max(1e-12, 1 - r * r))
    p = float(2 * stats.t.sf(abs(tstat), n - 2))
    return r, p


def corr_loss(master: dict, CT: list) -> tuple:
    """返回 (Σ((r-r_t)/R_TOL)², max|r-r_t|)；收敛判据用逐项 max。"""
    loss = 0.0
    mx = 0.0
    for ct in CT:
        r, _ = corr_achieved(master, ct)
        d = abs(r - ct["r_t"])
        mx = max(mx, d)
        loss += ((r - ct["r_t"]) / R_TOL) ** 2
    return loss, mx


SWAP_KEYS = [(v, g) for v in ("mT2", "mT1", "GA", "SINBAD", "mFA", "mADC")
             for g in GROUPS]


def stage3_run(master: dict, CT: list, rng: np.random.RandomState) -> tuple:
    """Metropolis 退火 swap + 贪心抛光：逼近全部相关目标。

    返回 (最终损失, max|Δr|)。收敛判据 = 逐项 |Δr| <= R_TOL（非总和）。
    退火段跟踪最优快照并在结束时恢复；抛光段纯爬山，停滞预算尽即停。
    """
    cur, mx = corr_loss(master, CT)
    t0, t1 = 20.0, 1e-3
    valid = []
    for (var, g) in SWAP_KEYS:
        idx = np.flatnonzero(~np.isnan(master[(var, g)]))
        if len(idx) >= 2:
            valid.append((var, g, idx))
    best = (cur, mx)
    best_master = {k: v.copy() for k, v in master.items()}
    for it in range(STAGE3_ITERS):
        if best[1] <= R_TOL:
            break
        var, g, idx = valid[int(rng.randint(len(valid)))]
        i, j = rng.choice(idx, size=2, replace=False)
        arr = master[(var, g)]
        arr[i], arr[j] = arr[j], arr[i]
        new, nmx = corr_loss(master, CT)
        T = t0 * (t1 / t0) ** (it / STAGE3_ITERS)
        if new <= cur or rng.rand() < math.exp(-(new - cur) / T):
            cur, mx = new, nmx
            if nmx < best[1] or (nmx == best[1] and new < best[0]):
                best = (new, nmx)
                best_master = {k: v.copy() for k, v in master.items()}
        else:
            arr[i], arr[j] = arr[j], arr[i]
    for k, v in best_master.items():
        master[k] = v
    cur, mx = best
    stale = 0
    for _ in range(STAGE3_POLISH_ITERS):
        if mx <= R_TOL or stale >= STAGE3_POLISH_STALE:
            break
        var, g, idx = valid[int(rng.randint(len(valid)))]
        i, j = rng.choice(idx, size=2, replace=False)
        arr = master[(var, g)]
        arr[i], arr[j] = arr[j], arr[i]
        new, nmx = corr_loss(master, CT)
        if nmx < mx or (nmx == mx and new < cur):
            cur, mx = new, nmx
            stale = 0
        else:
            arr[i], arr[j] = arr[j], arr[i]
            stale += 1
    return cur, mx


# ══════════════════════════════════════════════════════════════════
# stage4 核验与交付
# ══════════════════════════════════════════════════════════════════
def draw_base(rng: np.random.RandomState, var: str, n: int,
              m: float, sd: float, tries: int = 500) -> np.ndarray:
    """抽基础 z，要求在 a=b=0 下 apply_bounds 干净收敛（边界安全）；耗尽抛错。"""
    for _ in range(tries):
        z = std_base(rng, n)
        try:
            apply_bounds(shape_std(z, 0.0, 0.0), m, sd, *BOUNDS[var])
            return z
        except RuntimeError:
            continue
    raise RuntimeError(f"draw_base 失败: var={var}, n={n}, m={m}, sd={sd}")


def display_moments(vals: np.ndarray, var: str) -> tuple:
    """按源表报告位数取 (mean, sd)：均值用 MEAN_DP（落盘位），SD 用 SD_DP（模拟位）。"""
    sd = round(float(vals.std(ddof=1)), SD_DP[var]) if len(vals) > 1 else None
    return round(float(vals.mean()), MEAN_DP[var]), sd


def round_sim(master: dict) -> None:
    """把个体值落到模拟精度 SIM_DP（ROC 变量 4 位，其余取 SD 位数；SINBAD 整数）。"""
    for key, arr in master.items():
        var = key[0]
        master[key] = arr if var in INT_VARS else np.round(arr, SIM_DP[var])


def landed_view(master: dict) -> dict:
    """落盘副本：按 LAND_DP（均值位数）取整；不改动 master（Verify 仍在模拟精度上算）。"""
    return {k: (v if k[0] in INT_VARS else np.round(v, LAND_DP[k[0]]))
            for k, v in master.items()}


def moments_match(master: dict, var: str, g: str, relaxed: set) -> bool:
    """该 (var,group) 是否仍重现表内报告值：均值按 MEAN_DP、SD 按 SD_DP（relaxed 跳过 F）。"""
    for coh in ("S", "F"):
        if coh not in MOM[var] or g not in MOM[var][coh]:
            continue
        if coh == "F" and (var, g) in relaxed:
            continue
        n_t, m_t, sd_t = MOM[var][coh][g]
        vals = cohort_vals(master, var, coh, g)
        if len(vals) != n_t:
            return False
        if round(float(vals.mean()), MEAN_DP[var]) != round(m_t, MEAN_DP[var]):
            return False
        if round(float(vals.std(ddof=1)), SD_DP[var]) != round(sd_t, SD_DP[var]):
            return False
    return True


def ternary_fixed_delta(xi: float, xj: float, xk: float, delta: float,
                        rng: np.random.RandomState):
    """给定 δ 解 (ε, ζ)，使 Σx 与 Σx² 双守恒（ζ=-δ-ε，ε 解一元二次）。

    返回 ε；判别式 <0（该 δ 在 j/k 下无实解）返回 None。
    """
    A = xi - xk
    B = xj - xk
    disc = (B + delta) ** 2 - 4.0 * delta * (A + delta)
    if disc < 0:
        return None
    root = math.sqrt(disc)
    return ((-(B + delta) + root) / 2.0 if rng.rand() < 0.5
            else (-(B + delta) - root) / 2.0)


def micro_repair(master: dict, RT: list, rng: np.random.RandomState,
                 iters: int = MICRO_REPAIR_ITERS) -> tuple:
    """模拟精度舍入后的计数微修：只动临界值，用「定 δ 三元移动」把翻转的计数改回。

    对每个离散损失 >0 的 ROC 行，挑一个距 cut ≤3 ulp 的值 i，把它推到 cut 另一侧
    1.5 ulp 处；补偿由 j/k 分摊（sum/sumsq 双守恒的一元二次解）。候选须：仍在
    SIM_DP 网格、生理边界内、该 (var,group) 的显示精度矩不变、整表离散损失严格
    下降；否则回滚。返回 (最终离散损失, 已试候选数)。
    """
    cur, det = roc_discrete(master, RT)
    if cur == 0:
        return 0.0, 0
    base = {}
    for (var, g), arr in master.items():
        idx0 = np.flatnonzero(~np.isnan(arr))
        if len(idx0) >= 2:
            base[(var, g)] = display_moments(arr[idx0], var)
    tried = 0
    for _ in range(iters):
        if cur == 0:
            break
        rows = [d for d in det if d["eS"] or d["eP"] or d["eA"]]
        if not rows:
            break
        row = rows[int(rng.randint(len(rows)))]
        sides = []
        if row["eS"]:
            sides.append(("gA", row["ca"] - row["cnt_a_t"]))
        if row["eP"]:
            sides.append(("gB", row["cb"] - row["cnt_b_t"]))
        if not sides:
            continue
        which, signed = sides[int(rng.randint(len(sides)))]
        var, g = row["var"], row[which]
        arr = master[(var, g)]
        idx = np.flatnonzero(~np.isnan(arr))
        if len(idx) < 3:
            continue
        ulp = 1.0 if var in INT_VARS else 10.0 ** (-SIM_DP[var])
        cut = row["cut"]
        sgn = 1.0 if row["direction"] == "higher_pos" else -1.0
        want_uncount = signed > 0                  # 计数过多 → 把已计入的值推到另一侧
        crit = idx[np.abs(arr[idx] - cut) <= 3.0 * ulp]
        if not len(crit):
            continue
        counted = sgn * (arr[crit] - cut) >= 0
        pick = crit[counted == (not want_uncount)]
        if not len(pick):
            pick = crit
        i = int(pick[int(rng.randint(len(pick)))])
        target = cut - sgn * 1.5 * ulp if want_uncount else cut + sgn * 1.5 * ulp
        delta = float(target - arr[i])
        others = idx[idx != i]
        low, high = BOUNDS[var]
        for _cand in range(40):
            j, k = rng.choice(others, size=2, replace=False)
            eps = ternary_fixed_delta(float(arr[i]), float(arr[j]), float(arr[k]),
                                      delta, rng)
            if eps is None:
                continue
            new = arr.copy()
            new[i] += delta
            new[j] += eps
            new[k] += -delta - eps
            if new[idx].min() < low - 1e-12 or new[idx].max() > high + 1e-12:
                continue
            new = np.round(new, SIM_DP[var]) if var not in INT_VARS else new
            if display_moments(new[idx], var) != base[(var, g)]:
                continue
            tried += 1
            old = arr
            master[(var, g)] = new
            cand, cand_det = roc_discrete(master, RT)
            if cand < cur:
                cur, det = cand, cand_det
                break
            master[(var, g)] = old
    return cur, tried



def cohort_vals(master: dict, var: str, coh: str, g: str) -> np.ndarray:
    """取 (var, cohort, group) 的值向量（S=子集位；F=全长）。"""
    arr = master[(var, g)]
    return arr[:NS[g]] if coh == "S" else arr


def fmt_p(p: float) -> str:
    if not np.isfinite(p):
        return "NA"
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def p_pair(x1: np.ndarray, x2: np.ndarray) -> tuple:
    """两两比较三法 p：Welch t / pooled t / Mann-Whitney U。"""
    welch = float(stats.ttest_ind(x1, x2, equal_var=False).pvalue)
    pooled = float(stats.ttest_ind(x1, x2, equal_var=True).pvalue)
    mw = float(stats.mannwhitneyu(x1, x2, alternative="two-sided").pvalue)
    return welch, pooled, mw


def p_omni(groups: list) -> tuple:
    """三组总体 p：ANOVA F / Kruskal-Wallis。"""
    anova = float(stats.f_oneway(*groups).pvalue)
    kw = float(stats.kruskal(*groups).pvalue)
    return anova, kw


PAIR_MAP = {"P1": ("Positive", "Negative"), "P2": ("Positive", "Control"),
            "P3": ("Negative", "Control")}


def verify_moments(st: dict, master: dict) -> list:
    """A 档核验：各组 n/mean/SD 是否精确重现表内显示值。"""
    rows = []
    relaxed = {(v, g): note for (v, g, note) in st["relaxed"]}
    for var in MOM:
        for coh in ("S", "F"):
            if coh not in MOM[var]:
                continue
            for g, (n_t, m_t, sd_t) in MOM[var][coh].items():
                vals = cohort_vals(master, var, coh, g)
                m_dp, s_dp = MEAN_DP[var], SD_DP[var]
                m_a = round(float(vals.mean()), m_dp)
                sd_a = round(float(vals.std(ddof=1)), s_dp)
                ok = (len(vals) == n_t and m_a == round(m_t, m_dp)
                      and sd_a == round(sd_t, s_dp))
                note = ""
                status = "EXACT"
                if not ok:
                    if coh == "F" and (var, g) in relaxed:
                        status = "INCOMPATIBLE"
                        note = relaxed[(var, g)]
                    else:
                        status = "MISS"
                rows.append({"block": "moments",
                             "item": f"{var}|{coh}|{g}",
                             "target": f"n={n_t}, {m_t}({sd_t})",
                             "achieved": f"n={len(vals)}, {m_a}({sd_a})",
                             "delta": "" if ok else f"Δm={m_a - m_t:+.4f}, Δsd={sd_a - sd_t:+.4f}",
                             "status": status, "note": note})
    return rows


def verify_p(master: dict) -> list:
    """C 档核验：重算 P（两法都报），阈值型判 HIT/MISS，精确型如实 REPORT。"""
    rows = []
    for (coh, var), targets in PV.items():
        avail = MOM[var][coh]
        for comp, tgt in targets.items():
            if comp == "overall":
                gs = [g for g in GROUPS if g in avail]
                if len(gs) < 2:
                    continue
                anova, kw = p_omni([cohort_vals(master, var, coh, g) for g in gs])
                achieved = f"ANOVA={fmt_p(anova)}, KW={fmt_p(kw)}"
                vals = {"ANOVA": anova, "KW": kw}
            else:
                gA, gB = PAIR_MAP[comp]
                if gA not in avail or gB not in avail:
                    continue
                w, pp, mw = p_pair(cohort_vals(master, var, coh, gA),
                                   cohort_vals(master, var, coh, gB))
                achieved = f"Welch={fmt_p(w)}, pooled={fmt_p(pp)}, MW={fmt_p(mw)}"
                vals = {"Welch": w, "pooled": pp, "MW": mw}
            if isinstance(tgt, str):                 # 阈值型 "<x"
                thr = float(tgt.lstrip("<"))
                hits = [k for k, v in vals.items() if v < thr]
                status = "HIT" if hits else "MISS"
                note = f"命中方法: {','.join(hits)}" if hits else "无方法命中阈值"
                delta = ""
            else:
                best = min(vals.items(), key=lambda kv: abs(kv[1] - tgt))
                status = "REPORT"
                delta = f"最近法 {best[0]}: Δ={best[1] - tgt:+.4f}"
                note = "P 由矩决定，不可独立调节（C 档口径）"
            rows.append({"block": "P", "item": f"{coh}|{var}|{comp}",
                         "target": str(tgt), "achieved": achieved,
                         "delta": delta, "status": status, "note": note})
    return rows


def verify_roc(master: dict, RT: list) -> list:
    """B 档核验：AUC/sens/spec 对最近数学可达值；粒度不可达如实标注。"""
    rows = []
    _, details = roc_discrete(master, RT)
    for t in details:
        base = f"{t['var']}|{t['gA']} vs {t['gB']} ({t['coh']})"
        gran = 1.0 / (t["nA"] * t["nB"])
        unreach = abs(t["auc_d"] - t["auc_t"]) > 0.0005
        note = (f"目标在粒度 {gran:.6f}=1/({t['nA']}×{t['nB']}) 上不可达 → "
                f"最近可达 {t['auc_d']:.4f}") if unreach else ""
        if abs(t["auc"] - t["auc_t"]) <= 0.0005:
            status = "EXACT"
        elif abs(t["auc"] - t["auc_d"]) < 1e-9:
            status = "NEAREST"
        else:
            status = "MISS"
        rows.append({"block": "ROC-AUC", "item": base,
                     "target": f"{t['auc_t']:.3f}",
                     "achieved": f"{t['auc']:.4f}",
                     "delta": f"{t['auc'] - t['auc_t']:+.4f}",
                     "status": status, "note": note})
        cnt_achr = t["ca"] / t["nA"]
        unreach_s = abs(t["sens_d"] - t["sens_t"]) > 0.0005
        note_s = (f"目标 {t['sens_t']:.3f} 不可达（k/{t['nA']}）→ 最近 "
                  f"{t['cnt_a_t']}/{t['nA']}={t['sens_d']:.3f}") if unreach_s else ""
        status_s = ("EXACT" if abs(cnt_achr - t["sens_t"]) <= 0.0005
                    else "NEAREST" if t["ca"] == t["cnt_a_t"] else "MISS")
        rows.append({"block": "ROC-sens", "item": base + f" @cut={t['cut']}",
                     "target": f"{t['sens_t']:.3f}",
                     "achieved": f"{t['ca']}/{t['nA']}={cnt_achr:.3f}",
                     "delta": f"{cnt_achr - t['sens_t']:+.3f}",
                     "status": status_s, "note": note_s})
        cnt_bchr = t["cb"] / t["nB"]
        unreach_p = abs(t["spec_d"] - t["spec_t"]) > 0.0005
        note_p = (f"目标 {t['spec_t']:.3f} 不可达（k/{t['nB']}）→ 最近 "
                  f"{t['cnt_b_t']}/{t['nB']}={t['spec_d']:.3f}") if unreach_p else ""
        status_p = ("EXACT" if abs(cnt_bchr - t["spec_t"]) <= 0.0005
                    else "NEAREST" if t["cb"] == t["cnt_b_t"] else "MISS")
        rows.append({"block": "ROC-spec", "item": base + f" @cut={t['cut']}",
                     "target": f"{t['spec_t']:.3f}",
                     "achieved": f"{t['cb']}/{t['nB']}={cnt_bchr:.3f}",
                     "delta": f"{cnt_bchr - t['spec_t']:+.3f}",
                     "status": status_p, "note": note_p})
    return rows


def verify_corr(master: dict, CT: list) -> list:
    """相关系数核验：|Δr|<=R_TOL → HIT；否则如实报告。"""
    rows = []
    for ct in CT:
        r, p = corr_achieved(master, ct)
        ok = abs(r - ct["r_t"]) <= R_TOL
        scope = ct["masks"][0][0] if len(ct["masks"]) == 1 else "pooled"
        rows.append({"block": "corr",
                     "item": f"{ct['x']}-{ct['y']} ({scope}, n={ct['n']})",
                     "target": f"r={ct['r_t']:.3f}, p={ct['p_t']}",
                     "achieved": f"r={r:.4f}, p={fmt_p(p)}",
                     "delta": f"Δr={r - ct['r_t']:+.4f}",
                     "status": "HIT" if ok else "MISS",
                     "note": "" if ok else "配对自由度已用尽，如实报告"})
    return rows


def verify_landed(st: dict, master: dict, landed: dict, RT: list) -> list:
    """落盘复核：在落盘值（均值位数）上重算矩与 ROC，漂移如实记 DRIFT（不改 master）。"""
    rows = []
    relaxed = {(v, g): note for (v, g, note) in st["relaxed"]}
    for var in MOM:
        for coh in ("S", "F"):
            if coh not in MOM[var]:
                continue
            for g, (n_t, m_t, sd_t) in MOM[var][coh].items():
                vals = cohort_vals(landed, var, coh, g)
                m_a = round(float(vals.mean()), MEAN_DP[var])
                sd_a = round(float(vals.std(ddof=1)), SD_DP[var])
                ok = (len(vals) == n_t and m_a == round(m_t, MEAN_DP[var])
                      and sd_a == round(sd_t, SD_DP[var]))
                status = "EXACT"
                if not ok:
                    status = ("INCOMPATIBLE" if (coh == "F" and (var, g) in relaxed)
                              else "DRIFT")
                rows.append({"block": "moments-landed", "item": f"{var}|{coh}|{g}",
                             "target": f"n={n_t}, {m_t}({sd_t})",
                             "achieved": f"n={len(vals)}, {m_a}({sd_a})",
                             "delta": ("" if ok else
                                       f"Δm={m_a - m_t:+.4f}, Δsd={sd_a - sd_t:+.4f}"),
                             "status": status,
                             "note": "" if ok else "落盘位数（均值位）造成的显示差"})
    for t in roc_discrete(landed, RT)[1]:
        base = f"{t['var']}|{t['gA']} vs {t['gB']} ({t['coh']})"
        e_s = t["ca"] - t["cnt_a_t"]
        e_p = t["cb"] - t["cnt_b_t"]
        e_a = abs(round(t["auc"] * t["nA"] * t["nB"]) - t["auc_k"])
        ok = e_s == 0 and e_p == 0 and e_a == 0
        rows.append({"block": "ROC-landed", "item": base,
                     "target": f"AUC {t['auc_d']:.4f}, sens {t['cnt_a_t']}/{t['nA']}, "
                               f"spec {t['cnt_b_t']}/{t['nB']}",
                     "achieved": f"AUC {t['auc']:.4f}, sens {t['ca']}/{t['nA']}, "
                                 f"spec {t['cb']}/{t['nB']}",
                     "delta": f"ΔAUC={t['auc'] - t['auc_d']:+.4f}, Δsens={e_s:+d}, "
                              f"Δspec={e_p:+d}",
                     "status": "EXACT" if ok else "DRIFT",
                     "note": "" if ok else "落盘位数（1 位/3 位）下该计数/格点发生移动"})
    return rows


def build_rawdata(master: dict) -> pd.DataFrame:
    """受试者级长表：一行一人；S-only 变量非子集位空值；SYNTHETIC=1 标记。"""
    abbr = {"Positive": "POS", "Negative": "NEG", "Control": "CTL"}
    rows = []
    for g in GROUPS:
        for i in range(NF[g]):
            row = {"subject_id": f"SYN_{abbr[g]}_{i + 1:03d}", "group": g,
                   "in_subset_S": int(i < NS[g])}
            for var, col in RAW_COLS:
                v = master[(var, g)][i]
                if np.isnan(v):
                    row[col] = None
                elif var in INT_VARS:
                    row[col] = int(round(float(v)))
                else:
                    row[col] = float(v)
            row["SYNTHETIC"] = 1
            rows.append(row)
    return pd.DataFrame(rows)



def build_readme(st: dict, RT: list) -> list:
    """README sheet：方法、假设、不相容清单、警示（全部程序化生成）。"""
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    rows = [
        {"field": "purpose", "value": "从 re.xlsx 汇总统计表还原的受试者级合成原始数据"
                                     "（meta 分析用）。全部为 SYNTHETIC，非真实测量，不得冒充实测。"},
        {"field": "source", "value": str(SRC_XLSX)},
        {"field": "generated_at",
         "value": now.strftime("%Y-%m-%d %H:%M:%S (Asia/Shanghai UTC+08:00)")},
        {"field": "script", "value": str(Path(__file__).resolve())},
        {"field": "seed", "value": f"{SEED}（固定种子，全程可复现）"},
        {"field": "cohort", "value": "同一批 56 人：Positive 23 / Negative 13 / Control 20"
                                     "（Sheet2 全队列 F）；Sheet1 子集 S = 51 人（19/12/20，"
                                     "in_subset_S=1）。被排除者（Positive 4 人 + Negative 1 人，"
                                     "in_subset_S=0）无 GA/SINBAD/mT2/mT1。"},
        {"field": "precision", "value": "两套位数：**模拟精度**（带 AUC/CI 的变量 4 位，其余取 SD 位数，"
                                        "如 age 8.4→1 位；Verify 前段在此精度上核验）与**落盘精度**"
                                        "（取均值位数：age 71.2→1 位、mFA 0.418→3 位、SINBAD 整数），"
                                        "RawData 按落盘精度写出。城城 2026-11-11 裁定：模拟数据最终落盘时"
                                        "小数位数与表格一致（如 mT2 90.4 → 1 位）。"},
        {"field": "landed_check", "value": "Verify 的 *-landed 行（moments-landed / ROC-landed）"
                                          "是在落盘值上重算的结果；与模拟精度行不同即记 DRIFT，"
                                          "如实反映落盘位数带来的显示差/计数移动。"},
        {"field": "display_format", "value": "RawData 变量列的单元格显示位与表内均值位数一致"
                                            "（1 位→0.0、3 位→0.000、SINBAD→0）。"},

        {"field": "assumption_1", "value": "P1/P2/P3 = Pos vs Neg / Pos vs Ctrl / Neg vs Ctrl"
                                           "（用 age、mT1 行数值反推验证此顺序）。"},
        {"field": "assumption_2", "value": "跨 sheet 相关（mFA-GA 等）在两变量均有值的重叠受试者"
                                           "上计算（mFA/mADC∩GA = 子集 51 人；Pos 组内 ∩SINBAD = 19 人）。"},
        {"field": "assumption_3", "value": "SINBAD Control 组「/」视为不适用；其 overall/P2/P3 为"
                                           "模板残留，不拟合（仅拟合 P1 = Pos vs Neg）。"},
        {"field": "method", "value": "stage1 解析矩分解（SS 恒等式 + ±0.05 显示舍入窗口网格）→ "
                                     "stage2 ROC 形态调参（scipy.optimize.minimize Powell，logistic 平滑）→ "
                                     "repair 矩不变三元移动（sum/sumsq 双守恒）→ "
                                     "落到模拟精度（ROC 变量 4 位）→ 计数微修（定 δ 三元移动，只动临界值）→ "
                                     "stage3 相关配对（组内 swap Metropolis 退火，边际不变）→ "
                                     "落盘副本按均值位数写出并复核。"},


        {"field": "p_policy", "value": "mean/SD/n 锁定后参数法 P 由矩完全决定，不可独立调节；"
                                       "Verify 表报告 Welch/pooled/MW/KW/ANOVA 实测值（C 档）。"},
        {"field": "verify", "value": "Verify sheet：moments（A 档）/ P（C 档）/ ROC-AUC·sens·spec"
                                     "（B 档，含粒度不可达标注）/ corr（±0.0015）。"},
    ]
    for (var, g, note) in st["relaxed"]:
        rows.append({"field": f"INCOMPATIBLE:{var}|{g}", "value": note})
    for t in RT:
        if abs(t["auc_d"] - t["auc_t"]) > 0.0005:
            rows.append({"field": f"UNREACHABLE-AUC:{t['var']}|{t['gA']}vs{t['gB']}",
                         "value": f"目标 {t['auc_t']:.3f} 在 1/({t['nA']}×{t['nB']}) 粒度"
                                  f"不可达，取最近 {t['auc_d']:.4f}"})
        if abs(t["sens_d"] - t["sens_t"]) > 0.0005:
            rows.append({"field": f"UNREACHABLE-SENS:{t['var']}|{t['gA']}vs{t['gB']}",
                         "value": f"目标 {t['sens_t']:.3f} 非 k/{t['nA']}，取最近 "
                                  f"{t['cnt_a_t']}/{t['nA']}={t['sens_d']:.3f}"})
        if abs(t["spec_d"] - t["spec_t"]) > 0.0005:
            rows.append({"field": f"UNREACHABLE-SPEC:{t['var']}|{t['gA']}vs{t['gB']}",
                         "value": f"目标 {t['spec_t']:.3f} 非 k/{t['nB']}，取最近 "
                                  f"{t['cnt_b_t']}/{t['nB']}={t['spec_d']:.3f}"})
    return rows


def write_workbook(path: Path, raw_df: pd.DataFrame,
                   verify_rows: list, readme_rows: list) -> None:
    """写交付工作簿：RawData / Verify / README 三 sheet。

    RawData 的数值本身已落到表内显示位（land_master）；此处再按 RAW_FMT 设单元格
    显示位，保证打开时看到的小数位与源表一致。
    """
    verify_df = pd.DataFrame(verify_rows, columns=[
        "block", "item", "target", "achieved", "delta", "status", "note"])
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        raw_df.to_excel(w, sheet_name="RawData", index=False)
        ws = w.sheets["RawData"]
        for j, col in enumerate(raw_df.columns, start=1):
            fmt = RAW_FMT.get(col)
            if fmt is None:
                continue
            for i in range(2, len(raw_df) + 2):
                ws.cell(row=i, column=j).number_format = fmt
        verify_df.to_excel(w, sheet_name="Verify", index=False)
        pd.DataFrame(readme_rows).to_excel(w, sheet_name="README", index=False)



def main() -> None:
    """CLI 入口：默认 dry-run（只打印可行性预检）；--execute 才生成并写盘。"""
    global SRC_XLSX, OUT_XLSX
    ap = argparse.ArgumentParser(
        description="从 re.xlsx 汇总统计表还原受试者级合成原始数据（meta 分析用）。"
                    "默认 dry-run 只做锚点核验与可行性预检，不写盘；--execute 生成并写出。")
    ap.add_argument("--execute", action="store_true",
                    help="运行全部 stage 并写出 OUT_XLSX（默认关闭）")
    ap.add_argument("--src", default=None, help="覆盖 SRC_XLSX（源汇总表路径，只读）")
    ap.add_argument("--out", default=None, help="覆盖 OUT_XLSX（交付工作簿路径）")
    args = ap.parse_args()
    if args.src:
        SRC_XLSX = Path(args.src)
    if args.out:
        OUT_XLSX = Path(args.out)

    print("[0] 锚点核验 …")
    check_anchors(SRC_XLSX)
    print("    锚点全部命中 ✓")

    rng = np.random.RandomState(SEED)
    print("[1] stage1 矩方案解析 …")
    st = stage1_build(rng)
    for (var, g, note) in st["relaxed"]:
        print(f"    ⚠ INCOMPATIBLE {var}|{g}: {note}")
    for key in sorted(st["sol"]):
        s = st["sol"][key]
        comp = s["comp"]
        comp_txt = ("无" if comp is None else
                    f"nC={comp['nC']}, mC={comp['mC']:.4f}" +
                    (f", sdC={comp['sdC']:.4f}" if comp["sdC"] else ""))
        if s["mF"] is not None:
            print(f"    {key[0]:6s}|{key[1]:8s}: S=({s['mS']:.4f},{s['sdS']:.4f}) "
                  f"F=({s['mF']:.4f},{s['sdF']:.4f}) 补集: {comp_txt}")
        else:
            print(f"    {key[0]:6s}|{key[1]:8s}: 仅S=({s['mS']:.4f},{s['sdS']:.4f})")
    RT = roc_targets_discrete()
    print("[1b] ROC 粒度分析（B 档最近可达值）:")
    for t in RT:
        flags = []
        if abs(t["auc_d"] - t["auc_t"]) > 0.0005:
            flags.append(f"AUC {t['auc_t']:.3f}→{t['auc_d']:.4f}")
        if abs(t["sens_d"] - t["sens_t"]) > 0.0005:
            flags.append(f"sens {t['sens_t']:.3f}→{t['sens_d']:.3f}")
        if abs(t["spec_d"] - t["spec_t"]) > 0.0005:
            flags.append(f"spec {t['spec_t']:.3f}→{t['spec_d']:.3f}")
        mark = "; ".join(flags) if flags else "全部精确可达"
        print(f"    {t['var']:4s} {t['gA'][:3]}vs{t['gB'][:3]}({t['coh']}): {mark}")
    for var in INT_VARS:
        for g in GROUPS:
            if (var, g) in st["sinbad"]:
                v = st["sinbad"][(var, g)]
                print(f"[1c] {var}|{g} 整数多重集: mean={v.mean():.4f}, "
                      f"sd={v.std(ddof=1):.4f}, 值域=[{v.min():.0f},{v.max():.0f}]")

    if not args.execute:
        print(f"dry-run 完成：未写盘。加 --execute 生成 {OUT_XLSX}")
        return

    print("[1d] 组装 master …")
    master = assemble_master(st)
    print("[2] stage2 ROC 形态调参（Powell × 退火 × 重启）…")
    disc = stage2_run(st, master, RT, rng)
    print(f"    stage2 离散损失 = {disc:.1f}")
    print("[2b] repair 矩不变三元移动 …")
    disc = repair_run(master, RT, rng)
    print(f"    repair 后离散损失 = {disc:.1f}")
    print("[2c] 落到模拟精度（ROC 变量 4 位；其余取 SD 位数；SINBAD 整数）…")
    round_sim(master)
    post_disc, _ = roc_discrete(master, RT)
    print(f"    模拟精度离散损失 = {post_disc:.1f}")
    if post_disc > 0:
        print("[2d] 模拟精度计数微修（定 δ 三元移动，只动临界值）…")
        post_disc, tried = micro_repair(master, RT, rng)
        print(f"    微修后离散损失 = {post_disc:.1f}（试过候选 {tried}）")
    if post_disc > 0:
        print(f"    ⚠ 仍有离散损失（模拟精度下不可达）：{post_disc:.1f}")
    print("[3] stage3 相关配对退火 …")
    CT = corr_masks(master)
    closs, cmax = stage3_run(master, CT, rng)
    print(f"    stage3 相关损失 = {closs:.2f}, max|Δr| = {cmax:.4f}（目标 ±{R_TOL}）")
    print("[4] 核验与写盘 …")
    landed = landed_view(master)
    raw_df = build_rawdata(landed)
    verify_rows = (verify_moments(st, master) + verify_p(master) +
                   verify_roc(master, RT) + verify_corr(master, CT) +
                   verify_landed(st, master, landed, RT))
    n_by_status: dict = {}
    for r in verify_rows:
        n_by_status[r["status"]] = n_by_status.get(r["status"], 0) + 1
    print(f"    Verify 状态分布: {n_by_status}")
    readme_rows = build_readme(st, RT)
    write_workbook(OUT_XLSX, raw_df, verify_rows, readme_rows)
    print(f"[4b] 已写出 → {OUT_XLSX}")
    miss = [r for r in verify_rows if r["status"] in ("MISS", "INCOMPATIBLE")]
    if miss:
        print("    未精确命中项（如实报告，详见 Verify sheet）:")
        for r in miss:
            print(f"      [{r['status']}] {r['block']} {r['item']}: "
                  f"target={r['target']} achieved={r['achieved']} {r['note']}")


if __name__ == "__main__":
    main()
