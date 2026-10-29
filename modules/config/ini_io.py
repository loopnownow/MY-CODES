# -*- coding: utf-8 -*-
"""Project settings.ini — JSON values, sectioned INI, case-preserving."""
from __future__ import annotations

import configparser
import json
import math
import os
import re
import shutil
import sys
import types
from pathlib import Path
from typing import Any

from modules.console.paths import DEFAULT_GROK_ROOT

TUPLE_LIST_KEYS = {"DAG_EDGES", "DAG_MEDIATION_HIGHLIGHT"}
SKIP_KEYS = {
    "os", "copy", "List", "Optional", "Dict", "Any", "annotations", "PNG_DIR",
}
# 已退役：控制台不显示。等于默认则读入后丢掉、写回省略。
# 非默认保留，写在 [retired]。读旧 ini 不报错。
# GROUP_SOURCE_COL / OUT_DIR_BASE 是运行时槽，始终不写回。
RETIRED_KEYS = {
    "FORCE_REMOVE_PREFIXES",
    "OUTCOME_POSITIVE_LABELS",
    "PRESERVE_GROUP_AS",
    "GROUP_SOURCE_COL",
    "OUT_DIR_BASE",
    "DO_VIF",
    "VIF_THRESHOLD_HIGH",
    "RAD_LOGIT_VIF",
    "MEDIATION_BOOTSTRAP",
    "DO_PLOT",
    "FIG_FONT_FAMILY",
}
INI_SECTIONS: list[tuple[str, str, list[str]]] = [
    ("repro", "可重复性", ["DO_REPRO", "DRY_RUN", "SEED", "TRAIN_RATIO", "SPLIT_COL"]),
    ("io", "数据与路径", ["DO_IO",
        "INPUT", "MODULES_DIR", "RAD_SHEET", "CLIN_SHEET", "QC_SHEET", "ICC_SHEET",
        "RAD_ID_COL", "CLIN_ID_COL", "CLIN_PATH",
        "RAD_PATH", "LABEL_COL", "OUT_DIR",
        "OUTCOME_OUT_SUBDIR",
        "EXTERNAL_FILE", "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
        "USE_INTERNAL_SPLIT_WHEN_EXTERNAL",
        "OUTCOME_COLS", "OUTCOME_OVERRIDES", "PAIRWISE_OVERRIDES",
        "SUBPROJECT_BY_OUTCOME", "GROUP_COL_KEYWORD",
        "POSITIVE_GROUP_LABEL", "GROUP_DISPLAY_POS", "GROUP_DISPLAY_NEG",
        "DO_BASELINE_ALL", "DO_BASELINE_TRAIN_TEST",
        "EXTERNAL_TESTS",
        "DO_EXCLUDE_SAMPLES",
        "EXCLUDE_FILE", "EXCLUDE_SHEET", "EXCLUDE_COL", "EXCLUDE_ROW", "EXCLUDE_IDS",
        "READER_VALUE_MAP", "OUTCOME_BINARIZE",
        "GROUP_KEEP", "PAIRWISE_GROUPS", "PAIRWISE_ALL",
        "GROUP_FROM_ID", "GROUP_FROM_ID_SEP", "GROUP_LEVEL_DISPLAY",
        "SUBGROUP_COL",
        "VAL_MODE", "SUBGROUP_VAL_MODE", "EXTERNAL_VAL_MODE",
    ]),
    ("clinprep", "临床 / 预处理", [
        "CLIN_FORCE_CONTINUOUS", "CLIN_FORCE_CATEGORICAL", "SKIP_CLIN_FEATURES",
        "ORDINAL_MAX_LEVELS",
    ]),
    ("clin", "临床筛选", [
        "DO_CLIN",
        "DO_UNI_LOGISTIC_TABLE", "DO_MV_LOGISTIC_TABLE",
        "TABLE1_P_THRESHOLD",
        "FORCE_REMOVE_FEATURES", "FORCE_INTER_FEATURES", "FORCE_MODEL_FEATURES",
        "READER_COLS",
        "RAD_ID_COLS",
        "DO_FIRTH",
    ]),
    ("ancova", "协方差分析", [
        "DO_ANCOVA", "ANCOVA_OUTCOMES", "ANCOVA_GROUP", "ANCOVA_COVARIATES",
    ]),
    ("mlr", "多元线性回归", [
        "DO_MLR", "MLR_OUTCOMES", "MLR_GROUP", "MLR_COVARIATES",
    ]),
    ("corr", "相关热图", [
        "DO_HEATMAP", "HEATMAP_COLS", "CORR_GROUP",
    ]),
    ("pcorr", "相关/偏相关分析", [
        "DO_PARTIAL", "PARTIAL_X", "PARTIAL_Y", "PARTIAL_CONTROLS", "PARTIAL_METHOD",
        "PARTIAL_GROUP",
        "DO_CORR", "CORR_PREDICTORS", "CORR_REFS", "CORR_E_REF", "CORR_F_REF",
    ]),
    ("switch", "模块开关", [
        "DO_FALSE_CLASS", "DO_FDR", "DO_FWER",
        "VIF_THRESHOLD_WARN", "BOOTSTRAP_N",
        "DO_QC_REPORT", "QC_HTML",
        "DO_COMBINED_MODEL",
        "DO_NOMOGRAM", "DO_CALIBRATION",
        "DO_ROC", "ROC_MV_P_THRESHOLD", "FORCE_CLIN_FOR_ROC", "ROC_PLOT_EXCLUDE",
        "DO_DCA", "DCA_CURVES",
        "DO_NRI", "NRI_CUTOFF", "NRI_MODEL_OLD", "NRI_MODEL_NEW",
        "OUTCOME_READER_COMPARE", "READER_COMPARE_COL",
        "READER_ALL_IN_ROC", "NRI_OLD_COL", "NRI_NEW_COL",
    ]),
    ("surv", "生存曲线", [
        "DO_SURVIVAL", "SURVIVAL_TIME_COL", "SURVIVAL_TIME_UNIT",
        "DO_SURVIVAL_BY_RISK", "DO_COX",
        "DO_TIME_DEPENDENT_ROC",
        "TD_IPCW_TIME_COL", "TD_IPCW_STATUS_COL", "TD_IPCW_HORIZONS",
        "DO_LANDMARK_ROC",
        "TD_ROC_TIMES", "TD_ROC_STATUS_COLS",
    ]),
    ("advanced", "高级分析", [
        "DO_ADVANCED", "DO_DAG", "DO_SHAP", "DO_MEDIATION",
        "SHAP_MAX_SAMPLES",
        "DAG_NODES", "DAG_EDGES",
        "DAG_MEDIATION_HIGHLIGHT", "MEDIATION_PATHS",
    ]),
    ("radprep", "组学 / 预处理", [
        "DO_ICC_FILTER", "ICC_REQUIRE_ROW", "ENABLE_CORR_FILTER", "LASSO_CACHE", "RAD_COR_CUTOFF",
        "ICC_THRESHOLD", "ICC_FEATURE_COL", "ICC_INTRA_COL", "ICC_INTER_COL",
        "RAD_MARKER_SIDE", "RAD_MARKER_MIN_N",
        "RAD_FEATURE_PREFIX_EXCLUDE", "RAD_FEATURE_DISPLAY_STRIP",
        "RAD_FEATURE_PREFIX_GROUP",
    ]),
    ("rad", "组学 / LASSO", [
        "DO_LASSO", "RAD_FEATURE_SOURCE", "ALPHA", "LASSO_NFOLDS",
        "LASSO_N_LAMBDAS", "LASSO_MAX_ITER", "LASSO_N_JOBS", "LASSO_C_MAX",
        "LASSO_C_MIN", "LASSO_TOL", "LASSO_PREFILTER_TOPK", "LASSO_PLOT_PATH",
    ]),
    ("radlog", "组学 / Logistic", [
        "DO_RAD_LOGISTIC",
        "RAD_LOGIT_UNI_P", "RAD_LOGIT_MV_P", "DO_RAD_FIRTH",
    ]),
    ("fig", "图与显示", [
        "DO_FIGURE_PANELS",
        "DO_PLOT_TIFF", "DO_PLOT_PDF",
        "FIGURE_PANEL_DPI", "FIGURE_PANEL_MEDIATION_NCOLS",
        "FIGURE_PANEL_LABEL_POS", "FIGURE_PANEL_LABELS", "FIGURE_PANEL_GAP_PX",
        "FIG_FONT_SIZE", "FIG_PALETTE", "FIG_COLORS_MAIN", "FIG_COLORS_AUX",
    ]),
]
_KEY_SEC = {k: sid for sid, _t, ks in INI_SECTIONS for k in ks}
# 不在 DEFAULT_SETTINGS 里、但等于这些值时视为无用默认，不写回。
_RETIRED_KNOWN_DEFAULTS = {
    "FORCE_REMOVE_PREFIXES": [],
    "OUTCOME_POSITIVE_LABELS": {},
    "PRESERVE_GROUP_AS": "",
    "DO_VIF": False,
    "MEDIATION_BOOTSTRAP": 1000,
}
_RUNTIME_DROP_KEYS = {"GROUP_SOURCE_COL", "OUT_DIR_BASE"}


