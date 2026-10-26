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
import inspect
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

_RUN_LOCK = _state.RUN_LOCK   # 同一把锁
_RUN = _state.RUN             # 同一个 dict（只 update，不重绑）

# ── paths ──────────────────────────────────────────────────────
def _resolve(p: str | Path) -> Path:
    return _paths.resolve_path(p)

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

# ── overlay ────────────────────────────────────────────────────
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

# ── writeback settings.py ──────────────────────────────────────
# ── projects / data ────────────────────────────────────────────
_PREVIEW_CACHE = _state.PREVIEW_CACHE  # 同一个 dict

def _parse_pair_list(raw) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in list(raw or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = str(item[0]).strip(), str(item[1]).strip()
        elif isinstance(item, str) and "vs" in item.lower():
            a, b = [x.strip() for x in item.replace("VS", "vs").split("vs", 1)]
        else:
            continue
        if not a or not b or a == b:
            continue
        key = f"{a}\0{b}"
        if key in seen:
            continue
        seen.add(key)
        out.append((a, b))
    return out

def _levels_from_outcome_dist(
    preview: dict[str, Any], outcome: str = "", subgroup: str = ""
) -> list[str]:
    """亚组水平：优先 SUBGROUP_COL，否则该结局列。"""
    skip = {"", "nan", "none", "nat", "<na>"}
    col = str(subgroup or "").strip() or str(outcome or "").strip()
    clin = preview.get("clin_dist") or {}
    dist = preview.get("outcome_dist") or {}
    src = None
    if col and isinstance(clin.get(col), dict):
        src = clin[col]
    elif col and isinstance(dist.get(col), dict):
        src = dist[col]
    if src is not None:
        return _uniq_keep_str(
            k for k in src
            if str(k).strip() and str(k).strip().lower() not in skip
        )
    return _uniq_keep_str(preview.get("group_levels") or [])

def _levels_for_pairs(
    merged: dict[str, Any], preview: dict[str, Any], outcome: str = ""
) -> list[str]:
    keep = [str(x).strip() for x in (merged.get("GROUP_KEEP") or []) if str(x).strip()]
    if len(keep) >= 2:
        return _uniq_keep_str(keep)
    extra: list[str] = []
    for a, b in _parse_pair_list(merged.get("PAIRWISE_GROUPS")):
        extra.extend([a, b])
    sub = str(merged.get("SUBGROUP_COL") or "").strip()
    prev = _levels_from_outcome_dist(preview, outcome, sub)
    return _uniq_keep_str(prev + extra)

def resolve_dry_pairs(
    merged: dict[str, Any], preview: dict[str, Any], outcome: str = ""
) -> list[tuple[str, str]]:
    parsed = _parse_pair_list(merged.get("PAIRWISE_GROUPS"))
    if parsed:
        return parsed
    if not bool(merged.get("PAIRWISE_ALL")):
        return []
    labels = _levels_for_pairs(merged, preview, outcome)
    if len(labels) < 2:
        return []
    return [(a, b) for i, a in enumerate(labels) for b in labels[i + 1 :]]

def _pair_display(merged: dict[str, Any], outcome: str, pos: str, neg: str) -> tuple[str, str]:
    """对子展示名：PAIRWISE_OVERRIDES > GROUP_LEVEL_DISPLAY > 列值。"""
    from modules.config.outcome import pair_key, level_display_name

    pw = (merged.get("PAIRWISE_OVERRIDES") or {}).get(outcome) or {}
    ov = pw.get(pair_key(pos, neg)) if isinstance(pw, dict) else None
    ov = ov if isinstance(ov, dict) else {}
    mp = merged.get("GROUP_LEVEL_DISPLAY") or {}
    dp = ov.get("GROUP_DISPLAY_POS")
    dn = ov.get("GROUP_DISPLAY_NEG")
    pos_d = str(dp).strip() if dp not in (None, "") else level_display_name(pos, mapping=mp)
    neg_d = str(dn).strip() if dn not in (None, "") else level_display_name(neg, mapping=mp)
    return pos_d, neg_d

def _outcome_cfg(merged: dict[str, Any], outcome: str) -> dict[str, Any]:
    out = dict(merged)
    ov = (merged.get("OUTCOME_OVERRIDES") or {}).get(outcome) or {}
    if isinstance(ov, dict):
        out.update(ov)
    return out

def _pair_n(preview: dict[str, Any], pos: str, neg: str, outcome: str = "") -> str:
    dist = preview.get("outcome_dist") or {}
    vc = dist.get(outcome) if outcome else None
    if not isinstance(vc, dict):
        vc = next((v for v in dist.values() if isinstance(v, dict)), {}) or {}
    counts: dict[str, int] = {}
    for k, n in vc.items():
        try:
            counts[str(k)] = int(n)
        except (TypeError, ValueError):
            continue
    if pos in counts or neg in counts:
        return f"n≈{counts.get(pos, 0)}/{counts.get(neg, 0)}"
    return ""

def _exclude_file_log(raw: Any) -> str:
    """日志里的排除文件名。一个路径或「a.xlsx + b.xlsx」。"""
    if isinstance(raw, (list, tuple)):
        names = [Path(str(x)).name for x in raw if str(x).strip()]
        return " + ".join(names) if names else "false_classification.xlsx"
    text = str(raw or "").strip()
    return text or "false_classification.xlsx"

def plan_dry_dests(merged: dict[str, Any], preview: dict[str, Any]) -> list[dict[str, Any]]:
    from modules.config.outcome import (
        map_outcome_out_dir, pair_dir_name, results_html_name, safe_dir_token,
        subproject_root,
    )

    outcomes = [str(x).strip() for x in (merged.get("OUTCOME_COLS") or []) if str(x).strip()]
    if not outcomes:
        outcomes = [str(merged.get("GROUP_SOURCE_COL") or "").strip() or "Group"]
    mapping = merged.get("SUBPROJECT_BY_OUTCOME") or {}
    if not isinstance(mapping, dict):
        mapping = {}
    use_subdir = bool(merged.get("OUTCOME_OUT_SUBDIR", True))
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        cfg_o = _outcome_cfg(merged, outcome)
        parent = map_outcome_out_dir(outcome, "", mapping, use_subdir)
        pairs = resolve_dry_pairs(cfg_o, preview, outcome)
        if not pairs:
            sub = subproject_root(outcome, parent, mapping, use_subdir) or outcome
            html = results_html_name(sub)
            dest = str(parent or outcome or "<工程根>").replace("\\", "/")
            rows.append({
                "outcome": outcome,
                "pair": None,
                "pos": "",
                "neg": "",
                "disp_pos": "",
                "disp_neg": "",
                "dest": dest,
                "html": f"{dest}/{html}" if dest and dest != "<工程根>" else html,
                "n": "",
                "tabs": False,
            })
            continue
        tabs = len(pairs) >= 3
        for pos, neg in pairs:
            disp_pos, disp_neg = _pair_display(merged, outcome, pos, neg)
            folder = pair_dir_name(disp_pos, disp_neg)
            dest = f"{parent}/{folder}" if parent else folder
            dest = dest.replace("\\", "/")
            html = f"{safe_dir_token(f'{disp_pos}_vs_{disp_neg}')}-results.html"
            rows.append({
                "outcome": outcome,
                "pair": (pos, neg),
                "pos": pos,
                "neg": neg,
                "disp_pos": disp_pos,
                "disp_neg": disp_neg,
                "dest": dest,
                "html": f"{dest}/{html}",
                "n": _pair_n(preview, pos, neg, outcome),
                "tabs": tabs,
            })
    return rows

def _td_roc_name_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(x).strip() if x is not None else "" for x in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

def _td_roc_dry_lines(merged: dict[str, Any], clin_cols: list[str]) -> list[str]:
    from modules.config.ini_io import coerce_ipcw_horizons, coerce_td_roc_field
    from modules.stats.data_io import landmark_month_from_status, landmark_pairs

    cols = {str(c) for c in clin_cols}
    listed = ", ".join(clin_cols) if clin_cols else "（预览未提供临床列）"
    lines: list[str] = []

    if not merged.get("DO_LANDMARK_ROC"):
        lines.append("Landmark ROC: off")
    else:
        try:
            times = _td_roc_name_list(
                coerce_td_roc_field("TD_ROC_TIMES", merged.get("TD_ROC_TIMES", ""))
            )
            stats = _td_roc_name_list(
                coerce_td_roc_field("TD_ROC_STATUS_COLS", merged.get("TD_ROC_STATUS_COLS", ""))
            )
            pairs = landmark_pairs(times, stats)
        except ValueError as e:
            lines.append(f"Landmark ROC: on  ⚠ {e}")
            pairs = None
        if pairs is not None and not pairs:
            lines.append(
                "Landmark ROC: on  ⚠ 未配置时间列/状态列对（TD_ROC_TIMES / TD_ROC_STATUS_COLS）"
            )
        elif pairs:
            month_errs: list[str] = []
            bits: list[str] = []
            for p in pairs:
                if p["time_col"]:
                    bits.append(f"{p['time_col']}→{p['status_col']}")
                    continue
                try:
                    m = landmark_month_from_status(p["status_col"])
                    bits.append(f"{m:g}m→{p['status_col']}")
                except ValueError as e:
                    month_errs.append(str(e))
                    bits.append(f"?→{p['status_col']}")
            missing = [
                name for p in pairs for name in (p["time_col"], p["status_col"])
                if name and name not in cols
            ]
            line = f"Landmark ROC: on  pairs={len(pairs)}  " + "; ".join(bits)
            if month_errs:
                line += "  ⚠ " + "；".join(month_errs)
            if missing:
                line += f"  ⚠ 缺列 {missing!r}。临床列: {listed}"
            elif not clin_cols:
                line += f"  ⚠ 临床列未知，无法核对缺列。临床列: {listed}"
            lines.append(line)

    if not merged.get("DO_TIME_DEPENDENT_ROC"):
        lines.append("时间依赖性 ROC（IPCW）: off")
        return lines

    time_col = str(merged.get("TD_IPCW_TIME_COL") or "").strip()
    status_col = str(merged.get("TD_IPCW_STATUS_COL") or "").strip()
    try:
        horizons = coerce_ipcw_horizons(merged.get("TD_IPCW_HORIZONS"))
    except ValueError as e:
        lines.append(f"时间依赖性 ROC（IPCW）: on  ⚠ {e}")
        return lines
    if not time_col or not status_col or not horizons:
        lines.append(
            "时间依赖性 ROC（IPCW）: on  ⚠ 须填 TD_IPCW_TIME_COL、"
            f"TD_IPCW_STATUS_COL、TD_IPCW_HORIZONS。"
            f"当前时间列 {time_col!r}，状态列 {status_col!r}，评估月 {horizons!r}"
        )
        return lines
    missing = [c for c in (time_col, status_col) if c not in cols]
    line = (
        f"时间依赖性 ROC（IPCW）: on  time={time_col!r}  "
        f"status={status_col!r}  horizons={horizons}"
    )
    if missing:
        line += f"  ⚠ 缺列 {missing!r}。临床列: {listed}"
    elif not clin_cols:
        line += f"  ⚠ 临床列未知，无法核对缺列。临床列: {listed}"
    lines.append(line)
    return lines

def dry_run_text(project: str, data: Path, settings_path: Path,
                 settings: dict[str, Any], overlay: dict[str, Any],
                 preview: dict[str, Any],
                 run_outcome: str = "") -> str:
    from modules.config.ini_io import migrate_retired_settings

    merged = migrate_retired_settings({**settings, **overlay})
    hydrate_console_settings(merged)
    outcomes = [str(x).strip() for x in (merged.get("OUTCOME_COLS") or []) if str(x).strip()]
    run_one = str(run_outcome or "").strip()
    if run_one:
        outcomes = [run_one]
    levels = _levels_from_outcome_dist(
        preview,
        str(merged.get("GROUP_SOURCE_COL") or "").strip(),
        str(merged.get("SUBGROUP_COL") or "").strip(),
    )
    if not levels:
        levels = [str(x) for x in (preview.get("group_levels") or []) if str(x)]
    plan = plan_dry_dests(merged, preview)
    if run_one:
        plan = [r for r in plan if r.get("outcome") == run_one]
    n_diff = sum(1 for k, v in overlay.items() if jsonable(settings.get(k)) != jsonable(v))
    src_label = "settings.ini" if str(settings_path).lower().endswith(".ini") else "settings.py"

    lines = [
        "═══ DRY-RUN（不建模、不写图）═══",
        f"project     : {project}",
        f"data        : {data}",
        f"{src_label:<12}: {settings_path}",
        f"overlay     : 提交 {len(overlay)} 键，其中 {n_diff} 项与磁盘配置不同（页面优先）",
        f"run scope   : {'子结局 ' + run_one if run_one else '公共页 · 全部结局'}",
        f"outcomes    : {outcomes or '(none)'}",
        f"seed / split: {merged.get('SEED')} / {merged.get('TRAIN_RATIO')}"
        + (
            f"  SPLIT_COL={merged.get('SPLIT_COL')}"
            if str(merged.get("SPLIT_COL") or "").strip()
            else ""
        ),
        f"VAL_MODE    : {merged.get('VAL_MODE', 'refit')}",
        "",
        "sheets:",
    ]
    for s in preview.get("sheets") or []:
        inf = (preview.get("sheet_info") or {}).get(s) or {}
        lines.append(f"  - {s}: {inf.get('n_rows')} × {inf.get('n_cols')}")
    idc = preview.get("id_check") or {}
    if idc:
        lines += [
            "",
            f"ID  {idc.get('clin_id_col')} ↔ {idc.get('rad_id_col')}: "
            f"clin={idc.get('clin_n')} rad={idc.get('rad_n')} "
            f"overlap={idc.get('overlap')} "
            f"clin_only={idc.get('clin_only')} rad_only={idc.get('rad_only')}",
        ]
    exc = preview.get("exclude") or {}
    row_s = exc.get("row") if exc.get("row") not in (None, "", []) else merged.get("EXCLUDE_ROW") or "（整列当 ID）"
    lines.append(
        f"exclude     : n={exc.get('n', 0)}  DO_EXCLUDE={merged.get('DO_EXCLUDE_SAMPLES')}  "
        f"file={exc.get('file') or _exclude_file_log(merged.get('EXCLUDE_FILE'))}  "
        f"sheet={exc.get('sheet') or merged.get('EXCLUDE_SHEET')}  "
        f"col={exc.get('col') or merged.get('EXCLUDE_COL')}  "
        f"row={row_s}"
    )
    lines.append(
        f"survival    : DO_SURVIVAL={merged.get('DO_SURVIVAL', False)}  "
        f"time_col={merged.get('SURVIVAL_TIME_COL') or '（未选）'}  "
        f"status={merged.get('TD_IPCW_STATUS_COL') or '（未选，KM 用当前结局）'}  "
        f"unit={merged.get('SURVIVAL_TIME_UNIT', 'months')}  "
        f"by_risk={merged.get('DO_SURVIVAL_BY_RISK', True)}  "
        f"cox={merged.get('DO_COX', False)}"
    )
    lines += _td_roc_dry_lines(merged, [str(c) for c in (preview.get("clin_cols") or [])])
    lines.append(
        f"fig palette : {merged.get('FIG_PALETTE') or 'npg'}  "
        f"main={merged.get('FIG_COLORS_MAIN')}  aux={merged.get('FIG_COLORS_AUX')}"
    )
    if preview.get("n_after_exclude_est") is not None:
        lines.append(f"n(overlap−exc) ≈ {preview['n_after_exclude_est']}")
    if preview.get("outcome_dist"):
        lines.append("outcome dist:")
        for col, dist in preview["outcome_dist"].items():
            bits = ", ".join(f"{k}={v}" for k, v in dist.items())
            lines.append(f"  {col}: {bits}")

    lines.append("")
    lines.append("分组:")
    lines.append(f"  检出水平   : {', '.join(levels) if levels else '（预览未检出，先载入数据）'}")
    if merged.get("GROUP_FROM_ID"):
        lines.append(
            "  ⚠ GROUP_FROM_ID=true（控制台已撤下此项；仍会从 ID 前缀覆盖 Group）"
        )
    else:
        src = str(merged.get("GROUP_SOURCE_COL") or "").strip()
        if not src and len(outcomes) == 1:
            src = outcomes[0]
        if len(outcomes) > 1 and not src:
            src = "各结局列（进入该结局后用该列水平）"
        sub = str(merged.get("SUBGROUP_COL") or "").strip()
        src_col = sub or src or "当前结局列"
        lines.append(
            f"  来源       : {src_col}"
            + ("（亚组列）" if sub else "（子结局列；不用公共 Group）")
        )
    n_pairs = sum(1 for r in plan if r.get("pair"))
    if n_pairs:
        lines.append(f"  两两比较   : {n_pairs} 对（阳/阴按 PAIRWISE_GROUPS 顺序；各对可改）")
        if any(r.get("tabs") for r in plan):
            lines.append("  控制台分页 : ≥3 对 → 下行 公共 | 各对（标签用展示名）")
        level_set = set(levels)
        missing = []
        for r in plan:
            if not r.get("pair"):
                continue
            if r["pos"] not in level_set:
                missing.append(r["pos"])
            if r["neg"] not in level_set:
                missing.append(r["neg"])
        missing = _uniq_keep_str(missing)
        if levels and missing:
            lines.append(
                "  ⚠ 对子列值不在 Group 检出水平里: "
                + "、".join(missing)
                + "。GROUP_FROM_ID 已关，按这些字符串筛行会 n=0。"
                + " 请把 PAIRWISE_GROUPS 改成检出全称，展示名可另写 CCC / ECC。"
            )
    else:
        keep = [str(x).strip() for x in (merged.get("GROUP_KEEP") or []) if str(x).strip()]
        if keep:
            lines.append(f"  行筛选     : GROUP_KEEP={keep}")
        else:
            lines.append("  行筛选     : 不筛（全部组）")

    icc = preview.get("icc") or {}
    if icc:
        lines.append(
            f"ICC         : n={icc.get('n')}  ≥{icc.get('threshold')} both="
            f"{icc.get('both_ge_thr', '—')}"
        )
    lines.append("")
    lines.append("DO_* 生效值:")
    for k in sorted(merged):
        if k.startswith("DO_") or k in ("ENABLE_CORR_FILTER", "ICC_THRESHOLD"):
            mark = " ◆" if k in overlay and overlay[k] != settings.get(k) else ""
            lines.append(f"  {k}={merged[k]}{mark}")

    lines += ["", "将写入:"]
    if merged.get("DO_QC_REPORT", True) and str(merged.get("QC_SHEET") or "").strip():
        lines.append("  ref/qc.html   ← 工程 ref/")
    from modules.utils.u_qc import _external_specs_from_settings, qc_html_rel, external_qc_html_name
    for spec in _external_specs_from_settings(merged):
        lines.append(
            f"  {qc_html_rel(external_qc_html_name(spec['name']))}  ← 工程 ref/ 外验 QC"
            f"  [{spec['name']}] {spec['file']}"
        )
    if not plan:
        lines.append("  （无结局、无对子）")
    else:
        cur_out = None
        for r in plan:
            if r["outcome"] != cur_out:
                cur_out = r["outcome"]
                lines.append(f"  结局 {cur_out}")
            if r["pair"]:
                nbit = f"  {r['n']}" if r["n"] else ""
                lines.append(
                    f"    {r['pos']} vs {r['neg']}  →  {r['dest']}/{r['html'].rsplit('/', 1)[-1]}"
                    f"  展示 {r['disp_pos']} / {r['disp_neg']}{nbit}"
                )
            else:
                lines.append(f"    {r['html']}")
    lines.append("═══ DRY-RUN 结束 ═══")
    return "\n".join(lines)

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

def start_run(project: str, data: Path, settings_path: Path,
              overlay: dict[str, Any], dry_run: bool,
              run_outcome: str = "") -> dict[str, Any]:
    with _RUN_LOCK:
        if _RUN["status"] == "running" and _RUN.get("proc") is not None:
            proc = _RUN["proc"]
            if proc.poll() is None:
                raise RuntimeError("已有任务在跑，请先等待或停止")
        proj = project_dir(project)
        from modules.config.ini_io import project_ref_dir
        ref = project_ref_dir(proj)
        log_path = ref / ("console_dryrun.log" if dry_run else "pipeline_run.log")
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
        from modules.config.ini_io import project_ref_dir
        ref = project_ref_dir(proj)
        log_path = ref / "pipeline_run.log"
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
