"""ini_io 实验根判定：GROK_ROOT 优先，缺省回落 lab DEFAULT_GROK_ROOT。"""
from __future__ import annotations

from pathlib import Path

from modules.config.ini_io import _lab_grok_root, find_settings_ini

LAB_ROOT = Path(r"D:\0Grok")


def _make_project(root: Path) -> Path:
    proj = root / "demo_proj"
    (proj / "ref").mkdir(parents=True, exist_ok=True)
    ini = proj / "ref" / "settings.ini"
    ini.write_text("[RAD]\nproject = demo\n", encoding="utf-8")
    return ini


def test_default_root_is_lab_grok(monkeypatch):
    monkeypatch.delenv("GROK_ROOT", raising=False)
    assert _lab_grok_root() == LAB_ROOT


def test_blank_env_falls_back(monkeypatch):
    monkeypatch.setenv("GROK_ROOT", "   ")
    assert _lab_grok_root() == LAB_ROOT


def test_env_overrides_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GROK_ROOT", str(tmp_path))
    assert _lab_grok_root() == tmp_path


def test_tilde_is_expanded(monkeypatch):
    monkeypatch.setenv("GROK_ROOT", "~/grok-alias")
    got = _lab_grok_root()
    assert "~" not in str(got)
    assert got.is_absolute()


def test_project_outside_root_is_ignored(tmp_path, monkeypatch):
    monkeypatch.delenv("GROK_ROOT", raising=False)
    monkeypatch.delenv("RAD_PROJECT", raising=False)
    ini = _make_project(tmp_path)
    assert find_settings_ini(ini.parent.parent) is None


def test_project_inside_root_is_found(tmp_path, monkeypatch):
    monkeypatch.delenv("RAD_PROJECT", raising=False)
    monkeypatch.setenv("GROK_ROOT", str(tmp_path))
    ini = _make_project(tmp_path)
    assert find_settings_ini(ini.parent.parent) == ini


def test_rad_project_bypasses_root_check(tmp_path, monkeypatch):
    monkeypatch.delenv("GROK_ROOT", raising=False)
    ini = _make_project(tmp_path)
    monkeypatch.setenv("RAD_PROJECT", str(ini.parent.parent))
    assert find_settings_ini(LAB_ROOT) == ini
