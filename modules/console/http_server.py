"""Bind console's HTTP Handler into the console module namespace.

``http_impl.py`` holds the real ``Handler`` / ``ConsoleServer`` classes and owns
``_INJECTED``: the exact set of console names it needs.  Stateless leaf helpers
are plain-imported from ``modules.console.ops`` (``_OPS_OWNED`` below mirrors
that import so the contract stays falsifiable); ``install()`` injects only the
whitelisted names and nothing else from console's globals.
"""
from __future__ import annotations

from typing import Any

# 由 modules.console.ops 静态 import 的叶层符号：注入时不得覆盖，保证「真 import」可证伪。
_OPS_OWNED = frozenset({
    "_overlay_path",
    "allow_browser_origin",
    "clear_project_overlay",
    "jsonable",
    "project_settings",
    "resolve_meta_xlsx",
    "resolve_run_outcome",
    "sync_external_tests",
    "write_project_overlay",
})

# install() 完成后回导到 console 命名空间的符号。
_EXPORTED = (
    "Handler",
    "ConsoleServer",
    "MIME",
    "_json_bytes",
    "_read_json_body",
    "_merged_from_request",
)


def install(ns: dict[str, Any]) -> None:
    """按 http_impl._INJECTED 注入 console 全局，再把 Handler 符号回导到 ns。"""
    from modules.console import http_impl as impl

    missing = sorted(name for name in impl._INJECTED if name not in ns)
    if missing:
        raise KeyError("console 缺少 http_impl 依赖的名字: " + ", ".join(missing))
    clash = sorted(impl._INJECTED & _OPS_OWNED)
    if clash:
        raise RuntimeError("叶层符号被重复声明为注入名: " + ", ".join(clash))
    # 保留活字典：install 之后 console 侧的赋值（_HTTPD / _SERVE_PORT）仍可见。
    impl._NS = ns
    for name in impl._INJECTED:
        setattr(impl, name, ns[name])
    for name in _EXPORTED:
        ns[name] = getattr(impl, name)
