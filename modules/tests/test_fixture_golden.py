"""Fast structural checks for the bundled `test/` QC fixture.

The 80-second end-to-end run is driven by `python -B test/run_golden.py --refresh`
(see test/README.md); this module only guards what is committed:
the golden schema, its self-consistency, and the portability of the fixture config.

Run: python -B -m pytest modules/tests/test_fixture_golden.py -q
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from modules.tests import golden_tools as gt

REPO = Path(__file__).resolve().parents[2]
TEST_DIR = REPO / "test"
GOLDEN = TEST_DIR / "expected" / "golden.json"
SETTINGS = TEST_DIR / "ref" / "settings.ini"
OVERLAY = TEST_DIR / "ref" / ".rad_console_overlay.json"
FIXTURE_XLSX = TEST_DIR / "test.xlsx"
RUNNER = TEST_DIR / "run_golden.py"

EXPECTED_TABLES = {
    "table1-all",
    "table2-all",
    "table1-train-test",
    "table2-train-test",
    "uni-train",
    "table2-lasso-train",
    "combined-coef-train",
    "roc-metrics-train-test",
    "hosmer-lemeshow-internal",
}
ABS_WINDOWS = re.compile(r"[A-Za-z]:\\")


def test_fixture_files_exist() -> None:
    for path in (FIXTURE_XLSX, SETTINGS, OVERLAY, GOLDEN, RUNNER):
        assert path.is_file(), f"missing {path}"


def test_golden_has_every_table() -> None:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(payload["tables"]) == EXPECTED_TABLES
    assert payload["meta"]["run_status"] == "INCOMPLETE"
    for tid, table in payload["tables"].items():
        assert table["rows"], f"{tid} has no rows"
        assert table["header"], f"{tid} has no header"


def test_golden_is_self_consistent() -> None:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert gt.compare(payload, payload) == []


def test_roc_table_uses_looser_tolerance() -> None:
    assert gt.TABLE_TOL["roc-metrics-train-test"] == 1e-6


def test_fixture_config_is_portable() -> None:
    settings = SETTINGS.read_text(encoding="utf-8")
    assert not ABS_WINDOWS.search(settings), "settings.ini must not carry an absolute path"
    assert 'MODULES_DIR = ""' in settings
    assert 'INPUT = "test.xlsx"' in settings

    overlay = json.loads(OVERLAY.read_text(encoding="utf-8-sig"))
    assert not Path(str(overlay["data"])).is_absolute()
    assert overlay["project"] == "test"


def test_runner_points_at_the_committed_golden() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert 'EXPECTED = PROJECT_DIR / "expected" / "golden.json"' in source
    assert 'RESULT_HTML = Path("Group") / "ResultsGroup.html"' in source


def test_gitignore_covers_run_products() -> None:
    ignored = (TEST_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    for pattern in ("Group/", "ref/cache/", "*.png", "false_classification.xlsx"):
        assert pattern in ignored


@pytest.mark.parametrize("tid", sorted(EXPECTED_TABLES))
def test_table_rows_are_strings(tid: str) -> None:
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for row in payload["tables"][tid]["rows"]:
        assert all(isinstance(cell, str) for cell in row)
