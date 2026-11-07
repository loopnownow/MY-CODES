# -*- coding: utf-8 -*-
"""canonical spec v3 -> geometry（单一版式；英寸坐标，box id 稳定）。

顺序：screened -> inclusion/exclusion（右侧）-> included（可选）-> presplit 臂 ->
downsample -> cohorts -> external（与验证组同排，左侧虚线挂）-> pipeline steps ->
analyzed 只画汇合线标签（不画框）。

geometry::

    {
      "layout": <来源标记>,
      "page_in": [w, h], "page_cm": [w_cm, h_cm], "scale_x": float,
      "boxes": [{"id", "x", "y", "w", "h", "lines", "fs", "align"}],
      "labels": [{"id", "text", "x", "y", "fs", "align"}],
      "edges": [{"id", "kind": "arrow" | "line" | "dashed", "a": [x, y], "b": [x, y]}],
      "warnings": [str]
    }

页宽规则：目标 16 cm；内容更窄时横向等比铺满，内容更宽时按内容扩画布；
超过 MAX_WIDTH_CM（17.2 cm）只给 warning，仍出图。
"""
from __future__ import annotations

import re
import textwrap

from matplotlib import font_manager
from matplotlib.font_manager import FontProperties
from matplotlib.textpath import TextPath

from modules.flowfigure import spec as spec_mod

# -- CONFIG -------------------------------------------------------------------
FS = spec_mod.DEFAULT_FONT_SIZE_PT
FS_SIDE = FS - 0.3
TEXT_WIDTH = 30
COL_GAP = 0.34
COHORT_GAP = 0.22
FORK_DROP = 0.46
STEM_MIN = 0.70
PAGE_TARGET_IN = spec_mod.TARGET_WIDTH_CM / spec_mod.CM_PER_INCH
PAGE_MAX_CM = spec_mod.MAX_WIDTH_CM
PAGE_MARGIN_IN = spec_mod.MARGIN_IN
DEFAULT_ANALYZED_LABEL = "Analyzed"

_N_EQ = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)


def _font() -> str:
    for name in ("Arial", "Calibri", "Helvetica", "DejaVu Sans"):
        try:
            path = font_manager.findfont(name, fallback_to_default=False)
            if not path:
                continue
            if name != "DejaVu Sans" and "DejaVuSans" in path.replace("\\", "/"):
                continue
            return name
        except Exception:
            continue
    return "DejaVu Sans"


FONT = _font()


