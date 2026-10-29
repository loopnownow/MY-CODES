"""Layout SSOT: run logs live in ref/pipeline/, false_classification.xlsx at project root."""
from __future__ import annotations

import re
from pathlib import Path

from modules.config.ini_io import project_pipeline_dir, project_ref_dir
from modules.console.run import run_log_path
from modules.stats import data_io

MODULES = Path(__file__).resolve().parents[1]


def test_project_pipeline_dir_is_ref_pipeline_and_creates_it(tmp_path):
    proj = tmp_path / "demo_study"
    d = project_pipeline_dir(proj)
    assert d == proj / "ref" / "pipeline"
    assert d.is_dir()


def test_project_ref_dir_still_holds_settings_and_qc(tmp_path):
    """qc.html / settings.ini stay on ref/ root; only run logs move."""
    proj = tmp_path / "demo_study"
    assert project_ref_dir(proj) == proj / "ref"
    assert not (proj / "ref" / "pipeline" / "settings.ini").exists()


def test_run_log_paths_go_to_pipeline_folder(tmp_path):
    proj = tmp_path / "demo_study"
    assert run_log_path(proj) == proj / "ref" / "pipeline" / "pipeline_run.log"
    assert run_log_path(proj, dry_run=True) == (
        proj / "ref" / "pipeline" / "console_dryrun.log"
    )


def test_pipeline_default_log_path_is_under_ref_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    import modules.pipeline as pl

    got = Path(pl._default_run_log_path())
    assert got == tmp_path / "ref" / "pipeline" / "pipeline_run.log"
    assert got.parent.is_dir()


def test_no_module_writes_run_log_straight_into_ref_root():
    """Grep guard: the run log may only be joined under ref/pipeline/."""
    offenders: list[str] = []
    for p in sorted(MODULES.rglob("*.py")):
        if ".git" in p.parts:
            continue
        text = p.read_text(encoding="utf-8")
        for match in re.finditer(r"ref\W+pipeline_run\.log", text):
            offenders.append(f"{p.relative_to(MODULES)}: {match.group(0)}")
    assert offenders == []


def test_default_exclude_xlsx_is_project_root_name(tmp_path, monkeypatch):
    """The default name never carries an OUT_DIR / subproject prefix."""
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    for settings in (
        {"OUT_DIR": "Response", "SUBPROJECT_BY_OUTCOME": {"Response": "Response"}},
        {"OUT_DIR": "out/Group", "OUTCOME_COLS": ["Group"]},
        {"GROUP_SOURCE_COL": "Response", "OUT_DIR": "Response"},
        {},
    ):
        assert data_io.default_exclude_xlsx(settings) == "false_classification.xlsx"
        assert data_io.resolve_exclude_xlsx(settings) == str(
            tmp_path / "false_classification.xlsx"
        )
        assert data_io.resolve_exclude_files(settings) == [
            str(tmp_path / "false_classification.xlsx"),
        ]


def test_false_classification_path_joins_project_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    assert Path(data_io.false_classification_xlsx_path()) == (
        tmp_path / "false_classification.xlsx"
    )
    assert Path(data_io.false_classification_xlsx_path(tmp_path / "proj")) == (
        tmp_path / "proj" / "false_classification.xlsx"
    )


def test_pipeline_writes_false_classification_via_project_root_helper():
    """The pipeline must not build the false-classification path from OUT_DIR."""
    text = (MODULES / "pipeline.py").read_text(encoding="utf-8")
    assert "data_io.false_classification_xlsx_path()" in text
    assert not re.search(
        r"os\.path\.join\(\s*out\s*,\s*[\"']false_classification\.xlsx", text,
    )


def test_false_classification_write_and_exclude_read_are_one_file(tmp_path, monkeypatch):
    """DO_FALSE_CLASS writes where the exclude list reads — same project-root file."""
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    written = data_io.false_classification_xlsx_path()
    read = data_io.resolve_exclude_files({
        "OUT_DIR": "Response",
        "GROUP_SOURCE_COL": "Response",
    })[0]
    assert Path(read) == Path(written)


def test_console_run_opens_run_log_in_pipeline_folder():
    """start_run / start_run_bat both take the path from run_log_path."""
    text = (MODULES / "console" / "run.py").read_text(encoding="utf-8")
    assert text.count("log_path = run_log_path(proj") == 2
    assert "project_ref_dir" not in text


def test_dry_run_plan_names_the_new_paths():
    from modules.console.dryrun import dry_run_text

    text = dry_run_text(
        "demo_study", Path("/tmp/demo.xlsx"), Path("/tmp/ref/settings.ini"),
        {"OUTCOME_COLS": ["Response"], "DO_FALSE_CLASS": True},
        {}, {},
    )
    assert "ref/pipeline/pipeline_run.log" in text
    assert "false_classification.xlsx   ← 工程根" in text
