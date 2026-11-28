# -*- coding: utf-8 -*-
"""工程控制台服务（只绑 127.0.0.1）。

安全约束（硬编码，不接受请求方改）：
  * 只监听 127.0.0.1；端口 8791 起，占用则顺延到 8799。
  * 读接口按 Origin 白名单（null / 本机回环）；写接口与执行接口还要求 X-Token。
  * Token 每次启动随机生成，写进 %LOCALAPPDATA%\\habitat_core\\server.json（仅本机）。
  * 可执行动作是白名单：pipeline / smoke / 本工程在 _Codes\\tools 下的脚本。
  * 子进程用 argv 列表（shell=False）；不拼接任何请求方字符串。
  * 一次只跑一个任务；输出写 <工程归档>\\ref\\tmp\\run-<时间>.log。
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..ini_config import read_ini, write_ini
from ..ini_sections import KEY_LABELS, READONLY_KEYS, SECTIONS

HOST = "127.0.0.1"
PORTS = list(range(8791, 8800))
CORE = Path(os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}")
LAB = Path(os.environ.get("LAB", ""))
WEB_DIR = Path(__file__).resolve().parent
STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "habitat_core"
STATE_FILE = STATE_DIR / "server.json"
TZ = timezone(timedelta(hours=8))
TOKEN = secrets.token_urlsafe(24)
ALLOWED_KEYS = {k for _s, _t, keys in SECTIONS for k in keys}


class Runner:
    """单任务执行器（同一时刻只跑一个）。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.name = ""
        self.log: Path | None = None
        self.started = 0.0
        self.lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        alive = self.proc is not None and self.proc.poll() is None
        tail = ""
        size = 0
        if self.log and self.log.is_file():
            size = self.log.stat().st_size
            data = self.log.read_text(encoding="utf-8", errors="replace")
            tail = data[-8000:]
        return {"running": alive, "name": self.name,
                "code": None if alive or self.proc is None else self.proc.returncode,
                "elapsed": round(time.time() - self.started, 1) if self.started else 0,
                "log": str(self.log or ""), "log_size": size, "tail": tail}

    def start(self, name: str, argv: list[str], cwd: Path, log: Path) -> dict[str, Any]:
        with self.lock:
            if self.proc is not None and self.proc.poll() is None:
                raise RuntimeError(f"已有任务在跑：{self.name}")
            log.parent.mkdir(parents=True, exist_ok=True)
            fh = log.open("w", encoding="utf-8", errors="replace")
            self.proc = subprocess.Popen(argv, cwd=str(cwd), stdout=fh,
                                         stderr=subprocess.STDOUT, shell=False)
            self.name, self.log, self.started = name, log, time.time()
            return self.status()

    def stop(self, wait: float = 3.0) -> dict[str, Any]:
        """终止当前任务：先 terminate，超时再 kill；返回停止后的状态。"""
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return self.status()
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            return self.status()


RUNNER = Runner()


def python_exe() -> str:
    return sys.executable or "python"


RISK = {
    "pipeline": ("write", "会读 NAS 影像并写处理盘/归档盘"),
    "smoke": ("safe", "合成数据，不读 NAS"),
    "cleanup": ("write", "会删除 checkpoints（dry-run 时只列不删）"),
    "match": ("write", "会写 combined/unmatched/report"),
    "roi": ("nas-write", "会往影像盘写 ROI 文件"),
}
GROUP_TO_ACTION = {"cleanup": "cleanup", "match": "match", "roi": "roi",
                   "adc": "adc", "dicom": "dicom", "fit": "fit", "plot": "plot"}


def action_config(ini: Path, project: str) -> dict[str, Any]:
    from ..actions_config import load_actions
    return load_actions(ini, project)


def action_key(aid: str) -> str:
    """动作 id -> actions.ini 的段名：入口动作即 id，工具动作取分组名。"""
    if aid.startswith("tool:"):
        group = aid.split(":", 1)[1].split("/")[0]
        return GROUP_TO_ACTION.get(group, group)
    return GROUP_TO_ACTION.get(aid, aid)


def _is_runnable(path: Path) -> bool:
    """只把带 __main__ 入口的脚本当动作；依赖模块（如 workflow_utils.py）不算。"""
    if path.name.startswith("_"):
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "__main__" in text


