"""
advanced_stats.py — DAG / SHAP / Mediation 编排
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from modules import config as cfg
from modules.stats import dag_plot
from modules.stats import shap_analysis
from modules.stats import mediation


def build_analysis_frame(
    clin: pd.DataFrame,
    label: np.ndarray,
    radscore: Optional[np.ndarray] = None,
    clin_score: Optional[np.ndarray] = None,
) -> pd.DataFrame:
    """train 分析用宽表：临床 + Group/结局名(label) + 可选 RadScore/ClinicalScore。"""
    df = clin.copy().reset_index(drop=True)
    y = np.asarray(label).astype(float)
    df["Group"] = y
    # 结局列名别名（DAG/mediation 用 LVSI 等，不再写死 Group）
    outcome = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    if outcome and outcome not in df.columns:
        df[outcome] = y
    if radscore is not None:
        df["RadScore"] = np.asarray(radscore, dtype=float)
    if clin_score is not None:
        # ClinicalScore 可能短于 n（dropna 后）；对齐失败则仅写有限值
        cs = np.asarray(clin_score, dtype=float)
        if len(cs) == len(df):
            df["ClinicalScore"] = cs
        else:
            # 用 clin model 完整预测更稳妥——调用方应传入长度匹配的向量
            out = np.full(len(df), np.nan)
            out[: min(len(cs), len(df))] = cs[: min(len(cs), len(df))]
            df["ClinicalScore"] = out
    return df


def run_advanced_stats(
    frame: pd.DataFrame,
    candidate_features: Optional[Sequence[str]] = None,
    selected_features: Optional[Sequence[str]] = None,
    radscore: Optional[np.ndarray] = None,
    clin_score: Optional[np.ndarray] = None,
    label: Optional[np.ndarray] = None,
    rad_feature_df: Optional[pd.DataFrame] = None,
    rad_feature_names: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    在 train frame 上运行 DAG / SHAP / Mediation。
    SHAP 仅解释 LASSO 入选的影像组学特征（rad_feature_df），不含临床变量。
    （候选池 VIF 已在临床/组学筛选中计算；不再在此重复打印。）
    """
    from modules.utils import dry_run as _dry
    print("\n" + "━" * 70)
    print("LAYER: ADVANCED STATS (DAG / SHAP / Mediation)")
    print("━" * 70)
    if _dry.exit_after_plan("advanced", [
        f"frame={frame.shape}",
        "DAG / SHAP / Mediation (skipped)",
    ]):
        return {}

    info: Dict[str, Any] = {}
    label = np.asarray(label if label is not None else frame.get("Group"), dtype=float)

    extras = {}
    if radscore is not None:
        extras["RadScore"] = np.asarray(radscore, dtype=float)
        if "RadScore" not in frame.columns:
            frame = frame.copy()
            frame["RadScore"] = extras["RadScore"]
    elif "RadScore" in frame.columns:
        extras["RadScore"] = pd.to_numeric(frame["RadScore"], errors="coerce").to_numpy()

    if clin_score is not None:
        extras["ClinicalScore"] = np.asarray(clin_score, dtype=float)
        if "ClinicalScore" not in frame.columns or frame["ClinicalScore"].isna().all():
            frame = frame.copy()
            if len(extras["ClinicalScore"]) == len(frame):
                frame["ClinicalScore"] = extras["ClinicalScore"]
    elif "ClinicalScore" in frame.columns:
        extras["ClinicalScore"] = pd.to_numeric(frame["ClinicalScore"], errors="coerce").to_numpy()

    extras["__label__"] = label

    # 本数据里实际存在的列（大小写不敏感）；派生节点始终保留
    col_lut = {str(c).lower(): str(c) for c in frame.columns}
    for extra in ("RadScore", "Group", "Combined", "ClinicalScore"):
        col_lut.setdefault(extra.lower(), extra)

    def _in_data(name: Any) -> Optional[str]:
        if name is None:
            return None
        return col_lut.get(str(name).strip().lower())

    # ── Mediation 路径：RadScore → selected clinical → 结局 ──
    outcome = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "Group").strip() or "Group"
    outcome = _in_data(outcome) or "Group"
    sel = [str(f) for f in (selected_features or []) if f]
    sel = [c for f in sel if (c := _in_data(f))]
    med_paths: List[dict] = []
    if sel:
        for i, feat in enumerate(sel, 1):
            med_paths.append({
                "id": f"M{i}",
                "x": "RadScore",
                "m": feat,
                "y": outcome,
                "label": f"RadScore → {feat} → {outcome}",
            })
        print(f"  Mediation paths (selected clinical → {outcome}): "
              f"{', '.join(sel)}")
    else:
        kept_paths: List[dict] = []
        dropped_paths: List[str] = []
        for p in list(getattr(cfg, "MEDIATION_PATHS", []) or []):
            x = _in_data(p.get("x"))
            m = _in_data(p.get("m"))
            y = _in_data(p.get("y")) or outcome
            if x and m and y:
                q = dict(p)
                q["x"], q["m"], q["y"] = x, m, y
                kept_paths.append(q)
            else:
                dropped_paths.append(str(p.get("id") or p.get("label") or p))
        med_paths = kept_paths
        if dropped_paths:
            print(f"  Mediation dropped (not in data): {', '.join(dropped_paths)}")
        if med_paths:
            print(f"  Mediation: no selected clinical; using config paths "
                  f"(n={len(med_paths)})")
        else:
            print("  ⚠ Mediation: no selected clinical features — skip paths")

    # 同步 cfg 供 DAG 使用：丢掉本数据没有的模板节点（如别的课题的 mmse/phq15）
    try:
        cfg.MEDIATION_PATHS = list(med_paths)
        raw_nodes = list(getattr(cfg, "DAG_NODES", []) or [])
        base_nodes: List[str] = []
        seen: set[str] = set()
        dropped_nodes: List[str] = []
        for n in raw_nodes:
            c = _in_data(n)
            if not c:
                dropped_nodes.append(str(n))
                continue
            if c not in seen:
                seen.add(c)
                base_nodes.append(c)
        for f in sel:
            if f not in seen:
                seen.add(f)
                base_nodes.append(f)
        if outcome not in seen:
            base_nodes.append(outcome)
        if "RadScore" in col_lut and "RadScore" not in seen:
            base_nodes.insert(0, "RadScore")
        cfg.DAG_NODES = base_nodes
        if dropped_nodes:
            print(f"  DAG dropped (not in data): {', '.join(dropped_nodes)}")
        raw_edges = list(getattr(cfg, "DAG_EDGES", []) or [])
        edges: List[tuple] = []
        edge_set: set[tuple] = set()
        for e in raw_edges:
            if not isinstance(e, (list, tuple)) or len(e) < 2:
                continue
            a, b = _in_data(e[0]), _in_data(e[1])
            if a and b and (a, b) not in edge_set:
                edges.append((a, b))
                edge_set.add((a, b))
        hi = []
        for f in sel:
            hi.append(("RadScore", f))
            hi.append((f, outcome))
        if not hi:
            raw_hi = list(getattr(cfg, "DAG_MEDIATION_HIGHLIGHT", []) or [])
            for e in raw_hi:
                if not isinstance(e, (list, tuple)) or len(e) < 2:
                    continue
                a, b = _in_data(e[0]), _in_data(e[1])
                if a and b:
                    hi.append((a, b))
        for a, b in hi:
            if (a, b) not in edge_set:
                edges.append((a, b))
                edge_set.add((a, b))
        if "RadScore" in seen and outcome in seen and ( "RadScore", outcome) not in edge_set:
            edges.append(("RadScore", outcome))
        cfg.DAG_EDGES = edges
        cfg.DAG_MEDIATION_HIGHLIGHT = hi
    except Exception as e:
        print(f"  ⚠ Mediation/DAG path sync: {e}")

    # ── DAG ──
    if getattr(cfg, "DO_DAG", False):
        try:
            info["dag"] = dag_plot.run_dag(
                mediation_paths=med_paths,
                available_cols=list(frame.columns),
            )
        except Exception as e:
            print(f"  ⚠ DAG failed: {e}")
            info["dag"] = None
    else:
        info["dag"] = None

    # ── SHAP：仅组学（LASSO 入选特征）──
    if getattr(cfg, "DO_SHAP", True):
        try:
            if rad_feature_df is not None and len(getattr(rad_feature_df, "columns", [])) > 0:
                rfeats = list(rad_feature_names) if rad_feature_names else list(rad_feature_df.columns)
                rfeats = [c for c in rfeats if c in rad_feature_df.columns]
                Xrad = rad_feature_df[rfeats].apply(pd.to_numeric, errors="coerce")
                print(f"  SHAP scope: radiomics only ({len(rfeats)} selected features)")
                info["shap"] = shap_analysis.run_shap(
                    Xrad, label, feature_names=rfeats,
                    title_suffix="(radiomics features only)",
                )
            else:
                print("  ⚠ SHAP skipped: no radiomics feature matrix provided")
                info["shap"] = None
        except Exception as e:
            print(f"  ⚠ SHAP failed: {e}")
            info["shap"] = None
    else:
        info["shap"] = None

    # ── Mediation ──
    if getattr(cfg, "DO_MEDIATION", False):
        try:
            if med_paths:
                info["mediation"] = mediation.run_mediation_paths(
                    frame, paths=med_paths, extras=extras,
                )
            else:
                info["mediation"] = None
        except Exception as e:
            print(f"  ⚠ Mediation failed: {e}")
            info["mediation"] = None
    else:
        info["mediation"] = None

    return info
