"""控制台运行层（B4c5：从 console.py 逐字搬出）。

依赖 const / ops / settings_layer（路径、overlay、会话）与 state（运行状态指针）；
不 import console，也不 import http_impl；console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from modules.console import state as _state
from modules.console.const import MODULES_ROOT, OVERRIDE_PATH, SET_OUTCOME_DERIVED
from modules.console.ops import _overlay_path, jsonable, write_project_overlay
from modules.console.settings_layer import normalize_overlay, project_dir, save_session

_IMPORT_ROOT = Path(__file__).resolve().parents[2]  # 与 console.py 同一仓库根
_RUN = _state.RUN
_RUN_LOCK = _state.RUN_LOCK

def resolve_modules_dir(proj: Path) -> Path:
    """MODULES_DIR from the project's ref/settings.ini (required; no default, no fallback)."""
    from modules.config.ini_io import read_ini, resolve_settings_ini
    ini = resolve_settings_ini(proj)
    if not ini.is_file():
        raise FileNotFoundError(f"项目缺少 settings.ini，无法读取 MODULES_DIR: {ini}")
    raw = str(read_ini(ini).get("MODULES_DIR") or "").strip()
    if not raw:
        raise ValueError(f"MODULES_DIR 未在 {ini} 中设置；请填写共享算法库路径（如 {MODULES_ROOT}）")
    p = Path(raw)
    if not p.is_dir():
        raise FileNotFoundError(f"MODULES_DIR 不是现存目录: {raw}（来自 {ini}）")
    return p

def apply_overlay_to_modules(overlay: dict[str, Any], skip: set[str] | None = None) -> int:
    """把 overlay 写进已 import 的 modules.config 与 settings。"""
    from modules import config as cfg
    from modules.config import settings as S

    skip = skip or set()
    n = 0
    ov = normalize_overlay(overlay)
    for k, v in ov.items():
        if k.startswith("_") or k in skip or not str(k).isidentifier():
            continue
        if inspect.isfunction(getattr(cfg, k, None)):
            continue
        setattr(cfg, k, v)
        setattr(S, k, v)
        n += 1
    out_dir = getattr(cfg, "OUT_DIR", None)
    if out_dir:
        png = os.path.join(str(out_dir), "PNG")
        cfg.PNG_DIR = png
        S.PNG_DIR = png
    return n

def wrap_set_outcome(overlay: dict[str, Any]) -> None:
    from modules import config as cfg

    orig = getattr(cfg, "set_outcome", None)
    if orig is None:
        return

    def _wrapped(outcome: str, out_dir_base: str | None = None):
        orig(outcome, out_dir_base)
        skip = set(SET_OUTCOME_DERIVED)
        # 空列表不要盖掉 set_outcome 按旧字典补上的对照列
        if not list(overlay.get("FORCE_CLIN_FOR_ROC") or []):
            skip.add("FORCE_CLIN_FOR_ROC")
        apply_overlay_to_modules(overlay, skip=skip)

    cfg.set_outcome = _wrapped  # type: ignore[method-assign]

def run_pipeline_inprocess(project: str, data: Path, overlay: dict[str, Any],
                           dry_run: bool, log_path: Path | None,
                           outcome: str = "") -> int:
    proj = project_dir(project)
    os.chdir(proj)
    os.environ["RAD_PROJECT"] = str(proj)
    os.environ["RAD_MODULES"] = str(resolve_modules_dir(proj))
    if str(_IMPORT_ROOT) not in sys.path:
        sys.path.insert(0, str(_IMPORT_ROOT))
    sys.path.insert(0, str(proj))
    # drop previously imported project modules
    drop = [k for k in list(sys.modules)
            if k == "modules" or k.startswith("modules.")]
    for k in drop:
        sys.modules.pop(k, None)

    payload = {
        "project": project,
        "data": str(data),
        "overlay": jsonable(normalize_overlay(overlay)),
    }
    ov_path = write_project_overlay(proj, payload)
    os.environ["RAD_CONSOLE_OVERLAY"] = str(ov_path)

    import modules.config  # noqa: F401
    n = apply_overlay_to_modules(overlay)
    wrap_set_outcome(overlay)
    print(f"  ◆ Console overlay applied: {n} keys  (HTML/JSON 优先于 settings.py)")
    print(f"  ◆ Overlay file: {ov_path}")

    if dry_run:
        print("  ◆ --dry-run: invoke pipeline.main(--dry-run)")
        from modules import pipeline as P
        argv = ["--input", str(data), "--dry-run"]
        one = str(outcome or "").strip()
        if one:
            argv += ["--outcome", one]
        if log_path:
            argv += ["--log", str(log_path)]
        else:
            argv += ["--no-log"]
        P.main(argv)
        return 0

    from modules import pipeline as P
    argv = ["--input", str(data)]
    one = str(outcome or "").strip()
    if one:
        argv += ["--outcome", one]
        print(f"  ◆ Run outcome: {one}  (子结局页只跑这一项)")
    else:
        print("  ◆ Run outcomes: all OUTCOME_COLS  (公共页)")
    # 外层已经把 stdout 收进一份日志；这里再开 pipeline_run.log 会变成第二份。
    if log_path:
        argv += ["--log", str(log_path)]
    else:
        argv += ["--no-log"]
    P.main(argv)
    return 0

