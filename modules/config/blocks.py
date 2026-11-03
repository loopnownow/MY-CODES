"""模态块规格（一块一张工作表）的规范化、校验与读取。

配置来源：settings.ini ``[blocks] MODALITY_BLOCKS``（列表，一项一块）。
块名 = 工作表名；分数列默认 ``RadScore_<块名>``。

字段
----
name     块名（必填，唯一，不区分大小写）
sheet    工作表名（空 = 同 name）
id_col   ID 列（空 = 用 RAD_ID_COL）
method   lasso | logistic；空 = 该块不选方法（显式跳过）
enabled  是否启用
prep     预处理覆盖：{"icc": bool, "corr": bool, "corr_cutoff": float}
params   方法参数覆盖：{"lasso": {...}, "logistic": {...}}
score    分数列名（空 = RadScore_<name>）

非法值：raise ValueError（不静默回退）。

运行: 由 modules.stats.data_io / modules.stats.selectors / modules.pipeline /
modules.console.schema_ui 导入，不单独执行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

BLOCK_METHODS: tuple[str, ...] = ("lasso", "logistic")

COMBINED_MODES: tuple[str, ...] = ("per_block", "joint")

# 块类型：rad=数值组学矩阵；clin=临床式（文本列先按 TEXT_ENCODE 编码）
BLOCK_KINDS: tuple[str, ...] = ("rad", "clin")


@dataclass(frozen=True)
class BlockSpec:
    """一个模态块的运行规格。"""

    name: str
    sheet: str = ""
    id_col: str = ""
    method: str = ""
    enabled: bool = True
    kind: str = "rad"
    prep: Dict[str, Any] = field(default_factory=dict)
    params: Dict[str, Any] = field(default_factory=dict)
    score: str = ""

    @property
    def score_name(self) -> str:
        """分数列名：空 score 时用 RadScore_<块名>。"""
        return self.score or f"RadScore_{self.name}"

    @property
    def resolved_sheet(self) -> str:
        """实际工作表名：空 sheet 时同块名。"""
        return self.sheet or self.name


def _as_mapping(raw: Any, where: str) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必须是对象（dict），收到 {type(raw).__name__}")
    return dict(raw)


def _norm_text(raw: Any) -> str:
    return str(raw).strip() if raw is not None else ""


def _norm_bool(raw: Any, default: bool) -> bool:
    if raw is None or raw == "":
        return default
    if isinstance(raw, bool):
        return raw
    tok = str(raw).strip().lower()
    if tok in ("true", "1", "yes", "y", "on"):
        return True
    if tok in ("false", "0", "no", "n", "off"):
        return False
    raise ValueError(f"布尔值无法识别: {raw!r}")


def normalize_blocks(raw: Any = None) -> List[BlockSpec]:
    """把 ini/控制台给的块列表规范化。

    参数
    ----
    raw : list|dict|None
        None = 读 cfg.MODALITY_BLOCKS；空列表 = 没有显式块（返回 []）。

    返回
    ----
    list[BlockSpec]，按配置顺序。

    异常
    ----
    ValueError：块名缺失/重复、method 非法、非对象项。
    """
    if raw is None:
        from modules import config as cfg

        raw = getattr(cfg, "MODALITY_BLOCKS", None)
    if raw is None or raw == "":
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError(
            f"MODALITY_BLOCKS 必须是列表（每项一个块对象），收到 {type(raw).__name__}"
        )
    out: List[BlockSpec] = []
    seen: Dict[str, int] = {}
    for i, item in enumerate(raw):
        where = f"MODALITY_BLOCKS[{i}]"
        spec = _as_mapping(item, where)
        name = _norm_text(spec.get("name"))
        if not name:
            raise ValueError(f"{where} 缺少 name（块名，通常等于工作表名）")
        key = name.lower()
        if key in seen:
            raise ValueError(f"{where} 块名重复: {name!r}（与第 {seen[key]} 项相同）")
        seen[key] = i
        method = _norm_text(spec.get("method")).lower()
        if method and method not in BLOCK_METHODS:
            raise ValueError(
                f"{where} method={spec.get('method')!r} 无效；允许: "
                + " | ".join(BLOCK_METHODS)
                + "（留空 = 该块跳过）"
            )
        prep = _as_mapping(spec.get("prep"), f"{where}.prep")
        kind = _norm_text(spec.get("kind")).lower() or "rad"
        if kind not in BLOCK_KINDS:
            raise ValueError(
                f"{where} kind={spec.get('kind')!r} 无效；允许: " + " | ".join(BLOCK_KINDS)
            )
        raw_params = spec.get("params")
        if raw_params is None:
            params: Dict[str, Any] = {}
            for m in BLOCK_METHODS:
                if isinstance(spec.get(m), dict):
                    params[m] = dict(spec[m])
        else:
            params = _as_mapping(raw_params, f"{where}.params")
        out.append(
            BlockSpec(
                name=name,
                sheet=_norm_text(spec.get("sheet")),
                id_col=_norm_text(spec.get("id_col")),
                method=method,
                enabled=_norm_bool(spec.get("enabled"), True),
                kind=kind,
                prep=prep,
                params=params,
                score=_norm_text(spec.get("score")),
            )
        )
    return out


def explicit_blocks() -> List[BlockSpec]:
    """读 cfg.MODALITY_BLOCKS；空 = 没有显式块（走旧的单块路径）。"""
    return normalize_blocks(None)


def active_blocks(specs: Optional[Iterable[BlockSpec]] = None) -> List[BlockSpec]:
    """启用且有方法的块（method 空的块由调用方显式跳过）。"""
    items = list(specs) if specs is not None else explicit_blocks()
    return [b for b in items if b.enabled and b.method]


def skipped_blocks(specs: Optional[Iterable[BlockSpec]] = None) -> List[BlockSpec]:
    """启用但没选方法的块。"""
    items = list(specs) if specs is not None else explicit_blocks()
    return [b for b in items if b.enabled and not b.method]


def combined_mode(raw: Any = None) -> str:
    """Combined 主模型口径：per_block（默认）| joint。非法值 raise。"""
    if raw is None:
        from modules import config as cfg

        raw = getattr(cfg, "COMBINED_MODE", "per_block")
    tok = _norm_text(raw).lower().replace("-", "_")
    if not tok:
        return "per_block"
    aliases = {
        "perblock": "per_block",
        "block": "per_block",
        "each": "per_block",
        "joint": "joint",
        "all": "joint",
    }
    mode = tok if tok in COMBINED_MODES else aliases.get(tok)
    if mode not in COMBINED_MODES:
        raise ValueError(
            f"COMBINED_MODE={raw!r} 无效；允许: " + " | ".join(COMBINED_MODES)
        )
    return mode


def combined_block_names(scores: Iterable[str]) -> List[str]:
    """按 COMBINED_BLOCKS / COMBINED_EXCLUDE 挑选进 Combined 的分数名。"""
    from modules import config as cfg

    have = [str(s) for s in scores]
    keep = [_norm_text(x) for x in (getattr(cfg, "COMBINED_BLOCKS", None) or [])]
    keep = [x for x in keep if x]
    keep_lc = {x.lower() for x in keep}
    drop = {_norm_text(x).lower() for x in (getattr(cfg, "COMBINED_EXCLUDE", None) or [])}
    drop.discard("")
    picked: List[str] = []
    for name in have:
        if keep and name not in keep and name.lower() not in keep_lc:
            continue
        if name.lower() in drop:
            continue
        if name not in picked:
            picked.append(name)
    return picked
