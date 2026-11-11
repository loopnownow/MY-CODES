#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""smoke_test -- restore_raw.py 的单元/冒烟测试（只读，不写任何交付物）。

用法::

    python -B smoke_test.py    # 全部通过打印 SMOKE PASS；任一失败抛错退出非零
"""
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import restore_raw as rr  # noqa: E402


def _moment(x):
    return (float(x.mean()), float(x.std(ddof=1)))


def test_std_base_and_shapes():
    rng = np.random.RandomState(1)
    z = rr.std_base(rng, 25)
    assert abs(z.mean()) < 1e-12 and abs(z.std(ddof=1) - 1) < 1e-12
    y = rr.shape_std(z, 0.3, -0.2)
    assert abs(y.mean()) < 1e-12 and abs(y.std(ddof=1) - 1) < 1e-12
    cases = [("mT2", 19, 90.4, 24.7), ("GA", 12, 23.8, 6.5), ("GA", 20, 13.9, 1.4),
             ("mFA", 23, 0.418, 0.029), ("mADC", 13, 1.696, 0.281),
             ("mT1", 20, 1539.2, 241.8)]
    for (var, n, m, sd) in cases:
        x = rr.make_vec(np.random.RandomState(2), n, m, sd, var)
        assert abs(x.mean() - m) < 1e-9, (var, n, x.mean(), m)
        assert abs(x.std(ddof=1) - sd) < 1e-9
        lo, hi = rr.BOUNDS[var]
        assert x.min() >= lo - 1e-12 and x.max() <= hi + 1e-12
    print("  [1] std_base/shape_std/make_vec 矩精确 + 边界 ok")


def test_complement_identity():
    rng = np.random.RandomState(3)
    s = rr.make_vec(rng, 19, 71.2, 8.4, "age")
    c = rr.make_vec(rng, 4, 64.9, 16.0, "age")
    f = np.concatenate([s, c])
    comp = rr.complement_moments(19, *_moment(s), 23, *_moment(f))
    assert abs(comp["mC"] - c.mean()) < 1e-8
    assert abs(comp["sdC"] - c.std(ddof=1)) < 1e-8
    print("  [2] complement_moments SS 恒等式往返 ok")


def test_build_sinbad():
    rng = np.random.RandomState(4)
    pos = rr.build_sinbad(rng, 19, 4.4, 1.5)
    neg = rr.build_sinbad(rng, 12, 0.4, 0.7)
    for v, (m, sd) in [(pos, (4.4, 1.5)), (neg, (0.4, 0.7))]:
        assert np.all(v == np.round(v))
        assert v.min() >= 0 and v.max() <= 6
        assert round(float(v.mean()), 1) == m
        assert round(float(v.std(ddof=1)), 1) == sd
    print("  [3] build_sinbad 整数多重集显示位命中 ok")


def test_ternary_move_conservation():
    rng = np.random.RandomState(5)
    x = rr.make_vec(rng, 19, 90.4, 24.7, "mT2")
    idx = np.arange(19)
    n_ok = 0
    for _ in range(200):
        y = rr.ternary_move(x, idx, rng, 2.0)
        if y is None:
            continue
        assert abs(y.sum() - x.sum()) <= 1e-6 * abs(x.sum())
        assert abs((y ** 2).sum() - (x ** 2).sum()) <= 1e-6 * (x ** 2).sum()
        assert abs(y.mean() - x.mean()) < 1e-9
        assert abs(y.std(ddof=1) - x.std(ddof=1)) < 1e-9
        n_ok += 1
    assert n_ok > 50, f"三元移动成功率过低: {n_ok}/200"
    print(f"  [4] ternary_move sum/sumsq 双守恒（{n_ok}/200 有效）ok")



def test_auc_mw_known():
    assert rr.auc_mw(np.array([1., 2., 3.]), np.array([0., 0., 0.]),
                     "higher_pos") == 1.0
    assert rr.auc_mw(np.array([1., 2., 3.]), np.array([4., 5., 6.]),
                     "higher_pos") == 0.0
    assert rr.auc_mw(np.array([1., 1.]), np.array([1., 1.]),
                     "higher_pos") == 0.5
    assert rr.auc_mw(np.array([1., 2., 3.]), np.array([4., 5., 6.]),
                     "lower_pos") == 1.0
    print("  [5] auc_mw 已知例（含平局/方向）ok")


def test_roc_targets_discrete():
    RT = {t["var"] + t["gA"][:3] + t["gB"][:3]: t
          for t in rr.roc_targets_discrete()}
    assert abs(RT["mT1PosNeg"]["auc_d"] - 180 / 228) < 1e-12
    assert abs(RT["mFANegCon"]["auc_d"] - 241 / 260) < 1e-12
    assert RT["mT2PosNeg"]["cnt_a_t"] == 17 and RT["mT2PosNeg"]["cnt_b_t"] == 11
    assert RT["mADCNegCon"]["cnt_a_t"] == 9 and RT["mADCNegCon"]["cnt_b_t"] == 14
    print("  [6] roc_targets_discrete 粒度换算 ok")


def test_subset_superset_solutions():
    s_ageP = rr.solve_subset_superset("age", "Positive")
    assert s_ageP["relaxed"] is None and s_ageP["comp"]["sdC"] is not None
    s_bmiP = rr.solve_subset_superset("BMI", "Positive")
    assert s_bmiP["relaxed"] is None
    s_bmiN = rr.solve_subset_superset("BMI", "Negative")
    assert s_bmiN["relaxed"] is None and s_bmiN["comp"]["nC"] == 1
    s_ageN = rr.solve_subset_superset("age", "Negative")
    assert s_ageN["relaxed"] is not None and "INCOMPATIBLE" in s_ageN["relaxed"]
    s_ctl = rr.solve_subset_superset("age", "Control")
    assert s_ctl["comp"] is None and s_ctl["mS"] == 66.4
    print("  [7] solve_subset_superset 四情形（含 age|Negative INCOMPATIBLE）ok")


def test_anchors():
    rr.check_anchors(rr.SRC_XLSX)


def test_mini_pipeline():
    rng = np.random.RandomState(rr.SEED)
    st = rr.stage1_build(rng)
    master = rr.assemble_master(st)
    relaxed = {(a, b) for (a, b, _n) in st["relaxed"]}
    for var, coh in [("age", "S"), ("age", "F"), ("BMI", "S"), ("BMI", "F")]:
        for g, (n_t, m_t, sd_t) in rr.MOM[var][coh].items():
            v = rr.cohort_vals(master, var, coh, g)
            assert len(v) == n_t and not np.isnan(v).any()
            if not (coh == "F" and (var, g) in relaxed):
                assert round(float(v.mean()), rr.MEAN_DP[var]) == m_t, (var, coh, g)
                assert round(float(v.std(ddof=1)), rr.SD_DP[var]) == sd_t, (var, coh, g)
    for var in ("GA", "mT2", "mT1"):
        for g, (n_t, m_t, sd_t) in rr.MOM[var]["S"].items():
            v = rr.cohort_vals(master, var, "S", g)
            assert abs(v.mean() - m_t) < 1e-9 and abs(v.std(ddof=1) - sd_t) < 1e-9
    for var in ("mFA", "mADC"):
        for g, (n_t, m_t, sd_t) in rr.MOM[var]["F"].items():
            v = rr.cohort_vals(master, var, "F", g)
            assert abs(v.mean() - m_t) < 1e-9 and abs(v.std(ddof=1) - sd_t) < 1e-9
    loss = rr.stage2_soft_loss(np.zeros(2 * len(rr.SHAPE_KEYS)), st, master,
                               rr.roc_targets_discrete(), 0.5)
    assert np.isfinite(loss)
    print("  [9] 迷你流水线（stage1+组装+矩核验+软损失有限）ok")


def test_sim_and_land_precision():
    assert rr.SIM_DP["mT2"] == 4 and rr.LAND_DP["mT2"] == 1     # AUC 需 4 位；均值 90.4 → 1 位
    assert rr.SIM_DP["age"] == 1 and rr.LAND_DP["age"] == 1     # SD 8.4 / 均值 71.2 都是 1 位
    assert rr.SIM_DP["mFA"] == 4 and rr.LAND_DP["mFA"] == 3     # 均值 0.418 → 3 位
    assert rr.SIM_DP["GA"] == 1 and rr.LAND_DP["GA"] == 1
    assert rr.SIM_DP["SINBAD"] == 1 and rr.LAND_DP["SINBAD"] == 0  # 整数分
    for v in rr.REPORT_DP:
        assert rr.LAND_DP[v] <= rr.SIM_DP[v], v
    print("  [10] 模拟精度/落盘精度映射（mT2 sim 4 / land 1；age 1/1；mFA 4/3）ok")


def test_micro_repair_on_sim_grid():
    master = {("mADC", "Negative"): rr.make_vec(np.random.RandomState(31), 13, 1.696, 0.281, "mADC"),
              ("mADC", "Control"): rr.make_vec(np.random.RandomState(32), 20, 1.240, 0.300, "mADC")}
    rr.round_sim(master)
    for k, v in master.items():
        scale = 10.0 ** rr.SIM_DP[k[0]]
        assert np.allclose(v * scale, np.round(v * scale)), k
    base = {k: rr.display_moments(v, k[0]) for k, v in master.items()}
    RT = [t for t in rr.roc_targets_discrete()
          if t["var"] == "mADC" and t["gA"] == "Negative" and t["gB"] == "Control"]
    pre, _ = rr.roc_discrete(master, RT)
    post, _tried = rr.micro_repair(master, RT, np.random.RandomState(33), iters=3000)
    assert post <= pre, (post, pre)
    assert abs(post - rr.roc_discrete(master, RT)[0]) < 1e-12
    for k, v in master.items():
        scale = 10.0 ** rr.SIM_DP[k[0]]
        assert np.allclose(v * scale, np.round(v * scale)), k
        assert rr.display_moments(v, k[0]) == base[k], k
        lo, hi = rr.BOUNDS[k[0]]
        assert v.min() >= lo - 1e-12 and v.max() <= hi + 1e-12
    landed = rr.landed_view(master)
    for k, v in landed.items():
        assert np.allclose(v, np.round(v, rr.LAND_DP[k[0]])), k
    print(f"  [11] 模拟精度微修 + 落盘副本（loss {pre:.0f} -> {post:.0f}，矩/边界不变）ok")


def test_raw_display_formats():
    cols = {col for _v, col in rr.RAW_COLS}
    assert set(rr.RAW_FMT) == cols, set(rr.RAW_FMT) ^ cols
    assert rr.RAW_FMT["SINBAD"] == "0"
    assert rr.RAW_FMT["age_y"] == "0.0" and rr.RAW_FMT["mT1_ms"] == "0.0"
    assert rr.RAW_FMT["mFA"] == "0.000" and rr.RAW_FMT["mADC_e3_mm2_s"] == "0.000"
    int_cols = {col for v, col in rr.RAW_COLS if v in rr.INT_VARS}
    assert {c for c, f in rr.RAW_FMT.items() if f == "0"} == int_cols
    for var, col in rr.RAW_COLS:
        dp = rr.LAND_DP[var]
        assert rr.RAW_FMT[col] == ("0" if dp == 0 else "0." + "0" * dp), (var, col, dp)
    print("  [12] RawData 显示位映射（按落盘位数，覆盖全部变量列）ok")


if __name__ == "__main__":
    test_std_base_and_shapes()
    test_complement_identity()
    test_build_sinbad()
    test_ternary_move_conservation()
    test_auc_mw_known()
    test_roc_targets_discrete()
    test_subset_superset_solutions()
    test_anchors()
    test_mini_pipeline()
    test_sim_and_land_precision()
    test_micro_repair_on_sim_grid()
    test_raw_display_formats()
    print("SMOKE PASS（12/12）")

    print("  [8] re.xlsx 锚点核验 ok")
