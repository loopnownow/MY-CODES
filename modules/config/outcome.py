"""set_outcome — 切换结局并同步 config 包与 settings 模块。"""
from __future__ import annotations

import copy
import os

# 公共值 + OUTCOME_OVERRIDES[结局] 可覆盖的键。缺键=继承公共。
OUTCOME_OVERRIDE_KEYS = frozenset({
    "GROUP_DISPLAY_POS",
    "GROUP_DISPLAY_NEG",
    "SEED",
    "TRAIN_RATIO",
    "DO_EXCLUDE_SAMPLES",
    "EXCLUDE_FILE",
    "EXCLUDE_SHEET",
    "EXCLUDE_COL",
    "EXCLUDE_ROW",
    "EXCLUDE_IDS",
    "SKIP_CLIN_FEATURES",
    "FORCE_INTER_FEATURES",
    "FORCE_MODEL_FEATURES",
    "FORCE_REMOVE_FEATURES",
    "SUBGROUP_COL",
    "GROUP_KEEP",
    "PAIRWISE_GROUPS",
    "PAIRWISE_ALL",
    "GROUP_FROM_ID",
    "GROUP_FROM_ID_SEP",
    "POSITIVE_GROUP_LABEL",
    "GROUP_LEVEL_DISPLAY",
    "FORCE_CLIN_FOR_ROC",
    "ROC_PLOT_EXCLUDE",
    "DO_DCA",
    "DCA_CURVES",
    "DO_NRI",
    "NRI_CUTOFF",
    "NRI_MODEL_OLD",
    "NRI_MODEL_NEW",
    "CORR_PREDICTORS",
    "CORR_REFS",
    "CORR_E_REF",
    "CORR_F_REF",
    "DO_DAG",
    "DO_SHAP",
    "DO_MEDIATION",
    "DAG_NODES",
    "DAG_EDGES",
    "DAG_MEDIATION_HIGHLIGHT",
    "MEDIATION_PATHS",
    "FIG_PALETTE",
    "FIG_COLORS_MAIN",
    "FIG_COLORS_AUX",
})

# 对子页可覆盖的键：与结局覆盖同一套，但不改「有哪些对」。
PAIR_OVERRIDE_KEYS = frozenset(
    OUTCOME_OVERRIDE_KEYS
    - {
        "GROUP_KEEP",
        "PAIRWISE_GROUPS",
        "PAIRWISE_ALL",
        "GROUP_FROM_ID",
        "GROUP_FROM_ID_SEP",
        "POSITIVE_GROUP_LABEL",
        "GROUP_LEVEL_DISPLAY",
        "SUBGROUP_COL",
    }
)


def pair_key(pos: str, neg: str) -> str:
    return f"{str(pos).strip()}_vs_{str(neg).strip()}"


def safe_dir_token(name: str) -> str:
    s = str(name or "").strip() or "unnamed"
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "-")
    s = "_".join(s.split())
    return s or "unnamed"


def results_html_name(subproject: str) -> str:
    """结果页文件名：Results + 子项目名 + .html。例：perfusion → Resultsperfusion.html。"""
    return "Results" + safe_dir_token(subproject) + ".html"


def is_results_html_name(filename: str) -> bool:
    """新名 Results{子项目}.html，以及旧的 {结局}-results.html / results.html。"""
    n = str(filename or "").strip().lower()
    if not n.endswith(".html"):
        return False
    if n.endswith("-results.html") or n == "results.html":
        return True
    stem = n[:-5]
    return stem.startswith("results") and len(stem) > len("results")


def pair_dir_name(pos_disp: str, neg_disp: str) -> str:
    """结果子目录：阳性展示名_vs_阴性展示名。"""
    return f"{safe_dir_token(pos_disp)}_vs_{safe_dir_token(neg_disp)}"


def level_display_name(raw: str, mapping=None, S=None) -> str:
    """列值 → 短展示名。缺映射则原样。"""
    s = str(raw or "").strip()
    if mapping is None and S is not None:
        mapping = getattr(S, "GROUP_LEVEL_DISPLAY", None) or {}
    if isinstance(mapping, dict):
        v = mapping.get(s)
        if v is not None and str(v).strip():
            return str(v).strip()
    return s


