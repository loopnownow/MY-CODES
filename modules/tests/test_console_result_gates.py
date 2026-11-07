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
    # 保存按钮已从右侧控制台移走，只留顶部的「保存配置」
    assert ">保存</button>" not in html and ">读取</button>" not in html
    assert ">保存配置</button>" in html
    assert 'id="btnLoadBak"' not in html and "btnLoadBak" not in ui and "restoreBackup" not in ui
    assert "读取用 .bak" not in html
    assert 'id="btnWrite"' not in html and "btnWrite" not in ui
    assert html.index('id="btnDry"') < html.index('id="btnRun"')
    assert "run-actions" in html
    css_compact = css.replace(" ", "").replace("\n", "")
    assert ".pal-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));" in css_compact
    assert ".run-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));" in css_compact
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
    # 主数据卡也有「选择」按钮（与另两处同形）；一律走服务端原生对话框 /api/pick-file
    assert 'id="btnDataPick"' in html and 'id="ioDataPick"' not in html
    assert "pickExcelFromProject" in ui and "/api/pick-file" in ui
    # 操作卡：与配置页同一套「保存配置 / 重启服务」入口
    assert 'id="btnPvSave"' in html and '>保存配置</button>' in html
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


def test_modality_blocks_are_three_stages_and_combined_is_its_own_module():
    """组学模块按块三段式（预处理/筛选/模型，按方法给 lasso 或 logistic 参数）；Combined 主模型独立成段。"""
    from modules.console import schema

    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    css = (ROOT / "modules" / "console" / "console.css").read_text(encoding="utf-8")
    assert schema.SECTION_OF["COMBINED_MODE"][0] == "combined"
    assert schema.SECTION_OF["COMBINED_BLOCKS"][0] == "combined"
    assert schema.SECTION_OF["COMBINED_EXCLUDE"][0] == "combined"
    assert schema.SECTION_OF["CLIN_BLOCK_METHOD"][0] == "combined"
    radprep = [k for s, _t, ks in schema.SECTION_DEFS if s == "radprep" for k in ks]
    assert "MODALITY_BLOCKS" not in radprep
    assert "RAD_NORMALIZE" in radprep
    radblocks = [k for s, _t, ks in schema.SECTION_DEFS if s == "radblocks" for k in ks]
    assert radblocks == ["MODALITY_BLOCKS"]
    assert schema.SECTION_OF["MODALITY_BLOCKS"][0] == "radblocks"
    assert "function modalityBlocksPanel" in ui and "function combinedPanel" in ui
    assert 'data-sec="radblocks"' in ui and 'data-sec="combined"' in ui
    assert "blk-stage-k" in ui and "预处理" in ui and "筛选" in ui and "模型" in ui
    assert 'data-blk-param="' in ui and 'data-blk-param-bool="firth"' in ui
    assert "[data-blk-param], [data-blk-param-bool]" in ui
    assert ".blk-card" in css and ".blk-stage" in css


