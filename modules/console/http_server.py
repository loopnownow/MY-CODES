"""Bind console HTTP Handler into the console module namespace.

``http_impl.py`` holds the real ``Handler`` / ``ConsoleServer`` classes.
``console.py`` calls ``install(globals())`` after helpers are defined so
free names resolve inside ``http_impl`` without ``exec``.
"""
from __future__ import annotations

import sys
from types import ModuleType
from typing import Any


def install(ns: dict[str, Any]) -> None:
    """Inject console globals into http_impl, then export Handler symbols to ns."""
    from modules.console import http_impl as impl

    skip = {
        "__name__", "__file__", "__package__", "__doc__", "__loader__",
        "__spec__", "__builtins__", "__cached__",
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
