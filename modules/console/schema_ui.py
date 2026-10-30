"""控制台 UI schema 构建（B4c1：从 console.py 逐字搬出）。

纯函数层：只依赖 const（常量）、schema（元数据表）与 ops（jsonable）。
不 import console，也不 import http_impl；console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

import inspect
import types
from typing import Any

from modules.console.const import (
    CLIN_COL_SINGLE_KEYS,
    CONSOLE_DEFAULTS,
    EXCLUDE_EMPTY_LABELS,
    EXCLUDE_SELECT_KEYS,
    LIST_FROM_CLIN,
    LIST_FROM_RAD_META,
    RAD_COL_SINGLE_KEYS,
    SHEET_KEYS,
    SKIP_SETTING_KEYS,
    _RAD_FEAT_PREFIX,
)
from modules.console.ops import jsonable
from modules.console.schema import (
    HINTS,
    HOT_KEYS,
    LABELS,
    SECTION_DEFS,
    SECTION_OF,
    _ADDSELECT_LABEL,
    _COLUMN_MULTI,
    _LABEL_DISPLAY_CANDS,
    _MODEL_CHOICES,
)

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

def _column_matches_prefix(name: str, prefix: str) -> bool:
    """与 data_io._column_matches_prefix 同规则。"""
    from modules.stats.data_io import _column_matches_prefix as _match
    return _match(name, prefix)

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
        field["options"] = ["lock_threshold", "apply_formula", "refit"]
        field["option_labels"] = {
            "lock_threshold": "锁定阈值（独立验证）",
            "apply_formula": "套公式",
            "refit": "重新拟合/模型更新",
        }
        if key == "SUBGROUP_VAL_MODE":
            field["options"] = [""] + list(field["options"])
            field["option_labels"][""] = "空白=套公式"
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