def test_omics_block_params_are_per_method_and_pruned():
    """每块参数只按当前 method 显示与保存；旧 ini 的跨方法参数（params.lasso.firth）载入即清掉。"""
    from modules.console import schema
    from modules.console.const import CONSOLE_DEFAULTS

    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    js_schema = (ROOT / "modules" / "console" / "console_schema.js").read_text(encoding="utf-8")
    # [object Object]：通用字段渲染器不再画 MODALITY_BLOCKS，块面板是唯一渲染器
    hidden = js_schema.split("const HIDDEN_FORM_KEYS = new Set([", 1)[1].split("]);", 1)[0]
    assert '"MODALITY_BLOCKS"' in hidden
    # 隐藏后仍保留「组学模块」这张卡（补空分组，标题取 schema 的 section_title，位置在 radprep 后）
    assert 'if (!map.has("radblocks"))' in ui
    assert 'title: (rb && rb.section_title) || "组学模块"' in ui
    assert 'groups.splice(rbIdx + 1, 0, g)' in ui
    # 「归一化」在组学模块标题行（默认开）
    assert schema.SECTION_OF["RAD_NORMALIZE"][0] == "radprep"
    assert schema.LABELS["RAD_NORMALIZE"] == "归一化"
    assert "归一化" in schema.HINTS["RAD_NORMALIZE"]
    assert CONSOLE_DEFAULTS["RAD_NORMALIZE"] is True
    assert (
        'titleKeys = ["RAD_NORMALIZE", "DO_ICC_FILTER", "ICC_REQUIRE_ROW", '
        '"ENABLE_CORR_FILTER", "LASSO_CACHE"]' in ui
    )
    # lasso 常用四项内联，其余进「更多参数」折叠；参数框 placeholder 显示当前全局值
    lasso_inline = ui.split("lasso: [", 1)[1].split("lasso_more: [", 1)[0]
    for name in ("prefilter_topk", "nfolds", "n_lambdas", "alpha"):
        assert f'["{name}",' in lasso_inline, name
    lasso_more = ui.split("lasso_more: [", 1)[1].split("logistic: [", 1)[0]
    for name in ("c_max", "c_min", "max_iter", "tol", "n_jobs"):
        assert f'["{name}",' in lasso_more, name
    logit = ui.split("logistic: [", 1)[1].split("const BLK_PARAM_KEYS", 1)[0]
    for name in ("uni_p", "mv_p"):
        assert f'["{name}",' in logit, name
    for key in ("LASSO_PREFILTER_TOPK", "LASSO_NFOLDS", "LASSO_N_LAMBDAS", "ALPHA",
                "LASSO_C_MAX", "LASSO_C_MIN", "LASSO_MAX_ITER", "LASSO_TOL",
                "LASSO_N_JOBS", "RAD_LOGIT_UNI_P", "RAD_LOGIT_MV_P"):
        assert f'"{key}"' in ui, key
    assert "function blkGlobalPlaceholder" in ui and '"全局 " + v' in ui
    assert 'data-fold="blk-more-params"' in ui and "更多参数" in ui
    # Firth 未设时跟随全局 DO_RAD_FIRTH，并在面板标「继承全局」
    assert "function blkFirthState" in ui and 'fieldValue("DO_RAD_FIRTH")' in ui
    assert "继承全局" in ui and "function blkInheritHint" in ui
    # 线性拟合开关已取消（线性拟合不再出现在块面板或 params 里）
    assert "data-blk-linear" not in ui and "blkLinearState" not in ui
    assert '"linear"' not in lasso_inline and '"linear"' not in logit
    # 换方法剪参数；载入清理；发给后端前再清一遍
    assert "function cleanBlkParams" in ui and "function sanitizeBlkParams" in ui
    assert "cur.params = cleanBlkParams(cur)" in ui
    assert "sanitizeBlkParams();" in ui
    assert "cleanModalityBlocks(overlay.MODALITY_BLOCKS)" in ui
    assert '"firth"' in ui  # logistic 的 firth 仍留在允许表里
    assert "参数继承全局" not in ui


def test_clinical_prep_and_filter_are_big_two_column_modules():
    """临床预处理 / 临床筛选各自整行铺开，块内两列（大模块）。"""
    css = (ROOT / "modules" / "console" / "console.css").read_text(encoding="utf-8")
    compact = css.replace(" ", "").replace("\n", "")
    tail = css.split("临床两大模块", 1)[1]
    assert '[data-sec="clinprep"].sec-body.clinprep-body,' in tail.replace(" ", "")
    assert '[data-sec="clin"].sec-body.clin-body{' in tail.replace(" ", "")
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in tail
    assert "grid-column:1/-1" in tail  # wide / span2 / pathlist 占满整行
    assert "max-width:1000px" in tail  # 窄屏退回单列
    assert compact.count("grid-template-columns:repeat(2,minmax(0,1fr))") >= 2


def test_exclude_cards_write_per_file_specs():
    """排除名单每张卡有自己的 sheet/列/内容，写 EXCLUDE_TESTS；旧 EXCLUDE_* 仍镜像首文件。"""
    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    js = (ROOT / "modules" / "console" / "console_schema.js").read_text(encoding="utf-8")
    defaults = (ROOT / "modules" / "config" / "defaults.py").read_text(encoding="utf-8")
    ini = (ROOT / "modules" / "config" / "ini_io.py").read_text(encoding="utf-8")
    assert "function excludeTests" in ui and "function setExcSpec" in ui
    assert 'data-exc-file="' in ui          # 每个 select 记住自己属于哪份文件
    assert 'setFieldValue("EXCLUDE_TESTS"' in ui
    assert '"EXCLUDE_TESTS"' in js.split("const HIDDEN_FORM_KEYS", 1)[1]
    assert '"EXCLUDE_TESTS": []' in defaults
    assert '"EXCLUDE_TESTS"' in ini


