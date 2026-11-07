# -*- coding: utf-8 -*-
"""canonical spec v3 -> geometry（单一版式；英寸坐标，box id 稳定）。

顺序：screened -> inclusion/exclusion（右侧）-> included（可选）-> presplit 臂 ->
downsample -> cohorts（与臂同数同 n 且无降采样的臂不重复画队列）-> external（虚线
挂到方法行首格/训练队列）-> pipeline steps（横排，超宽折行）-> 结束（不再画 analyzed）。

geometry::

    {
      "layout": <来源标记>, "page_in": [w, h], "page_cm": [w_cm, h_cm],
      "scale_x": float,
      "boxes": [{"id", "x", "y", "w", "h", "lines", "fs", "align", "manual"}],
      "labels": [{"id", "text", "x", "y", "fs", "align"}],
      "edges": [{"id", "kind": "arrow" | "line" | "dashed", "a": [x, y], "b": [x, y]}],
      "warnings": [str]
    }

``overrides.text[boxId]`` 优先作为框内文字（按框宽重排，box.manual=True）。
页宽：目标 16 cm；内容更窄时横向铺满，内容更宽时按内容扩；超过 17.2 cm 仅 warning。
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
ROW_GAP = 0.20
STEP_GAP = 0.22
STEM_MIN = 0.70
PAGE_TARGET_IN = spec_mod.TARGET_WIDTH_CM / spec_mod.CM_PER_INCH
PAGE_MAX_CM = spec_mod.MAX_WIDTH_CM
PAGE_MARGIN_IN = spec_mod.MARGIN_IN
CHAIN_MAX_IN = PAGE_TARGET_IN - 2 * PAGE_MARGIN_IN

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


def _wrap(text: str, width: int = TEXT_WIDTH) -> str:
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


def _wrap_block(text: str, width: int = TEXT_WIDTH) -> str:
    """保留显式换行，逐行 wrap（用于含 n= 多行的节点文字）。"""
    out: list[str] = []
    for raw in str(text or "").split("\n"):
        line = " ".join(raw.split())
        if not line:
            out.append("")
            continue
        out.extend(textwrap.fill(line, width=width, break_long_words=False).split("\n"))
    return "\n".join(out)


def _override_text(canonical: dict, box_id: str) -> str | None:
    overrides = canonical.get("overrides") if isinstance(canonical.get("overrides"), dict) else {}
    text_map = overrides.get("text") if isinstance(overrides.get("text"), dict) else {}
    if box_id not in text_map:
        return None
    value = text_map[box_id]
    return None if value is None else str(value)


def node_text(node: dict) -> str:
    """臂/队列/外验统一记法：label / n=N / PP n=… · non-PP n=…。"""
    lines = []
    label = str(node.get("text") or node.get("label") or "").strip()
    if label:
        lines.extend(_wrap(label).split("\n"))
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
        lines.extend(_wrap(reason).split("\n"))
    n = spec_mod.parse_n(entry.get("n"))
    if n is not None:
        lines.append(f"n={n}")
    if not lines:
        lines = [f"n={n}" if n is not None else "Downsample"]
    return "\n".join(lines)


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
            align: str = "center", manual: bool = False) -> dict:
        item = {
            "id": box_id, "x": float(x), "y": float(y),
            "w": float(w), "h": float(h), "lines": _lines(text),
            "fs": float(fs), "align": align, "manual": bool(manual),
        }
        self.boxes.append(item)
        return item

    def label(self, x, y, text, label_id: str, fs: float = FS,
              align: str = "left") -> dict:
        item = {
            "id": label_id, "text": str(text), "x": float(x), "y": float(y),
            "fs": float(fs), "align": align,
        }
        self.labels.append(item)
        return item

    def arrow(self, a, b) -> None:
        self.edges.append({"id": f"e-{len(self.edges)}", "kind": "arrow",
                           "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])]})

    def line(self, a, b) -> None:
        self.edges.append({"id": f"e-{len(self.edges)}", "kind": "line",
                           "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])]})

    def dashed(self, a, b) -> None:
        self.edges.append({"id": f"e-{len(self.edges)}", "kind": "dashed",
                           "a": [float(a[0]), float(a[1])], "b": [float(b[0]), float(b[1])]})

    def _bounds(self) -> tuple[float, float, float, float]:
        if not self.boxes:
            raise ValueError("no boxes to draw")
        xs0 = min(box["x"] for box in self.boxes)
        ys0 = min(box["y"] for box in self.boxes)
        xs1 = max(box["x"] + box["w"] for box in self.boxes)
        ys1 = max(box["y"] + box["h"] for box in self.boxes)
        for label in self.labels:
            width = max((_line_w(line, label["fs"]) for line in str(label["text"]).split("\n")),
                        default=0.0)
            height = (label["fs"] / 72.0) * 1.4 * len(str(label["text"]).split("\n"))
            xs0 = min(xs0, label["x"])
            xs1 = max(xs1, label["x"] + width)
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


def _right(box: dict) -> float:
    return box["x"] + box["w"]


def _bottom(box: dict) -> float:
    return box["y"] + box["h"]


def _box_text(canonical: dict, box_id: str, default: str, fs: float,
              max_w: float, width: int = TEXT_WIDTH) -> tuple[str, float, float, bool]:
    """取 override.text 或默认文本，按框宽重排；返回 (text, w, h, manual)。"""
    override = _override_text(canonical, box_id)
    manual = override is not None
    text = override if manual else default
    wrapped = _wrap_block(text, width)
    w, h = _box_wh(wrapped, fs, max_w=max_w)
    return wrapped, w, h, manual


def _same_count(arm: dict, cohort: dict) -> bool:
    """无降采样臂与队列同 n（pp/non_pp 同时一致）→ 队列可省略。"""
    arm_n = spec_mod.parse_n(arm.get("n"))
    cohort_n = spec_mod.parse_n(cohort.get("n"))
    if arm_n is None or cohort_n is None or arm_n != cohort_n:
        return False
    for key in ("pp", "non_pp"):
        left = spec_mod.parse_n(arm.get(key))
        right = spec_mod.parse_n(cohort.get(key))
        if left is not None and right is not None and left != right:
            return False
    return True


def build(canonical: dict) -> dict:
    """canonical v3 -> geometry（单一版式；layout 仅来源标记）。"""
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
    screened_box = None
    if text:
        body, w, h, manual = _box_text(canonical, "screened", text, fs, 8.4, 46)
        screened_box = g.box(spine - w / 2, y, w, h, body, "screened", fs, "center", manual)
        y = _bottom(screened_box)
        last_bottom = y

    # 2 criteria 右侧
    criteria = canonical.get("criteria") or {}
    inc_txt = inclusion_block(criteria)
    exc_txt = exclusion_block(criteria)
    if inc_txt or exc_txt:
        x_side = spine + 0.55
        cursor = y + 0.14
        if inc_txt:
            body, w, h, manual = _box_text(canonical, "inclusion", inc_txt, fs_side, 4.8, 36)
            box = g.box(x_side, cursor, w, h, body, "inclusion", fs_side, "left", manual)
            g.arrow((box["x"], box["y"] + box["h"] / 2), (spine, box["y"] + box["h"] / 2))
            cursor = _bottom(box) + 0.22
        if exc_txt:
            body, w, h, manual = _box_text(canonical, "exclusion", exc_txt, fs_side, 4.8, 36)
            box = g.box(x_side, cursor, w, h, body, "exclusion", fs_side, "left", manual)
            g.arrow((spine, box["y"] + box["h"] / 2), (box["x"], box["y"] + box["h"] / 2))
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
    included_box = None
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
        body, w, h, manual = _box_text(canonical, "included", body, fs, 6.0, 40)
        included_box = g.box(spine - w / 2, y, w, h, body, "included", fs, "center", manual)
        y = _bottom(included_box)
        last_bottom = y

    # 4 presplit 臂
    arms = canonical.get("presplit") or []
    downsample = canonical.get("downsample") or []
    ds_by_arm = {str(entry.get("arm")): entry for entry in downsample}
    arm_boxes: dict[str, dict] = {}
    arm_bottoms: dict[str, float] = {}
    if arms:
        arm_y = y + 0.18
        fitted = []
        for arm in arms:
            default = node_text(arm)
            body, w, h, manual = _box_text(canonical, arm["id"], default, fs, 3.6)
            fitted.append((arm, body, w, h, manual))
        total = sum(item[2] for item in fitted) + COL_GAP * (len(fitted) - 1)
        x = spine - total / 2
        height = max(item[3] for item in fitted)
        for arm, body, width, _h, manual in fitted:
            box = g.box(x, arm_y, width, height, body, arm["id"], fs, "center", manual)
            g.arrow((spine, y), (_cx(box), arm_y))
            arm_boxes[arm["id"]] = box
            arm_bottoms[arm["id"]] = _bottom(box)
            x += width + COL_GAP
        last_bottom = max(arm_bottoms.values())

    # 5 downsample
    ds_boxes: dict[str, dict] = {}
    for arm in arms:
        entry = ds_by_arm.get(arm["id"])
        if entry is None:
            continue
        default = downsample_text(entry)
        box_id = f"downsample-{list(arms).index(arm)}"
        body, w, h, manual = _box_text(canonical, box_id, default, fs, 3.6)
        arm_box = arm_boxes[arm["id"]]
        ds_y = arm_bottoms[arm["id"]] + 0.26
        g.arrow((_cx(arm_box), arm_bottoms[arm["id"]]), (_cx(arm_box), ds_y))
        box = g.box(_cx(arm_box) - w / 2, ds_y, w, h, body, box_id, fs, "center", manual)
        ds_boxes[arm["id"]] = box
        arm_bottoms[arm["id"]] = _bottom(box)
    if ds_boxes:
        last_bottom = max(arm_bottoms.values())

    # 6 cohorts（无降采样且同 n 的臂不再重复画队列）
    cohorts = canonical.get("cohorts") or []
    drawn_cohorts: list[tuple[dict, dict | None]] = []
    if cohorts:
        paired = len(cohorts) == len(arms)
        for index, cohort in enumerate(cohorts):
            arm = arms[index] if paired else None
            if arm is not None and arm["id"] not in ds_by_arm and _same_count(arm, cohort):
                continue
            drawn_cohorts.append((cohort, arm))
        if drawn_cohorts:
            cohort_y = last_bottom + 0.30
            fitted = []
            for cohort, _arm in drawn_cohorts:
                default = node_text(cohort)
                body, w, h, manual = _box_text(canonical, cohort["id"], default, fs, 3.2)
                fitted.append((cohort, body, w, h, manual))
            total = sum(item[2] for item in fitted) + COHORT_GAP * (len(fitted) - 1)
            x = spine - total / 2
            height = max(item[3] for item in fitted)
            for cohort, body, width, _h, manual in fitted:
                box = g.box(x, cohort_y, width, height, body, cohort["id"], fs, "center", manual)
                arm = next((item_arm for item_cohort, item_arm in drawn_cohorts if item_cohort is cohort), None)
                if arm is not None and arm["id"] in arm_bottoms:
                    source = arm_bottoms[arm["id"]]
                    source_x = _cx(arm_boxes[arm["id"]]) if arm["id"] in arm_boxes else spine
                else:
                    source = last_bottom
                    source_x = spine
                g.arrow((source_x, source), (_cx(box), cohort_y))
                x += width + COHORT_GAP
            last_bottom = cohort_y + height

    # 7 external（左侧，虚线挂到方法行首格/训练队列）
    external = canonical.get("external") or []
    external_bracket: dict | None = None
    if external:
        fitted = []
        for entry in external:
            default = node_text(entry)
            body, w, h, manual = _box_text(canonical, entry["id"], default, fs, 3.2)
            fitted.append((entry, body, w, h, manual))
        ext_width = max(item[2] for item in fitted)
        left = min((box["x"] for box in g.boxes), default=spine) - 0.8 - ext_width
        top = (last_bottom + 0.30) if not cohorts else (last_bottom + 0.30)
        # 与队列同排：用最后一个内容 bottom 上方 0.30 已等于队列下一行；改为对齐最后一行顶部
        if cohorts and drawn_cohorts:
            last_cohort = next(
                (box for box in g.boxes if box["id"] == drawn_cohorts[-1][0]["id"]), None
            )
            if last_cohort is not None:
                top = last_cohort["y"]
        y_ext = top
        ext_boxes = []
        for entry, body, _w, h, manual in fitted:
            box = g.box(left, y_ext, ext_width, h, body, entry["id"], fs, "center", manual)
            ext_boxes.append(box)
            y_ext = _bottom(box) + 0.18
        bracket_x = left - 0.3
        top_y = ext_boxes[0]["y"]
        bottom_y = _bottom(ext_boxes[-1])
        g.dashed((bracket_x, top_y), (bracket_x, bottom_y))
        g.label(bracket_x, top_y - 0.3, "External validation", "external-label", fs, "left")
        for box in ext_boxes:
            g.dashed((bracket_x, box["y"] + box["h"] / 2), (box["x"], box["y"] + box["h"] / 2))
        external_bracket = {"x": bracket_x, "y": (top_y + bottom_y) / 2}
        last_bottom = max(last_bottom, bottom_y)

    # 8 pipeline 横排（balance 排首；超宽折行，箭头连续）
    pipeline = canonical.get("pipeline") or {}
    balance = pipeline.get("balance")
    nodes: list[dict] = []
    anchor_box = None
    if cohorts and drawn_cohorts:
        for cohort, arm in drawn_cohorts:
            if arm is not None and arm["id"] in ds_by_arm:
                anchor_box = next((box for box in g.boxes if box["id"] == cohort["id"]), None)
                break
        if anchor_box is None:
            anchor_box = next((box for box in g.boxes if box["id"] == drawn_cohorts[0][0]["id"]), None)
    if anchor_box is None:
        anchor_box = included_box or screened_box
    anchor_center = _cx(anchor_box) if anchor_box is not None else spine
    prev_bottom = _bottom(anchor_box) if anchor_box is not None else last_bottom

    def add_node(box_id: str, default: str, fs_node: float = FS, max_w: float = 3.2) -> None:
        body, w, h, manual = _box_text(canonical, box_id, default, fs_node, max_w)
        nodes.append({"id": box_id, "text": body, "w": w, "h": h,
                      "fs": fs_node, "manual": manual})

    if balance in spec_mod.BALANCE_KEYS:
        label = "Downsample" if balance == "downsample" else "SMOTE"
        n_balance = spec_mod.parse_n(pipeline.get("balance_n"))
        add_node("balance", f"{label}\nn={n_balance}" if n_balance is not None else label)
    step_index = 0
    for step in pipeline.get("steps") or []:
        if not step.get("enabled"):
            continue
        kind = str(step.get("kind") or "")
        label = str(step.get("label") or "").strip() or spec_mod.STEP_LABELS.get(kind, kind)
        if kind in ("models", "explain"):
            items = [str(item) for item in (step.get("items") or [])]
            if not items:
                items = list(spec_mod.MODEL_KEYS) if kind == "models" else list(spec_mod.EXPLAIN_KEYS)
            prefix = "model" if kind == "models" else "explain"
            for item in items:
                text = {
                    "clinical": "Clinical model", "radiomics": "Radiomics model",
                    "nomogram": "Nomogram", "dag": "DAG", "mediation": "Mediation",
                }.get(item, item)
                add_node(f"{prefix}-{item}", text, FS, 3.0)
        else:
            add_node(f"step-{step_index}", _wrap(label, 24), FS, 3.4)
        step_index += 1

    first_node_box = None
    if nodes:
        rows: list[list[dict]] = []
        current: list[dict] = []
        width = 0.0
        for node in nodes:
            extra = node["w"] + (STEP_GAP if current else 0.0)
            if current and width + extra > CHAIN_MAX_IN:
                rows.append(current)
                current = []
                width = 0.0
                extra = node["w"]
            current.append(node)
            width += extra
        if current:
            rows.append(current)
        row_y = prev_bottom + 0.35 if prev_bottom else last_bottom + 0.35
        for row in rows:
            total = sum(node["w"] for node in row) + STEP_GAP * (len(row) - 1)
            x = anchor_center - total / 2
            height = max(node["h"] for node in row)
            for node in row:
                node["box"] = g.box(x, row_y, node["w"], height, node["text"],
                                    node["id"], node["fs"], "center", node["manual"])
                if first_node_box is None:
                    first_node_box = node["box"]
                x += node["w"] + STEP_GAP
            row_y += height + ROW_GAP
        # 行内箭头
        for row in rows:
            for left_node, right_node in zip(row, row[1:]):
                g.arrow(
                    (_right(left_node["box"]), left_node["box"]["y"] + left_node["box"]["h"] / 2),
                    (right_node["box"]["x"], right_node["box"]["y"] + right_node["box"]["h"] / 2),
                )
        # 折行连接
        for upper, lower in zip(rows, rows[1:]):
            last = upper[-1]["box"]
            first = lower[0]["box"]
            mid_y = last["y"] + last["h"] / 2
            turn_y = first["y"] + first["h"] / 2
            bend_x = _right(last) + 0.18
            g.line((_right(last), mid_y), (bend_x, mid_y))
            g.line((bend_x, mid_y), (bend_x, turn_y))
            g.arrow((bend_x, turn_y), (first["x"], turn_y))
        # anchor -> 首节点
        first = nodes[0]["box"]
        if anchor_box is not None:
            start = (_cx(anchor_box), _bottom(anchor_box))
            elbow_y = first["y"] - 0.16
            g.line(start, (start[0], elbow_y))
            g.line((start[0], elbow_y), (_cx(first), elbow_y))
            g.arrow((_cx(first), elbow_y), (_cx(first), first["y"]))
        last_bottom = max(_bottom(node["box"]) for node in nodes)
    else:
        first_node_box = None

    # 9 外验虚线终点：方法行首格 -> 训练队列 -> 起始节点（不再指 analyzed）
    if external_bracket is not None:
        target = first_node_box or anchor_box
        if target is not None:
            target_y = target["y"] + target["h"] / 2
            g.dashed(
                (external_bracket["x"], external_bracket["y"]),
                (target["x"], target_y),
            )

    return g.finish(str(canonical.get("layout") or ""))
