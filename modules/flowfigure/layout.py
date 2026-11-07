# -*- coding: utf-8 -*-
"""canonical spec v2 -> geometry（英寸坐标，box id 稳定，供预览/拖拽覆盖）。

本模块只做版式计算，不画图：``layout.build(canonical)`` 返回::

    {
      "layout": str,
      "page_in": [w, h],
      "page_cm": [w_cm, h_cm],
      "scale_x": float,
      "boxes": [{"id": str, "x": float, "y": float, "w": float, "h": float,
                 "lines": [str], "fs": float, "align": "center" | "left"}],
      "edges": [{"id": str, "kind": "arrow" | "line",
                 "a": [x, y], "b": [x, y]}],
      "warnings": [str]
    }

页宽规则：目标 16.0 cm；内容更窄时横向等比铺满（内容占宽 ≥85%），内容更宽时
按内容扩画布；超过 MAX_WIDTH_CM（17.2 cm）记 warning，仍出图。

box id：screened / inclusion / exclusion / included / arm-N / downsample-N /
cohort-N / analyzed / balance / lasso / model-* / metric-* / explain-*。
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
PUBLISHED_SPINE_X = 3.15
COL_GAP = 0.34
FORK_DROP = 0.46
STEM_MIN = 0.70
COHORT_GAP = 0.22
DEFAULT_ANALYZED_LABEL = "Analyzed"
PAGE_TARGET_IN = spec_mod.TARGET_WIDTH_CM / spec_mod.CM_PER_INCH
PAGE_MAX_CM = spec_mod.MAX_WIDTH_CM
PAGE_MARGIN_IN = spec_mod.MARGIN_IN

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


# -- text helpers -------------------------------------------------------------
def _wrap(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    return textwrap.fill(text, width=width, break_long_words=False)


def _line_w(line: str, fs: float) -> float:
    fp = FontProperties(family=FONT, size=fs)
    tp = TextPath((0, 0), line or " ", size=fs, prop=fp)
    return max(0.06, tp.get_extents().width / 72.0)


def _box_wh(text: str, fs: float = FS, pad_x: float = 0.36, pad_y: float = 0.12,
            min_w: float = 1.45, max_w: float = 6.2) -> tuple[float, float]:
    lines = (text or " ").split("\n")
    w = min(max_w, max(min_w, max(_line_w(line, fs) for line in lines) + pad_x))
    h = max(0.36, 2 * pad_y + (fs / 72.0) * 1.42 * len(lines))
    return w, h


def _fit(text: str, max_chars: int, fs: float = FS) -> tuple[str, float, float]:
    wrapped = _wrap(text, max_chars) if text else ""
    w, h = _box_wh(wrapped, fs)
    return wrapped, w, h


def _numbered(title: str, lines: list[str], width: int = 36) -> str:
    out = [title]
    for index, line in enumerate(lines, 1):
        out.append(_wrap(f"{index}. {line}", width))
    return "\n".join(out)


def _lines(text: str) -> list[str]:
    return str(text or "").split("\n")


def exclusion_lines(criteria: dict) -> list[str]:
    """canonical exclusion 条目 -> 图内文本；n 缺失不补。"""
    items = []
    for entry in criteria.get("exclusion") or []:
        reason = str(entry.get("reason") or "").strip()
        n = spec_mod.parse_n(entry.get("n"))
        if n is None:
            n = spec_mod.parse_n(reason)
        if n is not None and not _N_EQ.search(reason):
            reason = f"{reason} (n={n})" if reason else f"(n={n})"
        if reason:
            items.append(reason)
    return items


def cohort_display(cohorts: list[dict]) -> list[tuple[str, int | None]]:
    items = []
    for cohort in cohorts:
        label = str(cohort.get("label") or "Cohort").strip()
        if "development" in label.lower():
            label = "Training Cohort"
        n = spec_mod.parse_n(cohort.get("n"))
        display = label
        if n is not None and not _N_EQ.search(display):
            display = f"{display}\n(n={n})"
        items.append((display, n))
    return items


# -- geometry recorder --------------------------------------------------------
class _Geo:
    def __init__(self) -> None:
        self.boxes: list[dict] = []
        self.edges: list[dict] = []
        self.warnings: list[str] = []

    def box(self, x, y, w, h, text, box_id: str, fs: float = FS,
            align: str = "center") -> dict:
        item = {
            "id": box_id,
            "x": float(x), "y": float(y), "w": float(w), "h": float(h),
            "lines": _lines(text), "fs": float(fs), "align": align,
        }
        self.boxes.append(item)
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

    def _bounds(self) -> tuple[float, float, float, float]:
        if not self.boxes:
            raise ValueError("no boxes to draw")
        xs0 = min(box["x"] for box in self.boxes)
        ys0 = min(box["y"] for box in self.boxes)
        xs1 = max(box["x"] + box["w"] for box in self.boxes)
        ys1 = max(box["y"] + box["h"] for box in self.boxes)
        for edge in self.edges:
            xs0 = min(xs0, edge["a"][0], edge["b"][0])
            ys0 = min(ys0, edge["a"][1], edge["b"][1])
            xs1 = max(xs1, edge["a"][0], edge["b"][0])
            ys1 = max(ys1, edge["a"][1], edge["b"][1])
        return xs0, ys0, xs1, ys1

    def fit_page(self) -> tuple[float, float, float]:
        """定页宽并就地铺满；返回 (page_w_in, page_h_in, scale_x)。"""
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
            "edges": self.edges,
            "warnings": self.warnings,
        }


def _cx(box: dict) -> float:
    return box["x"] + box["w"] / 2


def _cy(box: dict) -> float:
    return box["y"] + box["h"] / 2


def _bottom(box: dict) -> float:
    return box["y"] + box["h"]


def _right(box: dict) -> float:
    return box["x"] + box["w"]


# -- expanded（A'） -----------------------------------------------------------
def _arm_text(arm: dict, pos_name: str) -> str:
    if arm.get("text"):
        return str(arm["text"]).strip()
    n = spec_mod.parse_n(arm.get("n"))
    lines = [f"{arm.get('label') or 'Cohort'} (n = {n})"]
    pp = spec_mod.parse_n(arm.get("pp"))
    non_pp = spec_mod.parse_n(arm.get("non_pp"))
    if pp is not None and non_pp is not None:
        lines.append(f"{pp} labeled {pos_name};")
        lines.append(f"{non_pp} without")
    note = str(arm.get("note") or "").strip()
    if note:
        lines.append(note)
    return "\n".join(lines)


def _cohort_text(cohort: dict, pos_name: str) -> str:
    if cohort.get("text"):
        return str(cohort["text"]).strip()
    n = spec_mod.parse_n(cohort.get("n"))
    lines = [f"{cohort.get('label') or 'Cohort'} (n = {n})"]
    pp = spec_mod.parse_n(cohort.get("pp"))
    non_pp = spec_mod.parse_n(cohort.get("non_pp"))
    if pp is not None and non_pp is not None:
        lines.append(f"{pp} labeled {pos_name};")
        lines.append(f"{non_pp} without")
    return "\n".join(lines)


def _downsample_text(down: dict) -> str:
    if down.get("text"):
        return str(down["text"]).strip()
    reason = str(down.get("reason") or "Downsampled for class imbalance").strip()
    return f"{reason}\n(n = {spec_mod.parse_n(down.get('n'))})"


def layout_expanded(canonical: dict) -> dict:
    """A' 版式：screened -> presplit 臂 ->（downsample）-> cohorts -> analyzed。"""
    g = _Geo()
    fs = float(canonical.get("font_size") or FS)
    fs_side = max(1.0, fs - 0.3)
    pos_name = str(canonical.get("pos_name") or spec_mod.DEFAULT_POS_NAME)
    arms = canonical.get("presplit") or []
    cohorts = canonical.get("cohorts") or []
    downs = {entry["arm"]: entry for entry in (canonical.get("downsample") or [])}

    arm_txt = [_wrap(_arm_text(arm, pos_name), TEXT_WIDTH) for arm in arms]
    coh_txt = [_wrap(_cohort_text(cohort, pos_name), TEXT_WIDTH) for cohort in cohorts]
    ds_raw = [downs.get(arm["id"]) for arm in arms]
    ds_txt = [
        _wrap(_downsample_text(entry), TEXT_WIDTH) if entry is not None else ""
        for entry in ds_raw
    ]
    arm_wh = [_box_wh(text, fs, max_w=3.6) for text in arm_txt]
    coh_wh = [_box_wh(text, fs, max_w=3.6) for text in coh_txt]
    ds_wh = [_box_wh(text, fs, max_w=3.6) if text else (0.0, 0.0) for text in ds_txt]

    col_w = [
        max(arm_wh[index][0], coh_wh[index][0], ds_wh[index][0])
        for index in range(len(arms))
    ]
    col_total = sum(col_w) + COL_GAP * (len(arms) - 1)
    col_left = [0.0]
    for width in col_w[:-1]:
        col_left.append(col_left[-1] + width + COL_GAP)

    inclusion = list((canonical.get("criteria") or {}).get("inclusion") or [])
    inc_txt = _numbered("Inclusion criteria:", inclusion, width=36) if inclusion else ""
    w_inc, h_inc = (
        _box_wh(inc_txt, fs_side, pad_x=0.28, max_w=4.8) if inc_txt else (0.0, 0.0)
    )

    spine_x = max(3.15, col_total / 2 + 0.40)
    offset_x = spine_x - col_total / 2

    screened_text = str((canonical.get("screened") or {}).get("text") or "").strip()
    if screened_text:
        screened_txt, w_scr, h_scr = _fit(screened_text, 46, fs)
    else:
        screened_txt, w_scr, h_scr = "", 0.0, 0.0
    analyzed_text = str((canonical.get("analyzed") or {}).get("text") or "").strip()
    analyzed_txt, w_ana, h_ana = _fit(analyzed_text or DEFAULT_ANALYZED_LABEL, 46, fs)

    y = 0.18
    if screened_txt:
        g.box(spine_x - w_scr / 2, y, w_scr, h_scr, screened_txt, "screened", fs)
        y += h_scr

    stem_end = y + max(STEM_MIN, (h_inc + 0.28) if inc_txt else STEM_MIN)
    g.line((spine_x, y), (spine_x, stem_end))
    if inc_txt:
        b_inc = g.box(
            spine_x + 0.55, y + 0.14, w_inc, h_inc, inc_txt, "inclusion", fs_side, "left",
        )
        join_y = _cy(b_inc)
        g.arrow((b_inc["x"], join_y), (spine_x, join_y))

    arm_y = stem_end + 0.16
    arm_h = max(height for _, height in arm_wh)
    for index, arm in enumerate(arms):
        x = offset_x + col_left[index]
        cx = x + col_w[index] / 2
        g.box(x, arm_y, col_w[index], arm_h, arm_txt[index], arm["id"], fs)
        g.arrow((spine_x, stem_end), (cx, arm_y))

    cursor = arm_y + arm_h
    ds_bottom = [cursor] * len(arms)
    for index, arm in enumerate(arms):
        x = offset_x + col_left[index]
        cx = x + col_w[index] / 2
        if ds_txt[index]:
            ds_y = cursor + 0.26
            g.arrow((cx, cursor), (cx, ds_y))
            ds_box = g.box(x, ds_y, col_w[index], ds_wh[index][1], ds_txt[index],
                           f"downsample-{index}", fs)
            ds_bottom[index] = _bottom(ds_box)
    cursor = max(ds_bottom)

    coh_y = cursor + 0.30
    coh_h = max(height for _, height in coh_wh)
    for index, cohort in enumerate(cohorts):
        x = offset_x + col_left[index]
        cx = x + col_w[index] / 2
        g.box(x, coh_y, col_w[index], coh_h, coh_txt[index], cohort["id"], fs)
        g.arrow((cx, ds_bottom[index]), (cx, coh_y))

    fork_y = coh_y + coh_h + FORK_DROP
    cx_list = [offset_x + col_left[index] + col_w[index] / 2 for index in range(len(cohorts))]
    for cx in cx_list:
        g.line((cx, coh_y + coh_h), (cx, fork_y))
    if len(cx_list) > 1:
        g.line((min(cx_list), fork_y), (max(cx_list), fork_y))
    ana_y = fork_y + 0.28
    g.arrow((spine_x, fork_y), (spine_x, ana_y))
    g.box(spine_x - w_ana / 2, ana_y, w_ana, h_ana, analyzed_txt, "analyzed", fs)
    return g.finish(spec_mod.LAYOUT_EXPANDED)


