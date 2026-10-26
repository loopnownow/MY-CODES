"""
console.py — RAD 统计控制台后端（modules/console/console.html）

页面配置（console.html / overlay JSON）优先于 settings.ini。

用法:
  python console.py              # 启动 http://127.0.0.1:<端口>
  python console.py serve --port 8765   # 默认 8765，可改；记住后写在 console.port.json
  python console.py run --project demo_study --data PATH --overlay PATH [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:  # 运行期由文件末尾 http_server.install(globals()) 导出
    from modules.console.http_impl import ConsoleServer, Handler

# ── CONFIG ─────────────────────────────────────────────────────
# Protocol handler launches this file directly; bootstrap import root first.
_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from modules.console import paths as _paths  # noqa: E402  bootstrap 之后才能 import

from modules.console.const import (  # noqa: E402,F401  bootstrap 之后才能 import
    CHECK_PORT,
    CLIN_COL_SINGLE_KEYS,
    CONSOLE_DEFAULTS,
    CONSOLE_DIR,
    CSS_PATH,
    DEFAULT_PORT,
    DEFAULT_PROJECT,
    EXCLUDE_EMPTY_LABELS,
    EXCLUDE_SELECT_KEYS,
    EXTRA_PROJECT_ROOTS,
    GROK_ROOT,
    HOST,
    HTML_PATH,
    JS_ASSETS,
    JS_PATH,
    LIST_FROM_CLIN,
    LIST_FROM_MODELS,
    LIST_FROM_RAD_META,
    MODULES_ROOT,
    OVERRIDE_PATH,
    PORT_FILE,
    PORT_HI,
    PORT_LO,
    RAD_COL_SINGLE_KEYS,
    RAD_ROOT,
    SET_OUTCOME_DERIVED,
    SHEET_KEYS,
    SKIP_SETTING_KEYS,
    STAGE_ROOT_NAMES,
    STROBE_PATH,
    TUPLE_LIST_KEYS,
    _RAD_FEAT_PREFIX,
    _TOKEN_SAFE_GET,
)

# 叶层实现在 ops.py；此处保留同名绑定给 console 命名空间与旧调用点
from modules.console.ops import (  # noqa: F401
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


# ── 运行期可变状态：单一之家在 modules.console.state ──────────
from modules.console import state as _state

# 兼容别名（同一对象 / 同一字符串）：真值在 _state，此处只为少改调用点。
_CONSOLE_TOKEN = _state.CONSOLE_TOKEN


from modules.console.schema import (  # noqa: E402,F401
    HINTS,
    HOT_KEYS,
    LABELS,
    SECTION_DEFS,
    SECTION_OF,
    _ADDSELECT_LABEL,
    _COLUMN_MULTI,
    _LABEL_DISPLAY_CANDS,
    _MODEL_CHOICES,
    _PICK_XLSX_SCRIPT,
    _PREVIEW_HEAD_MAX_COLS,
    _RESULT_SKIP_DIRS,
    _SHEET_ALIASES,
)

# UI schema 实现在 schema_ui.py；此处回导同名符号给 console 命名空间与旧调用点。
from modules.console.schema_ui import (  # noqa: E402,F401
    _as_name_list,
    _as_prefix_group,
    _column_multi_pool,
    _column_names,
    _columns_open_for_analysis,
    _id_dropdown_columns,
    _is_rad_feature,
    _looks_like_id_column,
    _measure_cols,
    _rad_meta_cols,
    _section_of,
    _uniq_keep,
    _uniq_keep_str,
    _widget_for,
    apply_clin_role_priority,
    apply_label_col_suggestion,
    build_schema,
    hydrate_console_settings,
    implicit_skip_cols,
    public_settings,
    suggest_label_col,
)

# 设置/路径/会话层实现在 settings_layer.py；此处回导同名符号。
from modules.console.settings_layer import (  # noqa: E402,F401
    discover_projects,
    find_results,
    format_py_value,
    load_preferred_port,
    load_session,
    load_settings_module,
    normalize_overlay,
    open_result_folder,
    pick_file_dialog,
    project_dir,
    project_roots,
    read_project_overlay,
    save_preferred_port,
    save_session,
    under_project,
    under_root,
    unwrap_overlay_file,
    writeback_settings,
)

# 工作簿预览层实现在 preview.py；此处回导同名符号。
from modules.console.preview import (  # noqa: E402,F401
    _exclude_cell_text,
    _info_from_frame,
    _preview_cache_key,
    _preview_external_one,
    _preview_one_workbook,
    _resolve_ext_xlsx,
    exclude_select_options,
    preview_workbook,
    project_xlsx_names,
    resolve_sheet_name,
    xlsx_meta,
)

# dry-run 预览层实现在 dryrun.py；此处回导同名符号。
from modules.console.dryrun import (  # noqa: E402,F401
    _exclude_file_log,
    _levels_for_pairs,
    _levels_from_outcome_dist,
    _outcome_cfg,
    _pair_display,
    _pair_n,
    _parse_pair_list,
    _td_roc_dry_lines,
    _td_roc_name_list,
    dry_run_text,
    plan_dry_dests,
    resolve_dry_pairs,
)

# 运行层实现在 run.py；此处回导同名符号。
from modules.console.run import (  # noqa: E402,F401
    _popen_background_kwargs,
    apply_overlay_to_modules,
    read_log,
    resolve_modules_dir,
    run_pipeline_inprocess,
    run_project_data_qc,
    run_status,
    start_run,
    start_run_bat,
    stop_run,
    wrap_set_outcome,
)

_RUN_LOCK = _state.RUN_LOCK   # 同一把锁
_RUN = _state.RUN             # 同一个 dict（只 update，不重绑）

# ── paths ──────────────────────────────────────────────────────
def _resolve(p: str | Path) -> Path:
    return _paths.resolve_path(p)

# ── state 别名 ───────────────────────────────────────────────────
_PREVIEW_CACHE = _state.PREVIEW_CACHE  # 同一个 dict

# Port helpers / serve below; HTTP Handler installed at module end.

def pick_port(preferred: int) -> int:
    import socket
    hi = max(preferred + 12, PORT_HI + 1)
    for port in range(preferred, hi):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
            except OSError:
                continue
        return port
    raise RuntimeError("8765–8776 端口都被占用")

def _already_serving(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=1.0) as r:
            obj = json.loads(r.read().decode("utf-8"))
        return bool(obj.get("ok"))
    except Exception:
        return False

def _parse_listen_table(text: str) -> dict[int, int]:
    """netstat -ano -p tcp → {port: pid} for LISTENING rows. First hit wins."""
    found: dict[int, int] = {}
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        _host, sep, port_s = parts[1].rpartition(":")
        if not sep:
            continue
        try:
            port = int(port_s)
            pid = int(parts[-1])
        except ValueError:
            continue
        found.setdefault(port, pid)
    return found

def _listen_map() -> dict[int, int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"], text=True, encoding="utf-8", errors="replace"
        )
    except Exception:
        return {}
    return _parse_listen_table(out)

def _listen_pid(port: int) -> int | None:
    return _listen_map().get(int(port))

def _command_lines(pids: set[int]) -> dict[int, str]:
    """PID → command line. PowerShell first; wmic is absent on current Windows."""
    want = {int(p) for p in pids if int(p) > 0}
    if not want:
        return {}
    found: dict[int, str] = {}
    filt = " or ".join(f"ProcessId={p}" for p in sorted(want))
    script = (
        "$rows = @(Get-CimInstance Win32_Process -Filter '" + filt + "'); "
        "foreach ($x in $rows) { "
        "if ($null -eq $x) { continue }; "
        "Write-Output ($x.ProcessId.ToString() + '|' + [string]$x.CommandLine) }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True, encoding="utf-8", errors="replace", timeout=6,
        )
    except Exception:
        out = ""
    for line in (out or "").splitlines():
        pid_s, sep, cmd = line.partition("|")
        if not sep:
            continue
        try:
            found[int(pid_s.strip())] = cmd.strip()
        except ValueError:
            continue
    missing = want - set(found)
    for pid in sorted(missing):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                text=True, encoding="utf-8", errors="replace", timeout=8,
            )
        except Exception:
            continue
        found[pid] = out or ""
    return found

def _pid_is_console(pid: int) -> bool:
    cmd = _command_lines({int(pid)}).get(int(pid), "")
    return "console.py" in cmd.lower()

def _process_label(cmd: str, console: bool) -> str:
    if console:
        return "console.py"
    text = (cmd or "").strip()
    if not text:
        return ""
    if text.startswith('"'):
        end = text.find('"', 1)
        token = text[1:end] if end > 1 else text
    else:
        token = text.split()[0]
    return Path(token).name or token

def _service_label(cmd: str, console: bool) -> str:
    """Short name of whatever is listening, for the start page confirm line."""
    if not console:
        return _process_label(cmd, False)
    proj = ""
    parts = (cmd or "").split()
    for i, tok in enumerate(parts):
        if tok == "--project" and i + 1 < len(parts):
            proj = parts[i + 1].strip('"').strip("'")
            break
    return ("本控制台 · " + proj) if proj else "本控制台"

def describe_port(port: int) -> dict[str, Any]:
    """Who is listening on one port. Does not stop anything."""
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return {
            "ok": False, "port": 0, "listening": False, "console": False,
            "pid": 0, "name": "", "service": "", "error": "端口无效",
        }
    pid = _listen_pid(port_i)
    if not pid:
        return {
            "ok": True, "port": port_i, "listening": False, "console": False,
            "pid": 0, "name": "", "service": "", "error": "",
        }
    cmd = _command_lines({pid}).get(pid, "")
    console = "console.py" in cmd.lower()
    return {
        "ok": True,
        "port": port_i,
        "listening": True,
        "console": console,
        "pid": pid,
        "name": _process_label(cmd, console),
        "service": _service_label(cmd, console),
        "error": "",
    }

def publish_local(payload: dict[str, Any], port: int = CHECK_PORT, hold: float = 20) -> bool:
    """Serve the same JSON on 8780 for `hold` seconds.

    Every GET gets the body. A cancelled or preflight request does not use it up.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    delivered = {"n": 0}

    class One(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")

        def do_OPTIONS(self):  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                return
            delivered["n"] += 1

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    try:
        httpd = HTTPServer((HOST, int(port)), One)
    except OSError:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return False
    httpd.timeout = 0.5
    deadline = time.time() + max(0.2, float(hold))
    try:
        while time.time() < deadline:
            httpd.handle_request()
    finally:
        httpd.server_close()
    return delivered["n"] > 0

def radconsole_action(url: str) -> str:
    """serve starts one port; check names its listener; stop closes that one port."""
    u = urlparse(str(url or ""))
    action = ((u.netloc or "").strip() or (u.path or "").strip("/")).lower()
    if action in {"stop", "kill", "shutdown"}:
        return "stop"
    if action == "check":
        return "check"
    return "serve"

def _port_band(lo: int | None, hi: int | None) -> tuple[int, int]:
    """Clamp to 8765–8776. An inverted or out-of-band pair yields an empty range (lo > hi)."""
    def _as_int(v: int | None, default: int) -> int:
        if v is None or v == "":
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    a = _as_int(lo, PORT_LO)
    b = _as_int(hi, PORT_HI)
    if a > b:
        a, b = b, a
    a = max(PORT_LO, a)
    b = min(PORT_HI, b)
    return a, b

def stop_console_ports(lo: int | None = None, hi: int | None = None) -> dict[str, Any]:
    """End console.py listeners on 8765–8776. Other programs on those ports are left running."""
    a, b = _port_band(lo, hi)
    killed: list[dict[str, int]] = []
    spared: list[dict[str, int]] = []
    if a > b:
        return {"ok": True, "killed": killed, "spared": spared, "lo": a, "hi": b}
    listening = _listen_map()
    pids = {listening[port] for port in range(a, b + 1) if port in listening}
    pids.discard(os.getpid())
    commands = _command_lines(pids)
    seen: set[int] = set()
    for port in range(a, b + 1):
        pid = listening.get(port)
        if not pid or pid in seen or pid == os.getpid():
            continue
        seen.add(pid)
        if "console.py" not in commands.get(pid, "").lower():
            spared.append({"port": port, "pid": pid})
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            check=False, capture_output=True,
        )
        killed.append({"port": port, "pid": pid})
    return {"ok": True, "killed": killed, "spared": spared, "lo": a, "hi": b}

