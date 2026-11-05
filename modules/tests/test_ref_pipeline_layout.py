"""Layout SSOT: run logs in ref/pipeline/, false_classification.xlsx in the run's results folder."""
from __future__ import annotations

import re
from pathlib import Path

from modules.config.ini_io import project_pipeline_dir, project_ref_dir
from modules.console.run import run_log_path
from modules.stats import data_io

MODULES = Path(__file__).resolve().parents[1]


def _slash(p: str) -> str:
    return str(p).replace("\\", "/")


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


def test_results_out_dir_prefers_resolved_out_dir():
    """Results HTML 所在目录：已解析的 OUT_DIR 原样（两两比较的对子夹也认）。"""
    assert _slash(data_io.resolve_results_out_dir({"OUT_DIR": "Response"})) == "Response"
    assert _slash(data_io.resolve_results_out_dir({
        "OUT_DIR": "Response/CCC_vs_ECC",
    })) == "Response/CCC_vs_ECC"


def test_results_out_dir_falls_back_to_subproject_mapping():
    assert _slash(data_io.resolve_results_out_dir({
        "GROUP_SOURCE_COL": "Response",
        "SUBPROJECT_BY_OUTCOME": {"Response": "ASL"},
    })) == "ASL"
    assert _slash(data_io.resolve_results_out_dir({
        "OUTCOME_COLS": ["DSI"],
        "SUBPROJECT_BY_OUTCOME": {"DSI": "Group"},
    })) == "Group"
    assert data_io.resolve_results_out_dir({}) == ""


def test_default_exclude_xlsx_lives_in_the_run_results_folder():
    """默认错分表与 Results HTML 同夹：<OUT_DIR>/false_classification.xlsx。"""
    assert _slash(data_io.default_exclude_xlsx({"OUT_DIR": "Response"})) == (
        "Response/false_classification.xlsx"
    )
    assert _slash(data_io.default_exclude_xlsx({
        "GROUP_SOURCE_COL": "Stroke",
        "SUBPROJECT_BY_OUTCOME": {"Stroke": "ASL"},
    })) == "ASL/false_classification.xlsx"
    # 推不出结局结果夹时退回工程根同名文件，不编造子目录
    assert data_io.default_exclude_xlsx({}) == "false_classification.xlsx"


def test_false_classification_path_joins_project_dir_with_results_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    assert Path(data_io.false_classification_xlsx_path(
        settings={"OUT_DIR": "Response"},
    )) == tmp_path / "Response" / "false_classification.xlsx"
    assert Path(data_io.false_classification_xlsx_path(
        tmp_path / "proj",
        {"OUT_DIR": "out/Group"},
    )) == tmp_path / "proj" / "out" / "Group" / "false_classification.xlsx"


def test_pipeline_writes_false_classification_through_the_shared_helper():
    """The pipeline must not hard-code its own false-classification path."""
    text = (MODULES / "pipeline.py").read_text(encoding="utf-8")
    assert "data_io.false_classification_xlsx_path()" in text
    assert not re.search(
        r"os\.path\.join\(\s*out\s*,\s*[\"']false_classification\.xlsx", text,
    )


def test_false_classification_write_and_exclude_read_are_one_file(tmp_path, monkeypatch):
    """DO_FALSE_CLASS writes where the exclude list reads — same per-outcome file."""
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    settings = {
        "OUT_DIR": "Response/CCC_vs_ECC",
        "GROUP_SOURCE_COL": "Response",
    }
    written = data_io.false_classification_xlsx_path(settings=settings)
    read = data_io.resolve_exclude_files(settings)[0]
    assert Path(read) == Path(written)
    assert Path(written).parent.name == "CCC_vs_ECC"


def test_explicit_exclude_file_still_wins_over_the_default(tmp_path, monkeypatch):
    """EXCLUDE_FILE 非空 → 用户路径，不换到结果夹。"""
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    user = tmp_path / "ref" / "my_exclude.xlsx"
    settings = {"OUT_DIR": "Response", "EXCLUDE_FILE": str(user)}
    assert data_io.resolve_exclude_files(settings) == [str(user)]
    assert data_io.resolve_exclude_xlsx(settings) == str(user)


def test_console_run_opens_run_log_in_pipeline_folder():
    """start_run / start_run_bat both take the path from run_log_path."""
    text = (MODULES / "console" / "run.py").read_text(encoding="utf-8")
    assert text.count("log_path = run_log_path(proj") == 2
    assert "project_ref_dir" not in text


def test_dry_run_plan_names_the_new_paths():
    from modules.console.dryrun import dry_run_text

    text = dry_run_text(
        "demo_study", Path("/tmp/demo.xlsx"), Path("/tmp/ref/settings.ini"),
        {
            "OUTCOME_COLS": ["Response"],
            "SUBPROJECT_BY_OUTCOME": {"Response": "ASL"},
            "DO_FALSE_CLASS": True,
        },
        {}, {},
    )
    assert "ref/pipeline/pipeline_run.log" in text
    assert "ASL/false_classification.xlsx" in text
    assert "工程根（不随 OUT_DIR）" not in text


def test_pairwise_run_keeps_the_file_in_the_pair_folder():
    """两两比较：OUT_DIR 已是对子夹，错分表跟着进对子夹，不回结局父层或工程根。"""
    settings = {
        "OUT_DIR": "Group/Positive_vs_Negative",
        "GROUP_SOURCE_COL": "Group",
        "GROUP_KEEP": ["Positive", "Negative"],
    }
    assert _slash(data_io.default_exclude_xlsx(settings)) == (
        "Group/Positive_vs_Negative/false_classification.xlsx"
    )
    assert _slash(data_io.resolve_results_out_dir(settings)) == (
        "Group/Positive_vs_Negative"
    )


def test_results_folder_is_never_the_project_root():
    """结果夹与 Results HTML 同一处：都不落工程根。"""
    for settings in (
        {"OUT_DIR": "Response"},
        {"GROUP_SOURCE_COL": "Response", "SUBPROJECT_BY_OUTCOME": {"Response": "ASL"}},
        {"OUTCOME_COLS": ["Stroke"]},
    ):
        got = _slash(data_io.resolve_results_out_dir(settings))
        assert got and got != "."


def test_bare_exclude_name_resolves_inside_the_results_folder(tmp_path, monkeypatch):
    """控制台下拉里的裸文件名 → 该结局结果夹里的真文件（工程根没有时）。"""
    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    (tmp_path / "ASL").mkdir()
    target = tmp_path / "ASL" / "false_classification.xlsx"
    target.write_bytes(b"x")
    settings = {
        "OUT_DIR": "ASL",
        "EXCLUDE_FILE": "false_classification.xlsx",
    }
    assert data_io.resolve_exclude_files(settings) == [
        str(Path("ASL") / "false_classification.xlsx"),
    ]
    # 工程根真有同名文件时，仍是工程根那条（用户显式写的路径不猜）
    (tmp_path / "false_classification.xlsx").write_bytes(b"y")
    assert data_io.resolve_exclude_files(settings) == ["false_classification.xlsx"]
