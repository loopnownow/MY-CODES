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
import ast
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback
import types
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


from modules.console.schema import (  # noqa: E402
    HINTS,
    HOT_KEYS,
    LABELS,
    SECTION_DEFS,
    _ADDSELECT_LABEL,
    _COLUMN_MULTI,
    _LABEL_DISPLAY_CANDS,
    _MODEL_CHOICES,
    _PICK_XLSX_SCRIPT,
    _PREVIEW_HEAD_MAX_COLS,
    _RESULT_SKIP_DIRS,
    _SHEET_ALIASES,
)

SECTION_OF: dict[str, tuple[str, str]] = {}
for _sid, _title, _keys in SECTION_DEFS:
    for _k in _keys:
        SECTION_OF[_k] = (_sid, _title)

_RUN_LOCK = _state.RUN_LOCK   # 同一把锁
_RUN = _state.RUN             # 同一个 dict（只 update，不重绑）

# ── paths ──────────────────────────────────────────────────────
def _resolve(p: str | Path) -> Path:
    return _paths.resolve_path(p)

def project_roots() -> list[Path]:
    return _paths.collect_project_roots(RAD_ROOT, EXTRA_PROJECT_ROOTS)

def under_root(p: str | Path) -> Path:
    # Use live GROK_ROOT so tests can monkeypatch console.GROK_ROOT.
    return _paths.under_root(p, grok_root=GROK_ROOT)

def under_project(proj: Path, p: str | Path) -> Path:
    return _paths.under_project(proj, p, grok_root=GROK_ROOT)

def project_dir(name: str, explicit_dir: str = "") -> Path:
    return _paths.find_project_dir(
        name,
        grok_root=GROK_ROOT,
        project_roots=project_roots(),
        explicit_dir=explicit_dir,
    )

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

