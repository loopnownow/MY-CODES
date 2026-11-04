"""列线图变量轴名：保留原始大小写（CTA 不能变成 Cta）。"""
from __future__ import annotations

from modules.stats.models import FEATURE_DISPLAY, _display_name


def test_block_score_names_keep_original_case():
    assert _display_name("RadScore_CTA") == "RadScore CTA"
    assert _display_name("RadScore_US") == "RadScore US"
    assert _display_name("CTA") == "CTA"


def test_lowercase_features_still_get_a_capital():
    assert _display_name("lesion_volume") == "Lesion Volume"
    assert _display_name("idh_mutation") == "Idh Mutation"


def test_known_display_map_still_wins():
    assert _display_name("age") == FEATURE_DISPLAY["age"]
    assert _display_name("RadScore") == "RadScore"
