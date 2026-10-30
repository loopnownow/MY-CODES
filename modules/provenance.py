"""MODULES_DIR / RAD_MODULES provenance for run logs and HTML.

Public path policy (C1): prefer GROK_ROOT / STATS_ROOT / RAD_MODULES env and
project settings.ini; do not hard-code lab drive letters outside the documented
DEFAULT_GROK_ROOT lab exception in modules.console.paths.
"""
from __future__ import annotations

import os
import subprocess
import warnings
from pathlib import Path
from typing import Any


def git_short_commit(root: Path) -> str:
    """Best-effort short SHA for *root* (empty when not a git work tree)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if out.returncode == 0:
            return (out.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _strict_from_env() -> bool:
    raw = (os.environ.get("RAD_MODULES_STRICT") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def resolve_active_modules(
    package_dir: Path,
) -> tuple[str, Path, bool]:
    """Return (source_label, active_modules_dir, is_canonical).

    Canonical = the imported package directory (this checkout's modules/).
    RAD_MODULES / MODULES_DIR that resolve elsewhere are non-canonical.
    """
    here = package_dir.resolve()
    cands: list[tuple[str, Path]] = []
    env = (os.environ.get("RAD_MODULES") or "").strip()
    if env:
        cands.append(("RAD_MODULES", Path(env)))
    proj = (os.environ.get("RAD_PROJECT") or "").strip()
    if proj:
        try:
            from modules.config.ini_io import read_ini, resolve_settings_ini

            ini = resolve_settings_ini(Path(proj))
            if ini.is_file():
                raw = str(read_ini(ini).get("MODULES_DIR") or "").strip()
                if raw:
                    cands.append((f"MODULES_DIR in {ini}", Path(raw)))
        except Exception:
            pass
    for src, p in cands:
        if not p.is_dir():
            raise RuntimeError(f"{src} is not an existing directory: {p}")
        resolved = p.resolve()
        if resolved != here:
            return src, resolved, False
    return "package", here, True


def collect(
    package_dir: Path,
    *,
    version: str,
) -> dict[str, Any]:
    """Record code root + version + commit for the active modules tree."""
    source, active, canonical = resolve_active_modules(package_dir)
    code_root = active.parent if active.name.lower() == "modules" else active
    stats_env = (os.environ.get("STATS_ROOT") or "").strip()
    return {
        "source": source,
        "modules_dir": str(active),
        "code_root": str(code_root),
        "version": version,
        "commit": git_short_commit(code_root),
        "canonical": canonical,
        "stats_root_env": stats_env,
    }


def format_banner(rec: dict[str, Any]) -> str:
    commit = rec.get("commit") or "(no git)"
    flag = "canonical" if rec.get("canonical") else "NON-CANONICAL"
    lines = [
        "  modules provenance:",
        f"    source={rec.get('source')}  status={flag}",
        f"    modules_dir={rec.get('modules_dir')}",
        f"    code_root={rec.get('code_root')}",
        f"    version={rec.get('version')}  commit={commit}",
    ]
    if rec.get("stats_root_env"):
        lines.append(f"    STATS_ROOT={rec['stats_root_env']}")
    return "\\n".join(lines)


def format_html_note(rec: dict[str, Any]) -> str:
    commit = rec.get("commit") or "no-git"
    flag = "canonical" if rec.get("canonical") else "NON-CANONICAL"
    return (
        '<p class="note" id="modules-provenance">'
        f"modules source={rec.get('source')} ({flag}); "
        f"version={rec.get('version')}; commit={commit}; "
        f"code_root={rec.get('code_root')}"
        "</p>"
    )


def enforce(rec: dict[str, Any], *, strict: bool | None = None) -> None:
    """Warn on non-canonical; raise when RAD_MODULES_STRICT is set."""
    if rec.get("canonical"):
        return
    msg = (
        f"modules loaded from non-canonical path {rec.get('modules_dir')} "
        f"(source={rec.get('source')}; version={rec.get('version')}; "
        f"commit={rec.get('commit') or 'no-git'}). "
        "Prefer this repo's modules/ as the sole SSOT; set RAD_MODULES_STRICT=1 "
        "to block non-canonical roots."
    )
    if strict is None:
        strict = _strict_from_env()
    if strict:
        raise RuntimeError(msg)
    warnings.warn(msg, UserWarning, stacklevel=3)


def emit_to_stdout(package_dir: Path, *, version: str) -> dict[str, Any]:
    """Collect, enforce, print banner; return the record (for HTML hook)."""
    rec = collect(package_dir, version=version)
    enforce(rec)
    print(format_banner(rec), flush=True)
    return rec