def pair_override_map(S, outcome: str, pair: str) -> dict:
    raw = getattr(S, "PAIRWISE_OVERRIDES", None) or {}
    if not isinstance(raw, dict):
        return {}
    by_out = raw.get(outcome) or {}
    if not isinstance(by_out, dict):
        return {}
    ov = by_out.get(pair) or {}
    return ov if isinstance(ov, dict) else {}


def snapshot_outcome_base(S, cfg) -> dict:
    """第一次切换结局时冻结公共值，避免上一结局的改写漏到下一结局。"""
    cached = getattr(S, "_OUTCOME_BASE", None)
    if isinstance(cached, dict) and cached:
        return cached
    base: dict = {}
    for k in OUTCOME_OVERRIDE_KEYS:
        if hasattr(S, k):
            base[k] = copy.deepcopy(getattr(S, k))
        elif hasattr(cfg, k):
            base[k] = copy.deepcopy(getattr(cfg, k))
    setattr(S, "_OUTCOME_BASE", base)
    setattr(cfg, "_OUTCOME_BASE", copy.deepcopy(base))
    return base


def outcome_override_map(S, outcome: str) -> dict:
    raw = getattr(S, "OUTCOME_OVERRIDES", None) or {}
    if not isinstance(raw, dict):
        return {}
    ov = raw.get(outcome) or {}
    return ov if isinstance(ov, dict) else {}



def _implicit_skip(S) -> list[str]:
    cols: list[str] = []
    for k in ("CLIN_ID_COL", "LABEL_COL", "SPLIT_COL"):
        v = str(getattr(S, k, "") or "").strip()
        if v:
            cols.append(v)
    cols.extend(["Patient_ID", "Group"])
    for c in getattr(S, "OUTCOME_COLS", None) or []:
        s = str(c).strip()
        if s:
            cols.append(s)
    seen: set[str] = set()
    out: list[str] = []
    for c in cols:
        if c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def map_outcome_out_dir(
    outcome: str,
    cohort: str = "",
    mapping: dict | None = None,
    use_subdir: bool = True,
) -> str:
    """分析输出目录（相对工程根）。

    SUBPROJECT_BY_OUTCOME 将结局归到子工程：
      单结局子工程 → {子工程}/{队列}
      多结局子工程 → {子工程}/{队列}/{结局}
    无映射时保持 {队列}/{结局}（队列空则只有结局名）。
    """
    mapping = mapping or {}
    cohort = str(cohort or "").strip()
    sub = str(mapping.get(outcome) or "").strip()
    if sub:
        n = sum(1 for v in mapping.values() if str(v) == sub)
        if n <= 1:
            return os.path.join(sub, cohort) if cohort else sub
        if cohort:
            return os.path.join(sub, cohort, outcome)
        return os.path.join(sub, outcome)
    if use_subdir:
        return os.path.join(cohort, outcome) if cohort else outcome
    return cohort


def subproject_root(
    outcome: str = "",
    out_dir: str = "",
    mapping: dict | None = None,
    use_subdir: bool = True,
) -> str:
    """子工程根（相对工程根）。

    有 SUBPROJECT_BY_OUTCOME → 映射名（Response / DSI）。
    否则 OUT_DIR 第一段，或结局名。空=工程根。
    """
    outcome = str(outcome or "").strip()
    mapping = mapping if isinstance(mapping, dict) else {}
    if outcome:
        sub = str(mapping.get(outcome) or "").strip()
        if sub:
            return sub
    out_dir = str(out_dir or "").strip()
    if out_dir and out_dir not in (".",):
        first = os.path.normpath(out_dir).replace("\\", "/").split("/")[0]
        if first and first not in (".", ""):
            return first
    if use_subdir and outcome:
        return outcome
    return ""


