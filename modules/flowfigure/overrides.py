# -*- coding: utf-8 -*-
"""spec.overrides -> geometry 应用层（平移 / 隐藏），在版式定稿、渲染之前执行。

overrides 结构::

    {"boxes": {"analyzed": {"x": 3.4, "y": 9.1}},
     "hidden": ["inclusion"]}

规则：
- ``boxes[id]`` 平移该 box；落在原矩形边界（含边界，eps=0.02 in）的 edge 端点
  一起平移；x/y clamp 在页面内。
- ``hidden`` 里的 box 不画，端点在它矩形内的 edge 一并丢弃。
- 未知 id 丢弃并写入 ``geometry["warnings"]``。
- 不重新排版、不重新缩放：页面尺寸与 page_in 保持不变。
"""
from __future__ import annotations

import copy

EPS_IN = 0.02


def _inside(point, rect) -> bool:
    x, y = point
    x0, y0, x1, y1 = rect
    return (x0 - EPS_IN) <= x <= (x1 + EPS_IN) and (y0 - EPS_IN) <= y <= (y1 + EPS_IN)


def _on_boundary(point, rect) -> bool:
    x, y = point
    x0, y0, x1, y1 = rect
    if not _inside(point, rect):
        return False
    return (
        abs(x - x0) <= EPS_IN or abs(x - x1) <= EPS_IN
        or abs(y - y0) <= EPS_IN or abs(y - y1) <= EPS_IN
    )


def _rect(box: dict) -> tuple[float, float, float, float]:
    return (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"])


def apply_overrides(geometry: dict, overrides) -> dict:
    """返回应用 overrides 后的 geometry（深拷贝；失败抛 ValueError）。"""
    geometry = copy.deepcopy(geometry)
    overrides = overrides if overrides is not None else {}
    if not isinstance(overrides, dict):
        raise ValueError("overrides 必须是对象")
    warnings = geometry.setdefault("warnings", [])
    page_w, page_h = geometry["page_in"]
    by_id = {box["id"]: box for box in geometry["boxes"]}

    hidden = overrides.get("hidden") or []
    if not isinstance(hidden, list):
        raise ValueError("overrides.hidden 必须是数组")
    hidden_boxes = []
    for raw_id in hidden:
        box = by_id.get(str(raw_id))
        if box is None:
            warnings.append(f"overrides.hidden: 未知 box id {raw_id!r}，已丢弃")
            continue
        hidden_boxes.append(box)
    if hidden_boxes:
        removed = {box["id"] for box in hidden_boxes}
        geometry["boxes"] = [box for box in geometry["boxes"] if box["id"] not in removed]
        rects = [_rect(box) for box in hidden_boxes]
        geometry["edges"] = [
            edge for edge in geometry["edges"]
            if not any(_inside(edge["a"], rect) or _inside(edge["b"], rect) for rect in rects)
        ]
        by_id = {box["id"]: box for box in geometry["boxes"]}

    moves = overrides.get("boxes") or {}
    if not isinstance(moves, dict):
        raise ValueError("overrides.boxes 必须是对象")
    for raw_id, pos in moves.items():
        box = by_id.get(str(raw_id))
        if box is None:
            warnings.append(f"overrides.boxes: 未知 box id {raw_id!r}，已丢弃")
            continue
        if not isinstance(pos, dict):
            raise ValueError(f"overrides.boxes[{raw_id!r}] 必须是 {{x, y}} 对象")
        old_x, old_y = box["x"], box["y"]
        target_x = float(pos.get("x", old_x))
        target_y = float(pos.get("y", old_y))
        target_x = min(max(0.0, target_x), max(0.0, page_w - box["w"]))
        target_y = min(max(0.0, target_y), max(0.0, page_h - box["h"]))
        dx, dy = target_x - old_x, target_y - old_y
        if abs(dx) < 1e-12 and abs(dy) < 1e-12:
            continue
        rect = _rect(box)
        box["x"] = target_x
        box["y"] = target_y
        for edge in geometry["edges"]:
            for end in ("a", "b"):
                if _on_boundary(edge[end], rect):
                    edge[end] = [edge[end][0] + dx, edge[end][1] + dy]
    return geometry