def stop_console_port(port: int) -> dict[str, Any]:
    """Stop one console.py listener inside 8765–8776. Any other program is left running."""
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return {"ok": False, "port": 0, "listening": False, "console": False, "pid": 0, "name": "", "killed": False, "error": "端口无效"}
    if not (1024 <= port_i <= 65535):
        return {
            "ok": False, "port": port_i, "listening": False, "console": False,
            "pid": 0, "name": "", "killed": False, "error": "端口超出 1024–65535",
        }
    info = describe_port(port_i)
    if not info.get("listening"):
        return {**info, "killed": False, "error": "此端口没有进程"}
    if not info.get("console"):
        return {**info, "ok": False, "killed": False, "error": "占用者不是本控制台，未关闭"}
    pid = int(info["pid"])
    if pid == os.getpid():
        return {**info, "ok": False, "killed": False, "error": "不能结束当前进程"}
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
    return {**info, "ok": True, "killed": True, "error": ""}

def stop_from_query(qs: dict) -> dict[str, Any]:
    """radconsole://stop requires one port. A missing port does not close the range."""
    raw = qs.get("port") if isinstance(qs, dict) else None
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw or "").strip()
    if not text:
        return {
            "ok": False, "port": 0, "listening": False, "console": False,
            "pid": 0, "name": "", "killed": False, "error": "请指定一个端口",
        }
    try:
        return stop_console_port(int(text))
    except ValueError:
        return {
            "ok": False, "port": 0, "listening": False, "console": False,
            "pid": 0, "name": "", "killed": False, "error": "端口无效",
        }