def actions_for(proj: str, ini: Path | None = None) -> list[dict[str, Any]]:
    """动作清单（配置驱动）：入口两个 + 本工程在 tools 下的脚本。"""
    full = action_config(ini, proj) if ini else {"confirm": {}, "actions": {}}
    confirm = full.get("confirm") or {}
    actions_cfg = full.get("actions") or {}
    acts: list[dict[str, Any]] = []

    def add(aid: str, label: str, kind: str) -> None:
        key = action_key(aid)
        one = actions_cfg.get(key, {})
        risk, why = RISK.get(key, ("write", "会写盘"))
        acts.append({"id": aid, "label": label, "kind": kind,
                     "enabled": bool(one.get("ENABLED", True)),
                     "dry_run": bool(one.get("DRY_RUN", False)),
                     "risk": risk, "risk_note": why,
                     "confirm": bool(confirm.get("REQUIRE", True)),
                     "phrase": str(confirm.get("PHRASE") or proj)})

    add("pipeline", "跑流水线", "entry")
    add("smoke", "冒烟测试（12 项）", "entry")
    tools_root = CORE / "tools"
    if tools_root.is_dir():
        for group in sorted(p for p in tools_root.iterdir() if p.is_dir()):
            proj_dir = group / proj
            if not proj_dir.is_dir():
                continue
            for script in sorted(proj_dir.glob("*.py")):
                if not _is_runnable(script):
                    continue
                add(f"tool:{group.name}/{proj}/{script.name}",
                    f"{group.name} / {script.stem}", "tool")
    return acts


def build_argv(action: str, proj: str, ini: Path,
               full: dict[str, Any] | None = None) -> tuple[str, list[str], Path]:
    """把动作解析成 argv（含 dry-run / 输出目录 / limit）；非法动作直接拒绝。"""
    full = full or action_config(ini, proj)
    actions_cfg = full.get("actions") or {}
    key = action_key(action)
    one = actions_cfg.get(key, {})
    dry = bool(one.get("DRY_RUN", False))
    extra = list(one.get("ARGS") or [])

    if action == "pipeline":
        if dry:
            raise ValueError("流水线没有 dry-run 能力：请在 actions.ini 把 [pipeline] DRY_RUN 设 false")
        return ("跑流水线", [python_exe(), str(CORE / "entry" / "run_pipeline.py"),
                            "--ini", str(ini), *extra], ini.parent)
    if action == "smoke":
        return ("冒烟测试", [python_exe(), str(CORE / "entry" / "run_smoke_test.py"),
                            "--ini", str(ini), *extra], ini.parent)
    if action.startswith("tool:"):
        parts = action.split(":", 1)[1].split("/")
        if len(parts) != 3 or parts[1] != proj or not parts[2].endswith(".py"):
            raise ValueError("非法工具动作")
        script = CORE / "tools" / parts[0] / parts[1] / parts[2]
        if not script.is_file() or (CORE / "tools") not in script.parents:
            raise ValueError("工具不存在")
        argv = [python_exe(), str(script), "--project", proj]
        group = parts[0]
        if group == "cleanup":
            # 该脚本默认 dry-run，只有 --execute 才真删
            argv += extra if dry else ["--execute", *extra]
        else:
            if dry:
                argv.append("--dry-run")
            out_dir = str(one.get("OUTPUT_DIR") or "")
            if group in ("match", "roi") and out_dir:
                argv += ["--output-dir", out_dir]
            if group == "roi" and int(one.get("LIMIT") or 0) > 0:
                argv += ["--limit", str(int(one["LIMIT"]))]
            argv += extra
        return (parts[2], argv, ini.parent)
    raise ValueError(f"未知动作: {action}")


def preview_cmd(action: str, proj: str, ini: Path,
                full: dict[str, Any] | None = None) -> str:
    try:
        _name, argv, _cwd = build_argv(action, proj, ini, full)
    except ValueError as exc:
        return f"（不可执行：{exc}）"
    return " ".join(f'"{a}"' if " " in a else a for a in argv)



def field_kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (list, dict)):
        return "json"
    return "text"


