"""Multiple-imputation backends: default path unchanged, miceforest opt-in only.

`miceforest` is an *engine swap* (LightGBM instead of BayesianRidge), so the draws
are legitimately different; it is therefore never selected automatically. These
tests pin the four behaviours that matter:

* ``method="iterative"`` still produces m draws with no observed cell moved;
* an unknown method raises ValueError instead of silently falling back;
* ``method="miceforest"`` either runs (package present) or raises ImportError with
  an actionable message (package absent) - and never falls back to sklearn;
* the ``mi-forest`` extra keeps ``lightgbm<4.7``: miceforest 6.0.5 (still the
  latest release) calls ``Booster.__inner_predict(0)``, and lightgbm 4.7 dropped
  that argument (miceforest issue #104, open). Unpinned, the pair raises TypeError
  on the first ``mice()`` call.

Run: python -B -m pytest modules/tests/test_mi_backend.py -q
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib  # type: ignore

import numpy as np
import pandas as pd
import pytest

from modules.utils.u_mi import MI_BACKENDS, z_impute_mi

HAS_MICEFOREST = importlib.util.find_spec("miceforest") is not None
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def frame() -> pd.DataFrame:
    rng = np.random.RandomState(20261102)
    n = 60
    x1 = rng.normal(50, 10, n)
    x2 = 2.0 * x1 + rng.normal(0, 5, n)
    x1[rng.rand(n) < 0.15] = np.nan
    x2[rng.rand(n) < 0.15] = np.nan
    return pd.DataFrame({"pid": [f"p{i}" for i in range(n)], "x1": x1, "x2": x2})


def _observed_unchanged(src: pd.DataFrame, draw: pd.DataFrame, cols) -> bool:
    observed = src[cols].notna()
    return np.array_equal(
        draw[cols].where(observed).to_numpy(),
        src[cols].where(observed).to_numpy(),
        equal_nan=True,
    )


def test_default_backend_is_iterative() -> None:
    assert MI_BACKENDS[0] == "iterative"
    assert "miceforest" in MI_BACKENDS


def test_iterative_backend_still_works(frame: pd.DataFrame) -> None:
    draws = z_impute_mi(frame, m=3, method="iterative", verbose=False)
    assert len(draws) == 3
    for draw in draws:
        assert draw["x1"].isna().sum() == 0
        assert draw["x2"].isna().sum() == 0
        assert _observed_unchanged(frame, draw, ["x1", "x2"])
    # draws differ from each other: the posterior is sampled, not the mean
    assert not np.allclose(draws[0]["x1"].to_numpy(), draws[1]["x1"].to_numpy())


def test_unknown_method_raises(frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="unknown MI method"):
        z_impute_mi(frame, m=3, method="mean", verbose=False)


def test_miceforest_is_opt_in_and_never_falls_back(frame: pd.DataFrame) -> None:
    if HAS_MICEFOREST:
        draws = z_impute_mi(frame, m=3, method="miceforest", verbose=False)
        assert len(draws) == 3
        for draw in draws:
            assert draw["x1"].isna().sum() == 0
            assert _observed_unchanged(frame, draw, ["x1", "x2"])
    else:
        with pytest.raises(ImportError, match="miceforest"):
            z_impute_mi(frame, m=3, method="miceforest", verbose=False)


def test_mi_forest_extra_pins_lightgbm_below_47() -> None:
    """The extra must not let pip install the pair that raises TypeError."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["mi-forest"]
    pins = [dep.replace(" ", "") for dep in extra]
    assert any(dep.startswith("miceforest") for dep in pins), pins
    assert any(dep.startswith("lightgbm") and "<4.7" in dep for dep in pins), pins
