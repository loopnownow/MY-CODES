"""控制台端口 / 服务层（B4c6：从 console.py 逐字搬出）。

依赖 const（端口段、HOST）与标准库；不 import console，也不 import http_impl；
console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from modules.console.const import CHECK_PORT, HOST, PORT_HI, PORT_LO

_IMPORT_ROOT = Path(__file__).resolve().parents[2]   # 与 console.py 同一仓库根
_CONSOLE_PY = Path(__file__).resolve().parent / "console.py"   # 原 console.py 的 __file__

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
    script = str(_CONSOLE_PY)
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
        sys.executable, str(_CONSOLE_PY),
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
