"""Path / root helpers for the RAD console (extracted from console.py).

``console.py`` keeps module-level ``GROK_ROOT`` / ``RAD_ROOT`` so existing tests
can monkeypatch them; wrappers below pass those live values into the pure
functions here.
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

# Live stage folder under GROK_ROOT (was PREPARING_DIRNAME / "preparing").
DRAFTING_DIRNAME = "drafting"
PREPARING_DIRNAME = DRAFTING_DIRNAME  # back-compat alias; prefer DRAFTING_DIRNAME
DEFAULT_GROK_ROOT = Path(r"D:\0Grok")  # lab machine fallback; always prefer GROK_ROOT env
STAGE_ROOT_NAMES = (
    "pre-submit", "in-review", "accepted",
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_CONSOLE_ORIGIN_PORT_LO = 8765
_CONSOLE_ORIGIN_PORT_HI = 8776


def grok_root(modules_root: Path) -> Path:
    """<grok> root that holds the stage folders (drafting/pre-submit/...).

    Order: ``GROK_ROOT`` env -> frozen lab default ``DEFAULT_GROK_ROOT``.

    Prefer ``GROK_ROOT``. The former ``0script`` tree was retired; do not
    revive a ``0script`` path probe. ``modules_root`` is kept for API
    stability (callers pass it) but is unused for root resolution.
    """
    _ = modules_root  # API stability; layout probe retired with 0script
    env = os.environ.get("GROK_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_GROK_ROOT


def grok_and_drafting(modules_root: Path) -> tuple[Path, Path]:
    grok = grok_root(modules_root)
    drafting = grok / DRAFTING_DIRNAME
    if not drafting.is_dir():
        raise FileNotFoundError(f"准备中课题根不存在，应为: {drafting}")
    return grok, drafting


def grok_and_preparing(modules_root: Path) -> tuple[Path, Path]:
    """Back-compat wrapper; prefer ``grok_and_drafting``."""
    return grok_and_drafting(modules_root)

def resolve_path(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def collect_project_roots(rad_root: Path, extra_roots: tuple[Path, ...]) -> list[Path]:
    roots = [rad_root.resolve()]
    for extra in extra_roots:
        if extra.is_dir():
            r = extra.resolve()
            if r not in roots:
                roots.append(r)
    return roots


def under_root(p: str | Path, *, grok_root: Path) -> Path:
    path = resolve_path(p)
    grok = grok_root.resolve()
    try:
        path.relative_to(grok)
    except ValueError as e:
        raise ValueError(f"路径不在 {grok} 下: {path}") from e
    return path


def under_project(proj: Path, p: str | Path, *, grok_root: Path) -> Path:
    proj = resolve_path(proj)
    path = Path(p)
    if not path.is_absolute():
        path = proj / path
    path = resolve_path(path)
    try:
        path.relative_to(proj)
    except ValueError as e:
        raise ValueError(f"路径不在项目目录 {proj} 下: {path}") from e
    return under_root(path, grok_root=grok_root)


def find_project_dir(
    name: str,
    *,
    grok_root: Path,
    project_roots: list[Path],
    explicit_dir: str = "",
) -> Path:
    raw = str(name).strip()
    if not raw:
        raise ValueError("未指定项目")
    if explicit_dir:
        p = Path(str(explicit_dir).strip())
        if p.is_dir():
            return under_root(p, grok_root=grok_root)
    env = (os.environ.get("RAD_PROJECT") or "").strip()
    if env:
        ep = Path(env)
        if ep.is_dir() and ep.name.lower() == raw.lower():
            return under_root(ep, grok_root=grok_root)
    p = Path(raw)
    if p.is_absolute() or (len(raw) >= 2 and raw[1] == ":"):
        p = under_root(p, grok_root=grok_root)
        if not p.is_dir():
            raise ValueError(f"项目不存在: {name}")
        return p
    for root in project_roots:
        cand = root / raw
        if cand.is_dir():
            return cand
    raise ValueError(f"项目不存在: {name}")


def allow_browser_origin(origin: str) -> bool:
    """True only for http loopback console pages (not opaque/null origins)."""
    o = (origin or "").strip()
    if not o or o.lower() == "null":
        return False
    parts = urlsplit(o)
    if parts.scheme != "http":
        return False
    if parts.username or parts.password:
        return False
    if parts.fragment:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or host not in _LOOPBACK_HOSTS:
        return False
    port = parts.port
    if port is None:
        return True
    if port == 80:
        return True
    return _CONSOLE_ORIGIN_PORT_LO <= port <= _CONSOLE_ORIGIN_PORT_HI


def cors_allow_origin(origin: str, *, allow_null: bool = False) -> str | None:
    """Access-Control-Allow-Origin value, or None when JS must not read the body."""
    o = (origin or "").strip()
    if allow_browser_origin(o):
        return o
    if allow_null and o.lower() == "null":
        return "null"
    return None
