"""IPCW 删失 KM 与 lifelines.KaplanMeierFitter 的对拍（1e-12）。

生产代码保持自写：lifelines 版会丢 `censor_surv_at_events` 的严格查表守卫
（事件时间不在删失表里必须 raise），且 1e-16 漂移会动 IPCW 数字（B1）。
本文件只作独立 oracle，补上 PLAN §13 记的「IPCW 无对拍」缺口。

Run: python -B -m pytest modules/tests/test_ipcw_lifelines_parity.py -q
"""
from __future__ import annotations

import numpy as np
import pytest
from lifelines import KaplanMeierFitter

from modules.stats.curves_ipcw import (
    censor_surv_after,
    censor_surv_at_events,
    km_censor_table,
)

TOL = 1e-12

TIME = np.array([1.0, 2.0, 2.0, 3.0, 4.0, 5.0, 5.0, 6.0, 8.0, 9.0, 10.0, 12.0])
STATUS = np.array([1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0])


def _kmf() -> KaplanMeierFitter:
    return KaplanMeierFitter().fit(TIME, event_observed=1 - STATUS)


def test_survival_after_events_matches_lifelines() -> None:
    uniq, _before, after = km_censor_table(TIME, STATUS)
    want = _kmf().survival_function_at_times(uniq).to_numpy()
    assert np.allclose(after, want, rtol=0, atol=TOL)


def test_before_is_the_previous_step() -> None:
    uniq, before, after = km_censor_table(TIME, STATUS)
    assert before[0] == 1.0
    assert np.allclose(before[1:], after[:-1], rtol=0, atol=0.0)
    assert len(uniq) == len(set(TIME.tolist()))


def test_censor_surv_at_events_matches_lifelines() -> None:
    """G(t-)：事件时间处的「左极限」，等于 lifelines 里严格小于该时间的最后一个台阶。"""
    table = km_censor_table(TIME, STATUS)
    events = np.unique(TIME[STATUS == 1])
    sf = _kmf().survival_function_
    timeline, values = sf.index.to_numpy(), sf.to_numpy().ravel()
    want = []
    for t in events:
        idx = int(np.searchsorted(timeline, t, side="left")) - 1
        want.append(1.0 if idx < 0 else float(values[idx]))
    got = censor_surv_at_events(table, events)
    assert np.allclose(got, want, rtol=0, atol=TOL)


@pytest.mark.parametrize("t", [0.5, 2.5, 4.0, 7.0, 12.0, 20.0])
def test_censor_surv_after_matches_lifelines_carry_forward(t: float) -> None:
    table = km_censor_table(TIME, STATUS)
    got = censor_surv_after(table, t)
    want = float(_kmf().survival_function_at_times(t).iloc[0])
    assert abs(got - want) < TOL


def test_event_time_outside_table_raises() -> None:
    table = km_censor_table(TIME, STATUS)
    with pytest.raises(ValueError, match="不在删失 KM 表中"):
        censor_surv_at_events(table, np.array([2.5]))