def outcome_display_labels(
    outcome: str,
    *,
    base_pos: str = "",
    base_neg: str = "",
    override: dict | None = None,
    n_outcomes: int = 1,
) -> tuple[str, str]:
    """图/表用的阳、阴展示名。

    该结局覆盖里手填的名字优先。没手填时用分组卡上的
    GROUP_DISPLAY_POS / GROUP_DISPLAY_NEG，多结局也一样。
    不把表头改成结局列名 / non_结局列名。
    """
    ov = override if isinstance(override, dict) else {}
    outcome = str(outcome or "").strip()
    base_pos = str(base_pos or "").strip()
    base_neg = str(base_neg or "").strip()
    _ = n_outcomes  # 结局个数不再改展示名；参数保留，避免调用处再拆一套。

    def _pick(key: str, base: str, fallback: str) -> str:
        if key in ov and str(ov.get(key) or "").strip():
            return str(ov[key]).strip()
        if base:
            return base
        return fallback

    pos = _pick("GROUP_DISPLAY_POS", base_pos, outcome)
    neg = _pick("GROUP_DISPLAY_NEG", base_neg, "")
    return pos, neg


def inherited_nri_old_is_explicit(items) -> bool:
    """公共页已指定的旧模型（不是空、也不是默认 clinical）在结局「用默认」时保留。"""
    toks = [str(x).strip().lower() for x in (items or []) if str(x).strip()]
    return bool(toks) and toks not in (["clinical"], ["clin"])


