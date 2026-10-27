"""One version number for the package and the metadata.

`modules.__version__` is what projects see at runtime; `pyproject.toml` is what
`pip install` records. The 2026-10-27 QC found them three minor releases apart
(0.1.0 vs 4.3.1) and the MY-SKILLS prose still on 4.3.0, so the two are pinned
together here.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import modules

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)


def _pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_pyproject_matches_module_version():
    assert _pyproject_version() == modules.__version__


def test_version_is_a_release_triple():
    assert re.fullmatch(r"\d+\.\d+\.\d+", modules.__version__), modules.__version__
