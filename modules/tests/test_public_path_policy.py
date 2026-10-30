"""C1: public-path policy — no accidental absolute lab-path sprawl.

Allowlist the documented lab exception in modules.console.paths.DEFAULT_GROK_ROOT.
Everything else under the repo should prefer env / relative / example paths.
Lab topology SSOT remains MY-SKILLS lab-paths.yaml (do not reinvent here).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Documented lab exception only (C1).
ALLOW_FILES = {
    "modules/console/paths.py",
}

# Absolute Windows drive paths that look like this lab (not generic C:\Windows).
LAB_ABS = re.compile(
    r"[A-Za-z]:\\(?:0Grok|Users\\[^\\]+\\(?:0Grok|Documents\\GitHub))",
    re.I,
)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "egg-info"}
SUFFIXES = {".py", ".md", ".txt", ".toml", ".ini", ".yml", ".yaml", ".cfg", ".json", ".html", ".js", ".css"}


class TestPublicPathPolicy(unittest.TestCase):
    def test_no_lab_absolute_path_sprawl(self) -> None:
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUFFIXES:
                continue
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            rel = path.relative_to(ROOT)
            if rel.as_posix() in ALLOW_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if LAB_ABS.search(line):
                    offenders.append(f"{rel.as_posix()}:{i}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "absolute lab paths outside the DEFAULT_GROK_ROOT allowlist:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