def set_outcome(outcome: str, out_dir_base: str | None = None) -> None:
    """切换当前分析结局并同步输出目录（多结局项目）。

    写入 ``modules.config`` 与 ``modules.config.settings``，保证
    ``from modules import config as cfg`` 与 style 中的 ``_S.OUT_DIR`` 一致。
    """
    from modules import config as cfg
    from modules.config import settings as S

    outcome = str(outcome).strip()
    if not outcome:
        raise ValueError("outcome 不能为空")

    def _pub(name: str, val) -> None:
        setattr(cfg, name, val)
        setattr(S, name, val)

    cohort = out_dir_base if out_dir_base is not None else getattr(S, "OUT_DIR_BASE", "") or ""
    mapping = getattr(S, "SUBPROJECT_BY_OUTCOME", None) or {}
    if not isinstance(mapping, dict):
        mapping = {}
    use_subdir = bool(getattr(S, "OUTCOME_OUT_SUBDIR", True))
    new_out = map_outcome_out_dir(outcome, cohort, mapping, use_subdir)
    new_png = os.path.join(new_out, "PNG")

    base = snapshot_outcome_base(S, cfg)
    for k, v in base.items():
        _pub(k, copy.deepcopy(v))

    _pub("GROUP_SOURCE_COL", outcome)
    _pub("OUT_DIR", new_out)
    _pub("PNG_DIR", new_png)
    setattr(S, "_OUTCOME_OUT_DIR", new_out)
    setattr(cfg, "_OUTCOME_OUT_DIR", new_out)
    if "POSITIVE_GROUP_LABEL" not in outcome_override_map(S, outcome):
        _pub("POSITIVE_GROUP_LABEL", "1")

    ov = outcome_override_map(S, outcome)
    n_out = len([
        str(c).strip()
        for c in (getattr(S, "OUTCOME_COLS", None) or [])
        if str(c).strip()
    ])
    pos, neg = outcome_display_labels(
        outcome,
        base_pos=str(base.get("GROUP_DISPLAY_POS") or ""),
        base_neg=str(base.get("GROUP_DISPLAY_NEG") or ""),
        override=ov,
        n_outcomes=n_out or 1,
    )
    _pub("GROUP_DISPLAY_POS", pos)
    _pub("GROUP_DISPLAY_NEG", neg)
    # FORCE_INTER 缺键=继承公共；某结局覆盖不写进其它结局
    # 亚组列缺省空（不做分层评估）
    if "SUBGROUP_COL" not in ov:
        _pub("SUBGROUP_COL", "")

    for k, v in ov.items():
        if k not in OUTCOME_OVERRIDE_KEYS or k in ("GROUP_DISPLAY_POS", "GROUP_DISPLAY_NEG"):
            continue
        _pub(k, copy.deepcopy(v))

    skip_now = [str(x).strip() for x in (getattr(S, "SKIP_CLIN_FEATURES", None) or []) if str(x).strip()]
    seen_s: set[str] = set()
    skip_merged: list[str] = []
    for c in skip_now + _implicit_skip(S):
        if not c or c in seen_s:
            continue
        seen_s.add(c)
        skip_merged.append(c)
    _pub("SKIP_CLIN_FEATURES", skip_merged)
    skip = set(skip_merged)
    rm = [
        str(x).strip()
        for x in (getattr(S, "FORCE_REMOVE_FEATURES", None) or [])
        if str(x).strip() and str(x).strip() not in skip
    ]
    _pub("FORCE_REMOVE_FEATURES", rm)

    reader = getattr(S, "OUTCOME_READER_COMPARE", {}).get(outcome)
    _pub("READER_COMPARE_COL", reader)

    force_roc = list(getattr(S, "FORCE_CLIN_FOR_ROC", []) or [])
    if reader and reader not in force_roc:
        force_roc.append(reader)
    readers = list(getattr(S, "READER_COLS", None) or getattr(S, "_READER_COLS", []) or [])
    if bool(getattr(S, "READER_ALL_IN_ROC", False)):
        for r in readers:
            if r not in force_roc:
                force_roc.append(r)
    seen: set = set()
    force_roc = [x for x in force_roc if x and not (x in seen or seen.add(x))]
    _pub("FORCE_CLIN_FOR_ROC", force_roc)

    if "NRI_MODEL_OLD" not in ov or "NRI_MODEL_NEW" not in ov:
        old_col = str(getattr(S, "NRI_OLD_COL", "") or "").strip()
        new_col = str(getattr(S, "NRI_NEW_COL", "") or "").strip()
        if old_col and new_col:
            nri_old, nri_new = [old_col], [new_col]
        elif reader:
            nri_old, nri_new = [reader], ["combined"]
        else:
            hit = next((c for c in force_roc if c in readers), None)
            if hit:
                nri_old, nri_new = [hit], ["combined"]
            else:
                nri_old, nri_new = ["clinical"], ["combined"]
        inherited_old = list(getattr(S, "NRI_MODEL_OLD", None) or [])
        if "NRI_MODEL_OLD" not in ov and not inherited_nri_old_is_explicit(inherited_old):
            _pub("NRI_MODEL_OLD", list(nri_old))
        if "NRI_MODEL_NEW" not in ov:
            _pub("NRI_MODEL_NEW", list(nri_new))

    def _repl(obj):
        if isinstance(obj, str):
            return outcome if obj == "Group" else obj
        if isinstance(obj, tuple):
            return tuple(_repl(x) for x in obj)
        if isinstance(obj, list):
            return [_repl(x) for x in obj]
        if isinstance(obj, dict):
            return {k: _repl(v) for k, v in obj.items()}
        return obj

    _pub("DAG_NODES", _repl(list(getattr(S, "DAG_NODES", []) or [])))
    _pub("DAG_EDGES", _repl(list(getattr(S, "DAG_EDGES", []) or [])))
    _pub("DAG_MEDIATION_HIGHLIGHT", _repl(list(getattr(S, "DAG_MEDIATION_HIGHLIGHT", []) or [])))
    _pub("MEDIATION_PATHS", _repl(copy.deepcopy(getattr(S, "MEDIATION_PATHS", []) or [])))
    if "CORR_F_REF" not in ov:
        _pub("CORR_F_REF", "Group")

    # 只建结局父目录。PNG 留给真正写图的层：
    # 非两两比较 → run_pipeline.ensure_output_dirs；
    # 两两比较 → set_pairwise 建 {对子}/PNG，避免留下 {结局}/PNG 空壳。
    os.makedirs(new_out, exist_ok=True)
    msg = (
        f"  ◆ Outcome → {cfg.GROUP_SOURCE_COL}  |  OUT_DIR = {os.path.abspath(cfg.OUT_DIR)}"
        f"  |  labels: {cfg.GROUP_DISPLAY_POS} / {cfg.GROUP_DISPLAY_NEG}"
    )
    if reader:
        msg += f"  |  extra ROC/NRI/DCA: {reader}"
    else:
        msg += "  |  Reader compare: none"
    print(msg)


