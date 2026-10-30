"""RAD 共享算法库（本目录即包）。

PYTHONPATH=<repo root> 后: python -m modules.pipeline
可用 RAD_MODULES 或 settings.ini 的 MODULES_DIR 指向另一份拷贝。
"""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "4.3.1"

_HERE = Path(__file__).resolve().parent


def _override() -> Path | None:
    """RAD_MODULES or the project's MODULES_DIR may point at another copy; an invalid value is an error."""
    cands: list[tuple[str, Path]] = []
    env = (os.environ.get("RAD_MODULES") or "").strip()
    if env:
        cands.append(("RAD_MODULES", Path(env)))
    proj = (os.environ.get("RAD_PROJECT") or "").strip()
    if proj:
        from modules.config.ini_io import read_ini, resolve_settings_ini
        ini = resolve_settings_ini(Path(proj))
        if ini.is_file():
            raw = str(read_ini(ini).get("MODULES_DIR") or "").strip()
            if raw:
                cands.append((f"MODULES_DIR in {ini}", Path(raw)))
    here = _HERE.resolve()
    for src, p in cands:
        if not p.is_dir():
            raise RuntimeError(f"{src} 不是现存目录: {p}")
        if p.resolve() != here:
            return p
    return None


_ov = _override()
if _ov is not None and str(_ov) not in __path__:
    import warnings

    warnings.warn(
        f"modules loaded from non-canonical path {_ov} "
        f"(package dir {_HERE}; version {__version__}). "
        "Prefer the stats-analysis repo modules/ as the sole SSOT; "
        "record MODULES_DIR / RAD_MODULES provenance in run logs.",
        UserWarning,
        stacklevel=1,
    )
    __path__.insert(0, str(_ov))

