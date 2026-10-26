"""console 的运行期可变状态（唯一之家）。

这里只放运行过程中会被改写的全局，不放常量（常量在 const.py）、不放函数。
放在独立模块是因为 http_impl 也要读同一份状态，而 console 导入 http_impl；
状态留在 console 就只能靠注入或活字典兜圈。

运行: 由 console.py 导入，不单独执行。
"""
from __future__ import annotations

import secrets
import threading
from typing import Any

# ── HTTP 服务 ─────────────────────────────────────────────────
HTTPD: Any = None
SERVE_PORT: int | None = None
CONSOLE_TOKEN: str = secrets.token_urlsafe(24)

# ── 流水线运行 ────────────────────────────────────────────────
RUN_LOCK = threading.Lock()
RUN: dict[str, Any] = {
    "status": "idle",
    "pid": None,
    "proc": None,
    "log_path": "",
    "started": None,
    "ended": None,
    "error": "",
    "project": "",
    "dry_run": False,
}

# ── 预览缓存 ──────────────────────────────────────────────────
PREVIEW_CACHE: dict[tuple, dict[str, Any]] = {}
