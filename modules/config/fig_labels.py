"""图内软标签注册表：默认文本 + 按项目覆盖（settings.ini ``[fig] FIG_LABELS``）。

``FIG_LABEL_DEFAULTS`` 是唯一默认源：figure_id -> {key: (中文说明, 默认文本)}。
``cfg.FIG_LABELS`` 覆盖同结构（dict-of-dict，值必须是 str）；未覆盖时输出与
旧硬编码逐字一致。控制台「图与显示」面板用 ``figure_specs()`` 生成表单。

运行: 由 modules.stats / modules.utils 的绘图函数导入；不单独执行。
"""
from __future__ import annotations

from typing import Any, Dict, List

# figure_id -> {key: (中文标签, 默认文本)}；顺序即控制台字段顺序。
FIG_LABEL_DEFAULTS: Dict[str, Dict[str, tuple]] = {
    "roc": {
        "xlabel": ("X 轴", "1 − Specificity"),
        "ylabel": ("Y 轴", "Sensitivity"),
    },
    "roc_time": {
        "xlabel": ("X 轴（数值时间）", "Time ({unit})"),
        "xlabel_cat": ("X 轴（分组时间）", "Time"),
        "ylabel": ("Y 轴", "AUC"),
    },
    "dca": {
        "xlabel": ("X 轴", "Threshold Probability"),
        "ylabel": ("Y 轴", "Net Benefit"),
        "treat_all": ("图例：全部干预", "Treat All"),
        "treat_none": ("图例：全部不干预", "Treat None"),
    },
    "calibration": {
        "xlabel": ("X 轴", "Predicted Probability"),
        "ylabel": ("Y 轴", "Observed Proportion"),
    },
    "nomogram": {
        "points": ("轴名：得分", "Points"),
        "total_points": ("轴名：总分", "Total Points"),
        "risk": ("轴名：预测概率", "Predicted\nProbability"),
    },
    "km": {
        "xlabel": ("X 轴（{unit}=时间单位）", "Time ({unit})"),
        "ylabel": ("Y 轴", "Recurrence-free probability"),
    },
    "shap": {
        "xlabel": ("X 轴", "mean |SHAP value|"),
        "cbar": ("色条", "Feature value"),
    },
    "permutation": {
        "xlabel": ("X 轴", "Permutation importance (Δ AUC)"),
    },
    "vif": {
        "xlabel": ("X 轴", "VIF"),
        "legend": ("图例（{warn}=阈值）", "VIF={warn:g}"),
    },
    "correlation": {
        "cbar": ("色条", "Spearman ρ"),
    },
    "idi": {
        "ylabel": ("Y 轴", "Δ Predicted probability"),
    },
    "nri": {
        "prev_neg": ("旧模型·阴性组扇区（{group}=分组名）", "PC {group}"),
        "prev_pos": ("旧模型·阳性组扇区（{group}=分组名）", "PC {group}"),
        "new_neg": ("新模型·阴性组扇区（{group}=分组名）", "SC {group}"),
        "new_pos": ("新模型·阳性组扇区（{group}=分组名）", "SC {group}"),
        "no_data": ("无重分类数据时的文字", "(no data)"),
    },
    "lasso": {
        "xlabel": ("X 轴", "log(λ)"),
        "coef_ylabel": ("Y 轴（系数路径）", "Coefficients"),
        "auc_ylabel": ("Y 轴（CV AUC）", "CV AUC"),
    },
    "dag": {
        "legend_clin": ("图例：临床/病理", "Clinical / pathology"),
        "legend_rad": ("图例：组学", "Radiomics"),
        "legend_outcome": ("图例：结局", "Outcome"),
        "legend_edge": ("图例：假定边", "Assumed edge"),
        "legend_med": ("图例：中介相关边", "Mediation-related edge"),
    },
    "mediation": {
        "a": ("系数符号 a", "a"),
        "b": ("系数符号 b", "b"),
        "direct": ("符号：直接效应", "c′"),
        "acme": ("符号：间接效应", "ACME"),
    },
    "qc_habitat": {
        "ylabel": ("Y 轴（单图）", "Volume fraction within nodule (%)"),
        "ylabel_split": ("Y 轴（分面图）", "Volume fraction (%)"),
    },
    # 伪组：图例里的模型显示名（ROC / DCA / 校准 / 弦图共用）。
    "legend": {
        "Clinical": ("模型名：临床", "Clinical"),
        "RadScore": ("模型名：组学", "RadScore"),
        "Combined": ("模型名：联合", "Nomogram"),
    },
}

