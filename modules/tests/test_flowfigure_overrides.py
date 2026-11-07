# -*- coding: utf-8 -*-
"""overrides：平移 box 连带边界 edge、hidden 丢 box/edge、未知 id 告警、页面内 clamp。"""
from __future__ import annotations

import pytest

from modules.flowfigure import layout, overrides, spec as spec_mod

SPEC = {
    "layout": "expanded-split-downsample",
    "screened": "Eligible patients (n = 100)",
    "inclusion": ["Inclusion one", "Inclusion two"],
    "presplit": {"training": {"label": "Training", "n": 60, "pp": 20, "non_pp": 40},
                 "test": {"label": "Test", "n": 40, "pp": 10, "non_pp": 30}},
    "downsample": {"n": 10, "reason": "Downsampled for class imbalance"},
    "analyzed": "90 patients analyzed (30 positive vs 60 negative)",
    "splits": [{"label": "Training Cohort", "n": 50, "pp": 20, "non_pp": 30},
               {"label": "Test Cohort", "n": 40, "pp": 10, "non_pp": 30}],
}


def _geometry():
    return layout.build(spec_mod.normalize(SPEC))


def _box(geometry, box_id):
    return next(box for box in geometry["boxes"] if box["id"] == box_id)


def test_move_box_translates_boundary_edge_endpoints():
    geometry = _geometry()
    box = _box(geometry, "analyzed")
    old_x, old_y = box["x"], box["y"]
    edges_before = geometry["edges"]
    attached = [
        index for index, edge in enumerate(edges_before)
        if abs(edge["b"][0] - (old_x + box["w"] / 2)) < 1e-6 and abs(edge["b"][1] - old_y) < 1e-6
    ]
    assert attached

    result = overrides.apply_overrides(geometry, {"boxes": {"analyzed": {"x": old_x + 0.3, "y": old_y - 0.1}}})
    moved = _box(result, "analyzed")
    assert moved["x"] == old_x + 0.3
    assert moved["y"] == old_y - 0.1
    for index in attached:
        endpoint = result["edges"][index]["b"]
        assert endpoint[0] == pytest.approx(old_x + box["w"] / 2 + 0.3)
        assert endpoint[1] == pytest.approx(old_y - 0.1)
    assert result["page_in"] == geometry["page_in"]


def test_hidden_drops_box_and_edges_inside_it():
    geometry = _geometry()
    hidden_rect = _box(geometry, "inclusion")
    result = overrides.apply_overrides(geometry, {"hidden": ["inclusion"]})
    assert "inclusion" not in {box["id"] for box in result["boxes"]}
    assert len(result["edges"]) < len(geometry["edges"])
    for edge in result["edges"]:
        for end in ("a", "b"):
            x, y = edge[end]
            inside = (
                hidden_rect["x"] - 0.02 <= x <= hidden_rect["x"] + hidden_rect["w"] + 0.02
                and hidden_rect["y"] - 0.02 <= y <= hidden_rect["y"] + hidden_rect["h"] + 0.02
            )
            assert not inside


def test_unknown_ids_are_dropped_with_warnings():
    result = overrides.apply_overrides(
        _geometry(),
        {"boxes": {"nope": {"x": 1, "y": 1}}, "hidden": ["missing"]},
    )
    assert len(result["warnings"]) == 2


def test_move_is_clamped_to_page():
    geometry = _geometry()
    box = _box(geometry, "analyzed")
    result = overrides.apply_overrides(
        geometry, {"boxes": {"analyzed": {"x": -5, "y": 999}}},
    )
    moved = _box(result, "analyzed")
    assert moved["x"] == 0.0
    assert moved["y"] == result["page_in"][1] - box["h"]