def _outcome_parent_dir(S, cfg, out_dir_base: str | None = None) -> str:
    """对子目录的父层：始终是当前结局文件夹，避免第二次 set_pairwise 套在上一对下面。"""
    if out_dir_base is not None:
        return str(out_dir_base)
    cached = str(getattr(S, "_OUTCOME_OUT_DIR", "") or getattr(cfg, "_OUTCOME_OUT_DIR", "") or "").strip()
    if cached:
        return cached
    outcome = str(getattr(S, "GROUP_SOURCE_COL", "") or "").strip()
    if not outcome:
        cols = [
            str(x).strip()
            for x in (getattr(S, "OUTCOME_COLS", None) or [])
            if str(x).strip()
        ]
        outcome = cols[0] if cols else ""
    if not outcome:
        return str(getattr(S, "OUT_DIR_BASE", "") or "")
    mapping = getattr(S, "SUBPROJECT_BY_OUTCOME", None) or {}
    if not isinstance(mapping, dict):
        mapping = {}
    use_subdir = bool(getattr(S, "OUTCOME_OUT_SUBDIR", True))
    cohort = str(getattr(S, "OUT_DIR_BASE", "") or "")
    return map_outcome_out_dir(outcome, cohort, mapping, use_subdir)


def _rm_empty_png(parent: str) -> None:
    """删掉 parent/PNG（仅当存在且为空）。两两比较时结局父层不应挂 PNG。"""
    if not parent:
        return
    png = os.path.join(parent, "PNG")
    try:
        if os.path.isdir(png) and not os.listdir(png):
            os.rmdir(png)
    except OSError:
        pass


def set_pairwise(pos: str, neg: str, out_dir_base: str | None = None) -> None:
    """两两比较：只保留 pos/neg 两行，阳性=pos，输出到 {结局}/{pos}_vs_{neg}。"""
    from modules import config as cfg
    from modules.config import settings as S

    pos, neg = str(pos).strip(), str(neg).strip()
    if not pos or not neg or pos == neg:
        raise ValueError(f"无效两两比较: {pos!r} vs {neg!r}")

    def _pub(name: str, val) -> None:
        setattr(cfg, name, val)
        setattr(S, name, val)

    _pub("GROUP_KEEP", [pos, neg])
    outcome = str(getattr(S, "GROUP_SOURCE_COL", "") or "").strip()
    sub = str(getattr(S, "SUBGROUP_COL", "") or "").strip()
    same_as_outcome = not sub or sub == outcome
    if same_as_outcome:
        _pub("POSITIVE_GROUP_LABEL", pos)
        _pub("GROUP_DISPLAY_POS", level_display_name(pos, S=S))
        _pub("GROUP_DISPLAY_NEG", level_display_name(neg, S=S))
    pair = pair_key(pos, neg)
    ov = pair_override_map(S, outcome, pair)
    # FORCE_INTER 缺键=保留当前结局（再缺则公共）；对子覆盖只写这一对
    for k, v in ov.items():
        if k not in PAIR_OVERRIDE_KEYS:
            continue
        _pub(k, copy.deepcopy(v))

    if same_as_outcome:
        disp_pos = str(getattr(S, "GROUP_DISPLAY_POS", "") or pos).strip() or pos
        disp_neg = str(getattr(S, "GROUP_DISPLAY_NEG", "") or neg).strip() or neg
    else:
        disp_pos = level_display_name(pos, S=S)
        disp_neg = level_display_name(neg, S=S)
    folder = pair_dir_name(disp_pos, disp_neg)
    base = _outcome_parent_dir(S, cfg, out_dir_base)
    new_out = os.path.join(base, folder) if base else folder
    new_png = os.path.join(new_out, "PNG")
    _pub("OUT_DIR", new_out)
    _pub("PNG_DIR", new_png)

    os.makedirs(new_out, exist_ok=True)
    os.makedirs(new_png, exist_ok=True)
    # 清掉结局父层空 PNG（旧版 set_outcome 或上次非对子跑留下的）
    _rm_empty_png(base)
    print(
        f"  ◆ Pairwise → {pos} vs {neg}  |  OUT_DIR = {os.path.abspath(new_out)}"
        f"  |  labels: {disp_pos} / {disp_neg}  |  GROUP_KEEP={[pos, neg]}",
        flush=True,
    )