_FIGURE_TITLES: Dict[str, str] = {
    "roc": "ROC 曲线",
    "roc_time": "时间依赖性 / Landmark ROC",
    "dca": "决策曲线（DCA）",
    "calibration": "校准曲线",
    "nomogram": "列线图",
    "km": "生存曲线（KM）",
    "shap": "SHAP 重要性",
    "permutation": "置换重要性",
    "vif": "VIF 条形图",
    "correlation": "相关热图",
    "idi": "IDI",
    "nri": "NRI（重分类弦图）",
    "lasso": "LASSO 路径",
    "dag": "DAG 图",
    "mediation": "中介效应图",
    "qc_habitat": "QC 栖息地分布",
    "legend": "模型显示名（图例）",
}


def _configured() -> Dict[str, Dict[str, str]]:
    """读 cfg.FIG_LABELS 并校验为 dict-of-dict[str,str]；空/None → {}。"""
    from modules import config as cfg

    raw = getattr(cfg, "FIG_LABELS", None)
    if raw in (None, ""):
        return {}
    return normalize_fig_labels(raw)


def fig_label(figure: str, key: str, **fmt: Any) -> str:
    """图内标签：cfg.FIG_LABELS 覆盖值优先，否则用注册默认文本。

    默认文本可含 ``{unit}`` / ``{warn:g}`` 等占位，按 **fmt 格式化；未知
    figure/key 或非字符串覆盖值直接 raise（不静默回退）。
    """
    fig = str(figure or "").strip()
    key_s = str(key or "").strip()
    spec = FIG_LABEL_DEFAULTS.get(fig)
    if spec is None or key_s not in spec:
        raise ValueError(f"FIG_LABELS 未知图形/键: {fig}.{key_s}")
    default = str(spec[key_s][1])
    override = _configured().get(fig, {}).get(key_s)
    text = default if override is None else str(override)
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(
            f"图内标签格式化失败: {fig}.{key_s}={text!r} ({exc})"
        ) from exc


def legend_label(name: str) -> str:
    """图例里的模型显示名：legend 覆盖（按原名或展示名）→ 注册默认 → 分数展示名。"""
    from modules.stats.radiomics import score_display_name

    raw_name = str(name)
    disp = score_display_name(raw_name)
    configured = _configured().get("legend", {})
    defaults = {k: v[1] for k, v in FIG_LABEL_DEFAULTS["legend"].items()}
    for cand in (raw_name, disp):
        val = configured.get(cand)
        if val is None:
            val = defaults.get(cand)
        if val is not None:
            return str(val)
    return disp


def normalize_fig_labels(raw: Any) -> Dict[str, Dict[str, str]]:
    """把 FIG_LABELS 收成 dict-of-dict[str,str]。

    非 dict / 非字符串值 raise；未知 figure/key 原样保留（向前兼容，控制台
    只编辑注册表里的字段）。
    """
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"FIG_LABELS 必须是对象（dict），收到 {type(raw).__name__}")
    out: Dict[str, Dict[str, str]] = {}
    for fig, group in raw.items():
        fig_key = str(fig).strip()
        if not fig_key:
            raise ValueError("FIG_LABELS 含空图形名")
        if not isinstance(group, dict):
            raise ValueError(f"FIG_LABELS[{fig_key!r}] 必须是对象（dict）")
        inner: Dict[str, str] = {}
        for key, val in group.items():
            key_s = str(key).strip()
            if not key_s:
                raise ValueError(f"FIG_LABELS[{fig_key!r}] 含空键名")
            if not isinstance(val, str):
                raise ValueError(
                    f"FIG_LABELS[{fig_key!r}][{key_s!r}] 必须是字符串，"
                    f"收到 {type(val).__name__}"
                )
            inner[key_s] = val
        out[fig_key] = inner
    _migrate_nri_labels(out)
    return out


def _migrate_nri_labels(out: Dict[str, Dict[str, str]]) -> None:
    """旧版 NRI 两字段（prev_label/new_label）→ 四扇区字段。

    旧键会把重分类前/后的两个分组写成同名，四扇区各自独立后才能分别编辑；
    迁移保留用户原文，四个新键缺省时同时套用旧值。
    """
    nri = out.get("nri")
    if not nri:
        return
    for legacy, targets in (
        ("prev_label", ("prev_neg", "prev_pos")),
        ("new_label", ("new_neg", "new_pos")),
    ):
        if legacy not in nri:
            continue
        text = nri.pop(legacy)
        for target in targets:
            nri.setdefault(target, text)


def figure_specs() -> List[dict]:
    """控制台「图与显示」表单规格（JSON 可序列化）。

    返回 ``[{"id": figure_id, "title": 中文标题, "fields": [
    {"key", "label", "default"}, ...]}, ...]``，含 "legend" 伪组。
    """
    return [
        {
            "id": fig,
            "title": _FIGURE_TITLES[fig],
            "fields": [
                {"key": key, "label": spec[key][0], "default": spec[key][1]}
                for key in spec
            ],
        }
        for fig, spec in FIG_LABEL_DEFAULTS.items()
    ]
