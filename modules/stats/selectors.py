"""模态块选择器注册表：lasso / logistic。

每块按 ``BlockSpec.method`` 派发到对应实现；未知方法 raise（不静默回退）。
块内的参数覆盖只在本块运行期生效，跑完恢复原配置。

运行: 由 modules.pipeline / modules.stats.radiomics 导入，不单独执行。
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from modules import config as cfg
from modules.config.blocks import BlockSpec

# 参数别名 → settings 键
_LASSO_KEYS: Dict[str, str] = {
    "nfolds": "LASSO_NFOLDS",
    "n_lambdas": "LASSO_N_LAMBDAS",
    "max_iter": "LASSO_MAX_ITER",
    "alpha": "ALPHA",
    "c_max": "LASSO_C_MAX",
    "c_min": "LASSO_C_MIN",
    "tol": "LASSO_TOL",
    "prefilter_topk": "LASSO_PREFILTER_TOPK",
    "n_jobs": "LASSO_N_JOBS",
}
_LOGIT_KEYS: Dict[str, str] = {
    "uni_p": "RAD_LOGIT_UNI_P",
    "mv_p": "RAD_LOGIT_MV_P",
    "firth": "DO_RAD_FIRTH",
}
_PREP_KEYS: Dict[str, str] = {
    "icc": "DO_ICC_FILTER",
    "corr": "ENABLE_CORR_FILTER",
    "corr_cutoff": "RAD_COR_CUTOFF",
}


def _param_settings(spec: BlockSpec) -> Dict[str, object]:
    """块参数/预处理覆盖 → {settings 键: 值}。未知名 raise。"""
    out: Dict[str, object] = {}
    for raw_name, value in (spec.prep or {}).items():
        key = _PREP_KEYS.get(str(raw_name).strip().lower())
        if key is None:
            raise ValueError(
                f"块 {spec.name!r}.prep 未知项 {raw_name!r}；允许: "
                + " | ".join(sorted(_PREP_KEYS))
            )
        out[key] = value
    table = _LASSO_KEYS if spec.method == "lasso" else _LOGIT_KEYS
    for raw_name, value in (spec.params.get(spec.method) or {}).items():
        key = table.get(str(raw_name).strip().lower())
        if key is None:
            raise ValueError(
                f"块 {spec.name!r}.{spec.method} 未知参数 {raw_name!r}；允许: "
                + " | ".join(sorted(table))
            )
        out[key] = value
    return out


@contextmanager
def _cfg_override(values: Dict[str, object]):
    """临时改配置键，退出时恢复（包括原本没有该属性的情况）。"""
    missing = object()
    old = {k: getattr(cfg, k, missing) for k in values}
    try:
        for k, v in values.items():
            setattr(cfg, k, v)
        yield
    finally:
        for k, v in old.items():
            if v is missing:
                try:
                    delattr(cfg, k)
                except AttributeError:
                    pass
            else:
                setattr(cfg, k, v)


def _run_lasso(
    frame: pd.DataFrame, y: np.ndarray, spec: BlockSpec,
    dataset_label: str, group: Optional[pd.Series],
):
    from modules.stats import radiomics

    return radiomics.run_radiomics(
        frame, y, dataset_label=dataset_label, group=group,
        score_name=spec.score_name, group_name=spec.name,
    )


def _run_logistic(
    frame: pd.DataFrame, y: np.ndarray, spec: BlockSpec,
    dataset_label: str, group: Optional[pd.Series],
):
    from modules.stats import radiomics

    return radiomics.run_radiomics_logistic(
        frame, y, dataset_label=dataset_label, group=group,
        split_groups=False, score_name=spec.score_name, group_name=spec.name,
    )


SELECTORS = {
    "lasso": _run_lasso,
    "logistic": _run_logistic,
}


def run_block(
    name: str, frame: pd.DataFrame, y: np.ndarray, spec: BlockSpec,
    dataset_label: str = "all", group: Optional[pd.Series] = None,
):
    """跑一块：按 method 派发，套用该块参数覆盖。"""
    method = str(spec.method or "").strip().lower()
    fn = SELECTORS.get(method)
    if fn is None:
        raise ValueError(
            f"块 {name!r} method={spec.method!r} 无效；允许: "
            + " | ".join(sorted(SELECTORS))
        )
    print(f"\n▶ 块 {name!r} · {method} · 特征 {max(frame.shape[1] - 1, 0)}", flush=True)
    with _cfg_override(_param_settings(spec)):
        return fn(frame, y, spec, dataset_label, group)


def run_blocks(
    blocks: Dict[str, pd.DataFrame], y: np.ndarray, specs: List[BlockSpec],
    dataset_label: str = "all", group: Optional[pd.Series] = None,
):
    """逐块跑选择器，返回 groups 结果（每块一个 RadScore_<块名>）。"""
    from modules.stats import radiomics

    children = []
    for spec in specs:
        frame = blocks.get(spec.name)
        if frame is None or frame.empty:
            raise ValueError(f"块 {spec.name!r} 没有数据（blocks 键: {list(blocks)}）")
        children.append(
            run_block(spec.name, frame, y, spec, dataset_label=dataset_label, group=group)
        )
    frames = []
    for child in children:
        t2 = getattr(child, "table2", None)
        if t2 is None or len(t2) == 0:
            continue
        t = t2.copy()
        t.insert(0, "Block", child.group_name or "")
        t.insert(1, "Method", getattr(child, "method", ""))
        frames.append(t)
    table2 = pd.concat(frames, ignore_index=True) if frames else None
    return radiomics.RadiomicsResult(
        features=[], coefs=np.array([]), radscore=None, intercept=None,
        betas=None, ols=None, lambda_used=float("nan"), formula_df=None,
        formula_text=None, method="blocks", table2=table2, groups=children,
        select_source="blocks",
    )
