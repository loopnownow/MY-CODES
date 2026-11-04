# -*- coding: utf-8 -*-
"""Flowchart tab: manuscript file list and Materials-and-Methods prefill.

The console flowchart page (strobe/index.html) asks this module which manuscript
files sit in the project root and what the chosen one states, so the left form
can be prefilled. Counts that the manuscript does not state are listed in
`missing` and never invented.

    from modules.console import flow_fill
    flow_fill.list_manuscripts("fyh_CAC")          # {"files": [...], "default": "..."}
    flow_fill.fill_flow_spec("fyh_CAC", "Manuscript_fyh_CAC_V1.docx")
"""
from __future__ import annotations

import re
from pathlib import Path

MANUSCRIPT_KEYWORD = "manuscript"
SUFFIXES = (".docx",)
_VERSION = re.compile(r"[_\-\s][vV](\d+)(?=[_\-\s.]|$)")


def _version(name: str) -> int:
    match = _VERSION.search(name)
    return int(match.group(1)) if match else 0


def scan_manuscripts(proj: Path) -> list[dict]:
    """Root-level .docx files of a project, manuscript-like and newest version first."""
    files = []
    for path in sorted(proj.iterdir(), key=lambda p: p.name.lower()):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
                "is_manuscript": MANUSCRIPT_KEYWORD in path.name.lower(),
            }
        )
    files.sort(
        key=lambda f: (
            not f["is_manuscript"],
            -_version(f["name"]),
            -f["mtime"],
            f["name"].lower(),
        )
    )
    return files


def default_manuscript(files: list[dict]) -> str:
    """First manuscript-like file; otherwise the first .docx; '' when there is none."""
    for entry in files:
        if entry["is_manuscript"]:
            return entry["name"]
    return files[0]["name"] if files else ""


def flow_spec_from_docx(path: Path) -> dict:
    """Prefill spec from one .docx; raises when the file or its Methods is unusable."""
    from modules.stats.flow_spec_text import (
        docx_paragraphs,
        methods_section,
        spec_from_text,
    )

    if path.suffix.lower() != ".docx":
        raise ValueError(f"仅支持 .docx 手稿，请先另存为 .docx：{path.name}")
    out = spec_from_text(methods_section(docx_paragraphs(path)))
    out["file"] = path.name
    return out


def list_manuscripts(project: str, explicit_dir: str = "") -> dict:
    """Project-root .docx list plus the file the page should select by default."""
    from modules.console.settings_layer import project_dir

    proj = project_dir(project, explicit_dir)
    files = scan_manuscripts(proj)
    return {"project": project, "files": files, "default": default_manuscript(files)}


def fill_flow_spec(project: str, name: str, explicit_dir: str = "") -> dict:
    """Prefill spec for one project-root manuscript. Path must stay inside the project."""
    from modules.console.settings_layer import project_dir, under_project

    proj = project_dir(project, explicit_dir)
    path = under_project(proj, name)
    if not path.is_file():
        raise ValueError(f"文件不存在：{name}")
    return flow_spec_from_docx(path)