def test_external_cards_can_map_sheets_to_blocks():
    """有组学块时，外验每张表可选「块：NAME」，存成 EXTERNAL_TESTS.sheets 映射。"""
    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    assert "function extBlockNames" in ui and "function extRoleOptions" in ui
    assert '"blk:" + n' in ui
    assert 'role.indexOf("blk:") === 0' in ui
    assert "rec.sheets = sheetsMap" in ui
    assert 'item["sheets"] = sheets' in (ROOT / "modules" / "console" / "ops.py").read_text(encoding="utf-8")


def test_start_page_goes_straight_to_the_console_after_service_starts():
    """起始页：没有数据预览卡、没有「载入数据」按钮；开服务后自动进控制台「数据预览」。"""
    proj = (ROOT / "modules" / "console" / "project.html").read_text(encoding="utf-8")
    assert 'id="btnLoadNow"' not in proj and ">载入数据</button>" not in proj
    assert 'id="pvCard"' not in proj and 'id="pvBox"' not in proj
    assert "renderSheetBlocks" not in proj and 'id="pvMsg"' not in proj
    assert "btnGoPg" not in proj and ">进入数据预览</button>" not in proj
    assert "function autoOpenConsole" in proj and "autoOpenConsole(p)" in proj
    assert "autoLoadAndPreview" not in proj and "autoOpenPreview" not in proj
    assert '"tab", "preview"' in proj and '"tab", "config"' not in proj
    # 删了 #go 元素后，任何 `go.` / `if (go)` / setGoEnabled 都会在页面初始化时抛 ReferenceError，
    # 整个初始化中断 → 页面点不动（2026-11-04 真踩过）。
    assert 'getElementById("go")' not in proj
    for bad in ("if (!go)", "if (go)", "go.href", "go.classList", "setGoEnabled"):
        assert bad not in proj, bad
    # 空 sheet 参数不带，避免把 ini 里的值覆盖成空串
    assert "sheetParams.forEach" in proj and 'if (s) q.set(key, s)' in proj




def test_exclude_tests_gives_each_file_its_own_sheet_and_column(tmp_path):
    """EXCLUDE_TESTS：每个名单文件用自己那张表的 sheet / 列 / 内容（不再一套套所有文件）。"""
    from modules.stats.data_io import _EXCLUDE_CACHE, _row_id_is_excluded, load_exclude_id_keys

    a = tmp_path / "a.xlsx"
    b = tmp_path / "b.xlsx"
    pd.DataFrame({"exc": ["1001", "1002"]}).to_excel(a, sheet_name="exc", index=False)
    pd.DataFrame({"编号": ["2001"]}).to_excel(b, sheet_name="名单", index=False)

    _EXCLUDE_CACHE.clear()
    settings = {
        "DO_EXCLUDE_SAMPLES": True,
        "EXCLUDE_FILE": [str(a), str(b)],
        "EXCLUDE_SHEET": "exc", "EXCLUDE_COL": "exc", "EXCLUDE_ROW": "",
        "EXCLUDE_TESTS": [
            {"file": str(a), "sheet": "exc", "col": "exc", "row": ""},
            {"file": str(b), "sheet": "名单", "col": "编号", "row": ""},
        ],
    }
    keys = load_exclude_id_keys(None, settings)
    assert _row_id_is_excluded("1001", keys)
    assert _row_id_is_excluded("2001", keys)   # b 用自己那张表 + 自己的列
    assert not _row_id_is_excluded("9999", keys)

    # 旧口径（EXCLUDE_TESTS 空）：一套 sheet/列套到所有文件 → b 没有 exc 表，读不到 2001
    _EXCLUDE_CACHE.clear()
    legacy = dict(settings)
    legacy["EXCLUDE_TESTS"] = []
    keys2 = load_exclude_id_keys(None, legacy)
    assert _row_id_is_excluded("1001", keys2)
    assert not _row_id_is_excluded("2001", keys2)

    # EXCLUDE_TESTS 不是对象 → 明确报错，不静默跳过
    _EXCLUDE_CACHE.clear()
    bad = dict(settings)
    bad["EXCLUDE_TESTS"] = ["a.xlsx"]
    with pytest.raises(ValueError, match="EXCLUDE_TESTS\\[0\\]"):
        load_exclude_id_keys(None, bad)