def _retired_is_default(key: str, value: Any) -> bool:
    """隐藏退役键是否等于默认。运行时槽一律当默认，不写回。"""
    if key in _RUNTIME_DROP_KEYS:
        return True
    from modules.config.defaults import DEFAULT_SETTINGS
    if key in DEFAULT_SETTINGS:
        return value == DEFAULT_SETTINGS[key]
    if key in _RETIRED_KNOWN_DEFAULTS:
        return value == _RETIRED_KNOWN_DEFAULTS[key]
    return value in (None, "", [], {})


def _lift_landmark_roc(out: dict[str, Any]) -> None:
    """旧 ini 没有 DO_LANDMARK_ROC 时，Landmark 跟随 DO_TIME_DEPENDENT_ROC。

    两键都在时不改。这是读入兼容，不是运行时把时间依赖性 ROC 当成 Landmark。
    """
    if "DO_LANDMARK_ROC" in out:
        return
    if "DO_TIME_DEPENDENT_ROC" not in out:
        return
    out["DO_LANDMARK_ROC"] = bool(out.get("DO_TIME_DEPENDENT_ROC"))


def _choice_names(value: Any) -> list[str]:
    """收成去空、去重的名字。数字原样转成文字，调用方自行决定是否拒绝。"""
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        seen: set[str] = set()
        for x in value:
            if x is None:
                continue
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return []
    s = str(value).strip()
    return [s] if s else []


