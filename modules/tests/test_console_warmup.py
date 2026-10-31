"""控制台启动预热（warmup）行为。

运行: pytest modules/tests/test_console_warmup.py
"""
from __future__ import annotations

from modules.console import warmup


def _reset():
    warmup.STATE.update({"done": False, "error": "", "detail": ""})


def test_warmup_imports_only():
    _reset()
    warmup.run(None, "")
    assert warmup.STATE["done"] is True
    assert warmup.STATE["error"] == ""
    assert "imports" in warmup.STATE["detail"]


def test_warmup_reports_bad_project_without_raising():
    _reset()
    warmup.run("__no_such_project__", "")
    assert warmup.STATE["done"] is True
    assert warmup.STATE["error"] != ""