# -- published-a（A） ---------------------------------------------------------
def layout_published(canonical: dict) -> dict:
    """A 版式：screened -> 右侧纳入/排除 -> analyzed -> 底部 cohorts。"""
    g = _Geo()
    fs = float(canonical.get("font_size") or FS)
    screened = str((canonical.get("screened") or {}).get("text") or "").strip()
    inclusion = list((canonical.get("criteria") or {}).get("inclusion") or [])
    exclusion = exclusion_lines(canonical.get("criteria") or {})
    analyzed = str((canonical.get("analyzed") or {}).get("text") or "").strip()
    cohorts = canonical.get("cohorts") or []
    split_items = cohort_display(cohorts)
    if not analyzed and not screened:
        raise ValueError("spec needs at least screened or analyzed text")

    scr_txt, w_scr, h_scr = _fit(screened, 46, fs) if screened else ("", 0.0, 0.0)
    ana_txt, w_ana, h_ana = _fit(analyzed, 46, fs) if analyzed else ("", 0.0, 0.0)
    inc_txt = _numbered("Inclusion criteria:", inclusion, width=36) if inclusion else ""
    exc_txt = _numbered("Exclusion criteria:", exclusion, width=36) if exclusion else ""
    w_inc, h_inc = _box_wh(inc_txt, FS_SIDE, pad_x=0.28, max_w=4.8) if inc_txt else (0.0, 0.0)
    w_exc, h_exc = _box_wh(exc_txt, FS_SIDE, pad_x=0.28, max_w=4.8) if exc_txt else (0.0, 0.0)
    split_fitted = [_fit(text, 28, fs) for text, _ in split_items]

    spine_x = PUBLISHED_SPINE_X
    x_side = spine_x + 0.55
    y = 0.18
    last = None

    def place_center(text, w, h, y0, box_id):
        return g.box(spine_x - w / 2, y0, w, h, text, box_id)

    if scr_txt:
        last = place_center(scr_txt, w_scr, h_scr, y, "screened")
        y = y + h_scr

    stem_need = 0.0
    if inc_txt:
        stem_need += h_inc
    if exc_txt:
        stem_need += h_exc
    if inc_txt and exc_txt:
        stem_need += 0.22
    if inc_txt or exc_txt:
        stem_need = max(0.70, stem_need + 0.28)

    if ana_txt:
        if last and (inc_txt or exc_txt):
            ana_y = y + stem_need
            g.line((spine_x, y), (spine_x, ana_y))
            cursor = y + 0.14
            if inc_txt:
                b_inc = g.box(x_side, cursor, w_inc, h_inc, inc_txt, "inclusion",
                              FS_SIDE, "left")
                join_y = _cy(b_inc)
                g.arrow((b_inc["x"], join_y), (spine_x, join_y))
                cursor = _bottom(b_inc) + 0.22
            if exc_txt:
                b_exc = g.box(x_side, cursor, w_exc, h_exc, exc_txt, "exclusion",
                              FS_SIDE, "left")
                join_y = _cy(b_exc)
                g.arrow((spine_x, join_y), (b_exc["x"], join_y))
        elif last:
            ana_y = y + 0.48
            g.arrow((spine_x, y), (spine_x, ana_y))
        else:
            ana_y = y
            if inc_txt or exc_txt:
                cursor = ana_y
                if inc_txt:
                    b_inc = g.box(x_side, cursor, w_inc, h_inc, inc_txt, "inclusion",
                                  FS_SIDE, "left")
                    join_y = _cy(b_inc)
                    g.arrow(
                        (b_inc["x"], join_y),
                        (spine_x + w_ana / 2, ana_y + h_ana / 2),
                    )
                    cursor = _bottom(b_inc) + 0.22
                if exc_txt:
                    b_exc = g.box(x_side, cursor, w_exc, h_exc, exc_txt, "exclusion",
                                  FS_SIDE, "left")
                    g.arrow(
                        (spine_x + w_ana / 2, ana_y + h_ana / 2),
                        (b_exc["x"], _cy(b_exc)),
                    )
        last = place_center(ana_txt, w_ana, h_ana, ana_y, "analyzed")
        y = ana_y + h_ana
    elif last and (inc_txt or exc_txt):
        cursor = y + 0.16
        if inc_txt:
            b_inc = g.box(x_side, cursor, w_inc, h_inc, inc_txt, "inclusion",
                          FS_SIDE, "left")
            g.arrow((b_inc["x"], _cy(b_inc)), (_right(last), _cy(last)))
            cursor = _bottom(b_inc) + 0.22
        if exc_txt:
            b_exc = g.box(x_side, cursor, w_exc, h_exc, exc_txt, "exclusion",
                          FS_SIDE, "left")
            g.arrow((_right(last), _cy(last)), (b_exc["x"], _cy(b_exc)))
            y = max(y, _bottom(b_exc))

    if split_fitted and last:
        widths = [w for _, w, _ in split_fitted]
        heights = [h for _, _, h in split_fitted]
        h = max(heights)
        total = sum(widths) + COHORT_GAP * (len(widths) - 1)
        x0 = spine_x - total / 2
        fork_top = y + 0.46
        x_cursor = x0
        for index, (text, w, _h0) in enumerate(split_fitted):
            g.box(x_cursor, fork_top, w, h, text, f"cohort-{index}")
            g.arrow((spine_x, _bottom(last)), (x_cursor + w / 2, fork_top))
            x_cursor += w + COHORT_GAP
    return g.finish(spec_mod.LAYOUT_PUBLISHED)


