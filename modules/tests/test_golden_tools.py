"""Unit coverage for the golden parity harness (``modules.tests.golden_tools``).

Fixtures here are synthetic HTML — the real parity run uses an external project
fixture through the CLI, so no patient data enters the repo.

Run: python -B -m pytest modules/tests/test_golden_tools.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from modules.tests import golden_tools as gt

HTML = """<!DOCTYPE html>
<html><body>
<h1>ResultsDemo</h1>
<h2>Run status: COMPLETE</h2>
<h2>B. Table 1</h2>
<table class="threeline" id="table1-all">
<caption>Overall</caption>
<thead><tr><th>Variable</th><th>Group=9</th><th>0=11</th><th>P value</th></tr></thead>
<tbody>
<tr><td class="col-first">Age</td><td>54.30&plusmn;12.51</td><td>54.59&plusmn;11.22</td><td>0.866</td></tr>
<tr><td class="col-first">Sex</td><td>52.7%</td><td>52.3%</td><td>0.960</td></tr>
<tr><td class="col-first">Volume</td><td>541.31&plusmn;157.17</td><td>439.90&plusmn;171.59</td><td>&lt;0.001***</td></tr>
<tr><td class="col-first">Empty</td><td>&mdash;</td><td>&mdash;</td><td>&mdash;</td></tr>
</tbody>
</table>
<h4>ROC summary</h4>
<table class="threeline" id="roc-metrics-train-test">
<caption>ROC summary</caption>
<thead><tr><th>Split</th><th>Model</th><th>AUC</th><th>95% CI</th></tr></thead>
<tbody>
<tr><td>train</td><td>Combined</td><td>0.688</td><td>(0.610-0.764)</td></tr>
</tbody>
</table>
</body></html>
"""

AGE_CELL = "54.30&plusmn;12.51"
BOUND_CELL = "&lt;0.001***"


@pytest.fixture()
def html_path(tmp_path: Path) -> Path:
    p = tmp_path / "ResultsDemo.html"
    p.write_text(HTML, encoding="utf-8")
    return p


def test_extract_reads_meta_and_tables(html_path: Path) -> None:
    data = gt.extract(html_path)
    assert data["meta"]["run_status"] == "COMPLETE"
    assert data["meta"]["table_ids"] == ["roc-metrics-train-test", "table1-all"]
    t1 = data["tables"]["table1-all"]
    assert t1["caption"] == "Overall"
    assert t1["header"] == [["Variable", "Group=9", "0=11", "P value"]]
    assert [r[0] for r in t1["rows"]] == ["Age", "Sex", "Volume", "Empty"]


def test_extract_normalizes_entities_and_whitespace(html_path: Path) -> None:
    rows = gt.extract(html_path)["tables"]["table1-all"]["rows"]
    assert rows[0][1] == "54.30±12.51"
    assert rows[2][3] == "<0.001***"
    assert rows[3][1] == "—"


def test_compare_identical_yields_no_diff(html_path: Path) -> None:
    data = gt.extract(html_path)
    assert gt.compare(data, data) == []


def test_compare_accepts_change_within_tolerance(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "tweaked.html"
    p.write_text(HTML.replace(AGE_CELL, "54.30000000001&plusmn;12.51"), encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert [d["level"] for d in diffs] == ["WARN"]
    assert "within" in diffs[0]["note"]


def test_compare_flags_change_beyond_tolerance(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "drift.html"
    p.write_text(HTML.replace(AGE_CELL, "54.31&plusmn;12.51"), encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert len(diffs) == 1
    assert diffs[0]["level"] == "FAIL"
    assert diffs[0]["table"] == "table1-all"
    assert "54.31" in diffs[0]["new"]


def test_compare_flags_bound_marker_change(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "unbound.html"
    p.write_text(HTML.replace(BOUND_CELL, "0.001***"), encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert [d["level"] for d in diffs] == ["FAIL"]
    assert "bound marker" in diffs[0]["note"]


def test_compare_reports_text_only_change_as_warn(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "stars.html"
    p.write_text(HTML.replace(BOUND_CELL, "&lt;0.001**"), encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert [d["level"] for d in diffs] == ["WARN"]


def test_compare_flags_missing_table(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "short.html"
    p.write_text(HTML.split("<h4>ROC summary</h4>")[0], encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert any(d["table"] == "roc-metrics-train-test" and d["level"] == "FAIL" for d in diffs)


def test_compare_flags_row_label_and_header(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)

    a = tmp_path / "label.html"
    a.write_text(HTML.replace(">Age<", ">Age_years<"), encoding="utf-8")
    assert any("row label" in d["note"] for d in gt.compare(golden, gt.extract(a)))

    b = tmp_path / "header.html"
    b.write_text(HTML.replace("<th>P value</th>", "<th>P</th>"), encoding="utf-8")
    assert any(d["where"] == "header" for d in gt.compare(golden, gt.extract(b)))


def test_compare_uses_looser_tolerance_for_bootstrap_table(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "ci.html"
    p.write_text(HTML.replace("0.688", "0.6880001"), encoding="utf-8")
    # 1e-7 drift is inside TABLE_TOL for the bootstrap table, outside the default 1e-10
    assert [d["level"] for d in gt.compare(golden, gt.extract(p))] == ["WARN"]
    assert [d["level"] for d in gt.compare(golden, gt.extract(p), tol=1e-10)] == ["WARN"]


def test_compare_flags_small_drift_outside_bootstrap_table(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "p.html"
    p.write_text(HTML.replace("<td>0.866</td>", "<td>0.8661</td>"), encoding="utf-8")
    assert [d["level"] for d in gt.compare(golden, gt.extract(p))] == ["FAIL"]


def test_compare_run_status_change_fails(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "status.html"
    p.write_text(HTML.replace("Run status: COMPLETE", "Run status: INCOMPLETE"), encoding="utf-8")
    diffs = gt.compare(golden, gt.extract(p))
    assert diffs and diffs[0]["table"] == "<meta>"


def test_only_filter_limits_tables(tmp_path: Path, html_path: Path) -> None:
    golden = gt.extract(html_path)
    p = tmp_path / "drift.html"
    p.write_text(HTML.replace(AGE_CELL, "54.31&plusmn;12.51"), encoding="utf-8")
    assert gt.compare(golden, gt.extract(p), only=["roc-metrics-train-test"]) == []


def test_cli_roundtrip(tmp_path: Path, html_path: Path) -> None:
    golden_file = tmp_path / "golden.json"
    report_file = tmp_path / "report.md"
    assert gt.main(["extract", "--html", str(html_path), "--out", str(golden_file)]) == 0
    payload = json.loads(golden_file.read_text(encoding="utf-8"))
    assert payload["meta"]["run_status"] == "COMPLETE"
    code = gt.main([
        "compare", "--golden", str(golden_file), "--html", str(html_path),
        "--report", str(report_file),
    ])
    assert code == 0
    assert "Full parity" in report_file.read_text(encoding="utf-8")


def test_cli_compare_returns_one_on_fail(tmp_path: Path, html_path: Path) -> None:
    golden_file = tmp_path / "golden.json"
    gt.main(["extract", "--html", str(html_path), "--out", str(golden_file)])
    drift = tmp_path / "drift.html"
    drift.write_text(HTML.replace("<td>0.866</td>", "<td>0.900</td>"), encoding="utf-8")
    assert gt.main(["compare", "--golden", str(golden_file), "--html", str(drift)]) == 1


def test_cli_extract_without_tables_returns_two(tmp_path: Path) -> None:
    empty = tmp_path / "empty.html"
    empty.write_text("<html><body><p>nothing</p></body></html>", encoding="utf-8")
    assert gt.main(["extract", "--html", str(empty), "--out", str(tmp_path / "g.json")]) == 2


def test_render_report_lists_both_levels() -> None:
    diffs = [
        {"level": "FAIL", "table": "t", "where": "a", "old": "1", "new": "2", "note": "n"},
        {"level": "WARN", "table": "t", "where": "b", "old": "x", "new": "y", "note": "n"},
    ]
    text = gt.render_report(diffs, "g.json", "c.html")
    assert "FAIL: **1**" in text and "WARN: 1" in text
