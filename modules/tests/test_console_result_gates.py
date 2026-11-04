"""结果写出开关、组列表头、HC 列、相关列名、ROC 两行。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from modules import config as cfg
from modules.config.defaults import merged_settings
from modules.config.ini_io import read_ini, write_ini
from modules.console.console import build_schema, hydrate_console_settings
from modules.stats.assoc import run_assoc
from modules.stats.clinical import _build_table1, apply_hc_to_table1
from modules.stats.correlation import check_correlation, resolve_named_column
from modules.stats.data_io import (
    _EXCLUDE_CACHE,
    _encode_group_column,
    _hc_relabel_stem,
    _hc_rows_outside_keep,
    load_exclude_id_keys,
)
from modules.stats.report import _build_panel, _partial_html, _table1_train_test_section, generate_results_html

ROOT = Path(__file__).resolve().parents[2]


def _clin_bag():
    table = pd.DataFrame({
        "Variable": ["Age (years)"],
        "MCI=3": ["11.00±1.00"],
        "CN=3": ["21.00±1.00"],
        "P value": ["0.010"],
        "Significance": ["*"],
        "_is_level": [False],
        "_p_num": [0.01],
    })
    return SimpleNamespace(
        table1=table,
        uni_res=pd.DataFrame({
            "Feature": ["Age (years)"],
            "OR": [1.2], "CI_low": [1.0], "CI_high": [1.4], "p": [0.01],
        }),
        mv_df=pd.DataFrame({
            "Feature": ["Age (years)"],
            "OR": [1.1], "CI_low": [0.9], "CI_high": [1.3], "p": [0.04],
        }),
        mv_selected=None,
        features=["Age (years)"],
        select_source="mv_p",
        firth_uni=pd.DataFrame({
            "Feature": ["Age (years)"],
            "OR": [1.3], "CI_low": [1.0], "CI_high": [1.6], "p": [0.02],
        }),
        firth_mv=None,
    )


def test_table_headers_use_mci_and_cn(monkeypatch):
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "CN", raising=False)
    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {"HC": "CN", "CN-ESRD": "CN"}, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    series = pd.Series(["MCI", "HC", "MCI", "HC", "MCI", "HC"])
    group_num, _factor, lbl0, lbl1 = _encode_group_column(series)
    assert (lbl1, lbl0) == ("MCI", "CN")
    clin = pd.DataFrame({"Age (years)": [10, 20, 11, 21, 12, 22]})
    idx0 = np.where(group_num.to_numpy() == 0)[0]
    idx1 = np.where(group_num.to_numpy() == 1)[0]
    table = _build_table1(clin, idx0, idx1, lbl0, lbl1)
    cols = [str(c) for c in table.columns]
    assert any(c.startswith("MCI=") for c in cols)
    assert any(c.startswith("CN=") for c in cols)
    assert not any(c.startswith("Group=") for c in cols)
    assert not any(c.startswith("non_") for c in cols)


def test_baseline_switches_drop_only_their_sections(monkeypatch):
    clin = _clin_bag()
    monkeypatch.setattr(cfg, "DO_BASELINE_ALL", False, raising=False)
    off = _build_panel("all", None, None, clin, "A. All Data — Baseline")
    assert "总表" not in off
    assert "Baseline — All" not in off
    monkeypatch.setattr(cfg, "DO_BASELINE_ALL", True, raising=False)
    on = _build_panel("all", None, None, clin, "A. All Data — Baseline")
    assert "总表" in on
    assert "Baseline — All" not in on
    monkeypatch.setattr(cfg, "DO_BASELINE_TRAIN_TEST", False, raising=False)
    assert _table1_train_test_section({"clin": clin}, {"clin": clin}, data=None) == ""
    monkeypatch.setattr(cfg, "DO_BASELINE_TRAIN_TEST", True, raising=False)
    tt = _table1_train_test_section({"clin": clin}, {"clin": clin}, data=None)
    assert "B. Table 1" in tt
    assert "Baseline Table 1 — Train vs Test" not in tt


def test_train_test_keeps_hc_column_beside_cn(monkeypatch):
    monkeypatch.setattr(cfg, "DO_BASELINE_TRAIN_TEST", True, raising=False)
    clin = _clin_bag()
    hc = pd.DataFrame({"Age (years)": [30, 31, 32]})
    data = SimpleNamespace(hc_train=hc, hc_test=hc, hc_relabel_from="")
    html = _table1_train_test_section({"clin": clin}, {"clin": clin}, data=data)
    assert "<th>HC</th>" in html
    assert "<th>CN</th>" in html
    assert "<th>MCI</th>" in html
    assert "non_Group" not in html
    assert "31.00" in html
    assert "21.00" in html
    held = _hc_rows_outside_keep(
        pd.DataFrame({"Group": ["MCI", "HC", "CN-ESRD", "HC"]}),
        ["MCI", "CN-ESRD"],
        False,
    )
    assert held["Group"].tolist() == ["HC", "HC"]
    assert _hc_rows_outside_keep(
        pd.DataFrame({"Group": ["MCI", "HC"]}),
        ["MCI", "HC"],
        False,
    ) is None


def test_modeled_hc_is_renamed_back_on_train_test_only():
    table = pd.DataFrame({
        "Variable": ["Age (years)"],
        "MCI=3": ["11.00±1.00"],
        "CN=4": ["21.00±1.00"],
        "P value": ["0.020"],
        "_is_level": [False],
    })
    out = apply_hc_to_table1(table, None, "CN")
    cols = [str(c) for c in out.columns]
    assert "HC=4" in cols
    assert "CN=4" not in cols
    assert "MCI=3" in cols
    raw = pd.Series(["MCI", "HC", "HC"])
    num = pd.Series([1, 0, 0])
    assert _hc_relabel_stem(raw, num, "CN", "MCI") == "CN"


def test_table1_three_groups_show_anova_and_lsd(monkeypatch):
    monkeypatch.setattr(cfg, "DO_BASELINE_TRAIN_TEST", True, raising=False)
    monkeypatch.setattr(cfg, "DO_BASELINE_ALL", True, raising=False)
    monkeypatch.setattr(cfg, "POSITIVE_GROUP_LABEL", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "MCI", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "CN", raising=False)
    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {}, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", ["MCI", "CN-ESRD"], raising=False)
    monkeypatch.setattr(cfg, "PAIRWISE_ALL", False, raising=False)
    from modules.stats import data_io as dio
    dio.CLIN_VAR_TYPES = {"Age": "continuous"}
    dio.CLIN_LEVEL_MAPS = {}
    age = np.array([1, 2, 3, 2, 3, 4, 5, 6, 7], dtype=float)
    groups = ["MCI", "MCI", "MCI", "CN-ESRD", "CN-ESRD", "CN-ESRD", "HC", "HC", "HC"]
    frame = pd.DataFrame({"Age": age})
    grp = pd.Series(groups)
    data = SimpleNamespace(
        baseline_train_clin=frame,
        baseline_train_group=grp,
        baseline_test_clin=frame,
        baseline_test_group=grp,
        hc_train=pd.DataFrame({"Age": [100, 101, 102]}),
        hc_test=pd.DataFrame({"Age": [100, 101, 102]}),
        hc_relabel_from="",
    )
    dummy = SimpleNamespace(table1=pd.DataFrame({
        "Variable": ["Age"],
        "MCI=2": ["9.00±1.00"],
        "CN=2": ["8.00±1.00"],
        "P value": ["0.500"],
        "_is_level": [False],
    }))
    html = _table1_train_test_section({"clin": dummy}, {"clin": dummy}, data=data)
    assert "ANOVA" in html
    assert "Fisher" in html
    assert 'colspan="3">Train</th>' in html
    assert 'rowspan="3">MCI vs HC</th>' in html
    assert "100.00" not in html
    assert "6.00±1.00" in html
    panel = _build_panel(
        "all", None, None,
        SimpleNamespace(table1=pd.DataFrame({
            "Variable": ["Age"],
            "MCI=3": ["2.00±1.00"],
            "CN-ESRD=3": ["3.00±1.00"],
            "HC=3": ["6.00±1.00"],
            "P value": ["0.004"],
            "MCI vs HC": ["0.002"],
            "_is_level": [False],
        })),
        "A. All Data",
    )
    assert "总表" in panel
    assert "ANOVA" in panel
    assert "MCI vs HC" in panel
    assert "0.002" in panel


def test_logistic_table_switches_leave_the_other_table(monkeypatch):
    clin = _clin_bag()
    monkeypatch.setattr(cfg, "DO_FIRTH", True, raising=False)
    monkeypatch.setattr(cfg, "DO_UNI_LOGISTIC_TABLE", False, raising=False)
    monkeypatch.setattr(cfg, "DO_MV_LOGISTIC_TABLE", True, raising=False)
    html = _build_panel("train", None, None, clin, "C. Training Set", include_table1=False)
    assert "Univariate Logistic" not in html
    assert "Firth Penalized Logistic — Univariate" in html
    assert "Multivariate Logistic" in html
    assert "Selected clinical features" in html
    monkeypatch.setattr(cfg, "DO_UNI_LOGISTIC_TABLE", True, raising=False)
    monkeypatch.setattr(cfg, "DO_MV_LOGISTIC_TABLE", False, raising=False)
    html = _build_panel("train", None, None, clin, "C. Training Set", include_table1=False)
    assert "Univariate Logistic" in html
    assert "Multivariate Logistic" not in html
    assert "Selected clinical features" in html


def test_corr_and_partial_switches_are_independent(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "DO_HEATMAP", False, raising=False)
    monkeypatch.setattr(cfg, "DO_NRI", False, raising=False)
    monkeypatch.setattr(cfg, "DO_CORR", False, raising=False)
    monkeypatch.setattr(cfg, "DO_PARTIAL", True, raising=False)
    monkeypatch.setattr(cfg, "GROUP_KEEP", [], raising=False)
    layer = SimpleNamespace()
    data = SimpleNamespace(all=layer, train=layer, test=layer)
    corr = pd.DataFrame({
        "predictor": ["Score"],
        "ref": ["Age"],
        "spearman_rho": [0.5],
        "p_value": [0.01],
        "ci_lower": [0.2],
        "ci_upper": [0.7],
    })
    assoc = {"partial": [{
        "x": "Score", "y": "Age (years)", "controls": [], "method": "spearman",
        "r": 0.4, "lo": 0.1, "hi": 0.6, "p": 0.02, "n": 40, "df": 37, "group": "",
    }]}
    html = generate_results_html(
        data,
        {"clin": SimpleNamespace(table1=pd.DataFrame()), "rad": None},
        corr_ref_tables=[corr],
        assoc_info=assoc,
        out_dir=str(tmp_path),
    )
    assert "Correlation with reference variables" not in html
    assert "Partial correlation" in html
    monkeypatch.setattr(cfg, "DO_PARTIAL", False, raising=False)
    assert _partial_html(assoc) == ""


def test_reference_name_age_matches_display_column(monkeypatch):
    monkeypatch.setattr(cfg, "BOOTSTRAP_N", 20, raising=False)
    age = np.linspace(50, 80, 30)
    score = age + 1
    layer = SimpleNamespace(
        clin=pd.DataFrame({"Age (years)": age, "Score": score}),
        label=np.zeros(30),
        group=pd.Series(["MCI"] * 30),
        rad=pd.DataFrame(),
    )
    got = check_correlation(layer, egfr_col="Age", targets=["Score"], nboot=20, seed=1)
    assert got.loc[0, "ref"] == "Age"
    assert np.isfinite(got.loc[0, "spearman_rho"])
    assert np.isfinite(got.loc[0, "p_value"])
    assert np.isfinite(got.loc[0, "ci_lower"])
    assert np.isfinite(got.loc[0, "ci_upper"])
    assert resolve_named_column("Age", ["Age (years)", "Sex"]) == "Age (years)"
    with pytest.raises(ValueError, match="找不到列") as exc:
        check_correlation(layer, egfr_col="eGFR", targets=["Score"], nboot=20, seed=1)
    assert "Age (years)" in str(exc.value)
    with pytest.raises(ValueError, match="对上不止一列"):
        resolve_named_column("Age", ["Age (years)", "Age (months)"])


def test_partial_uses_the_same_column_match(monkeypatch):
    monkeypatch.setattr(cfg, "DO_PARTIAL", True, raising=False)
    monkeypatch.setattr(cfg, "DO_CORR", False, raising=False)
    monkeypatch.setattr(cfg, "DO_ANCOVA", False, raising=False)
    monkeypatch.setattr(cfg, "DO_MLR", False, raising=False)
    monkeypatch.setattr(cfg, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(cfg, "PARTIAL_X", ["RadScore"], raising=False)
    monkeypatch.setattr(cfg, "PARTIAL_Y", ["Age"], raising=False)
    monkeypatch.setattr(cfg, "PARTIAL_CONTROLS", [], raising=False)
    monkeypatch.setattr(cfg, "PARTIAL_METHOD", "pearson", raising=False)
    monkeypatch.setattr(cfg, "PARTIAL_GROUP", "", raising=False)
    age = np.linspace(50, 80, 40)
    layer = SimpleNamespace(
        clin=pd.DataFrame({"Age (years)": age}),
        group=pd.Series(["MCI"] * 40),
        rad=pd.DataFrame(),
    )
    out = run_assoc(layer, extras={"RadScore": age + 0.1})
    assert out and out.get("partial")
    assert np.isfinite(out["partial"][0]["r"])
    assert not any("缺少列" in w for w in out.get("warnings") or [])
    bare = SimpleNamespace(
        clin=pd.DataFrame({"Score": age}),
        group=pd.Series(["MCI"] * 40),
        rad=pd.DataFrame(),
    )
    with pytest.raises(ValueError, match="找不到列") as exc:
        run_assoc(bare, extras={"RadScore": age})
    assert "Score" in str(exc.value)


def test_roc_rows_and_time_options_come_from_clinical_columns():
    js = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    html = (ROOT / "modules" / "console" / "console.html").read_text(encoding="utf-8")
    assert 'class="roc-row roc-row-1"' in js
    assert 'class="surv-row surv-line"' in js
    assert 'data-gate="DO_LANDMARK_ROC"' in js
    assert 'titleKeys = ["DO_SURVIVAL", "DO_TIME_DEPENDENT_ROC", "DO_SURVIVAL_BY_RISK", "DO_COX", "DO_LANDMARK_ROC"]' in js
    assert 'data-gpol=' in js
    assert "levelPolaritySelect" in js
    assert 'setFieldValue("TD_IPCW_TIME_COL"' in js
    assert 'id="grpCodeLine"' not in html
    assert 'title: "阈值"' not in js
    assert 'pcorr: "DO_PARTIAL"' not in js
    assert 'return "non_" + t' not in js
    assert 'id="doBaselineAll"' in html and "DO_BASELINE_ALL" in html
    assert 'id="doBaselineTt"' in html and "DO_BASELINE_TRAIN_TEST" in html
    fields = build_schema({}, clin_cols=["Age (years)", "FollowUp (months)"])
    by = {f["key"]: f for f in fields}
    assert by["TD_ROC_TIMES"]["widget"] == "growselect"
    assert by["TD_ROC_TIMES"]["more_label"] == "（再选一列）"
    assert "Age (years)" in by["TD_ROC_TIMES"]["options"]
    assert "FollowUp (months)" in by["TD_ROC_TIMES"]["options"]
    assert by["TD_ROC_STATUS_COLS"]["widget"] == "checklist"
    assert by["TD_ROC_STATUS_COLS"]["add_label"] == "+ 搜索列名"
    assert "Age (years)" in by["TD_ROC_STATUS_COLS"]["option_pool"]
    assert "FollowUp (months)" in by["TD_ROC_STATUS_COLS"]["option_pool"]
    assert by["TD_IPCW_TIME_COL"]["widget"] == "select"
    assert by["TD_IPCW_STATUS_COL"]["widget"] == "select"
    assert by["TD_IPCW_HORIZONS"]["widget"] == "timeselect"
    assert by["TD_IPCW_HORIZONS"]["options"] == [3, 6, 12, 24]
    assert "时间依赖性 ROC（IPCW）" in js
    assert 'data-sec="surv"' in js
    proj = (ROOT / "modules" / "console" / "project.html").read_text(encoding="utf-8")
    assert "function fileDir()" in proj
    assert '/api/pick-file' in proj
    assert "dir: dir" in proj or 'dir: dir,' in proj
    assert "htmlDir(" not in proj
    assert by["EXCLUDE_FILE"]["widget"] == "growselect"
    assert by["EXCLUDE_FILE"]["more_label"] == "（再选一个文件）"
    assert 'class="grow-rows"' in js
    assert "data-grow" in js
    assert "（再选一个文件）" in js


def test_missing_baseline_keys_default_on_and_old_times_error(tmp_path):
    got = merged_settings({})
    assert got["DO_BASELINE_ALL"] is True
    assert got["DO_BASELINE_TRAIN_TEST"] is True
    assert got["DO_UNI_LOGISTIC_TABLE"] is True
    assert got["DO_MV_LOGISTIC_TABLE"] is True
    path = tmp_path / "settings.ini"
    path.write_text("[roc]\nTD_ROC_TIMES = [3, 6]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="临床"):
        read_ini(path)
    path.write_text("[roc]\nTD_ROC_TIMES = []\nTD_ROC_STATUS_COLS = {}\n", encoding="utf-8")
    loaded = read_ini(path)
    assert loaded["TD_ROC_TIMES"] == ""
    assert loaded["TD_ROC_STATUS_COLS"] == ""
    out = tmp_path / "round.ini"
    write_ini(out, {
        "DO_BASELINE_ALL": False,
        "DO_BASELINE_TRAIN_TEST": True,
        "DO_UNI_LOGISTIC_TABLE": False,
        "DO_MV_LOGISTIC_TABLE": True,
        "TD_ROC_TIMES": "FollowUp (months)",
        "TD_ROC_STATUS_COLS": "response",
    })
    back = read_ini(out)
    assert back["DO_BASELINE_ALL"] is False
    assert back["DO_BASELINE_TRAIN_TEST"] is True
    assert back["DO_UNI_LOGISTIC_TABLE"] is False
    assert back["TD_ROC_TIMES"] == "FollowUp (months)"
    assert back["TD_ROC_STATUS_COLS"] == "response"
    path.write_text(
        '[roc]\nTD_ROC_TIMES = ["FollowUp (months)"]\n',
        encoding="utf-8",
    )
    assert read_ini(path)["TD_ROC_TIMES"] == "FollowUp (months)"
    path.write_text(
        '[roc]\nTD_ROC_TIMES = ["FollowUp (months)", "Age (years)"]\n',
        encoding="utf-8",
    )
    assert read_ini(path)["TD_ROC_TIMES"] == ["FollowUp (months)", "Age (years)"]
    write_ini(out, {"TD_ROC_TIMES": ["FollowUp (months)", "Age (years)"]})
    assert read_ini(out)["TD_ROC_TIMES"] == ["FollowUp (months)", "Age (years)"]
    write_ini(out, {
        "TD_ROC_TIMES": ["FollowUp (months)", "", "Age (years)"],
        "TD_ROC_STATUS_COLS": ["response_3m", "response_6m", ""],
    })
    aligned = read_ini(out)
    assert aligned["TD_ROC_TIMES"] == ["FollowUp (months)", "", "Age (years)"]
    assert aligned["TD_ROC_STATUS_COLS"] == ["response_3m", "response_6m", ""]


def test_exclude_file_one_stays_string_and_two_merge(tmp_path):
    assert hydrate_console_settings({"EXCLUDE_FILE": ["a.xlsx"]})["EXCLUDE_FILE"] == "a.xlsx"
    assert hydrate_console_settings({"EXCLUDE_FILE": ["a.xlsx", "b.xlsx"]})["EXCLUDE_FILE"] == ["a.xlsx", "b.xlsx"]
    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    pd.DataFrame({"exc": ["p1", "p2"]}).to_excel(a, sheet_name="exc", index=False)
    pd.DataFrame({"exc": ["p2", "p3"]}).to_excel(b, sheet_name="exc", index=False)
    _EXCLUDE_CACHE.clear()
    one = load_exclude_id_keys(settings={
        "DO_EXCLUDE_SAMPLES": True,
        "EXCLUDE_FILE": str(a),
        "EXCLUDE_SHEET": "exc",
        "EXCLUDE_COL": "exc",
        "EXCLUDE_ROW": "",
        "EXCLUDE_IDS": [],
    })
    _EXCLUDE_CACHE.clear()
    both = load_exclude_id_keys(settings={
        "DO_EXCLUDE_SAMPLES": True,
        "EXCLUDE_FILE": [str(a), str(b)],
        "EXCLUDE_SHEET": "exc",
        "EXCLUDE_COL": "exc",
        "EXCLUDE_ROW": "",
        "EXCLUDE_IDS": [],
    })
    assert "p1" in one and "p3" not in one
    assert "p1" in both and "p3" in both


def test_blank_ini_keys_clears_only_the_reported_key(tmp_path):
    from modules.config.ini_io import blank_ini_keys, keys_in_settings_error

    path = tmp_path / "settings.ini"
    path.write_text(
        '[io]\nINPUT = "a.xlsx"\n'
        '[switch]\nTD_ROC_TIMES = [3, 6]\nTD_ROC_STATUS_COLS = "response"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        read_ini(path)
    keys = keys_in_settings_error(str(exc.value))
    assert keys == ["TD_ROC_TIMES"]
    info = blank_ini_keys(path, keys)
    assert info["cleared"] == ["TD_ROC_TIMES"]
    assert Path(info["backup"]).is_file()
    loaded = read_ini(path)
    assert loaded["TD_ROC_TIMES"] == ""
    assert loaded["TD_ROC_STATUS_COLS"] == "response"
    assert loaded["INPUT"] == "a.xlsx"
    assert keys_in_settings_error("ORDINAL_MAX_LEVELS 须为正整数，当前=0") == ["ORDINAL_MAX_LEVELS"]
    assert keys_in_settings_error(
        "CLIN_FORCE_CATEGORICAL 须为列名，不能写水平数 3。水平数写 ORDINAL_MAX_LEVELS。"
    ) == ["CLIN_FORCE_CATEGORICAL"]
    with pytest.raises(ValueError, match="没有可清空"):
        blank_ini_keys(path, ["NOT_A_REAL_KEY"])


def test_console_preview_tab_and_wider_run_column():
    html = (ROOT / "modules" / "console" / "console.html").read_text(encoding="utf-8")
    css = (ROOT / "modules" / "console" / "console.css").read_text(encoding="utf-8")
    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    assert 'id="tabBtnPreview"' in html
    assert 'id="panePreview"' in html
    assert html.index('id="tabBtnPreview"') < html.index('id="tabBtnConfig"') < html.index('id="tabBtnFlow"')
    assert ">数据预览</button>" in html and ">统计配置</button>" in html
    assert 'id="pvDataForm"' in html and 'id="pvSheets"' in html and 'id="pvExclude"' in html
    assert 'id="extRows"' in html and 'id="ioData"' in html
    assert 'id="btnPreviewReload"' in html and 'id="btnToConfig"' in html
    assert "function renderDataPane" in ui and "DATA_PANE_KEYS" in ui
    assert 'id="pvProjectBox"' in html and 'data-pvcol="left"' in html
    assert 'data-pvcol="mid"' in html and 'data-pvcol="right"' in html
    assert "function renderRoleTable" in ui and "data-role-row" in ui
    assert "data-exc-row" in ui and "data-reader-add" in ui
    proj = (ROOT / "modules" / "console" / "project.html").read_text(encoding="utf-8")
    assert 'id="radToggle"' not in proj and 'id="step5"' not in proj
    flow = proj.split('<ol class="flow"', 1)[1].split("</ol>", 1)[0]
    assert flow.count("<li>") == 3
    assert 'id="lockCard"' not in html
    assert "lockline" in html and 'id="lockProject"' in html
    assert "配置档案" not in html
    assert 'id="runBat"' not in html
    assert 'id="doQcHtml"' not in html
    assert 'id="palCol"' in html and 'id="palCard"' in html
    assert html.index('id="palCol"') < html.index('class="col mid"')
    assert ">干跑</button>" in html and ">运行</button>" in html and ">重启</button>" in html
    assert ">保存</button>" in html and ">读取</button>" not in html
    assert 'id="btnLoadBak"' not in html and "btnLoadBak" not in ui and "restoreBackup" not in ui
    assert "读取用 .bak" not in html
    assert html.index('id="btnWrite"') < html.index('id="btnDry"') < html.index('id="btnRun"')
    assert "run-actions" in html
    css_compact = css.replace(" ", "").replace("\n", "")
    assert ".pal-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));" in css_compact
    assert ".run-actions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));" in css_compact
    assert "minmax(220px,280px)" in css
    assert "440px" in css
    assert "palCardPark" not in ui
    assert 'showPane("preview")' in ui
    assert ">流程图</button>" in html
    assert "入组流程图" not in html
    assert "sec-quad" in ui and "data-td-status" in ui
    schema = (ROOT / "modules" / "console" / "console_schema.js").read_text(encoding="utf-8")
    assert "ICC 与相关过滤阈值" not in schema
    compact = css.replace(" ", "").replace("\n", "")
    assert "#paneConfig[hidden]" in compact and "display:none!important" in compact


def test_data_pane_uses_one_file_card_for_all_three_inputs():
    """数据文件 / 外验 / 排除名单共用一张文件卡：选文件 → 自动读表 → 手动选角色。"""
    base = ROOT / "modules" / "console"
    html = (base / "console.html").read_text(encoding="utf-8")
    ui = (base / "console_ui.js").read_text(encoding="utf-8")
    css = (base / "console.css").read_text(encoding="utf-8")
    api = (base / "console_api.js").read_text(encoding="utf-8")
    assert 'data-file-card="main"' in html
    assert "pv-file-card" in css and "pv-file-head" in css
    assert "data-ext-card" in ui and "data-exc-row" in ui
    assert "data-ext-role" in ui and "EXT_ROLE_LABELS" in ui
    assert "fetchXlsxMeta" in api and "loadExtMeta" in ui
    # 旧的「块表 块=表」文本入口已删，不再有第二套外验表单
    assert "data-ext=sheets" not in ui and "parseSheetsText" not in ui
    assert "previewIntoExtRow" not in api and "previewIntoExtRow" not in ui
    # 主数据卡也有「选择」按钮（与另两处同形）
    assert 'id="btnDataPick"' in html and 'id="ioDataPick"' in html
    # 操作卡：与配置页同一套「保存 ini / 重启服务」入口
    assert 'id="btnPvSave"' in html and '>保存 ini</button>' in html
    assert 'id="btnPvRestart"' in html and '>重启服务</button>' in html
    assert '$("btnPvSave")' in ui and '$("btnPvRestart")' in ui
    assert "writeback()" in ui and "restartServer()" in ui


def test_reader_direction_lives_only_in_the_encode_card():
    """阅片列方向（READER_VALUE_MAP）只留在「文本编码」卡；配置页不再重复一份（也不许落进开关段）。"""
    from modules.config.defaults import merged_settings
    from modules.console import schema
    from modules.console.schema_ui import build_schema

    assert "READER_VALUE_MAP" not in schema.SECTION_OF
    js = (ROOT / "modules" / "console" / "console_schema.js").read_text(encoding="utf-8")
    hidden = js.split("const HIDDEN_FORM_KEYS = new Set([", 1)[1].split("]);", 1)[0]
    assert '"READER_VALUE_MAP"' in hidden  # 表单隐藏，避免被 _section_of 兜底塞进「开关」段
    fields = build_schema(merged_settings(), sheets=["cln"])
    assert [f for f in fields if f.get("key") == "READER_VALUE_MAP"]  # 键仍在（后端要它）
    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    assert "function readerDirPanel" in ui
    assert "captureFoldState" in ui
    assert "结局 / 子工程 / 阅片方向" not in ui


def test_run_column_shows_whenever_the_config_pane_is_shown():
    """统计配置页右侧运行台：切到配置就显示，进预览/流程图才收起。"""
    html = (ROOT / "modules" / "console" / "console.html").read_text(encoding="utf-8")
    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    assert 'id="runCol"' in html
    seg = ui.split("function showPane(name)", 1)[1].split("function loadFlowFrame", 1)[0]
    assert "runCol" in seg and "hidden = flow || preview" in seg


def test_preview_lists_every_role_sheet_and_the_intersection(tmp_path, monkeypatch):
    """预览覆盖所有已分配角色的工作表，并给出 ID 交集合计。"""
    from modules.console import preview as prev_mod

    monkeypatch.setattr(prev_mod, "under_root", lambda p: Path(p))
    book = tmp_path / "demo.xlsx"
    clin = pd.DataFrame({
        "Patient_ID": ["a", "b", "c"],
        "Stroke": ["y", "n", "y"],
        "Age": [1, 2, 3],
    })
    cta = pd.DataFrame({"Patient_ID": ["a", "b", "c"], "f1": [1.0, 2.0, 3.0], "f2": [4.0, 5.0, 6.0]})
    us = pd.DataFrame({"Patient_ID": ["a", "b"], "g1": [1.0, 2.0]})
    with pd.ExcelWriter(book) as xw:
        clin.to_excel(xw, sheet_name="cln", index=False)
        cta.to_excel(xw, sheet_name="CTA", index=False)
        us.to_excel(xw, sheet_name="US", index=False)
    settings = {
        "CLIN_SHEET": "cln", "CLIN_ID_COL": "Patient_ID",
        "RAD_ID_COL": "Patient_ID", "OUTCOME_COLS": ["Stroke"],
        "MODALITY_BLOCKS": [
            {"name": "CTA", "sheet": "CTA", "method": "logistic"},
            {"name": "US", "sheet": "US", "method": "logistic"},
        ],
    }
    pv = prev_mod.preview_workbook(book, dict(settings))
    roles = {(r["role"], r["sheet"]) for r in pv["role_sheets"]}
    assert ("clin", "cln") in roles
    assert ("rad", "CTA") in roles and ("rad", "US") in roles
    assert pv["role_intersection"] == 2  # US 只有 a, b
    by_sheet = {r["sheet"]: r for r in pv["role_sheets"]}
    assert by_sheet["CTA"]["n_id"] == 3 and by_sheet["US"]["n_id"] == 2
    # 起始页预览：每张表都给前 3 行（最多 12 列）
    blocks = pv["sheet_blocks"]
    assert set(blocks) == {"cln", "CTA", "US"}
    for name, blk in blocks.items():
        assert blk["rows"], name
        assert len(blk["rows"]) <= 3, name
        assert len(blk["columns"]) <= 12, name
    assert blocks["cln"]["columns"][0] == "Patient_ID"
    assert [str(x) for x in blocks["CTA"]["rows"][0]][:2] == ["a", "1"]


def test_start_page_loads_data_and_previews_every_sheet():
    """起始页：开服务后自动读主数据、列出所有工作表前 3 行；右侧操作台有「载入数据」。"""
    proj = (ROOT / "modules" / "console" / "project.html").read_text(encoding="utf-8")
    assert 'id="btnLoadNow"' in proj and ">载入数据</button>" in proj
    assert 'id="btnGoPg"' in proj and ">进入数据预览</button>" in proj
    assert 'id="pvCard"' in proj and 'id="pvBox"' in proj and 'id="pvMsg"' in proj
    assert "function renderSheetBlocks" in proj
    assert "renderSheetBlocks(prev)" in proj
    assert "function autoLoadAndPreview" in proj
    assert "autoOpenPreview" not in proj  # 不再自动跳控制台
    seg = proj.split("async function autoLoadAndPreview", 1)[1].split("function keysFromSettingsError", 1)[0]
    assert "location.href" not in seg
    assert proj.index('id="btnLoadNow"') < proj.index('id="msg"')




def test_sync_external_tests_keeps_role_sheets():
    """外验卡上选的质控 / ICC 工作表现在随 EXTERNAL_TESTS 一起保留。"""
    from modules.console.ops import sync_external_tests

    out = sync_external_tests({
        "EXTERNAL_TESTS": [{
            "file": "ext.xlsx", "clin_sheet": "cln", "rad_sheet": "CTA",
            "rad_id_col": "Patient_ID", "qc_sheet": "qc", "icc_sheet": "icc",
        }],
    })
    spec = out["EXTERNAL_TESTS"][0]
    assert spec["clin_sheet"] == "cln" and spec["rad_sheet"] == "CTA"
    assert spec["qc_sheet"] == "qc" and spec["icc_sheet"] == "icc"
    assert out["EXTERNAL_FILE"] == "ext.xlsx"