def test_sync_external_tests_keeps_role_sheets():
    """外验卡上选的质控 / ICC 工作表、以及「块→工作表」映射都随 EXTERNAL_TESTS 保留。"""
    from modules.console.ops import sync_external_tests

    out = sync_external_tests({
        "EXTERNAL_TESTS": [{
            "file": "ext.xlsx", "clin_sheet": "cln", "rad_sheet": "CTA",
            "rad_id_col": "Patient_ID", "qc_sheet": "qc", "icc_sheet": "icc",
            "sheets": {"CTA": "CTA_ext", "US": "US_ext", "": "junk"},
        }],
    })
    spec = out["EXTERNAL_TESTS"][0]
    assert spec["clin_sheet"] == "cln" and spec["rad_sheet"] == "CTA"
    assert spec["qc_sheet"] == "qc" and spec["icc_sheet"] == "icc"
    assert spec["sheets"] == {"CTA": "CTA_ext", "US": "US_ext"}
    assert out["EXTERNAL_FILE"] == "ext.xlsx"


def test_external_specs_for_qc_keep_block_sheets():
    """u_qc 的外验 spec 归一化不丢 sheets（外验 QC / 预览要用）。"""
    from modules.utils.u_qc import _external_specs_from_settings

    specs = _external_specs_from_settings({
        "EXTERNAL_TESTS": [{
            "file": "ext.xlsx", "clin_sheet": "cln", "rad_sheet": "CTA",
            "sheets": {"CTA": "CTA_ext"},
        }],
    })
    assert specs and specs[0]["sheets"] == {"CTA": "CTA_ext"}


def test_default_exclude_xlsx_follows_each_outcome_folder(tmp_path, monkeypatch):
    """多结局：默认错分表写进当前结局的结果夹，不再共用一个工程根文件。"""
    from modules.stats import data_io

    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    mapping = {"Response": "ASL", "DSI": "Group"}
    per_outcome = []
    for outcome in ("Response", "DSI"):
        settings = {
            "DO_EXCLUDE_SAMPLES": True,
            "GROUP_SOURCE_COL": outcome,
            "OUT_DIR": mapping[outcome],
            "SUBPROJECT_BY_OUTCOME": mapping,
            "EXCLUDE_FILE": "",
        }
        per_outcome.append(data_io.false_classification_xlsx_path(settings=settings))
    assert [Path(p).parent.name for p in per_outcome] == ["ASL", "Group"]
    assert per_outcome[0] != per_outcome[1]
    assert all(Path(p).name == "false_classification.xlsx" for p in per_outcome)


def test_exclude_read_uses_the_folder_of_the_outcome_being_written(tmp_path, monkeypatch):
    """写哪个结局就读那个结局结果夹的名单；工程根的旧表不参与。"""
    from modules.stats.data_io import _EXCLUDE_CACHE, _row_id_is_excluded, load_exclude_id_keys

    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    outcome_dir = tmp_path / "ASL"
    outcome_dir.mkdir(parents=True)
    pd.DataFrame({"exc": ["resp-1"]}).to_excel(
        outcome_dir / "false_classification.xlsx", sheet_name="exc", index=False,
    )
    pd.DataFrame({"exc": ["root-1"]}).to_excel(
        tmp_path / "false_classification.xlsx", sheet_name="exc", index=False,
    )
    settings = {
        "DO_EXCLUDE_SAMPLES": True,
        "GROUP_SOURCE_COL": "Response",
        "OUT_DIR": "ASL",
        "EXCLUDE_FILE": "",
        "EXCLUDE_SHEET": "exc", "EXCLUDE_COL": "exc", "EXCLUDE_ROW": "",
        "EXCLUDE_IDS": [],
    }
    _EXCLUDE_CACHE.clear()
    keys = load_exclude_id_keys(None, settings)
    assert _row_id_is_excluded("resp-1", keys)
    assert not _row_id_is_excluded("root-1", keys)


