"""LASSO cache: hit reads, miss fits and writes, switch defaults on."""
from __future__ import annotations

import numpy as np
import pandas as pd

from modules.config.defaults import DEFAULT_SETTINGS


def _xy():
    rng = np.random.RandomState(0)
    n = 40
    y = np.array([0] * 20 + [1] * 20)
    signal = rng.normal(0, 1, n)
    signal[y == 1] += 1.5
    X = pd.DataFrame({
        "a": signal,
        "b": rng.normal(0, 1, n),
        "c": rng.normal(0, 1, n),
    })
    return X, y


def test_lasso_cache_switch_defaults_on_and_sits_on_radprep_title():
    from pathlib import Path
    from modules.console.console import build_schema

    assert DEFAULT_SETTINGS["LASSO_CACHE"] is True
    by = {f["key"]: f for f in build_schema({})}
    assert by["LASSO_CACHE"]["widget"] == "bool"
    assert by["LASSO_CACHE"]["value"] is True
    assert by["LASSO_CACHE"]["section"] == "radprep"
    assert by["LASSO_CACHE"]["label"] == "LASSO 缓存"
    root = Path(__file__).resolve().parents[1]
    js = (root / "console" / "console_ui.js").read_text(encoding="utf-8")
    assert (
        'titleKeys = ["DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "LASSO_CACHE"]'
        in js
    )


def test_lasso_cache_hit_reads_and_miss_writes(tmp_path, monkeypatch, capsys):
    from modules import config as cfg
    from modules.stats import radiomics as rad

    monkeypatch.setattr(rad, "_lasso_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "LASSO_CACHE", True, raising=False)
    monkeypatch.setattr(cfg, "LASSO_C_MIN", 1e-2, raising=False)
    monkeypatch.setattr(cfg, "LASSO_C_MAX", 10.0, raising=False)
    monkeypatch.setattr(cfg, "LASSO_TOL", 1e-3, raising=False)
    monkeypatch.setattr(cfg, "LASSO_N_JOBS", 1, raising=False)

    n = {"k": 0}
    orig = rad._fold_preprocess_and_path

    def wrapped(*args, **kwargs):
        n["k"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(rad, "_fold_preprocess_and_path", wrapped)
    X, y = _xy()
    kw = dict(
        nfolds=2, n_cs=3, seed=1, max_iter=40, alpha=1.0, topk=0,
        cutoff=1.0, enable_corr=False,
    )

    n["k"] = 0
    first = rad._fit_lasso_cv_path(X, y, **kw)
    text = capsys.readouterr().out
    assert n["k"] == 2
    assert "LASSO cache write" in text
    saved = list(tmp_path.glob("*.joblib"))
    assert len(saved) == 1
    blob = saved[0].read_bytes()

    n["k"] = 0
    second = rad._fit_lasso_cv_path(X, y, **kw)
    text = capsys.readouterr().out
    assert n["k"] == 0
    assert "LASSO cache hit" in text
    assert np.array_equal(first[1], second[1])
    assert first[0] == second[0]
    assert list(first[5].columns) == list(second[5].columns)

    n["k"] = 0
    y2 = y.copy()
    y2[0] = 1
    rad._fit_lasso_cv_path(X, y2, **kw)
    text = capsys.readouterr().out
    assert n["k"] == 2
    assert "LASSO cache write" in text
    assert saved[0].read_bytes() == blob

    monkeypatch.setattr(cfg, "LASSO_CACHE", False, raising=False)
    n["k"] = 0
    third = rad._fit_lasso_cv_path(X, y, **kw)
    text = capsys.readouterr().out
    assert n["k"] == 2
    assert "LASSO cache hit" not in text
    assert saved[0].read_bytes() == blob
    assert np.array_equal(first[1], third[1])

    monkeypatch.setattr(cfg, "LASSO_CACHE", True, raising=False)
    saved[0].write_bytes(b"not-a-cache")
    n["k"] = 0
    fourth = rad._fit_lasso_cv_path(X, y, **kw)
    text = capsys.readouterr().out
    assert n["k"] == 2
    assert "unreadable" in text
    assert "LASSO cache write" in text
    assert np.array_equal(first[1], fourth[1])
