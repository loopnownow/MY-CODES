"""RAD console HTTP Handler / ConsoleServer (normal module).

Imported by console.py after helpers exist; console then injects the names in
``_INJECTED`` (functions that still read console's own state).  Constants come
from ``modules.console.const``, mutable run/serve state from
``modules.console.state`` and the schema tables from ``modules.console.schema``
— those three are real imports, not injection.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from modules.console.ops import (  # 叶层：真实 import，不再依赖注入
    _overlay_path,
    allow_browser_origin,
    clear_project_overlay,
    jsonable,
    project_settings,
    resolve_meta_xlsx,
    resolve_run_outcome,
    sync_external_tests,
    write_project_overlay,
)
# 常量层：真实 import（与 modules.console.const 同源，不经注入）
from modules.console.const import (  # noqa: F401
    CONSOLE_DEFAULTS,
    CSS_PATH,
    DEFAULT_PROJECT,
    HTML_PATH,
    JS_ASSETS,
    MODULES_ROOT,
    OVERRIDE_PATH,
    PORT_HI,
    PORT_LO,
    RAD_ROOT,
    STROBE_PATH,
    _TOKEN_SAFE_GET,
)
# 运行期状态：与 console 同一份对象（不进注入表）
from modules.console import state as _state
from modules.console.state import CONSOLE_TOKEN
# UI schema 层：真 import（B4c1 起从 console 搬出，不进注入表）
from modules.console.schema_ui import (
    apply_label_col_suggestion,
    build_schema,
    hydrate_console_settings,
    public_settings,
)
# 设置/路径/会话层：真 import（B4c2 起从 console 搬出，不进注入表）
from modules.console.settings_layer import (
    discover_projects,
    find_results,
    load_preferred_port,
    load_session,
    load_settings_module,
    normalize_overlay,
    open_result_folder,
    pick_file_dialog,
    project_dir,
    read_project_overlay,
    save_preferred_port,
    save_session,
    under_project,
    unwrap_overlay_file,
    writeback_settings,
)
# 工作簿预览层：真 import（B4c3 起从 console 搬出，不进注入表）
from modules.console.preview import (
    exclude_select_options,
    preview_workbook,
    xlsx_meta,
)
# dry-run 预览层：真 import（B4c4 起从 console 搬出，不进注入表）
from modules.console.dryrun import dry_run_text
# 运行层：真 import（B4c5 起从 console 搬出，不进注入表）
from modules.console.run import (
    read_log,
    run_project_data_qc,
    run_status,
    start_run,
    start_run_bat,
    stop_run,
)

if TYPE_CHECKING:  # 静态视图；运行期由 http_server.install(console_globals) 注入
    from modules.console.console import (
        spawn_successor,
    )

# 注入契约（SSOT）：http_impl 仍需要 console 提供的自由名。
# 常量层（modules.console.const）与运行期状态（modules.console.state）都是真 import，
# 这里只剩仍住在 console 的函数。
# http_server.install() 只按这张表注入；缺一个即 KeyError，其余全局一概不进。
_INJECTED: frozenset[str] = frozenset({
    # 读 / 写 console 可变状态的函数
    "spawn_successor",
})

def __getattr__(name: str) -> Any:
    """未注入就报错：比裸 NameError 多一句「谁没注入」。"""
    if name in _INJECTED:
        raise RuntimeError(
            f"http_impl 未注入 modules.console.console.{name}；"
            "检查 modules.console.http_server.install(console_globals)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _require_project(value: Any) -> str:
    """页面必须显式给 project；缺了直接报错，不回退 DEFAULT_PROJECT。"""
    name = str(value or "").strip()
    if not name:
        raise ValueError("未指定 project")
    return name


# ── HTTP ───────────────────────────────────────────────────────
def _json_bytes(obj: Any, code: int = 200) -> tuple[int, bytes, str]:
    raw = json.dumps(obj, ensure_ascii=False, indent=None, default=str).encode("utf-8")
    return code, raw, "application/json; charset=utf-8"


MIME = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    n = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(n) if n else b"{}"
    if not raw:
        return {}
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("JSON body 必须是对象")
    return obj


def _merged_from_request(body: dict[str, Any]) -> tuple[str, Path, Path, dict, dict]:
    project = _require_project(body.get("project"))
    proj = project_dir(project)
    settings_raw = str(body.get("settings") or "").strip()
    settings_path = Path(settings_raw) if settings_raw else project_settings(proj)
    if not settings_path.is_absolute():
        settings_path = proj / settings_path
    settings_path = under_project(proj, settings_path)
    data_raw = str(body.get("data") or "").strip()
    if not data_raw:
        raise ValueError("未指定 data")
    data = Path(data_raw)
    if not data.is_absolute():
        data = proj / data
    data = under_project(proj, data)
    if not data.is_file():
        raise FileNotFoundError(f"数据文件不存在: {data}")
    mod = load_settings_module(settings_path)
    settings = public_settings(mod)
    overlay = normalize_overlay(dict(body.get("overlay") or {}))
    return project, data, settings_path, settings, overlay


class ConsoleServer(ThreadingHTTPServer):
    # Windows 上 SO_REUSEADDR 会让多个进程同时 LISTEN 同一端口，Firefox 收到空响应
    allow_reuse_address = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "console/1.0"
    protocol_version = "HTTP/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors_headers(self) -> dict[str, str]:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin or not allow_browser_origin(origin):
            return {}
        return {
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Console-Token",
            "Access-Control-Allow-Private-Network": "true",
        }

    def _send(self, code: int, payload: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        hdrs = dict(self._cors_headers())
        if extra:
            hdrs.update(extra)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _err(self, code: int, msg: str) -> None:
        c, b, t = _json_bytes({"ok": False, "error": msg}, code)
        self._send(c, b, t)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._do_GET()
        except ValueError as e:
            self._err(400, str(e))
        except Exception as e:
            traceback.print_exc()
            self._err(500, str(e))

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._do_POST()
        except Exception as e:
            traceback.print_exc()
            self._err(400, str(e))

    def do_OPTIONS(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin") or ""
        self.send_response(204)
        if allow_browser_origin(origin):
            acao = origin.strip() or "*"
            self.send_header("Access-Control-Allow-Origin", acao)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Console-Token, Access-Control-Request-Private-Network")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._do_DELETE()
        except Exception as e:
            traceback.print_exc()
            self._err(400, str(e))

    def _origin_ok(self) -> bool:
        return allow_browser_origin(self.headers.get("Origin") or "")

    def _token_ok(self) -> bool:
        got = self.headers.get("X-Console-Token") or ""
        if not got:
            q = parse_qs(urlparse(self.path).query)
            got = (q.get("token") or [""])[0]
        if not got or not secrets.compare_digest(str(got), str(CONSOLE_TOKEN)):
            self._err(403, "invalid console token")
            return False
        return True

    def _guard(self, path: str, mutating: bool) -> bool:
        if not self._origin_ok():
            self._err(403, "origin not allowed")
            return False
        if mutating:
            return self._token_ok()
        if path.startswith("/api/") and path not in _TOKEN_SAFE_GET:
            return self._token_ok()
        return True

    def _do_GET(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        qs = {k: v[0] if v else "" for k, v in parse_qs(u.query, keep_blank_values=True).items()}
        if not self._guard(path, mutating=False):
            return

        if path in ("/", "/console.html", "/xlm_console.html"):
            if not HTML_PATH.is_file():
                self._err(404, f"缺少 {HTML_PATH}")
                return
            html = HTML_PATH.read_text(encoding="utf-8")
            if "__CONSOLE_TOKEN__" in html:
                html = html.replace("__CONSOLE_TOKEN__", CONSOLE_TOKEN)
            else:
                html = html.replace(
                    "<head>",
                    f'<head><meta name="console-token" content="{CONSOLE_TOKEN}">',
                    1,
                )
            # Cache-bust static assets so Ctrl+F5 is enough after edits
            asset_vers = {}
            if CSS_PATH.is_file():
                asset_vers["console.css"] = int(CSS_PATH.stat().st_mtime)
            for _jp in JS_ASSETS.values():
                if _jp.is_file():
                    asset_vers[_jp.name] = int(_jp.stat().st_mtime)
            for _name, _ver in asset_vers.items():
                html = html.replace(
                    f'href="{_name}"', f'href="{_name}?v={_ver}"'
                ).replace(
                    f'src="{_name}"', f'src="{_name}?v={_ver}"'
                )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/console.css":
            if not CSS_PATH.is_file():
                self._err(404, f"缺少 {CSS_PATH}")
                return
            self._send(200, CSS_PATH.read_bytes(), "text/css; charset=utf-8")
            return

        if path in JS_ASSETS:
            jp = JS_ASSETS[path]
            if not jp.is_file():
                self._err(404, f"缺少 {jp}")
                return
            self._send(
                200, jp.read_bytes(),
                "application/javascript; charset=utf-8",
            )
            return

        if path in ("/strobe", "/strobe-flowchart", "/strobe-flowchart/",
                    "/flow_chart.html", "/flow_chart"):
            project = qs.get("project") or ""
            gold = STROBE_PATH
            if not gold.is_file():
                self._err(404, f"缺少 {gold}")
                return
            html = gold.read_text(encoding="utf-8")
            if project:
                html = html.replace(
                    'const STORE = "strobe-flowchart-v2";',
                    f'const STORE = "strobe-flowchart-v2-{project}";',
                    1,
                )
                html = html.replace(
                    "<title>SCI Figure 1 · 研究流程图</title>",
                    f"<title>SCI Figure 1 · {project}</title>",
                    1,
                )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/api/health":
            # token 仅给本机 / file:// 起始页；跨站读不到（CORS 已收紧）
            payload = {
                "ok": True,
                "root": str(RAD_ROOT),
                "port": _state.SERVE_PORT or load_preferred_port(),
                "preferred": load_preferred_port(),
            }
            if self._origin_ok():
                payload["token"] = CONSOLE_TOKEN
            c, b, t = _json_bytes(payload)
            self._send(c, b, t)
            return

        if path == "/api/port":
            c, b, t = _json_bytes({
                "ok": True,
                "current": _state.SERVE_PORT,
                "preferred": load_preferred_port(),
                "lo": PORT_LO,
                "hi": PORT_HI,
            })
            self._send(c, b, t)
            return

        if path == "/api/shutdown":
            if not self._token_ok():
                return
            def _stop() -> None:
                httpd = _state.HTTPD
                if httpd is not None:
                    httpd.shutdown()
            threading.Timer(0.25, _stop).start()
            c, b, t = _json_bytes({"ok": True, "stopping": True})
            self._send(c, b, t)
            return

        if path == "/api/projects":
            c, b, t = _json_bytes({"ok": True, "projects": discover_projects(),
                                   "default_project": DEFAULT_PROJECT})
            self._send(c, b, t)
            return

        if path == "/api/session":
            ses = load_session()
            c, b, t = _json_bytes({"ok": True, "session": ses})
            self._send(c, b, t)
            return

        if path == "/api/settings":
            project = _require_project(qs.get("project"))
            proj = project_dir(project, qs.get("dir") or "")
            sp = Path(qs["settings"]) if qs.get("settings") else project_settings(proj)
            if not sp.is_absolute():
                sp = proj / sp
            sp = under_project(proj, sp)
            data_s = qs.get("data") or ""
            if sp.is_file():
                try:
                    mod = load_settings_module(sp)
                    settings = public_settings(mod)
                except Exception as e:
                    from modules.config.ini_io import keys_in_settings_error
                    c, b, t = _json_bytes({
                        "ok": False,
                        "error": str(e),
                        "bad_keys": keys_in_settings_error(str(e)),
                        "settings_path": str(sp),
                    }, 400)
                    self._send(c, b, t)
                    return
            else:
                settings = dict(CONSOLE_DEFAULTS)
                settings["INPUT"] = Path(data_s).name if data_s else f"{project}.xlsx"
                settings["MODULES_DIR"] = str(MODULES_ROOT)
                settings = hydrate_console_settings(settings)
            _qs_sheet = {
                "RAD_SHEET": "rad_sheet",
                "CLIN_SHEET": "clin_sheet",
                "QC_SHEET": "qc_sheet",
                "ICC_SHEET": "icc_sheet",
                "RAD_ID_COL": "rad_id",
                "CLIN_ID_COL": "clin_id",
            }
            for key, qn in _qs_sheet.items():
                val = (qs.get(qn) or "").strip()
                if val:
                    settings[key] = val
            if data_s:
                settings["INPUT"] = Path(data_s).name
            ov_file = _overlay_path(proj)
            if ov_file.is_file():
                try:
                    ov = unwrap_overlay_file(json.loads(ov_file.read_text(encoding="utf-8")))
                    if ov:
                        settings.update(ov)
                except Exception as e:
                    print(f"  ⚠ overlay JSON ignored: {e}", flush=True)
            raw_ext = (qs.get("ext_tests") or "").strip()
            if raw_ext:
                try:
                    parsed = json.loads(raw_ext)
                    if isinstance(parsed, list):
                        settings["EXTERNAL_TESTS"] = parsed
                except Exception as e:
                    print(f"  ⚠ ext_tests JSON ignored: {e}", flush=True)
            settings = sync_external_tests(hydrate_console_settings(settings))
            sheets: list[str] = []
            clin_cols: list[str] = []
            rad_id_cols: list[str] = []
            icc_cols: list[str] = []
            preview = None
            if data_s:
                try:
                    dp = Path(data_s)
                    if not dp.is_absolute():
                        dp = proj / dp
                    dp = under_project(proj, dp)
                    preview = preview_workbook(dp, settings)
                    sheets = preview.get("sheets") or []
                    clin_cols = preview.get("clin_cols") or []
                    rad_id_cols = preview.get("rad_id_cols") or []
                    icc_cols = preview.get("icc_cols") or []
                except Exception as e:
                    preview = {"error": str(e), "data": data_s}
            preview = preview or {}
            apply_label_col_suggestion(settings, clin_cols)
            # 起始页显式传入时盖过 overlay 和姓名列建议，空姓名列、空非特征列也生效。
            if "label_col" in qs:
                settings["LABEL_COL"] = (qs.get("label_col") or "").strip()
            if "rad_id_cols" in qs:
                raw_meta = (qs.get("rad_id_cols") or "").strip()
                if not raw_meta:
                    settings["RAD_ID_COLS"] = []
                else:
                    try:
                        parsed_meta = json.loads(raw_meta)
                    except Exception:
                        parsed_meta = None
                    if isinstance(parsed_meta, list):
                        settings["RAD_ID_COLS"] = [
                            str(x).strip() for x in parsed_meta if str(x).strip()
                        ]
            dp_opt = None
            if data_s:
                dp_opt = Path(data_s)
                if not dp_opt.is_absolute():
                    dp_opt = proj / data_s
            extra_sel = exclude_select_options(proj, settings, dp_opt, preview)
            schema = build_schema(
                settings, sheets, clin_cols, rad_id_cols, icc_cols,
                preview.get("group_levels") or [],
                preview.get("rad_meta_cols") or [],
                preview.get("id_options") or [],
                extra_sel,
                preview.get("measure_cols") or [],
                preview.get("rad_cols") or [],
            )
            c, b, t = _json_bytes({
                "ok": True,
                "project": project,
                "settings_path": str(sp),
                "values": jsonable(settings),
                "schema": schema,
                "preview": preview,
            })
            self._send(c, b, t)
            return

        if path == "/api/run/status":
            c, b, t = _json_bytes({"ok": True, **run_status()})
            self._send(c, b, t)
            return

        if path == "/api/run/log":
            off = int(qs.get("offset") or 0)
            c, b, t = _json_bytes({"ok": True, **read_log(off)})
            self._send(c, b, t)
            return

        if path == "/api/results":
            project = _require_project(qs.get("project"))
            c, b, t = _json_bytes({"ok": True, "results": find_results(project)})
            self._send(c, b, t)
            return

        if path == "/api/xlsx-meta":
            project = _require_project(qs.get("project"))
            proj = project_dir(project)
            file_s = str(qs.get("file") or "").strip()
            sheet_s = str(qs.get("sheet") or "").strip()
            col_s = str(qs.get("col") or "").strip()
            data_s = str(qs.get("data") or "").strip()
            dp = None
            if data_s:
                dp = Path(data_s)
                if not dp.is_absolute():
                    dp = proj / data_s
                dp = under_project(proj, dp)
            try:
                if file_s:
                    path_x = resolve_meta_xlsx(proj, file_s, dp)
                else:
                    from modules.stats.data_io import default_exclude_xlsx
                    sp = Path(qs["settings"]) if qs.get("settings") else project_settings(proj)
                    if not sp.is_absolute():
                        sp = proj / sp
                    try:
                        st = public_settings(load_settings_module(under_project(proj, sp)))
                    except Exception:
                        st = {}
                    rel = default_exclude_xlsx(st)
                    path_x = Path(rel)
                    if not path_x.is_absolute():
                        path_x = proj / rel
                path_x = under_project(proj, path_x)
                meta = xlsx_meta(path_x, sheet_s, col_s)
                meta["ok"] = True
            except Exception as e:
                meta = {
                    "ok": False, "error": str(e),
                    "sheets": [], "columns": [], "values": [],
                }
            c, b, t = _json_bytes(meta)
            self._send(c, b, t)
            return

        if path.startswith("/files/"):
            rel = path[len("/files/"):]
            # /files/<project>/...
            parts = rel.split("/", 1)
            name = unquote(parts[0])
            rest = unquote(parts[1]) if len(parts) > 1 else ""
            proj = project_dir(name)
            full = under_project(proj, rest) if rest else proj
            if not full.is_file():
                self._err(404, f"文件不存在: {rel}")
                return
            ctype = MIME.get(full.suffix.lower(), "application/octet-stream")
            data = full.read_bytes()
            extra = {}
            if full.suffix.lower() == ".html":
                extra["Content-Disposition"] = "inline"
            self._send(200, data, ctype, extra)
            return

        self._err(404, path)

    def _do_POST(self) -> None:
        u = urlparse(self.path)
        path = unquote(u.path)
        if not self._guard(path, mutating=True):
            return
        body = _read_json_body(self)

        if path == "/api/preview":
            project, data, settings_path, settings, overlay = _merged_from_request(body)
            merged = dict(settings)
            merged.update(overlay)
            preview = preview_workbook(data, merged)
            apply_label_col_suggestion(merged, preview.get("clin_cols") or [])
            proj = project_dir(project)
            extra_sel = exclude_select_options(proj, merged, Path(data), preview)
            schema = build_schema(
                merged,
                preview.get("sheets") or [],
                preview.get("clin_cols") or [],
                preview.get("rad_id_cols") or [],
                preview.get("icc_cols") or [],
                preview.get("group_levels") or [],
                preview.get("rad_meta_cols") or [],
                preview.get("id_options") or [],
                extra_sel,
                preview.get("measure_cols") or [],
                preview.get("rad_cols") or [],
            )
            c, b, t = _json_bytes({
                "ok": True, "project": project, "preview": preview, "schema": schema,
                "settings_path": str(settings_path),
            })
            self._send(c, b, t)
            return

        if path == "/api/qc":
            project, data, settings_path, settings, overlay = _merged_from_request(body)
            info = run_project_data_qc(project, data, settings, overlay)
            c, b, t = _json_bytes({"ok": True, **info})
            self._send(c, b, t)
            return

        if path == "/api/dryrun":
            project, data, settings_path, settings, overlay = _merged_from_request(body)
            run_outcome = resolve_run_outcome(body, overlay)
            merged = dict(settings)
            merged.update(overlay)
            preview = preview_workbook(data, merged)
            apply_label_col_suggestion(merged, preview.get("clin_cols") or [])
            if merged.get("LABEL_COL") != overlay.get("LABEL_COL"):
                overlay = dict(overlay)
                overlay["LABEL_COL"] = merged["LABEL_COL"]
            text = dry_run_text(
                project, data, settings_path, settings, overlay, preview,
                run_outcome=run_outcome,
            )
            save_session({
                "project": project,
                "data": str(data),
                "settings": str(settings_path),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "overlay": jsonable(normalize_overlay(overlay)),
            })
            c, b, t = _json_bytes({"ok": True, "text": text, "preview": preview})
            self._send(c, b, t)
            return

        if path == "/api/run":
            project, data, settings_path, settings, overlay = _merged_from_request(body)
            dry = bool(body.get("dry_run"))
            run_outcome = resolve_run_outcome(body, overlay)
            info = start_run(
                project, data, settings_path, overlay, dry,
                run_outcome=run_outcome,
            )
            c, b, t = _json_bytes({"ok": True, **info})
            self._send(c, b, t)
            return

        if path in ("/api/run/bat", "/api/run/runbat"):
            project = _require_project(body.get("project"))
            info = start_run_bat(project)
            c, b, t = _json_bytes({"ok": True, **info})
            self._send(c, b, t)
            return

        if path == "/api/run/stop":
            c, b, t = _json_bytes({"ok": True, **stop_run()})
            self._send(c, b, t)
            return

        if path == "/api/restart":
            try:
                stop_run()
            except Exception:
                pass
            port = _state.SERVE_PORT or load_preferred_port()
            project = str(body.get("project") or "").strip() or None
            spawn_successor(port, project)
            def _stop() -> None:
                httpd = _state.HTTPD
                if httpd is not None:
                    httpd.shutdown()
            threading.Timer(0.35, _stop).start()
            c, b, t = _json_bytes({"ok": True, "restarting": True, "port": port})
            self._send(c, b, t)
            return

        if path == "/api/settings/clear-keys":
            from modules.config.ini_io import blank_ini_keys
            project = _require_project(body.get("project"))
            proj = project_dir(project, str(body.get("dir") or ""))
            settings_raw = str(body.get("settings") or "").strip()
            settings_path = Path(settings_raw) if settings_raw else project_settings(proj)
            if not settings_path.is_absolute():
                settings_path = proj / settings_path
            settings_path = under_project(proj, settings_path)
            raw_keys = body.get("keys") or []
            if isinstance(raw_keys, str):
                raw_keys = [part for part in raw_keys.split(",") if part.strip()]
            if not isinstance(raw_keys, list):
                raise ValueError("keys 须是列表")
            info = blank_ini_keys(settings_path, [str(k) for k in raw_keys])
            c, b, t = _json_bytes({"ok": True, **info, "settings": str(settings_path)})
            self._send(c, b, t)
            return

        if path == "/api/writeback":
            only_keys = body.get("only_keys")
            if only_keys:
                only = [str(k) for k in only_keys if str(k).strip()]
                project = _require_project(body.get("project"))
                proj = project_dir(project)
                settings_raw = str(body.get("settings") or "").strip()
                settings_path = Path(settings_raw) if settings_raw else project_settings(proj)
                if not settings_path.is_absolute():
                    settings_path = proj / settings_path
                settings_path = under_project(proj, settings_path)
                data_raw = str(body.get("data") or "").strip()
                if not data_raw:
                    raise ValueError("未指定 data")
                data = Path(data_raw)
                if not data.is_absolute():
                    data = proj / data
                data = under_project(proj, data)
                if not data.is_file():
                    raise FileNotFoundError(f"数据文件不存在: {data}")
                raw_ov = dict(body.get("overlay") or {})
                overlay = {k: raw_ov[k] for k in only if k in raw_ov}
                overlay = sync_external_tests(dict(overlay))
                keep = set(only) | {"EXTERNAL_TESTS"}
                overlay = {k: v for k, v in overlay.items() if k in keep}
                info = writeback_settings(settings_path, overlay, normalize=False)
                ses = load_session() or {}
                prev = dict(ses.get("overlay") or {})
                prev.update(overlay)
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                save_session({
                    "project": project,
                    "data": str(data),
                    "settings": str(settings_path),
                    "saved_at": stamp,
                    "restore_page": False,
                    "overlay": jsonable(prev),
                })
                cur = read_project_overlay(proj)
                cur.update(overlay)
                write_project_overlay(proj, {
                    "project": project,
                    "data": str(data),
                    "settings": str(settings_path),
                    "saved_at": stamp,
                    "overlay": jsonable(cur),
                })
            else:
                project, data, settings_path, settings, overlay = _merged_from_request(body)
                info = writeback_settings(settings_path, overlay)
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                ov = jsonable(normalize_overlay(overlay))
                save_session({
                    "project": project,
                    "data": str(data),
                    "settings": str(settings_path),
                    "saved_at": stamp,
                    "restore_page": False,
                    "overlay": ov,
                })
                write_project_overlay(project_dir(project), {
                    "project": project,
                    "data": str(data),
                    "settings": str(settings_path),
                    "saved_at": stamp,
                    "overlay": ov,
                })
            c, b, t = _json_bytes({"ok": True, **info, "settings": str(settings_path)})
            self._send(c, b, t)
            return

        if path == "/api/open-folder":
            project = _require_project(body.get("project"))
            rel = str(body.get("rel") or "").strip()
            try:
                info = open_result_folder(project, rel)
                c, b, t = _json_bytes(info)
            except Exception as e:
                c, b, t = _json_bytes({"ok": False, "error": str(e)}, 400)
            self._send(c, b, t)
            return

        if path == "/api/pick-file":
            info = pick_file_dialog(
                str(body.get("dir") or ""),
                str(body.get("title") or "选择 Excel"),
            )
            c, b, t = _json_bytes(info)
            self._send(c, b, t)
            return

        if path == "/api/port":
            n = int(body.get("port") or 0)
            if n < 1024 or n > 65535:
                raise ValueError("端口须在 1024–65535")
            save_preferred_port(n)
            c, b, t = _json_bytes({
                "ok": True,
                "preferred": n,
                "current": _state.SERVE_PORT,
                "note": "已记住。下次启动用此端口；当前进程仍占用原端口。",
            })
            self._send(c, b, t)
            return

        if path == "/api/overlay/clear":
            project = _require_project(body.get("project"))
            cleared = clear_project_overlay(project_dir(project))
            c, b, t = _json_bytes({"ok": True, "cleared": cleared})
            self._send(c, b, t)
            return

        if path == "/api/session":
            project, data, settings_path, _s, overlay = _merged_from_request(body)
            save_session({
                "project": project,
                "data": str(data),
                "settings": str(settings_path),
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "restore_page": True,
                "overlay": jsonable(normalize_overlay(overlay)),
            })
            c, b, t = _json_bytes({"ok": True, "path": str(OVERRIDE_PATH)})
            self._send(c, b, t)
            return

        self._err(404, path)

    def _do_DELETE(self) -> None:
        u = urlparse(self.path)
        self._err(404, u.path)