def test_preview_resolves_default_exclude_inside_the_results_folder(tmp_path, monkeypatch):
    """预览读的是结果夹里的默认名单；数据表在子目录也能找到。"""
    from modules.console import preview as prev_mod

    monkeypatch.setenv("RAD_PROJECT", str(tmp_path))
    monkeypatch.setattr(prev_mod, "under_root", lambda p: Path(p))
    (tmp_path / "ref").mkdir(parents=True)
    (tmp_path / "ref" / "settings.ini").write_text("", encoding="utf-8")
    (tmp_path / "data").mkdir()
    book = tmp_path / "data" / "demo.xlsx"
    pd.DataFrame({"Patient_ID": ["a", "b"]}).to_excel(book, sheet_name="cln", index=False)
    (tmp_path / "ASL").mkdir()
    pd.DataFrame({"exc": ["a"]}).to_excel(
        tmp_path / "ASL" / "false_classification.xlsx", sheet_name="exc", index=False,
    )
    settings = {
        "CLIN_SHEET": "cln", "CLIN_ID_COL": "Patient_ID",
        "OUTCOME_COLS": ["Response"], "GROUP_SOURCE_COL": "Response",
        "OUT_DIR": "ASL", "SUBPROJECT_BY_OUTCOME": {"Response": "ASL"},
        "DO_EXCLUDE_SAMPLES": True, "EXCLUDE_FILE": "",
        "EXCLUDE_SHEET": "exc", "EXCLUDE_COL": "exc", "EXCLUDE_ROW": "",
    }
    pv = prev_mod.preview_workbook(book, dict(settings))
    exc = pv.get("exclude") or {}
    assert Path(str(exc.get("file"))).name == "false_classification.xlsx"
    assert str(tmp_path / "ASL") in str(exc.get("file"))
    assert exc.get("n") == 1


def test_fig_labels_panel_edits_in_figure_texts():
    """图内软标签：schema 归「图与显示」、注册表注入 build_schema、面板按图分组写 FIG_LABELS。"""
    from modules.config.defaults import DEFAULT_SETTINGS, merged_settings
    from modules.config.fig_labels import figure_specs
    from modules.console.console import HINTS, LABELS, SECTION_OF, build_schema
    from modules.console.const import CONSOLE_DEFAULTS

    assert SECTION_OF["FIG_LABELS"][0] == "fig"
    assert LABELS["FIG_LABELS"] == "图内标签（软标签）"
    assert "留空=用默认" in HINTS["FIG_LABELS"]
    assert CONSOLE_DEFAULTS["FIG_LABELS"] == {}
    assert DEFAULT_SETTINGS["FIG_LABELS"] == {}

    specs = figure_specs()
    assert len(specs) >= 12
    assert any(g["id"] == "legend" for g in specs)
    legend = next(g for g in specs if g["id"] == "legend")
    assert {"Clinical", "RadScore", "Combined"} <= {f["key"] for f in legend["fields"]}

    fields = {f["key"]: f for f in build_schema(dict(merged_settings()))}
    assert fields["FIG_LABELS"]["section"] == "fig"
    assert fields["FIG_LABELS"]["figures"] == specs
    assert fields["FIG_LABELS"]["value"] == {}

    ui = (ROOT / "modules" / "console" / "console_ui.js").read_text(encoding="utf-8")
    js_schema = (ROOT / "modules" / "console" / "console_schema.js").read_text(encoding="utf-8")
    hidden = js_schema.split("const HIDDEN_FORM_KEYS = new Set([", 1)[1].split("]);", 1)[0]
    assert '"FIG_LABELS"' in hidden
    assert "function figLabelsPanel" in ui and "function figLabelGroup" in ui
    assert "function figLegendKeys" in ui and "blockScoreNames()" in ui
    assert 'data-fig-label=' in ui and 'data-fig-key=' in ui and 'data-fig-reset=' in ui
    assert 'data-fold="FIG_LABELS"' in ui
    assert "图内标签（软标签）" in ui
    assert 'setFieldValue("FIG_LABELS", all)' in ui
    # 空输入=删覆盖→用默认；placeholder 就是默认值
    assert "if (s) one[k] = s;" in ui and "else delete one[k];" in ui
    assert 'placeholder="${esc(def)}"' in ui
    # 面板占满「图与显示」整行（不然挤在 1/8 窄格里，输入框只剩几个字符）
    css = (ROOT / "modules" / "console" / "console.css").read_text(encoding="utf-8")
    assert 'details[data-fold="FIG_LABELS"]' in css and "grid-column:1/-1" in css
    assert ".kv-rows.fig-labels .kv-row" in css
    assert "minmax(11em,auto) minmax(0,1fr)" in css