def name_choice(value: Any) -> str | list[str]:
    """空 → ""。一个名字保持字符串。多个名字 → 列表。

    旧 ini 里的单文件、单列不用改成列表。
    """
    names = _choice_names(value)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return names


def coerce_td_roc_field(key: str, value: Any) -> str | list[str]:
    """TD_ROC_TIMES 与 TD_ROC_STATUS_COLS 都是临床列名，行数对齐。

    空收成 ""；一个列名保持字符串；多个列名是字符串列表，空位保留以便和另一列对齐。
    数字、数字列表、时间→列的字典都不是列名，直接报错。不把数字解释成月份。
    """
    if key == "TD_ROC_TIMES":
        if value in (None, "", []):
            return ""
        if isinstance(value, bool) or isinstance(value, (int, float)):
            raise ValueError(
                f"TD_ROC_TIMES={value!r} 是数字。须改成临床表里的时间列名，不能再写月份。"
            )
        if isinstance(value, dict):
            raise ValueError(
                f"TD_ROC_TIMES={value!r} 无法当作临床列名。须改成临床表里的列。"
            )
        if isinstance(value, (list, tuple)):
            names: list[str] = []
            for x in value:
                if isinstance(x, bool) or isinstance(x, (int, float)):
                    raise ValueError(
                        f"TD_ROC_TIMES={list(value)!r} 仍含数字。"
                        "须改成临床列名，不能再写月份。"
                    )
                if x is None:
                    names.append("")
                    continue
                if not isinstance(x, str):
                    raise ValueError(
                        f"TD_ROC_TIMES={list(value)!r} 无法当作临床列名。须改成临床表里的列。"
                    )
                names.append(x.strip())
            if not any(names):
                return ""
            if len(names) == 1:
                return names[0]
            return names
        if isinstance(value, str):
            return name_choice(value)
        raise ValueError(
            f"TD_ROC_TIMES={value!r} 无法当作临床列名。须改成临床表里的列。"
        )
    if key == "TD_ROC_STATUS_COLS":
        if value in (None, "", [], {}):
            return ""
        if isinstance(value, dict):
            raise ValueError(
                f"TD_ROC_STATUS_COLS={value!r} 须是状态列名，不是时间到列名的映射。"
            )
        if isinstance(value, bool) or isinstance(value, (int, float)):
            raise ValueError(
                f"TD_ROC_STATUS_COLS={value!r} 是数字。须改成临床表里的状态列名。"
            )
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            names: list[str] = []
            for x in value:
                if isinstance(x, bool) or isinstance(x, (int, float)):
                    raise ValueError(
                        f"TD_ROC_STATUS_COLS={list(value)!r} 仍含数字。"
                        "须改成临床列名。"
                    )
                if x is None:
                    names.append("")
                    continue
                if not isinstance(x, str):
                    raise ValueError(
                        f"TD_ROC_STATUS_COLS={list(value)!r} 无法当作临床列名。"
                    )
                names.append(x.strip())
            if not any(names):
                return ""
            if len(names) == 1:
                return names[0]
            return names
        raise ValueError(
            f"TD_ROC_STATUS_COLS={value!r} 须是状态列名。"
        )
    raise ValueError(f"未知 tdROC 字段 {key}")