def _reclaim_dead_port(port: int) -> None:
    """8765 被僵死的旧 console 占着时，健康检查失败 → 结束该进程，让新服务绑回 8765。"""
    if _already_serving(port):
        return
    pid = _listen_pid(port)
    if not pid or pid == os.getpid():
        return
    if not _pid_is_console(pid):
        print(f"端口 {port} 被 PID {pid} 占用且不是本控制台，换端口", flush=True)
        return
    print(f"端口 {port} 上的旧 console.py (PID {pid}) 无响应，正在结束…", flush=True)
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, capture_output=True)
    except Exception:
        return
    for _ in range(20):
        time.sleep(0.15)
        if _listen_pid(port) != pid:
            return

def register_protocol() -> None:
    """HKCU radconsole:// → 本脚本 serve（无需管理员）。"""
    try:
        import winreg
    except ImportError:
        return
    exe = sys.executable
    script = str(Path(__file__).resolve())
    # bootstrap at top of this file makes PYTHONPATH unnecessary
    cmd = f'"{exe}" "{script}" "%1"'
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\radconsole")
    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:RAD Console")
    winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    sub = winreg.CreateKey(key, r"shell\open\command")
    winreg.SetValueEx(sub, "", 0, winreg.REG_SZ, cmd)
    winreg.CloseKey(sub)
    winreg.CloseKey(key)

