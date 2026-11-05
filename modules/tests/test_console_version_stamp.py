"""控制台版本台账：ini [console] 段能写回保留、旧 ini 兼容、不进配置卡。

运行: PYTHONPATH=<stats_root> python -m pytest modules/tests/test_console_version_stamp.py -q
"""
from __future__ import annotations

import types
from pathlib import Path

from modules.config.ini_io import INI_SECTIONS, read_ini, write_ini
from modules.console.const import SKIP_SETTING_KEYS

CONSOLE_KEYS = (
    "CONSOLE_GOLD_COMMIT",
    "CONSOLE_GOLD_SHA12",
    "CONSOLE_PAGE_SHA12",
    "CONSOLE_SYNCED_AT",
)
STAMP = {
    "CONSOLE_GOLD_COMMIT": "c2b38fa",
    "CONSOLE_GOLD_SHA12": "0123456789ab",
    "CONSOLE_PAGE_SHA12": "ba9876543210",
    "CONSOLE_SYNCED_AT": "2026-11-05T19:10:00+08:00",
}


def test_console_section_registered_first():
    """[console] 排最前：打开 ini 第一段就是版本戳，便于对照。"""
    sid, _title, keys = INI_SECTIONS[0]
    assert sid == "console"
    assert tuple(keys) == CONSOLE_KEYS


def test_write_ini_keeps_console_block_at_top(tmp_path: Path):
    """在册的 4 个键写盘后落在 [console] 段，且能读回。"""
    p = tmp_path / "settings.ini"
    write_ini(p, {**STAMP, "SEED": 66666})
    text = p.read_text(encoding="utf-8")
    assert text.split("\n\n")[1].startswith("[console]")
    for key in CONSOLE_KEYS:
        assert f"{key} = " in text
    assert read_ini(p)["CONSOLE_GOLD_COMMIT"] == "c2b38fa"


def test_old_ini_without_console_keys_writes_no_section(tmp_path: Path):
    """没戳过的旧 ini 不会被塞出空 [console] 段。"""
    p = tmp_path / "settings.ini"
    write_ini(p, {"SEED": 1})
    text = p.read_text(encoding="utf-8")
    assert "[console]" not in text
    assert "CONSOLE_" not in text


def test_writeback_settings_keeps_console_keys(monkeypatch, tmp_path: Path):
    """控制台「保存配置」整篇重写 ini 时，4 个台账键必须仍在。"""
    from modules.console import settings_layer

    monkeypatch.setattr(settings_layer, "GROK_ROOT", tmp_path)
    p = tmp_path / "ref" / "settings.ini"
    p.parent.mkdir(parents=True)
    write_ini(p, {**STAMP, "SEED": 66666})

    res = settings_layer.writeback_settings(p, {"SEED": 999})
    assert "SEED" in res["updated"]

    back = read_ini(p)
    assert back["SEED"] == 999
    for key in CONSOLE_KEYS:
        assert back[key] == STAMP[key]


def test_console_keys_never_reach_the_schema():
    """台账键不进 public_settings，配置卡不会长出 4 个裸文本框。"""
    from modules.console.schema_ui import public_settings

    mod = types.ModuleType("settings")
    mod.SEED = 66666
    for key in CONSOLE_KEYS:
        setattr(mod, key, "x")

    out = public_settings(mod)
    assert "SEED" in out
    for key in CONSOLE_KEYS:
        assert key in SKIP_SETTING_KEYS
        assert key not in out