def coerce_ipcw_horizons(value: Any) -> list[float]:
    if value in (None, "", []):
        return []
    if isinstance(value, bool):
        raise ValueError(f"TD_IPCW_HORIZONS={value!r} 须是评估月数字，如 3, 6, 12, 24。")
    if isinstance(value, (int, float)):
        items: list[Any] = [value]
    elif isinstance(value, str):
        items = [x for x in re.split(r"[\s,;，；]+", value.strip()) if x]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"TD_IPCW_HORIZONS={value!r} 须是评估月数字，如 3, 6, 12, 24。")
    out: list[float] = []
    for x in items:
        if isinstance(x, bool):
            raise ValueError(f"TD_IPCW_HORIZONS 含非数字 {x!r}。")
        try:
            v = float(x)
        except (TypeError, ValueError):
            raise ValueError(f"TD_IPCW_HORIZONS 含非数字 {x!r}。须是评估月，如 3, 6, 12, 24。") from None
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"TD_IPCW_HORIZONS 含 {x!r}。评估月须为正数。")
        if v not in out:
            out.append(v)
    return sorted(out)


def migrate_retired_settings(data: dict[str, Any]) -> dict[str, Any]:
    """旧键迁到现用字段后丢掉退役项。

    GROUP_SOURCE_COL 且 OUTCOME_COLS 为空 → OUTCOME_COLS = [该列]。
    OUT_DIR_BASE 直接丢弃（输出目录由结局名 / SUBPROJECT_BY_OUTCOME 决定）。
    """
    out = dict(data or {})
    cols = [str(x).strip() for x in (out.get("OUTCOME_COLS") or []) if str(x).strip()]
    g = str(out.get("GROUP_SOURCE_COL") or "").strip()
    if not cols and g:
        out["OUTCOME_COLS"] = [g]
    if "DO_PLOT_TIFF" not in out and "DO_PLOT_PDF" not in out and "DO_PLOT" in out:
        old = bool(out.get("DO_PLOT"))
        out["DO_PLOT_TIFF"] = old
        out["DO_PLOT_PDF"] = old
    # MEDIATION_BOOTSTRAP → BOOTSTRAP_N (keep old key as alias until stripped elsewhere)
    if "BOOTSTRAP_N" not in out and "MEDIATION_BOOTSTRAP" in out:
        try:
            out["BOOTSTRAP_N"] = int(out.get("MEDIATION_BOOTSTRAP") or 1000)
        except (TypeError, ValueError):
            out["BOOTSTRAP_N"] = 1000
    _lift_landmark_roc(out)
    for key in ("TD_ROC_TIMES", "TD_ROC_STATUS_COLS"):
        if key in out:
            out[key] = coerce_td_roc_field(key, out[key])
    for k in list(RETIRED_KEYS):
        if k not in out:
            continue
        if _retired_is_default(k, out[k]):
            out.pop(k, None)
    return out