def _serve_url(port: int, project: str | None, data: str | None = None) -> str:
    from urllib.parse import urlencode
    url = f"http://{HOST}:{port}/"
    q: dict[str, str] = {}
    if project:
        q["project"] = str(project)
    if data:
        q["data"] = str(data)
    if q:
        url += "?" + urlencode(q)
    return url

def _wait_port_free(port: int, seconds: float = 12.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _already_serving(port) and _listen_pid(port) is None:
            return
        time.sleep(0.2)

def spawn_successor(port: int, project: str | None = None) -> None:
    """另起一个 console.py，等本进程让出端口后再 bind。"""
    cmd = [
        sys.executable, str(Path(__file__).resolve()),
        "serve", "--port", str(int(port)), "--no-browser", "--wait-free",
    ]
    if project:
        cmd.extend(["--project", str(project)])
    kw: dict[str, Any] = {
        "cwd": str(_IMPORT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kw["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kw["start_new_session"] = True
    subprocess.Popen(cmd, **kw)

def _tcp_open(port: int, timeout: float = 0.2) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect((HOST, port))
            return True
        except OSError:
            return False

def cmd_serve(port: int, open_browser: bool, project: str | None = None,
              wait_free: bool = False, data: str | None = None,
              exact: bool = False) -> None:
    register_protocol()
    if not HTML_PATH.is_file():
        print(f"缺少界面文件: {HTML_PATH}", file=sys.stderr)
        sys.exit(2)
    if wait_free:
        _wait_port_free(port)
    # 端口没人听就直接绑。健康检查和 netstat 只在端口已被占用时做。
    if _tcp_open(port):
        if _already_serving(port):
            url = _serve_url(port, project, data)
            print(f"已有可用控制台 {url}，不再重复启动", flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        if exact:
            info = describe_port(port)
            who = "本控制台" if info.get("console") else "其他程序"
            extra = ""
            if info.get("listening"):
                extra = f"（{who} PID {info.get('pid')} {info.get('name') or ''}）".rstrip()
                info["error"] = "端口被本控制台占用" if info.get("console") else "端口被其他程序占用"
            else:
                info["error"] = "端口没有可用的控制台"
            info["op"] = "serve"
            info["ok"] = False
            info["killed"] = False
            print(f"端口 {port} 已被占用{extra}。未自动关闭，也未改绑其他端口。", flush=True)
            publish_local(info)
            sys.exit(2)
        _reclaim_dead_port(port)
        if _already_serving(port):
            url = _serve_url(port, project, data)
            print(f"已有可用控制台 {url}，不再重复启动", flush=True)
            if open_browser:
                webbrowser.open(url)
            return
    if not exact:
        try:
            port = pick_port(port)
        except RuntimeError:
            print("端口被占用且健康检查失败。请关掉旧的 python 窗口后重试。", file=sys.stderr)
            sys.exit(2)
    try:
        httpd = ConsoleServer((HOST, port), Handler)
    except OSError:
        print(f"端口 {port} 无法绑定。", flush=True)
        if exact:
            publish_local({
                "op": "serve", "ok": False, "port": port, "listening": True,
                "console": False, "pid": 0, "name": "", "killed": False,
                "error": "端口无法绑定",
            })
        sys.exit(2)
    _state.HTTPD = httpd
    _state.SERVE_PORT = port
    save_preferred_port(port)
    url = _serve_url(port, project, data)
    print(f"console.html  {url}", flush=True)
    print(f"RAD_ROOT     {RAD_ROOT}", flush=True)
    print(f"MODULES      {MODULES_ROOT}", flush=True)
    print("Ctrl+C 结束", flush=True)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstop")
    finally:
        _state.HTTPD = None
        _state.SERVE_PORT = None
        httpd.server_close()

def cmd_run(args: argparse.Namespace) -> int:
    project = args.project
    data = under_root(args.data)
    ov_path = _resolve(args.overlay) if args.overlay else OVERRIDE_PATH
    overlay: dict[str, Any] = {}
    if ov_path.is_file():
        overlay = unwrap_overlay_file(json.loads(ov_path.read_text(encoding="utf-8")))
    outcome = str(getattr(args, "outcome", "") or "").strip()
    if args.dry_run:
        settings_path = Path(args.settings) if args.settings else (
            project_settings(project_dir(project))
        )
        settings_path = under_root(settings_path)
        settings = public_settings(load_settings_module(settings_path))
        merged = dict(settings)
        merged.update(overlay)
        preview = preview_workbook(data, merged)
        print(dry_run_text(
            project, data, settings_path, settings, overlay, preview,
            run_outcome=outcome,
        ))
        return 0
    return run_pipeline_inprocess(
        project, data, overlay, dry_run=False, log_path=None,
        outcome=outcome,
    )

def main(argv: list[str] | None = None) -> None:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and str(raw[0]).startswith("radconsole:"):
        u = urlparse(raw[0])
        qs = parse_qs(u.query)
        action = radconsole_action(raw[0])
        if action == "check":
            try:
                port = int((qs.get("port") or [""])[0] or 0)
            except ValueError:
                port = 0
            if not port:
                print("请指定要确认的端口", flush=True)
                return
            info = describe_port(port)
            if info.get("console"):
                print(f"端口 {port} 是本控制台 PID {info.get('pid')}", flush=True)
            elif info.get("listening"):
                print(
                    f"端口 {port} 被其他程序占用 PID {info.get('pid')} {info.get('name') or ''}".rstrip(),
                    flush=True,
                )
            else:
                print(f"端口 {port} 没有进程", flush=True)
            publish_local(info)
            return
        if action == "stop":
            result = stop_from_query(qs)
            if result.get("killed"):
                print(f"已关闭端口 {result.get('port')} (PID {result.get('pid')})", flush=True)
            else:
                print(result.get("error") or "未关闭", flush=True)
            publish_local(result)
            return
        project = (qs.get("project") or [""])[0] or None
        data = (qs.get("data") or [""])[0] or None
        try:
            port = int((qs.get("port") or [""])[0] or 0)
        except ValueError:
            port = 0
        mods = (qs.get("modules") or [""])[0].strip()
        if mods:
            if not Path(mods).is_dir():
                raise SystemExit(f"MODULES_DIR 不是现存目录: {mods}")
            os.environ["RAD_MODULES"] = str(Path(mods))
        proj_dir = (qs.get("dir") or [""])[0].strip()
        if proj_dir:
            os.environ["RAD_PROJECT"] = str(Path(proj_dir))
        elif project:
            os.environ["RAD_PROJECT"] = str(project_dir(project))
        exact = (qs.get("exact") or ["0"])[0].strip().lower() in {"1", "true", "yes"}
        cmd_serve(
            port or load_preferred_port(),
            open_browser=False,
            project=project,
            data=data,
            exact=exact,
        )
        return

    parser = argparse.ArgumentParser(description="RAD 统计控制台")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="启动网页")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.add_argument("--wait-free", action="store_true")
    p_serve.add_argument("--project", default="")

    p_run = sub.add_parser("run", help="用 overlay 跑 pipeline")
    p_run.add_argument("--project", required=True)
    p_run.add_argument("--data", required=True)
    p_run.add_argument("--settings", default="")
    p_run.add_argument("--overlay", default="")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.add_argument(
        "--outcome", default="",
        help="只跑这一结局；空=公共页，跑全部 OUTCOME_COLS",
    )

    args = parser.parse_args(argv)
    if args.cmd in (None, "serve"):
        port = getattr(args, "port", None) or load_preferred_port()
        nob = getattr(args, "no_browser", False)
        project = getattr(args, "project", "") or None
        wait_free = bool(getattr(args, "wait_free", False))
        cmd_serve(port, open_browser=not nob, project=project, wait_free=wait_free)
        return
    if args.cmd == "run":
        try:
            rc = cmd_run(args)
        except Exception:
            traceback.print_exc()
            rc = 1
        sys.exit(rc)
    parser.print_help()

# Install Handler after all helpers exist (http_impl looks up free names here).
from modules.console.http_server import install as _install_http
_install_http(globals())

if __name__ == "__main__":
    main()