def load_settings_module(settings_path: Path):
    settings_path = under_root(settings_path)
    if settings_path.suffix.lower() == ".ini":
        from modules.config.ini_io import ensure_settings_ini, load_ini_namespace
        ensure_settings_ini(settings_path)
        return load_ini_namespace(settings_path)
    key = "radset_" + hashlib.md5(str(settings_path).encode("utf-8")).hexdigest()[:12]
    sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(key, settings_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {settings_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def public_settings(mod) -> dict[str, Any]:
    from modules.config.ini_io import migrate_retired_settings
    out: dict[str, Any] = {}
    for k, v in vars(mod).items():
        if k.startswith("_") or k in SKIP_SETTING_KEYS:
            continue
        if inspect.isfunction(v) or inspect.ismodule(v) or inspect.isclass(v):
            continue
        if isinstance(v, types.ModuleType):
            continue
        out[k] = v
    out = migrate_retired_settings(out)
    out = hydrate_console_settings(out)
    from modules.config.defaults import DEFAULT_SETTINGS
    for src in (DEFAULT_SETTINGS, CONSOLE_DEFAULTS):
        for k, v in src.items():
            out.setdefault(k, v)
    return out

def _as_name_list(value: Any) -> list[str]:
    """字符串或列表都收成去空、去重的列名。旧 ini 里的单列字符串仍可用。"""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        seen: set[str] = set()
        for x in value:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    s = str(value).strip()
    return [s] if s else []

def _as_prefix_group(value: Any) -> dict[str, Any]:
    """分类名顺序 + 前缀归属。空结构表示仍做一整份 Logistic。"""
    names: list[str] = []
    seen: set[str] = set()
    assign: dict[str, str] = {}
    if isinstance(value, dict):
        for x in value.get("names") or []:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            names.append(s)
        raw = value.get("assign") or {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                p = str(k).strip()
                g = str(v).strip()
                if p and g and g in seen:
                    assign[p] = g
    return {"names": names, "assign": assign}

def hydrate_console_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """把旧键收成控制台现用字段。不删 ini 里的编码映射。"""
    row = settings.get("EXCLUDE_ROW")
    if isinstance(row, (list, tuple)):
        settings["EXCLUDE_ROW"] = str(row[0]).strip() if row else ""
    elif row is None:
        settings["EXCLUDE_ROW"] = ""
    else:
        settings["EXCLUDE_ROW"] = str(row).strip()
    if "EXCLUDE_FILE" in settings:
        from modules.config.ini_io import name_choice
        settings["EXCLUDE_FILE"] = name_choice(settings.get("EXCLUDE_FILE"))
    # 样本排除改为工程级：若顶层为空，从结局覆盖里上收一次，并清掉覆盖里的同名键
    _exc_keys = (
        "DO_EXCLUDE_SAMPLES", "EXCLUDE_FILE", "EXCLUDE_SHEET",
        "EXCLUDE_COL", "EXCLUDE_ROW", "EXCLUDE_IDS",
    )
    ov_all = settings.get("OUTCOME_OVERRIDES")
    if isinstance(ov_all, dict):
        for _oc, o in list(ov_all.items()):
            if not isinstance(o, dict):
                continue
            for k in _exc_keys:
                if k not in o:
                    continue
                top = settings.get(k)
                empty_top = (
                    top is None
                    or top == ""
                    or top == []
                    or (k == "DO_EXCLUDE_SAMPLES" and top is False and o.get(k) is True)
                )
                if k == "DO_EXCLUDE_SAMPLES":
                    if top is None or (
                        top is False and o.get(k) is True
                    ):
                        settings[k] = bool(o.get(k))
                elif empty_top and o.get(k) not in (None, "", []):
                    settings[k] = o.get(k)
                o.pop(k, None)
            if not o:
                ov_all.pop(_oc, None)
    refs = [str(x).strip() for x in (settings.get("CORR_REFS") or []) if str(x).strip()]
    settings["CORR_REFS"] = refs
    if "DO_NOMOGRAM" in settings:
        settings["DO_COMBINED_MODEL"] = bool(settings.get("DO_NOMOGRAM"))
    elif "DO_COMBINED_MODEL" in settings:
        settings["DO_NOMOGRAM"] = bool(settings.get("DO_COMBINED_MODEL"))
    specs = settings.get("EXTERNAL_TESTS") or []
    if isinstance(specs, list) and specs and isinstance(specs[0], dict):
        s0 = specs[0]
        settings.setdefault("EXTERNAL_FILE", str(s0.get("file") or ""))
        settings.setdefault("EXTERNAL_CLIN_SHEET", str(s0.get("clin_sheet") or ""))
        settings.setdefault("EXTERNAL_RAD_SHEET", str(s0.get("rad_sheet") or ""))
    curves = [str(x).strip() for x in (settings.get("DCA_CURVES") or []) if str(x).strip()]
    if not curves and "DCA_CURVES" not in settings:
        settings["DCA_CURVES"] = ["Clinical", "RadScore", "Combined"]
    if not isinstance(settings.get("OUTCOME_OVERRIDES"), dict):
        settings["OUTCOME_OVERRIDES"] = {}
    if not isinstance(settings.get("PAIRWISE_OVERRIDES"), dict):
        settings["PAIRWISE_OVERRIDES"] = {}
    if not isinstance(settings.get("GROUP_LEVEL_DISPLAY"), dict):
        settings["GROUP_LEVEL_DISPLAY"] = {}
    settings["OUTCOME_OUT_SUBDIR"] = True
    # Shared continuous vars: CORR_PREDICTORS↔PARTIAL_X, CORR_REFS↔PARTIAL_Y
    # UI canonical = CORR_*; PARTIAL_* kept in settings.ini for backend/assoc.
    _stock_pred = ["RadScore", "Clinical", "Combined"]
    cx = _as_name_list(settings.get("CORR_PREDICTORS"))
    px = _as_name_list(settings.get("PARTIAL_X"))
    if px and (not cx or cx == _stock_pred) and px != cx:
        cx = list(px)
    settings["CORR_PREDICTORS"] = cx
    settings["PARTIAL_X"] = list(cx)
    cy = _as_name_list(settings.get("CORR_REFS"))
    py = _as_name_list(settings.get("PARTIAL_Y"))
    if py and not cy:
        cy = list(py)
    settings["CORR_REFS"] = cy
    settings["PARTIAL_Y"] = list(cy)
    if "HEATMAP_COLS" in settings:
        settings["HEATMAP_COLS"] = _as_name_list(settings.get("HEATMAP_COLS"))
    if "RAD_FEATURE_PREFIX_EXCLUDE" in settings:
        settings["RAD_FEATURE_PREFIX_EXCLUDE"] = _as_name_list(
            settings.get("RAD_FEATURE_PREFIX_EXCLUDE")
        )
    settings["RAD_FEATURE_PREFIX_GROUP"] = _as_prefix_group(
        settings.get("RAD_FEATURE_PREFIX_GROUP")
    )
    apply_clin_role_priority(settings)
    raw_om = settings.get("ORDINAL_MAX_LEVELS", 5)
    if raw_om in (None, ""):
        settings["ORDINAL_MAX_LEVELS"] = 5
    else:
        try:
            n_om = int(raw_om)
        except (TypeError, ValueError) as e:
            raise ValueError(f"ORDINAL_MAX_LEVELS 须为正整数，当前={raw_om!r}") from e
        if n_om < 1:
            raise ValueError(f"ORDINAL_MAX_LEVELS 须为正整数，当前={n_om}")
        settings["ORDINAL_MAX_LEVELS"] = n_om
    fc_names = _as_name_list(settings.get("CLIN_FORCE_CATEGORICAL"))
    fc_bad = [x for x in fc_names if x.isdigit()]
    if fc_bad:
        raise ValueError(
            "CLIN_FORCE_CATEGORICAL 须为列名，不能写水平数 "
            + ",".join(fc_bad)
            + "。水平数写 ORDINAL_MAX_LEVELS。"
        )
    settings["CLIN_FORCE_CATEGORICAL"] = fc_names
    return settings

def _uniq_keep_str(seq) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def implicit_skip_cols(settings: dict[str, Any]) -> list[str]:
    """ID / 结局列 / Group 默认跳过，不进 Table 1 和 nomogram。姓名只在 LABEL_COL 指向时跳过。"""
    cols: list[str] = []
    for k in ("CLIN_ID_COL", "LABEL_COL"):
        v = str(settings.get(k) or "").strip()
        if v:
            cols.append(v)
    cols.extend(["Patient_ID", "Group"])
    for c in settings.get("OUTCOME_COLS") or []:
        s = str(c).strip()
        if s:
            cols.append(s)
    return _uniq_keep_str(cols)

def apply_clin_role_priority(settings: dict[str, Any]) -> dict[str, Any]:
    """跳过优先于强制剔除：隐含跳过并入 SKIP，重叠从 FORCE_REMOVE 拿掉。"""
    locked = implicit_skip_cols(settings)
    skip = _uniq_keep_str(list(settings.get("SKIP_CLIN_FEATURES") or []) + locked)
    skip_set = set(skip)
    rm = [
        str(x).strip()
        for x in (settings.get("FORCE_REMOVE_FEATURES") or [])
        if str(x).strip() and str(x).strip() not in skip_set
    ]
    settings["SKIP_CLIN_FEATURES"] = skip
    settings["FORCE_REMOVE_FEATURES"] = rm
    return settings

def _section_of(key: str) -> tuple[str, str]:
    if key in SECTION_OF:
        return SECTION_OF[key]
    if (
        key.startswith("SURVIVAL")
        or key.startswith("TD_ROC")
        or key.startswith("TD_IPCW")
        or key in (
            "DO_SURVIVAL", "DO_SURVIVAL_BY_RISK", "DO_COX",
            "DO_LANDMARK_ROC", "DO_TIME_DEPENDENT_ROC",
        )
    ):
        return ("surv", "生存曲线")
    if key.startswith("NRI_"):
        return ("nri", "NRI / IDI")
    if key == "DO_HEATMAP" or key in ("HEATMAP_COLS", "CORR_GROUP"):
        return ("corr", "相关热图")
    if key == "DO_CORR" or key.startswith("CORR_"):
        return ("pcorr", "相关/偏相关分析")
    if (
        key.startswith("ROC_")
        or key in ("FORCE_CLIN_FOR_ROC", "DO_ROC")
    ):
        return ("roc", "ROC")
    if key == "FORCE_MODEL_FEATURES":
        return ("nomo", "列线图")
    if key in ("VAL_MODE", "SUBGROUP_VAL_MODE", "EXTERNAL_VAL_MODE"):
        return ("valmode", "内部验证 / 亚组验证 / 外部验证")
    if key == "DO_EXCLUDE_SAMPLES" or key.startswith("EXCLUDE_"):
        return ("exclude", "样本排除名单")
    if key.startswith("DCA_") or key == "DO_DCA":
        return ("dca", "DCA")
    if key in ("DO_PLOT", "DO_PLOT_TIFF", "DO_PLOT_PDF"):
        return ("fig", "图与显示")
    if key.startswith("DO_") or key.startswith("ENABLE_") or key.startswith("ICC_"):
        return ("switch", "开关")
    if "LASSO" in key or key.startswith("RAD_"):
        return ("rad", "组学 / LASSO")
    if key == "DO_FIGURE_PANELS" or key.startswith("FIGURE_") or key.startswith("FIG_"):
        return ("fig", "图与显示")
    # No UI「其他」section — leftovers map to switch (usually hidden/retired).
    return ("switch", "开关")

def _is_rad_feature(name: str) -> bool:
    s = str(name)
    low = s.lower()
    if s.startswith(_RAD_FEAT_PREFIX) or low.startswith(_RAD_FEAT_PREFIX):
        return True
    return any(
        tok in low
        for tok in ("_glcm_", "_glrlm_", "_glszm_", "_gldm_", "_ngtdm_", "_fbn_", "firstorder")
    )

def _looks_like_id_column(name: str) -> bool:
    """受试者 ID / 姓名列。不把测量特征当成 ID 候选项。"""
    s = str(name or "").strip()
    if not s:
        return False
    low = s.lower().replace(" ", "")
    compact = low.replace("_", "").replace("-", "")
    if compact in {
        "id", "name", "patientid", "pateintid", "subjectid", "caseid",
        "recordid", "sampleid", "fileid", "patient", "subject",
    }:
        return True
    if low.endswith("_id") or low.endswith("-id"):
        return True
    return any(tok in s for tok in ("编号", "姓名", "病案", "患者"))

def _id_dropdown_columns(columns, configured: str = "") -> list[str]:
    """组学 ID 下拉。非特征列很多时只留 ID/姓名，避免一长串特征名把下拉撑坏。"""
    cols = [str(c) for c in (columns or []) if str(c).strip()]
    meta = _rad_meta_cols(cols) if cols else []
    pool = meta or cols
    picked = [c for c in pool if _looks_like_id_column(c)]
    cur = str(configured or "").strip()
    if cur and cur in cols and cur not in picked:
        picked.insert(0, cur)
    if len(pool) <= 12:
        out = list(pool)
        if cur and cur in cols and cur not in out:
            out.insert(0, cur)
        return out
    if not picked and cols:
        picked = [cols[0]]
    return picked

def _column_matches_prefix(name: str, prefix: str) -> bool:
    """与 data_io._column_matches_prefix 同规则。"""
    from modules.stats.data_io import _column_matches_prefix as _match
    return _match(name, prefix)

def _columns_open_for_analysis(columns, clin_cols, prefixes, id_cols=None) -> list[str]:
    """从分析用列里拿掉命中前缀的组学特征。临床列和 ID 列保留。"""
    prefs = [str(p).strip() for p in (prefixes or []) if str(p).strip()]
    names = _column_names(columns)
    if not prefs:
        return names
    clin_set = set(_column_names(clin_cols))
    id_set = {
        "Name", "Patient_ID", "patient_id", "PatientID", "Group", "ID",
        *[str(c).strip() for c in (id_cols or []) if str(c).strip()],
    }
    out: list[str] = []
    for name in names:
        if name in clin_set or name in id_set:
            out.append(name)
            continue
        if any(_column_matches_prefix(name, p) for p in prefs):
            continue
        out.append(name)
    return out

def _column_names(cols) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in cols or []:
        s = str(c).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def _column_multi_pool(section: str, clin_cols, rad_cols, picked) -> tuple[list[str], list[str], str]:
    """临床模块只用 cln；组学模块只用 rad；其余模块两表都要，并带三个模型名。"""
    if section in ("clinprep", "clin"):
        base = _column_names(clin_cols)
        models: list[str] = []
        source = "clin"
    elif section in ("radprep", "rad", "radlog"):
        base = _column_names(rad_cols)
        models = []
        source = "rad"
    else:
        base = _column_names(clin_cols) + _column_names(rad_cols)
        models = list(_MODEL_CHOICES)
        source = "both"
    pool = _uniq_keep(list(models) + base)
    for x in picked or []:
        s = str(x).strip()
        if s and s not in pool:
            pool.append(s)
    return pool, models, source

def _measure_cols(clin_cols, rad_cols) -> list[str]:
    """临床列 + 组学表里的非高维影像组学列。LASSO 特征名不进协方差下拉。"""
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str, skip_rad_feature: bool) -> None:
        s = str(name).strip()
        if not s or s in seen or _looks_like_id_column(s):
            return
        if skip_rad_feature and _is_rad_feature(s):
            return
        seen.add(s)
        out.append(s)

    for c in clin_cols or []:
        _add(c, False)
    for c in rad_cols or []:
        _add(c, True)
    return out

def _rad_meta_cols(columns) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in columns:
        s = str(c)
        if _is_rad_feature(s) or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def _uniq_keep(seq) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        s = str(x)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out

def _widget_for(key: str, value: Any, sheets: list[str], clin_cols: list[str],
                rad_id_cols: list[str], icc_cols: list[str],
                group_levels: list[str] | None = None,
                rad_meta_cols: list[str] | None = None,
                id_options: list[str] | None = None,
                skip_clin: list[str] | None = None,
                locked_skip: list[str] | None = None,
                extra_select: dict[str, list[str]] | None = None,
                rad_sheet: str | None = None,
                measure_cols: list[str] | None = None,
                rad_cols: list[str] | None = None) -> dict[str, Any]:
    group_levels = group_levels or []
    rad_meta_cols = rad_meta_cols or []
    id_options = id_options or []
    rad_cols = rad_cols or []
    sid, stitle = _section_of(key)
    field: dict[str, Any] = {
        "key": key,
        "label": LABELS.get(key, key),
        "help": HINTS.get(key, ""),
        "hot": key in HOT_KEYS,
        "section": sid,
        "section_title": stitle,
        "value": jsonable(value),
    }
    if key == "EXCLUDE_FILE":
        # 可多个文件。须在「值是列表就当 checklist」之前拦住。
        from modules.config.ini_io import name_choice
        extra = (extra_select or {}).get(key) or []
        packed = name_choice(value)
        chosen = packed if isinstance(packed, list) else ([packed] if packed else [])
        opts = [""]
        seen = {""}
        for x in list(extra) + chosen:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            opts.append(s)
        field["widget"] = "growselect"
        field["value"] = packed
        field["options"] = opts
        field["option_labels"] = {"": EXCLUDE_EMPTY_LABELS.get(key, "（空）")}
        field["more_label"] = "（再选一个文件）"
        return field
    if key == "TD_ROC_TIMES":
        # 临床列名，可多列。旧的月份列表在这里报错。生存曲线行不展示这一列。
        from modules.config.ini_io import coerce_td_roc_field
        coerced = coerce_td_roc_field(key, value)
        field["value"] = coerced
        field["widget"] = "growselect"
        field["options"] = [""] + [str(c) for c in clin_cols]
        field["option_labels"] = {"": "（不设）"}
        field["more_label"] = "（再选一列）"
        return field
    if key == "TD_ROC_STATUS_COLS":
        # 与 ROC「额外对照列」相同：已选芯片 + 搜索下拉。单列字符串也收成列表。
        from modules.config.ini_io import coerce_td_roc_field
        picked = _as_name_list(coerce_td_roc_field(key, value))
        pool = [str(c) for c in clin_cols]
        have = set(pool)
        for name in picked:
            if name not in have:
                pool.append(name)
                have.add(name)
        field["value"] = picked
        field["widget"] = "checklist"
        field["pool_from"] = "clin"
        field["option_pool"] = pool
        field["add_options"] = [c for c in pool if c not in set(picked)]
        field["add_label"] = "+ 搜索列名"
        return field
    if key in ("TD_IPCW_TIME_COL", "TD_IPCW_STATUS_COL"):
        cur = str(value or "").strip()
        opts = [""] + [str(c) for c in clin_cols]
        if cur and cur not in opts:
            opts.append(cur)
        field["value"] = cur
        field["widget"] = "select"
        field["options"] = opts
        field["option_labels"] = {"": "（不设）"}
        return field
    if key == "TD_IPCW_HORIZONS":
        from modules.config.ini_io import coerce_ipcw_horizons
        field["value"] = coerce_ipcw_horizons(value)
        field["widget"] = "timeselect"
        field["options"] = [3, 6, 12, 24]
        return field
    if key == "ORDINAL_MAX_LEVELS":
        field["widget"] = "number"
        field["placeholder"] = "5"
        if value is None or value == "":
            field["value"] = 5
        return field
    if key in _COLUMN_MULTI:
        picked = _as_name_list(value)
        pool, models, source = _column_multi_pool(sid, clin_cols, rad_cols, picked)
        skip_set = {str(x) for x in (skip_clin or [])}
        if key in ("FORCE_REMOVE_FEATURES", "FORCE_INTER_FEATURES", "FORCE_MODEL_FEATURES"):
            picked = [x for x in picked if x not in skip_set]
            pool_show = [c for c in pool if c not in skip_set]
        else:
            pool_show = list(pool)
        field["pool_from"] = source
        field["model_options"] = models
        field["option_pool"] = pool_show
        field["value"] = picked
        field["add_options"] = [c for c in pool_show if c not in set(picked)]
        field["locked_options"] = list(locked_skip or []) if key == "SKIP_CLIN_FEATURES" else []
        if key in _ADDSELECT_LABEL:
            field["widget"] = "addselect"
            field["add_label"] = _ADDSELECT_LABEL[key]
        else:
            field["widget"] = "checklist"
            field["add_label"] = "+ 搜索列名"
        return field
    if isinstance(value, bool):
        field["widget"] = "bool"
        return field
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        field["widget"] = "number"
        return field
    if isinstance(value, dict):
        field["widget"] = "json"
        return field
    if isinstance(value, list):
        field["widget"] = "checklist"
        add: list[str] = []
        col_pool = list(clin_cols) or list(rad_meta_cols)
        if key == "GROUP_KEEP":
            add = list(group_levels) if group_levels else list(col_pool)
        elif key in LIST_FROM_RAD_META:
            add = list(rad_meta_cols) or list(rad_id_cols) or list(col_pool)
        elif key in LIST_FROM_CLIN:
            add = list(col_pool)
        elif key == "EXCLUDE_IDS":
            add = list(id_options)
        elif key == "NRI_CUTOFF":
            field["widget"] = "numlist"
            return field
        elif key == "MEDIATION_PATHS":
            field["widget"] = "pathlist"
            field["option_pool"] = _uniq_keep(
                list(col_pool) + ["RadScore", "Group", "Combined"]
            )
            field["value"] = [p if isinstance(p, dict) else {} for p in value]
            return field
        elif key in ("DAG_EDGES", "DAG_MEDIATION_HIGHLIGHT",
                     "EXTERNAL_TESTS", "RAD_FEATURE_DISPLAY_STRIP",
                     "RAD_FEATURE_PREFIX_EXCLUDE",
                     "FIG_COLORS_MAIN", "FIG_COLORS_AUX"):
            field["widget"] = "json"
            return field
        have = [str(x) for x in value]
        field["value"] = have
        pool_all = _uniq_keep(add)
        field["option_pool"] = list(pool_all)
        field["add_options"] = [c for c in pool_all if c not in have]
        if key == "OUTCOME_COLS":
            field["widget"] = "addselect"
            field["add_label"] = _ADDSELECT_LABEL["OUTCOME_COLS"]
            field["locked_options"] = []
        return field
    # str
    field["widget"] = "text"
    if key in EXCLUDE_SELECT_KEYS:
        extra = (extra_select or {}).get(key) or []
        cur_s = "" if value is None else str(value).strip()
        cur_items = [cur_s] if cur_s else []
        opts = [""]
        seen = {""}
        for x in list(extra) + cur_items:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            opts.append(s)
        field["widget"] = "select"
        field["options"] = opts
        field["option_labels"] = {"": EXCLUDE_EMPTY_LABELS.get(key, "（空）")}
        return field
    if key == "EXTERNAL_FILE":
        field["placeholder"] = "留空 = 不做外验"
        return field
    if key == "EXTERNAL_CLIN_SHEET":
        field["placeholder"] = "临床工作表，空=不做外验"
        return field
    if key == "EXTERNAL_RAD_SHEET":
        field["placeholder"] = "组学工作表，空=不做外验"
        return field
    if key == "RAD_MARKER_SIDE":
        field["widget"] = "select"
        field["options"] = ["prefix", "suffix", "both"]
        field["option_labels"] = {
            "prefix": "前缀",
            "suffix": "后缀",
            "both": "二者",
        }
        return field
    if key == "RAD_FEATURE_SOURCE":
        field["widget"] = "select"
        field["options"] = ["both", "fbn_32", "fbs_16"]
        field["option_labels"] = {
            "both": "全部（both）",
            "fbn_32": "MRI / fbn_32",
            "fbs_16": "CT / fbs_16",
        }
        return field
    if key == "ALPHA":
        field["widget"] = "select"
        opts = [1.0, 0.75, 0.5, 0.25]
        try:
            cur = float(value)
        except (TypeError, ValueError):
            cur = None
        if cur is not None and all(abs(cur - x) > 1e-9 for x in opts):
            opts = [cur] + opts
        field["options"] = opts
        field["option_labels"] = {
            "1": "1.0 纯 LASSO（L1）",
            "1.0": "1.0 纯 LASSO（L1）",
            "0.75": "0.75 弹性网",
            "0.5": "0.5 弹性网",
            "0.25": "0.25 弹性网",
        }
        return field
    if key in ("VAL_MODE", "SUBGROUP_VAL_MODE", "EXTERNAL_VAL_MODE"):
        field["widget"] = "select"
        field["options"] = ["refit", "apply_formula", "lock_threshold"]
        field["option_labels"] = {
            "refit": "重新拟合",
            "apply_formula": "套公式",
            "lock_threshold": "锁定阈值",
        }
        if key == "SUBGROUP_VAL_MODE":
            field["options"] = [""] + list(field["options"])
            field["option_labels"][""] = "跟随内部验证"
        return field
    if key in ("CORR_GROUP", "PARTIAL_GROUP"):
        field["widget"] = "select"
        opts = [""] + [str(x) for x in group_levels if str(x).strip()]
        if isinstance(value, (list, tuple)):
            cur = next((str(x).strip() for x in value if str(x).strip()), "")
        else:
            cur = "" if value is None else str(value).strip()
        if cur and cur not in opts:
            opts.append(cur)
        field["options"] = opts
        field["option_labels"] = {"": "全部"}
        field["value"] = cur
        return field
    if key == "PARTIAL_METHOD":
        field["widget"] = "select"
        field["options"] = ["spearman", "pearson"]
        field["option_labels"] = {
            "spearman": "Spearman 偏相关",
            "pearson": "Pearson 偏相关",
        }
        return field
    if key in ("ANCOVA_GROUP", "MLR_GROUP"):
        field["widget"] = "select"
        opts = [""] + list(clin_cols)
        if "Group" not in opts:
            opts.append("Group")
        cur = "" if value is None else str(value)
        if cur and cur not in opts:
            opts.append(cur)
        field["options"] = opts
        field["option_labels"] = {"": "（不选）"}
        return field
    if key == "SURVIVAL_TIME_UNIT":
        field["widget"] = "select"
        field["options"] = ["months", "days", "years"]
        return field
    if key == "FIG_PALETTE":
        field["widget"] = "select"
        field["options"] = [
            "npg", "okabe", "tol", "radiology", "ibm",
            "aaas", "nejm", "lancet", "jama", "jco",
            "d3", "tableau", "set1", "dark2", "paired",
            "tolbright", "frontiers", "bmj", "uchicago", "gray",
            "custom",
        ]
        return field
    if key == "SURVIVAL_TIME_COL":
        field["widget"] = "select"
        opts = [""] + list(clin_cols)
        if value and str(value) not in opts:
            opts = ["", str(value)] + list(clin_cols)
        field["options"] = opts
        return field
    if key == "SPLIT_COL":
        field["widget"] = "select"
        opts = [""] + list(clin_cols)
        if value and str(value) not in opts:
            opts = ["", str(value)] + list(clin_cols)
        field["options"] = opts
        field["option_labels"] = {"": "（空＝按比例随机拆）"}
        return field
    if key == "POSITIVE_GROUP_LABEL" and group_levels:
        field["widget"] = "select"
        opts = list(group_levels)
        if value and str(value) not in opts:
            opts = [str(value)] + opts
        field["options"] = opts
        return field
    if key in SHEET_KEYS and sheets:
        field["widget"] = "select"
        opts = [""] + list(sheets)
        if value and str(value) not in opts:
            opts = ["", str(value)] + list(sheets)
        field["options"] = opts
    elif key in CLIN_COL_SINGLE_KEYS and clin_cols:
        field["widget"] = "select"
        opts = list(clin_cols)
        if value and str(value) not in opts:
            opts = [str(value)] + opts
        field["options"] = opts
    elif key == "RAD_ID_COL" and rad_id_cols:
        field["widget"] = "select"
        opts = list(rad_id_cols)
        if value and str(value) not in opts:
            opts = [str(value)] + opts
        field["options"] = opts
    elif key in RAD_COL_SINGLE_KEYS and icc_cols and key != "RAD_ID_COL":
        field["widget"] = "select"
        opts = list(icc_cols)
        if value and str(value) not in opts:
            opts = [str(value)] + opts
        field["options"] = opts
    return field

def suggest_label_col(clin_cols: list[str], clin_id: str, current: str) -> str:
    """LABEL_COL ≠ CLIN_ID_COL。配置写成同一列但表里另有姓名列时改用姓名列。"""
    cols = [str(c) for c in (clin_cols or [])]
    colset = set(cols)
    cid = str(clin_id or "").strip()
    cur = str(current or "").strip()
    if cur and cur in colset and cur != cid:
        return cur
    for c in _LABEL_DISPLAY_CANDS:
        if c in colset and c != cid:
            return c
    if cid.lower() in {
        "patientid", "patient_id", "id", "record_id", "file_id", "label",
    }:
        for c in ("Name", "name"):
            if c in colset and c != cid:
                return c
    if cur and cur in colset:
        return cur
    if cid and cid in colset:
        return cid
    for c in ("Name", "name", "label"):
        if c in colset:
            return c
    return cur

def apply_label_col_suggestion(settings: dict[str, Any], clin_cols: list[str]) -> str | None:
    """若应改 LABEL_COL，写入 settings 并返回新列名。"""
    if not clin_cols:
        return None
    sug = suggest_label_col(
        clin_cols,
        str(settings.get("CLIN_ID_COL") or ""),
        str(settings.get("LABEL_COL") or ""),
    )
    cur = str(settings.get("LABEL_COL") or "")
    if sug and sug != cur:
        settings["LABEL_COL"] = sug
        return sug
    return None

def build_schema(settings: dict[str, Any], sheets: list[str] | None = None,
                 clin_cols: list[str] | None = None,
                 rad_id_cols: list[str] | None = None,
                 icc_cols: list[str] | None = None,
                 group_levels: list[str] | None = None,
                 rad_meta_cols: list[str] | None = None,
                 id_options: list[str] | None = None,
                 extra_select: dict[str, list[str]] | None = None,
                 measure_cols: list[str] | None = None,
                 rad_cols: list[str] | None = None) -> list[dict[str, Any]]:
    sheets = sheets or []
    clin_cols = clin_cols or []
    rad_id_cols = rad_id_cols or []
    icc_cols = icc_cols or []
    group_levels = group_levels or []
    rad_meta_cols = rad_meta_cols or []
    id_options = id_options or []
    extra_select = extra_select or {}
    measure_cols = measure_cols or []
    rad_cols = rad_cols or []
    settings = dict(settings)
    from modules.config.defaults import DEFAULT_SETTINGS
    for src in (DEFAULT_SETTINGS, CONSOLE_DEFAULTS):
        for k, v in src.items():
            settings.setdefault(k, v)
    apply_clin_role_priority(settings)
    excl = _as_name_list(settings.get("RAD_FEATURE_PREFIX_EXCLUDE"))
    settings["RAD_FEATURE_PREFIX_EXCLUDE"] = excl
    rad_cols = _columns_open_for_analysis(rad_cols, clin_cols, excl, rad_id_cols)
    measure_cols = _columns_open_for_analysis(measure_cols, clin_cols, excl, rad_id_cols)
    skip_clin = list(settings.get("SKIP_CLIN_FEATURES") or [])
    locked_skip = implicit_skip_cols(settings)
    fields = [
        _widget_for(
            k, v, sheets, clin_cols, rad_id_cols, icc_cols, group_levels,
            rad_meta_cols, id_options, skip_clin, locked_skip, extra_select,
            rad_sheet=str(settings.get("RAD_SHEET") or "").strip() or None,
            measure_cols=measure_cols,
            rad_cols=rad_cols,
        )
        for k, v in settings.items()
    ]
    order = {k: i for i, k in enumerate(
        [x for _s, _t, ks in SECTION_DEFS for x in ks]
    )}
    fields.sort(key=lambda f: (order.get(f["key"], 10_000), f["key"]))
    return fields

# ── overlay ────────────────────────────────────────────────────
def normalize_overlay(ov: dict[str, Any]) -> dict[str, Any]:
    from modules.config.ini_io import migrate_retired_settings
    out = migrate_retired_settings(ov or {})
    for k in TUPLE_LIST_KEYS:
        if k in out and isinstance(out[k], list):
            out[k] = [tuple(x) if isinstance(x, list) else x for x in out[k]]
    return sync_external_tests(hydrate_console_settings(out))

def unwrap_overlay_file(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("overlay"), dict):
        raw = dict(obj["overlay"])
    else:
        raw = {k: v for k, v in obj.items()
               if k not in ("project", "data", "settings", "name", "saved_at")}
    return normalize_overlay(raw)

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

def save_session(payload: dict[str, Any]) -> None:
    OVERRIDE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_session() -> dict[str, Any] | None:
    if not OVERRIDE_PATH.is_file():
        return None
    try:
        return json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

# ── writeback settings.py ──────────────────────────────────────
def format_py_value(val: Any, indent: int = 0) -> str:
    pad = "    "
    if isinstance(val, bool):
        return "True" if val else "False"
    if val is None:
        return "None"
    if isinstance(val, int) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    if isinstance(val, str):
        return repr(val)
    if isinstance(val, tuple):
        if len(val) == 1:
            return f"({format_py_value(val[0])},)"
        return "(" + ", ".join(format_py_value(x) for x in val) + ")"
    if isinstance(val, list):
        if not val:
            return "[]"
        simple = all(
            isinstance(x, (str, int, float, bool, tuple)) or x is None
            for x in val
        )
        if simple and len(val) <= 4 and not any(isinstance(x, tuple) for x in val):
            return "[" + ", ".join(format_py_value(x) for x in val) + "]"
        inner = ",\n".join(pad + format_py_value(x) for x in val)
        return "[\n" + inner + ",\n]"
    if isinstance(val, dict):
        if not val:
            return "{}"
        parts = []
        for k, v in val.items():
            parts.append(f"{pad}{format_py_value(k)}: {format_py_value(v)}")
        return "{\n" + ",\n".join(parts) + ",\n}"
    return repr(val)

def writeback_settings(
    settings_path: Path,
    overlay: dict[str, Any],
    *,
    normalize: bool = True,
) -> dict[str, Any]:
    settings_path = under_root(settings_path)
    if "MODULES_DIR" in overlay:
        modules_dir = str(overlay.get("MODULES_DIR") or "").strip()
        if not modules_dir or not Path(modules_dir).is_dir():
            raise ValueError(f"MODULES_DIR 无效（未填写或不是现存目录）: {modules_dir!r}")
    if settings_path.suffix.lower() == ".ini":
        from modules.config.ini_io import ensure_settings_ini, read_ini, write_ini
        existed = settings_path.is_file()
        if not existed:
            ensure_settings_ini(settings_path)
        cur = read_ini(settings_path) if settings_path.is_file() else {}
        ov = normalize_overlay(overlay) if normalize else dict(overlay)
        updated, appended = [], []
        for k, v in ov.items():
            if not str(k).isidentifier() or str(k).startswith("_"):
                continue
            if k in cur:
                updated.append(k)
            else:
                appended.append(k)
            cur[k] = v
        bak = settings_path.with_suffix(settings_path.suffix + ".bak")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            shutil.copy2(settings_path, bak)
            backup = str(bak)
        else:
            backup = ""
        write_ini(settings_path, cur)
        return {
            "updated": sorted(updated),
            "appended": appended,
            "backup": backup,
        }
    src = settings_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    ov = normalize_overlay(overlay) if normalize else dict(overlay)
    spans: list[tuple[int, int, str, str]] = []

    def _rhs_span(node: ast.AST) -> tuple[int, int] | None:
        if not hasattr(node, "lineno"):
            return None
        # replace from first '=' on start line through end_lineno
        return int(node.lineno), int(getattr(node, "end_lineno", node.lineno) or node.lineno)

    assigned: set[str] = set()
    for node in tree.body:
        key = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            key = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            t0 = node.targets[0]
            if isinstance(t0, ast.Name):
                key = t0.id
        if key is None or key not in ov or key.startswith("_"):
            continue
        span = _rhs_span(node)
        if span is None:
            continue
        assigned.add(key)
        spans.append((span[0], span[1], key, format_py_value(ov[key])))

    spans.sort(key=lambda x: x[0], reverse=True)
    for start, end, key, rhs in spans:
        i0, i1 = start - 1, end
        block = "".join(lines[i0:i1])
        if "=" not in block:
            continue
        left, _old = block.split("=", 1)
        new_block = left.rstrip() + " = " + rhs
        if not new_block.endswith("\n"):
            new_block += "\n"
        lines[i0:i1] = [new_block]

    missing = [k for k in ov if k not in assigned and not str(k).startswith("_")
               and str(k).isidentifier()]
    if missing:
        extra = ["\n", "# ── console.html 追加 ──\n"]
        for k in missing:
            extra.append(f"{k} = {format_py_value(ov[k])}\n")
        lines.extend(extra)

    bak = settings_path.with_suffix(settings_path.suffix + ".bak")
    shutil.copy2(settings_path, bak)
    settings_path.write_text("".join(lines), encoding="utf-8")
    return {
        "updated": sorted(assigned),
        "appended": missing,
        "backup": str(bak),
    }

# ── projects / data ────────────────────────────────────────────
def discover_projects() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    dirs: list[Path] = []
    for root in project_roots():
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir() or p.name.startswith((".", "0", "_")):
                continue
            if p.name in seen:
                continue
            dirs.append(p)
            seen.add(p.name)
    for p in dirs:
        settings = project_settings(p)
        xlsx = sorted(str(x) for x in p.glob("*.xlsx") if not x.name.startswith("~"))
        if not settings.is_file() and not xlsx:
            continue
        default_xlsx = ""
        try:
            mod = load_settings_module(settings)
            inp = str(getattr(mod, "INPUT", "") or "")
            if inp:
                cand = Path(inp)
                if not cand.is_absolute():
                    cand = p / inp
                if cand.is_file():
                    default_xlsx = str(cand.resolve())
        except Exception:
            default_xlsx = ""
        if not default_xlsx and xlsx:
            default_xlsx = xlsx[0]
        items.append({
            "name": p.name,
            "dir": str(p),
            "settings": str(settings),
            "xlsx": xlsx,
            "default_xlsx": default_xlsx,
        })
    items.sort(key=lambda x: (0 if x["name"] == DEFAULT_PROJECT else 1, x["name"]))
    return items

def project_xlsx_names(proj: Path) -> list[str]:
    return [
        p.name for p in sorted(proj.glob("*.xlsx"))
        if p.is_file() and not p.name.startswith("~")
    ]

def _exclude_cell_text(raw) -> str:
    if raw is None:
        return ""
    try:
        if raw != raw:  # NaN
            return ""
    except Exception as e:
        print(f"  ⚠ exclude cell NaN check: {e}", flush=True)
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    return s

def xlsx_meta(path: Path, sheet: str = "", col: str = "") -> dict[str, Any]:
    import pandas as pd

    out: dict[str, Any] = {
        "file": str(path),
        "exists": path.is_file(),
        "sheets": [],
        "columns": [],
        "values": [],
        "sheet": "",
        "col": "",
    }
    if not path.is_file():
        return out
    xl = pd.ExcelFile(path)
    sheets = [str(s) for s in xl.sheet_names]
    out["sheets"] = sheets
    if not sheet:
        return out
    hit = sheet if sheet in sheets else next(
        (s for s in sheets if s.lower() == sheet.lower()), None
    )
    if hit is None:
        return out
    df0 = xl.parse(hit, nrows=0)
    cols = [str(c) for c in df0.columns]
    out["columns"] = cols
    out["sheet"] = hit
    if not col:
        return out
    use = col if col in df0.columns else (cols[0] if cols else None)
    if use is None:
        return out
    df = xl.parse(hit, usecols=[use])
    counts: dict[str, int] = {}
    order: list[str] = []
    for v in df[use].tolist():
        s = _exclude_cell_text(v)
        if not s:
            continue
        if s not in counts:
            counts[s] = 0
            order.append(s)
        counts[s] += 1
    order.sort(key=lambda x: (-counts[x], x))
    out["values"] = order[:80]
    out["col"] = str(use)
    return out

def exclude_select_options(
    proj: Path,
    settings: dict[str, Any],
    data_path: Path | None,
    preview: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    files = project_xlsx_names(proj)
    meta = (preview or {}).get("exclude") or {}
    sheets = list(meta.get("sheets") or [])
    cols = list(meta.get("columns") or [])
    rows = list(meta.get("values") or [])
    if not sheets and preview:
        sheets = list(preview.get("sheets") or [])
    if not cols:
        sh = str(settings.get("EXCLUDE_SHEET") or "").strip()
        info = ((preview or {}).get("sheet_info") or {}).get(sh) or {}
        cols = list(info.get("columns") or [])
        if not cols and sh and sh == str(settings.get("CLIN_SHEET") or "").strip():
            cols = list((preview or {}).get("clin_cols") or [])
    from modules.config.ini_io import name_choice
    packed_files = name_choice(settings.get("EXCLUDE_FILE"))
    cur_files = packed_files if isinstance(packed_files, list) else (
        [packed_files] if packed_files else []
    )
    for cur_file in reversed(cur_files):
        base = Path(str(cur_file)).name
        if base and base not in files:
            files = [base] + files
    return {
        "EXCLUDE_FILE": files,
        "EXCLUDE_SHEET": sheets,
        "EXCLUDE_COL": cols,
        "EXCLUDE_ROW": rows,
    }

def _info_from_frame(df, name: str, max_rows: int = 12) -> dict[str, Any]:
    """Preview rows for a sheet already in memory. Every column is included."""
    all_cols = [str(c) for c in df.columns]
    n = len(df)
    miss = {
        str(c): (0 if n == 0 else int(df[c].isna().sum()))
        for c in df.columns
    }
    head = []
    for _, row in df.head(max_rows).iterrows():
        rec = {}
        for c in df.columns:
            v = row[c]
            if v is None or (isinstance(v, float) and v != v):
                rec[str(c)] = None
            else:
                s = str(v)
                rec[str(c)] = s if len(s) < 80 else s[:77] + "..."
        head.append(rec)
    return {
        "name": name,
        "n_rows": int(n),
        "n_cols": len(all_cols),
        "columns": all_cols,
        "missing": miss,
        "head": head,
    }

def resolve_sheet_name(name: str, sheets: list[str]) -> str | None:
    """Match a configured sheet to a workbook name (case, cln↔clin)."""
    raw = str(name or "").strip()
    if not raw:
        return None
    if raw in sheets:
        return raw
    low = raw.lower()
    for s in sheets:
        if s.lower() == low:
            return s
    for alt in _SHEET_ALIASES.get(low, ()):
        for s in sheets:
            if s.lower() == alt.lower():
                return s
    return None

def _preview_one_workbook(data_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd
    from modules.utils.xlsx_sheet import read_columns, read_header, read_sheet, workbook_info

    data_path = under_root(data_path)
    try:
        book = workbook_info(data_path)
    except Exception:
        book = []
    sheets = [str(s["name"]) for s in book]
    size = {str(s["name"]): s for s in book}
    if not sheets:
        xl = pd.ExcelFile(data_path)
        sheets = [str(s) for s in xl.sheet_names]
        size = {}
    frames: dict[str, pd.DataFrame] = {}
    headers: dict[str, list[str]] = {}

    def _wide(name: str) -> bool:
        return int((size.get(name) or {}).get("n_cols") or 0) > 40

    def _frame(name: str) -> pd.DataFrame:
        got = frames.get(name)
        if got is None:
            got = read_sheet(data_path, name)
            frames[name] = got
        return got

    def _header(name: str) -> list[str]:
        got = headers.get(name)
        if got is None:
            if _wide(name):
                got = read_header(data_path, name)
            else:
                got = [str(c) for c in _frame(name).columns]
            headers[name] = got
        return got

    def _cols(name: str, columns: list[str]) -> pd.DataFrame:
        if _wide(name):
            return read_columns(data_path, name, columns)
        df = _frame(name)
        use = [c for c in columns if c in df.columns]
        return df.loc[:, use] if use else df.iloc[:, 0:0]

    info: dict[str, Any] = {}
    for sname in sheets:
        meta = size.get(sname) or {}
        info[sname] = {
            "name": sname,
            "n_rows": int(meta.get("n_rows") or 0),
            "n_cols": int(meta.get("n_cols") or 0),
            "columns": [],
            "missing": {},
            "head": [],
        }

    clin_sheet = str(settings.get("CLIN_SHEET") or "").strip()
    rad_sheet = str(settings.get("RAD_SHEET") or "").strip()
    qc_sheet = str(settings.get("QC_SHEET") or "").strip()
    icc_sheet = str(settings.get("ICC_SHEET") or "").strip()
    exc_sheet = str(settings.get("EXCLUDE_SHEET") or "").strip()
    clin_id = str(settings.get("CLIN_ID_COL") or "PatientID")
    rad_id = str(settings.get("RAD_ID_COL") or "patient_id")

    def _hit(name: str) -> str | None:
        return resolve_sheet_name(name, sheets)

    clin_cols: list[str] = []
    rad_id_cols: list[str] = []
    icc_cols: list[str] = []
    outcome_dist: dict[str, Any] = {}
    id_check: dict[str, Any] = {}
    exclude: dict[str, Any] = {"n": 0, "ids": []}
    qc_sum: dict[str, Any] = {}
    icc_sum: dict[str, Any] = {}

    def _id_col(df) -> str | None:
        for cand in (
            settings.get("RAD_ID_COL"), settings.get("CLIN_ID_COL"),
            "Name", "PatientID", "patient_id", "Patient_ID", "ID", "Unnamed: 0",
        ):
            if cand and cand in df.columns:
                return str(cand)
        if len(df.columns) and not str(df.columns[0]).startswith("h"):
            return str(df.columns[0])
        return None

    def _levels_from_id(df) -> tuple[list[str], dict[str, int]]:
        col = _id_col(df)
        if not col:
            return [], {}
        sep = str(settings.get("GROUP_FROM_ID_SEP") or "_")
        pref = df[col].astype(str).str.strip().str.split(sep, n=1).str[0]
        vc = pref.value_counts()
        return [str(x) for x in vc.index.tolist()], {str(k): int(v) for k, v in vc.to_dict().items()}

    ch = _hit(clin_sheet)
    group_levels: list[str] = []
    clin_dist: dict[str, Any] = {}
    preview_group_source = ""
    outcomes = [str(c) for c in (settings.get("OUTCOME_COLS") or []) if str(c).strip()]
    cdf = None
    if ch:
        cdf = _frame(ch)
        if len(cdf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[ch] = _info_from_frame(cdf, ch)
        else:
            info[ch]["n_rows"] = int(len(cdf))
            info[ch]["n_cols"] = int(len(cdf.columns))
        clin_cols = [str(c) for c in cdf.columns]
        for col in clin_cols:
            vc = cdf[col].value_counts(dropna=False).to_dict()
            clin_dist[col] = {str(k): int(v) for k, v in vc.items()}
        seen = set()
        for col in outcomes:
            if col in seen or col not in cdf.columns:
                continue
            seen.add(col)
            outcome_dist[col] = dict(clin_dist.get(col) or {})
        gcol = None
        sub = str(settings.get("SUBGROUP_COL") or "").strip()
        src = str(settings.get("GROUP_SOURCE_COL") or "").strip()
        if sub and sub in cdf.columns:
            gcol = sub
        elif src and src in cdf.columns:
            gcol = src
        elif len(outcomes) == 1 and outcomes[0] in cdf.columns:
            gcol = outcomes[0]
        elif "Group" in cdf.columns:
            gcol = "Group"
        elif "group" in cdf.columns:
            gcol = "group"
        if gcol:
            group_levels = [str(x) for x in cdf[gcol].dropna().astype(str).unique().tolist()]
            preview_group_source = gcol
        elif settings.get("GROUP_FROM_ID"):
            group_levels, vc_id = _levels_from_id(cdf)
            if vc_id:
                outcome_dist["Group(from ID)"] = vc_id
                preview_group_source = "Group(from ID)"

    rad_meta_cols: list[str] = []
    id_options: list[str] = []
    rh = _hit(rad_sheet)
    rad_all: list[str] = []
    if rh:
        rad_all = _header(rh)
        info[rh]["n_cols"] = len(rad_all) or info[rh]["n_cols"]
        info[rh]["columns"] = rad_all[:16] if len(rad_all) > 40 else rad_all
        rad_meta_cols = _rad_meta_cols(rad_all)
        rad_id_cols = _id_dropdown_columns(rad_all, str(settings.get("RAD_ID_COL") or ""))
        if not group_levels:
            rdf_names = pd.DataFrame(columns=rad_all)
            idc = _id_col(rdf_names)
            gcol = None
            src = str(settings.get("GROUP_SOURCE_COL") or "").strip()
            if src and src in rdf_names.columns:
                gcol = src
            elif len(outcomes) == 1 and outcomes[0] in rdf_names.columns:
                gcol = outcomes[0]
            elif "Group" in rdf_names.columns:
                gcol = "Group"
            elif "group" in rdf_names.columns:
                gcol = "group"
            usecols = [c for c in (idc, gcol) if c]
            if not usecols and rad_all:
                usecols = [rad_all[0]]
            rdf_g = _cols(rh, usecols) if usecols else pd.DataFrame()
            if gcol and gcol in rdf_g.columns:
                group_levels = [str(x) for x in rdf_g[gcol].dropna().astype(str).unique().tolist()]
                outcome_dist[str(gcol)] = {
                    str(k): int(v) for k, v in rdf_g[gcol].astype(str).value_counts().to_dict().items()
                }
            elif settings.get("GROUP_FROM_ID"):
                lv, vc_id = _levels_from_id(rdf_g)
                if lv:
                    group_levels = lv
                    outcome_dist["Group(from ID)"] = vc_id

    if ch and rh and cdf is not None:
        rad_id_name = rad_id if rad_id in rad_all else (rad_all[0] if rad_all else "")
        rdf = _cols(rh, [rad_id_name]) if rad_id_name else pd.DataFrame()
        cs = cdf[clin_id] if clin_id in cdf.columns else (
            cdf.iloc[:, 0] if len(cdf.columns) else None
        )
        rs = rdf[rad_id_name] if rad_id_name and rad_id_name in rdf.columns else (
            rdf.iloc[:, 0] if len(rdf.columns) else None
        )
        if cs is not None and rs is not None:
            cset = {str(x).strip() for x in cs.dropna().tolist()}
            rset = {str(x).strip() for x in rs.dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": len(rset),
                "overlap": len(cset & rset),
                "clin_only": len(cset - rset),
                "rad_only": len(rset - cset),
                "clin_id_col": clin_id if clin_id in cdf.columns else str(cdf.columns[0]),
                "rad_id_col": rad_id_name if rad_id_name in rdf.columns else (
                    str(rdf.columns[0]) if len(rdf.columns) else rad_id
                ),
            }
        elif cs is not None:
            cset = {str(x).strip() for x in cs.dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": 0,
                "overlap": 0,
                "clin_only": len(cset),
                "rad_only": 0,
                "clin_id_col": clin_id if clin_id in cdf.columns else str(cdf.columns[0]),
                "rad_id_col": "（无组学列）",
            }
    elif ch and cdf is not None:
        cid = clin_id if clin_id in cdf.columns else _id_col(cdf)
        if cid:
            cset = {str(x).strip() for x in cdf[cid].dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": 0,
                "overlap": 0,
                "clin_only": len(cset),
                "rad_only": 0,
                "clin_id_col": cid,
                "rad_id_col": "（无组学表）",
            }
    elif rh:
        rad_id_name = rad_id if rad_id in rad_all else (rad_all[0] if rad_all else "")
        rdf = _cols(rh, [rad_id_name]) if rad_id_name else pd.DataFrame()
        rid = rad_id_name if rad_id_name in rdf.columns else (
            str(rdf.columns[0]) if len(rdf.columns) else None
        )
        if rid:
            rset = {str(x).strip() for x in rdf[rid].dropna().tolist()}
            id_check = {
                "clin_n": 0,
                "rad_n": len(rset),
                "overlap": len(rset),
                "clin_only": 0,
                "rad_only": 0,
                "clin_id_col": "（无临床表）",
                "rad_id_col": rid,
            }

    eh = _hit(exc_sheet)
    try:
        from modules.stats.data_io import (
            resolve_exclude_files,
            normalize_exclude_rows,
            _keys_from_exclude_frame,
        )
        paths: list[Path] = []
        for exclude_file in resolve_exclude_files(settings):
            ex_p = Path(str(exclude_file))
            if not ex_p.is_absolute():
                ex_p = data_path.parent / ex_p
            paths.append(ex_p)
        ecol = str(settings.get("EXCLUDE_COL") or "exc")
        row_vals = normalize_exclude_rows(settings.get("EXCLUDE_ROW"))
        sheets_u: list[str] = []
        columns_u: list[str] = []
        values_u: list[str] = []
        all_keys: set[str] = set()
        n_raw_total = 0
        id_used = ""
        hit_sheet = exc_sheet or ""
        used_col = ecol
        for i, ex_p in enumerate(paths):
            meta = xlsx_meta(ex_p, exc_sheet or "exc", ecol) if ex_p.is_file() else {}
            for s in meta.get("sheets") or []:
                if s not in sheets_u:
                    sheets_u.append(str(s))
            if i == 0:
                columns_u = [str(c) for c in (meta.get("columns") or [])]
                values_u = [str(v) for v in (meta.get("values") or [])]
                hit_sheet = str(meta.get("sheet") or (exc_sheet or ""))
                used_col = str(meta.get("col") or ecol)
            if not (ex_p.is_file() and meta.get("sheet")):
                continue
            edf = pd.ExcelFile(ex_p).parse(meta["sheet"])
            use = ecol if ecol in edf.columns else (
                edf.columns[0] if len(edf.columns) else None
            )
            if use is None:
                continue
            keys, n_raw, id_one = _keys_from_exclude_frame(
                edf, str(use), row_vals, settings
            )
            all_keys |= {str(x) for x in keys if str(x).strip()}
            n_raw_total += int(n_raw)
            if id_one:
                id_used = str(id_one)
            if i == 0:
                used_col = str(use)
        file_label = (
            " + ".join(p.name for p in paths)
            if len(paths) > 1
            else (str(paths[0]) if paths else "")
        )
        exclude = {
            "n": int(n_raw_total),
            "ids": sorted(all_keys)[:80],
            "file": file_label,
            "files": [str(p) for p in paths],
            "sheet": hit_sheet,
            "col": used_col,
            "sheets": sheets_u,
            "columns": columns_u,
            "values": values_u,
            "row": row_vals[0] if len(row_vals) == 1 else row_vals,
            "missing": not any(p.is_file() for p in paths),
            "id_col": id_used,
        }
        if not exclude.get("sheets"):
            exclude["sheets"] = list(sheets)
            if eh:
                inf = info.get(eh) or {}
                exclude["columns"] = list(inf.get("columns") or [])
    except Exception:
        if eh:
            edf = _frame(eh)
            ecol = str(settings.get("EXCLUDE_COL") or "exc")
            use = ecol if ecol in edf.columns else (
                edf.columns[0] if len(edf.columns) else None
            )
            ids = []
            if use is not None:
                ids = [str(x) for x in edf[use].dropna().tolist() if str(x).strip()]
            exclude = {
                "n": len(ids), "ids": ids[:80], "col": str(use) if use else "",
                "sheets": list(sheets),
                "columns": [str(c) for c in (edf.columns if use is not None else [])],
                "values": [],
            }

    qh = _hit(qc_sheet)
    if qh:
        qdf = _frame(qh)
        if len(qdf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[qh] = _info_from_frame(qdf, qh)
        qc_sum = {"n": int(len(qdf)), "columns": [str(c) for c in qdf.columns[:20]]}
        for cand in ("QC_Status", "Status", "QC_MI_Warn"):
            if cand in qdf.columns:
                qc_sum[cand] = {str(k): int(v) for k, v in
                                qdf[cand].value_counts(dropna=False).head(12).to_dict().items()}

    ih = _hit(icc_sheet)
    if ih:
        idf = _frame(ih)
        if len(idf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[ih] = _info_from_frame(idf, ih)
        icc_cols = [str(c) for c in idf.columns]
        thr = float(settings.get("ICC_THRESHOLD") or 0.75)
        intra_c = str(settings.get("ICC_INTRA_COL") or "intra_ICC")
        inter_c = str(settings.get("ICC_INTER_COL") or "inter_ICC")
        icc_sum = {"n": int(len(idf)), "threshold": thr}
        if intra_c in idf.columns and inter_c in idf.columns:
            intra = pd.to_numeric(idf[intra_c], errors="coerce")
            inter = pd.to_numeric(idf[inter_c], errors="coerce")
            both = (intra >= thr) & (inter >= thr)
            icc_sum["both_ge_thr"] = int(both.sum())
            icc_sum["missing_icc"] = int((intra.isna() | inter.isna()).sum())

    after_n = None
    if id_check:
        if ch and rh:
            base = id_check.get("overlap", 0)
        elif ch:
            base = id_check.get("clin_n", 0)
        else:
            base = id_check.get("rad_n", 0)
        exc_n = int(exclude.get("n") or 0) if settings.get("DO_EXCLUDE_SAMPLES") else 0
        after_n = max(0, int(base or 0) - exc_n)

    if not clin_cols and rad_meta_cols:
        clin_cols = list(rad_meta_cols)
        for extra in ("Name", "Group"):
            if extra not in clin_cols:
                clin_cols.append(extra)

    idc_pick = ""
    id_src = None
    if ch:
        try:
            id_src = cdf
            idc_pick = str(settings.get("CLIN_ID_COL") or "")
            if id_src is not None and idc_pick not in id_src.columns:
                idc_pick = _id_col(id_src) or ""
        except Exception:
            id_src = None
    elif rh:
        idc_pick = str(settings.get("RAD_ID_COL") or "")
        if idc_pick not in (rad_meta_cols or rad_id_cols):
            for cand in ("Name", "ID", "PatientID", "patient_id", "Patient_ID"):
                if cand in rad_meta_cols:
                    idc_pick = cand
                    break
            else:
                idc_pick = rad_meta_cols[0] if rad_meta_cols else ""
        if idc_pick:
            try:
                id_src = _cols(rh, [idc_pick])
            except Exception:
                id_src = None
    if id_src is not None and idc_pick and idc_pick in id_src.columns:
        raw_ids = [str(x).strip() for x in id_src[idc_pick].dropna().tolist() if str(x).strip()]
        id_options = _uniq_keep(raw_ids)[:400]

    return {
        "data": str(data_path),
        "sheets": sheets,
        "sheet_info": info,
        "clin_cols": clin_cols,
        "rad_id_cols": rad_id_cols,
        "rad_meta_cols": rad_meta_cols,
        "measure_cols": _measure_cols(
            clin_cols,
            _columns_open_for_analysis(
                rad_all, clin_cols,
                _as_name_list(settings.get("RAD_FEATURE_PREFIX_EXCLUDE")),
                rad_id_cols,
            ),
        ),
        "rad_cols": rad_all,
        "id_options": id_options,
        "icc_cols": icc_cols,
        "outcome_dist": outcome_dist,
        "clin_dist": clin_dist,
        "id_check": id_check,
        "exclude": exclude,
        "qc": qc_sum,
        "icc": icc_sum,
        "n_after_exclude_est": after_n,
        "group_levels": group_levels,
        "group_source": preview_group_source,
        "resolved_sheets": {
            "CLIN_SHEET": ch or "",
            "RAD_SHEET": rh or "",
            "QC_SHEET": qh or "",
            "ICC_SHEET": ih or "",
        },
        "sheet_rule": (
            "CLIN_SHEET 与 RAD_SHEET 必须二选一"
            if not (ch or rh)
            else ""
        ),
    }

def _resolve_ext_xlsx(file: str, data_path: Path) -> Path:
    src = Path(str(file or "").strip())
    if not src.is_absolute():
        src = data_path.parent / src
    return under_root(src)

def _preview_external_one(
    data_path: Path, settings: dict[str, Any], spec: dict[str, str]
) -> dict[str, Any]:
    from modules.utils.u_qc import external_qc_html_name, qc_html_rel, _pick_named_sheet

    name = str(spec.get("name") or "ext")
    rec: dict[str, Any] = {
        "name": name,
        "qc_html": qc_html_rel(external_qc_html_name(name)),
        "file": str(spec.get("file") or ""),
    }
    try:
        path = _resolve_ext_xlsx(spec["file"], data_path)
    except Exception as e:
        rec["error"] = str(e)
        return rec
    rec["file"] = str(path)
    if not path.is_file():
        rec["error"] = f"missing file: {path}"
        return rec
    try:
        from modules.utils.xlsx_sheet import workbook_info
        sheets = [str(s["name"]) for s in workbook_info(path)]
    except Exception as e:
        rec["error"] = str(e)
        return rec
    st = dict(settings)
    st["CLIN_SHEET"] = spec.get("clin_sheet") or ""
    st["RAD_SHEET"] = spec.get("rad_sheet") or ""
    if spec.get("rad_id_col"):
        st["RAD_ID_COL"] = spec["rad_id_col"]
    st["QC_SHEET"] = _pick_named_sheet(
        sheets, spec.get("qc_sheet", ""), settings.get("QC_SHEET"), "qc",
    )
    st["ICC_SHEET"] = _pick_named_sheet(
        sheets, spec.get("icc_sheet", ""), settings.get("ICC_SHEET"), "icc",
    )
    st["DO_EXCLUDE_SAMPLES"] = False
    st["EXCLUDE_FILE"] = ""
    try:
        one = _preview_one_workbook(path, st)
    except Exception as e:
        rec["error"] = str(e)
        return rec
    one.update(rec)
    one["has_qc_sheet"] = bool(st.get("QC_SHEET"))
    return one

_PREVIEW_CACHE = _state.PREVIEW_CACHE  # 同一个 dict

def _preview_cache_key(data_path: Path, settings: dict[str, Any]) -> tuple:
    data_path = Path(data_path)
    st = data_path.stat()
    keep = (
        "CLIN_SHEET", "RAD_SHEET", "QC_SHEET", "ICC_SHEET",
        "CLIN_ID_COL", "RAD_ID_COL", "LABEL_COL",
        "OUTCOME_COLS", "SUBGROUP_COL", "GROUP_SOURCE_COL",
        "GROUP_FROM_ID", "GROUP_FROM_ID_SEP",
        "DO_EXCLUDE_SAMPLES", "EXCLUDE_FILE", "EXCLUDE_SHEET",
        "EXCLUDE_COL", "EXCLUDE_ROW",
        "ICC_THRESHOLD", "ICC_INTRA_COL", "ICC_INTER_COL",
        "EXTERNAL_FILE", "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
        "EXTERNAL_TESTS",
    )
    blob = []
    for key in keep:
        blob.append((key, json.dumps(settings.get(key), ensure_ascii=False, sort_keys=True, default=str)))
    extras = []
    names = []
    raw_ext = settings.get("EXTERNAL_TESTS") or []
    if isinstance(raw_ext, list):
        for spec in raw_ext:
            if isinstance(spec, dict) and spec.get("file"):
                names.append(str(spec.get("file")))
    if settings.get("EXTERNAL_FILE"):
        names.append(str(settings.get("EXTERNAL_FILE")))
    for name in names:
        p = Path(name)
        if not p.is_absolute():
            p = data_path.parent / p
        if p.is_file():
            est = p.stat()
            extras.append((str(p), est.st_mtime_ns, est.st_size))
    return (str(data_path.resolve()), st.st_mtime_ns, st.st_size, tuple(blob), tuple(extras))

def preview_workbook(data_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    from modules.utils.u_qc import _external_specs_from_settings

    data_path = Path(data_path)
    try:
        key = _preview_cache_key(data_path, settings)
    except OSError:
        key = None
    if key is not None and key in _PREVIEW_CACHE:
        return _PREVIEW_CACHE[key]
    main = _preview_one_workbook(data_path, settings)
    externals: list[dict[str, Any]] = []
    for spec in _external_specs_from_settings(settings):
        externals.append(_preview_external_one(data_path, settings, spec))
    main["external"] = externals
    if key is not None:
        if len(_PREVIEW_CACHE) > 8:
            _PREVIEW_CACHE.clear()
        _PREVIEW_CACHE[key] = main
    return main

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

def read_project_overlay(proj: Path) -> dict[str, Any]:
    path = _overlay_path(proj)
    if not path.is_file():
        old = Path(proj) / ".rad_console_overlay.json"
        path = old if old.is_file() else path
    if not path.is_file():
        return {}
    try:
        return unwrap_overlay_file(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}

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

def find_results(project: str) -> list[dict[str, Any]]:
    from modules.config.outcome import is_results_html_name
    from modules.utils.u_qc import is_qc_html_name, relocate_legacy_qc_html
    proj = project_dir(project)
    relocate_legacy_qc_html(proj)
    hits = []
    for p in proj.rglob("*.html"):
        try:
            rel_parts = p.relative_to(proj).parts
        except ValueError:
            continue
        name_l = p.name.lower()
        in_ref_qc = (
            len(rel_parts) == 2
            and rel_parts[0].lower() == "ref"
            and is_qc_html_name(p.name)
        )
        if any(part in _RESULT_SKIP_DIRS for part in rel_parts[:-1]) and not in_ref_qc:
            continue
        if name_l in {
            f"{project.lower()}.html", "console.html", "flow_chart.html",
            "index.html", "project.html",
        }:
            continue
        is_qc = in_ref_qc
        is_res = is_results_html_name(p.name)
        if not (is_qc or is_res):
            continue
        rel = p.relative_to(proj).as_posix()
        hits.append({
            "name": p.name,
            "rel": rel,
            "kind": "qc" if is_qc else "results",
            "mtime": p.stat().st_mtime,
            "size": p.stat().st_size,
        })
    def _qc_rank(item: dict[str, Any]) -> tuple:
        if item["kind"] != "qc":
            return (1, 0, -item["mtime"])
        name = str(item.get("name") or "").lower()
        if name == "qc.html":
            return (0, 0, -item["mtime"])
        return (0, 1, -item["mtime"])
    hits.sort(key=_qc_rank)
    return hits[:80]

def open_result_folder(project: str, rel: str) -> dict[str, Any]:
    proj = project_dir(project)
    rel_s = str(rel or "").strip().replace("\\", "/")
    if not rel_s or rel_s.startswith("/") or ".." in Path(rel_s).parts:
        raise ValueError(f"结果相对路径无效: {rel!r}")
    target = under_project(proj, Path(rel_s))
    if target.is_file():
        folder = target.parent
    elif target.is_dir():
        folder = target
    else:
        raise FileNotFoundError(f"结果路径不存在: {target}")
    folder = under_project(proj, folder)
    if sys.platform == "win32":
        os.startfile(str(folder))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(folder)], check=True)
    else:
        subprocess.run(["xdg-open", str(folder)], check=True)
    return {"ok": True, "folder": str(folder), "rel": rel_s}

def pick_file_dialog(initial_dir: str, title: str = "选择 Excel") -> dict[str, Any]:
    raw_dir = str(initial_dir or "").strip()
    if not raw_dir:
        raise ValueError("缺少 dir：起始页须用 file:// 打开，才能定位 html 所在文件夹")
    start = Path(raw_dir).resolve()
    if not start.is_dir():
        raise FileNotFoundError(f"起始文件夹不存在: {start}")
    proc = subprocess.run(
        [sys.executable, "-c", _PICK_XLSX_SCRIPT, str(start), str(title or "选择 Excel")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"文件对话框失败: {proc.stderr.strip()}")
    picked_raw = proc.stdout.strip()
    if not picked_raw:
        return {"ok": True, "path": "", "rel": "", "dir": str(start)}
    picked = Path(picked_raw).resolve()
    if picked.is_relative_to(start):
        rel = picked.relative_to(start).as_posix()
    else:
        rel = str(picked)
    return {"ok": True, "path": str(picked), "rel": rel, "dir": str(start)}

# Port helpers / serve below; HTTP Handler installed at module end.

def load_preferred_port() -> int:
    if PORT_FILE.is_file():
        try:
            obj = json.loads(PORT_FILE.read_text(encoding="utf-8"))
            n = int(obj.get("port") or DEFAULT_PORT)
            if 1024 <= n <= 65535:
                return n
        except Exception as e:
            print(f"  ⚠ console.port.json ignored: {e}", flush=True)
    return DEFAULT_PORT

def save_preferred_port(port: int) -> None:
    PORT_FILE.write_text(
        json.dumps({"port": int(port)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

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
