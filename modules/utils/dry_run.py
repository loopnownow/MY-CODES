"""Cross-module dry-run helpers.

Enable via settings ``DRY_RUN=True``, env ``MODULES_DRY_RUN=1``, or
``set_dry_run(True)`` in tests. When enabled, entry points should print the
planned work and skip writes / heavy fits.
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

_FORCE: Optional[bool] = None


def set_dry_run(flag: Optional[bool]) -> None:
    """Test/cli override. Pass None to clear and fall back to cfg/env."""
    global _FORCE
    _FORCE = None if flag is None else bool(flag)


def enabled(explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    if _FORCE is not None:
        return bool(_FORCE)
    env = (os.environ.get("MODULES_DRY_RUN") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        from modules import config as cfg
        return bool(getattr(cfg, "DRY_RUN", False))
    except Exception:
        return False


def note(action: str) -> None:
    print(f"  ◆ DRY-RUN: {action}", flush=True)


def plan(module: str, actions: Iterable[str]) -> None:
    print(f"  ◆ DRY-RUN [{module}]", flush=True)
    for a in actions:
        print(f"      - {a}", flush=True)


def skip_write(path, *, what: str = "write") -> bool:
    """If dry-run, log and return True (caller must not write)."""
    if not enabled():
        return False
    note(f"skip {what}: {path}")
    return True


def exit_after_plan(module: str, actions: Iterable[str]) -> bool:
    """If dry-run, print plan and return True so caller can return early."""
    if not enabled():
        return False
    plan(module, actions)
    return True
