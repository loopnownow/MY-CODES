"""Hygiene guard: no UTF-8 BOM, and every .py parses (a BOM breaks compile(str))."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUFFIXES = {".py", ".html", ".js", ".css", ".toml"}
BOM = b"\xef\xbb\xbf"


class Hygiene(unittest.TestCase):
    def test_no_bom(self):
        bad = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
               if p.is_file() and p.suffix in SUFFIXES and ".git" not in p.parts
               and p.read_bytes().startswith(BOM)]
        self.assertEqual(bad, [])

    def test_python_parses(self):
        for p in ROOT.rglob("*.py"):
            if ".git" not in p.parts:
                ast.parse(p.read_text(encoding="utf-8"), str(p))


if __name__ == "__main__":
    unittest.main()
