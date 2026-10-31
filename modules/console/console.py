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
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    run_log_path,
    run_pipeline_inprocess,
    run_project_data_qc,
    run_status,
    start_run,
    start_run_bat,
    stop_run,
    wrap_set_outcome,
)

# 端口 / 服务层实现在 serve.py；此处回导同名符号。
from modules.console.serve import (  # noqa: E402,F401
    _already_serving,
    _command_lines,
    _listen_map,
    _listen_pid,
    _parse_listen_table,
    _pid_is_console,
    _port_band,
    _process_label,
    _reclaim_dead_port,
    _serve_url,
    _service_label,
    _tcp_open,
    _wait_port_free,
    describe_port,
    pick_port,
    publish_local,
    radconsole_action,
    register_protocol,
    spawn_successor,
    stop_console_port,
    stop_console_ports,
    stop_from_query,
)

# HTTP Handler / Server 实现：真 import（B4c7 起不再 install 注入）
from modules.console.http_impl import ConsoleServer, Handler  # noqa: E402
from modules.console import registry as project_registry  # noqa: E402
from modules.console import warmup as console_warmup  # noqa: E402

_RUN_LOCK = _state.RUN_LOCK   # 同一把锁
_RUN = _state.RUN             # 同一个 dict（只 update，不重绑）

# ── paths ──────────────────────────────────────────────────────
def _resolve(p: str | Path) -> Path:
    return _paths.resolve_path(p)

# ── state 别名 ───────────────────────────────────────────────────
_PREVIEW_CACHE = _state.PREVIEW_CACHE  # 同一个 dict

# ── CLI：serve / run（端口与服务 helper 见 serve.py）────────────

def _warmup_then_log(project: str | None, data: str | None) -> None:
    console_warmup.run(project, data)
    state = console_warmup.STATE
    if state["error"]:
        print(f"[warmup] 失败\n{state['error']}", flush=True)
    else:
        print(f"[warmup] {state['detail']}", flush=True)


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
    threading.Thread(
        target=_warmup_then_log, args=(project, data), daemon=True
    ).start()
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

def register_project(
    project: str,
    directory: str = "",
    *,
    scopes: list[str] | None = None,
    rotate: bool = False,
) -> dict[str, Any]:
    """登记项目并把 token 写进 <project>.html；CLI 与 radconsole://register 共用。"""
    key = str(project or "").strip()
    if not key:
        raise ValueError("未指定 project")
    proj = project_dir(key, directory)
    starter = Path(proj) / f"{key}.html"
    entry, token = project_registry.register(
        key,
        proj,
        scopes=scopes or None,
        rotate=rotate,
        starter_html=str(starter) if starter.is_file() else "",
    )
    out: dict[str, Any] = {
        "ok": True,
        "op": "register",
        "project": entry.project,
        "dir": entry.dir,
        "scopes": entry.scopes,
        "starter": str(starter),
        "action": "",
        "token_written": False,
    }
    if token and starter.is_file():
        out["action"] = project_registry.inject_token(starter, token)
        out["token_written"] = True
    elif token:
        out["token"] = token
    return out


def cmd_register(args: argparse.Namespace) -> int:
    scopes = [s.strip() for s in str(getattr(args, "scopes", "") or "").split(",") if s.strip()]
    result = register_project(
        str(args.project or "").strip(),
        getattr(args, "dir", "") or "",
        scopes=scopes or None,
        rotate=bool(getattr(args, "rotate", False)),
    )
    print(f"已登记 {result['project']} → {result['dir']}", flush=True)
    print(f"scopes  {','.join(result['scopes'])}", flush=True)
    if result["token_written"]:
        print(f"token 已写入启动页（{result['action']}）: {result['starter']}", flush=True)
    elif result.get("token"):
        print(f"缺启动页 {result['starter']}；token 只出现在本次输出，请手工写入页面 meta", flush=True)
        print(f"TOKEN {result['token']}", flush=True)
    else:
        print("token 未轮换（页面里旧 token 继续有效）；要重写页面请加 --rotate", flush=True)
    print(f"注册表 {project_registry.path()}", flush=True)
    return 0


def cmd_unregister(args: argparse.Namespace) -> int:
    project = str(args.project or "").strip()
    entry = project_registry.get(project)
    starter = Path(entry.starter_html) if entry and entry.starter_html else None
    if not project_registry.unregister(project):
        print(f"未登记: {project}", flush=True)
        return 1
    if starter and starter.is_file():
        project_registry.clear_token(starter)
        print(f"已抹掉页面 token: {starter}", flush=True)
    print(f"已注销 {project}", flush=True)
    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    rows = project_registry.listing()
    if not rows:
        print("注册表为空", flush=True)
        return 0
    for row in rows:
        state = "ok" if row.get("dir_ok") else "STALE"
        print(
            f"{row['project']:<14} {state:<6} {','.join(row.get('scopes') or []):<22} {row.get('dir')}",
            flush=True,
        )
    print(f"注册表 {project_registry.path()}", flush=True)
    return 0


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
        if action in ("register", "unregister"):
            project_q = (qs.get("project") or [""])[0]
            dir_q = (qs.get("dir") or [""])[0]
            try:
                if action == "unregister":
                    entry = project_registry.get(project_q)
                    starter = Path(entry.starter_html) if entry and entry.starter_html else None
                    removed = project_registry.unregister(project_q)
                    if starter and starter.is_file():
                        project_registry.clear_token(starter)
                    result = {
                        "ok": removed,
                        "op": "unregister",
                        "project": project_q,
                        "starter": str(starter or ""),
                        "error": "" if removed else "未登记",
                    }
                else:
                    scopes_q = (qs.get("scopes") or [""])[0]
                    scopes = [s.strip() for s in scopes_q.split(",") if s.strip()] or None
                    result = register_project(
                        project_q, dir_q, scopes=scopes, rotate=True,
                    )
            except Exception as e:
                result = {"ok": False, "op": action, "project": project_q, "error": str(e)}
            print(json.dumps(result, ensure_ascii=False), flush=True)
            publish_local(result, hold=12)
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

    p_reg = sub.add_parser("register", help="登记项目并给启动页写 token")
    p_reg.add_argument("--project", required=True)
    p_reg.add_argument("--dir", default="", help="显式项目目录；空=按项目名自动查找")
    p_reg.add_argument(
        "--scopes", default="",
        help="逗号分隔 read,write-settings,run；空=默认 read,write-settings",
    )
    p_reg.add_argument("--rotate", action="store_true", help="重发 token 并重写启动页")

    p_unreg = sub.add_parser("unregister", help="注销项目并抹掉启动页 token")
    p_unreg.add_argument("--project", required=True)

    sub.add_parser("registry", help="列出注册表")

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
    if args.cmd == "register":
        sys.exit(cmd_register(args))
    if args.cmd == "unregister":
        sys.exit(cmd_unregister(args))
    if args.cmd == "registry":
        sys.exit(cmd_registry(args))
    parser.print_help()

if __name__ == "__main__":
    main()
