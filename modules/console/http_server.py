"""Bind console's HTTP Handler into the console module namespace.

``http_impl.py`` holds the real ``Handler`` / ``ConsoleServer`` classes.  Two
paths feed its free names: stateless leaf helpers are plain-imported from
``modules.console.ops`` (so they are skipped here via ``_OPS_OWNED``), while the
names that read console's mutable state (``_RUN``, ``GROK_ROOT``,
``_CONSOLE_TOKEN``, ``main``) are injected by ``console.py`` calling
``install(globals())`` once its helpers exist.
"""
from __future__ import annotations

from typing import Any

# 由 modules.console.ops 静态 import 的叶层符号：注入时跳过，保证「真 import」可证伪。
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


def install(ns: dict[str, Any]) -> None:
    """Inject console globals into http_impl, then export Handler symbols to ns."""
    from modules.console import http_impl as impl

    skip = {
        "__name__", "__file__", "__package__", "__doc__", "__loader__",
        "__spec__", "__builtins__", "__cached__",
        # 叶层已由 http_impl 静态 import（modules.console.ops），注入不得覆盖。
        *_OPS_OWNED,
    }
    # Keep the live globals dict so later assignments (_HTTPD, _SERVE_PORT)
    # remain visible to http_impl without re-install.
    impl._NS = ns
    for key, val in ns.items():
        if key in skip:
            continue
        if key in ("Handler", "ConsoleServer", "MIME", "_json_bytes"):
            continue
        setattr(impl, key, val)

    ns["Handler"] = impl.Handler
    ns["ConsoleServer"] = impl.ConsoleServer
    ns["MIME"] = impl.MIME
    ns["_json_bytes"] = impl._json_bytes
    ns["_read_json_body"] = impl._read_json_body
    ns["_merged_from_request"] = impl._merged_from_request
