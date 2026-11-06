# -*- coding: utf-8 -*-
"""Dash-cadence contract for tools/orchestrator/style_lint.py (t1684u).

Aligns with MY-SKILLS #303: U+2013 en dash allowed; U+2014 em dash and spaced "--" banned.
"""
from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILLS = Path(
    os.environ.get("SKILLS_ROOT") or os.environ.get("MY_SKILLS") or REPO.parent / "MY-SKILLS"
).resolve()
if not (SKILLS / "00_orchestrator").is_dir():
    raise SystemExit(
        f"MY-SKILLS checkout not found at {SKILLS}; set SKILLS_ROOT (no silent fallback)"
    )
SCRIPT = REPO / "tools" / "orchestrator" / "style_lint.py"

os.environ["SKILLS_ROOT"] = str(SKILLS)

spec = importlib.util.spec_from_file_location("style_lint_under_test", SCRIPT)
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)

FILLER = "We studied a cohort of patients with cirrhosis and recorded outcomes over two years."


def doc(section: str, body: str) -> str:
    return "## %s\n\n%s %s\n" % (section, body, FILLER)


def failed(result, group: str) -> bool:
    return result["groups"][group]["failed"]


class StyleLintDashTests(unittest.TestCase):
    def test_en_dash_ranges_allowed(self):
        r = sl.lint(doc("Discussion", "Lesions of 5\u201310 mm and 20\u201330 mm were seen."))
        self.assertFalse(failed(r, "dash_cadence"))

    def test_en_dash_ci_allowed(self):
        r = sl.lint(doc("Discussion", "AUC was 0.85 (95% CI: 0.80\u20130.93) in the test set."))
        self.assertFalse(failed(r, "dash_cadence"))

    def test_em_dash_still_fails(self):
        self.assertTrue(failed(sl.lint(doc("Discussion", "The model \u2014 trained early failed.")), "dash_cadence"))

    def test_spaced_double_hyphen_still_fails(self):
        self.assertTrue(failed(sl.lint(doc("Discussion", "The model -- trained early failed.")), "dash_cadence"))

    def test_ascii_hyphen_ranges_are_fine(self):
        r = sl.lint(doc("Discussion", "Lesions of 5-10 mm and 20-30 mm were seen."))
        self.assertFalse(failed(r, "dash_cadence"))

    def test_emdash_pattern_omits_en_dash(self):
        self.assertIsNone(sl._EMDASH.search("5\u201310"))
        self.assertIsNotNone(sl._EMDASH.search("\u2014"))
        self.assertIsNotNone(sl._EMDASH.search(" a -- b "))
        self.assertIsNone(sl._EM_DASH_CHAR.search("\u2013"))
        self.assertIsNotNone(sl._EM_DASH_CHAR.search("\u2014"))


if __name__ == "__main__":
    unittest.main()