def decode_value(raw: str) -> Any:
    s = (raw or "").strip()
    if s == "":
        return ""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        low = s.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in ("none", "null"):
            return None
        return s


def encode_value(val: Any) -> str:
    if isinstance(val, tuple):
        val = list(val)
    if isinstance(val, list):
        val = [list(x) if isinstance(x, tuple) else x for x in val]
    return json.dumps(val, ensure_ascii=False)


def read_ini(path: Path) -> dict[str, Any]:
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    cp.read_string(text)
    out: dict[str, Any] = {}
    for sec in cp.sections():
        for k, raw in cp.items(sec):
            if k.startswith("_") or k in SKIP_KEYS:
                continue
            out[k] = decode_value(raw)
    for k in TUPLE_LIST_KEYS:
        if k in out and isinstance(out[k], list):
            out[k] = [tuple(x) if isinstance(x, list) else x for x in out[k]]
    out = migrate_retired_settings(out)
    out_dir = out.get("OUT_DIR") or ""
    out["PNG_DIR"] = os.path.join(str(out_dir), "PNG") if out_dir else "PNG"
    qc_html = out.get("QC_HTML")
    if not qc_html or _is_default_qc_html(qc_html):
        out["QC_HTML"] = "ref/qc.html"
    return out


_DEFAULT_QC_HTML = {
    "output/qc.html",
    "output/qc/qc.html",
    "qc/qc.html",
    "qc.html",
    "ref/qc.html",
}


def _is_default_qc_html(path: str | Path) -> bool:
    norm = str(path).replace("\\", "/").strip().lower().lstrip("./")
    return norm in _DEFAULT_QC_HTML



