"""控制台 Round2 界面契约（c2b38fa）静态回归：读金版 HTML/JS/CSS/schema/defaults 断言，不启浏览器。

契约逐条对应 c2b38fa 提交说明；注释侧证据见 modules/console/_design_comments_harvest_t1677u.md。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from modules.config.defaults import DEFAULT_SETTINGS
from modules.stats.figure_strobe_flow import audit_study

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "modules" / "console"

HTML = (CONSOLE / "console.html").read_text(encoding="utf-8")
PROJECT = (CONSOLE / "project.html").read_text(encoding="utf-8")
CSS = (CONSOLE / "console.css").read_text(encoding="utf-8")
UI = (CONSOLE / "console_ui.js").read_text(encoding="utf-8")
SCHEMA_JS = (CONSOLE / "console_schema.js").read_text(encoding="utf-8")
STROBE = (CONSOLE / "strobe" / "index.html").read_text(encoding="utf-8")
SCHEMA = (ROOT / "modules" / "console" / "schema.py").read_text(encoding="utf-8")
INI_IO = (ROOT / "modules" / "config" / "ini_io.py").read_text(encoding="utf-8")
CSS_TIGHT = re.sub(r"\s+", "", CSS)

DATA_PANE_KEYS = ["CLIN_SCORE", "QC_ID_COL", "QC_SCORE", "ICC_ID_COL", "ICC_SCORE"]
NEW_KEYS = DATA_PANE_KEYS + ["NOMOGRAM_SOURCE", "ROC_PLOT_FEATURES", "ROC_PLOT_SCORE_CURVES"]


def test_start_page_splits_open_and_close_below_the_port_probe():
    """开/关拆两键，且都排在端口探测行之后。"""
    assert 'id="btnSvcOn"' in PROJECT and 'id="btnSvcOff"' in PROJECT
    assert PROJECT.index('id="portRow"') < PROJECT.index('id="btnSvcOn"')
    assert PROJECT.index('id="btnSvcOn"') < PROJECT.index('id="btnSvcOff"')
    assert "开启服务" in PROJECT and "关闭服务" in PROJECT


def test_no_ext_split_param_left_anywhere():
    """隐藏的 extSplit 与 ext_split 传参已删干净（含 js / py / css / html）。"""
    offenders = []
    for path in sorted(CONSOLE.rglob("*")):
        if path.suffix not in {".html", ".js", ".css", ".py"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "extSplit" in text or "ext_split" in text:
            offenders.append(path.name)
    assert offenders == []


def test_data_path_is_folded_into_the_advanced_details():
    """数据位置在「高级」折叠块内，不在课题路径主区露出。"""
    start = PROJECT.index('<details class="adv-row">')
    end = PROJECT.index("</details>", start)
    assert start < PROJECT.index('id="projDir"') < end
    # 主区（课题路径标题到 details 之前）不再出现数据位置
    assert 'id="projDir"' not in PROJECT[PROJECT.index('id="h-cfg"'):start]


def test_preview_three_columns_and_breakpoints_match_the_config_page():
    """数据预览三列宽度与断点照抄统计配置。"""
    three = "minmax(220px,280px)minmax(0,1fr)440px"
    assert ".preview-page{display:grid;grid-template-columns:" + three in CSS_TIGHT
    assert "main:has(#runCol:not([hidden])){grid-template-columns:" + three in CSS_TIGHT
    # 1280 断点：两侧同步收窄到 400px
    assert CSS_TIGHT.count("minmax(200px,240px)minmax(0,1fr)400px") >= 2
    assert ".preview-page{grid-template-columns:minmax(0,1fr)}" in CSS_TIGHT


def test_ids_panel_sits_inside_the_main_file_card():
    """ID 列 / 非特征列在「主数据」文件卡内，不在外验或排除名单段里。"""
    card = HTML.index('data-file-card="main"')
    assert card < HTML.index('id="pvIds"') < HTML.index('data-pvsec="ext"')
    assert '$("pvIds")' in UI


def test_external_pick_uses_the_project_root_dialog_and_readonly_input():
    """外验只选不填：服务端原生对话框 + 只读输入框。"""
    assert 'api("/api/pick-file", {' in UI
    assert 'JSON.stringify({dir: String(entry.dir || ""), title:' in UI
    io_data = HTML.index('id="ioData"')
    assert "readonly" in HTML[io_data:io_data + 200]
    assert 'id="btnDataPick"' in HTML


def test_role_table_carries_qc_and_icc_id_and_score():
    """角色表有分数名列（临床默认 Clinical），QC / ICC 各有 ID 列与分数名。"""
    assert "分数名" in UI
    assert 'placeholder="Clinical"' in UI
    for key in DATA_PANE_KEYS:
        assert key in UI
        assert key in SCHEMA


def test_save_button_reads_save_config_in_the_header():
    """「保存 ini」文案已改「保存配置」，且留在 tabs 之后的表头。"""
    assert ">保存配置</button>" in HTML
    assert "保存 ini" not in HTML and "保存 ini" not in UI
    assert HTML.index('id="tabBtnFlow"') < HTML.index('id="btnPvSave"')


def test_internal_split_switch_moved_to_the_repro_title_row():
    assert 'titleKeys = ["USE_INTERNAL_SPLIT_WHEN_EXTERNAL"]' in UI
    assert 'data-sec="repro"' in UI


def test_omics_prep_merges_into_the_omics_module():
    assert 'mergeGroup("radprep", "radblocks")' in UI
    assert "组学模块：预处理并入后整卡单列堆叠" in CSS


def test_heatmap_merges_into_the_correlation_section():
    assert 'mergeGroup("corr", "pcorr", "相关 / 偏相关")' in UI
    assert 'titleKeys = ["DO_HEATMAP", "DO_CORR", "DO_PARTIAL"]' in UI


def test_omics_block_score_name_is_readonly_text():
    """组学块不再有可改的模型分数名输入框，分数名回「数据预览」的角色表设。"""
    assert 'class="blk-score mono"' in UI
    assert "data-blk-score" not in UI
    assert "分数名在「数据预览」页的该工作表行里设" in UI


def test_linear_fit_switch_is_gone_from_the_console():
    """线性拟合开关已取消：默认 true，但控制台不再有勾选（分节与标题行都没有）。"""
    assert DEFAULT_SETTINGS["CLIN_SCORE_LINEAR"] is True
    assert DEFAULT_SETTINGS["RAD_SCORE_LINEAR"] is True
    assert (
        'titleKeys = ["DO_UNI_LOGISTIC_TABLE", "DO_MV_LOGISTIC_TABLE", "DO_FIRTH"]'
        in UI
    )
    assert '"CLIN_SCORE_LINEAR"' not in SCHEMA
    assert "data-blk-param-bool=\"linear\"" not in UI
    assert "筛选后一律原始尺度 OLS" in UI


def test_six_new_keys_reach_schema_defaults_and_ini():
    """六枚新键：schema 有键与说明、defaults 有值、ini_io 有分节、预览五键在 console_schema.js。"""
    for key in NEW_KEYS:
        assert f'"{key}"' in SCHEMA
        assert key in DEFAULT_SETTINGS
    for key in NEW_KEYS:
        assert f'"{key}"' in INI_IO
    for key in DATA_PANE_KEYS:
        assert f'"{key}"' in SCHEMA_JS
    for key in DATA_PANE_KEYS:
        assert DEFAULT_SETTINGS[key] == ""
    assert DEFAULT_SETTINGS["CLIN_SCORE_LINEAR"] is True
    assert DEFAULT_SETTINGS["NOMOGRAM_SOURCE"] == "post"
    assert DEFAULT_SETTINGS["ROC_PLOT_FEATURES"] is True
    assert DEFAULT_SETTINGS["ROC_PLOT_SCORE_CURVES"] is False


def test_flowchart_audits_only_the_numbers_it_is_given():
    """缺数留空：audit 放行缺项，只校验已给数字；图上画 n = -。"""
    audit_study({})
    audit_study({"included_n": 10})
    doc = inspect.getdoc(audit_study) or ""
    assert "只校验已给数字" in doc
    assert "n = -" in STROBE


def test_rad_normalize_is_a_title_toggle_on_the_omics_module():
    """「归一化」在「组学模块」标题行（title-sw 同机制），schema / defaults 都有，默认开。"""
    from modules.console.const import CONSOLE_DEFAULTS
    from modules.console.schema import HINTS, LABELS, SECTION_OF

    assert SECTION_OF["RAD_NORMALIZE"][0] == "radprep"
    assert LABELS["RAD_NORMALIZE"] == "归一化"
    assert HINTS["RAD_NORMALIZE"]
    assert CONSOLE_DEFAULTS["RAD_NORMALIZE"] is True
    assert '"RAD_NORMALIZE"' in SCHEMA
    assert (
        'titleKeys = ["RAD_NORMALIZE", "DO_ICC_FILTER", "ICC_REQUIRE_ROW", '
        '"ENABLE_CORR_FILTER", "LASSO_CACHE"]' in UI
    )
    assert "function titleSwitchHtml" in UI


def test_marker_matching_and_strip_cover_middle_segments():
    """标记支持 mid: 中间片段：识别、排除、分类、剥离同一套匹配；手填 _x_ 存成 mid:x。"""
    from modules.console.schema import HINTS

    assert 'if (raw.indexOf("mid:") === 0) return { anchor: "mid", part: raw.slice(4) };' in UI
    assert "function delimiterAligned" in UI
    assert "function stripAnchoredToken" in UI
    assert "function radMidCandidates" in UI
    assert "RAD_MID_MAX_ROWS = 40" in UI
    assert 'counts.set("mid:" + row[0], row[1])' in UI
    assert 'return "mid:" + core;' in UI
    assert '["mid", "中间"]' in UI
    assert '["both", "全部"]' in UI
    assert 'spec.anchor === "mid" ? "中间"' in UI
    # 识别出的中间标记走同一套匹配：排除 / 分类 / 剥离都共用 columnMatchesPrefix
    assert "radMidCandidates(name)" in UI
    assert "columnMatchesPrefix(name, prefix)" in UI
    assert "中间" in HINTS["RAD_MARKER_SIDE"]
    assert "全部" in HINTS["RAD_MARKER_SIDE"]
    assert "mid:" in HINTS["RAD_FEATURE_PREFIX_EXCLUDE"]
