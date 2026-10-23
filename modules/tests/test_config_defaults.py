"""C-01: modules.stats imports without a project settings.ini."""
from __future__ import annotations

import sys
from contextlib import contextmanager


@contextmanager
def _fresh_modules_under(tmp_path, monkeypatch):
    """Reload modules.* in an empty cwd, then restore the pre-test package graph.

    Popping without restore leaves later tests holding stale imports (e.g. report.cfg)
    while monkeypatch targets the newly loaded config object.
    """
    monkeypatch.delenv("RAD_PROJECT", raising=False)
    monkeypatch.chdir(tmp_path)
    saved = {
        k: sys.modules.pop(k)
        for k in list(sys.modules)
        if k == "modules" or k.startswith("modules.")
    }
    try:
        yield
    finally:
        for k in [k for k in list(sys.modules) if k == "modules" or k.startswith("modules.")]:
            sys.modules.pop(k, None)
        sys.modules.update(saved)


def test_config_has_group_col_keyword_without_ini(tmp_path, monkeypatch):
    with _fresh_modules_under(tmp_path, monkeypatch):
        from modules import config as cfg
        from modules.config.defaults import DEFAULT_SETTINGS

        assert getattr(cfg, "GROUP_COL_KEYWORD", None) == "Group"
        assert getattr(cfg, "SEED", None) == DEFAULT_SETTINGS["SEED"]
        assert getattr(cfg, "RAD_COR_CUTOFF", None) == DEFAULT_SETTINGS["RAD_COR_CUTOFF"]
        assert DEFAULT_SETTINGS["FIGURE_PANEL_LABEL_POS"] == "lower_left"


def test_stats_modules_import_without_ini(tmp_path, monkeypatch):
    with _fresh_modules_under(tmp_path, monkeypatch):
        from modules.stats import data_io, radiomics, clinical, nri_cir  # noqa: F401

        assert callable(data_io._find_group_column)
        assert data_io._find_group_column(["Patient_ID", "Group"]) == "Group"