def _wrap(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return textwrap.fill(text, width=width, break_long_words=False)


def _line_w(line: str, fs: float) -> float:
    fp = FontProperties(family=FONT, size=fs)
    tp = TextPath((0, 0), line or " ", size=fs, prop=fp)
    return max(0.06, tp.get_extents().width / 72.0)


def _box_wh(text: str, fs: float = FS, pad_x: float = 0.36, pad_y: float = 0.12,
            min_w: float = 1.2, max_w: float = 6.2) -> tuple[float, float]:
    lines = (text or " ").split("\n")
    w = min(max_w, max(min_w, max(_line_w(line, fs) for line in lines) + pad_x))
    h = max(0.36, 2 * pad_y + (fs / 72.0) * 1.42 * len(lines))
    return w, h


def _lines(text: str) -> list[str]:
    return str(text or "").split("\n")


def _text_of(node: dict) -> str:
    return str(node.get("text") or "").strip()


def node_text(node: dict) -> str:
    """臂/队列/外验统一记法：label / n=N / PP n=… · non-PP n=…。"""
    lines = []
    label = _text_of(node) or str(node.get("label") or "").strip()
    if label:
        lines.extend(_wrap(label, TEXT_WIDTH).split("\n"))
    n = spec_mod.parse_n(node.get("n"))
    pp = spec_mod.parse_n(node.get("pp"))
    non_pp = spec_mod.parse_n(node.get("non_pp"))
    if n is not None:
        lines.append(f"n={n}")
    if pp is not None and non_pp is not None:
        lines.append(f"PP n={pp} · non-PP n={non_pp}")
    return "\n".join(lines)


def downsample_text(entry: dict) -> str:
    lines = []
    reason = str(entry.get("reason") or "").strip()
    if reason:
        lines.extend(_wrap(reason, TEXT_WIDTH).split("\n"))
    n = spec_mod.parse_n(entry.get("n"))
    if n is not None:
        lines.append(f"n={n}")
    if not lines:
        lines = [f"n={n}" if n is not None else "Downsample"]
    return "\n".join(lines)


def analyzed_label(analyzed: dict) -> str:
    """优先 label；否则 n/pp/non_pp；再否则原文折行。"""
    label = str(analyzed.get("label") or "").strip()
    if label:
        return label
    n = spec_mod.parse_n(analyzed.get("n"))
    pp = spec_mod.parse_n(analyzed.get("pp"))
    non_pp = spec_mod.parse_n(analyzed.get("non_pp"))
    if n is not None and pp is not None and non_pp is not None:
        return f"n={n} (PP n={pp} · non-PP n={non_pp})"
    if n is not None:
        return f"n={n}"
    text = _text_of(analyzed)
    return _wrap(text, 46) if text else ""


def exclusion_block(criteria: dict) -> str:
    items = criteria.get("exclusion") or []
    if not items:
        return ""
    values = [spec_mod.parse_n(item.get("n")) for item in items]
    title = "Exclusion criteria"
    if values and all(value is not None for value in values):
        title += f" (n={sum(values)})"
    lines = [title]
    for indexed, (item, value) in enumerate(zip(items, values), 1):
        reason = str(item.get("reason") or "").strip()
        if value is None:
            value = spec_mod.parse_n(reason)
        text = f"{reason} (n={value})" if value is not None else reason
        lines.append(_wrap(f"{indexed}. {text}", 36))
    return "\n".join(lines)


def inclusion_block(criteria: dict) -> str:
    items = [str(item).strip() for item in (criteria.get("inclusion") or []) if str(item).strip()]
    if not items:
        return ""
    lines = ["Inclusion criteria"]
    for indexed, item in enumerate(items, 1):
        lines.append(_wrap(f"{indexed}. {item}", 36))
    return "\n".join(lines)


# -- geometry recorder --------------------------------------------------------
class _Geo:
    def __init__(self) -> None:
        self.boxes: list[dict] = []
        self.labels: list[dict] = []
        self.edges: list[dict] = []
        self.warnings: list[str] = []

    def box(self, x, y, w, h, text, box_id: str, fs: float = FS,
            align: str = "center") -> dict:
        item = {
            "id": box_id, "x": float(x), "y": float(y),
            "w": float(w), "h": float(h), "lines": _lines(text),
            "fs": float(fs), "align": align,
        }
        self.boxes.append(item)
        return item

    def label(self, x, y, text, label_id: str, fs: float = FS,
              align: str = "center") -> dict:
        item = {
            "id": label_id, "text": str(text), "x": float(x), "y": float(y),
            "fs": float(fs), "align": align,
        }
        self.labels.append(item)
        return item

    def arrow(self, a, b) -> None:
        self.edges.append({
            "id": f"e-{len(self.edges)}", "kind": "arrow",
            "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])],
        })

    def line(self, a, b) -> None:
        self.edges.append({
            "id": f"e-{len(self.edges)}", "kind": "line",
            "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])],
        })

    def dashed(self, a, b) -> None:
        self.edges.append({
            "id": f"e-{len(self.edges)}", "kind": "dashed",
            "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])],
        })

    def _bounds(self) -> tuple[float, float, float, float]:
        if not self.boxes:
            raise ValueError("no boxes to draw")
        xs0 = min(box["x"] for box in self.boxes)
        ys0 = min(box["y"] for box in self.boxes)
        xs1 = max(box["x"] + box["w"] for box in self.boxes)
        ys1 = max(box["y"] + box["h"] for box in self.boxes)
        for label in self.labels:
            width = max(
                (_line_w(line, label["fs"]) for line in str(label["text"]).split("\n")),
                default=0.0,
            )
            height = (label["fs"] / 72.0) * 1.4 * len(str(label["text"]).split("\n"))
            left = label["x"] - width / 2
            xs0 = min(xs0, left)
            xs1 = max(xs1, left + width)
            ys0 = min(ys0, label["y"])
            ys1 = max(ys1, label["y"] + height)
        for edge in self.edges:
            xs0 = min(xs0, edge["a"][0], edge["b"][0])
            ys0 = min(ys0, edge["a"][1], edge["b"][1])
            xs1 = max(xs1, edge["a"][0], edge["b"][0])
            ys1 = max(ys1, edge["a"][1], edge["b"][1])
        return xs0, ys0, xs1, ys1

    def fit_page(self) -> tuple[float, float, float]:
        x0, y0, x1, y1 = self._bounds()
        margin = PAGE_MARGIN_IN
        content_w = max(1e-6, x1 - x0)
        content_h = max(1e-6, y1 - y0)
        page_w = max(PAGE_TARGET_IN, content_w + 2 * margin)
        scale = 1.0
        if page_w <= PAGE_TARGET_IN + 1e-9:
            page_w = PAGE_TARGET_IN
            scale = (PAGE_TARGET_IN - 2 * margin) / content_w
        actual_cm = page_w * spec_mod.CM_PER_INCH
        if actual_cm > PAGE_MAX_CM + 1e-9:
            self.warnings.append(
                f"page width {actual_cm:.1f} cm exceeds MAX_WIDTH_CM {PAGE_MAX_CM:.1f} cm; "
                "drawn at content width"
            )
        page_h = content_h + 2 * margin

        def tx(value: float) -> float:
            return margin + (value - x0) * scale

        def ty(value: float) -> float:
            return margin + (value - y0)

        for box in self.boxes:
            box["x"] = tx(box["x"])
            box["y"] = ty(box["y"])
            box["w"] = box["w"] * scale
        for label in self.labels:
            label["x"] = tx(label["x"])
            label["y"] = ty(label["y"])
        for edge in self.edges:
            edge["a"] = [tx(edge["a"][0]), ty(edge["a"][1])]
            edge["b"] = [tx(edge["b"][0]), ty(edge["b"][1])]
        return page_w, page_h, scale

    def finish(self, layout: str) -> dict:
        page_w, page_h, scale = self.fit_page()
        return {
            "layout": layout,
            "page_in": [page_w, page_h],
            "page_cm": [page_w * spec_mod.CM_PER_INCH, page_h * spec_mod.CM_PER_INCH],
            "scale_x": scale,
            "boxes": self.boxes,
            "labels": self.labels,
            "edges": self.edges,
            "warnings": self.warnings,
        }


def _cx(box: dict) -> float:
    return box["x"] + box["w"] / 2


def _bottom(box: dict) -> float:
    return box["y"] + box["h"]


def _text_box(text: str, fs: float, max_w: float = 6.2) -> tuple[float, float]:
    return _box_wh(text, fs, max_w=max_w)


def _place_row(g: _Geo, items: list[tuple[str, str, float, float]], center_x: float,
               y: float, gap: float, fs: float) -> list[dict]:
    """items: (id, text, w, h)；按中心对齐排一行。"""
    if not items:
        return []
    total = sum(item[2] for item in items) + gap * (len(items) - 1)
    x = center_x - total / 2
    height = max(item[3] for item in items)
    boxes = []
    for box_id, text, width, _h in items:
        boxes.append(g.box(x, y, width, height, text, box_id, fs))
        x += width + gap
    return boxes


def build(canonical: dict) -> dict:
    """canonical v3 -> geometry（单一版式；layout 仅作来源标记）。"""
    g = _Geo()
    fs = float(canonical.get("font_size") or FS)
    fs_side = max(1.0, fs - 0.3)
    spine = 0.0
    y = 0.0
    last_bottom = 0.0

    # 1 screened
    screened = canonical.get("screened") or {}
    text = str(screened.get("text") or "").strip()
    screened_n = spec_mod.parse_n(screened.get("n"))
    if not text and screened_n is not None:
        text = f"{screened_n} patients screened"
    if text:
        wrapped = _wrap(text, 46)
        w, h = _box_wh(wrapped, fs, max_w=8.4)
        g.box(spine - w / 2, y, w, h, wrapped, "screened", fs)
        y = _bottom(g.boxes[-1])
        last_bottom = y

    # 2 inclusion / exclusion（右侧）
    criteria = canonical.get("criteria") or {}
    inc_txt = inclusion_block(criteria)
    exc_txt = exclusion_block(criteria)
    crit_boxes = []
    if inc_txt or exc_txt:
        x_side = spine + 0.55
        cursor = y + 0.14
        if inc_txt:
            w, h = _box_wh(inc_txt, fs_side, pad_x=0.28, max_w=4.8)
            box = g.box(x_side, cursor, w, h, inc_txt, "inclusion", fs_side, "left")
            g.arrow((box["x"], box["y"] + box["h"] / 2), (spine, box["y"] + box["h"] / 2))
            crit_boxes.append(box)
            cursor = _bottom(box) + 0.22
        if exc_txt:
            w, h = _box_wh(exc_txt, fs_side, pad_x=0.28, max_w=4.8)
            box = g.box(x_side, cursor, w, h, exc_txt, "exclusion", fs_side, "left")
            g.arrow((spine, box["y"] + box["h"] / 2), (box["x"], box["y"] + box["h"] / 2))
            crit_boxes.append(box)
            cursor = _bottom(box)
        stem_end = max(cursor, y + 0.70) + 0.15
        g.line((spine, y), (spine, stem_end))
        y = stem_end
    else:
        g.line((spine, y), (spine, y + STEM_MIN))
        y += STEM_MIN

    # 3 included（可选）
    included = canonical.get("included") or {}
    inc_text = str(included.get("text") or "").strip()
    inc_n = spec_mod.parse_n(included.get("n"))
    if inc_text or inc_n is not None:
        lines = []
        if inc_text:
            lines.extend(_wrap(inc_text, 40).split("\n"))
        else:
            lines.append("Patients included")
        if inc_n is not None:
            lines.append(f"n={inc_n}")
        pp = spec_mod.parse_n(included.get("pp"))
        non_pp = spec_mod.parse_n(included.get("non_pp"))
        if pp is not None and non_pp is not None:
            lines.append(f"PP n={pp} · non-PP n={non_pp}")
        body = "\n".join(lines)
        w, h = _box_wh(body, fs, max_w=6.0)
        box = g.box(spine - w / 2, y, w, h, body, "included", fs)
        y = _bottom(box)
        last_bottom = y

    # 4 presplit 臂
    arms = canonical.get("presplit") or []
    arm_boxes = []
    arm_bottoms: dict[str, float] = {}
    if arms:
        arm_y = y + 0.18
        fitted = []
        for arm in arms:
            body = node_text(arm)
            w, h = _text_box(body, fs, max_w=3.6)
            fitted.append((arm, body, w, h))
        total = sum(item[2] for item in fitted) + COL_GAP * (len(fitted) - 1)
        x = spine - total / 2
        height = max(item[3] for item in fitted)
        for arm, body, width, _h in fitted:
            box = g.box(x, arm_y, width, height, body, arm["id"], fs)
            g.arrow((spine, y), (_cx(box), arm_y))
            arm_boxes.append(box)
            arm_bottoms[arm["id"]] = _bottom(box)
            x += width + COL_GAP
        last_bottom = max(arm_bottoms.values())

    # 5 downsample
    downsample = canonical.get("downsample") or []
    if downsample and arms:
        for entry in downsample:
            arm_id = str(entry.get("arm"))
            arm_box = next((box for box in arm_boxes if box["id"] == arm_id), None)
            if arm_box is None:
                continue
            body = downsample_text(entry)
            w, h = _box_wh(body, fs, max_w=3.6)
            x = _cx(arm_box) - w / 2
            ds_y = arm_bottoms.get(arm_id, _bottom(arm_box)) + 0.26
            g.arrow((_cx(arm_box), arm_bottoms.get(arm_id, _bottom(arm_box))), (_cx(arm_box), ds_y))
            box = g.box(x, ds_y, w, h, body, f"downsample-{len([b for b in g.boxes if b['id'].startswith('downsample-')])}", fs)
            arm_bottoms[arm_id] = _bottom(box)
        last_bottom = max(arm_bottoms.values())

    # 6 cohorts
    cohorts = canonical.get("cohorts") or []
    cohort_boxes = []
    cohort_y = 0.0
    cohort_bottom = 0.0
    if cohorts:
        cohort_y = last_bottom + 0.30
        fitted = []
        for cohort in cohorts:
            body = node_text(cohort)
            w, h = _text_box(body, fs, max_w=3.2)
            fitted.append((cohort, body, w, h))
        total = sum(item[2] for item in fitted) + COHORT_GAP * (len(fitted) - 1)
        x = spine - total / 2
        height = max(item[3] for item in fitted)
        for cohort, body, width, _h in fitted:
            box = g.box(x, cohort_y, width, height, body, cohort["id"], fs)
            source_y = last_bottom
            if cohort["id"] in arm_bottoms:
                source_y = arm_bottoms[cohort["id"]]
            g.arrow((spine if not arm_boxes else _cx(box), source_y), (_cx(box), cohort_y))
            cohort_boxes.append(box)
            x += width + COHORT_GAP
        cohort_bottom = cohort_y + height
        last_bottom = cohort_bottom

    # 7 external（与验证组同排，左侧虚线）
    external = canonical.get("external") or []
    external_bottom = cohort_bottom
    if external:
        ext_top = cohort_y if cohorts else last_bottom + 0.30
        row_top = ext_top
        boxes = []
        y_ext = row_top
        ext_width = 0.0
        fitted_ext = []
        for entry in external:
            body = node_text(entry)
            w, h = _text_box(body, fs, max_w=3.2)
            fitted_ext.append((entry, body, w, h))
            ext_width = max(ext_width, w)
        left = min((box["x"] for box in cohort_boxes), default=spine - 1.0) - 0.8 - ext_width
        for index, (entry, body, _w, h) in enumerate(fitted_ext):
            box = g.box(left, y_ext, ext_width, h, body, entry["id"], fs)
            boxes.append(box)
            y_ext = _bottom(box) + 0.18
        bracket_x = left - 0.3
        top = boxes[0]["y"]
        bottom_ext = _bottom(boxes[-1])
        g.dashed((bracket_x, top), (bracket_x, bottom_ext))
        g.label(bracket_x, top - 0.3, "External validation", "external-label", fs, "left")
        for box in boxes:
            g.dashed((bracket_x, box["y"] + box["h"] / 2), (box["x"], box["y"] + box["h"] / 2))
        external_bottom = max(bottom_ext, external_bottom)
        last_bottom = max(last_bottom, external_bottom)

    # 8 pipeline steps
    pipeline = canonical.get("pipeline") or {}
    balance = pipeline.get("balance")
    steps = [step for step in (pipeline.get("steps") or []) if step.get("enabled")]
    train_box = cohort_boxes[0] if cohort_boxes else (arm_boxes[0] if arm_boxes else None)
    train_center = _cx(train_box) if train_box is not None else spine
    prev_bottom = _bottom(train_box) if train_box is not None else last_bottom
    chain_bottom = prev_bottom
    chain_center = train_center
    if balance in spec_mod.BALANCE_KEYS:
        balance_label = "Downsample" if balance == "downsample" else "SMOTE"
        balance_n = spec_mod.parse_n(pipeline.get("balance_n"))
        if balance_n is not None:
            balance_label += f"\nn={balance_n}"
        w, h = _text_box(balance_label, fs, max_w=3.4)
        box = g.box(train_center - w / 2, prev_bottom + 0.28, w, h, balance_label, "balance", fs)
        g.arrow((chain_center, prev_bottom), (_cx(box), box["y"]))
        prev_bottom = _bottom(box)
        chain_bottom = prev_bottom
        chain_center = _cx(box)
    for index, step in enumerate(steps):
        kind = str(step.get("kind") or "")
        label = str(step.get("label") or "").strip() or spec_mod.STEP_LABELS.get(kind, kind)
        step_y = prev_bottom + 0.28
        if kind in ("models", "explain"):
            items = [str(item) for item in (step.get("items") or [])]
            if not items:
                items = list(spec_mod.MODEL_KEYS) if kind == "models" else list(spec_mod.EXPLAIN_KEYS)
            prefix = "model" if kind == "models" else "explain"
            entries = []
            for item in items:
                text = (
                    {"clinical": "Clinical model", "radiomics": "Radiomics model",
                     "nomogram": "Nomogram", "dag": "DAG", "mediation": "Mediation"}.get(item, item)
                )
                w, h = _text_box(text, fs, max_w=3.0)
                entries.append((f"{prefix}-{item}", text, w, h))
            total = sum(item[2] for item in entries) + 0.20 * (len(entries) - 1)
            x = train_center - total / 2
            height = max(item[3] for item in entries)
            row_boxes = []
            for box_id, text, width, _h in entries:
                row_boxes.append(g.box(x, step_y, width, height, text, box_id, fs))
                x += width + 0.20
            bar_y = step_y - 0.16
            g.line((chain_center, prev_bottom), (chain_center, bar_y))
            g.line((_cx(row_boxes[0]), bar_y), (_cx(row_boxes[-1]), bar_y))
            for box in row_boxes:
                g.arrow((_cx(box), bar_y), (_cx(box), box["y"]))
            prev_bottom = step_y + height
            chain_bottom = prev_bottom
            chain_center = train_center
        else:
            w, h = _text_box(_wrap(label, 24), fs, max_w=3.4)
            box = g.box(train_center - w / 2, step_y, w, h, _wrap(label, 24), f"step-{index}", fs)
            g.arrow((chain_center, prev_bottom), (_cx(box), step_y))
            prev_bottom = _bottom(box)
            chain_bottom = prev_bottom
            chain_center = _cx(box)

    # 9 analyzed 汇合线标签（不画框）
    label_text = analyzed_label(canonical.get("analyzed") or {}) or DEFAULT_ANALYZED_LABEL
    if cohorts:
        merge_y = max(chain_bottom, cohort_bottom) + 0.5
        sources = []
        for box in cohort_boxes[1:]:
            sources.append((box, _bottom(box)))
        if steps or balance:
            sources.append((None, chain_bottom, chain_center))
        elif cohort_boxes:
            sources = [(box, _bottom(box)) for box in cohort_boxes]
        xs = []
        for item in sources:
            if len(item) == 3:
                x, bottom = item[2], item[1]
            else:
                box, bottom = item
                x = _cx(box)
            g.line((x, bottom), (x, merge_y))
            xs.append(x)
        if not xs:
            xs = [spine]
            g.line((spine, last_bottom), (spine, merge_y))
        g.line((min(xs), merge_y), (max(xs), merge_y))
        g.arrow((sum(xs) / len(xs), merge_y), (sum(xs) / len(xs), merge_y + 0.14))
        g.label(sum(xs) / len(xs), merge_y + 0.2, label_text, "analyzed", fs, "center")
    else:
        bottom = max(chain_bottom, last_bottom)
        g.arrow((chain_center, bottom), (chain_center, bottom + 0.18))
        g.label(chain_center, bottom + 0.24, label_text, "analyzed", fs, "center")

    return g.finish(str(canonical.get("layout") or ""))
