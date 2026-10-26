"""控制台注入契约：_INJECTED / 静态视图 / ops 叶层三方必须对齐。"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CONSOLE_DIR = Path(__file__).resolve().parents[1] / "console"


def _imported(source: str, module: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            return {alias.name for alias in node.names}
    raise AssertionError(f"缺 from {module} import ...")


def _http_impl_source() -> str:
    return (CONSOLE_DIR / "http_impl.py").read_text(encoding="utf-8-sig")


def test_injected_matches_type_checking_view():
    from modules.console import http_impl

    assert set(http_impl._INJECTED) == _imported(
        _http_impl_source(), "modules.console.console"
    )


def test_ops_owned_matches_real_imports():
    from modules.console import http_server, ops

    imported = _imported(_http_impl_source(), "modules.console.ops")
    assert set(http_server._OPS_OWNED) == imported
    for name in sorted(imported):
        assert hasattr(ops, name), f"ops 缺 {name}"


def test_injected_and_ops_do_not_overlap():
    from modules.console import http_impl, http_server

    assert set(http_impl._INJECTED).isdisjoint(http_server._OPS_OWNED)


def test_install_binds_every_injected_name():
    from modules.console import console, http_impl

    for name in sorted(http_impl._INJECTED):
        assert hasattr(http_impl, name), f"未注入 {name}"
        assert getattr(http_impl, name) is getattr(console, name), name


def test_missing_injection_is_loud():
    from modules.console import http_impl

    name = "spawn_successor"
    original = getattr(http_impl, name)
    delattr(http_impl, name)
    try:
        with pytest.raises(RuntimeError, match=name):
            getattr(http_impl, name)
    finally:
        setattr(http_impl, name, original)


def test_unknown_attribute_still_raises_attribute_error():
    from modules.console import http_impl

    with pytest.raises(AttributeError):
        http_impl.no_such_console_symbol


def test_const_layer_is_static_not_injected():
    from modules.console import const, http_impl

    imported = _imported(_http_impl_source(), "modules.console.const")
    assert imported, "http_impl 未静态 import 常量层"
    assert imported.isdisjoint(http_impl._INJECTED)
    for name in sorted(imported):
        assert getattr(http_impl, name) is getattr(const, name), name


def test_mutable_state_has_one_home():
    from modules.console import console, http_impl, state

    assert console._RUN is state.RUN
    assert console._RUN_LOCK is state.RUN_LOCK
    assert console._PREVIEW_CACHE is state.PREVIEW_CACHE
    assert console._CONSOLE_TOKEN is state.CONSOLE_TOKEN
    assert "_CONSOLE_TOKEN" not in http_impl._INJECTED
    assert http_impl.CONSOLE_TOKEN is state.CONSOLE_TOKEN


