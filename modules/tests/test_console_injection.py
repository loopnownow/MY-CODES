"""控制台零注入契约：http_impl 只做真 import，无 _INJECTED / __getattr__ / http_server 垫片。"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

CONSOLE_DIR = Path(__file__).resolve().parents[1] / "console"
# B4c1–B4c6 从 console.py 搬出的模块：谁都不许反向 import console。
MOVED_MODULES = ("schema_ui", "settings_layer", "preview", "dryrun", "run", "serve")


def _imported(source: str, module: str) -> set[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            return {alias.name for alias in node.names}
    raise AssertionError(f"缺 from {module} import ...")


def _http_impl_source() -> str:
    return (CONSOLE_DIR / "http_impl.py").read_text(encoding="utf-8-sig")


def test_http_impl_has_no_injection_machinery():
    from modules.console import http_impl

    assert not hasattr(http_impl, "_INJECTED")
    assert not hasattr(http_impl, "__getattr__")
    assert "from modules.console.console import" not in _http_impl_source()


def test_http_server_shim_is_gone():
    assert importlib.util.find_spec("modules.console.http_server") is None
    assert not (CONSOLE_DIR / "http_server.py").exists()


def test_ops_owned_matches_real_imports():
    from modules.console import http_impl, ops

    imported = _imported(_http_impl_source(), "modules.console.ops")
    assert set(http_impl._OPS_OWNED) == imported
    for name in sorted(imported):
        assert hasattr(ops, name), f"ops 缺 {name}"


def test_const_layer_is_static_not_injected():
    from modules.console import const, http_impl

    imported = _imported(_http_impl_source(), "modules.console.const")
    assert imported, "http_impl 未静态 import 常量层"
    for name in sorted(imported):
        assert getattr(http_impl, name) is getattr(const, name), name


def test_console_imports_handler_from_http_impl():
    from modules.console import console, http_impl

    assert console.Handler is http_impl.Handler
    assert console.ConsoleServer is http_impl.ConsoleServer


def test_moved_modules_never_import_console():
    for name in MOVED_MODULES:
        src = (CONSOLE_DIR / f"{name}.py").read_text(encoding="utf-8-sig")
        assert "from modules.console.console import" not in src, name
        assert "modules.console import console" not in src, name


def test_unknown_attribute_raises_attribute_error():
    from modules.console import http_impl

    with pytest.raises(AttributeError):
        http_impl.console_has_no_such_symbol


def test_mutable_state_has_one_home():
    from modules.console import console, http_impl, state

    assert console._RUN is state.RUN
    assert console._RUN_LOCK is state.RUN_LOCK
    assert console._PREVIEW_CACHE is state.PREVIEW_CACHE
    assert console._CONSOLE_TOKEN is state.CONSOLE_TOKEN
    assert http_impl.CONSOLE_TOKEN is state.CONSOLE_TOKEN
    assert http_impl._state is state