def backup_dir(ini: Path, values: dict[str, Any] | None = None) -> Path:
    vals = values if values is not None else read_ini(ini)
    raw = vals.get("BACKUP_DIR")
    return Path(str(raw)) if raw else ini.parent / "ref" / "tmp"


def build_payload(ini: Path) -> dict[str, Any]:
    from ..actions_config import ACTIONS_NAME, CONSOLE_NAME, load_console
    values = read_ini(ini)
    sections = []
    for sid, title, keys in SECTIONS:
        fields = [{"key": k, "label": KEY_LABELS.get(k, k), "value": values[k],
                   "kind": field_kind(values[k]), "readonly": k in READONLY_KEYS}
                  for k in keys if k in values]
        if fields:
            sections.append({"id": sid, "title": title, "fields": fields})
    proj = str(values.get("PROJECT") or ini.parent.name)
    full = action_config(ini, proj)
    acts = actions_for(proj, ini)
    for act in acts:
        act["preview"] = preview_cmd(act["id"], proj, ini, full)
    return {"ini": str(ini), "project": proj, "sections": sections,
            "backup_dir": str(backup_dir(ini, values)),
            "actions": acts,
            "console": load_console(ini, proj),
            "actions_ini": str(ini.parent / ACTIONS_NAME),
            "console_ini": str(ini.parent / CONSOLE_NAME),
            "confirm": full.get("confirm") or {}}


def save_payload(ini: Path, incoming: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(incoming) - ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"不认识的键: {unknown}")
    locked = sorted(set(incoming) & READONLY_KEYS)
    if locked:
        raise ValueError(f"只读键不可改: {locked}")
    values = read_ini(ini)
    values.update(incoming)
    bak_dir = backup_dir(ini, values)
    bak_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    (bak_dir / f"settings.ini.{stamp}.bak").write_bytes(ini.read_bytes())
    write_ini(ini, values, SECTIONS,
              header=[f"# {values.get('PROJECT', ini.parent.name)} 工程配置（只改本文件）",
                      "# 值用 JSON：true/false/null、数字、字符串带引号、列表/对象。"])
    return {"ok": True, "saved": len(incoming), "backup_dir": str(bak_dir)}