# -- study（含 Methods 行） ---------------------------------------------------
MODEL_LABELS = {
    "clinical": "Clinical model",
    "radiomics": "Radiomics model",
    "plaque": "Plaque score",
    "nomogram": "Nomogram",
}
METRIC_LABELS = {
    "roc": "ROC AUC",
    "calibration": "Calibration",
    "dca": "DCA",
    "nri": "NRI",
    "idi": "IDI",
}
EXPLAIN_LABELS = {"dag": "DAG", "mediation": "Mediation"}


def layout_study(canonical: dict) -> dict:
    """study 版式：筛查 -> 纳入/排除 -> 纳入 -> 队列 -> 处理/模型/评价行。"""
    g = _Geo()
    fs = float(canonical.get("font_size") or FS)
    screened = str((canonical.get("screened") or {}).get("text") or "").strip()
    inclusion = list((canonical.get("criteria") or {}).get("inclusion") or [])
    exclusion = exclusion_lines(canonical.get("criteria") or {})
    analyzed = str((canonical.get("analyzed") or {}).get("text") or "").strip()
    cohorts = canonical.get("cohorts") or []
    pipeline = canonical.get("pipeline") or {}

    if not screened and not analyzed and not cohorts:
        raise ValueError("study 版式需要 screened / analyzed / cohorts 至少一项")

    scr_txt, w_scr, h_scr = _fit(screened, 40, fs) if screened else ("", 0.0, 0.0)
    inc_txt = _numbered("Inclusion criteria:", inclusion, width=32) if inclusion else ""
    exc_txt = _numbered("Exclusion criteria:", exclusion, width=32) if exclusion else ""
    w_inc, h_inc = _box_wh(inc_txt, fs, pad_x=0.28, max_w=4.6) if inc_txt else (0.0, 0.0)
    w_exc, h_exc = _box_wh(exc_txt, fs, pad_x=0.28, max_w=4.6) if exc_txt else (0.0, 0.0)
    ana_txt, w_ana, h_ana = _fit(analyzed or "Patients included", 34, fs)

    cohort_fitted = []
    for cohort in cohorts:
        text = _wrap(
            f"{cohort.get('label') or 'Cohort'}\n(n = {spec_mod.parse_n(cohort.get('n'))})",
            30,
        )
        w, h = _box_wh(text, fs, max_w=3.2)
        cohort_fitted.append((cohort["id"], text, w, h))

    y = 0.2
    top = g.box(0, y, w_scr, h_scr, scr_txt or "Patients screened", "screened", fs)
    y = _bottom(top) + 0.32

    crit_h = max(h_inc, h_exc, 0.4)
    if inc_txt or exc_txt:
        x = 0.0
        if inc_txt:
            b_inc = g.box(x, y, w_inc, h_inc, inc_txt, "inclusion", fs, "left")
            g.arrow((_cx(top), _bottom(top)), (_cx(b_inc), b_inc["y"]))
            x += w_inc + 0.20
        if exc_txt:
            b_exc = g.box(x, y, w_exc, h_exc, exc_txt, "exclusion", fs, "left")
            g.arrow((_cx(top), _bottom(top)), (_cx(b_exc), b_exc["y"]))
        y += crit_h + 0.32

    included = g.box(0, y, w_ana, h_ana, ana_txt, "included", fs)
    if inc_txt or exc_txt:
        for box_id in ("inclusion", "exclusion"):
            source = next((box for box in g.boxes if box["id"] == box_id), None)
            if source is not None:
                g.arrow((_cx(source), _bottom(source)), (_cx(included), included["y"]))
    else:
        g.arrow((_cx(top), _bottom(top)), (_cx(included), included["y"]))
    y = _bottom(included) + 0.32

    cohort_boxes = []
    if cohort_fitted:
        gap = 0.20
        total = sum(w for _, _, w, _ in cohort_fitted) + gap * (len(cohort_fitted) - 1)
        x = -total / 2
        h_max = max(h for _, _, _, h in cohort_fitted)
        for box_id, text, w, _h in cohort_fitted:
            box = g.box(x, y, w, h_max, text, box_id, fs)
            cohort_boxes.append(box)
            g.arrow((_cx(included), _bottom(included)), (_cx(box), box["y"]))
            x += w + gap
        y += h_max + 0.32

    train = cohort_boxes[0] if cohort_boxes else included
    stem_center = _cx(train)

    def stem_box(label: str, box_id: str) -> dict:
        nonlocal y
        text = _wrap(label, 24)
        w, h = _box_wh(text, fs, max_w=3.2)
        box = g.box(stem_center - w / 2, y, w, h, text, box_id, fs)
        y = _bottom(box) + 0.20
        return box

    balance = pipeline.get("balance")
    if balance in spec_mod.BALANCE_KEYS:
        label = "Downsample" if balance == "downsample" else "SMOTE"
        stem = stem_box(label, "balance")
        g.arrow((stem_center, _bottom(train)), (stem_center, stem["y"]))
        train = stem
    if pipeline.get("lasso"):
        stem = stem_box("LASSO", "lasso")
        g.arrow((stem_center, _bottom(train)), (stem_center, stem["y"]))
        train = stem

    def row_boxes(keys: list[str], labels: dict, prefix: str,
                  y0: float) -> tuple[list[dict], float]:
        if not keys:
            return [], 0.0
        gap = 0.20
        fitted = []
        for key in keys:
            text = _wrap(labels.get(key, key), 18)
            fitted.append((f"{prefix}-{key}", text, *_box_wh(text, fs, max_w=3.0)))
        total = sum(w for _, _, w, _ in fitted) + gap * (len(fitted) - 1)
        x = stem_center - total / 2
        h = max(h for _, _, _, h in fitted)
        boxes = []
        for box_id, text, w, _h in fitted:
            boxes.append(g.box(x, y0, w, h, text, box_id, fs))
            x += w + gap
        return boxes, h

    models, model_h = row_boxes(list(pipeline.get("models") or []), MODEL_LABELS, "model", y)
    if models:
        bar_y = y - 0.14
        g.line((stem_center, _bottom(train)), (stem_center, bar_y))
        g.line((_cx(models[0]), bar_y), (_cx(models[-1]), bar_y))
        for box in models:
            g.arrow((_cx(box), bar_y), (_cx(box), box["y"]))
        y += model_h + 0.24

    metrics, metric_h = row_boxes(list(pipeline.get("metrics") or []), METRIC_LABELS, "metric", y)
    if metrics:
        sources = list(models)
        sources.extend(cohort_boxes[1:])
        bus_y = y - 0.20
        xs = [_cx(box) for box in sources]
        if not sources:
            xs = [stem_center]
        g.line((min(xs), bus_y), (max(xs), bus_y))
        if not models:
            g.line((stem_center, _bottom(train)), (stem_center, bus_y))
        for box in sources:
            g.line((_cx(box), _bottom(box)), (_cx(box), bus_y))
        for box in metrics:
            g.arrow((_cx(box), bus_y), (_cx(box), box["y"]))
        y += metric_h + 0.24

        explain = list(pipeline.get("explain") or [])
        if explain:
            src = metrics[len(metrics) // 2]
            gap = 0.20
            fitted = []
            for key in explain:
                text = _wrap(EXPLAIN_LABELS.get(key, key), 18)
                fitted.append((f"explain-{key}", text, *_box_wh(text, fs, max_w=3.0)))
            total = sum(w for _, _, w, _ in fitted) + gap * (len(fitted) - 1)
            x = _cx(src) - total / 2
            h = max(h for _, _, _, h in fitted)
            for box_id, text, w, _h in fitted:
                box = g.box(x, y, w, h, text, box_id, fs)
                g.arrow((_cx(src), _bottom(src)), (_cx(box), box["y"]))
                x += w + gap
    return g.finish(spec_mod.LAYOUT_STUDY)


def build(canonical: dict) -> dict:
    """canonical spec -> geometry；layout 分派，未知 layout 显式报错。"""
    layout = canonical.get("layout")
    if layout == spec_mod.LAYOUT_EXPANDED:
        geometry = layout_expanded(canonical)
    elif layout == spec_mod.LAYOUT_PUBLISHED:
        geometry = layout_published(canonical)
    elif layout == spec_mod.LAYOUT_STUDY:
        geometry = layout_study(canonical)
    else:
        raise ValueError(f"未知 spec.layout={layout!r}")
    return geometry
