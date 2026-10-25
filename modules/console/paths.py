"""Path / root helpers for the RAD console (extracted from console.py).

``console.py`` keeps module-level ``GROK_ROOT`` / ``RAD_ROOT`` so existing tests
can monkeypatch them; wrappers below pass those live values into the pure
functions here.
"""
from __future__ import annotations

import os
from pathlib import Path

# Live stage folder under GROK_ROOT (was PREPARING_DIRNAME / "preparing").
DRAFTING_DIRNAME = "drafting"
PREPARING_DIRNAME = DRAFTING_DIRNAME  # back-compat alias; prefer DRAFTING_DIRNAME
DEFAULT_GROK_ROOT = Path(r"D:\0Grok")  # frozen lab default; GROK_ROOT overrides
STAGE_ROOT_NAMES = (
    "pre-submit", "in-review", "accepted",
)


def grok_root(modules_root: Path) -> Path:
    """<grok> root that holds the stage folders (drafting/pre-submit/...).

    Order: ``GROK_ROOT`` env -> legacy ``<grok>/0script/<coarse>/modules``
    -> frozen lab default. Never raises: the package may live outside
    ``0script`` (e.g. ``0doc/GitHub/stats-analysis``).
    """
    env = os.environ.get("GROK_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    script_root = modules_root.parent.parent
    if script_root.name == "0script":
        return script_root.parent
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
    """file:// 起始页 Origin 为 null；控制台页为 127.0.0.1。"""
    o = (origin or "").strip()
    if not o or o == "null":
        return True
    return o.startswith("http://127.0.0.1") or o.startswith("http://localhost")