def run_project_data_qc(
    project: str,
    data: Path,
    settings: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, Any]:
    """对整表做 QC，写入 ref/qc.html；有外验则另写 ref/qc-ext-*.html。"""
    from modules.utils.u_qc import write_project_data_qc, write_external_qc_reports, qc_html_rel

    proj = project_dir(project)
    merged = dict(settings)
    merged.update(overlay)
    info = write_project_data_qc(data, settings=merged, out_dir=proj, out_html="qc.html")
    info["project"] = project
    info["out_dir"] = str(proj)
    info["rel"] = qc_html_rel("qc.html")
    info["external"] = write_external_qc_reports(
        merged, dest=proj, skip_existing=False, input_dir=proj,
    )
    return info

def _popen_background_kwargs() -> dict[str, Any]:
    """Windows: no new console window; keep stdout/stderr as caller set."""
    kw: dict[str, Any] = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kw["start_new_session"] = True
    return kw

def run_log_path(proj: Path, dry_run: bool = False) -> Path:
    """运行日志一律在工程 ref/pipeline/：pipeline_run.log / console_dryrun.log。"""
    from modules.config.ini_io import project_pipeline_dir
    name = "console_dryrun.log" if dry_run else "pipeline_run.log"
    return project_pipeline_dir(proj) / name


def start_run(project: str, data: Path, settings_path: Path,
              overlay: dict[str, Any], dry_run: bool,
              run_outcome: str = "") -> dict[str, Any]:
    with _RUN_LOCK:
        if _RUN["status"] == "running" and _RUN.get("proc") is not None:
            proc = _RUN["proc"]
            if proc.poll() is None:
                raise RuntimeError("已有任务在跑，请先等待或停止")
        proj = project_dir(project)
        log_path = run_log_path(proj, dry_run)
        ov_payload = {
            "project": project,
            "data": str(data),
            "settings": str(settings_path),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "restore_page": False,
            "overlay": jsonable(normalize_overlay(overlay)),
        }
        save_session(ov_payload)
        write_project_overlay(proj, ov_payload)
        ov_file = OVERRIDE_PATH
        cmd = [
            sys.executable, "-u", str(Path(__file__).resolve()),
            "run",
            "--project", project,
            "--data", str(data),
            "--settings", str(settings_path),
            "--overlay", str(ov_file),
        ]
        if dry_run:
            cmd.append("--dry-run")
        one = str(run_outcome or "").strip()
        if one:
            cmd += ["--outcome", one]
        log_f = open(log_path, "w", encoding="utf-8-sig", newline="\n")
        proc = subprocess.Popen(
            cmd,
            cwd=str(_IMPORT_ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8",
                 "RAD_PROJECT": str(proj),
                 "RAD_MODULES": str(resolve_modules_dir(proj)),
                 "RAD_CONSOLE_OVERLAY": str(_overlay_path(proj))},
            **_popen_background_kwargs(),
        )
        _RUN.update({
            "status": "running",
            "pid": proc.pid,
            "proc": proc,
            "log_f": log_f,
            "log_path": str(log_path),
            "started": time.time(),
            "ended": None,
            "error": "",
            "project": project,
            "dry_run": dry_run,
        })

        def _wait():
            rc = proc.wait()
            try:
                log_f.close()
            except Exception as e:
                print(f"  ⚠ close run log failed: {e}", flush=True)
            with _RUN_LOCK:
                _RUN["ended"] = time.time()
                _RUN["status"] = "ok" if rc == 0 else "error"
                if rc != 0:
                    _RUN["error"] = f"exit {rc}"
                _RUN["proc"] = None

        threading.Thread(target=_wait, daemon=True).start()
        return {
            "status": "running",
            "pid": proc.pid,
            "log_path": str(log_path),
            "dry_run": dry_run,
            "mode": "overlay",
            "run_outcome": one,
        }

