"""
modules.pipeline — 主管线
═══════════════════════════════════════════════════════════════════
三层架构（All → Train → Test）：
    1. ALL   — 全队列仅 Baseline Table 1
    2. TRAIN — 组学（LASSO 或 Logistic）+ clin_select（训练集拟合）
    3. TEST  — 复用 TRAIN 特征；内部 / 亚组 / 外部验证各自决定套公式、锁定阈值或重拟合。
              组学 Logistic 在「重新拟合」时锁定入选特征和训练集标准化，只重估系数。
    4. COMBINED — Nomogram / ROC / Cal / DCA / NRI
    5. Advanced + Results{子项目}.html

运行（在项目根目录）：
    python -m modules.pipeline
    python -m modules.pipeline --input pipeline_data.xlsx
    # 日志默认 UTF-8 写入 pipeline_run.log（勿再用 PowerShell Tee-Object，易中文乱码）
    python -m modules.pipeline --log ref/pipeline_run.log
    python -m modules.pipeline --no-log

配置：工程根目录 settings.ini
算法：<repo root>/modules
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import argparse
import io
import os
import sys
from typing import Dict, List, Optional, TextIO

import numpy as np
import pandas as pd
import statsmodels.api as sm

from modules import config as cfg


class _TeeTextIO(io.TextIOBase):
    """同时写入控制台与日志文件（UTF-8），避免 PowerShell Tee-Object 中文乱码。"""

    def __init__(self, *streams: TextIO):
        self._streams = streams

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        n = 0
        for st in self._streams:
            try:
                n = st.write(s)
                st.flush()
            except Exception as e:
                # best-effort tee: one stream may be closed; keep the other
                sys.__stderr__.write(f"  ⚠ tee write: {e}\n")
        return n if n else len(s)

    def flush(self) -> None:  # type: ignore[override]
        for st in self._streams:
            try:
                st.flush()
            except Exception as e:
                sys.__stderr__.write(f"  ⚠ tee flush: {e}\n")

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return "utf-8"

    def isatty(self) -> bool:  # type: ignore[override]
        try:
            return bool(self._streams[0].isatty())
        except Exception:
            return False


def _configure_stdio_utf8() -> None:
    """Windows 控制台尽量用 UTF-8，减少打印中文时的替换符。"""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception as e:
            sys.__stderr__.write(f"  ⚠ stdio utf8 reconfigure: {e}\n")
    # 子进程 / 部分库读取该环境变量
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _attach_run_log(log_path: str) -> TextIO:
    """
    将 stdout/stderr 双写到 UTF-8-SIG 日志文件（带 BOM，Windows 记事本可正确打开）。
    返回已打开的文件对象，调用方负责 close。
    """
    _configure_stdio_utf8()
    log_path = os.path.abspath(log_path)
    parent = os.path.dirname(log_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    log_f = open(log_path, "w", encoding="utf-8-sig", newline="\n", buffering=1)
    sys.stdout = _TeeTextIO(sys.__stdout__, log_f)  # type: ignore[assignment]
    sys.stderr = _TeeTextIO(sys.__stderr__, log_f)  # type: ignore[assignment]
    print(f"  ◆ Run log (UTF-8): {log_path}", flush=True)
    return log_f
from modules.stats import data_io
from modules.stats import clinical as clin_select
from modules.stats import radiomics
from modules.stats.curves_roc import plot_roc
from modules.stats.correlation import (
    check_correlation,
    clear_selected_heatmap,
    expand_corr_predictors,
    expand_heatmap_columns,
    group_row_mask,
    heatmap_wanted,
    run_correlation,
    select_heatmap_frames,
    take_group_rows,
    take_group_values,
)
from modules.stats.report import generate_results_html
from modules.stats.models import build_nomogram
from modules.stats.curves_calibration import plot_calibration
from modules.stats.curves_dca import plot_dca
from modules.stats.nri_cir import nri, cir_plot, nri_from_probs, idi_from_probs, plot_idi
from modules.stats.curves_survival import plot_survival_bundle
from modules.stats import advanced as advanced_stats
from modules.stats.false_class import (
    compute_false_class,
    print_false_classification,
    save_false_classification_xlsx,
)
from modules.stats.val_mode import (
    resolve_val_mode,
    val_mode_label,
    refit_clinical_prob,
    refit_radscore_ols,
    refit_combined_prob,
)


# ═════════════════════════════════════════════════════════════════
# Helper: 构建多模型预测值字典
# ═════════════════════════════════════════════════════════════════
def _token(s: str) -> str:
    return str(s).strip().lower().replace(" ", "").replace("_", "")


class RunStatus:
    """Fail-closed ledger: requested analyses that raise are marked, not silent."""

    def __init__(self) -> None:
        self.failures: List[Dict[str, object]] = []

    def warn(self, name: str, exc: object, *, critical: bool = False) -> None:
        msg = str(exc)
        self.failures.append({"name": name, "error": msg, "critical": bool(critical)})
        tag = "FAILED" if critical else "INCOMPLETE"
        print(f"  ⚠ [{tag}] {name}: {msg}")

    @property
    def status(self) -> str:
        if not self.failures:
            return "OK"
        if any(bool(x.get("critical")) for x in self.failures):
            return "FAILED"
        return "INCOMPLETE"

    def as_dict(self) -> Dict[str, object]:
        return {"status": self.status, "failures": list(self.failures)}


_NRI_MODEL_TOKENS = {
    "clinical", "clin", "combined", "comb", "combine", "clinrad", "radscore", "rad",
}


def _nri_spec_items(spec) -> List[str]:
    if spec is None:
        return []
    if isinstance(spec, str):
        text = str(spec).strip()
        return [text] if text else []
    return [str(x).strip() for x in spec if str(x).strip()]


def _nri_slug(label: str) -> str:
    chars = []
    for ch in str(label).strip():
        chars.append(ch if (ch.isalnum() or ch in "._-") else "_")
    slug = "".join(chars).strip("._")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "old"


def _is_nri_reader_name(name: str, readers: List[str]) -> bool:
    if name in set(readers):
        return True
    return str(name).strip().lower().startswith("reader")


def nri_old_arms(spec, readers: Optional[List[str]] = None) -> List[List[str]]:
    """一个旧模型返回一项。多个 Reader / 模型名各自成一项，上下各出一份 NRI。

    普通临床列（如体积 + 病灶数）仍合成一个旧模型。
    """
    items = _nri_spec_items(spec)
    if len(items) <= 1:
        return [items] if items else [[]]
    pool = list(getattr(cfg, "READER_COLS", None) or []) if readers is None else list(readers)
    if all(_token(x) in _NRI_MODEL_TOKENS or _is_nri_reader_name(x, pool) for x in items):
        return [[x] for x in items]
    return [items]


def _spec_to_prob(spec, p_clin, p_rad, p_comb):
    """Map NRI_MODEL_* tokens to locked probability vectors. None → caller refits."""
    if spec is None:
        return None
    items = [spec] if isinstance(spec, str) else list(spec)
    if len(items) != 1:
        return None
    t = _token(items[0])
    if t in ("clinical", "clin"):
        return p_clin
    if t in ("combined", "comb"):
        return p_comb
    if t in ("radscore", "rad"):
        return p_rad
    return None


def group_label_map(dat: data_io.DataLayer) -> Dict[int, str]:
    """从 DataLayer 提取 label 0/1 → 展示名。优先用组列上已经写好的展示名。"""
    y = np.asarray(dat.label).astype(int)
    g = dat.group
    pos = str(getattr(cfg, "GROUP_DISPLAY_POS", "") or "").strip()
    neg = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    out: Dict[int, str] = {}
    for val in (0, 1):
        idx = np.where(y == val)[0]
        if len(idx) > 0 and g is not None:
            out[val] = str(g.iloc[int(idx[0])])
        elif val == 1 and pos:
            out[val] = str(pos)
        elif val == 0 and neg:
            out[val] = str(neg)
        else:
            out[val] = "Negative" if val == 0 else "Positive"
    return out


def resolve_model_frame(
    spec,
    clin_df: pd.DataFrame,
    clin_features: List[str],
    radscore: Optional[np.ndarray],
    side_name: str = "model",
) -> pd.DataFrame:
    """
    将 NRI/IDI 配置解析为预测矩阵。
    spec 可为 str 或 list；令牌 clinical / radscore / combined，或 clin 列名。
    """
    if spec is None:
        items: List[str] = []
    elif isinstance(spec, str):
        items = [spec]
    else:
        items = list(spec)

    parts: List[pd.Series] = []
    used = set()
    n = len(clin_df)

    def _add_col(name: str, arr):
        if name in used:
            return
        a = np.asarray(arr, dtype=float).ravel()
        if len(a) != n:
            raise ValueError(f"{side_name}: '{name}' length {len(a)} != n={n}")
        parts.append(pd.Series(a, name=name))
        used.add(name)

    for item in items:
        t = _token(item)
        if t in ("clinical", "clin"):
            for f in clin_features:
                if f in clin_df.columns:
                    _add_col(f, pd.to_numeric(clin_df[f], errors="coerce"))
        elif t in ("radscore", "rad"):
            if radscore is None:
                raise ValueError(f"{side_name}: RadScore unavailable")
            _add_col("RadScore", radscore)
        elif t in ("combined", "combine", "clinrad"):
            for f in clin_features:
                if f in clin_df.columns:
                    _add_col(f, pd.to_numeric(clin_df[f], errors="coerce"))
            if radscore is None:
                raise ValueError(f"{side_name}: RadScore unavailable for combined")
            _add_col("RadScore", radscore)
        else:
            # clin 列名（大小写不敏感）
            cmap = { _token(c): c for c in clin_df.columns }
            if t in cmap:
                c = cmap[t]
                _add_col(c, pd.to_numeric(clin_df[c], errors="coerce"))
            elif item in clin_df.columns:
                _add_col(item, pd.to_numeric(clin_df[item], errors="coerce"))
            else:
                raise ValueError(f"{side_name}: unknown feature/token {item!r}")

    if not parts:
        raise ValueError(f"{side_name}: empty feature set from spec={spec!r}")
    return pd.concat(parts, axis=1)


def _vectors_for_roc_names(layer, names) -> Dict[str, np.ndarray]:
    """按名单取数值列。临床表优先，没有则用组学表。长度须与该层 label 一致。"""
    clin = getattr(layer, "clin", None)
    rad = getattr(layer, "rad", None)
    label = getattr(layer, "label", None)
    n = None if label is None else len(np.asarray(label))
    out: Dict[str, np.ndarray] = {}
    for raw in list(names or []):
        name = str(raw).strip()
        if not name or name in out:
            continue
        src = None
        if clin is not None and name in clin.columns:
            src = clin[name]
        elif rad is not None and name in rad.columns:
            src = rad[name]
        if src is None:
            continue
        vec = pd.to_numeric(src, errors="coerce").to_numpy()
        if n is not None and len(vec) != n:
            continue
        out[name] = vec
    return out


def build_models(clin_df: pd.DataFrame, radscore_vec: Optional[np.ndarray],
                  clin_features: Optional[List[str]] = None) -> Dict[str, np.ndarray]:
    models: Dict[str, np.ndarray] = {}
    if radscore_vec is not None:
        models["RadScore"] = radscore_vec
    force = list(getattr(cfg, "FORCE_CLIN_FOR_ROC", []))
    candidates = list(clin_features or []) + force
    seen = set()
    for col in candidates:
        if col in clin_df.columns and col not in seen:
            models[col] = pd.to_numeric(clin_df[col], errors="coerce").to_numpy()
            seen.add(col)
    return models


def empty_radiomics_result() -> radiomics.RadiomicsResult:
    return radiomics.RadiomicsResult(
        features=[], coefs=np.array([]), radscore=None, intercept=None, betas=None,
        ols=None, lambda_used=np.nan, formula_df=None, formula_text=None,
    )


# ═════════════════════════════════════════════════════════════════
# Helper: 单数据层完整分析
# ═════════════════════════════════════════════════════════════════
def run_analysis(dat: data_io.DataLayer, label_str: str,
                  rad_override: Optional[radiomics.RadiomicsResult] = None,
                  fixed_clin_features: Optional[List[str]] = None,
                  skip_roc: bool = False,
                  roc_clin_features: Optional[List[str]] = None) -> Dict:
    """
    单层分析。skip_roc=True 时不画 ROC（由 Combined 层合并绘制）。
    roc_clin_features：若给定，仅用该列表（而非 clin_res.features）构建单临床 ROC。
    """
    clin_res = clin_select.select_clinical(
        dat.clin, dat.label, dat.group,
        force_inter_features=cfg.FORCE_INTER_FEATURES,
        force_remove_features=cfg.FORCE_REMOVE_FEATURES,
        force_model_features=getattr(cfg, "FORCE_MODEL_FEATURES", None),
        p_threshold=cfg.TABLE1_P_THRESHOLD,
        dataset_label=label_str,
        fixed_features=fixed_clin_features,
        table1_only=fixed_clin_features is None and not bool(getattr(cfg, "DO_CLIN", True)),
    )

    if rad_override is None:
        skip_lasso = not bool(getattr(cfg, "DO_LASSO", True))
        do_rad_log = bool(getattr(cfg, "DO_RAD_LOGISTIC", False))
        no_rad = dat.rad is None or dat.rad.empty or dat.rad.shape[1] == 0
        if no_rad:
            rad_res = empty_radiomics_result()
        elif do_rad_log:
            if not skip_lasso:
                print("  DO_RAD_LOGISTIC=True — skip LASSO.")
            rad_res = radiomics.run_radiomics_logistic(
                dat.rad.drop(columns=["Name"], errors="ignore"),
                dat.label, dataset_label=label_str, group=dat.group,
            )
        elif skip_lasso:
            print("  DO_LASSO=False — skip LASSO.")
            rad_res = empty_radiomics_result()
        else:
            rad_frame = dat.rad.drop(columns=["Name"], errors="ignore")
            if radiomics.prefix_group_plan(list(rad_frame.columns)):
                print("  Prefix categories apply to logistic — this LASSO run stays one model.")
            rad_res = radiomics.run_radiomics(
                rad_frame, dat.label, dataset_label=label_str, group=dat.group,
            )
    else:
        rad_res = rad_override

    if not skip_roc:
        clin_for_roc = (list(roc_clin_features) if roc_clin_features is not None
                        else list(clin_res.features))
        payload_scores = radiomics.score_map(rad_res)
        grouped = bool(list(getattr(rad_res, "groups", None) or []))
        if grouped or (payload_scores and set(payload_scores) != {"RadScore"}):
            ml = build_models(dat.clin, None, clin_features=clin_for_roc)
            for _sname, _svec in payload_scores.items():
                ml[str(_sname)] = _svec
        else:
            ml = build_models(dat.clin, rad_res.radscore, clin_features=clin_for_roc)
        if len(ml) > 0:
            plot_roc(ml, dat.label, dataset_label=label_str)

    return {"clin": clin_res, "rad": rad_res}


def _maybe_td_rocs(dataset_label: str, model_list, layer, *, ipcw_required: bool = True) -> None:
    _maybe_landmark_roc(dataset_label, model_list, layer)
    _maybe_ipcw_roc(dataset_label, model_list, layer, required=ipcw_required)


def _maybe_ipcw_roc(dataset_label: str, model_list, layer, *, required: bool = True) -> None:
    if not cfg.ipcw_roc_on():
        return
    if not model_list or layer is None:
        return
    ipcw = getattr(layer, "ipcw", None)
    if not ipcw:
        msg = (
            f"时间依赖性 ROC（IPCW）[{dataset_label}]: 这一层没有 IPCW 时间/状态。"
            "检查 TD_IPCW_TIME_COL / TD_IPCW_STATUS_COL。"
        )
        if required:
            raise ValueError(msg)
        print(f"  ⚠ {msg} 外验跳过。")
        return
    from modules.stats.curves_ipcw import plot_ipcw_roc
    plot_ipcw_roc(model_list, ipcw, dataset_label=dataset_label)


def _maybe_landmark_roc(dataset_label: str, model_list, layer) -> None:
    """同一套分数在各时间点标签上的 Landmark ROC。关着或没有状态列则跳过。"""
    if not cfg.landmark_roc_on():
        return
    if not model_list or layer is None:
        return
    landmarks = getattr(layer, "landmark", None) or {}
    if not landmarks:
        print(f"  ⚠ Landmark ROC [{dataset_label}]: no landmark labels on this layer")
        return
    from modules.stats.curves_roc import plot_landmark_roc
    plot_landmark_roc(model_list, landmarks, dataset_label=dataset_label)


def run_pipeline(input_file: Optional[str] = None) -> None:
    """执行完整管线：All / Train / Test / Combined / Correlation / HTML 报告。"""
    input_file = input_file or cfg.INPUT
    cfg.ensure_output_dirs()
    run_status = RunStatus()

    data = data_io.load_data(file=input_file, ratio=cfg.TRAIN_RATIO, seed=cfg.SEED)

    print("\n" + "━" * 70)
    print("LAYER: ALL DATA — 总表 only")
    print("━" * 70)
    base_clin = getattr(data, "baseline_clin", None)
    base_grp = getattr(data, "baseline_group", None)
    if base_clin is not None and base_grp is not None and len(base_clin) and len(base_grp):
        # 选两组时建模队列已筛成阳/阴；总表仍用筛前全水平。
        # ≥3 个原始水平时一律 ANOVA + Fisher LSD，不看选两组 / 全部组 / 全部两两。
        from modules.stats.clinical import _build_table1_by_groups
        n_levels = len({str(x).strip() for x in base_grp if str(x).strip() and str(x).strip().lower() != "nan"})
        tbl_all = _build_table1_by_groups(base_clin, base_grp)
        print(
            f"  总表: {len(tbl_all)} rows  levels={sorted(set(map(str, base_grp)))}  "
            f"pairwise={'ANOVA+LSD' if n_levels >= 3 else 'off'}"
        )
        clin_all = clin_select.ClinSelectResult(
            features=[],
            uni_res=pd.DataFrame(),
            table1=tbl_all,
            clinscore=None,
            model=None,
        )
    else:
        clin_all = clin_select.select_clinical(
            data.all.clin, data.all.label, data.all.group,
            force_inter_features=cfg.FORCE_INTER_FEATURES,
            force_remove_features=cfg.FORCE_REMOVE_FEATURES,
            p_threshold=cfg.TABLE1_P_THRESHOLD,
            dataset_label="all",
            table1_only=True,
        )
    res_all = {"clin": clin_all, "rad": empty_radiomics_result()}

    val_mode = resolve_val_mode()
    subgroup_results: list = []
    print(f"  VAL_MODE={val_mode}  ({val_mode_label(val_mode)})")

    print("\n" + "━" * 70)
    print("LAYER: TRAIN")
    print("━" * 70)
    # ROC 统一延后绘制（mv p 过滤 + 可选合并 Clinical/Combined）
    res_train = run_analysis(data.train, "train", skip_roc=True)
    # Selected clinical = 手动 FORCE_MODEL，否则多因素 p 默认
    train_clin_feat = list(res_train["clin"].features)
    p_cut = getattr(cfg, "ROC_MV_P_THRESHOLD", 0.05)
    roc_clin_feats = clin_select.features_for_roc(res_train["clin"], p_cut=p_cut)
    if not roc_clin_feats and train_clin_feat:
        roc_clin_feats = list(train_clin_feat)
    src = getattr(res_train["clin"], "select_source", None) or ""
    print(f"  Train selected clinical ({src or 'none'}, for ROC/Combined/Test): {train_clin_feat}")

    def _score_payload(rad_res):
        """ROC / 列线图：只有一份 RadScore 时仍传数组；分类后传 {分数名: 数组}。"""
        smap = radiomics.score_map(rad_res) if rad_res is not None else {}
        if not smap:
            return None
        grouped = bool(list(getattr(rad_res, "groups", None) or []))
        if not grouped and set(smap) == {"RadScore"}:
            return smap["RadScore"]
        return smap

    def _apply_train_radscore(layer: data_io.DataLayer) -> radiomics.RadiomicsResult:
        """用 TRAIN 公式在验证层 / 全队列计算 RadScore（不重新筛选）。"""
        rad = res_train["rad"]
        if list(getattr(rad, "groups", None) or []):
            return radiomics.apply_locked_result(rad, layer.rad)
        if len(rad.features) == 0 or rad.betas is None or rad.intercept is None:
            return empty_radiomics_result()
        rs = radiomics.apply_locked_radscore(rad, layer.rad)
        return radiomics.RadiomicsResult(
            features=list(rad.features), coefs=rad.coefs, radscore=rs,
            intercept=rad.intercept, betas=rad.betas, ols=None,
            lambda_used=rad.lambda_used, formula_df=rad.formula_df,
            formula_text=rad.formula_text,
            method=str(getattr(rad, "method", "") or "lasso"),
            preprocess=getattr(rad, "preprocess", None),
        )

    def _validation_radscore(layer: data_io.DataLayer, applied):
        """重新拟合：LASSO 重估原始尺度 OLS；Logistic 锁定特征和训练集标准化，只重估系数。"""
        rad = res_train["rad"]
        if list(getattr(rad, "groups", None) or []):
            refit = radiomics.refit_locked_result(rad, layer.rad, layer.label)
            return radiomics.score_map(refit) or None
        rs = None if applied is None else applied.radscore
        feats = list(rad.features or [])
        if not feats:
            return rs
        if str(getattr(rad, "method", "") or "") == "logistic":
            rs_new = radiomics.refit_radscore_logistic(rad, layer.rad, layer.label)
            if rs_new is None:
                print("  ⚠ RadScore logistic refit failed — keep train formula")
                return rs
            print(f"  Refit RadScore logistic ({len(feats)} locked features, train scaling)")
            return rs_new
        rs_new = refit_radscore_ols(layer.rad, layer.label, feats)
        if rs_new is None:
            print("  ⚠ RadScore refit failed — keep train formula")
            return rs
        print(f"  Refit RadScore OLS ({len(feats)} locked features)")
        return rs_new

    # 全队列 RadScore = train 公式应用（供 IDI / correlation / combined score）
    rad_all_applied = _apply_train_radscore(data.all)
    res_all["rad"] = rad_all_applied
    if data.all.rad is not None and not data.all.rad.empty:
        try:
            rad_tr = res_train["rad"]
            method = str(getattr(rad_tr, "method", "") or "lasso")
            if list(getattr(rad_tr, "groups", None) or []):
                res_all["rad"].table2 = radiomics.grouped_table2(
                    rad_tr,
                    data.all.rad.drop(columns=["Name"], errors="ignore"),
                    data.all.label, group=data.all.group,
                )
                t2_all = res_all["rad"].table2
                print(f"  All Table2 (rad, category): {0 if t2_all is None else len(t2_all)} rows")
                keep = []
            elif method == "logistic":
                keep = list((getattr(rad_tr, "preprocess", None) or {}).get("keep_cols") or [])
                pre = getattr(rad_tr, "preprocess", None)
            else:
                # LASSO: Table 2 on selected features (locked from train)
                keep = list(rad_tr.features or [])
                pre = None
            if keep:
                t2_all = radiomics.build_rad_table2(
                    data.all.rad.drop(columns=["Name"], errors="ignore"),
                    data.all.label, group=data.all.group,
                    keep_cols=keep,
                    preprocess=pre,
                )
                res_all["rad"].table2 = t2_all
                tag = "LASSO-selected" if method != "logistic" else "pool"
                print(f"  All Table2 (rad, {tag}): {0 if t2_all is None else len(t2_all)} rows")
        except Exception as e:
            print(f"  ⚠ All Table2 (rad) failed: {e}")

    # ── 内部 hold-out test（可能为空：当 EXTERNAL_TESTS 接管时）──
    res_test = None
    if data.test is not None and len(getattr(data.test, "label", [])) > 0:
        print("\n" + "━" * 70)
        print(f"LAYER: TEST (internal) — features locked to TRAIN  [{val_mode}]")
        print("━" * 70)
        rad_test_formula = _apply_train_radscore(data.test)
        if radiomics.score_map(rad_test_formula):
            nfeat = len(radiomics.selected_feature_names(rad_test_formula))
            print(f"  Test RadScore from {nfeat} train features")
        res_test = run_analysis(
            data.test, "test",
            rad_override=rad_test_formula,
            fixed_clin_features=train_clin_feat,
            skip_roc=True,
        )
        res_test["rad"] = rad_test_formula
        if data.test is not None and data.test.rad is not None and not data.test.rad.empty:
            try:
                rad_tr = res_train["rad"]
                method = str(getattr(rad_tr, "method", "") or "lasso")
                if list(getattr(rad_tr, "groups", None) or []):
                    t2_te = radiomics.grouped_table2(
                        rad_tr,
                        data.test.rad.drop(columns=["Name"], errors="ignore"),
                        data.test.label, group=data.test.group,
                    )
                    rad_test_formula.table2 = t2_te
                    res_test["rad"].table2 = t2_te
                    print(f"  Test Table2 (rad, category): {0 if t2_te is None else len(t2_te)} rows")
                    keep = []
                elif method == "logistic":
                    keep = list((getattr(rad_tr, "preprocess", None) or {}).get("keep_cols") or [])
                    pre = getattr(rad_tr, "preprocess", None)
                else:
                    keep = list(rad_tr.features or [])
                    pre = None
                if keep:
                    t2_te = radiomics.build_rad_table2(
                        data.test.rad.drop(columns=["Name"], errors="ignore"),
                        data.test.label, group=data.test.group,
                        keep_cols=keep,
                        preprocess=pre,
                    )
                    rad_test_formula.table2 = t2_te
                    res_test["rad"].table2 = t2_te
                    tag = "LASSO-selected" if method != "logistic" else "pool"
                    print(f"  Test Table2 (rad, {tag}): {0 if t2_te is None else len(t2_te)} rows")
            except Exception as e:
                print(f"  ⚠ Test Table2 (rad) failed: {e}")
    else:
        print("\n  (no internal test split)")

    # ── 外部验证层（只加载；评估走锁死主模型，同亚组）──
    external_layers = []
    input_file = input_file or cfg.INPUT
    for spec in _resolve_external_specs(input_file):
        name = str(spec.get("name", "ext"))
        cs = spec.get("clin_sheet")
        rs = spec.get("rad_sheet")
        ext_file = spec.get("file") or input_file
        if not cs or not rs:
            print(f"  ⚠ EXTERNAL_TESTS[{name}] missing clin_sheet/rad_sheet — skip")
            continue
        try:
            print(f"  load external [{name}]  file={ext_file}  clin={cs}  rad={rs}")
            ext_layer = data_io.load_external_layer(
                ext_file, clin_sheet=cs, rad_sheet=rs, name=name,
                rad_id_col=str(spec.get("rad_id_col") or "").strip(),
            )
            external_layers.append({"name": name, "data": ext_layer})
        except Exception as e:
            print(f"  ⚠ External [{name}] load failed: {e}")
    external_results: list = []

    def _merged_roc_models(layer: data_io.DataLayer, radscore, p_clin=None, p_comb=None):
        """单临床(mv p 过滤) + RadScore + 可选 Clinical/Combined → 一张 ROC。"""
        if isinstance(radscore, dict):
            ml = build_models(layer.clin, None, clin_features=roc_clin_feats)
            for name, vec in radscore.items():
                if vec is None:
                    continue
                ml[str(name)] = np.asarray(vec, dtype=float)
        else:
            ml = build_models(layer.clin, radscore, clin_features=roc_clin_feats)
        if p_clin is not None:
            ml["Clinical"] = np.asarray(p_clin, dtype=float)
        if p_comb is not None:
            ml["Combined"] = np.asarray(p_comb, dtype=float)
        for name, vec in _vectors_for_roc_names(
            layer, getattr(cfg, "FORCE_CLIN_FOR_ROC", []) or [],
        ).items():
            ml.setdefault(name, vec)
        return ml

    # ── false_classification：仅控制台打印，不写 xlsx/其他表 ──
    if getattr(cfg, "DO_FALSE_CLASS", True):
        try:
            fc_details: Dict = {}
            fc_predictors: Dict[str, np.ndarray] = {}
            for _sname, _svec in radiomics.score_map(res_all["rad"]).items():
                fc_predictors[str(_sname)] = np.asarray(_svec, dtype=float)
            roc_names = list(train_clin_feat) + list(
                getattr(cfg, "FORCE_CLIN_FOR_ROC", []) or []
            )
            for nm, vec in _vectors_for_roc_names(data.all, roc_names).items():
                if nm not in fc_predictors:
                    fc_predictors[nm] = vec
            for nm, val in fc_predictors.items():
                keep = np.isfinite(val)
                if keep.sum() < 2 or len(np.unique(data.all.label[keep])) < 2:
                    continue
                model_name = str(nm).replace(" ", "")
                sample_labels = (
                    [data.all.sample_labels[i] for i in np.where(keep)[0]]
                    if data.all.sample_labels is not None else None
                )
                detail, _summary = compute_false_class(
                    val[keep], data.all.label[keep], labels=sample_labels,
                    dataset_label="all", model_name=model_name,
                )
                fc_details[model_name] = detail
            train_ids = set(str(x) for x in (data.train.sample_labels or []))
            test_ids = (
                set(str(x) for x in (data.test.sample_labels or []))
                if data.test else set()
            )
            split_by_id = {}
            for sid in (data.all.sample_labels or []):
                s = str(sid)
                if s in train_ids:
                    split_by_id[s] = "train"
                elif s in test_ids:
                    split_by_id[s] = "test"
                else:
                    split_by_id[s] = ""
            # 控制台打印 + 默认写入 xlsx（单 sheet，无其他表）
            gmap = group_label_map(data.all)
            print_false_classification(
                fc_details, split_by_id=split_by_id, group_labels=gmap,
            )
            import os
            out = str(getattr(cfg, "OUT_DIR", "") or "").strip()
            fc_path = os.path.join(out, "false_classification.xlsx") if out else "false_classification.xlsx"
            save_false_classification_xlsx(
                fc_details, fc_path, split_by_id=split_by_id, group_labels=gmap,
            )
        except Exception as e:
            print(f"  ⚠ false_classification failed: {e}")

    combined_info = None
    _tr_scores = radiomics.score_map(res_train["rad"])
    has_rs = bool(_tr_scores)
    do_nomo = bool(getattr(cfg, "DO_NOMOGRAM", True))
    if do_nomo or has_rs or train_clin_feat:
        print("\n" + "━" * 70)
        if has_rs and list(_tr_scores) == ["RadScore"]:
            print("LAYER: COMBINED MODEL (Clin + RadScore)")
        elif has_rs:
            print("LAYER: COMBINED MODEL (Clin + " + ", ".join(_tr_scores) + ")")
        else:
            print("LAYER: CLINICAL MODEL / NOMOGRAM (no radiomics)")
        print("━" * 70)

        # Combined / nomogram = FORCE_MODEL_FEATURES（空则用 Selected = 多因素 p 默认）+ RadScore。
        # FORCE_CLIN_FOR_ROC 只额外进 ROC/NRI/DCA，不得把已入选/强制入模的列从列线图里剔除。
        nomo_exclude = set(
            getattr(cfg, "READER_COLS", None)
            or getattr(cfg, "_READER_COLS", None)
            or []
        )
        reader_col = getattr(cfg, "READER_COMPARE_COL", None)
        if reader_col:
            nomo_exclude.add(reader_col)

        clin_feat_combined = []
        seen = set()
        force_nomo_raw = [c for c in list(getattr(cfg, "FORCE_MODEL_FEATURES", []) or []) if c]
        _rs_names = {"radscore", "rad_score"}
        force_nomo_has_rs = any(str(c).strip().lower() in _rs_names for c in force_nomo_raw)
        force_nomo = [
            c for c in force_nomo_raw
            if str(c).strip().lower() not in _rs_names
        ]
        src_feat = force_nomo or train_clin_feat
        for f in src_feat:
            if f in nomo_exclude:
                continue
            if f in data.train.clin.columns and f not in seen:
                clin_feat_combined.append(f)
                seen.add(f)
        if force_nomo_has_rs and not has_rs:
            print("  ⚠ FORCE_MODEL_FEATURES 含 RadScore，但当前无可用 RadScore（检查 rad sheet / 组学）")
        extra_roc = [
            c for c in list(getattr(cfg, "FORCE_CLIN_FOR_ROC", []) or [])
            if c and c not in seen and str(c).strip().lower() not in _rs_names
        ]
        if extra_roc:
            print(f"  Extra ROC/NRI/DCA cols (not required in nomogram): {', '.join(extra_roc)}")
        elif reader_col:
            print(f"  Reader comparator (ROC/NRI only, not in nomogram): {reader_col}")
        if not has_rs:
            _score_label = ""
        elif list(_tr_scores) == ["RadScore"]:
            _score_label = " + RadScore"
        else:
            _score_label = " + " + ", ".join(_tr_scores)
        print(f"  Nomogram clinical features ({len(clin_feat_combined)}): "
              f"{', '.join(clin_feat_combined) or '（无）'}"
              + _score_label)

        _te_scores = (
            radiomics.score_map(res_test["rad"])
            if (res_test and res_test.get("rad")) else {}
        )
        _te_clin = data.test.clin if (data.test is not None and len(getattr(data.test, "label", [])) > 0) else None
        _te_y = data.test.label if _te_clin is not None else None
        nomo = None
        if do_nomo and (has_rs or clin_feat_combined):
            nomo = build_nomogram(
                train_label=data.train.label,
                radscore_train=None,
                train_clin=data.train.clin,
                clin_feat=clin_feat_combined,
                radscore_test=None,
                test_clin=_te_clin,
                test_label=_te_y,
                score_train=_tr_scores or None,
                score_test=_te_scores or None,
            )

        def _score_to_prob(score_tr, y_tr, score_te=None):
            if score_tr is None:
                return None, None
            score_tr = np.asarray(score_tr, dtype=float)
            y_tr = np.asarray(y_tr)
            ok = np.isfinite(score_tr)
            if ok.sum() < 5:
                return None, None
            X = sm.add_constant(score_tr[ok])
            fit = sm.GLM(y_tr[ok], X, family=sm.families.Binomial()).fit()
            p_tr = np.full(len(score_tr), np.nan)
            p_tr[ok] = fit.predict(X)
            p_te = None
            if score_te is not None:
                score_te = np.asarray(score_te, dtype=float)
                ok2 = np.isfinite(score_te)
                if ok2.sum() >= 1:
                    X2 = sm.add_constant(score_te[ok2])
                    p_te = np.full(len(score_te), np.nan)
                    p_te[ok2] = fit.predict(X2)
            return p_tr, p_te

        def _apply_clin_model(clin_df):
            if res_train["clin"].model is None or len(train_clin_feat) == 0:
                return None
            X = sm.add_constant(
                clin_df[train_clin_feat].apply(pd.to_numeric, errors="coerce")
            )
            for c in res_train["clin"].model.params.index:
                if c not in X.columns and c != "const":
                    X[c] = np.nan
            cols = [c for c in res_train["clin"].model.params.index if c in X.columns]
            return res_train["clin"].model.predict(X[cols], which="linear").to_numpy()

        clin_score_tr = None
        try:
            clin_score_tr = _apply_clin_model(data.train.clin)
        except Exception as e:
            print(f"  ⚠ ClinicalScore train failed: {e}")

        def _expand_to_full(prob_short, df_ref, n_full):
            if prob_short is None or df_ref is None:
                return None
            out = np.full(n_full, np.nan)
            idx = df_ref.index.to_numpy()
            out[idx] = np.asarray(prob_short, dtype=float)
            return out

        p_nomo_tr = None
        if nomo is not None:
            p_nomo_tr = _expand_to_full(nomo.train_prob, nomo.df_train, len(data.train.label))
        p_comb_tr = p_nomo_tr if has_rs else None
        p_clin_tr, _ = _score_to_prob(clin_score_tr, data.train.label, None)
        if not has_rs and p_nomo_tr is not None:
            p_clin_tr = p_nomo_tr
        p_rad_map = {}
        for _sname, _svec in _tr_scores.items():
            _p_one, _ = _score_to_prob(_svec, data.train.label, None)
            if _p_one is not None:
                p_rad_map[str(_sname)] = _p_one
        p_rad_tr = p_rad_map.get("RadScore")

        def _apply_nomo(layer, rs):
            if nomo is None or nomo.model is None:
                return None
            try:
                df_v = layer.clin.copy()
                if isinstance(rs, dict):
                    for name, arr in rs.items():
                        if arr is None:
                            continue
                        df_v[str(name)] = np.asarray(arr, dtype=float)
                elif rs is not None:
                    df_v["RadScore"] = np.asarray(rs, dtype=float)
                missing = [f for f in (nomo.features or []) if f not in df_v.columns]
                if missing:
                    return None
                use = list(nomo.features or [])
                Xm = sm.add_constant(df_v[use].apply(pd.to_numeric, errors="coerce"))
                cols = [c for c in nomo.model.params.index if c in Xm.columns]
                return nomo.model.predict(Xm[cols]).to_numpy()
            except Exception as e:
                print(f"  ⚠ Nomogram apply failed: {e}")
                return None

        def _val_scores(layer, rad_res_layer):
            """验证层 Clinical / RadScore / Combined 概率（train 拟合）。"""
            try:
                cs = _apply_clin_model(layer.clin)
            except Exception:
                cs = None
            payload = _score_payload(rad_res_layer)
            _, p_c_val = _score_to_prob(clin_score_tr, data.train.label, cs)
            te_map = radiomics.score_map(rad_res_layer) if rad_res_layer is not None else {}
            p_map = {}
            for name, tr_vec in _tr_scores.items():
                _, p_te = _score_to_prob(tr_vec, data.train.label, te_map.get(name))
                if p_te is not None:
                    p_map[str(name)] = p_te
            if rad_res_layer is not None and p_map:
                rad_res_layer.score_probs = p_map
            p_r_val = p_map.get("RadScore")
            p_nomo_val = _apply_nomo(layer, payload)
            p_comb_val = p_nomo_val if has_rs else None
            if not has_rs and p_nomo_val is not None:
                p_c_val = p_nomo_val
            return p_c_val, p_r_val, p_comb_val

        def _refit_val_scores(layer, rad_res_layer):
            """验证层：锁定 Train 特征，重估 CLN / RadScore / Combined（不重筛）。"""
            if list(getattr(res_train["rad"], "groups", None) or []):
                refit_res = radiomics.refit_locked_result(
                    res_train["rad"], layer.rad, layer.label,
                )
                scores = radiomics.score_map(refit_res)
                p_c = None
                if train_clin_feat:
                    p_c = refit_clinical_prob(layer.clin, layer.label, train_clin_feat)
                    if p_c is None:
                        print("  ⚠ Clinical refit failed — keep train formula")
                        try:
                            cs = _apply_clin_model(layer.clin)
                        except Exception:
                            cs = None
                        _, p_c = _score_to_prob(clin_score_tr, data.train.label, cs)
                p_comb = None
                if has_rs and scores:
                    p_comb = refit_combined_prob(
                        layer.clin, layer.label, clin_feat_combined, scores,
                    )
                    if p_comb is None:
                        print("  ⚠ Combined refit failed — keep train formula")
                        p_comb = _apply_nomo(layer, scores)
                elif not has_rs and nomo is not None:
                    p_nomo_v = _apply_nomo(layer, None)
                    if p_nomo_v is not None:
                        p_c = p_nomo_v
                p_map = {}
                y_ref = np.asarray(layer.label)
                for name, arr in scores.items():
                    arr = np.asarray(arr, dtype=float)
                    ok_r = np.isfinite(arr)
                    if ok_r.sum() >= 5 and len(np.unique(y_ref[ok_r])) >= 2:
                        Xr = sm.add_constant(arr[ok_r])
                        fit_r = sm.GLM(
                            y_ref[ok_r], Xr, family=sm.families.Binomial()
                        ).fit()
                        p_one = np.full(len(arr), np.nan)
                        p_one[ok_r] = fit_r.predict(Xr)
                        p_map[str(name)] = p_one
                if rad_res_layer is not None and p_map:
                    rad_res_layer.score_probs = p_map
                return p_c, p_map.get("RadScore"), p_comb, scores or None
            rs = _validation_radscore(layer, rad_res_layer)
            p_c = None
            if train_clin_feat:
                p_c = refit_clinical_prob(layer.clin, layer.label, train_clin_feat)
                if p_c is None:
                    print("  ⚠ Clinical refit failed — keep train formula")
                    try:
                        cs = _apply_clin_model(layer.clin)
                    except Exception:
                        cs = None
                    _, p_c = _score_to_prob(clin_score_tr, data.train.label, cs)
            p_comb = None
            if has_rs and rs is not None:
                p_comb = refit_combined_prob(
                    layer.clin, layer.label, clin_feat_combined, rs,
                )
                if p_comb is None:
                    print("  ⚠ Combined refit failed — keep train formula")
                    p_comb = _apply_nomo(layer, rs)
            elif not has_rs and nomo is not None:
                p_nomo_v = _apply_nomo(layer, None)
                if p_nomo_v is not None:
                    p_c = p_nomo_v
            p_r = None
            if rs is not None:
                rs_arr = np.asarray(rs, dtype=float)
                ok_r = np.isfinite(rs_arr)
                if ok_r.sum() >= 5 and len(np.unique(layer.label[ok_r])) >= 2:
                    Xr = sm.add_constant(rs_arr[ok_r])
                    fit_r = sm.GLM(
                        layer.label[ok_r], Xr, family=sm.families.Binomial()
                    ).fit()
                    p_r = np.full(len(rs_arr), np.nan)
                    p_r[ok_r] = fit_r.predict(Xr)
            return p_c, p_r, p_comb, rs

        # train：合并 ROC（单临床 p<阈值 + RadScore + Clinical + Combined）/ 校准 / DCA
        ml_tr = _merged_roc_models(
            data.train, _score_payload(res_train["rad"]), p_clin=p_clin_tr, p_comb=p_comb_tr,
        )
        if ml_tr:
            plot_roc(ml_tr, data.train.label, dataset_label="train", val_mode=val_mode)
            _maybe_td_rocs("train", ml_tr, data.train)
        def _add_reader_to_dca(ml_dca: Dict, clin_df: pd.DataFrame) -> Dict:
            """按 DCA_CURVES 收曲线；未列出的临床列也可从 clin 补。"""
            wanted = [str(x).strip() for x in (getattr(cfg, "DCA_CURVES", None) or []) if str(x).strip()]
            if not wanted:
                wanted = ["Clinical", "RadScore", "Combined"]
            extras = []
            reader = getattr(cfg, "READER_COMPARE_COL", None)
            if reader:
                extras.append(reader)
            for c in list(getattr(cfg, "FORCE_CLIN_FOR_ROC", []) or []):
                if c and c not in extras:
                    extras.append(c)
            for c in extras:
                if c in clin_df.columns and c not in ml_dca:
                    ml_dca[c] = pd.to_numeric(clin_df[c], errors="coerce").to_numpy()
            lut = {str(k).strip().lower(): k for k in ml_dca}
            clin_lut = {str(c).strip().lower(): c for c in clin_df.columns}
            picked: Dict = {}
            for w in wanted:
                wk = w.lower()
                if wk in lut:
                    picked[lut[wk]] = ml_dca[lut[wk]]
                    continue
                if wk in clin_lut:
                    col = clin_lut[wk]
                    picked[col] = pd.to_numeric(clin_df[col], errors="coerce").to_numpy()
            want_rad = any(
                str(w).strip().lower().replace(" ", "").replace("_", "") == "radscore"
                for w in wanted
            )
            if want_rad:
                for k, v in list(ml_dca.items()):
                    ks = str(k)
                    if ks == "RadScore" or ks.startswith("RadScore_"):
                        picked.setdefault(k, v)
            return picked

        # DCA：模型级概率 + Reader 对照（若有）
        ml_tr_dca = {}
        if p_clin_tr is not None:
            ml_tr_dca["Clinical"] = p_clin_tr
        for _sname, _p in p_rad_map.items():
            ml_tr_dca[_sname] = _p
        if p_comb_tr is not None:
            ml_tr_dca["Combined"] = p_comb_tr
        ml_tr_dca = _add_reader_to_dca(ml_tr_dca, data.train.clin)
        cal_prob = p_comb_tr if has_rs else p_clin_tr
        cal_name = "Combined" if has_rs and p_comb_tr is not None else "Clinical"
        if getattr(cfg, "DO_CALIBRATION", False) and cal_prob is not None:
            plot_calibration(cal_prob, data.train.label,
                             model_name=cal_name, dataset_label="train",
                             recalibrate=False)
        if getattr(cfg, "DO_DCA", False) and ml_tr_dca:
            ok = np.ones(len(data.train.label), dtype=bool)
            for v in ml_tr_dca.values():
                ok &= np.isfinite(np.asarray(v, dtype=float))
            if ok.sum() >= 10:
                plot_dca({k: np.asarray(v)[ok] for k, v in ml_tr_dca.items()},
                         data.train.label[ok], dataset_label="train")

        # 内部 test：应用 train 模型 + 合并 ROC（外验另走锁死评估节）
        val_jobs = []
        val_probs: Dict[str, tuple] = {}
        if res_test is not None and len(data.test.label) > 0:
            val_jobs.append(("test", data.test, res_test["rad"]))

        for ds_label, layer, rad_layer in val_jobs:
            print(f"  Validation [{ds_label}]  VAL_MODE={val_mode}")
            if val_mode == "refit":
                p_c, p_r, p_comb, rs = _refit_val_scores(layer, rad_layer)
            else:
                p_c, p_r, p_comb = _val_scores(layer, rad_layer)
                rs = _score_payload(rad_layer)
            val_probs[ds_label] = (p_c, p_r, p_comb, layer)
            ml = _merged_roc_models(layer, rs, p_clin=p_c, p_comb=p_comb)
            if ml:
                plot_roc(ml, layer.label, dataset_label=ds_label, val_mode=val_mode)
                _maybe_td_rocs(ds_label, ml, layer)
            ml_dca = {}
            if p_c is not None:
                ml_dca["Clinical"] = p_c
            pmap = getattr(rad_layer, "score_probs", None) or {}
            if pmap:
                ml_dca.update(pmap)
            elif p_r is not None:
                ml_dca["RadScore"] = p_r
            if p_comb is not None:
                ml_dca["Combined"] = p_comb
            ml_dca = _add_reader_to_dca(ml_dca, layer.clin)
            if getattr(cfg, "DO_CALIBRATION", False) and p_comb is not None:
                plot_calibration(
                    p_comb, layer.label,
                    model_name="Combined", dataset_label=ds_label,
                    recalibrate=(val_mode == "apply_formula"),
                )
            if getattr(cfg, "DO_DCA", False) and ml_dca:
                ok = np.ones(len(layer.label), dtype=bool)
                for v in ml_dca.values():
                    ok &= np.isfinite(np.asarray(v, dtype=float))
                if ok.sum() >= 10:
                    plot_dca({k: np.asarray(v)[ok] for k, v in ml_dca.items()},
                             layer.label[ok], dataset_label=ds_label)

        if _subgroup_eval_mode():
            try:
                subgroup_results = _run_subgroup_eval(
                    data=data,
                    res_test=res_test,
                    apply_radscore=_apply_train_radscore,
                    score_layer=_val_scores,
                    merged_roc=_merged_roc_models,
                    add_dca=_add_reader_to_dca,
                    val_mode=_subgroup_val_mode(),
                    refit_layer=_refit_val_scores,
                )
            except Exception as e:
                run_status.warn("subgroup", e)
        if external_layers:
            try:
                external_results = _run_external_eval(
                    layers=external_layers,
                    apply_radscore=_apply_train_radscore,
                    score_layer=_val_scores,
                    merged_roc=_merged_roc_models,
                    add_dca=_add_reader_to_dca,
                    val_mode=_external_val_mode(),
                    refit_layer=_refit_val_scores,
                )
            except Exception as e:
                run_status.warn("external", e)

        nri_blocks: List[dict] = []
        if getattr(cfg, "DO_NRI", False):
            gmap = group_label_map(data.train)
            old_spec = getattr(cfg, "NRI_MODEL_OLD", ["clinical"])
            new_spec = getattr(cfg, "NRI_MODEL_NEW", ["combined"])
            cutoff = getattr(cfg, "NRI_CUTOFF", [0.0, 0.3, 0.7, 1.0])
            arms = nri_old_arms(old_spec)
            try:
                test_pack = val_probs.get("test")
                use_test = (
                    test_pack is not None
                    and test_pack[3] is not None
                    and len(test_pack[3].label) >= 10
                )
                if use_test:
                    p_c_n, p_r_n, p_comb_n, nri_layer = test_pack
                    y_nri = nri_layer.label
                    layer_name = "test"
                    apparent = False
                    gmap = group_label_map(nri_layer)
                else:
                    p_c_n, p_r_n, p_comb_n = p_clin_tr, p_rad_tr, p_comb_tr
                    y_nri = data.train.label
                    layer_name = "train"
                    apparent = True
                    gmap = group_label_map(data.train)
            except Exception as e:
                run_status.warn("NRI", e)
                use_test = False
                arms = []
            reader = getattr(cfg, "READER_COMPARE_COL", None)
            for i, arm in enumerate(arms):
                label = arm[0] if len(arm) == 1 else ", ".join(arm)
                slug = _nri_slug(label)
                try:
                    print(
                        f"  NRI/IDI layer={layer_name} apparent={apparent}  "
                        f"old={arm} new={new_spec}"
                        + (f"  reader={reader}" if reader else "")
                    )
                    p_old = _spec_to_prob(arm, p_c_n, p_r_n, p_comb_n)
                    p_new = _spec_to_prob(new_spec, p_c_n, p_r_n, p_comb_n)
                    if p_old is not None and p_new is not None:
                        nri_one = nri_from_probs(
                            p_old, p_new, y_nri, cutoff=cutoff, seed=cfg.SEED,
                            apparent=apparent, layer=layer_name,
                        )
                    else:
                        frame_clin = nri_layer.clin if use_test else data.train.clin
                        frame_rad = (
                            res_test["rad"].radscore if use_test else res_train["rad"].radscore
                        )
                        x1 = resolve_model_frame(
                            arm, frame_clin, train_clin_feat,
                            frame_rad, side_name="NRI_OLD",
                        )
                        x2 = resolve_model_frame(
                            new_spec, frame_clin, train_clin_feat,
                            frame_rad, side_name="NRI_NEW",
                        )
                        nri_one = nri(
                            x1, x2, y_nri, cutoff=cutoff, seed=cfg.SEED,
                            apparent=True, layer=layer_name + "+refit",
                        )
                    print(
                        f"  NRI [{label}] total = {nri_one['NRI_total']:.4f}  "
                        f"(n={nri_one.get('n_used')}  bins={nri_one.get('n_risk_bins')}  "
                        f"cutoff={nri_one.get('cutoff')})"
                    )
                    chord_label = layer_name if i == 0 else f"{layer_name}_{slug}"
                    counts = nri_one.get("chord_counts")
                    n0, n1 = gmap.get(0, "Negative"), gmap.get(1, "Positive")
                    chord_file = ""
                    if counts and sum(counts) > 0:
                        cir_plot(
                            counts,
                            a=f"PC {n0}",
                            b=f"PC {n1}",
                            c=f"SC {n0}",
                            d=f"SC {n1}",
                            title_0=f"Reclassification ({n0})",
                            title_1=f"Reclassification ({n1})",
                            dataset_label=chord_label,
                        )
                        chord_file = f"chord_{chord_label}.png"
                    nri_one["group_labels"] = gmap
                    idi_one = None
                    idi_file = ""
                    try:
                        if nri_one.get("p_old") is None:
                            raise RuntimeError("IDI skipped: no locked NRI probabilities")
                        idi_one = idi_from_probs(
                            nri_one["p_old"], nri_one["p_new"], nri_one["y"],
                            seed=cfg.SEED,
                            apparent=bool(nri_one.get("apparent")),
                            layer=str(nri_one.get("layer") or ""),
                        )
                        print(
                            f"  IDI [{label}] ({idi_one.get('layer')} N={idi_one.get('n_used')}) "
                            f"= {idi_one['IDI']:.4f}  "
                            f"(95% CI {idi_one['IDI_CI_low']:.4f}–{idi_one['IDI_CI_high']:.4f})"
                        )
                        idi_one["group_labels"] = gmap
                        idi_stem = "idi" if i == 0 else f"idi_{slug}"
                        plot_idi(
                            idi_one,
                            dataset_label=str(nri_one.get("layer") or layer_name),
                            group_labels=gmap,
                            file_stem=idi_stem,
                        )
                        idi_file = f"{idi_stem}.png"
                    except Exception as e:
                        run_status.warn(f"IDI {label}", e)
                    nri_blocks.append({
                        "nri": nri_one,
                        "idi": idi_one,
                        "old_label": label,
                        "chord_file": chord_file,
                        "idi_file": idi_file,
                    })
                except Exception as e:
                    run_status.warn(f"NRI {label}", e)

        nri_result = nri_blocks[0]["nri"] if nri_blocks else None
        idi_result = nri_blocks[0]["idi"] if nri_blocks else None
        combined_info = {
            "nomo": nomo,
            "nri": nri_result,
            "idi": idi_result,
            "nri_blocks": nri_blocks,
            "group_labels": group_label_map(data.train),
            "clin_feat_combined": clin_feat_combined,
            "p_clin_tr": p_clin_tr,
            "p_rad_tr": p_rad_tr,
            "clin_score_all": None,
            "comb_score_all": None,
            "val_mode": val_mode,
        }

        # Clinical / Combined 分数 → 全队列（用于 E/F Correlation）
        try:
            if res_train["clin"].model is not None and len(train_clin_feat) > 0:
                Xa = sm.add_constant(
                    data.all.clin[train_clin_feat].apply(pd.to_numeric, errors="coerce")
                )
                cols = [c for c in res_train["clin"].model.params.index if c in Xa.columns]
                combined_info["clin_score_all"] = (
                    res_train["clin"].model.predict(Xa[cols], which="linear").to_numpy()
                )
        except Exception as e:
            print(f"  ⚠ Clinical score on all failed: {e}")
        try:
            if nomo is not None and nomo.model is not None:
                feats = list(nomo.features)
                Xc = data.all.clin.copy()
                smap_all = radiomics.score_map(res_all["rad"])
                for f in feats:
                    if f in smap_all:
                        Xc[f] = np.asarray(smap_all[f], dtype=float)
                use = [f for f in feats if f in Xc.columns]
                Xm = sm.add_constant(Xc[use].apply(pd.to_numeric, errors="coerce"))
                cols = [c for c in nomo.model.params.index if c in Xm.columns]
                combined_info["comb_score_all"] = (
                    nomo.model.predict(Xm[cols], which="linear").to_numpy()
                )
        except Exception as e:
            print(f"  ⚠ Combined score on all failed: {e}")

    else:
        # 无联合模型：仅画过滤后的单临床 + RadScore
        ml_tr = _merged_roc_models(data.train, _score_payload(res_train["rad"]))
        if ml_tr:
            plot_roc(ml_tr, data.train.label, dataset_label="train", val_mode=val_mode)
            _maybe_td_rocs("train", ml_tr, data.train)
        val_jobs_nc = []
        if res_test is not None and len(data.test.label) > 0:
            val_jobs_nc.append(("test", data.test, res_test["rad"]))
        for ds_label, layer, rad_layer in val_jobs_nc:
            rs = _score_payload(rad_layer)
            if val_mode == "refit":
                rs = _validation_radscore(layer, rad_layer)
            ml_e = _merged_roc_models(layer, rs)
            if ml_e:
                plot_roc(ml_e, layer.label, dataset_label=ds_label, val_mode=val_mode)
                _maybe_td_rocs(ds_label, ml_e, layer)

        def _score_nc(layer, rad_res):
            return None, None, None

        def _refit_nc(layer, rad_res):
            return None, None, None, _validation_radscore(layer, rad_res)

        if _subgroup_eval_mode():
            try:
                subgroup_results = _run_subgroup_eval(
                    data=data,
                    res_test=res_test,
                    apply_radscore=_apply_train_radscore,
                    score_layer=_score_nc,
                    merged_roc=_merged_roc_models,
                    add_dca=None,
                    val_mode=_subgroup_val_mode(),
                    refit_layer=_refit_nc,
                )
            except Exception as e:
                print(f"  ⚠ Subgroup eval failed: {e}")
        if external_layers:
            try:
                external_results = _run_external_eval(
                    layers=external_layers,
                    apply_radscore=_apply_train_radscore,
                    score_layer=_score_nc,
                    merged_roc=_merged_roc_models,
                    add_dca=None,
                    val_mode=_external_val_mode(),
                    refit_layer=_refit_nc,
                )
            except Exception as e:
                print(f"  ⚠ External eval failed: {e}")

    print("\n" + "━" * 70)
    print("LAYER: COMBINED CORRELATION")
    print("━" * 70)

    do_corr = bool(getattr(cfg, "DO_CORR", True))
    do_heatmap = bool(getattr(cfg, "DO_HEATMAP", True))
    if not do_corr:
        print("  DO_CORR=False — skip correlation tables.")
    corr_group = str(getattr(cfg, "CORR_GROUP", "") or "").strip()
    corr_mask = None
    try:
        corr_mask = group_row_mask(data.all.group, corr_group, len(data.all.clin))
    except ValueError as exc:
        print(f"  ⚠ {exc} — 相关分析改用全部样本")
        corr_group = ""
        corr_mask = None
    if corr_group and corr_mask is not None:
        print(f"  Corr group: {corr_group} (N={int(corr_mask.sum())})")
    else:
        print("  Corr group: 全部")
    clin_hm = take_group_rows(data.all.clin, corr_mask)
    rad_hm = take_group_rows(data.all.rad, corr_mask)
    label_hm = data.all.label if corr_mask is None else np.asarray(data.all.label)[corr_mask]

    # Score extras (RadScore / Clinical / Combined) shared by heatmap + corr tables.
    score_names = list(radiomics.score_map(res_train["rad"]))
    rad_selected_names = radiomics.selected_feature_names(res_train["rad"])
    extra_pred = {}
    for _sname, _svec in radiomics.score_map(res_all["rad"]).items():
        got = take_group_values(_svec, corr_mask)
        if corr_mask is not None and got is None:
            print(f"  ⚠ {_sname} 行数与组别筛选不一致，相关表未按组别截断这一列")
            got = _svec
        if got is not None:
            extra_pred[str(_sname)] = got
    radscore_all = extra_pred.get("RadScore")
    if combined_info is not None:
        if combined_info.get("clin_score_all") is not None:
            clin_score = take_group_values(combined_info["clin_score_all"], corr_mask)
            if clin_score is None and corr_mask is not None:
                print("  ⚠ Clinical 分数行数与组别筛选不一致，相关表未按组别截断这一列")
                clin_score = combined_info["clin_score_all"]
            extra_pred["Clinical"] = clin_score
        if combined_info.get("comb_score_all") is not None:
            comb_score = take_group_values(combined_info["comb_score_all"], corr_mask)
            if comb_score is None and corr_mask is not None:
                print("  ⚠ Combined 分数行数与组别筛选不一致，相关表未按组别截断这一列")
                comb_score = combined_info["comb_score_all"]
            extra_pred["Combined"] = comb_score

    # Inject score columns into heatmap clinical frame so HEATMAP_COLS tokens resolve.
    if extra_pred and clin_hm is not None:
        clin_hm = clin_hm.copy()
        n_hm = len(clin_hm)
        for _ek, _ev in extra_pred.items():
            if _ev is None:
                continue
            _arr = np.asarray(_ev)
            if len(_arr) == n_hm:
                clin_hm[_ek] = _arr

    # 热图只读 HEATMAP_COLS。Clinical / RadScore = 评分 + 入选成分。空名单不画。
    drew_heatmap = False
    if do_heatmap:
        heatmap_raw = heatmap_wanted(getattr(cfg, "HEATMAP_COLS", None))
        clin_have = set() if clin_hm is None else set(clin_hm.columns)
        rad_have = set() if rad_hm is None else set(rad_hm.columns)
        clin_screened = [c for c in train_clin_feat if c in clin_have]
        rad_screened = [c for c in rad_selected_names if c in rad_have]
        heatmap_names, heatmap_empty = expand_heatmap_columns(
            heatmap_raw, clin_screened, rad_screened, score_names=score_names,
        )
        for name in heatmap_empty:
            print(f"  ⚠ HEATMAP_COLS {name!r} 没有筛选后的列，已跳过")
        if not heatmap_names:
            if heatmap_raw:
                print("  HEATMAP_COLS 展开后没有可画的列 — skip heatmap.")
            else:
                print("  HEATMAP_COLS 为空 — skip heatmap.")
        else:
            rad_sel, clin_sel, heatmap_missing = select_heatmap_frames(
                heatmap_names, clin_hm, rad_hm, label_hm,
            )
            for name in heatmap_missing:
                print(f"  ⚠ HEATMAP_COLS {name!r} 不在临床列或组学列里，已跳过")
            print(
                "  Heatmap columns: "
                f"clin={list(clin_sel.columns)} rad={list(rad_sel.columns)}"
            )
            if clin_sel.shape[1] + rad_sel.shape[1] == 0:
                print("  Heatmap has no columns — skip.")
            else:
                run_correlation(rad_df=rad_sel, clin_df=clin_sel, dataset_label="selected")
                drew_heatmap = True
    else:
        print("  DO_HEATMAP=False — skip heatmap.")
    if not drew_heatmap:
        clear_selected_heatmap()

    # 相关表：CORR_REFS × CORR_PREDICTORS（只出表，不出图）。空名单 = 不出参考表。
    corr_predictors = list(getattr(cfg, "CORR_PREDICTORS",
                                    ["RadScore", "Clinical", "Combined"]))
    corr_predictors = expand_corr_predictors(
        corr_predictors,
        rad_selected=rad_selected_names,
        clin_selected=list(train_clin_feat or []),
        score_names=score_names,
    )
    corr_refs = [str(x).strip() for x in (getattr(cfg, "CORR_REFS", None) or []) if str(x).strip()]
    corr_refs = expand_corr_predictors(
        corr_refs,
        rad_selected=rad_selected_names,
        clin_selected=list(train_clin_feat or []),
        score_names=score_names,
    )
    if not do_corr:
        corr_refs = []

    print(f"  Corr predictors: {corr_predictors}")
    if not corr_refs:
        print("  Corr refs: （空）— skip Correlation with reference variables")
    else:
        print(f"  Corr refs (tables only): {corr_refs}")
    corr_ref_tables = []
    from dataclasses import replace
    corr_layer = data.all if corr_mask is None else replace(
        data.all,
        clin=clin_hm,
        rad=rad_hm if rad_hm is not None else data.all.rad,
        label=label_hm,
    )
    for ref in corr_refs:
        # 列名对齐只在 check_correlation。这里不再单独把 Age 改成 Age/age。
        corr_ref_tables.append(check_correlation(
            corr_layer, radscore=radscore_all, egfr_col=ref,
            targets=corr_predictors, extra_predictors=extra_pred, seed=cfg.SEED,
        ))
    corr_egfr_df = corr_ref_tables[0] if corr_ref_tables else None
    corr_group_df = corr_ref_tables[1] if len(corr_ref_tables) > 1 else None

    # ── Kaplan–Meier（时间与 IPCW 共用；事件=状态列，空则当前结局）──
    if bool(getattr(cfg, "DO_SURVIVAL", False)):
        try:
            t_all = getattr(data.all, "time", None)
            if t_all is None:
                print("\n  ⚠ Survival skipped: no time vector (check SURVIVAL_TIME_COL)")
            else:
                risk_all = None
                risk_name = "Combined"
                risk_tr = None
                nomo_obj = None if combined_info is None else combined_info.get("nomo")
                if combined_info is not None:
                    if combined_info.get("comb_score_all") is not None:
                        risk_all = np.asarray(combined_info["comb_score_all"], dtype=float)
                        risk_name = "Combined"
                        if nomo_obj is not None and nomo_obj.model is not None:
                            try:
                                Xc = data.train.clin.copy()
                                smap_tr = radiomics.score_map(res_train["rad"])
                                for f in list(nomo_obj.features):
                                    if f in smap_tr:
                                        Xc[f] = np.asarray(smap_tr[f], dtype=float)
                                use = [f for f in nomo_obj.features if f in Xc.columns]
                                Xm = sm.add_constant(
                                    Xc[use].apply(pd.to_numeric, errors="coerce")
                                )
                                cols = [
                                    c for c in nomo_obj.model.params.index if c in Xm.columns
                                ]
                                risk_tr = nomo_obj.model.predict(
                                    Xm[cols], which="linear"
                                ).to_numpy()
                            except Exception as e:
                                print(f"  ⚠ train Combined LP for KM cut: {e}")
                    elif combined_info.get("clin_score_all") is not None:
                        risk_all = np.asarray(combined_info["clin_score_all"], dtype=float)
                        risk_name = "Clinical"
                        if res_train["clin"].model is not None and len(train_clin_feat) > 0:
                            try:
                                Xtr = sm.add_constant(
                                    data.train.clin[train_clin_feat].apply(
                                        pd.to_numeric, errors="coerce"
                                    )
                                )
                                cols = [
                                    c for c in res_train["clin"].model.params.index
                                    if c in Xtr.columns
                                ]
                                risk_tr = res_train["clin"].model.predict(
                                    Xtr[cols], which="linear"
                                ).to_numpy()
                            except Exception:
                                risk_tr = None
                if risk_all is None and radscore_all is not None:
                    risk_all = np.asarray(radscore_all, dtype=float)
                    risk_name = "RadScore"
                    risk_tr = (
                        np.asarray(res_train["rad"].radscore, dtype=float)
                        if res_train["rad"].radscore is not None else None
                    )
                cut = None
                if risk_tr is not None and np.isfinite(risk_tr).sum() >= 5:
                    cut = float(np.nanmedian(risk_tr))
                do_risk = bool(getattr(cfg, "DO_SURVIVAL_BY_RISK", True))
                time_unit = str(getattr(cfg, "SURVIVAL_TIME_UNIT", "months") or "months")
                event = getattr(data.all, "event", None)
                if event is None:
                    event = data.all.label
                plot_survival_bundle(
                    t_all,
                    event,
                    risk_score=risk_all if do_risk else None,
                    risk_name=risk_name,
                    dataset_label="all",
                    time_unit=time_unit,
                    median_cut=cut,
                    do_cox=bool(getattr(cfg, "DO_COX", False)),
                )
        except Exception as e:
            print(f"  ⚠ Survival KM failed: {e}")

    # ── VIF / DAG / SHAP / Mediation（train）──
    advanced_info = None
    if not bool(getattr(cfg, "DO_ADVANCED", True)):
        print("  DO_ADVANCED=False — skip DAG / SHAP / mediation.")
    else:
        try:
            # ClinicalScore：用 train 最终临床模型在全 train 上预测（对齐长度）
            clin_score_tr_full = None
            if res_train["clin"].model is not None and len(train_clin_feat) > 0:
                try:
                    Xtr = sm.add_constant(
                        data.train.clin[train_clin_feat].apply(pd.to_numeric, errors="coerce")
                    )
                    cols = [c for c in res_train["clin"].model.params.index if c in Xtr.columns]
                    clin_score_tr_full = (
                        res_train["clin"].model.predict(Xtr[cols], which="linear").to_numpy()
                    )
                except Exception as e:
                    print(f"  ⚠ ClinicalScore for advanced stats: {e}")

            _adv_scores = radiomics.score_map(res_train["rad"])
            _adv_rs = _adv_scores.get("RadScore") if set(_adv_scores) == {"RadScore"} else None
            frame_tr = advanced_stats.build_analysis_frame(
                data.train.clin,
                data.train.label,
                radscore=_adv_rs,
                clin_score=clin_score_tr_full,
            )
            cand_feats = list(getattr(res_train["clin"], "candidates", None) or [])
            if not cand_feats:
                # 回退：Table1 候选 ≈ 非 skip 临床列
                skip = set(getattr(cfg, "SKIP_CLIN_FEATURES", []))
                cand_feats = [c for c in data.train.clin.columns if c not in skip]

            # SHAP 仅组学：train 上入选特征。分类时各类用自己的训练集标准化。
            rad_feat_df = None
            rad_use_cols: List[str] = []
            _shap_groups = list(getattr(res_train["rad"], "groups", None) or [])
            if _shap_groups:
                frames = []
                for g in _shap_groups:
                    cols = [c for c in (g.features or []) if c in data.train.rad.columns]
                    if not cols:
                        continue
                    params = getattr(g, "preprocess", None)
                    if params:
                        scaled = radiomics._apply_preprocess(
                            data.train.rad.drop(columns=["Name"], errors="ignore"),
                            params,
                        )
                        frames.append(scaled.reindex(columns=cols).reset_index(drop=True))
                    else:
                        frames.append(data.train.rad[cols].reset_index(drop=True))
                if frames:
                    rad_feat_df = pd.concat(frames, axis=1)
                    rad_feat_df = rad_feat_df.loc[:, ~rad_feat_df.columns.duplicated()]
                    rad_use_cols = list(rad_feat_df.columns)
            else:
                rad_feats = list(res_train["rad"].features or [])
                if rad_feats:
                    rad_use_cols = [c for c in rad_feats if c in data.train.rad.columns]
                    if rad_use_cols:
                        params = getattr(res_train["rad"], "preprocess", None)
                        if params:
                            scaled = radiomics._apply_preprocess(
                                data.train.rad.drop(columns=["Name"], errors="ignore"),
                                params,
                            )
                            rad_feat_df = scaled.reindex(columns=rad_use_cols).reset_index(drop=True)
                        else:
                            rad_feat_df = data.train.rad[rad_use_cols].reset_index(drop=True)

            advanced_info = advanced_stats.run_advanced_stats(
                frame_tr,
                candidate_features=cand_feats,
                selected_features=train_clin_feat,
                radscore=_adv_rs,
                clin_score=clin_score_tr_full,
                label=data.train.label,
                rad_feature_df=rad_feat_df,
                rad_feature_names=rad_use_cols or None,
            )
        except Exception as e:
            run_status.warn("advanced", e)
            advanced_info = None

    # 拼板（在 HTML 之前，保证 panel_*.png 可嵌入）
    if bool(getattr(cfg, "DO_FIGURE_PANELS", True)):
        try:
            from modules.stats.figure_panels import build_outcome_panels
            build_outcome_panels(cfg.OUT_DIR)
        except Exception as e:
            run_status.warn("figure_panels", e)

    print("\n" + "=" * 70)
    print(f"PIPELINE {run_status.status}")
    gmap = group_label_map(data.all)
    n1 = int((data.all.label == 1).sum())
    n0 = int((data.all.label == 0).sum())
    print(f"  All:  N={len(data.all.label)} ({gmap.get(1,'1')}={n1}, {gmap.get(0,'0')}={n0})")
    print(f"  Train: N={len(data.train.label)}")
    if res_test is not None and len(data.test.label) > 0:
        print(f"  Internal test: N={len(data.test.label)}")
    if external_results:
        print(f"  External tests: {', '.join(str(e.get('display') or e.get('level') or e.get('name') or 'ext') for e in external_results)}")
    print(f"  All: Table1 only (RadScore on all via train formula: "
          f"{'yes' if radiomics.score_map(res_all['rad']) else 'no'})")
    print(f"  Rad features — Train: {len(radiomics.selected_feature_names(res_train['rad']))}")
    print(f"  Clinical features — Train: {len(res_train['clin'].features)} "
          f"(ROC clin mv p<{getattr(cfg, 'ROC_MV_P_THRESHOLD', 0.05)}: {roc_clin_feats})")
    print(f"  VAL_MODE={val_mode}  ({val_mode_label(val_mode)})")
    import os
    print(f"  Output: {os.path.abspath(cfg.OUT_DIR)}/")
    print("=" * 70 + "\n")

    assoc_info = None
    try:
        from modules.stats.assoc import run_assoc
        assoc_extras: Dict[str, object] = {}
        for _sname, _svec in radiomics.score_map(res_all.get("rad")).items():
            assoc_extras[str(_sname)] = np.asarray(_svec, dtype=float)
        if combined_info is not None:
            if combined_info.get("clin_score_all") is not None:
                assoc_extras["Clinical"] = np.asarray(
                    combined_info["clin_score_all"], dtype=float
                )
            if combined_info.get("comb_score_all") is not None:
                assoc_extras["Combined"] = np.asarray(
                    combined_info["comb_score_all"], dtype=float
                )
        assoc_info = run_assoc(
            data.all,
            extras=assoc_extras or None,
            rad_selected=radiomics.selected_feature_names(res_train["rad"]),
            clin_selected=list(train_clin_feat or []),
            score_names=list(radiomics.score_map(res_train["rad"])),
        )
        if assoc_info and assoc_info.get("warnings"):
            for msg in assoc_info["warnings"]:
                print(f"  ⚠ assoc: {msg}")
        elif assoc_info and assoc_info.get("partial"):
            print(f"  Partial pairs: {len(assoc_info['partial'])}")
    except Exception as e:
        text = str(e)
        # 列名对不上必须停。不要写成警告后再出带 NA 的结果页。
        if text.startswith("找不到列") or text.startswith("缺少列") or "对上不止一列" in text:
            raise
        run_status.warn("assoc", e)

    generate_results_html(
        data, res_all=res_all, res_train=res_train, res_test=res_test,
        corr_egfr_df=corr_egfr_df, corr_group_df=corr_group_df,
        corr_ref_tables=corr_ref_tables,
        seed=cfg.SEED, train_ratio=cfg.TRAIN_RATIO, out_dir=cfg.OUT_DIR,
        combined_info=combined_info, external_results=external_results,
        advanced_info=advanced_info,
        subgroup_results=subgroup_results,
        run_status=run_status.as_dict(),
        assoc_info=assoc_info,
    )


def _resolve_external_specs(input_file: str) -> list:
    """控制台三格（文件/临床sheet/组学sheet）或旧 EXTERNAL_TESTS 列表。
    必须有外验文件 + 临床 sheet + 组学 sheet；仅默认 sheet 名、无文件 = 不做外验。
    （与 console sync_external_tests 一致；勿把主表 input 冒充外验。）
    """
    specs = list(getattr(cfg, "EXTERNAL_TESTS", []) or [])
    if not specs:
        cs = str(getattr(cfg, "EXTERNAL_CLIN_SHEET", "") or "").strip()
        rs = str(getattr(cfg, "EXTERNAL_RAD_SHEET", "") or "").strip()
        f = str(getattr(cfg, "EXTERNAL_FILE", "") or "").strip()
        if f and cs and rs:
            specs = [{"name": "ext", "file": f, "clin_sheet": cs, "rad_sheet": rs}]
    out = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        cs = str(spec.get("clin_sheet") or "").strip()
        rs = str(spec.get("rad_sheet") or "").strip()
        f = str(spec.get("file") or "").strip()
        if not f or not cs or not rs:
            continue
        item = {
            "name": str(spec.get("name") or "ext"),
            "file": f,
            "clin_sheet": cs,
            "rad_sheet": rs,
        }
        rid = str(spec.get("rad_id_col") or "").strip()
        if rid:
            item["rad_id_col"] = rid
        out.append(item)
    return out


def _subgroup_eval_mode() -> bool:
    """SUBGROUP_COL 与结局不同：只评估主模型，不按亚组重训。"""
    sub = str(getattr(cfg, "SUBGROUP_COL", "") or "").strip()
    outcome = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    return bool(sub and sub != outcome)


def _subgroup_val_mode() -> str:
    """亚组切点：只用 SUBGROUP_VAL_MODE，不跟随内部验证。"""
    from modules.stats.val_mode import resolve_val_mode
    raw = str(getattr(cfg, "SUBGROUP_VAL_MODE", "") or "").strip()
    return resolve_val_mode(raw or "apply_formula")


def _external_val_mode() -> str:
    """外验切点：只用 EXTERNAL_VAL_MODE，不跟随内部验证。"""
    from modules.stats.val_mode import resolve_val_mode
    raw = str(getattr(cfg, "EXTERNAL_VAL_MODE", "") or "").strip()
    return resolve_val_mode(raw or "refit")


def _wanted_subgroup_levels(series: pd.Series) -> list[str]:
    skip = {"", "nan", "none", "nat", "<na>"}
    present: list[str] = []
    seen: set[str] = set()
    for x in series.astype(str):
        s = str(x).strip()
        if not s or s.lower() in skip or s in seen:
            continue
        seen.add(s)
        present.append(s)
    keep = [str(x).strip() for x in (getattr(cfg, "GROUP_KEEP", None) or []) if str(x).strip()]
    if keep:
        return [k for k in keep if k in seen]
    pairs = _parse_pairwise(getattr(cfg, "PAIRWISE_GROUPS", None))
    if pairs:
        out: list[str] = []
        for a, b in pairs:
            if a in seen and a not in out:
                out.append(a)
            if b in seen and b not in out:
                out.append(b)
        return out
    return present


def _run_subgroup_eval(
    *,
    data,
    res_test,
    apply_radscore,
    score_layer,
    merged_roc,
    add_dca=None,
    val_mode: str = "apply_formula",
    refit_layer=None,
) -> list:
    """用锁死的主模型（列线图 / RadScore / Clinical）评估各亚组。"""
    from modules.config.outcome import level_display_name, safe_dir_token
    from modules.stats.curves_roc import ROC_METRICS

    sg = getattr(data.all, "subgroup", None)
    if sg is None or len(sg) == 0:
        return []
    levels = _wanted_subgroup_levels(sg)
    if not levels:
        print("  ⚠ 亚组列无可用水平 — skip subgroup eval")
        return []
    col = str(getattr(cfg, "SUBGROUP_COL", "") or "").strip()
    print("\n" + "━" * 70)
    print(f"LAYER: SUBGROUP EVAL — locked nomogram on {col!r}  [{val_mode}]")
    print("━" * 70)
    out: list = []
    jobs: list = []
    if getattr(data, "train", None) is not None and len(getattr(data.train, "label", [])) > 0:
        jobs.append(("train", data.train))
    if res_test is not None and data.test is not None and len(getattr(data.test, "label", [])) > 0:
        jobs.append(("test", data.test))
    if not jobs:
        print("  ⚠ 亚组无 train/test 层 — skip subgroup eval")
        return []
    for level in levels:
        disp = level_display_name(level)
        token = safe_dir_token(disp or level)
        row: dict = {
            "level": level, "display": disp or level, "token": token,
            "col": col, "val_mode": val_mode, "splits": {},
        }
        for split, layer in jobs:
            series = getattr(layer, "subgroup", None)
            if series is None or len(series) != len(layer.label):
                print(f"  ⚠ {level} [{split}]: no subgroup labels — skip")
                continue
            mask = series.astype(str).str.strip() == str(level)
            n = int(mask.sum())
            y = np.asarray(layer.label)[mask.to_numpy()]
            n1 = int((y == 1).sum()) if n else 0
            n0 = int((y == 0).sum()) if n else 0
            rec = {"n": n, "n1": n1, "n0": n0, "auc": None, "panel": None}
            if n < 10 or n1 < 3 or n0 < 3:
                print(f"  {disp} [{split}]: n={n} (y1={n1}, y0={n0}) — too small, skip")
                row["splits"][split] = rec
                continue
            sub = data_io.slice_layer(layer, mask.to_numpy())
            rad_sub = apply_radscore(sub)
            rs = None if rad_sub is None else rad_sub.radscore
            if rad_sub is not None and list(getattr(rad_sub, "groups", None) or []):
                rs = radiomics.score_map(rad_sub) or None
            if val_mode == "refit" and refit_layer is not None:
                p_c, p_r, p_comb, rs = refit_layer(sub, rad_sub)
            else:
                p_c, p_r, p_comb = score_layer(sub, rad_sub)
            ds = f"sg_{token}_{split}"
            ml = merged_roc(sub, rs, p_clin=p_c, p_comb=p_comb)
            if ml:
                plot_roc(ml, sub.label, dataset_label=ds, val_mode=val_mode)
                mdf = ROC_METRICS.get(f"roc_{ds}")
                if mdf is not None and len(mdf):
                    hit = mdf[mdf["Model"].astype(str).str.lower().eq("combined")]
                    if hit.empty:
                        hit = mdf
                    rec["auc"] = float(hit.iloc[0]["AUC"]) if len(hit) else None
                    rec["metrics"] = mdf
                rec["panel"] = f"roc_{ds}.png"
            if add_dca is not None:
                ml_dca = {}
                if p_c is not None:
                    ml_dca["Clinical"] = p_c
                pmap = getattr(rad_sub, "score_probs", None) or {}
                if pmap:
                    ml_dca.update(pmap)
                elif p_r is not None:
                    ml_dca["RadScore"] = p_r
                if p_comb is not None:
                    ml_dca["Combined"] = p_comb
                ml_dca = add_dca(ml_dca, sub.clin)
                if getattr(cfg, "DO_CALIBRATION", False) and p_comb is not None:
                    plot_calibration(
                        p_comb, sub.label,
                        model_name="Combined", dataset_label=ds,
                        recalibrate=(val_mode == "apply_formula"),
                    )
                    rec["cal"] = f"calibration_{ds}.png"
                if getattr(cfg, "DO_DCA", False) and ml_dca:
                    ok = np.ones(len(sub.label), dtype=bool)
                    for v in ml_dca.values():
                        ok &= np.isfinite(np.asarray(v, dtype=float))
                    if ok.sum() >= 10:
                        plot_dca(
                            {k: np.asarray(v)[ok] for k, v in ml_dca.items()},
                            sub.label[ok], dataset_label=ds,
                        )
                        rec["dca"] = f"dca_{ds}.png"
            if rec.get("auc") is not None:
                print(f"  {disp} [{split}]: n={n} (y1={n1})  AUC={rec['auc']:.3f}")
            else:
                print(f"  {disp} [{split}]: n={n} (y1={n1})")
            row["splits"][split] = rec
        row["panels"] = {
            "roc": f"panel_roc_sg_{token}.png",
            "cal": f"panel_cal_sg_{token}.png",
            "dca": f"panel_dca_sg_{token}.png",
        }
        out.append(row)
    try:
        from modules.stats.figure_panels import stitch_subgroup_train_test
        stitch_subgroup_train_test(cfg.OUT_DIR)
    except Exception as e:
        print(f"  ⚠ subgroup train|test panels failed: {e}")
    return out


def _run_external_eval(
    *,
    layers: list,
    apply_radscore,
    score_layer,
    merged_roc,
    add_dca=None,
    val_mode: str = "apply_formula",
    refit_layer=None,
) -> list:
    """主模型锁死后评估外验整队列（不重筛、不按亚组再拆）。多条横拼，一条不拼。"""
    from modules.config.outcome import safe_dir_token
    from modules.stats.curves_roc import ROC_METRICS

    if not layers:
        return []
    print("\n" + "━" * 70)
    print(f"LAYER: EXTERNAL EVAL — locked nomogram  [{val_mode}]  n={len(layers)}")
    print("━" * 70)
    out: list = []
    tokens: list[str] = []
    used_tok: set[str] = set()
    for spec in layers:
        name = str(spec.get("name") or "ext")
        layer = spec.get("data")
        token = safe_dir_token(name) or "ext"
        base = token
        n = 2
        while token in used_tok:
            token = f"{base}{n}"
            n += 1
        used_tok.add(token)
        tokens.append(token)
        row: dict = {
            "level": name, "display": name, "token": token,
            "col": "external", "val_mode": val_mode, "splits": {},
        }
        rec = {"n": 0, "n1": 0, "n0": 0, "auc": None, "panel": None}
        if layer is None or len(getattr(layer, "label", [])) == 0:
            print(f"  ⚠ {name}: empty — skip")
            row["splits"]["ext"] = rec
            out.append(row)
            continue
        y = np.asarray(layer.label)
        n = int(len(y))
        n1 = int((y == 1).sum())
        n0 = int((y == 0).sum())
        rec.update({"n": n, "n1": n1, "n0": n0})
        if cfg.ipcw_roc_on() and not getattr(layer, "ipcw", None):
            rec["ipcw_note"] = (
                "Time-dependent ROC (IPCW) was not run on this external set: "
                "the sheet has no ipcw_time or ipcw_status."
            )
        if n < 10 or n1 < 3 or n0 < 3:
            rec["skip_reason"] = (
                f"too small for ROC/DCA (n={n}, events={n1}, non-events={n0}; "
                "need n≥10 and both classes ≥3)"
            )
            print(f"  {name}: n={n} (y1={n1}, y0={n0}) — too small, skip")
            row["splits"]["ext"] = rec
            out.append(row)
            continue
        rad_sub = apply_radscore(layer)
        rs = None if rad_sub is None else rad_sub.radscore
        if rad_sub is not None and list(getattr(rad_sub, "groups", None) or []):
            rs = radiomics.score_map(rad_sub) or None
        if val_mode == "refit" and refit_layer is not None:
            p_c, p_r, p_comb, rs = refit_layer(layer, rad_sub)
        else:
            p_c, p_r, p_comb = score_layer(layer, rad_sub)
        ds = f"ext_{token}"
        ml = merged_roc(layer, rs, p_clin=p_c, p_comb=p_comb)
        if ml:
            plot_roc(ml, layer.label, dataset_label=ds, val_mode=val_mode)
            _maybe_td_rocs(ds, ml, layer, ipcw_required=False)
            mdf = ROC_METRICS.get(f"roc_{ds}")
            if mdf is not None and len(mdf):
                hit = mdf[mdf["Model"].astype(str).str.lower().eq("combined")]
                if hit.empty:
                    hit = mdf
                rec["auc"] = float(hit.iloc[0]["AUC"]) if len(hit) else None
                rec["metrics"] = mdf
            rec["panel"] = f"roc_{ds}.png"
        if add_dca is not None:
            ml_dca = {}
            if p_c is not None:
                ml_dca["Clinical"] = p_c
            pmap = getattr(rad_sub, "score_probs", None) or {}
            if pmap:
                ml_dca.update(pmap)
            elif p_r is not None:
                ml_dca["RadScore"] = p_r
            if p_comb is not None:
                ml_dca["Combined"] = p_comb
            ml_dca = add_dca(ml_dca, layer.clin)
            if getattr(cfg, "DO_CALIBRATION", False) and p_comb is not None:
                plot_calibration(
                    p_comb, layer.label,
                    model_name="Combined", dataset_label=ds,
                    recalibrate=(val_mode == "apply_formula"),
                )
                rec["cal"] = f"calibration_{ds}.png"
            if getattr(cfg, "DO_DCA", False) and ml_dca:
                ok = np.ones(len(layer.label), dtype=bool)
                for v in ml_dca.values():
                    ok &= np.isfinite(np.asarray(v, dtype=float))
                if ok.sum() >= 10:
                    plot_dca(
                        {k: np.asarray(v)[ok] for k, v in ml_dca.items()},
                        layer.label[ok], dataset_label=ds,
                    )
                    rec["dca"] = f"dca_{ds}.png"
        if rec.get("auc") is not None:
            print(f"  {name}: n={n} (y1={n1})  AUC={rec['auc']:.3f}")
        else:
            print(f"  {name}: n={n} (y1={n1})")
        row["splits"]["ext"] = rec
        row["panels"] = {
            "roc": "panel_roc_ext.png" if len(layers) >= 2 else rec.get("panel"),
            "cal": "panel_cal_ext.png" if len(layers) >= 2 else rec.get("cal"),
            "dca": "panel_dca_ext.png" if len(layers) >= 2 else rec.get("dca"),
        }
        out.append(row)
    if len(tokens) >= 2:
        try:
            from modules.stats.figure_panels import stitch_external_panels
            stitch_external_panels(cfg.OUT_DIR, tokens)
        except Exception as e:
            print(f"  ⚠ external panels failed: {e}")
    return out


def _parse_pairwise(raw) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for item in list(raw or []):
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            a, b = str(item[0]).strip(), str(item[1]).strip()
        elif isinstance(item, str) and "vs" in item.lower():
            a, b = [x.strip() for x in item.replace("VS", "vs").split("vs", 1)]
        else:
            continue
        if a and b and a != b:
            pairs.append((a, b))
    return pairs


def _resolve_pairwise() -> list[tuple[str, str]]:
    pairs = _parse_pairwise(getattr(cfg, "PAIRWISE_GROUPS", None))
    if pairs:
        return pairs
    if not bool(getattr(cfg, "PAIRWISE_ALL", False)):
        return []
    keep = [str(x).strip() for x in (getattr(cfg, "GROUP_KEEP", None) or []) if str(x).strip()]
    if len(keep) >= 2:
        labels = keep
    else:
        return []
    out = []
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            out.append((a, b))
    return out


def _resolve_outcomes(outcome_arg: str | None) -> list[str] | None:
    """结局列表。优先 --outcome，否则 OUTCOME_COLS；再退到 GROUP_SOURCE_COL / GROUP_COL_KEYWORD。"""
    cols = [str(x).strip() for x in (getattr(cfg, "OUTCOME_COLS", None) or []) if str(x).strip()]
    if outcome_arg:
        items = [x.strip() for x in str(outcome_arg).split(",") if x.strip()]
        if not items:
            raise ValueError("--outcome 为空")
        return items
    if cols:
        return cols
    fallback = (
        str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
        or str(getattr(cfg, "GROUP_COL_KEYWORD", "") or "").strip()
        or "Group"
    )
    if bool(getattr(cfg, "OUTCOME_OUT_SUBDIR", True)) and fallback:
        return [fallback]
    return None


def _apply_console_overlay() -> None:
    """console.html / JSON 覆盖配置优先于 settings.py。"""
    # 仅当控制台显式设置环境变量时才覆盖；命令行默认只用 settings.py
    path = os.environ.get("RAD_CONSOLE_OVERLAY") or ""
    if not path:
        return
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"RAD_CONSOLE_OVERLAY points to a missing overlay file: {path}"
        )
    import json
    with open(path, encoding="utf-8") as f:
        obj = json.load(f)
    ov = obj.get("overlay") if isinstance(obj, dict) and isinstance(obj.get("overlay"), dict) else obj
    if not isinstance(ov, dict):
        return
    from modules.config import settings as S
    n = 0
    for k, v in ov.items():
        if not str(k).isidentifier() or str(k).startswith("_"):
            continue
        if k in ("DAG_EDGES", "DAG_MEDIATION_HIGHLIGHT") and isinstance(v, list):
            v = [tuple(x) if isinstance(x, list) else x for x in v]
        setattr(cfg, k, v)
        setattr(S, k, v)
        n += 1
    out_dir = getattr(cfg, "OUT_DIR", None)
    if out_dir:
        png = os.path.join(str(out_dir), "PNG")
        cfg.PNG_DIR = png
        S.PNG_DIR = png
    print(
        f"  ◆ Console overlay: {n} keys from {os.path.abspath(path)}"
        f"  (HTML/JSON 优先于 settings.py)",
        flush=True,
    )
    try:
        from modules.config.style import apply_fig_palette
        applied = apply_fig_palette(S)
        if applied:
            print(
                f"  ◆ Fig palette: {applied.get('palette')}  "
                f"main={applied.get('main')}  aux={applied.get('aux')}",
                flush=True,
            )
    except Exception as e:
        print(f"  ⚠ Fig palette skipped: {e}", flush=True)


def _maybe_write_qc_report(input_file: Optional[str] = None) -> None:
    """主循环前生成 ref/qc.html（一次，与结局无关）；外验另写 ref/qc-ext-*.html。"""
    if not bool(getattr(cfg, "DO_QC_REPORT", True)):
        return
    sheet = str(getattr(cfg, "QC_SHEET", "") or "").strip()
    xlsx = input_file or getattr(cfg, "INPUT", "data_merged.xlsx")
    from modules.config.ini_io import resolve_project_dir
    from modules.utils.u_qc import (
        relocate_legacy_qc_html, resolve_qc_html_path,
        write_external_qc_reports, write_project_data_qc,
    )
    dest = resolve_project_dir()
    relocate_legacy_qc_html(dest)
    cfg.QC_HTML = "ref/qc.html"
    out_path = resolve_qc_html_path("qc.html", dest)
    from modules import config as _cfg
    settings = {
        "CLIN_SHEET": getattr(_cfg, "CLIN_SHEET", "cln"),
        "RAD_SHEET": getattr(_cfg, "RAD_SHEET", "rad"),
        "QC_SHEET": sheet,
        "ICC_SHEET": getattr(_cfg, "ICC_SHEET", "icc"),
        "CLIN_ID_COL": getattr(_cfg, "CLIN_ID_COL", "Patient_ID"),
        "RAD_ID_COL": getattr(_cfg, "RAD_ID_COL", "Patient_ID"),
        "OUTCOME_COLS": list(getattr(_cfg, "OUTCOME_COLS", None) or []),
        "SKIP_CLIN_FEATURES": list(getattr(_cfg, "SKIP_CLIN_FEATURES", None) or []),
        "ICC_THRESHOLD": getattr(_cfg, "ICC_THRESHOLD", 0.75),
        "ICC_INTRA_COL": getattr(_cfg, "ICC_INTRA_COL", "intra_ICC"),
        "ICC_INTER_COL": getattr(_cfg, "ICC_INTER_COL", "inter_ICC"),
    }
    for k in (
        "EXTERNAL_TESTS", "EXTERNAL_FILE",
        "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
        "EXTERNAL_RAD_ID_COL",
    ):
        if hasattr(cfg, k):
            settings[k] = getattr(cfg, k)
    print("\n═══ QC REPORT ═══")
    try:
        if out_path.is_file() and out_path.stat().st_size > 200:
            print(f"  exists → skip  ({out_path})")
        else:
            write_project_data_qc(xlsx, settings=settings, out_html=out_path)
    except Exception as e:
        print(f"  ⚠ QC report failed: {e}")
    try:
        write_external_qc_reports(
            settings, dest=dest, skip_existing=True, input_dir=dest,
        )
    except Exception as e:
        print(f"  ⚠ external QC report failed: {e}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Radiomics + Clinical Pipeline")
    parser.add_argument(
        "--input", type=str, default=None,
        help="Excel 数据文件路径（默认 settings.ini INPUT）",
    )
    parser.add_argument(
        "--outcome", type=str, default=None,
        help="结局列名；逗号分隔多个；仅当 config.OUTCOME_COLS 存在时使用",
    )
    parser.add_argument(
        "--log", type=str, default="ref/pipeline_run.log",
        help="运行日志路径（UTF-8 BOM，默认工程 ref/）。空字符串禁用写文件",
    )
    parser.add_argument(
        "--no-log", action="store_true",
        help="不写日志文件（仅控制台）",
    )
    parser.add_argument(
        "--no-overlay", action="store_true",
        help="忽略 console.html 覆盖配置，只用 settings.py",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只打印将执行的步骤与写出路径，不拟合、不写结果",
    )
    args = parser.parse_args(argv)
    if not args.no_overlay:
        _apply_console_overlay()

    if args.dry_run:
        from modules.utils import dry_run as _dry
        _dry.set_dry_run(True)
        cfg.DRY_RUN = True
        print("  ◆ pipeline --dry-run enabled", flush=True)
        if args.input:
            cfg.INPUT = args.input
        xlsx = args.input or getattr(cfg, "INPUT", "")
        outcomes = _resolve_outcomes(args.outcome)
        _dry.plan("pipeline", [
            f"cwd={os.getcwd()}",
            f"input={xlsx}",
            f"outcomes={outcomes or ['(default Group)']}",
            "load → table1 → radiomics/clinical → val → report (not executed)",
        ])
        return

    log_f = None
    if not args.no_log and args.log:
        try:
            log_f = _attach_run_log(args.log)
        except Exception as e:
            _configure_stdio_utf8()
            print(f"  ⚠ 无法打开日志 {args.log!r}: {e}", flush=True)
    else:
        _configure_stdio_utf8()

    code = 0
    try:
        # 质控报告与结局无关，只写一次
        if args.input:
            cfg.INPUT = args.input
        _maybe_write_qc_report(args.input)

        outcomes = _resolve_outcomes(args.outcome)

        def _run_pairs(pairs: list[tuple[str, str]], title: str) -> None:
            print("=" * 70)
            print(f"  {title}  |  n_pairs={len(pairs)}")
            print("=" * 70)
            for i, (pos, neg) in enumerate(pairs, 1):
                print(f"\n>>> [{i}/{len(pairs)}] {pos} vs {neg}")
                if hasattr(cfg, "set_pairwise"):
                    cfg.set_pairwise(pos, neg)
                else:
                    from modules.config.outcome import set_pairwise
                    set_pairwise(pos, neg)
                run_pipeline(input_file=args.input)
            print("\n" + "=" * 70)
            print("  ALL PAIRS COMPLETE")
            print("=" * 70)

        if outcomes is None:
            if _subgroup_eval_mode():
                run_pipeline(input_file=args.input)
                return
            pairs = _parse_pairwise(getattr(cfg, "PAIRWISE_GROUPS", None)) or _resolve_pairwise()
            if pairs:
                fallback = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip() or "Group"
                if hasattr(cfg, "set_outcome"):
                    cfg.set_outcome(fallback)
                _run_pairs(pairs, "PAIRWISE PIPELINE")
            else:
                run_pipeline(input_file=args.input)
            return

        if not hasattr(cfg, "set_outcome"):
            raise RuntimeError(
                "config.OUTCOME_COLS 已设置但缺少 set_outcome()；请检查 settings.py"
            )
        print("=" * 70)
        print(f"  MULTI-OUTCOME PIPELINE  |  n_outcomes={len(outcomes)}")
        print("=" * 70)
        for i, outcome in enumerate(outcomes, 1):
            print(f"\n>>> [{i}/{len(outcomes)}] OUTCOME = {outcome}")
            cfg.set_outcome(outcome)
            if _subgroup_eval_mode():
                run_pipeline(input_file=args.input)
                continue
            pairs = _parse_pairwise(getattr(cfg, "PAIRWISE_GROUPS", None)) or _resolve_pairwise()
            if pairs:
                _run_pairs(pairs, f"PAIRWISE [{outcome}]")
            else:
                run_pipeline(input_file=args.input)
        print("\n" + "=" * 70)
        print("  ALL OUTCOMES COMPLETE")
        print("=" * 70)
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        # 恢复 stdio 并关闭日志
        if log_f is not None:
            try:
                print(f"  ◆ Log saved: {os.path.abspath(args.log)}", flush=True)
            except Exception as e:
                sys.__stderr__.write(f"  ⚠ log saved banner: {e}\n")
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            try:
                log_f.close()
            except Exception as e:
                sys.__stderr__.write(f"  ⚠ close pipeline log: {e}\n")
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    main()

