# -*- coding: utf-8 -*-
"""tidy_paths — 统一路径解析。

合并来源：`lxj_AN/*.py` 里的 `BASE` 常量 + `ssd/ssd_paths.py`。

目录约定（见 `D:\\0Grok\\drafting\\lxj_AN\\ref\\AGENTS.md`）::

    脚本   → D:\\0Grok\\0doc\\_code\\02-data-processing/clean-data\\   本包
    数据   → D:\\0Grok\\<阶段>\\<子工程>\\         如 D:\\0Grok\\drafting\\lxj_AN
    临时   → D:\\0del\\                            所有子工程共用，只搬不删（DEL_ROOT 可覆盖）

ORAD_ROOT（准备中课题根）::

    ZERAD_ROOT → ORAD_ROOT → MS_ORAD_ROOT → <grok>\\drafting（不存在即报错）

子工程根：PROFILE_ENV 环境变量 → 设了 ORAD_ROOT 类变量时在该目录下找，
否则在 <grok> 各阶段目录（drafting/pre-submit/…）中按 PROFILE_DIRNAME 查找；
找不到或重名即报错。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional, Union

_REPO_ROOT = Path(__file__).resolve().parents[2]  # tools/clean-data -> 仓根
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from modules.console.paths import DEFAULT_GROK_ROOT  # C1: 唯一文档化 lab 默认
GROK_DEFAULT = Path(os.environ.get("GROK_ROOT") or DEFAULT_GROK_ROOT)

#: 准备中课题根目录名（<grok>\drafting）
ORAD_DIRNAME = "drafting"  # was preparing
#: 课题所在的阶段目录（同 MY-CODES/modules/console/paths.py）
STAGE_NAMES = (
    "drafting", "pre-submit", "in-review", "accepted",
)


def get_orad_root() -> Path:
    """返回准备中课题根目录：环境变量优先，否则 <grok>/drafting（不存在即报错）。"""
    raw = (
        os.environ.get("ZERAD_ROOT")
        or os.environ.get("ORAD_ROOT")
        or os.environ.get("MS_ORAD_ROOT")
    )
    if raw:
        return Path(raw)
    grok = Path(os.environ.get("GROK_ROOT") or GROK_DEFAULT)
    p = grok / ORAD_DIRNAME
    if not p.is_dir():
        raise FileNotFoundError(f"准备中课题根不存在，应为: {p}")
    return p


ORAD_ROOT: Path = get_orad_root()
_ORAD_FROM_ENV = bool(os.environ.get("ZERAD_ROOT") or os.environ.get("ORAD_ROOT") or os.environ.get("MS_ORAD_ROOT"))


def _resolve_grok_root(orad: Path) -> Path:
    explicit = os.environ.get("GROK_ROOT")
    if explicit:
        return Path(explicit)
    return orad.parent if orad.name == ORAD_DIRNAME else orad


GROK_ROOT: Path = _resolve_grok_root(ORAD_ROOT)
#: 全子工程共用的临时/删除区（2026-10-23 起与 `{DEL}` 对齐为 `D:\0del`；环境变量 DEL_ROOT 可覆盖）
DEL_ROOT: Path = Path(os.environ.get("DEL_ROOT") or r"D:\0del")

# ── 子工程根目录 ────────────────────────────────────────────────
#: profile → 环境变量名（用户在外部覆盖数据位置）
PROFILE_ENV = {
    "lxj_AN": "LXJ_AN_ROOT",
    "ssd": "SSD_ROOT",
    "ssd_dti_ivim": "SSD_DTI_IVIM_ROOT",
    "ssd_dki": "SSD_DKI_ROOT",
    "yt_RDL": "YT_RDL_ROOT",
    "lxf_PP": "LXF_PP_ROOT",
    "lya_WML": "LYA_WML_ROOT",
    "wkl_UGIB": "WKL_UGIB_ROOT",
    "zyp_OV": "ZYP_OV_ROOT",
}
#: profile → 课题目录名（位于 <grok>\<阶段> 下）
PROFILE_DIRNAME = {
    "lxj_AN": "lxj_AN",
    "ssd": "jfl_SSD_ASL",
    "ssd_dti_ivim": "jfl_SSD_DTI_IVIM",
    "ssd_dki": "jfl_SSD_DKI",
    "yt_RDL": "yt_RDL",
    "lxf_PP": "lxf_RP",  # downsample 源表 lxf_LG.xlsx 在 lxf_RP；产出 lxf_PP.xlsx
    "lya_WML": "lya_WML",
    "wkl_UGIB": "wkl_UGIB",
    "zyp_OV": "zyp_OV",
}


def project_root(profile: str) -> Path:
    """子工程根目录：环境变量优先，否则在各阶段目录中查找（找不到或重名即报错）。"""
    key = PROFILE_ENV.get(profile)
    if key:
        raw = os.environ.get(key)
        if raw:
            return Path(raw)
    if profile not in PROFILE_DIRNAME:
        raise KeyError(f"unknown profile: {profile!r} (known: {sorted(PROFILE_DIRNAME)})")
    name = PROFILE_DIRNAME[profile]
    roots = [ORAD_ROOT] if _ORAD_FROM_ENV else [GROK_ROOT / s for s in STAGE_NAMES]
    hits = [r / name for r in roots if (r / name).is_dir()]
    if not hits:
        raise FileNotFoundError(f"课题目录不存在: {name}（已查 {', '.join(str(r) for r in roots)}）")
    if len(hits) > 1:
        raise FileNotFoundError(f"课题目录重名: {name} → {', '.join(str(h) for h in hits)}")
    return hits[0]


def source_dir(profile: str) -> Path:
    """原始文件目录：``<子工程根>/ref``。"""
    return project_root(profile) / "ref"


def del_path(*parts: Union[str, Path]) -> Path:
    """``D:\\0del\\<parts...>``（DEL_ROOT 下按 parts 逐级拼接）。"""
    p = DEL_ROOT
    for x in parts:
        p = p / str(x)
    return p


def ensure_dir(path: Path, dry_run: bool = False) -> Path:
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)
    return path


def backup_to_del(
    src: Union[str, Path],
    name: Optional[str] = None,
    subdir: Optional[str] = None,
    move: bool = False,
    overwrite: bool = False,
) -> Optional[Path]:
    """把文件搬到/复制到 0del。默认「仅首次备份」，已存在则原样保留。

    与旧 `ssd/impute_ssd_dti_ivim.py` 的 ``BACKUP`` 语义一致：重复运行不会
    覆盖第一次的备份（那才是插补前的原始态）。

    Returns
    -------
    Path | None
        目标路径；``src`` 不存在或已存在同名备份且非 overwrite 时返回 None。
    """
    src = Path(src)
    if not src.exists():
        return None
    dst_dir = del_path(subdir) if subdir else DEL_ROOT
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / (name or src.name)
    if dst.exists() and not overwrite:
        return None
    if move:
        shutil.move(str(src), str(dst))
    else:
        shutil.copy2(src, dst)
    return dst
