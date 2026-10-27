"""R-11/12 + C-03: imputation naming / outcome stratify; project-scoped paths."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from modules.console import settings_layer
from modules.utils.u_impute import _normalize_method, z_impute


def test_mice_alias_is_iterative():
    assert _normalize_method("mice") == "iterative"
    assert _normalize_method("iterative") == "iterative"


def test_implicit_group_not_used_for_stratify_when_outcome_like():
    """No `group_col` passed + a `Group` column: do not stratify on it.

    The conventional `Group` column is usually the outcome, and stratifying on an
    outcome writes `y` into the predictors. Nobody asked for a stratum here, so the
    safe default is one pool.
    """
    rng = np.random.RandomState(4)
    n = 40
    y = np.array([0] * 20 + [1] * 20)
    df = pd.DataFrame({
        "Group": y,
        "x": rng.normal(0, 1, n),
    })
    df.loc[0:3, "x"] = np.nan
    out = z_impute(df, method="median", verbose=False)
    assert out is not None
    assert "_impute_pool" not in out.columns
    assert out["x"].isna().sum() == 0


def test_explicit_group_is_always_honoured():
    """An explicitly named column stratifies, even when it is called `Group`.

    The caller knows their data; second-guessing an explicit argument silently is
    how a table ends up with numbers nobody can reproduce.
    """
    rng = np.random.RandomState(7)
    frame = pd.DataFrame({
        "Group": np.repeat(["low", "high"], 20),
        "x": np.concatenate([rng.normal(10, 1, 20), rng.normal(30, 1, 20)]),
    })
    frame.loc[[0, 20], "x"] = np.nan
    out = z_impute(frame, group_col="Group", method="median", verbose=False)
    low_fill = float(out.loc[0, "x"])
    high_fill = float(out.loc[20, "x"])
    # Stratified fills sit inside their own group, roughly 20 apart.
    assert low_fill < 15
    assert high_fill > 25


def test_group_none_means_one_pool():
    rng = np.random.RandomState(8)
    frame = pd.DataFrame({
        "Group": np.repeat(["a", "b"], 20),
        "x": np.concatenate([rng.normal(10, 1, 20), rng.normal(30, 1, 20)]),
    })
    frame.loc[[0, 20], "x"] = np.nan
    out = z_impute(frame, group_col=None, method="median", verbose=False)
    # One pool: both fills are the same overall median.
    assert float(out.loc[0, "x"]) == float(out.loc[20, "x"])


def test_outcome_cols_releases_a_cohort_named_group():
    """`Group` as a cohort label is fine once the real outcome is named."""
    rng = np.random.RandomState(9)
    frame = pd.DataFrame({
        "Group": np.repeat(["HC", "SSD"], 20),
        "Outcome": rng.randint(0, 2, 40),
        "x": np.concatenate([rng.normal(10, 1, 20), rng.normal(30, 1, 20)]),
    })
    frame.loc[[0, 20], "x"] = np.nan
    out = z_impute(frame, method="median", verbose=False, outcome_cols=["Outcome"])
    assert float(out.loc[0, "x"]) < 15
    assert float(out.loc[20, "x"]) > 25


def test_missing_explicit_group_column_raises():
    frame = pd.DataFrame({"Group": ["a", "b"], "x": [1.0, np.nan]})
    with pytest.raises(ValueError, match="not found"):
        z_impute(frame, group_col="Cohort", method="median", verbose=False)


def test_empty_corr_refs_not_filled_from_legacy():
    from modules.console.console import hydrate_console_settings

    out = hydrate_console_settings({
        "CORR_REFS": [],
        "CORR_E_REF": "Age",
        "CORR_F_REF": "Group",
        "DO_NOMOGRAM": True,
    })
    assert out["CORR_REFS"] == []
    assert out["DO_COMBINED_MODEL"] is True


def test_allow_file_origin_for_start_page():
    from modules.console.console import allow_browser_origin

    assert allow_browser_origin("") is True
    assert allow_browser_origin("null") is True
    assert allow_browser_origin("http://127.0.0.1:8765") is True
    assert allow_browser_origin("http://localhost:8765") is True
    assert allow_browser_origin("https://example.com") is False


def test_extra_project_roots_current_stages():
    from modules.console import console as cons

    names = {p.name.lower() for p in cons.EXTRA_PROJECT_ROOTS}
    assert names == {"pre-submit", "in-review", "accepted"}
    assert cons.RAD_ROOT.name == "drafting"


def test_project_dir_explicit_dir(tmp_path, monkeypatch):
    from modules.console import console as cons

    monkeypatch.setattr(settings_layer, "GROK_ROOT", tmp_path)
    in_review = tmp_path / "in-review" / "tyt_DSI"
    in_review.mkdir(parents=True)
    (in_review / "tyt_DSI.xlsx").write_bytes(b"xx")
    got = cons.project_dir("tyt_DSI", str(in_review))
    assert got == in_review.resolve()


def test_fig_palettes_ten_named_plus_custom():
    from modules.config.style import FIG_PALETTES
    from modules.console.console import build_schema

    named = [k for k in FIG_PALETTES if k != "custom"]
    assert len(named) >= 10
    for pid in named:
        pal = FIG_PALETTES[pid]
        assert len(pal["main"]) == 5
        assert len(pal["aux"]) == 5
    by = {f["key"]: f for f in build_schema({})}
    opts = by["FIG_PALETTE"]["options"]
    assert "custom" in opts
    assert len([x for x in opts if x != "custom"]) >= 10


def test_fig_palettes_twenty_match_schema_js_and_options():
    import json
    import re

    from modules.config.style import FIG_PALETTES
    from modules.console.console import build_schema

    new_ids = ["d3", "tableau", "set1", "dark2", "paired", "tolbright", "frontiers", "bmj", "uchicago", "gray"]
    assert len(FIG_PALETTES) == 20
    assert list(FIG_PALETTES)[:10] == ["npg", "okabe", "tol", "radiology", "ibm", "aaas", "nejm", "lancet", "jama", "jco"]
    assert list(FIG_PALETTES)[10:] == new_ids
    js = (Path(__file__).resolve().parents[1] / "console" / "console_schema.js").read_text(encoding="utf-8")
    order = re.search(r"const PAL_ORDER = (\[.*?\]);", js, re.S).group(1)
    order = json.loads(re.sub(r",\s*\]", "]", order))
    assert order == list(FIG_PALETTES)
    for pid, pal in FIG_PALETTES.items():
        block = re.search(rf"\n  {pid}: \{{(.*?)\n  \}},", js, re.S).group(1)
        main = json.loads(re.search(r"main: (\[.*?\])", block).group(1))
        aux = json.loads(re.search(r"aux: (\[.*?\])", block).group(1))
        assert main == pal["main"], pid
        assert aux == pal["aux"], pid
        assert len(set(main + aux)) == 10, pid
    by = {f["key"]: f for f in build_schema({})}
    assert by["FIG_PALETTE"]["options"] == list(FIG_PALETTES) + ["custom"]


def test_open_result_folder_project_only(tmp_path, monkeypatch):
    from modules.console import console as cons

    monkeypatch.setattr(settings_layer, "GROK_ROOT", tmp_path)
    proj = tmp_path / "in-review" / "tyt_DSI"
    proj.mkdir(parents=True)
    html = proj / "MCI" / "ResultsMCI.html"
    html.parent.mkdir()
    html.write_text("<html></html>", encoding="utf-8")
    opened = []
    monkeypatch.setattr(settings_layer, "project_dir", lambda name, explicit_dir="": proj.resolve())
    monkeypatch.setattr(cons.os, "startfile", lambda p: opened.append(p), raising=False)
    monkeypatch.setattr(cons.sys, "platform", "win32")
    info = cons.open_result_folder("tyt_DSI", "MCI/ResultsMCI.html")
    assert info["ok"] is True
    assert Path(info["folder"]) == html.parent.resolve()
    assert opened == [str(html.parent.resolve())]
    with pytest.raises(ValueError):
        cons.open_result_folder("tyt_DSI", "../other")
    with pytest.raises(FileNotFoundError):
        cons.open_result_folder("tyt_DSI", "missing.html")


def test_under_project_blocks_sibling_project(tmp_path, monkeypatch):
    from modules.console import console as cons

    monkeypatch.setattr(settings_layer, "GROK_ROOT", tmp_path)
    a = tmp_path / "proj_a"
    b = tmp_path / "proj_b"
    a.mkdir()
    b.mkdir()
    secret = b / "secret.xlsx"
    secret.write_bytes(b"xx")
    with pytest.raises(ValueError):
        cons.under_project(a, secret)
    ok = a / "data.xlsx"
    ok.write_bytes(b"yy")
    assert cons.under_project(a, "data.xlsx") == Path(ok).resolve()


def test_resolve_sheet_name_cln_clin_alias():
    from modules.console.console import resolve_sheet_name

    sheets = ["clin", "恶性", "良性"]
    assert resolve_sheet_name("clin", sheets) == "clin"
    assert resolve_sheet_name("cln", sheets) == "clin"
    assert resolve_sheet_name("CLIN", sheets) == "clin"
    assert resolve_sheet_name("rad", sheets) is None
    assert resolve_sheet_name("", sheets) is None


def test_nomogram_clinical_only(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    from modules.stats import models as M

    rng = np.random.RandomState(0)
    n = 40
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = (0.8 * x1 + 0.4 * x2 + rng.normal(0, 0.3, n) > 0).astype(int)
    clin = pd.DataFrame({"Age": x1, "Shape": x2})
    monkeypatch.chdir(tmp_path)
    res = M.build_nomogram(
        train_label=y,
        radscore_train=None,
        train_clin=clin,
        clin_feat=["Age", "Shape"],
    )
    assert "RadScore" not in res.features
    assert "Age" in res.features
    assert np.isfinite(res.train_auc)


def test_nomogram_uses_original_category_strings(tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    from modules.stats import data_io as dio
    from modules.stats import models as M

    dio.CLIN_LEVEL_MAPS = {
        "Layers Involvement": {
            0.0: "epidermis and dermis",
            1.0: "epidermis and dermis and subcutaneous",
            2.0: "epidermis",
        }
    }
    x = np.array([0, 1, 2] * 12, dtype=float)
    y = (x >= 1).astype(int)
    clin = pd.DataFrame({"Layers Involvement": x})
    monkeypatch.chdir(tmp_path)
    res = M.build_nomogram(
        train_label=y,
        radscore_train=None,
        train_clin=clin,
        clin_feat=["Layers Involvement"],
    )
    tab = res.points_table
    assert tab is not None
    labels = set(tab["Label"].astype(str))
    assert "epidermis and dermis" in labels
    assert "epidermis and dermis and subcutaneous" in labels
    assert "0" not in labels
    assert "1" not in labels
