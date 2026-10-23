"""展示名进表头，组学 ID 下拉不塞特征名，图能画出中文。"""
from __future__ import annotations

import io
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from modules.config.outcome import outcome_display_labels
from modules.config.style import apply_clean_theme, apply_fig_font, fig_font_chain
from modules.console.console import _id_dropdown_columns


def test_single_outcome_keeps_public_display_names():
    pos, neg = outcome_display_labels(
        "Group", base_pos="AC", base_neg="NC", override={}, n_outcomes=1,
    )
    assert (pos, neg) == ("AC", "NC")


def test_multi_outcome_without_override_keeps_card_names():
    pos, neg = outcome_display_labels(
        "LVSI", base_pos="AC", base_neg="NC", override={}, n_outcomes=3,
    )
    assert (pos, neg) == ("AC", "NC")


def test_outcome_override_display_wins():
    pos, neg = outcome_display_labels(
        "Group",
        base_pos="AC",
        base_neg="NC",
        override={"GROUP_DISPLAY_POS": "CCC", "GROUP_DISPLAY_NEG": "ECC"},
        n_outcomes=1,
    )
    assert (pos, neg) == ("CCC", "ECC")


def test_level_map_names_the_raw_group_not_the_class_slot(monkeypatch):
    from modules import config as cfg
    from modules.stats.data_io import _display_group_labels

    monkeypatch.setattr(cfg, "GROUP_LEVEL_DISPLAY", {"实验组": "AC", "对照组": "NC"}, raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_POS", "Group", raising=False)
    monkeypatch.setattr(cfg, "GROUP_DISPLAY_NEG", "non_Group", raising=False)
    # 实验组是 y=0，仍应叫 AC，不能因为落在阴性槽就叫 non_Group
    assert _display_group_labels("实验组", "对照组") == ("AC", "NC")


def test_wide_sheet_id_dropdown_drops_feature_names():
    cols = ["Pateint_ID"] + [f"0_平滑追踪-{i}" for i in range(40)]
    got = _id_dropdown_columns(cols, configured="Pateint_ID")
    assert got == ["Pateint_ID"]


def test_short_sheet_id_dropdown_keeps_every_column():
    cols = ["Patient_ID", "Group", "Age"]
    assert _id_dropdown_columns(cols, configured="Patient_ID") == cols


def test_figure_draws_cjk_without_missing_glyph():
    chain = fig_font_chain("Arial")
    assert chain[0] == "Arial"
    assert any(name != "Arial" for name in chain)
    apply_fig_font("Arial")
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.set_title("性别")
    ax.plot([0, 1], [0, 1], label="年龄")
    ax.legend()
    apply_clean_theme(ax)
    buf = io.BytesIO()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fig.savefig(buf, format="png")
    plt.close(fig)
    missing = [str(w.message) for w in caught if "Glyph" in str(w.message)]
    assert missing == []
