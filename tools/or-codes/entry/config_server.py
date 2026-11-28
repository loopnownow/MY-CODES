# -*- coding: utf-8 -*-
"""工程控制台启动器（`_Codes` 里唯一的启动入口）。

用途：
    python entry\\config_server.py --project fyh_CAC --open
    python entry\\config_server.py --ini G:\\CC\\fyh_CAC\\settings.ini --open
    python entry\\config_server.py --launch-protocol "habitat-config:fyh_CAC"
    python entry\\config_server.py --register-protocol      # 登记浏览器协议（当前用户）
    python entry\\config_server.py --unregister-protocol
    python entry\\config_server.py --status

浏览器协议 `habitat-config:<project>` 只接受 [A-Za-z0-9_] 的工程名，其余一律忽略并给出提示；
命令行为固定模板，不拼接任何请求方字符串。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import winreg
from pathlib import Path

CORE = Path(os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}")
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

LAB = Path(os.environ.get("LAB", ""))
SCHEME = "habitat-config"
PROTO_KEY = rf"Software\Classes\{SCHEME}"
PROJECT_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
PROTO_RE = re.compile(rf"^{SCHEME}:(?://)?([A-Za-z0-9_]*)")
STATE = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "habitat_core" / "server.json"
LAUNCH_LOG = STATE.with_name("launcher.log")


def redirect_stdout_if_headless() -> None:
    """pythonw 下 sys.stdout 可能为 None，print 会抛异常；重定向到日志文件便于排查。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
        fh = LAUNCH_LOG.open("a", encoding="utf-8", errors="replace")
        sys.stdout = fh
        sys.stderr = fh
    except Exception:
        pass


def already_serving() -> int | None:
    """已有实例在跑就返回其端口，便于直接开页面而不是再起一个。"""
    if not STATE.is_file():
        return None
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        port = int(data.get("port") or 0)
    except (ValueError, OSError):
        return None
    if not port:
        return None
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            if json.loads(r.read().decode("utf-8")).get("ok"):
                return port
    except Exception:
        return None
    return None


def pythonw() -> str:
    exe = Path(sys.executable or "python.exe")
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.is_file() else exe)


def projects() -> list[str]:
    direct = [p.parent.name for p in LAB.glob("*/settings.ini")]
    nested = [p.parent.name for p in LAB.glob("*/*/settings.ini")]
    return sorted(set(direct) | set(nested))


def pick_ini(project: str | None, ini: str | None) -> Path | None:
    if ini:
        p = Path(ini)
        return p if p.is_file() else None
    if project and PROJECT_RE.match(project):
        p = LAB / project / "settings.ini"
    if not p.is_file():
        hits = sorted(LAB.glob(f"*/{project}/settings.ini"))
        if hits:
            p = hits[0]
        return p if p.is_file() else None
    return None


def launch_browser(port: int) -> None:
    import webbrowser
    webbrowser.open(f"http://127.0.0.1:{port}/")


def status() -> int:
    if not STATE.is_file():
        print("未运行")
        return 1
    data = json.loads(STATE.read_text(encoding="utf-8"))
    print(f"运行中 port={data.get('port')} ini={data.get('ini')} pid={data.get('pid')}")
    return 0


def register_protocol(execute: bool = True) -> int:
    cmd = f'"{pythonw()}" "{CORE / "entry" / "config_server.py"}" --launch-protocol "%1"'
    if not execute:
        print(cmd)
        return 0
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTO_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:Habitat Config Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROTO_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)
    print(f"已登记 {SCHEME}: -> {cmd}")
    return 0


def unregister_protocol() -> int:
    for path in (PROTO_KEY + r"\shell\open\command", PROTO_KEY + r"\shell\open", PROTO_KEY):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass
    print(f"已撤销 {SCHEME}:")
    return 0


def main() -> int:
    redirect_stdout_if_headless()
    parser = argparse.ArgumentParser(description="工程控制台启动器")
    parser.add_argument("--project")
    parser.add_argument("--ini")
    parser.add_argument("--port", type=int)
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--launch-protocol")
    parser.add_argument("--register-protocol", action="store_true")
    parser.add_argument("--unregister-protocol", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.register_protocol:
        return register_protocol()
    if args.unregister_protocol:
        return unregister_protocol()
    if args.status:
        return status()

    project, ini = args.project, args.ini
    if args.launch_protocol:
        m = PROTO_RE.match(args.launch_protocol)
        project = (m.group(1) if m and m.group(1) else project) or project

    port = already_serving()
    if port:
        print(f"已有一个控制台在 {port}，直接打开页面")
        launch_browser(port)
        return 0

    target = pick_ini(project, ini)
    if target is None:
        names = projects()
        print("需要指定工程：--project <name> 或 --ini <settings.ini>")
        print("可用工程：" + (", ".join(names) if names else "（G:\\ 下没有 settings.ini）"))
        if project:
            print(f"（收到的工程名：{project}）")
        return 2

    from habitat_core.web.server import serve
    return serve(target, prefer_port=args.port,
                 open_browser=args.open or bool(args.launch_protocol))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # 无控制台时把异常写进 launcher.log
        import traceback
        traceback.print_exc()
        sys.exit(1)