def ensure_settings_ini(path: Path, seed: dict[str, Any] | None = None) -> Path:
    """若 settings.ini 不存在则新建（含 ref/）；已存在则原样返回。

    seed 为首次写入的键；默认空字典，只写文件头，由控制台保存填全量。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        write_ini(path, dict(seed or {}))
    return path


def write_ini(path: Path, data: dict[str, Any]) -> None:
    buckets: dict[str, list[tuple[str, Any]]] = {sid: [] for sid, _t, _k in INI_SECTIONS}
    buckets["other"] = []
    seen: set[str] = set()
    for sid, _t, keys in INI_SECTIONS:
        for k in keys:
            if k in data and not k.startswith("_") and k not in SKIP_KEYS and k not in RETIRED_KEYS:
                buckets[sid].append((k, data[k]))
                seen.add(k)
    # 退役键等于默认：不写。非默认：写到 [retired]，下次读入仍认。
    legacy = [
        (k, data[k]) for k in data
        if k in RETIRED_KEYS and not str(k).startswith("_") and not _retired_is_default(k, data[k])
    ]
    legacy.sort(key=lambda kv: kv[0])

    lines = [
        "# RAD 工程配置（只改本文件）",
        "# 值用 JSON：true/false/null、数字、字符串带引号、列表/对象。",
        "# 运行: <工程名>.html   配置: ref/settings.ini   算法: <STATS_ROOT>/modules",
        "",
    ]
    for sid, title, _keys in INI_SECTIONS:
        items = buckets.get(sid) or []
        if not items:
            continue
        lines.append(f"[{sid}]  ; {title}")
        for k, v in items:
            lines.append(f"{k} = {encode_value(v)}")
        lines.append("")
    if legacy:
        lines.append("[retired]  ; 旧键，值不是默认")
        for k, v in legacy:
            lines.append(f"{k} = {encode_value(v)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _setting_keys() -> set[str]:
    keys = set(RETIRED_KEYS)
    for _sid, _title, names in INI_SECTIONS:
        keys.update(names)
    return keys


def keys_in_settings_error(message: str) -> list[str]:
    """从设置报错里取出键名。只认已知设置键，避免把说明文字里的词清掉。"""
    text = str(message or "")
    known = _setting_keys()
    found: list[str] = []

    def add(name: str) -> None:
        if name in known and name not in found:
            found.append(name)

    for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*)\s*=", text):
        add(match.group(1))
    for match in re.finditer(r"未知 tdROC 字段\s+([A-Z][A-Z0-9_]*)", text):
        add(match.group(1))
    if not found:
        for match in re.finditer(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*)(?=\s*须)", text):
            add(match.group(1))
    return found


_INI_ASSIGN = re.compile(r"^([ \t]*)([A-Z][A-Z0-9_]*)[ \t]*=[ \t]*.*$")


def blank_ini_keys(path: Path, keys: list[str]) -> dict[str, Any]:
    """把指名的键写成空字符串。不解析值，所以坏键也能清。先备份 .bak。"""
    path = Path(path)
    if path.suffix.lower() != ".ini":
        raise ValueError("只能清空 settings.ini 里的键")
    if not path.is_file():
        raise FileNotFoundError(f"没有配置: {path}")
    known = _setting_keys()
    wanted: list[str] = []
    for raw in keys:
        name = str(raw).strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            continue
        if name not in known or name in wanted:
            continue
        wanted.append(name)
    if not wanted:
        raise ValueError("没有可清空的报错键")
    text = path.read_text(encoding="utf-8")
    bom = text.startswith("\ufeff")
    if bom:
        text = text[1:]
    want = set(wanted)
    found: list[str] = []
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        body, nl = line, ""
        if body.endswith("\r\n"):
            body, nl = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, nl = body[:-1], "\n"
        elif body.endswith("\r"):
            body, nl = body[:-1], "\r"
        match = _INI_ASSIGN.match(body)
        if match and match.group(2) in want:
            if match.group(2) not in found:
                found.append(match.group(2))
            out.append(f'{match.group(1)}{match.group(2)} = ""{nl}')
        else:
            out.append(body + nl)
    if not found:
        raise ValueError("配置里没有这些键：" + "、".join(wanted))
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    path.write_text(("\ufeff" if bom else "") + "".join(out), encoding="utf-8")
    return {
        "cleared": found,
        "missing": [k for k in wanted if k not in found],
        "backup": str(bak),
    }


def namespace_from_dict(data: dict[str, Any], name: str = "settings") -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in data.items():
        setattr(mod, k, v)
    return mod


def load_ini_namespace(path: Path) -> types.ModuleType:
    return namespace_from_dict(read_ini(path), "modules.config.settings")


def resolve_project_dir(proj: Path | None = None) -> Path:
    """当前子工程根（如 <GROK_ROOT>/drafting/<study>），不是 lab 仓库根。

    优先级：显式路径 > 环境变量 RAD_PROJECT > cwd。
    """
    if proj is not None:
        return Path(proj)
    env = os.environ.get("RAD_PROJECT", "").strip()
    return Path(env) if env else Path.cwd()


def project_ref_dir(proj: Path | None = None) -> Path:
    """工程下的 ref/：settings.ini、运行日志、QC HTML、审稿 md。"""
    d = resolve_project_dir(proj) / "ref"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_ini_candidates(proj: Path) -> list[Path]:
    p = Path(proj)
    return [p / "ref" / "settings.ini", p / "settings.ini"]


def resolve_settings_ini(proj: Path) -> Path:
    """已有 ini 优先 ref/settings.ini；没有则返回默认写出路径。"""
    for p in settings_ini_candidates(proj):
        if p.is_file():
            return p
    return Path(proj) / "ref" / "settings.ini"


def _lab_grok_root() -> Path:
    """Lab fallback root; GROK_ROOT 优先（与 console.paths.grok_root 同口径）。"""
    env = os.environ.get("GROK_ROOT", "").strip()
    return Path(env).expanduser() if env else DEFAULT_GROK_ROOT


def find_settings_ini(start: Path | None = None) -> Path | None:
    env = os.environ.get("RAD_PROJECT", "").strip()
    if env:
        hit = resolve_settings_ini(Path(env))
        if hit.is_file():
            return hit
    cwd = Path(start) if start else Path.cwd()
    grok = _lab_grok_root()
    for base in (cwd, *cwd.parents):
        for p in settings_ini_candidates(base):
            if not p.is_file():
                continue
            try:
                p.resolve().relative_to(grok)
            except ValueError:
                continue
            return p
    return None


def apply_to_globals(ns: dict[str, Any], data: dict[str, Any]) -> types.ModuleType:
    for k, v in data.items():
        ns[k] = v
    settings = namespace_from_dict(data, "modules.config.settings")
    sys.modules["modules.config.settings"] = settings
    ns["settings"] = settings
    return settings
