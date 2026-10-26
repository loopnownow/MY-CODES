"""
data_io.py — 数据加载与分层拆分
═══════════════════════════════════════════════════════════════════
功能：
    从 Excel 读取影像组学 (rad) 和临床 (clin) 工作表，统一 Group
    编码为 0/1，并按 TRAIN_RATIO 分层随机拆分训练/测试集。
    SPLIT_COL 非空时改用表内已有 training/test 划分，不再随机拆。
    支持同一文件多 sheet 外部验证集加载。
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations
import os
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional, List

import numpy as np
import pandas as pd

from modules import config as cfg

# 编码后 code → 原始类别标签（供 Table1 显示 male/female、Positive 等）
# 结构: {col_name: {0.0: "Negative", 1.0: "Positive", ...}}
CLIN_LEVEL_MAPS: Dict[str, Dict[float, str]] = {}

# 临床列统计类型：col → "continuous"（计量）| "categorical"（计数）
# 由 load 阶段登记。原始整列都是数字 → 计量；英文标签 / 罗马数字 token → 计数。
CLIN_VAR_TYPES: Dict[str, str] = {}

# 原始表并非整列纯数字（罗马数字、Ⅰ/Ⅱ 与阿拉伯混排等）→ 计数候选
CLIN_ORDINAL_TOKEN_COLS: set[str] = set()


@dataclass
class DataLayer:
    """单个数据层（all / train / test）的容器。"""
    rad: pd.DataFrame                       # 影像组学特征矩阵
    clin: pd.DataFrame                      # 临床特征矩阵（已剔除 SKIP_CLIN_FEATURES）
    group: pd.Series                        # 分组标签（字符串因子，如 "CKD"/"non-CKD"）
    label: np.ndarray                       # 二分类结局 (0/1) — int 数组
    sample_labels: Optional[List[str]] = None  # 原始样本标签（可用于图注/ID），可为 None
    time: Optional[np.ndarray] = None  # 生存/随访时间（不进临床模型）
    subgroup: Optional[pd.Series] = None  # 亚组列原文（评估主模型用，不进建模）
    landmark: Optional[dict] = None  # 时间列向量、状态列 0/1、两个列名。不是月份到标签。
    ipcw: Optional[dict] = None
    event: Optional[np.ndarray] = None  # KM 与 IPCW 共用的状态。空则 KM 用当前结局。


@dataclass
class SplitData:
    """三层数据容器：all / train / test。"""
    all: DataLayer
    train: DataLayer
    test: DataLayer
    # 选两组把 HC 留在建模队列外时的描述用帧。不进建模拆分。
    hc_train: Optional[pd.DataFrame] = None
    hc_test: Optional[pd.DataFrame] = None
    # 建模组原始水平已是 HC，但展示名被映走。这张 Train vs Test 表把该列改回 HC。
    hc_relabel_from: str = ""
    # GROUP_KEEP 前的全水平临床帧 + 原始组标签，只供总表。不进建模。
    baseline_clin: Optional[pd.DataFrame] = None
    baseline_group: Optional[pd.Series] = None
    # 同一套全水平，按建模拆分切到 Train / Test，只供 Table 1。不进建模。
    baseline_train_clin: Optional[pd.DataFrame] = None
    baseline_train_group: Optional[pd.Series] = None
    baseline_test_clin: Optional[pd.DataFrame] = None
    baseline_test_group: Optional[pd.Series] = None


def _find_group_column(columns: List[str], keyword: Optional[str] = None) -> str:
    """在列名中按关键字（不区分大小写）查找 Group 列，返回原始列名。"""
    if keyword is None:
        keyword = str(getattr(cfg, "GROUP_COL_KEYWORD", "Group") or "Group")
    hits = [c for c in columns if keyword.lower() in str(c).lower()]
    if len(hits) == 0:
        raise ValueError(f"Group 列未在 CLIN_SHEET 中找到（关键词: {keyword}）")
    if len(hits) > 1:
        print(f"  ⚠ 在 CLIN_SHEET 中找到多个候选 Group 列；使用第一个: {hits[0]}")
    return hits[0]


def _group_source_col(columns: List[str]) -> Optional[str]:
    """当前子结局列优先；没有才回退到已有 Group / 关键词列。"""
    cols = list(columns)
    gsrc = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    if gsrc and gsrc in cols:
        return gsrc
    if "Group" in cols:
        return "Group"
    keyword = str(getattr(cfg, "GROUP_COL_KEYWORD", "Group") or "Group")
    hits = [c for c in cols if keyword.lower() in str(c).lower()]
    return hits[0] if hits else None


def _assign_group_from_source(clin: pd.DataFrame) -> pd.DataFrame:
    """把当前结局列写入 Group。已有公共 Group 列时仍覆盖，避免串结局。"""
    src = _group_source_col(list(clin.columns))
    if not src:
        return clin
    if src == "Group":
        return clin
    out = clin.copy()
    out["Group"] = out[src]
    print(f"  Group ← 结局列 {src!r}")
    return out


# Excel 中允许的英文分类取值 → 分析用数值（与 heatmap 约定一致）
_ENGLISH_CAT_MAP = {
    "male": 1,
    "female": 0,
    "m": 1,
    "f": 0,
    "yes": 1,
    "no": 0,
    "y": 1,
    "n": 0,
    "true": 1,
    "false": 0,
    "positive": 1,
    "negative": 0,
    # 常见错拼 / 数据表笔误
    "negstive": 0,
    "positve": 1,
    "unclear": 1,
    "clear": 0,
    "mixed": 1,
    "fat-like": 0,
    "fatlike": 0,
    "dedifferentiated": 1,
    "well-differentiated": 0,
    "well differentiated": 0,
    # PCSR / ring integrity
    "complete": 1,
    "interrupted": 0,
    "intact": 1,
    "disrupted": 0,
    # Lung subtype (rough solid-component ordinal; override via factor if needed)
    "homogeneous nonsolid": 0,
    "heterogeneous nonsolid": 1,
    # 数据表常见拼写（Negstive）
    "homogeneous negstivensolid": 0,
    "heterogeneous negstivensolid": 1,
    "part-solid": 2,
    "part solid": 2,
    "solid": 3,
    # FIGO simplified stage (ordinal)
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    # Unicode roman numerals sometimes seen in exports
    "ⅰ": 1,
    "ⅱ": 2,
    "ⅲ": 3,
    "ⅳ": 4,
}

# 有序分级：罗马数字 / 半角罗马 → 整数（Fazekas、MTA 等）
_ORDINAL_TOKEN_MAP = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "ⅰ": 1, "ⅱ": 2, "ⅲ": 3, "ⅳ": 4, "ⅴ": 5,
}


def _norm_cat_key(v) -> str:
    """分类标签规范化 key（小写、压缩空白）。"""
    s = str(v).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _lookup_english_cat(v) -> float:
    """查英文分类映射；未命中返回 nan。"""
    if pd.isna(v):
        return np.nan
    key = _norm_cat_key(v)
    if key in _ENGLISH_CAT_MAP:
        return float(_ENGLISH_CAT_MAP[key])
    # 去连字符再试（fat-like 等已在表中；Part-Solid → part solid）
    key2 = key.replace("-", " ")
    if key2 in _ENGLISH_CAT_MAP:
        return float(_ENGLISH_CAT_MAP[key2])
    return np.nan


def _ordinal_max_levels() -> int:
    raw = getattr(cfg, "ORDINAL_MAX_LEVELS", 5)
    if raw is None or raw == "":
        return 5
    n = int(raw)
    if n < 1:
        raise ValueError(f"ORDINAL_MAX_LEVELS 须为正整数，当前={n}")
    return n


def _force_continuous_cols() -> set:
    return {str(x) for x in (getattr(cfg, "CLIN_FORCE_CONTINUOUS", None) or [])}


def _force_categorical_cols() -> set:
    return {str(x) for x in (getattr(cfg, "CLIN_FORCE_CATEGORICAL", None) or [])}


def _try_ordinal_token(v):
    """将 '2' / 'Ⅱ' / 'ii' 等转为 int；失败返回 None。"""
    if pd.isna(v):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)) and np.isfinite(v):
        fv = float(v)
        if abs(fv - round(fv)) < 1e-9:
            return int(round(fv))
        return None
    key = _norm_cat_key(v)
    if key in _ORDINAL_TOKEN_MAP:
        return int(_ORDINAL_TOKEN_MAP[key])
    # 纯数字字符串
    try:
        fv = float(key)
        if abs(fv - round(fv)) < 1e-9:
            return int(round(fv))
    except (TypeError, ValueError):
        pass
    return None


def _coerce_numeric_strings(clin: pd.DataFrame) -> pd.DataFrame:
    """
    object 列规范化：
      1) 非空值全部可 to_numeric → float（原始就是数字，计量）
      2) 否则若全部为有序 token（罗马数字 / ⅠⅡ 混排）→ float 整数码（计数）
    含英文分类标签的列保持字符串，供计数编码。
    """
    global CLIN_ORDINAL_TOKEN_COLS, CLIN_LEVEL_MAPS
    CLIN_ORDINAL_TOKEN_COLS = set()
    out = clin.copy()
    coerced = []
    ordinalized = []
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_numeric_dtype(s):
            continue
        if s.dropna().empty:
            continue
        num = pd.to_numeric(s, errors="coerce")
        ok = s.isna() | num.notna()
        if bool(ok.all()) and bool(num.notna().any()):
            out[col] = num.astype(float)
            coerced.append(col)
            continue
        # 罗马数字 / 混排 0,'Ⅰ','Ⅱ'
        tokens = []
        good = True
        for v in s:
            if pd.isna(v):
                tokens.append(np.nan)
                continue
            t = _try_ordinal_token(v)
            if t is None:
                good = False
                break
            tokens.append(float(t))
        if good and any(pd.notna(x) for x in tokens):
            out[col] = pd.Series(tokens, index=s.index, dtype=float)
            ordinalized.append(col)
            CLIN_ORDINAL_TOKEN_COLS.add(str(col))
            # 建模用整数码；Table1 / 列线图刻度保留原文（I/II/Ⅲ…，不是 1/2/3）
            level_map: Dict[float, str] = {}
            for raw, code in zip(s, tokens):
                if pd.isna(code):
                    continue
                fv = float(code)
                if fv in level_map:
                    continue
                if pd.isna(raw):
                    continue
                level_map[fv] = str(raw).strip()
            if level_map:
                CLIN_LEVEL_MAPS[str(col)] = level_map
    if coerced:
        print(f"  Coerced numeric strings → float (计量候选): {', '.join(coerced)}")
    if ordinalized:
        print(f"  Coerced ordinal tokens (0/1/Ⅱ…) → int codes (计数候选): {', '.join(ordinalized)}")
    return out



def classify_clin_series(
    col: str,
    series: pd.Series,
    level_maps: Optional[Dict[str, Dict[float, str]]] = None,
) -> str:
    """
    判定单列计量/计数。适用于全部子工程。
    - CLIN_FORCE_CONTINUOUS → continuous（覆盖默认）
    - CLIN_FORCE_CATEGORICAL → categorical（覆盖默认；手写逗号列名）
    - 默认：非空水平数 ≤ ORDINAL_MAX_LEVELS（默认 5）→ categorical（计数）；
      > 阈值 → continuous（计量）
    - 非数值列按水平数同样分级（文本多水平仍为计数）
    """
    level_maps = level_maps if level_maps is not None else CLIN_LEVEL_MAPS
    if col in _force_continuous_cols():
        return "continuous"
    if col in _force_categorical_cols():
        return "categorical"
    s = series.dropna()
    if s.empty:
        if col in (level_maps or {}) or col in CLIN_ORDINAL_TOKEN_COLS:
            return "categorical"
        return "continuous" if pd.api.types.is_numeric_dtype(series) else "categorical"
    n_unique = int(s.nunique())
    if n_unique <= _ordinal_max_levels():
        return "categorical"
    if not pd.api.types.is_numeric_dtype(series):
        # 文本且水平很多：仍按计数（Table1 展开水平）；不因阈值改成计量
        return "categorical"
    return "continuous"


def _register_ordinal_level_maps(clin: pd.DataFrame) -> None:
    """为判定为计数的有序整数列补充 CLIN_LEVEL_MAPS（标签=数字字符串）。"""
    global CLIN_LEVEL_MAPS
    for col, kind in CLIN_VAR_TYPES.items():
        if kind != "categorical" or col not in clin.columns:
            continue
        if col in CLIN_LEVEL_MAPS:
            continue
        s = clin[col]
        if not pd.api.types.is_numeric_dtype(s):
            continue
        level_map: Dict[float, str] = {}
        for v in sorted(s.dropna().unique(), key=lambda z: float(z)):
            fv = float(v)
            if abs(fv - round(fv)) < 1e-9:
                level_map[fv] = str(int(round(fv)))
            else:
                level_map[fv] = str(v)
        if level_map:
            CLIN_LEVEL_MAPS[col] = level_map


def _classify_clin_columns(clin: pd.DataFrame) -> Dict[str, str]:
    """填充 CLIN_VAR_TYPES 并打印汇总。"""
    global CLIN_VAR_TYPES
    types: Dict[str, str] = {}
    for col in clin.columns:
        types[str(col)] = classify_clin_series(str(col), clin[col])
    CLIN_VAR_TYPES = types
    _register_ordinal_level_maps(clin)

    cont = [c for c, t in types.items() if t == "continuous"]
    cat = [c for c, t in types.items() if t == "categorical"]
    fc = sorted(_force_continuous_cols() & set(types))
    fk = sorted(_force_categorical_cols() & set(types))
    print(f"  CLIN var types: continuous(计量)={len(cont)}  categorical(计数)={len(cat)}")
    if cont:
        print(f"    continuous: {', '.join(cont)}")
    if cat:
        print(f"    categorical: {', '.join(cat)}")
    if fc:
        print(f"    CLIN_FORCE_CONTINUOUS applied: {', '.join(fc)}")
    if fk:
        print(f"    CLIN_FORCE_CATEGORICAL applied: {', '.join(fk)}")
    if CLIN_ORDINAL_TOKEN_COLS:
        print(f"    ordinal-token (计数): {', '.join(sorted(CLIN_ORDINAL_TOKEN_COLS))}")
    return types


def _encode_english_categoricals(clin: pd.DataFrame) -> pd.DataFrame:
    """
    临床列类型约定（全部子工程）：
      · 原始整列都是数字（含可 parse 的文本数字、少水平整数）→ 计量
      · 英文字符串 → 计数（映射为数值供建模，CLIN_LEVEL_MAPS 保留原文）
      · 罗马数字等有序 token → 计数
    字典无法完整映射的多水平文本：警告 + factor 编码，不中断。
    结束时写入 CLIN_VAR_TYPES。
    """
    global CLIN_LEVEL_MAPS
    out = _coerce_numeric_strings(clin)
    encoded = []
    factor_encoded = []
    warn_cols = []
    for col in out.columns:
        s = out[col]
        if pd.api.types.is_numeric_dtype(s):
            continue
        # 原文标签（保留首次出现的展示形式）
        raw_labels = {}
        for v in s.dropna():
            key = _norm_cat_key(v)
            if key not in raw_labels:
                raw_labels[key] = str(v).strip()

        mapped = s.map(_lookup_english_cat)
        # 仅当非空值全部成功映射时才替换
        ok = s.isna() | mapped.notna()
        if ok.all() and mapped.notna().any():
            out[col] = mapped.astype("float")
            level_map: Dict[float, str] = {}
            for key, disp in raw_labels.items():
                code = _lookup_english_cat(key)
                if pd.notna(code) and float(code) not in level_map:
                    level_map[float(code)] = disp
            CLIN_LEVEL_MAPS[col] = level_map
            encoded.append(col)
            continue
        # 多水平/未映射文本：稳定 factor 编码，避免 GLM 时 to_numeric 全变 NaN
        n_unique = s.dropna().nunique()
        unmapped = sorted({
            str(v).strip()
            for v in s.dropna()
            if pd.isna(_lookup_english_cat(v))
        })[:12]
        if n_unique >= 2 and n_unique <= 20:
            cat = pd.Categorical(s.astype(str).where(s.notna(), other=np.nan))
            codes = cat.codes.astype(float)
            codes[codes < 0] = np.nan
            out[col] = codes
            level_map = {
                float(i): str(lab)
                for i, lab in enumerate(cat.categories)
            }
            CLIN_LEVEL_MAPS[col] = level_map
            factor_encoded.append(col)
            warn_cols.append((col, unmapped))
        elif n_unique >= 1:
            warn_cols.append((col, unmapped))
            print(
                f"  ⚠ categorical column {col!r} not encoded "
                f"(n_unique={n_unique}); sample={unmapped}"
            )
    if encoded:
        print(f"  Encoded English categoricals → numeric: {', '.join(encoded)}")
    if factor_encoded:
        print(f"  Factor-encoded multi-level categoricals → numeric: {', '.join(factor_encoded)}")
    for col, samples in warn_cols:
        if col in factor_encoded:
            print(
                f"  ⚠ non-dictionary categorical labels in {col!r} "
                f"(factor-coded, not aborted): {samples}"
            )
    if CLIN_LEVEL_MAPS:
        print(f"  CLIN_LEVEL_MAPS: {len(CLIN_LEVEL_MAPS)} columns for Table1 labels")
    _classify_clin_columns(out)
    return out


def _norm_patient_id(x: str) -> str:
    """归一化患者 ID，便于 gfy-/yfy- 前缀、_1 后缀等变体对齐。"""
    s = str(x).strip().lower()
    s = re.sub(r"^(gfy-|yfy-)", "", s)
    s = re.sub(r"_\d+$", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s

def _patient_key_for_exclude(label: str) -> str:
    """去掉尾部结节序号（'cao xiao hong  1' → 'cao xiao hong'），便于按人排除。"""
    s = str(label).strip().lower()
    s = re.sub(r"\s+\d+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if s else str(label).strip().lower()


def _id_listed_keys(raw) -> set:
    """排除名单上的这一条 ID。保留结节序号，不把同人的其他结节算进去。"""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return set()
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return set()
    collapsed = re.sub(r"\s+", " ", s.lower()).strip()
    keys = {s, s.lower(), collapsed, _norm_patient_id(s)}
    return {k for k in keys if k}


_TRAIN_SPLIT_TOKENS = {
    "training", "train", "development", "derivation",
    "训练", "训练集",
}
_TEST_SPLIT_TOKENS = {
    "test", "testing", "validation", "val", "valid", "holdout",
    "验证", "验证集", "测试", "测试集",
}


def _split_role(raw) -> str:
    s = str(raw).strip().lower()
    if not s or s in ("nan", "none", "nat"):
        return ""
    if s in _TRAIN_SPLIT_TOKENS:
        return "train"
    if s in _TEST_SPLIT_TOKENS:
        return "test"
    return ""


def _indices_from_split_col(clin: pd.DataFrame, keys: list, n_total: int):
    """Use a pre-assigned train/test column. Patient-level: no mixed roles."""
    col = _cfg_col("SPLIT_COL")
    if not col:
        return None
    if col not in clin.columns:
        raise ValueError(f"SPLIT_COL={col!r} 不在临床表")
    if len(clin) != n_total:
        raise ValueError(f"SPLIT_COL 行数 {len(clin)} ≠ {n_total}")
    roles = [_split_role(v) for v in clin[col].tolist()]
    bad = sorted({str(clin[col].iloc[i]).strip() for i, r in enumerate(roles) if not r})
    if bad:
        raise ValueError(
            f"SPLIT_COL={col!r} 无法识别: {bad[:12]}  "
            f"（训练=training/train，验证=test/validation）"
        )
    key_roles: dict = {}
    for i, k in enumerate(keys):
        key_roles.setdefault(k, set()).add(roles[i])
    leak = {k: sorted(v) for k, v in key_roles.items() if len(v) > 1}
    if leak:
        raise RuntimeError(f"Patient-level split leakage in {col}: {len(leak)} keys")
    train_idx = np.array([i for i, r in enumerate(roles) if r == "train"], dtype=int)
    test_idx = np.array([i for i, r in enumerate(roles) if r == "test"], dtype=int)
    if len(train_idx) == 0:
        raise ValueError(f"SPLIT_COL={col!r} 没有 training 行")
    if len(test_idx) == 0:
        print(f"  ⚠ SPLIT_COL={col!r}: 没有 test 行，主表全部作为 TRAIN")
    print(
        f"  SPLIT_COL={col!r}: patients train={sum(1 for v in key_roles.values() if 'train' in v)}, "
        f"test={sum(1 for v in key_roles.values() if 'test' in v)}; "
        f"rows train={len(train_idx)}, test={len(test_idx)} (locked, no random split)"
    )
    return train_idx, test_idx


def _id_match_keys(raw) -> set:
    """一个 ID 展开为可匹配 key 集合（原文 / 小写 / 归一化 / 去结节序号）。"""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return set()
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return set()
    keys = {
        s,
        s.lower(),
        re.sub(r"\s+", " ", s.lower()).strip(),
        _norm_patient_id(s),
        _patient_key_for_exclude(s),
        _norm_patient_id(_patient_key_for_exclude(s)),
    }
    return {k for k in keys if k}


# 展示姓名列（≠ CLIN_ID_COL）。ID 用来对齐/排除/按人划分；本列只用于错分表/图注。
_LABEL_DISPLAY_CANDS = (
    "patient_name", "PatientName", "Chinese name", "Chinese_name",
    "ChineseName", "姓名",
)


def _cfg_col(name: str, default: str = "") -> str:
    return str(getattr(cfg, name, default) or default).strip()


def resolve_label_col(
    columns,
    clin_id_col: Optional[str] = None,
    configured: Optional[str] = None,
) -> str:
    """
    解析展示用 LABEL_COL。
    CLIN_ID_COL 与 LABEL_COL 职责不同：前者对组学 ID，后者是姓名/标签。
    配置写成同一列、但表里另有姓名列时，改用姓名列。
    """
    if columns is None:
        cols = []
    else:
        cols = [str(c) for c in list(columns)]
    colset = set(cols)
    cid = str(
        clin_id_col if clin_id_col is not None
        else _cfg_col("CLIN_ID_COL", "PatientID")
    ).strip()
    cur = str(
        configured if configured is not None
        else _cfg_col("LABEL_COL", "")
    ).strip()
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


def _pick_label_series(
    df: pd.DataFrame,
    rad: Optional[pd.DataFrame] = None,
) -> Optional[pd.Series]:
    """取展示姓名列；与 CLIN_ID_COL 可以同列，也可以不同。"""
    col = resolve_label_col(df.columns)
    if col and col in df.columns:
        return df[col]
    if "Name" in df.columns:
        return df["Name"]
    if rad is not None and "Name" in rad.columns:
        return rad["Name"]
    return None


def _row_excluded_mask(
    df: pd.DataFrame,
    exclude_keys: frozenset,
    preferred: List[str],
) -> Optional[pd.Series]:
    """任一对齐/展示列命中排除名单即剔除（ID 与姓名都可写进 exc）。"""
    cols: List[str] = []
    for c in preferred:
        if c and c in df.columns and c not in cols:
            cols.append(c)
    if not cols:
        s = _pick_id_series(df)
        if s is None:
            return None
        return s.map(lambda x: _row_id_is_excluded(x, exclude_keys))
    mask = pd.Series(False, index=df.index)
    for c in cols:
        mask = mask | df[c].map(lambda x: _row_id_is_excluded(x, exclude_keys))
    return mask


def _skip_nonfeature_cols(df: pd.DataFrame) -> List[str]:
    """SKIP 列表 + 临床 ID + 展示姓名，避免进模型。"""
    skip = [str(c) for c in (getattr(cfg, "SKIP_CLIN_FEATURES", None) or [])]
    extra = [
        _cfg_col("CLIN_ID_COL", "PatientID"),
        resolve_label_col(df.columns),
        _cfg_col("SPLIT_COL"),
        "Patient_ID", "patient_id",
        "Name", "name", "Group",
    ]
    out: List[str] = []
    seen = set()
    for c in skip + extra:
        if not c or c in seen:
            continue
        seen.add(c)
        if c in df.columns:
            out.append(c)
    return out


# 排除列表缓存：path::sheet::col → frozenset of match keys
_EXCLUDE_CACHE: Dict[str, frozenset] = {}


def _cfg_get(settings: Optional[dict], key: str, default=""):
    if settings is not None and key in settings:
        return settings.get(key, default)
    return getattr(cfg, key, default)


def resolve_subproject_root(settings: Optional[dict] = None) -> str:
    """当前结局的子工程根（相对工程根）。"""
    from modules.config.outcome import subproject_root
    outcome = str(_cfg_get(settings, "GROUP_SOURCE_COL") or "").strip()
    if not outcome:
        cols = _cfg_get(settings, "OUTCOME_COLS") or []
        if isinstance(cols, (list, tuple)) and cols:
            outcome = str(cols[0] or "").strip()
    mapping = _cfg_get(settings, "SUBPROJECT_BY_OUTCOME") or {}
    use_subdir = bool(_cfg_get(settings, "OUTCOME_OUT_SUBDIR", True))
    out_dir = str(_cfg_get(settings, "OUT_DIR") or "").strip()
    return subproject_root(outcome, out_dir, mapping, use_subdir)


def default_exclude_xlsx(settings: Optional[dict] = None) -> str:
    """子工程根下 false_classification.xlsx；无子工程则工程根。"""
    name = "false_classification.xlsx"
    root = resolve_subproject_root(settings)
    return os.path.join(root, name) if root else name


def exclude_file_names(settings: Optional[dict] = None) -> list[str]:
    """已填的排除名单。一个文件是单元素列表；没填则空列表（调用方再落默认文件）。"""
    from modules.config.ini_io import name_choice
    packed = name_choice(_cfg_get(settings, "EXCLUDE_FILE", ""))
    if isinstance(packed, list):
        return [str(x) for x in packed if str(x).strip()]
    return [packed] if packed else []


def resolve_exclude_files(settings: Optional[dict] = None) -> list[str]:
    """排除名单路径。填了就用所填的全部；一个都没填 → 子工程根 false_classification.xlsx。"""
    names = exclude_file_names(settings)
    if names:
        return names
    return [default_exclude_xlsx(settings)]


def resolve_exclude_xlsx(settings: Optional[dict] = None) -> str:
    """
    排除名单 xlsx（第一个）。
    EXCLUDE_FILE 非空 → 用该路径；空 → 子工程根 false_classification.xlsx。
    多个文件时其余文件由 resolve_exclude_files 读取，这里仍返回第一个。
    """
    return resolve_exclude_files(settings)[0]


def _resolve_exclude_file(data_file: Optional[str] = None) -> str:
    """排除表路径：EXCLUDE_FILE > 子工程根 false_classification.xlsx。"""
    _ = data_file
    return resolve_exclude_xlsx()


def blank_exc_dataframe(
    sheet_col: Optional[str] = None,
) -> pd.DataFrame:
    """空白排除表：仅一列表头（默认列名 exc）。"""
    col = str(sheet_col or getattr(cfg, "EXCLUDE_COL", "exc") or "exc")
    return pd.DataFrame(columns=[col])



def ensure_blank_exc_sheet(
    xlsx_path: str,
    sheet_name: Optional[str] = None,
    col_name: Optional[str] = None,
    *,
    create_if_missing_file: bool = False,
) -> bool:
    """
    确保 xlsx 含空白/可用的排除工作表：
      - 无该 sheet → 追加仅含列名的空表
      - 有 sheet 但无列 → 写回仅含列名
      - 已有数据列 → 不动（不覆盖用户已填的排除 ID）
    返回 True 表示对文件做了写入。
    """
    sheet = str(sheet_name or getattr(cfg, "EXCLUDE_SHEET", "exc") or "exc")
    col = str(col_name or getattr(cfg, "EXCLUDE_COL", "exc") or "exc")
    path = str(xlsx_path).strip()
    if not path:
        return False

    if not os.path.isfile(path):
        if not create_if_missing_file:
            return False
        with pd.ExcelWriter(path, engine="openpyxl") as xw:
            blank_exc_dataframe(col).to_excel(xw, sheet_name=sheet, index=False)
        print(f"  Created new workbook with blank sheet {sheet!r}.{col}: {path}")
        # 清缓存，避免读到旧空集
        for k in list(_EXCLUDE_CACHE.keys()):
            if path in k or os.path.abspath(path) in k:
                _EXCLUDE_CACHE.pop(k, None)
        return True

    try:
        from openpyxl import load_workbook
    except ImportError:
        print("  ⚠ openpyxl missing — cannot ensure exc sheet")
        return False

    try:
        wb = load_workbook(path)
        names_lower = {str(s).lower(): s for s in wb.sheetnames}
        hit = sheet if sheet in wb.sheetnames else names_lower.get(sheet.lower())

        if hit is None:
            ws = wb.create_sheet(title=sheet)
            ws.cell(1, 1, value=col)
            wb.save(path)
            print(f"  ◆ Added blank exclude sheet {sheet!r} (col={col!r}) → {path}")
            for k in list(_EXCLUDE_CACHE.keys()):
                if path in k or os.path.abspath(path) in k:
                    _EXCLUDE_CACHE.pop(k, None)
            return True

        ws = wb[hit]
        # 已有表头或任意单元格内容则保留
        first = ws.cell(1, 1).value
        if first is None or str(first).strip() == "":
            ws.cell(1, 1, value=col)
            wb.save(path)
            print(f"  ◆ Filled header {col!r} on sheet {hit!r} → {path}")
            for k in list(_EXCLUDE_CACHE.keys()):
                if path in k or os.path.abspath(path) in k:
                    _EXCLUDE_CACHE.pop(k, None)
            return True
        # 已有内容：不改
        return False
    except Exception as e:
        print(f"  ⚠ ensure_blank_exc_sheet failed ({path}): {e}")
        return False


def _cell_text(raw) -> str:
    """单元格 → 去空白字符串；空 / NaN 返回空。"""
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return ""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    return s


def normalize_exclude_rows(raw) -> List[str]:
    """EXCLUDE_ROW → 要匹配的排除内容。空 = 该列整列当 ID。"""
    if raw is None or raw is False:
        return []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        seen: set = set()
        for x in raw:
            s = _cell_text(x)
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out
    s = _cell_text(raw)
    return [s] if s else []


def _exclude_id_col_name(
    df: pd.DataFrame,
    exclude_col: str,
    settings: Optional[dict] = None,
) -> Optional[str]:
    """取值匹配模式下，从哪一列取被剔除的 ID。"""
    preferred = [
        _cfg_get(settings, "CLIN_ID_COL", ""),
        _cfg_get(settings, "RAD_ID_COL", ""),
        "Patient_ID", "patient_id", "PatientID", "Name", "ID",
    ]
    seen: set = set()
    for c in preferred:
        s = str(c or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if s in df.columns:
            return s
    for c in df.columns:
        s = str(c)
        if s != str(exclude_col):
            return s
    return str(exclude_col) if exclude_col in df.columns else (
        str(df.columns[0]) if len(df.columns) else None
    )


def _keys_from_exclude_frame(
    df: pd.DataFrame,
    use_col: str,
    row_vals: List[str],
    settings: Optional[dict] = None,
) -> tuple:
    """
    从排除表抽出匹配 key。
    row_vals 非空：EXCLUDE_COL 等于这些内容的行，取其 ID 列，只剔除这些 ID。
    不按去掉结节序号后的人名扩大（'cao xiao hong  2' 不连带 'cao xiao hong  1'）。
    row_vals 空：EXCLUDE_COL 整列当 ID（旧 exc 名单），仍可按人匹配。
    返回 (keys, n_raw, id_col_used)。
    """
    excel_keys: set = set()
    if df is None or df.empty or use_col not in df.columns:
        return excel_keys, 0, ""
    if row_vals:
        want = {str(x) for x in row_vals}
        mask = df[use_col].map(_cell_text).isin(want)
        id_col = _exclude_id_col_name(df, str(use_col), settings)
        if not id_col or id_col not in df.columns:
            return excel_keys, 0, ""
        n_raw = 0
        for v in df.loc[mask, id_col].tolist():
            ks = _id_listed_keys(v)
            if ks:
                n_raw += 1
                excel_keys |= ks
        return excel_keys, n_raw, str(id_col)
    n_raw = 0
    for v in df[use_col].tolist():
        ks = _id_match_keys(v)
        if ks:
            n_raw += 1
            excel_keys |= ks
    return excel_keys, n_raw, str(use_col)


def _keys_from_one_exclude_file(
    xfile: str,
    sheet: str,
    col: str,
    row_vals: List[str],
    settings: Optional[dict],
) -> set:
    """一个排除名单里的匹配 key。文件或 sheet 不在时返回空集并写日志。"""
    if not os.path.isfile(xfile):
        print(f"  Exclude list: file not found {xfile!r}")
        return set()
    try:
        xl = pd.ExcelFile(xfile)
        names = [str(s) for s in xl.sheet_names]
        hit = sheet if sheet in names else next(
            (s for s in names if s.lower() == sheet.lower()), None
        )
        if hit is None:
            print(
                f"  Exclude list: sheet {sheet!r} not in {xfile!r} "
                f"(sheets={names}) — no Excel exclusions"
            )
            return set()
        df = pd.read_excel(xl, sheet_name=hit)
        if df is None or df.empty:
            print(
                f"  Exclude list: sheet {hit!r} is blank "
                f"(header only) — no Excel exclusions"
            )
            return set()
        use_col = col if col in df.columns else (
            df.columns[0] if len(df.columns) else None
        )
        if use_col is None:
            print(f"  Exclude list: sheet {hit!r} has no columns")
            return set()
        if str(use_col) != col:
            print(
                f"  Exclude list: column {col!r} missing, "
                f"use first column {use_col!r}"
            )
        excel_keys, n_raw, id_used = _keys_from_exclude_frame(
            df, str(use_col), row_vals, settings
        )
        if row_vals:
            print(
                f"  Exclude list: {xfile} [{hit}].{use_col}"
                f" == {row_vals} → {n_raw} id(s) from {id_used}"
            )
        else:
            print(
                f"  Exclude list: {xfile} [{hit}].{use_col} → "
                f"{n_raw} id(s) loaded"
            )
        return set(excel_keys)
    except Exception as e:
        print(f"  ⚠ Exclude list read failed ({xfile}): {e}")
        return set()


def load_exclude_id_keys(
    data_file: Optional[str] = None,
    settings: Optional[dict] = None,
) -> frozenset:
    """
    读取排除 ID 集合（匹配用 key）。
    来源：EXCLUDE_IDS + 排除 xlsx（可多个；默认子工程根 false_classification.xlsx）
    的 EXCLUDE_SHEET / EXCLUDE_COL（默认 exc / exc）。同一 sheet、列、内容套到每个文件，名单合并。
    EXCLUDE_ROW 非空：只剔除该列内容等于所选值的行（取其 ID 列）。
    无文件 / 无 sheet / 关闭开关 → 空集。
    """
    if not bool(_cfg_get(settings, "DO_EXCLUDE_SAMPLES", True)):
        return frozenset()

    if settings is not None:
        files = resolve_exclude_files(settings)
    else:
        files = resolve_exclude_files()
    _ = data_file
    sheet = str(_cfg_get(settings, "EXCLUDE_SHEET", "exc") or "exc")
    col = str(_cfg_get(settings, "EXCLUDE_COL", "exc") or "exc")
    row_vals = normalize_exclude_rows(_cfg_get(settings, "EXCLUDE_ROW", ""))
    row_key = "|".join(row_vals)
    cache_key = "||".join(
        f"{os.path.abspath(xfile) if os.path.isfile(xfile) else xfile}"
        f"::{sheet}::{col}::{row_key}"
        for xfile in files
    )

    hard = list(_cfg_get(settings, "EXCLUDE_IDS", None) or [])
    hard_keys: set = set()
    for v in hard:
        hard_keys |= _id_match_keys(v)

    if cache_key in _EXCLUDE_CACHE:
        return frozenset(set(_EXCLUDE_CACHE[cache_key]) | hard_keys)

    excel_keys: set = set()
    found_any = False
    for xfile in files:
        if os.path.isfile(xfile):
            found_any = True
        excel_keys |= _keys_from_one_exclude_file(xfile, sheet, col, row_vals, settings)
    if not found_any and hard:
        print("  Exclude list: using EXCLUDE_IDS only")
    elif found_any and len(files) > 1:
        print(f"  Exclude list: {len(files)} files merged → match-keys={len(excel_keys | hard_keys)}")

    merged = frozenset(excel_keys | hard_keys)
    _EXCLUDE_CACHE[cache_key] = frozenset(excel_keys)
    if hard:
        print(f"  Exclude list: +{len(hard)} from EXCLUDE_IDS → total match-keys={len(merged)}")
    return frozenset(set(merged) | hard_keys)


def _row_id_is_excluded(raw_id, exclude_keys: frozenset) -> bool:
    if not exclude_keys:
        return False
    return bool(_id_match_keys(raw_id) & exclude_keys)


def _pick_id_series(df: pd.DataFrame, preferred: Optional[str] = None) -> Optional[pd.Series]:
    """从 rad/clin 表选出用于排除匹配的 ID 列。"""
    candidates = []
    if preferred:
        candidates.append(preferred)
    candidates.extend([
        "Name", "PatientID", "patient_id", "Patient_ID", "label", "ID", "id",
        getattr(cfg, "RAD_ID_COL", "patient_id"),
        getattr(cfg, "CLIN_ID_COL", "PatientID"),
        getattr(cfg, "LABEL_COL", "PatientID"),
    ])
    seen = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        if c in df.columns:
            return df[c]
    # 首列若像 ID（非纯特征）
    if len(df.columns) > 0:
        c0 = df.columns[0]
        if not str(c0).startswith("h") and "intensity" not in str(c0).lower():
            return df[c0]
    return None


def apply_sample_exclusion(
    rad: pd.DataFrame,
    clin: pd.DataFrame,
    data_file: Optional[str] = None,
    *,
    stage: str = "raw",
) -> tuple:
    """
    【最先执行】按排除列表剔除 rad/clin 行，再进入后续对齐与统计。
    两侧按各自 ID 列独立过滤；行序在排除后仍应对齐（排除后会校验行数）。
    """
    keys = load_exclude_id_keys(data_file)
    if not keys:
        return rad, clin

    rad_out = rad
    clin_out = clin
    n_rad0, n_clin0 = len(rad), len(clin)

    rad_pref = [
        _cfg_col("RAD_ID_COL", "patient_id"),
        "Name", "patient_id", "PatientID", "ID",
    ]
    clin_pref = [
        _cfg_col("CLIN_ID_COL", "PatientID"),
        resolve_label_col(clin.columns),
        "Name", "PatientID", "patient_id", "patient_name",
    ]

    dropped_rad = []
    dropped_clin = []

    mask_r = _row_excluded_mask(rad, keys, rad_pref)
    if mask_r is not None:
        rad_ids = _pick_id_series(rad, preferred=rad_pref[0])
        dropped_rad = rad_ids[mask_r].astype(str).tolist() if rad_ids is not None else []
        rad_out = rad.loc[~mask_r].reset_index(drop=True)
    else:
        print(f"  ⚠ Exclude [{stage}]: rad 无 ID 列，跳过 rad 过滤")

    mask_c = _row_excluded_mask(clin, keys, clin_pref)
    if mask_c is not None:
        clin_ids = _pick_id_series(clin, preferred=clin_pref[0])
        dropped_clin = clin_ids[mask_c].astype(str).tolist() if clin_ids is not None else []
        clin_out = clin.loc[~mask_c].reset_index(drop=True)
    else:
        print(f"  ⚠ Exclude [{stage}]: clin 无 ID 列，跳过 clin 过滤")

    n_drop = max(len(dropped_rad), len(dropped_clin))
    if n_drop > 0:
        show = (dropped_rad or dropped_clin)[:15]
        more = "..." if n_drop > 15 else ""
        print(
            f"  ◆ EXCLUDE first [{stage}]: drop rad={len(dropped_rad)} "
            f"clin={len(dropped_clin)}  "
            f"remain rad={len(rad_out)}/{n_rad0} clin={len(clin_out)}/{n_clin0}  "
            f"ids={show}{more}"
        )
    else:
        print(
            f"  Exclude [{stage}]: list loaded (keys={len(keys)}) but no row matched "
            f"(rad={n_rad0}, clin={n_clin0})"
        )
    return rad_out, clin_out


def _align_clin_to_rad(
    rad_ids: pd.Series,
    clin_df: pd.DataFrame,
    clin_id_col: str,
) -> pd.DataFrame:
    """
    将临床表按组学表 ID 顺序一对一对齐（以 rad 为准）。
    优先精确匹配，剩余样本再按归一化 ID 匹配。
    """
    clin = clin_df.copy().reset_index(drop=True)
    if clin_id_col not in clin.columns:
        raise ValueError(f"临床表缺少 ID 列: {clin_id_col}")
    clin["_align_id"] = clin[clin_id_col].astype(str).str.strip()
    remaining = clin.copy()
    aligned_rows = []
    fuzzy_pairs = []

    for rid in rad_ids.astype(str).str.strip().tolist():
        exact = remaining.index[remaining["_align_id"] == rid].tolist()
        if len(exact) >= 1:
            pick = exact[0]
            how = "exact"
        else:
            nrid = _norm_patient_id(rid)
            fuzzy = remaining.index[
                remaining["_align_id"].map(_norm_patient_id) == nrid
            ].tolist()
            if len(fuzzy) == 0:
                raise ValueError(f"组学 ID 在临床表中无匹配: {rid!r}")
            if len(fuzzy) > 1:
                print(f"  ⚠ 归一化后多条临床记录匹配 {rid!r}，取第一条: "
                      f"{remaining.loc[fuzzy, '_align_id'].tolist()}")
            pick = fuzzy[0]
            how = "fuzzy"
            fuzzy_pairs.append((rid, remaining.at[pick, "_align_id"]))

        aligned_rows.append(remaining.loc[pick])
        remaining = remaining.drop(index=pick)

    if len(remaining) > 0:
        leftover = remaining["_align_id"].tolist()
        print(f"  ⚠ 临床表未对齐样本 {len(leftover)}（已忽略，以组学为准）: "
              f"{leftover[:10]}{'...' if len(leftover) > 10 else ''}")

    out = pd.DataFrame(aligned_rows).reset_index(drop=True)
    out = out.drop(columns=["_align_id"], errors="ignore")
    if fuzzy_pairs:
        print(f"  ID 模糊对齐 {len(fuzzy_pairs)} 对: {fuzzy_pairs}")
    print(f"  对齐完成: rad N={len(rad_ids)} → clin N={len(out)}")
    return out


def _prefix_exclude_list(raw=None) -> list[str]:
    if raw is None:
        raw = getattr(cfg, "RAD_FEATURE_PREFIX_EXCLUDE", None)
    out: list[str] = []
    seen: set[str] = set()
    if raw is None:
        return out
    items = [raw] if isinstance(raw, str) else list(raw)
    for x in items:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _prefix_id_names(extra=None) -> set[str]:
    names = {"Name", "Patient_ID", "patient_id", "PatientID", "Group", "ID"}
    rid = str(getattr(cfg, "RAD_ID_COL", "") or "").strip()
    if rid:
        names.add(rid)
    for c in list(getattr(cfg, "RAD_ID_COLS", None) or []):
        s = str(c).strip()
        if s:
            names.add(s)
    for c in extra or []:
        s = str(c).strip()
        if s:
            names.add(s)
    return names


def _marker_anchor(token: str) -> tuple[str, str]:
    """保存下来的标记 → (端, 正文)。

    检出的后缀存成 suffix:_Vgm，只认列名结尾。
    没有这前缀的旧标记仍只认列名开头，不根据首字符猜测。
    """
    raw = str(token or "")
    mark = "suffix:"
    if raw.startswith(mark):
        return "end", raw[len(mark):]
    if raw.startswith("prefix:"):
        return "start", raw[len("prefix:"):]
    return "start", raw


def _column_matches_prefix(name: str, prefix: str) -> bool:
    """列名是否命中已选标记。筛选和分类共用。不在名称中间匹配。

    前缀只认列名开头。以 __ 结尾时同时覆盖少一个下划线的族
    （perfusion_weighted__ 也命中 perfusion_weighted_Frontal_L）。
    后缀（suffix: 开头的保存值）只认列名结尾。
    """
    s = str(name)
    anchor, part = _marker_anchor(prefix)
    if not part:
        return False
    if anchor == "end":
        return s.endswith(part)
    if s.startswith(part):
        return True
    if part.endswith("__"):
        broad = part[:-1]  # foo__ → foo_
        if broad and s.startswith(broad):
            return True
    return False


def _strip_anchored_token(name: str, token: str) -> str:
    """只剥已选标记所在的那一端，与 _column_matches_prefix 同一套规则。"""
    s = str(name)
    anchor, part = _marker_anchor(token)
    if not part:
        return s
    if anchor == "end":
        if s.endswith(part):
            return s[: -len(part)]
        return s
    if s.startswith(part):
        return s[len(part):]
    if part.endswith("__"):
        broad = part[:-1]
        if broad and s.startswith(broad):
            return s[len(broad):]
    return s


def drop_excluded_rad_columns(rad: pd.DataFrame, prefixes=None, id_cols=None) -> pd.DataFrame:
    """去掉选中标记的组学特征。ID 列保留。空名单不改表。"""
    prefs = _prefix_exclude_list(prefixes)
    if rad is None or not prefs:
        return rad
    keep_ids = _prefix_id_names(id_cols)
    drop = [
        c for c in rad.columns
        if str(c) not in keep_ids and any(_column_matches_prefix(c, p) for p in prefs)
    ]
    if not drop:
        return rad
    shown = ", ".join(prefs)
    print(f"  Token exclude ({shown}): drop {len(drop)} radiomics columns")
    return rad.drop(columns=drop)


def _prepare_rad_frame(rad_raw: pd.DataFrame) -> pd.DataFrame:
    """统一组学表 ID 列为 Name，去掉 Group，并丢掉前缀筛选命中的特征。"""
    rad = rad_raw.copy()
    if "Name" not in rad.columns:
        id_col = None
        preferred = str(getattr(cfg, "RAD_ID_COL", "") or "").strip()
        for c in [preferred, "patient_id", "PatientID", "Patient_ID", "ID", "Unnamed: 0"]:
            if c and c in rad.columns:
                id_col = c
                break
        if id_col is None and len(rad.columns):
            c0 = rad.columns[0]
            if not str(c0).startswith("h"):
                id_col = c0
        if id_col is not None:
            rad = rad.rename(columns={id_col: "Name"})
    rad = rad.drop(columns=["Group"], errors="ignore")
    return drop_excluded_rad_columns(rad)


def _group_from_id_series(ids: pd.Series) -> pd.Series:
    sep = str(getattr(cfg, "GROUP_FROM_ID_SEP", "_") or "_")
    return ids.astype(str).str.strip().str.split(sep, n=1).str[0]


def _synth_clin_from_rad(rad_raw: pd.DataFrame) -> pd.DataFrame:
    """无临床 sheet 时用组学 ID 合成 clin（可含从 ID 前缀推断的 Group）。"""
    rad = _prepare_rad_frame(rad_raw)
    if "Name" not in rad.columns:
        raise ValueError("无临床表且组学表找不到 ID 列（Name / RAD_ID_COL / 首列）")
    clin = pd.DataFrame({"Name": rad["Name"].astype(str).to_numpy()})
    if "Group" in rad_raw.columns:
        clin["Group"] = pd.Series(rad_raw["Group"].to_numpy())
        print("  无 CLIN_SHEET：用 rad.Group 作为分组")
    elif bool(getattr(cfg, "GROUP_FROM_ID", False)):
        clin["Group"] = _group_from_id_series(clin["Name"])
        vc = clin["Group"].value_counts(dropna=False).to_dict()
        print(f"  无 CLIN_SHEET：Group 由 ID 前缀推断 → {vc}")
    else:
        raise ValueError(
            "无 CLIN_SHEET：请设 GROUP_FROM_ID=True（从 ID 前缀分组）或在 rad 中提供 Group 列"
        )
    return clin


def _map_series_by_dict(s: pd.Series, value_map: dict, col_name: str) -> pd.Series:
    """
    将文本/混杂取值按 dict 映射为数值。
    key 匹配不区分大小写；已是数值且仅含 map 值域的保持不变。
    """
    if s is None:
        return s
    # 规范化 map：小写 key
    norm_map = {str(k).strip().lower(): int(v) for k, v in value_map.items()}
    allowed = set(norm_map.values())

    if pd.api.types.is_numeric_dtype(s):
        uniq = {int(x) for x in s.dropna().unique()}
        if uniq and uniq.issubset(allowed):
            return s.astype(float)
        # 数值但不在约定编码内，仍尝试按字符串映射

    def _one(v):
        if pd.isna(v):
            return np.nan
        key = str(v).strip().lower()
        if key in norm_map:
            return float(norm_map[key])
        # 已是 "0"/"1"
        if key in ("0", "1"):
            return float(key)
        return np.nan

    mapped = s.map(_one)
    n_bad = int((s.notna() & mapped.isna()).sum())
    if n_bad > 0:
        bad_vals = sorted({str(x) for x in s[s.notna() & mapped.isna()].unique()})[:8]
        raise ValueError(
            f"列 {col_name!r} 有 {n_bad} 个值无法按映射编码: {bad_vals} | map={value_map}"
        )
    return mapped.astype(float)


def _apply_outcome_binarize(clin: pd.DataFrame) -> pd.DataFrame:
    """按 OUTCOME_BINARIZE 将当前结局列编码为 0/1（在 rename→Group 之前）。"""
    out = clin.copy()
    gsrc = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    bmap_all = getattr(cfg, "OUTCOME_BINARIZE", None) or {}
    if not gsrc or gsrc not in out.columns:
        return out
    user_map = bmap_all.get(gsrc) if isinstance(bmap_all, dict) else None
    extra = dict(user_map) if isinstance(user_map, dict) else {}
    rmap = getattr(cfg, "READER_VALUE_MAP", None) or {}
    bmap = {"Positive": 1, "Negative": 0}
    if isinstance(rmap, dict):
        bmap.update(rmap)
    bmap.update(extra)
    before = out[gsrc].value_counts(dropna=False)
    try:
        out[gsrc] = _map_series_by_dict(out[gsrc], bmap, gsrc)
    except ValueError:
        if extra:
            raise
        return out
    n1 = int((out[gsrc] == 1).sum())
    n0 = int((out[gsrc] == 0).sum())
    print(
        f"  Outcome binarize [{gsrc}]: y=1 → {n1}, y=0 → {n0}  "
        f"(raw levels: {dict(before)})"
    )
    return out


def _td_roc_name(key: str) -> str:
    """读一个列名。多个时间列请用 _td_roc_names。数字列表在这里报错。"""
    names = _td_roc_names(key)
    return names[0] if names else ""


def _td_roc_names(key: str) -> list[str]:
    """读列名。一个保持兼容；多个按填写顺序。数字列表和旧的时间→列字典在这里报错。"""
    from modules.config.ini_io import coerce_td_roc_field
    val = coerce_td_roc_field(key, getattr(cfg, key, ""))
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def _binarize_status_column(series: pd.Series, column: str) -> pd.Series:
    """按该列的 OUTCOME_BINARIZE（再叠加 Reader 默认）收成 0/1。"""
    bmap_all = getattr(cfg, "OUTCOME_BINARIZE", None) or {}
    user_map = bmap_all.get(column) if isinstance(bmap_all, dict) else None
    extra = dict(user_map) if isinstance(user_map, dict) else {}
    rmap = getattr(cfg, "READER_VALUE_MAP", None) or {}
    bmap = {"Positive": 1, "Negative": 0}
    if isinstance(rmap, dict):
        bmap.update(rmap)
    bmap.update(extra)
    return _map_series_by_dict(series, bmap, column)


_LANDMARK_MONTH_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m|mo|mon|month|months|y|yr|year|years)$", re.IGNORECASE,
)


def _td_roc_slots(key: str) -> list[str]:
    from modules.config.ini_io import coerce_td_roc_field
    val = coerce_td_roc_field(key, getattr(cfg, key, ""))
    if isinstance(val, list):
        return [str(x).strip() for x in val]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def landmark_month_from_status(status_col: str) -> float:
    m = _LANDMARK_MONTH_RE.search(str(status_col or "").strip())
    if not m:
        raise ValueError(
            f"Landmark 状态列 {status_col!r} 没有配时间列，列名也读不出窗口月"
            "（如 _3m / _6m / _1y / _2y）。请在同一行选时间列，或改用带窗口月的状态列名。"
        )
    n = float(m.group(1))
    return n * 12.0 if m.group(2).lower().startswith("y") else n


def landmark_pairs(times: list[str], stats: list[str]) -> list[dict]:
    pairs: list[dict] = []
    for i in range(max(len(times), len(stats))):
        t = str(times[i]).strip() if i < len(times) else ""
        s = str(stats[i]).strip() if i < len(stats) else ""
        if not t and not s:
            continue
        if not s:
            raise ValueError(
                f"Landmark 第 {i + 1} 行只有时间列 {t!r}，没有状态列。每一行都须有状态列。"
            )
        month = None if t else landmark_month_from_status(s)
        pairs.append({"time_col": t, "status_col": s, "month": month})
    return pairs


def collect_landmark_labels(clin: pd.DataFrame) -> Optional[dict]:
    if not cfg.landmark_roc_on():
        return None
    times = _td_roc_slots("TD_ROC_TIMES")
    stats = _td_roc_slots("TD_ROC_STATUS_COLS")
    if not any(times) and not any(stats):
        return None
    pairs = landmark_pairs(times, stats)
    cols = [] if clin is None else [str(c) for c in clin.columns]
    listed = ", ".join(cols)
    if clin is None or clin.empty:
        raise ValueError(
            f"Landmark ROC 打开了，但临床表是空的。"
            f"时间列 {times!r}，状态列 {stats!r}。临床表实际列名: {listed}"
        )
    missing = [
        name for p in pairs for name in (p["time_col"], p["status_col"])
        if name and name not in clin.columns
    ]
    if missing:
        raise ValueError(f"找不到列 {missing!r}。临床表实际列名: {listed}")
    out = []
    for p in pairs:
        mapped = _binarize_status_column(clin[p["status_col"]], p["status_col"])
        status = pd.to_numeric(mapped, errors="coerce").to_numpy(dtype=float)
        if p["time_col"]:
            time = pd.to_numeric(clin[p["time_col"]], errors="coerce").to_numpy(dtype=float)
            finite = np.unique(time[np.isfinite(time)])
            label = f"{finite[0]:g}" if len(finite) == 1 else p["time_col"]
        else:
            time = np.full(len(clin), float(p["month"]), dtype=float)
            label = f"{float(p['month']):g}"
        n1 = int(np.nansum(status == 1))
        n0 = int(np.nansum(status == 0))
        src = p["time_col"] or "window month"
        print(f"  Landmark t={label} ({src}) status {p['status_col']!r}: y=1 → {n1}, y=0 → {n0}")
        out.append({
            "time_col": p["time_col"],
            "time_label": label,
            "status_col": p["status_col"],
            "time": time,
            "status": status,
        })
    return {"pairs": out}


def _slice_landmark(lm: Optional[dict], idx) -> Optional[dict]:
    if not lm:
        return None
    if not isinstance(lm, dict) or "pairs" not in lm:
        raise ValueError("Landmark 标签不再接受月份到标签的字典。须是时间列和状态列成对。")
    ix = np.asarray(idx, dtype=int)
    return {"pairs": [
        {
            **p,
            "time": np.asarray(p["time"], dtype=float)[ix].copy(),
            "status": np.asarray(p["status"], dtype=float)[ix].copy(),
        }
        for p in lm["pairs"]
    ]}


def collect_ipcw_labels(clin: pd.DataFrame) -> Optional[dict]:
    if not cfg.ipcw_roc_on():
        return None
    from modules.config.ini_io import coerce_ipcw_horizons
    time_col = str(getattr(cfg, "TD_IPCW_TIME_COL", "") or "").strip()
    status_col = str(getattr(cfg, "TD_IPCW_STATUS_COL", "") or "").strip()
    horizons = coerce_ipcw_horizons(getattr(cfg, "TD_IPCW_HORIZONS", None))
    cols = [] if clin is None else [str(c) for c in clin.columns]
    listed = ", ".join(cols)
    if not time_col or not status_col or not horizons:
        raise ValueError(
            "时间依赖性 ROC（IPCW）须填 TD_IPCW_TIME_COL、TD_IPCW_STATUS_COL、TD_IPCW_HORIZONS。"
            f"当前时间列 {time_col!r}，状态列 {status_col!r}，评估月 {horizons!r}。"
        )
    if clin is None or clin.empty:
        raise ValueError(
            f"时间依赖性 ROC（IPCW）打开了，但临床表是空的。临床表实际列名: {listed}"
        )
    missing = [c for c in (time_col, status_col) if c not in clin.columns]
    if missing:
        raise ValueError(f"IPCW 找不到列 {missing!r}。临床表实际列名: {listed}")
    time = pd.to_numeric(clin[time_col], errors="coerce").to_numpy(dtype=float)
    if np.any(np.isfinite(time) & (time < 0)):
        raise ValueError(f"IPCW 时间列 {time_col!r} 有负值。")
    mapped = _binarize_status_column(clin[status_col], status_col)
    status = pd.to_numeric(mapped, errors="coerce").to_numpy(dtype=float)
    bad = np.isfinite(status) & ~np.isin(status, (0.0, 1.0))
    if bad.any():
        vals = sorted({float(x) for x in status[bad]})[:8]
        raise ValueError(
            f"IPCW 状态列 {status_col!r} 须为 0/1（1=事件，0=删失），实际还有 {vals}。"
        )
    ok = np.isfinite(time) & np.isfinite(status)
    print(
        f"  IPCW time {time_col!r} status {status_col!r} horizons {horizons}: "
        f"n={int(ok.sum())} events={int(np.sum(status[ok] == 1))} "
        f"censored={int(np.sum(status[ok] == 0))}"
    )
    return {
        "time_col": time_col,
        "status_col": status_col,
        "horizons": list(horizons),
        "time": time,
        "status": status,
    }


def external_ipcw_labels(clin: pd.DataFrame, name: str) -> Optional[dict]:
    """外验缺 IPCW 列时返回 None。列在但取值不合法仍报错。"""
    try:
        return collect_ipcw_labels(clin)
    except ValueError as exc:
        text = str(exc)
        if text.startswith("IPCW 找不到列"):
            print(
                f"  ⚠ External [{name}] 无 IPCW 列，这一层不做时间依赖性 ROC。{text}"
            )
            return None
        raise


def _slice_ipcw(ip: Optional[dict], idx) -> Optional[dict]:
    if not ip:
        return None
    ix = np.asarray(idx, dtype=int)
    return {
        **ip,
        "time": np.asarray(ip["time"], dtype=float)[ix].copy(),
        "status": np.asarray(ip["status"], dtype=float)[ix].copy(),
    }


def _apply_reader_value_map(clin: pd.DataFrame) -> pd.DataFrame:
    """将 Reader 列按 READER_VALUE_MAP 编码为 0/1。

    同时写入 CLIN_LEVEL_MAPS[col][code] = 原文英文标签，供 Table1 显示
    （如 DMI/SMI、CSI/non_CSI），避免 Table1 出现 0/1。

    注意：settings 中以下划线开头的名（如 _READER_COLS）不会经
    ``from settings import *`` 进入 config 包命名空间，故读 settings 模块。
    """
    global CLIN_LEVEL_MAPS
    out = clin.copy()
    try:
        from modules.config import settings as _S
    except Exception:
        _S = cfg
    rmap = {
        "Positive": 1,
        "Negative": 0,
    }
    user_map = getattr(_S, "READER_VALUE_MAP", None) or getattr(
        cfg, "READER_VALUE_MAP", None
    ) or {}
    if isinstance(user_map, dict):
        rmap.update(user_map)
    readers = list(
        getattr(_S, "_READER_COLS", None)
        or getattr(cfg, "READER_COLS", None)
        or getattr(cfg, "_READER_COLS", None)
        or []
    )
    for c in list(getattr(cfg, "FORCE_CLIN_FOR_ROC", None) or []):
        if c and c not in readers:
            readers.append(c)
    # 兜底：列名含 Reader 的列
    if not readers:
        readers = [c for c in out.columns if "reader" in str(c).lower()]

    norm_map = {str(k).strip().lower(): int(v) for k, v in rmap.items()}
    encoded = []
    for col in readers:
        if col not in out.columns:
            continue
        s = out[col]
        # 已是数值（有序分级/连续评分）→ 保留，不做 Positive/Negative 映射
        if pd.api.types.is_numeric_dtype(s):
            continue
        # 仅当非空值能按 READER_VALUE_MAP（或 0/1）解释时才编码；
        # 避免把 Fazekas/MTA 等对照列误当成二分类 Reader。
        def _reader_token_ok(v) -> bool:
            if pd.isna(v):
                return True
            key = str(v).strip().lower()
            if key in norm_map or key in ("0", "1"):
                return True
            try:
                fv = float(v)
                return fv in (0.0, 1.0)
            except (TypeError, ValueError):
                return False

        if s.dropna().empty or not bool(s.map(_reader_token_ok).all()):
            continue

        # 原文标签 → code（优先保留数据中首次出现的英文展示形式）
        level_map: Dict[float, str] = {}
        for v in s.dropna():
            key = str(v).strip().lower()
            if key in norm_map:
                code = float(norm_map[key])
                disp = str(v).strip()
            elif key in ("0", "1"):
                code = float(key)
                disp = next(
                    (str(k) for k, vv in rmap.items() if int(vv) == int(code)),
                    key,
                )
            else:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv not in (0.0, 1.0):
                    continue
                code = fv
                disp = next(
                    (str(k) for k, vv in rmap.items() if int(vv) == int(code)),
                    str(int(code)),
                )
            if code not in level_map:
                level_map[code] = disp

        out[col] = _map_series_by_dict(s, rmap, col)
        if level_map:
            CLIN_LEVEL_MAPS[col] = level_map
        encoded.append(col)
    if encoded:
        print(f"  Reader value map → 0/1 (+Table1 English labels): {', '.join(encoded)}")
    return out


def _prepare_clin_frame(clin_raw: pd.DataFrame) -> pd.DataFrame:
    """统一临床表 Name / Group 列；应用结局二分类与 Reader 编码。"""
    clin = clin_raw.copy()

    # 展示姓名 → 内部 Name（复制，不改 CLIN_ID_COL；两列可以同名也可以不同）
    if "Name" not in clin.columns:
        lab = resolve_label_col(clin.columns)
        if lab and lab in clin.columns:
            clin["Name"] = clin[lab].astype(str)
        else:
            idc = _cfg_col("CLIN_ID_COL", "PatientID")
            for c in (idc, "PatientID", "patient_id", "label"):
                if c and c in clin.columns:
                    clin["Name"] = clin[c].astype(str)
                    break

    # 当前结局二分类（多类如 Pathologic_MI → Deep MI 0/1）
    clin = _apply_outcome_binarize(clin)
    # Reader 文本 → 0/1（含 Suspicious→1）
    clin = _apply_reader_value_map(clin)

    # 结局 → Group（当前子结局列优先，覆盖表里残留的公共 Group）
    clin = _assign_group_from_source(clin)
    if "Group" not in clin.columns:
        gcol = _find_group_column(list(clin.columns))
        if gcol != "Group":
            clin = clin.rename(columns={gcol: "Group"})

    return clin


def _align_and_prepare(rad_raw: pd.DataFrame, clin_raw: pd.DataFrame):
    """以 rad 表 ID 为准对齐 clin，并统一 Name/Group 列名。"""
    rad = _prepare_rad_frame(rad_raw)
    clin_id_col = getattr(cfg, "CLIN_ID_COL", "PatientID")
    if clin_id_col not in clin_raw.columns:
        for c in ("PatientID", "patient_id", "Name", "label"):
            if c in clin_raw.columns:
                clin_id_col = c
                break
    if rad.empty:
        clin = _prepare_clin_frame(clin_raw.reset_index(drop=True))
        return rad, clin
    rad_ids = rad["Name"] if "Name" in rad.columns else rad_raw.iloc[:, 0]
    # 若临床侧找不到可用 ID 列，则假定已与 rad 同行序对齐
    if clin_id_col not in clin_raw.columns:
        print("  ⚠ 临床表无 ID 列，按行序对齐（不校验 ID）")
        if len(clin_raw) != len(rad):
            raise ValueError(
                f"无 ID 且行数不一致: rad={len(rad)}, clin={len(clin_raw)}"
            )
        clin = _prepare_clin_frame(clin_raw.reset_index(drop=True))
        return rad, clin

    clin_aligned = _align_clin_to_rad(rad_ids, clin_raw, clin_id_col=clin_id_col)
    clin = _prepare_clin_frame(clin_aligned)
    return rad, clin


# 多终点循环时避免重复读 13MB+ xlsx（每结局仅重做 clin 结局编码）
_RAW_TABLE_CACHE: Dict[str, tuple] = {}


def _read_xlsx(path, sheet):
    """Radiomics sheets: zip reader. pandas if the sheet is not an xlsx name."""
    if str(path).lower().endswith(".csv"):
        return pd.read_csv(path)
    if not isinstance(sheet, str) or not str(path).lower().endswith((".xlsx", ".xlsm")):
        return pd.read_excel(path, sheet_name=sheet)
    from modules.utils.xlsx_sheet import read_sheet
    return read_sheet(path, sheet)


def _load_raw_tables(file: Optional[str] = None):
    """
    读取 rad / clin 原始表。
    主模式：同一 Excel 双 sheet（INPUT + RAD_SHEET/CLIN_SHEET），按 rad ID 对齐。
    可选：RAD_PATH + CLIN_PATH 均非空时走双文件模式。
    原始 sheet 按路径缓存；结局切换时仍会重新 binarize / 对齐。
    """
    rad_path = getattr(cfg, "RAD_PATH", "") or ""
    clin_path = getattr(cfg, "CLIN_PATH", "") or ""

    # 显式 --input：覆盖主 Excel 路径（单文件双 sheet）
    excel_file = None
    if file and str(file).strip():
        excel_file = file

    # 双文件模式（两者均配置）
    if rad_path and clin_path:
        # --input 若给出且 RAD_PATH 存在，则 file 视为临床表覆盖
        if excel_file and str(excel_file) != cfg.INPUT:
            clin_path = excel_file
        print("  Dual-file mode")
        print(f"    RAD : {rad_path}")
        print(f"    CLIN: {clin_path} [{getattr(cfg, 'CLIN_SHEET', 'cln')}]")
        cache_key = f"dual::{rad_path}::{clin_path}"
        if cache_key in _RAW_TABLE_CACHE:
            rad_raw, clin_raw = _RAW_TABLE_CACHE[cache_key]
            print("  (raw tables cache hit)")
        else:
            if str(rad_path).lower().endswith(".csv"):
                rad_raw = pd.read_csv(rad_path)
            else:
                rad_raw = _read_xlsx(rad_path, getattr(cfg, "RAD_SHEET", 0))
            clin_sheet = getattr(cfg, "CLIN_SHEET", 0)
            if str(clin_path).lower().endswith(".csv"):
                clin_raw = pd.read_csv(clin_path)
            else:
                clin_raw = pd.read_excel(clin_path, sheet_name=clin_sheet)
            _RAW_TABLE_CACHE[cache_key] = (rad_raw, clin_raw)
        # 【最先】按 exc 列表剔除样本，再对齐/编码/统计
        rad_raw, clin_raw = apply_sample_exclusion(
            rad_raw, clin_raw, data_file=excel_file or clin_path or rad_path, stage="raw-dual"
        )
        rad, clin = _align_and_prepare(rad_raw, clin_raw)
        return rad, clin, True

    # 主模式：单 Excel 双 sheet；RAD_SHEET 为空则纯临床
    xfile = excel_file or cfg.INPUT
    print(f"  Excel dual-sheet mode: {xfile}")
    rad_sheet = str(getattr(cfg, "RAD_SHEET", "") or "").strip()
    clin_sheet = str(getattr(cfg, "CLIN_SHEET", "") or "").strip()
    print(f"    sheets: rad={rad_sheet!r}, clin={clin_sheet!r}")
    if not rad_sheet and not clin_sheet:
        raise ValueError(
            "CLIN_SHEET 与 RAD_SHEET 必须二选一（可同时为空以外的任意工作表名，但不能都空）"
        )
    # 排除名单：仅当文件已存在时补 exc 表头，不在工程根凭空新建空表
    if bool(getattr(cfg, "DO_EXCLUDE_SAMPLES", True)):
        for ex_path in resolve_exclude_files():
            if os.path.isfile(ex_path):
                ensure_blank_exc_sheet(ex_path, create_if_missing_file=False)
    cache_key = f"xlsx::{xfile}::{rad_sheet}::{clin_sheet}"
    if cache_key in _RAW_TABLE_CACHE:
        rad_raw, clin_raw = _RAW_TABLE_CACHE[cache_key]
        print("  (raw tables cache hit)")
    else:
        t0 = time.perf_counter()
        if not rad_sheet:
            rad_raw = pd.DataFrame()
        else:
            rad_raw = _read_xlsx(xfile, rad_sheet)
        if not clin_sheet:
            clin_raw = _synth_clin_from_rad(rad_raw)
        else:
            clin_raw = pd.read_excel(xfile, sheet_name=clin_sheet)
        _RAW_TABLE_CACHE[cache_key] = (rad_raw, clin_raw)
        print(f"  raw tables loaded & cached in {time.perf_counter() - t0:.1f}s")
    # 【最先】按 exc 列表剔除样本，再对齐/编码/统计
    rad_raw, clin_raw = apply_sample_exclusion(
        rad_raw, clin_raw, data_file=xfile, stage="raw"
    )
    rad, clin = _align_and_prepare(rad_raw, clin_raw)
    return rad, clin, False


def _mapped_level_name(raw: str) -> str:
    """列值 → GROUP_LEVEL_DISPLAY。没有映射则空串。"""
    mapping = getattr(cfg, "GROUP_LEVEL_DISPLAY", None) or {}
    if not isinstance(mapping, dict):
        return ""
    v = mapping.get(str(raw).strip())
    if v is None:
        return ""
    return str(v).strip()


def _display_group_labels(lbl0: str, lbl1: str) -> tuple:
    """
    Table1 / 图例用的两组名字。

    列值在 GROUP_LEVEL_DISPLAY 里时用展示名（实验组→AC，对照组→NC），
    不随 y=0/1 对调。没有逐水平映射时，y=0 用阴性展示名，y=1 用阳性展示名。
    """
    d0 = _mapped_level_name(lbl0)
    d1 = _mapped_level_name(lbl1)
    pos = str(getattr(cfg, "GROUP_DISPLAY_POS", "") or "").strip()
    neg = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    # 展示名空时用列值原文。不造 non_ 前缀，也不用结局列名顶替分组卡。
    if not d0:
        d0 = neg or str(lbl0)
    if not d1:
        d1 = pos or str(lbl1)
    return d0, d1


def _encode_group_column(group_col: pd.Series):
    """
    Group → 0/1。
    优先：POSITIVE_GROUP_LABEL → y=1；否则按字典序（前一个为 0）。
    返回 (group_num Series, group_factor Series, lbl0, lbl1)
    group_factor / lbl 使用分组卡展示名或 GROUP_LEVEL_DISPLAY，不用 non_ 前缀。
    """
    if not pd.api.types.is_numeric_dtype(group_col):
        gvals = sorted(pd.unique(group_col.dropna().astype(str)))
        if len(gvals) != 2:
            raise ValueError(f"Group 必须恰好有 2 个唯一值；当前: {', '.join(map(str, gvals))}")
        pos_pref = str(getattr(cfg, "POSITIVE_GROUP_LABEL", "") or "").strip()
        gvals_l = {v.lower(): v for v in gvals}
        if pos_pref and pos_pref.lower() in gvals_l:
            raw1 = gvals_l[pos_pref.lower()]
            raw0 = [v for v in gvals if v != raw1][0]
            gmap = {raw0: 0, raw1: 1}
            print(f"  Group 映射 (POSITIVE_GROUP_LABEL={pos_pref!r}): "
                  f"'{raw0}' → 0, '{raw1}' → 1")
            lbl0, lbl1 = raw0, raw1
        else:
            print(f"  Group 映射: '{gvals[0]}' → 0, '{gvals[1]}' → 1")
            gmap = {gvals[0]: 0, gvals[1]: 1}
            lbl0, lbl1 = gvals[0], gvals[1]
        group_num = group_col.astype(str).map(gmap).astype(int)
    else:
        lbl0, lbl1 = "0", "1"
        group_num = pd.to_numeric(group_col, errors="coerce")
        if group_num.isna().any():
            raise ValueError(
                f"Group 有 {int(group_num.isna().sum())} 个缺失/非数值，无法编成 0/1"
            )
        group_num = group_num.astype(int)

    lbl0, lbl1 = _display_group_labels(lbl0, lbl1)
    group_factor = group_num.map({0: lbl0, 1: lbl1})
    print(f"  Group 展示标签: 0 → {lbl0!r}, 1 → {lbl1!r}")
    return group_num, group_factor, lbl0, lbl1


def load_external_layer(file: str, clin_sheet: str, rad_sheet: str,
                         name: str = "external",
                         rad_id_col: str = "") -> DataLayer:
    """从同一 xlsx 的 clin/rad sheet 对加载一个外部验证 DataLayer。"""
    print(f"  External [{name}]: clin={clin_sheet!r}, rad={rad_sheet!r}"
          + (f"  rad_id={rad_id_col!r}" if rad_id_col else ""))
    old_rid = getattr(cfg, "RAD_ID_COL", None)
    if rad_id_col:
        cfg.RAD_ID_COL = rad_id_col
    try:
        return _load_external_layer_body(file, clin_sheet, rad_sheet, name)
    finally:
        if rad_id_col:
            if old_rid is None:
                try:
                    delattr(cfg, "RAD_ID_COL")
                except Exception:
                    cfg.RAD_ID_COL = old_rid
            else:
                cfg.RAD_ID_COL = old_rid


def _load_external_layer_body(file: str, clin_sheet: str, rad_sheet: str,
                              name: str) -> DataLayer:
    """load_external_layer 主体（可临时覆盖 RAD_ID_COL）。"""
    rad_raw = _read_xlsx(file, rad_sheet)
    clin_raw = pd.read_excel(file, sheet_name=clin_sheet)
    # 【最先】排除列表与主队列同一规则
    rad_raw, clin_raw = apply_sample_exclusion(
        rad_raw, clin_raw, data_file=file, stage=f"external:{name}"
    )
    rad, clin_prep = _align_and_prepare(rad_raw, clin_raw)

    if "Group" not in clin_prep.columns:
        # _prepare_clin_frame 可能已处理；再查 clin_raw
        if "Group" not in clin_raw.columns:
            gsrc = getattr(cfg, "GROUP_SOURCE_COL", "LVSI")
            if gsrc in clin_raw.columns:
                clin_prep = clin_prep.copy()
                # align used prepared frame — re-prepare from aligned raw
                pass
    # 用对齐后的 clin：保证有 Group
    clin_aligned = clin_prep
    if "Group" not in clin_aligned.columns:
        raise ValueError(f"External [{name}] 缺少 Group/结局列")

    lab_s = _pick_label_series(clin_aligned, rad)
    sample_labels = lab_s.astype(str).tolist() if lab_s is not None else None

    skip_hit = _skip_nonfeature_cols(clin_aligned)
    clin = clin_aligned.drop(columns=skip_hit, errors="ignore")
    clin = _encode_english_categoricals(clin)

    group_num, group_factor, lbl0, lbl1 = _encode_group_column(clin_aligned["Group"])
    n0 = int((group_num == 0).sum())
    n1 = int((group_num == 1).sum())
    print(f"    N={len(clin)}  ({lbl1}={n1}, {lbl0}={n0})")

    rad = rad.drop(columns=["Group"], errors="ignore").reset_index(drop=True)
    return DataLayer(
        rad=rad,
        clin=clin.reset_index(drop=True),
        group=group_factor.reset_index(drop=True),
        label=group_num.to_numpy(),
        sample_labels=sample_labels,
        landmark=collect_landmark_labels(clin_aligned),
        ipcw=external_ipcw_labels(clin_aligned, name),
    )


def _hc_rows_outside_keep(clin_raw: pd.DataFrame, keep: list, subgroup_eval: bool) -> Optional[pd.DataFrame]:
    """选两组把精确水平 HC 留在建模队列外时，抄出这些行。

    HC 已经在建模两组里，或数据里没有这个水平，返回 None。不造列。
    """
    if subgroup_eval or not keep or clin_raw is None or "Group" not in clin_raw.columns:
        return None
    if "HC" in {str(x).strip() for x in keep}:
        return None
    mask = clin_raw["Group"].astype(str).str.strip().eq("HC")
    if not bool(mask.any()):
        return None
    return clin_raw.loc[mask].copy()


def _hc_relabel_stem(raw_group: pd.Series, group_num: pd.Series, lbl0: str, lbl1: str) -> str:
    """建模行里原始水平已是 HC，但展示名被映走时，返回那个展示名。

    Train vs Test 表只把这一列改回 HC。没有 HC，或展示名本来就是 HC，返回空串。
    """
    raw = pd.Series(raw_group).astype(str).str.strip().reset_index(drop=True)
    gn = pd.Series(group_num).reset_index(drop=True)
    if len(raw) != len(gn):
        raise ValueError(f"HC 展示名回改：Group 行数 {len(raw)} 与编码 {len(gn)} 不一致")
    hit = raw.eq("HC").to_numpy()
    if not bool(hit.any()):
        return ""
    sides = pd.unique(gn.to_numpy()[hit])
    if len(sides) != 1:
        raise ValueError(f"HC 落在不止一个编码上: {list(sides)}")
    stem = lbl0 if int(sides[0]) == 0 else lbl1
    stem = str(stem).strip()
    if stem == "HC":
        return ""
    return stem


def _prepare_hc_features(hc_raw: pd.DataFrame, clin_columns: list) -> pd.DataFrame:
    """HC 行对齐到已编码的建模列。

    不调用 _coerce_numeric_strings：那个函数会清空 CLIN_LEVEL_MAPS，把建模编码冲掉。
    分类列用已经拟合的水平表把原文映回代码。
    """
    skip_hit = _skip_nonfeature_cols(hc_raw)
    src = hc_raw.drop(columns=skip_hit, errors="ignore")
    missing = [c for c in clin_columns if c not in src.columns]
    if missing:
        raise ValueError(
            f"找不到列 {missing!r}。HC 表实际列名: {list(src.columns)}"
        )
    out = pd.DataFrame(index=src.index)
    for col in clin_columns:
        series = src[col]
        level_map = CLIN_LEVEL_MAPS.get(str(col)) or {}
        if level_map:
            rev: Dict[str, float] = {}
            codes = set()
            for code, lab in level_map.items():
                fv = float(code)
                codes.add(fv)
                rev[str(lab).strip()] = fv
                rev[str(lab).strip().casefold()] = fv
            mapped = []
            known = sorted({str(v) for v in level_map.values()})
            for val in series:
                if pd.isna(val):
                    mapped.append(np.nan)
                    continue
                key = str(val).strip()
                if key in rev:
                    mapped.append(rev[key])
                    continue
                if key.casefold() in rev:
                    mapped.append(rev[key.casefold()])
                    continue
                try:
                    fv = float(key)
                except (TypeError, ValueError):
                    fv = None
                if fv is not None and fv in codes:
                    mapped.append(fv)
                    continue
                raise ValueError(
                    f"HC 列 {col!r} 的值 {key!r} 不在已编码水平里: {known}。"
                    f"HC 表实际列名: {list(src.columns)}"
                )
            out[col] = mapped
            continue
        num = pd.to_numeric(series, errors="coerce")
        numeric_like = pd.api.types.is_numeric_dtype(series) or (
            bool(series.notna().any()) and bool((series.isna() | num.notna()).all())
        )
        out[col] = num if numeric_like else series
    return out.reset_index(drop=True)


def _collapse_extra_groups_for_model(clin_raw: pd.DataFrame) -> pd.DataFrame:
    """全部组且超过两个水平时，建模把阳性以外收成一个阴性。

    总表和 Table 1 用筛前副本，仍是原始水平。没有阳性标签就停，不猜哪一组是阳性。
    """
    if clin_raw is None or "Group" not in clin_raw.columns:
        return clin_raw
    g = clin_raw["Group"].astype(str).str.strip()
    levels = [x for x in pd.unique(g) if x and x.lower() != "nan"]
    if len(levels) <= 2:
        return clin_raw
    pos = str(getattr(cfg, "POSITIVE_GROUP_LABEL", "") or "").strip()
    if not pos or pos not in set(levels):
        raise ValueError(
            f"全部组有 {len(levels)} 个水平 ({', '.join(map(str, levels))})，"
            f"建模需要阳性标签。当前 POSITIVE_GROUP_LABEL={pos!r}。"
        )
    neg = str(getattr(cfg, "GROUP_DISPLAY_NEG", "") or "").strip()
    taken = set(levels)
    if not neg or neg == pos or neg in taken:
        neg = f"non_{pos}"
    if neg in taken or neg == pos:
        neg = f"non_{pos}_model"
    out = clin_raw.copy()
    out["Group"] = np.where(g.eq(pos), pos, neg)
    print(
        f"  全部组 {len(levels)} 个水平：建模把「{pos}」以外收成 {neg}。"
        f"总表和 Table 1 仍比较原始水平。"
    )
    return out


def _split_index_list(ids, ratio: float, seed: int) -> tuple[list[int], list[int]]:
    """与 _split_hc_frame 同一套比例和随机序列，只切行号。"""
    ids = np.asarray(list(ids), dtype=int)
    n = len(ids)
    rng = np.random.RandomState(int(seed))
    order = rng.permutation(n) if n else np.array([], dtype=int)
    n_train = int(np.floor(n * float(ratio)))
    return ids[order[:n_train]].tolist(), ids[order[n_train:]].tolist()


def _baseline_split_ids(
    row_ids,
    train_idx,
    test_idx,
    outside_ids,
    ratio: float,
    seed: int,
    use_internal: bool,
) -> tuple[set[int], set[int]]:
    """建模行跟建模拆分走。被 GROUP_KEEP 留在外面的行按同一比例切开，只给 Table 1。"""
    row_ids = np.asarray(row_ids, dtype=int)
    tr = np.asarray(train_idx, dtype=int)
    te = np.asarray(test_idx, dtype=int)
    train_ids = set(row_ids[tr].tolist()) if len(tr) else set()
    test_ids = set(row_ids[te].tolist()) if len(te) else set()
    if not use_internal:
        train_ids.update(int(x) for x in outside_ids)
        return train_ids, test_ids
    left, right = _split_index_list(outside_ids, ratio, seed)
    train_ids.update(left)
    test_ids.update(right)
    return train_ids, test_ids


def _split_hc_frame(frame: pd.DataFrame, ratio: float, seed: int):
    """HC 描述列用同一 seed 和比例拆开。

    新开一个 RandomState，不消耗建模拆分已经用过的随机序列。
    """
    n = len(frame)
    rng = np.random.RandomState(int(seed))
    order = rng.permutation(n) if n else np.array([], dtype=int)
    n_train = int(np.floor(n * float(ratio)))
    train = frame.iloc[np.sort(order[:n_train])].reset_index(drop=True)
    test = frame.iloc[np.sort(order[n_train:])].reset_index(drop=True)
    return train, test


def load_data(file: Optional[str] = None, ratio: Optional[float] = None,
              seed: Optional[int] = None) -> SplitData:
    """
    读取数据源并构建 all/train/test 三层数据集。
    双文件模式下以组学表行序为准对齐临床表。

    返回
    ----
    SplitData(all=..., train=..., test=...)
    """
    global CLIN_LEVEL_MAPS, CLIN_VAR_TYPES, CLIN_ORDINAL_TOKEN_COLS
    CLIN_LEVEL_MAPS = {}
    CLIN_VAR_TYPES = {}
    CLIN_ORDINAL_TOKEN_COLS = set()
    if file is None:
        file = str(getattr(cfg, "INPUT", "data_merged.xlsx") or "data_merged.xlsx")
    if ratio is None:
        ratio = float(getattr(cfg, "TRAIN_RATIO", 0.7))
    ratio = float(ratio)
    if not 0.0 < ratio <= 1.0:
        raise ValueError(
            f"TRAIN_RATIO must be in (0, 1]; got {ratio!r} "
            "(1 = no internal split, all rows stay in TRAIN)"
        )
    if seed is None:
        seed = int(getattr(cfg, "SEED", 66666) or 66666)
    print("═══ DATA LOADING & SPLIT ═══")

    rad_raw, clin_raw, dual = _load_raw_tables(file)

    # ── 当前子结局列 → Group（覆盖公共 Group；无结局列才回退关键词）──
    clin_raw = _assign_group_from_source(clin_raw)
    if "Group" not in clin_raw.columns:
        gcol = _find_group_column(list(clin_raw.columns))
        if gcol != "Group":
            clin_raw = clin_raw.rename(columns={gcol: "Group"})

    if bool(getattr(cfg, "GROUP_FROM_ID", False)):
        # 分组前缀来自临床 ID，不是展示姓名
        idc = _cfg_col("CLIN_ID_COL", "PatientID")
        src = None
        if idc and idc in clin_raw.columns:
            src = clin_raw[idc]
        elif not rad_raw.empty and "Name" in rad_raw.columns:
            src = rad_raw["Name"]
        elif "Name" in clin_raw.columns:
            src = clin_raw["Name"]
        if src is not None:
            derived = _group_from_id_series(src)
            clin_raw = clin_raw.copy()
            clin_raw["Group"] = derived
            print(f"  GROUP_FROM_ID 覆盖 Group → {derived.value_counts().to_dict()}")
        else:
            print("  ⚠ GROUP_FROM_ID：找不到 CLIN_ID_COL / Name，未改 Group")

    sub_col = str(getattr(cfg, "SUBGROUP_COL", "") or "").strip()
    outcome_col = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "").strip()
    subgroup_eval = bool(sub_col and sub_col != outcome_col and sub_col in clin_raw.columns)
    if subgroup_eval:
        print(f"  亚组评估列 {sub_col!r}（主模型锁死后分层 ROC，不按此列筛训练）")

    # ── 行筛选：只保留指定组（两两比较前先收成 2 类）──
    # HC 若被「选两组」留在建模队列外，先抄出来。只给 Train vs Test 描述列，不进建模。
    # 总表用筛前全水平副本：选两组时仍写出 MCI|CN-ESRD|HC（实际有的水平）。
    # _prekeep_id 把建模拆分指回这份副本，Table 1 才能把留在外面的组加回去。
    keep = [str(x).strip() for x in (getattr(cfg, "GROUP_KEEP", None) or []) if str(x).strip()]
    clin_raw = clin_raw.copy()
    clin_raw["_prekeep_id"] = np.arange(len(clin_raw), dtype=int)
    prekeep_clin = clin_raw.drop(columns=["_prekeep_id"]).copy()
    keep_outside_ids: list[int] = []
    hc_source = _hc_rows_outside_keep(clin_raw, keep, subgroup_eval)
    hc_relabel_from = ""
    hc_train = None
    hc_test = None
    if keep and not subgroup_eval:
        filt = "Group"
        g = clin_raw[filt].astype(str)
        m = g.isin(keep)
        n_drop = int((~m).sum())
        keep_outside_ids = clin_raw.loc[~m, "_prekeep_id"].astype(int).tolist()
        clin_raw = clin_raw.loc[m].reset_index(drop=True)
        if not rad_raw.empty and len(rad_raw) == len(m):
            rad_raw = rad_raw.loc[m.values].reset_index(drop=True)
        print(f"  GROUP_KEEP {keep}: keep {len(clin_raw)}  drop {n_drop}")
        if len(clin_raw) == 0:
            raise ValueError(f"GROUP_KEEP={keep} 后 N=0")

    # 结局缺失 / 二值化后非 0/1 的行不能进模型
    if "Group" in clin_raw.columns:
        g = clin_raw["Group"]
        if pd.api.types.is_numeric_dtype(g):
            gnum = pd.to_numeric(g, errors="coerce")
            g_ok = gnum.isin([0, 1])
        else:
            g_ok = g.notna() & ~g.astype(str).str.strip().str.lower().isin(
                ("", "nan", "none", "nat")
            )
        n_drop_g = int((~g_ok).sum())
        if n_drop_g:
            clin_raw = clin_raw.loc[g_ok].reset_index(drop=True)
            if not rad_raw.empty and len(rad_raw) == len(g_ok):
                rad_raw = rad_raw.loc[g_ok.to_numpy()].reset_index(drop=True)
            print(f"  Drop missing/invalid Group: {n_drop_g}  remain {len(clin_raw)}")
        if len(clin_raw) == 0:
            raise ValueError("Group 有效行 N=0")

    subgroup_raw: Optional[pd.Series] = None
    if subgroup_eval and sub_col in clin_raw.columns:
        subgroup_raw = clin_raw[sub_col].astype(str).reset_index(drop=True)

    # ── 若 rad 表中也含 Group 列（历史文件可能包含），合并前删除，避免 Group_x/Group_y ──
    rad = rad_raw.drop(columns=["Group"], errors="ignore")

    # 行数一致性（无组学表时 rad 为空，允许不一致）
    if not rad.empty and len(rad) != len(clin_raw):
        raise ValueError(
            f"rad/clin 行数不一致: rad={len(rad)}, clin={len(clin_raw)} "
            f"(dual_file={dual})"
        )

    # ── 生存时间（跳过临床特征前单独保留，不进预测模型）──
    survival_time: Optional[np.ndarray] = None
    time_col = str(getattr(cfg, "SURVIVAL_TIME_COL", "") or "").strip()
    if time_col and time_col in clin_raw.columns:
        survival_time = pd.to_numeric(clin_raw[time_col], errors="coerce").to_numpy(dtype=float)
        n_t = int(np.isfinite(survival_time).sum())
        print(
            f"  Survival time: {time_col!r}  finite={n_t}/{len(survival_time)}  "
            f"range=[{np.nanmin(survival_time):.2f}, {np.nanmax(survival_time):.2f}]"
        )
    elif time_col:
        print(f"  ⚠ SURVIVAL_TIME_COL={time_col!r} not found in clin sheet")

    survival_event: Optional[np.ndarray] = None
    status_col = str(getattr(cfg, "TD_IPCW_STATUS_COL", "") or "").strip()
    if status_col and status_col in clin_raw.columns:
        mapped = _binarize_status_column(clin_raw[status_col], status_col)
        survival_event = pd.to_numeric(mapped, errors="coerce").to_numpy(dtype=float)
        n_ev = int(np.nansum(survival_event == 1))
        n_c = int(np.nansum(survival_event == 0))
        print(f"  Survival event: {status_col!r}  event={n_ev}  censor={n_c}")
    elif status_col:
        print(f"  ⚠ TD_IPCW_STATUS_COL={status_col!r} not found in clin sheet")

    landmark_all = collect_landmark_labels(clin_raw)
    ipcw_all = collect_ipcw_labels(clin_raw)

    # ── 提取样本标签（false_class / 图注用姓名；与 CLIN_ID_COL 可以不同）──
    #     在过滤 SKIP 列之前读取，确保 LABEL_COL 不会被移除
    lab_s = _pick_label_series(clin_raw, rad)
    sample_labels = lab_s.astype(str).tolist() if lab_s is not None else None
    resolved_lab = resolve_label_col(clin_raw.columns)
    cfg_lab = _cfg_col("LABEL_COL")
    if resolved_lab and resolved_lab != cfg_lab:
        print(f"  LABEL_COL {cfg_lab!r} → {resolved_lab!r}（展示姓名 ≠ 临床 ID）")

    # ── 移除跳过列（ID、姓名、Group 等非特征列），得到纯特征矩阵 ──
    skip_hit = _skip_nonfeature_cols(clin_raw)
    clin = clin_raw.drop(columns=list(skip_hit) + ["_prekeep_id"], errors="ignore")
    if skip_hit:
        print(f"  Skipped: {', '.join(skip_hit)}")

    # ── 英文分类变量（Positive/Negative 等）→ 分析用数值 ──
    clin = _encode_english_categoricals(clin)

    # ── Group → 0/1（优先 POSITIVE_GROUP_LABEL 为 y=1）──
    # 全部组留下 3 个以上水平时，只把建模用的 Group 收成阳性 / 其余。
    # prekeep 仍是原始水平，总表和 Table 1 按原始水平做 ANOVA 与 LSD。
    clin_raw = _collapse_extra_groups_for_model(clin_raw)
    group_num, group_factor, lbl0, lbl1 = _encode_group_column(clin_raw["Group"])
    hc_relabel_from = _hc_relabel_stem(clin_raw["Group"], group_num, lbl0, lbl1)
    if hc_source is not None:
        hc_feat = _prepare_hc_features(hc_source, list(clin.columns))
        hc_train, hc_test = _split_hc_frame(hc_feat, ratio, seed)
    n_total = len(clin_raw)
    n0 = int((group_num == 0).sum())
    n1 = int((group_num == 1).sum())
    print(f"  总 N = {n_total}  ({lbl1}={n1}, {lbl0}={n0})  [y=1 为阳性]")

    all_data = DataLayer(
        rad=rad.reset_index(drop=True),
        clin=clin.reset_index(drop=True),
        group=group_factor.reset_index(drop=True),
        label=group_num.to_numpy(),
        sample_labels=sample_labels,
        time=None if survival_time is None else survival_time.copy(),
        subgroup=None if subgroup_raw is None else subgroup_raw.reset_index(drop=True),
        landmark=landmark_all,
        ipcw=ipcw_all,
        event=None if survival_event is None else survival_event.copy(),
    )

    external_specs = list(getattr(cfg, "EXTERNAL_TESTS", []) or [])
    use_internal = True
    if external_specs and not getattr(cfg, "USE_INTERNAL_SPLIT_WHEN_EXTERNAL", True):
        use_internal = False
        print("  EXTERNAL_TESTS 已配置 → 主表全部作为 TRAIN（不做内部拆分）")

    if use_internal:
        # Patient-level split: all nodules from one patient stay in the same set.
        # Patient key = CLIN_ID_COL (not the display LABEL_COL), trailing nodule index stripped.
        idc = _cfg_col("CLIN_ID_COL", "PatientID")
        if idc and idc in clin_raw.columns:
            keys = [_patient_key_for_exclude(x) for x in clin_raw[idc].astype(str).tolist()]
        elif sample_labels is not None and len(sample_labels) == n_total:
            keys = [_patient_key_for_exclude(x) for x in sample_labels]
            print("  ⚠ Patient-level split: no CLIN_ID_COL, using LABEL_COL")
        else:
            keys = [str(i) for i in range(n_total)]
            print("  ⚠ No sample labels for patient-level split; falling back to nodule-level keys")

        locked = _indices_from_split_col(clin_raw, keys, n_total)
        if locked is not None:
            train_idx, test_idx = locked
        else:
            # Patient-level label = 1 if any nodule is growth (for stratification)
            key_to_idx: dict = {}
            key_to_y: dict = {}
            y_arr = group_num.to_numpy()
            for i, k in enumerate(keys):
                key_to_idx.setdefault(k, []).append(i)
                key_to_y[k] = max(key_to_y.get(k, 0), int(y_arr[i]))

            keys_1 = [k for k, yv in key_to_y.items() if yv == 1]
            keys_0 = [k for k, yv in key_to_y.items() if yv == 0]
            rng = np.random.RandomState(seed)
            n_train_1 = int(np.floor(len(keys_1) * ratio))
            n_train_0 = int(np.floor(len(keys_0) * ratio))
            train_keys = set(
                list(rng.choice(keys_1, size=max(n_train_1, 0), replace=False))
                + list(rng.choice(keys_0, size=max(n_train_0, 0), replace=False))
            )
            train_idx = np.sort(
                np.array([i for k, idxs in key_to_idx.items() if k in train_keys for i in idxs], dtype=int)
            )
            test_idx = np.setdiff1d(np.arange(n_total), train_idx)
            te_keys = set(keys[i] for i in test_idx)
            leak = train_keys & te_keys
            if leak:
                raise RuntimeError(f"Patient-level split leakage: {len(leak)} keys")
            print(
                f"  Patient-level split: patients train={len(train_keys)}, test={len(te_keys)}; "
                f"nodules train={len(train_idx)}, test={len(test_idx)} (no cross-set multi-nodule split)"
            )
    else:
        train_idx = np.arange(n_total)
        test_idx = np.array([], dtype=int)

    row_ids_now = clin_raw["_prekeep_id"].to_numpy(dtype=int)
    if len(row_ids_now) != n_total:
        raise ValueError(
            f"总表行号 {len(row_ids_now)} 与建模队列 {n_total} 不一致"
        )
    baseline_train_ids, baseline_test_ids = _baseline_split_ids(
        row_ids_now, train_idx, test_idx, keep_outside_ids,
        ratio, seed, use_internal,
    )

    def _time_sub(ix: np.ndarray) -> Optional[np.ndarray]:
        if survival_time is None:
            return None
        if len(ix) == 0:
            return np.array([], dtype=float)
        return survival_time[ix].copy()

    def _event_sub(ix: np.ndarray) -> Optional[np.ndarray]:
        if survival_event is None:
            return None
        if len(ix) == 0:
            return np.array([], dtype=float)
        return survival_event[ix].copy()

    def _sg_sub(idx: np.ndarray) -> Optional[pd.Series]:
        if subgroup_raw is None:
            return None
        if len(idx) == 0:
            return subgroup_raw.iloc[0:0].reset_index(drop=True)
        return subgroup_raw.iloc[idx].reset_index(drop=True)

    def _subset(idx: np.ndarray) -> DataLayer:
        if len(idx) == 0:
            return DataLayer(
                rad=rad.iloc[0:0].reset_index(drop=True),
                clin=clin.iloc[0:0].reset_index(drop=True),
                group=group_factor.iloc[0:0].reset_index(drop=True),
                label=np.array([], dtype=int),
                sample_labels=[] if sample_labels is not None else None,
                time=_time_sub(idx),
                subgroup=_sg_sub(idx),
                landmark=_slice_landmark(landmark_all, idx),
                ipcw=_slice_ipcw(ipcw_all, idx),
                event=_event_sub(idx),
            )
        if rad.empty:
            rad_sub = rad.iloc[0:0].reset_index(drop=True)
        else:
            rad_sub = rad.reset_index(drop=True).iloc[idx].reset_index(drop=True)
        return DataLayer(
            rad=rad_sub,
            clin=clin.iloc[idx].reset_index(drop=True),
            group=group_factor.iloc[idx].reset_index(drop=True),
            label=group_num.to_numpy()[idx],
            sample_labels=[sample_labels[i] for i in idx] if sample_labels is not None else None,
            time=_time_sub(idx),
            subgroup=_sg_sub(idx),
            landmark=_slice_landmark(landmark_all, idx),
            ipcw=_slice_ipcw(ipcw_all, idx),
            event=_event_sub(idx),
        )

    train = _subset(train_idx)
    test = _subset(test_idx)

    n_all = len(all_data.clin)
    print(f"  All N={n_all} ({lbl1}={n1}, {lbl0}={n0})  "
          f"Train N={len(train.clin)}  InternalTest N={len(test.clin)}")

    # 总表帧：筛前全水平。列与建模 clin 对齐；缺水平不造列。
    # Train / Test 用同一帧按建模拆分切开，留在 GROUP_KEEP 外的组只进 Table 1。
    baseline_clin = None
    baseline_group = None
    baseline_train_clin = None
    baseline_train_group = None
    baseline_test_clin = None
    baseline_test_group = None
    if "Group" in prekeep_clin.columns and len(clin.columns):
        baseline_clin = _prepare_hc_features(prekeep_clin, list(clin.columns))
        baseline_group = prekeep_clin["Group"].astype(str).str.strip().reset_index(drop=True)
        if len(baseline_group) != len(prekeep_clin):
            raise ValueError(
                f"总表行数 {len(baseline_group)} 与筛前队列 {len(prekeep_clin)} 不一致"
            )
        n_base = int(baseline_group.nunique())
        print(f"  总表水平 ({n_base}): {', '.join(sorted(baseline_group.unique()))}")

        def _take_base(id_set: set[int]):
            ix = np.array(sorted(id_set), dtype=int)
            ix = ix[(ix >= 0) & (ix < len(baseline_clin))]
            if len(ix) == 0:
                empty_c = baseline_clin.iloc[0:0].copy()
                empty_g = baseline_group.iloc[0:0].reset_index(drop=True)
                return empty_c, empty_g
            return (
                baseline_clin.iloc[ix].reset_index(drop=True),
                baseline_group.iloc[ix].reset_index(drop=True),
            )

        baseline_train_clin, baseline_train_group = _take_base(baseline_train_ids)
        baseline_test_clin, baseline_test_group = _take_base(baseline_test_ids)

    return SplitData(
        all=all_data, train=train, test=test,
        hc_train=hc_train, hc_test=hc_test, hc_relabel_from=hc_relabel_from,
        baseline_clin=baseline_clin, baseline_group=baseline_group,
        baseline_train_clin=baseline_train_clin, baseline_train_group=baseline_train_group,
        baseline_test_clin=baseline_test_clin, baseline_test_group=baseline_test_group,
    )


def slice_layer(layer: DataLayer, mask) -> DataLayer:
    """按布尔/下标切一层，供亚组评估主模型。"""
    m = np.asarray(mask).astype(bool)
    if m.shape[0] != len(layer.label):
        raise ValueError(f"slice_layer mask N={m.shape[0]} ≠ layer N={len(layer.label)}")
    idx = np.where(m)[0]
    sg = getattr(layer, "subgroup", None)
    raw_ev = getattr(layer, "event", None)
    if raw_ev is None:
        ev = None
    elif len(idx) == 0:
        ev = np.array([], dtype=float)
    else:
        ev = np.asarray(raw_ev)[idx].copy()

    def _sg():
        if sg is None:
            return None
        return sg.iloc[idx].reset_index(drop=True)

    if len(idx) == 0:
        rad = layer.rad.iloc[0:0].reset_index(drop=True)
        clin = layer.clin.iloc[0:0].reset_index(drop=True)
        grp = layer.group.iloc[0:0].reset_index(drop=True)
        t = None if layer.time is None else np.array([], dtype=float)
        labs = [] if layer.sample_labels is not None else None
        return DataLayer(
            rad=rad, clin=clin, group=grp, label=np.array([], dtype=int),
            sample_labels=labs, time=t, subgroup=_sg(),
            landmark=_slice_landmark(getattr(layer, "landmark", None), idx),
            ipcw=_slice_ipcw(getattr(layer, "ipcw", None), idx),
            event=ev,
        )
    rad = layer.rad
    if rad is None or rad.empty:
        rad_sub = rad.iloc[0:0].reset_index(drop=True) if rad is not None else pd.DataFrame()
    else:
        rad_sub = rad.reset_index(drop=True).iloc[idx].reset_index(drop=True)
    t = None if layer.time is None else np.asarray(layer.time)[idx].copy()
    labs = [layer.sample_labels[i] for i in idx] if layer.sample_labels is not None else None
    return DataLayer(
        rad=rad_sub,
        clin=layer.clin.reset_index(drop=True).iloc[idx].reset_index(drop=True),
        group=layer.group.reset_index(drop=True).iloc[idx].reset_index(drop=True),
        label=np.asarray(layer.label)[idx],
        sample_labels=labs,
        time=t,
        subgroup=_sg(),
        landmark=_slice_landmark(getattr(layer, "landmark", None), idx),
        ipcw=_slice_ipcw(getattr(layer, "ipcw", None), idx),
        event=ev,
    )