def start_run_bat(project: str) -> dict[str, Any]:
    """等价于旧 run.bat：PYTHONPATH=<STATS_ROOT> 且 python -m modules.pipeline --no-overlay。"""
    with _RUN_LOCK:
        if _RUN["status"] == "running" and _RUN.get("proc") is not None:
            proc = _RUN["proc"]
            if proc.poll() is None:
                raise RuntimeError("已有任务在跑，请先等待或停止")
        proj = project_dir(project)
        log_path = run_log_path(proj)
        env = {
            **os.environ,
            "PYTHONPATH": str(_IMPORT_ROOT),
            "PYTHONIOENCODING": "utf-8",
            "RAD_PROJECT": str(proj),
            "RAD_MODULES": str(resolve_modules_dir(proj)),
        }
        env.pop("RAD_CONSOLE_OVERLAY", None)
        cmd = [sys.executable, "-u", "-m", "modules.pipeline", "--no-overlay", "--no-log"]
        log_f = open(log_path, "w", encoding="utf-8-sig", newline="\n")
        log_f.write(
            f"运行统计  {project}\n"
            f"cwd={proj}\n"
            f"cmd={' '.join(cmd)}\n"
            f"{'═' * 40}\n"
        )
        log_f.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=str(proj),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            **_popen_background_kwargs(),
        )
        _RUN.update({
            "status": "running",
            "pid": proc.pid,
            "proc": proc,
            "log_f": log_f,
            "log_path": str(log_path),
            "started": time.time(),
            "ended": None,
            "error": "",
            "project": project,
            "dry_run": False,
            "mode": "bat",
        })

        def _wait() -> None:
            rc = proc.wait()
            try:
                log_f.close()
            except Exception as e:
                print(f"  ⚠ close run log failed: {e}", flush=True)
            with _RUN_LOCK:
                _RUN["ended"] = time.time()
                _RUN["status"] = "ok" if rc == 0 else "error"
                if rc != 0:
                    _RUN["error"] = f"exit {rc}"
                _RUN["proc"] = None

        threading.Thread(target=_wait, daemon=True).start()
        return {
            "status": "running",
            "pid": proc.pid,
            "log_path": str(log_path),
            "dry_run": False,
            "mode": "bat",
        }

def stop_run() -> dict[str, Any]:
    with _RUN_LOCK:
        proc = _RUN.get("proc")
        if proc is None or proc.poll() is not None:
            _RUN["status"] = _RUN["status"] if _RUN["status"] != "running" else "idle"
            return {"status": _RUN["status"], "stopped": False}
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()
        _RUN["status"] = "stopped"
        _RUN["ended"] = time.time()
        _RUN["error"] = "stopped by user"
        return {"status": "stopped", "stopped": True}

def run_status() -> dict[str, Any]:
    with _RUN_LOCK:
        proc = _RUN.get("proc")
        if proc is not None and proc.poll() is not None and _RUN["status"] == "running":
            _RUN["status"] = "ok" if proc.returncode == 0 else "error"
        return {
            "status": _RUN["status"],
            "pid": _RUN.get("pid"),
            "log_path": _RUN.get("log_path") or "",
            "started": _RUN.get("started"),
            "ended": _RUN.get("ended"),
            "error": _RUN.get("error") or "",
            "project": _RUN.get("project") or "",
            "dry_run": bool(_RUN.get("dry_run")),
            "mode": _RUN.get("mode") or "",
        }

def read_log(offset: int = 0, max_bytes: int = 200_000) -> dict[str, Any]:
    path = _RUN.get("log_path") or ""
    if not path or not os.path.isfile(path):
        return {"text": "", "n": 0, "offset": 0, "status": run_status()["status"]}
    size = os.path.getsize(path)
    offset = max(0, int(offset))
    if offset > size:
        offset = size
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
    text = chunk.decode("utf-8-sig", errors="replace")
    return {
        "text": text,
        "n": offset + len(chunk),
        "offset": offset,
        "size": size,
        "status": run_status()["status"],
    }