class Handler(BaseHTTPRequestHandler):
    ini: Path = Path("settings.ini")

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin == "null" or origin.startswith(f"http://{HOST}:")

    def _cors(self) -> str:
        """file:// 页面的 Origin 是 null，必须原样回 null，否则浏览器拦截 fetch。"""
        origin = self.headers.get("Origin")
        if origin == "null":
            return "null"
        if origin and origin.startswith(f"http://{HOST}:"):
            return origin
        return f"http://{HOST}:{self.server.server_port}"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", self._cors())
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict[str, Any]) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _authed(self) -> bool:
        return self.headers.get("X-Token") == TOKEN

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:  # noqa: N802
        raw_path, _, query = self.path.partition("?")
        path = raw_path
        if not self._origin_ok():
            self._json(403, {"ok": False, "error": "origin 不允许"})
            return
        if path in ("/", "/index.html", "/config.html"):
            self._switch_project(query)
            page = (WEB_DIR / "config.html").read_text(encoding="utf-8")
            page = page.replace("__TOKEN__", TOKEN)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/static/app.js":
            js = WEB_DIR / "static" / "app.js"
            if js.is_file():
                self._send(200, js.read_bytes(), "application/javascript; charset=utf-8")
                return
            self._json(404, {"ok": False, "error": "app.js 缺失"})
            return
        if path == "/api/ping":
            self._json(200, {"ok": True, "port": self.server.server_port,
                             "project": Handler.ini.parent.name})
            return
        if path == "/api/config":
            try:
                self._json(200, build_payload(self.ini))
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/run":
            self._json(200, RUNNER.status())
            return
        if path == "/api/run/log":
            st = RUNNER.status()
            text = ""
            if st.get("log") and Path(str(st["log"])).is_file():
                text = Path(str(st["log"])).read_text(encoding="utf-8", errors="replace")
            full = "full=1" in (query or "")
            body = text if full else text[-int(200_000):]
            self._send(200, body.encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._json(404, {"ok": False, "error": f"未知路径 {path}"})

    def _switch_project(self, query: str) -> None:
        """?project=<name> 切换本服务绑定的 ini（只接受 G:\\<name>\\settings.ini 存在的工程）。"""
        if not query:
            return
        from urllib.parse import parse_qs
        name = (parse_qs(query).get("project") or [""])[0]
        if not name or not name.isascii() or not name.replace("_", "").isalnum():
            return
        cand = LAB / name / "settings.ini"
        if cand.is_file():
            Handler.ini = cand

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if not self._origin_ok():
            self._json(403, {"ok": False, "error": "origin 不允许"})
            return
        if not self._authed():
            self._json(401, {"ok": False, "error": "缺少或错误的 X-Token"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw or "{}")
        except ValueError:
            self._json(400, {"ok": False, "error": "请求体不是 JSON"})
            return
        try:
            if path == "/api/config":
                self._json(200, save_payload(self.ini, payload.get("values") or {}))
                return
            if path == "/api/run":
                values = read_ini(self.ini)
                proj = str(values.get("PROJECT") or self.ini.parent.name)
                action = str(payload.get("action") or "")
                full = action_config(self.ini, proj)
                acts = {a["id"]: a for a in actions_for(proj, self.ini)}
                act = acts.get(action)
                if act is None:
                    raise ValueError(f"未知动作: {action}")
                if not act.get("enabled"):
                    raise ValueError("该动作已在 actions.ini 里禁用（ENABLED = false）")
                need = full.get("confirm") or {}
                if need.get("REQUIRE", True):
                    phrase = str(need.get("PHRASE") or proj)
                    if str(payload.get("confirm") or "").strip() != phrase:
                        self._json(400, {"ok": False,
                                         "error": f"二次确认未通过：请原样输入 {phrase}"})
                        return
                name, argv, cwd = build_argv(action, proj, self.ini, full)
                bak = backup_dir(self.ini, values)
                log = bak / f"run-{datetime.now(TZ):%Y%m%d-%H%M%S}.log"
                self._json(200, {"ok": True, **RUNNER.start(name, argv, cwd, log)})
                return
            if path == "/api/run/stop":
                self._json(200, {"ok": True, **RUNNER.stop()})
                return
            if path == "/api/logs/clean":
                values = read_ini(self.ini)
                proj = str(values.get("PROJECT") or self.ini.parent.name)
                from ..actions_config import load_console
                keep = int(load_console(self.ini, proj).get("KEEP_LOG_FILES") or 20)
                bak = backup_dir(self.ini, values)
                logs = sorted(bak.glob("run-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
                removed = 0
                freed = 0
                for old in logs[keep:]:
                    try:
                        freed += old.stat().st_size
                        old.unlink()
                        removed += 1
                    except OSError:
                        pass
                self._json(200, {"ok": True, "removed": removed, "kept": min(len(logs), keep),
                                 "freed_kb": round(freed / 1024, 1), "dir": str(bak)})
                return
            if path == "/api/shutdown":
                self._json(200, {"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self._json(404, {"ok": False, "error": f"未知路径 {path}"})
        except Exception as exc:
            self._json(400, {"ok": False, "error": str(exc)})


def serve(ini: Path, prefer_port: int | None = None, open_browser: bool = False) -> int:
    """启动服务（阻塞）。写 STATE_FILE 供启动器/下次复用。"""
    ini = ini.resolve()
    if not ini.is_file():
        print(f"找不到 ini: {ini}")
        return 1
    from ..actions_config import ensure_files
    proj = str(read_ini(ini).get("PROJECT") or ini.parent.name)
    actions_path, console_path = ensure_files(ini, proj)
    print(f"动作策略 {actions_path.name} ｜ 输出面板 {console_path.name}")
    Handler.ini = ini
    ports = ([prefer_port] if prefer_port else []) + PORTS
    httpd = None
    for port in ports:
        try:
            httpd = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if httpd is None:
        print("8791–8799 端口都被占用")
        return 1
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"port": httpd.server_port, "pid": os.getpid(),
                                      "ini": str(ini), "token": TOKEN},
                                     ensure_ascii=False), encoding="utf-8")
    url = f"http://{HOST}:{httpd.server_port}/"
    print(f"控制台 {url}  ini {ini}")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass
    return 0

