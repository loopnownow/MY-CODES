# -*- coding: utf-8 -*-
"""Figure 1 study flowchart.

Enrolment, cohorts, training-only processing, and the evaluations named in
Materials and Methods. Text and counts come from that section. Do not invent n.

    python figure_strobe_flow.py --docx manuscript.docx --out Figure1_flow.pdf

White ground, black square boxes, Times New Roman 12 pt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.textpath import TextPath

EDGE = "#000000"
FACE = "#FFFFFF"
LW = 1.05
FS = 8.3
FS_SMALL = 8.0

_N_EQ = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
_LEADING_COUNT = re.compile(
    r"^\s*(\d+)\s+(?:patients?|subjects?|cases?|women|men)\b", re.I
)


class AuditError(ValueError):
    """n-audit failed; CLI exits nonzero."""


def _font():
    for name in ("Times New Roman", "Times", "Arial", "Calibri", "Helvetica", "DejaVu Sans"):
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
            min_w: float = 1.45, max_w: float = 6.2) -> tuple[float, float]:
    lines = (text or " ").split("\n")
    w = min(max_w, max(min_w, max(_line_w(ln, fs) for ln in lines) + pad_x))
    h = max(0.36, 2 * pad_y + (fs / 72.0) * 1.42 * len(lines))
    return w, h


def _fit(text: str, max_chars: int, fs: float = FS) -> tuple[str, float, float]:
    t = _wrap(text, max_chars) if text else ""
    w, h = _box_wh(t, fs)
    return t, w, h


def parse_n(value) -> int | None:
    """Parse a count. Prefer n=N, then a leading '<N> patients' phrase. Never invent."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        if value.get("n") not in (None, ""):
            return parse_n(value.get("n"))
        for key in ("text", "label", "reason", "screened", "analyzed"):
            if key in value:
                found = parse_n(value.get(key))
                if found is not None:
                    return found
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _N_EQ.search(text)
    if match:
        return int(match.group(1))
    match = _LEADING_COUNT.match(text)
    if match:
        return int(match.group(1))
    return None


def _exclusion_items(raw) -> list[tuple[str, int | None]]:
    items = []
    for entry in raw or []:
        if isinstance(entry, dict):
            reason = str(entry.get("reason") or entry.get("text") or "").strip()
            n = parse_n(entry.get("n"))
            if n is None:
                n = parse_n(reason)
            if n is not None and not _N_EQ.search(reason):
                reason = f"{reason} (n={n})" if reason else f"(n={n})"
            items.append((reason, n))
        else:
            text = str(entry).strip()
            items.append((text, parse_n(text)))
    return [(t, n) for t, n in items if t]


def _numbered(title: str, lines: list[str], width: int = 36) -> str:
    out = [title]
    for i, line in enumerate(lines, 1):
        out.append(_wrap(f"{i}. {line}", width))
    return "\n".join(out)




class Canvas:
    """Inch-coordinate canvas. Origin at top-left; y grows downward.

    Items are collected first, so the canvas height follows the content, and
    everything is drawn once in render().
    """

    def __init__(self, width: float, height: float | None = None, dpi: int = 600):
        self.width = float(width)
        self.height = float(height) if height else 0.0
        self.dpi = int(dpi)
        self._items: list[tuple] = []
        self._bottom = 0.0

    def add_box(self, x, y, w, h, text, fs: float = FS, align: str = "center"):
        box = (float(x), float(y), float(w), float(h))
        self._items.append(("box", box, text or "", float(fs), align))
        self._bottom = max(self._bottom, box[1] + box[3])
        return box

    def add_arrow(self, x1, y1, x2, y2):
        seg = (float(x1), float(y1), float(x2), float(y2))
        self._items.append(("arrow", seg))
        self._bottom = max(self._bottom, seg[1], seg[3])

    def add_line(self, x1, y1, x2, y2):
        seg = (float(x1), float(y1), float(x2), float(y2))
        self._items.append(("line", seg))
        self._bottom = max(self._bottom, seg[1], seg[3])

    def render(self, out_path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        height = self.height or (self._bottom + 0.3)
        fig = plt.figure(figsize=(self.width, height), facecolor=FACE)
        ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
        ax.set_xlim(0.0, self.width)
        ax.set_ylim(height, 0.0)
        ax.set_axis_off()
        ax.set_facecolor(FACE)
        for kind, seg, *rest in self._items:
            if kind == "box":
                x, y, w, h = seg
                text, fs, align = rest
                ax.add_patch(
                    FancyBboxPatch(
                        (x, y), w, h,
                        boxstyle="square,pad=0",
                        linewidth=LW, edgecolor=EDGE, facecolor=FACE,
                    )
                )
                if text:
                    ha = "left" if align == "left" else "center"
                    tx = x + 0.14 if ha == "left" else x + w / 2
                    ax.text(
                        tx, y + h / 2, text,
                        ha=ha, va="center", multialignment=ha,
                        linespacing=1.42, color=EDGE,
                        fontproperties=FontProperties(family=FONT, size=fs),
                    )
            elif kind == "arrow":
                x1, y1, x2, y2 = seg
                ax.add_patch(
                    FancyArrowPatch(
                        (x1, y1), (x2, y2),
                        arrowstyle="-|>", mutation_scale=9.0,
                        linewidth=LW, color=EDGE, shrinkA=0.0, shrinkB=0.0,
                    )
                )
            else:
                x1, y1, x2, y2 = seg
                ax.plot([x1, x2], [y1, y2], color=EDGE, linewidth=LW,
                        solid_capstyle="butt")
        fig.savefig(out_path, dpi=self.dpi, facecolor=FACE)
        plt.close(fig)
        return out_path


FS_STUDY = 12
_MODELS = ("clinical", "radiomics", "nomogram")
_MODEL_LABEL = {
    "clinical": "Clinical model",
    "radiomics": "Radiomics model",
    "nomogram": "Nomogram",
}
_METRICS = ("roc", "calibration", "dca", "nri", "idi")
_METRIC_LABEL = {
    "roc": "ROC AUC",
    "calibration": "Calibration",
    "dca": "DCA",
    "nri": "NRI",
    "idi": "IDI",
}
_EXPLAIN = ("dag", "mediation")
_EXPLAIN_LABEL = {"dag": "DAG", "mediation": "Mediation"}


def _count(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def _need(spec: dict, key: str, problems: list[str]) -> int | None:
    n = _count(spec.get(key))
    if n is None:
        problems.append(f"missing {key}")
    return n


def _cohort(raw, name: str, problems: list[str]) -> dict | None:
    if not raw:
        return None
    if not isinstance(raw, dict):
        problems.append(f"{name} must be an object with n, positive, negative")
        return None
    n = _count(raw.get("n"))
    pos = _count(raw.get("positive"))
    neg = _count(raw.get("negative"))
    if n is None or pos is None or neg is None:
        problems.append(f"{name} needs n, positive, and negative")
        return None
    if pos + neg != n:
        problems.append(f"{name}: positive ({pos}) + negative ({neg}) != n ({n})")
    return {"n": n, "positive": pos, "negative": neg}


def audit_study(spec: dict) -> None:
    problems: list[str] = []
    screened = _need(spec, "screened_n", problems)
    included_n = _need(spec, "included_n", problems)
    included_pos = _need(spec, "included_positive", problems)
    included_neg = _need(spec, "included_negative", problems)
    if included_n is not None and included_pos is not None and included_neg is not None:
        if included_pos + included_neg != included_n:
            problems.append(
                f"included positive ({included_pos}) + negative ({included_neg}) "
                f"!= included_n ({included_n})"
            )
    exclusions = _exclusion_items(spec.get("exclusion"))
    if screened is not None and included_n is not None and exclusions:
        excl_sum = sum(n for _, n in exclusions if n is not None)
        if any(n is None for _, n in exclusions):
            problems.append("each exclusion needs an n")
        elif screened - excl_sum != included_n:
            problems.append(
                f"screened ({screened}) − Σ exclusion n ({excl_sum}) "
                f"!= included_n ({included_n})"
            )
    training = _cohort(spec.get("training"), "training", problems)
    test = _cohort(spec.get("test"), "test", problems)
    validation = _cohort(spec.get("validation"), "validation", problems) if spec.get("validation") else None
    if training and test and included_n is not None:
        total = training["n"] + test["n"] + (validation["n"] if validation else 0)
        if total != included_n:
            problems.append(f"cohort n sum ({total}) != included_n ({included_n})")
        pos = training["positive"] + test["positive"] + (validation["positive"] if validation else 0)
        neg = training["negative"] + test["negative"] + (validation["negative"] if validation else 0)
        if included_pos is not None and pos != included_pos:
            problems.append(f"cohort positive sum ({pos}) != included_positive ({included_pos})")
        if included_neg is not None and neg != included_neg:
            problems.append(f"cohort negative sum ({neg}) != included_negative ({included_neg})")
    balance = spec.get("balance") or None
    if balance not in (None, "", "downsample", "smote"):
        problems.append("balance must be downsample, smote, or omitted")
    if balance in ("downsample", "smote") and spec.get("balance_n") not in (None, ""):
        # Optional removed count. Not drawn as an eligibility exclusion.
        if _count(spec.get("balance_n")) is None:
            problems.append("balance_n must be an integer when set")
    for key, allowed in (("models", _MODELS), ("metrics", _METRICS), ("explain", _EXPLAIN)):
        unknown = [x for x in (spec.get(key) or []) if x not in allowed]
        if unknown:
            problems.append(f"unknown {key}: " + ", ".join(unknown))
    if problems:
        raise AuditError("n-audit failed: " + "; ".join(problems) + ". Do not invent n.")


def _pick(spec: dict, key: str, allowed: tuple[str, ...]) -> list[str]:
    return [x for x in allowed if x in (spec.get(key) or [])]


def draw_study_flow(spec: dict, out_path, audit: bool = True) -> Path:
    """Study flowchart: enrolment, cohorts, training-only processing, then evaluation."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spec = dict(spec or {})
    if audit:
        audit_study(spec)

    period = (spec.get("period") or "").strip()
    screened_n = int(spec["screened_n"])
    top = period + f"\n{screened_n} patients screened" if period else f"{screened_n} patients screened"
    inclusion = [str(x).strip() for x in (spec.get("inclusion") or []) if str(x).strip()]
    exclusions = _exclusion_items(spec.get("exclusion"))
    inc_txt = _numbered("Inclusion criteria:", inclusion, width=32) if inclusion else ""
    exc_txt = _numbered("Exclusion criteria:", [t for t, _ in exclusions], width=32) if exclusions else ""
    def _arm(raw: dict, side: str, default: str) -> str:
        label = str(raw.get(f"{side}_label") or default).strip() or default
        return f"{label} n = {int(raw[side])}"

    included = (
        f"Patients included\n(n = {int(spec['included_n'])})\n"
        f"{_arm(spec, 'included_positive', 'positive')}\n"
        f"{_arm(spec, 'included_negative', 'negative')}"
    )
    training = spec["training"]
    test = spec["test"]
    validation = spec.get("validation") or None

    def cohort_txt(title: str, raw: dict) -> str:
        return (
            f"{title}\n(n = {int(raw['n'])})\n"
            f"{_arm(raw, 'positive', 'positive')}\n"
            f"{_arm(raw, 'negative', 'negative')}"
        )

    cohorts = [("cohort-training", cohort_txt("Training Cohort", training))]
    cohorts.append(("cohort-test", cohort_txt("Test Cohort", test)))
    if validation:
        cohorts.append(("cohort-validation", cohort_txt("Validation Cohort", validation)))

    balance = spec.get("balance") or None
    balance_txt = ""
    if balance == "downsample":
        extra = spec.get("balance_n")
        balance_txt = "Downsample" if extra in (None, "") else f"Downsample\n(n = {int(extra)} removed)"
    elif balance == "smote":
        balance_txt = "SMOTE"
    lasso = bool(spec.get("lasso"))
    models = _pick(spec, "models", _MODELS)
    metrics = _pick(spec, "metrics", _METRICS)
    explain = _pick(spec, "explain", _EXPLAIN)

    gap_x = 0.32
    gap_y = 0.46
    fs = FS_STUDY

    def fitted(text: str, width: int = 28):
        if "\n" in (text or ""):
            w, h = _box_wh(text, fs, max_w=8.8)
            return text, w, h
        return _fit(text, width, fs=fs)

    top_t, top_w, top_h = fitted(top, 42)
    inc_w, inc_h = _box_wh(inc_txt, fs, pad_x=0.28, max_w=4.6) if inc_txt else (0.0, 0.0)
    exc_w, exc_h = _box_wh(exc_txt, fs, pad_x=0.28, max_w=4.6) if exc_txt else (0.0, 0.0)
    ana_t, ana_w, ana_h = fitted(included, 28)
    cohort_fit = [(bid, *fitted(text, 24)) for bid, text in cohorts]
    bal_fit = fitted(balance_txt, 22) if balance_txt else None
    las_fit = fitted("LASSO", 16) if lasso else None
    model_fit = [(mid, *fitted(_MODEL_LABEL[mid], 18)) for mid in models]
    metric_fit = [(mid, *fitted(_METRIC_LABEL[mid], 16)) for mid in metrics]
    explain_fit = [(mid, *fitted(_EXPLAIN_LABEL[mid], 16)) for mid in explain]

    def row_width(items) -> float:
        if not items:
            return 0.0
        return sum(w for _, w, _ in items) + gap_x * (len(items) - 1)

    cohort_w = row_width([(None, w, h) for _, _, w, h in cohort_fit])
    model_w = row_width([(None, w, h) for _, _, w, h in model_fit])
    metric_w = row_width([(None, w, h) for _, _, w, h in metric_fit])
    side_w = inc_w + (gap_x if inc_txt and exc_txt else 0) + exc_w
    canvas_w = max(11.2, top_w, ana_w, cohort_w, model_w, metric_w, side_w) + 0.8
    c = Canvas(width=canvas_w)

    def center_row(items, y, ids=None):
        widths = []
        packed = []
        if ids is None:
            for text, w, h in items:
                packed.append((None, text, w, h))
                widths.append(w)
        else:
            packed = items
            widths = [w for _, _, w, _ in items]
        total = sum(widths) + gap_x * (len(widths) - 1)
        x = (canvas_w - total) / 2
        placed = []
        for i, item in enumerate(packed):
            if ids is None:
                text, w, h = items[i]
                bid = None
            else:
                bid, text, w, h = item
            box = c.add_box(x, y, w, h, text, fs=fs)
            placed.append((bid, box))
            x += w + gap_x
        return placed

    y = 0.2
    top_box = c.add_box((canvas_w - top_w) / 2, y, top_w, top_h, top_t, fs=fs)
    y = top_box[1] + top_h + gap_y

    crit_h = max(inc_h, exc_h, 0.4)
    crit_ids = {}
    if inc_txt or exc_txt:
        pair_w = side_w or max(inc_w, exc_w)
        x = (canvas_w - pair_w) / 2
        if inc_txt:
            crit_ids["inclusion"] = c.add_box(x, y, inc_w, inc_h, inc_txt, align="left", fs=fs)
            c.add_arrow(top_box[0] + top_w / 2, top_box[1] + top_h, x + inc_w / 2, y)
            x += inc_w + gap_x
        if exc_txt:
            crit_ids["exclusion"] = c.add_box(x, y, exc_w, exc_h, exc_txt, align="left", fs=fs)
            c.add_arrow(top_box[0] + top_w / 2, top_box[1] + top_h, x + exc_w / 2, y)
        y += crit_h + gap_y

    ana = c.add_box((canvas_w - ana_w) / 2, y, ana_w, ana_h, ana_t, fs=fs)
    if "inclusion" in crit_ids:
        b = crit_ids["inclusion"]
        c.add_arrow(b[0] + b[2] / 2, b[1] + b[3], ana[0] + ana_w / 2, ana[1])
    if "exclusion" in crit_ids:
        b = crit_ids["exclusion"]
        c.add_arrow(b[0] + b[2] / 2, b[1] + b[3], ana[0] + ana_w / 2, ana[1])
    if not crit_ids:
        c.add_arrow(top_box[0] + top_w / 2, top_box[1] + top_h, ana[0] + ana_w / 2, ana[1])
    y = ana[1] + ana_h + gap_y

    placed_cohorts = center_row(cohort_fit, y, ids=True)
    cohort_map = {bid: box for bid, box in placed_cohorts}
    for _, box in placed_cohorts:
        c.add_arrow(ana[0] + ana_w / 2, ana[1] + ana_h, box[0] + box[2] / 2, box[1])
    cohort_bottom = max(box[1] + box[3] for _, box in placed_cohorts)
    train = cohort_map["cohort-training"]
    train_cx = train[0] + train[2] / 2
    y = cohort_bottom + gap_y
    stem = train

    if bal_fit:
        text, w, h = bal_fit
        bal = c.add_box(train_cx - w / 2, y, w, h, text, fs=fs)
        c.add_arrow(stem[0] + stem[2] / 2, stem[1] + stem[3], train_cx, y)
        stem = bal
        y = bal[1] + bal[3] + gap_y
    if las_fit:
        text, w, h = las_fit
        las = c.add_box(train_cx - w / 2, y, w, h, text, fs=fs)
        c.add_arrow(stem[0] + stem[2] / 2, stem[1] + stem[3], train_cx, y)
        stem = las
        y = las[1] + las[3] + gap_y
    model_boxes = []
    if model_fit:
        total = row_width([(None, w, h) for _, _, w, h in model_fit])
        x = train_cx - total / 2
        test_box = cohort_map.get("cohort-test")
        if test_box is not None:
            x = min(x, test_box[0] - 0.36 - total)
        x = max(0.2, x)
        h = max(h for _, _, _, h in model_fit)
        for mid, text, w, _h in model_fit:
            box = c.add_box(x, y, w, h, text, fs=fs)
            model_boxes.append(box)
            x += w + gap_x
        bar_y = y - 0.16
        c.add_line(stem[0] + stem[2] / 2, stem[1] + stem[3], stem[0] + stem[2] / 2, bar_y)
        c.add_line(model_boxes[0][0] + model_boxes[0][2] / 2, bar_y, model_boxes[-1][0] + model_boxes[-1][2] / 2, bar_y)
        for box in model_boxes:
            c.add_arrow(box[0] + box[2] / 2, bar_y, box[0] + box[2] / 2, box[1])
        y = y + h + gap_y
        stem_bottom = y - gap_y
    else:
        stem_bottom = stem[1] + stem[3]

    if metric_fit:
        bus_y = max(stem_bottom, cohort_bottom) + 0.55
        total = row_width([(None, w, h) for _, _, w, h in metric_fit])
        x = (canvas_w - total) / 2
        h = max(h for _, _, _, h in metric_fit)
        metric_boxes = []
        for mid, text, w, _h in metric_fit:
            box = c.add_box(x, bus_y + 0.36, w, h, text, fs=fs)
            metric_boxes.append(box)
            x += w + gap_x
        xs = [box[0] + box[2] / 2 for box in metric_boxes]
        xs.extend(box[0] + box[2] / 2 for box in model_boxes)
        for key in ("cohort-test", "cohort-validation"):
            side = cohort_map.get(key)
            if side:
                xs.append(side[0] + side[2] / 2)
        left, right = min(xs), max(xs)
        c.add_line(left, bus_y, right, bus_y)
        for box in model_boxes:
            cx = box[0] + box[2] / 2
            c.add_line(cx, box[1] + box[3], cx, bus_y)
        if not model_boxes:
            c.add_line(train_cx, stem[1] + stem[3], train_cx, bus_y)
        for key in ("cohort-test", "cohort-validation"):
            box = cohort_map.get(key)
            if not box:
                continue
            cx = box[0] + box[2] / 2
            c.add_line(cx, box[1] + box[3], cx, bus_y)
        for box in metric_boxes:
            cx = box[0] + box[2] / 2
            c.add_arrow(cx, bus_y, cx, box[1])
        y = metric_boxes[0][1] + h + gap_y
        if explain_fit:
            total = row_width([(None, w, h) for _, _, w, h in explain_fit])
            x = (canvas_w - total) / 2
            eh = max(h for _, _, _, h in explain_fit)
            explain_boxes = []
            for mid, text, w, _h in explain_fit:
                box = c.add_box(x, y, w, eh, text, fs=fs)
                explain_boxes.append(box)
                x += w + gap_x
            src = metric_boxes[len(metric_boxes) // 2]
            for box in explain_boxes:
                c.add_arrow(src[0] + src[2] / 2, src[1] + src[3], box[0] + box[2] / 2, box[1])

    c.render(out_path)
    return out_path


def docx_paragraphs(path) -> list[str]:
    """Paragraph text from a docx. Insertions are kept. Deletions are not."""
    import zipfile
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    paragraphs = []
    for para in root.iter(ns + "p"):
        parts = []
        for node in para.iter():
            if node.tag == ns + "delText":
                continue
            if node.tag == ns + "t" and node.text:
                parts.append(node.text)
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def methods_section(paragraphs: list[str]) -> str:
    start = None
    for i, line in enumerate(paragraphs):
        if re.fullmatch(r"(Materials and Methods|Methods)", line, flags=re.I):
            start = i + 1
            break
    if start is None:
        raise AuditError(
            "Materials and Methods was not found. The figure text is taken only from that section."
        )
    end = len(paragraphs)
    for i in range(start, len(paragraphs)):
        if re.fullmatch(r"Results", paragraphs[i], flags=re.I):
            end = i
            break
    text = "\n".join(paragraphs[start:end]).strip()
    if not text:
        raise AuditError("Materials and Methods is empty.")
    return text


def _numbered_block(text: str, heading: str, stop: str) -> list[str]:
    match = re.search(heading + r"(.{0,1200})", text, flags=re.I | re.S)
    if not match:
        return []
    block = match.group(1)
    block = re.split(stop, block, maxsplit=1, flags=re.I)[0]
    parts = re.split(r"\(\d+\)\s*", block)
    items = []
    for part in parts[1:]:
        part = re.split(r"\.\s+[A-Z]", part, maxsplit=1)[0]
        part = re.sub(r"^\s*and\s+", "", part, flags=re.I)
        part = re.sub(r"\s*;\s*and\s*$", "", part, flags=re.I)
        part = part.strip(" ;.\n")
        if part:
            items.append(part)
    return items


def _pair(snippet: str) -> dict | None:
    match = re.search(
        r"([A-Za-z][A-Za-z0-9\-/ ]{0,40}?),\s*n\s*=\s*(\d+)\s*;\s*"
        r"([A-Za-z][A-Za-z0-9\-/ ]{0,40}?),\s*n\s*=\s*(\d+)",
        snippet,
    )
    if not match:
        return None
    return {
        "positive_label": match.group(1).strip(),
        "positive": int(match.group(2)),
        "negative_label": match.group(3).strip(),
        "negative": int(match.group(4)),
    }


def _cohort_snippet(text: str, anchor: str) -> dict | None:
    match = re.search(anchor + r"(.{0,280})", text, flags=re.I | re.S)
    if not match:
        return None
    snippet = match.group(0)
    total = re.search(r"\(\s*n\s*=\s*(\d+)", snippet, flags=re.I)
    pair = _pair(snippet)
    if not pair:
        return None
    summed = pair["positive"] + pair["negative"]
    if total and int(total.group(1)) != summed and int(total.group(1)) not in (
        pair["positive"],
        pair["negative"],
    ):
        return None
    n = int(total.group(1)) if total and int(total.group(1)) == summed else summed
    return {"n": n, **pair}


def spec_from_methods(text: str) -> dict:
    """Build a study-flow spec from Materials and Methods only. Missing counts are not invented."""
    missing = []
    period = ""
    dates = re.search(
        r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\.|,|\s+were|\s+was)",
        text,
        flags=re.I,
    )
    if dates:
        period = f"{dates.group(1).strip()} to {dates.group(2).strip()}"

    screened = re.search(r"(\d+)\s+patients were screened", text, flags=re.I)
    if not screened:
        screened = re.search(r"\breview of\s+(\d+)\s+patients", text, flags=re.I)
    if not screened:
        missing.append("screened n")

    included_n = re.search(r"(\d+)\s+patients were (?:analyzed|included)", text, flags=re.I)
    included_pair = None
    analyzed_at = re.search(r"patients were (?:analyzed|included).{0,180}", text, flags=re.I | re.S)
    if analyzed_at:
        included_pair = _pair(analyzed_at.group(0))
    if not included_n:
        missing.append("included n")
    if not included_pair:
        missing.append("included positive and negative counts")

    inclusion = _numbered_block(
        text,
        r"Inclusion criteria were as follows:\s*",
        r"\bExclusion criteria\b|\bModel building\b|\bStatistical analysis\b",
    )
    exclusion_lines = _numbered_block(
        text,
        r"Exclusion criteria were as follows:\s*",
        r"\bModel building\b|\bStatistical analysis\b|\bPatients were divided\b",
    )
    exclusion = []
    for line in exclusion_lines:
        n = parse_n(line)
        if n is None:
            missing.append(f"exclusion n for: {line[:80]}")
        exclusion.append({"reason": line, "n": n})
    if re.search(r"Inclusion criteria", text, flags=re.I) and not inclusion:
        missing.append("inclusion criteria list")
    if re.search(r"Exclusion criteria", text, flags=re.I) and not exclusion:
        missing.append("exclusion criteria list")

    training = _cohort_snippet(text, r"training set")
    test = _cohort_snippet(text, r"test set")
    if not training:
        missing.append("training set n and both group counts")
    if not test:
        missing.append("test set n and both group counts")

    validation = None
    if re.search(r"validation set\s*\(\s*n\s*=", text, flags=re.I) and not re.search(
        r"no validation set|validation set is required|validation set was not",
        text,
        flags=re.I,
    ):
        validation = _cohort_snippet(text, r"validation set")
        if not validation:
            missing.append("validation set n and both group counts")

    if re.search(r"\bSMOTE\b", text) and re.search(r"downsampl", text, flags=re.I):
        missing.append("Methods names both SMOTE and downsample; the figure draws only one")
        balance = None
    elif re.search(r"\bSMOTE\b", text):
        balance = "smote"
    elif re.search(r"downsampl", text, flags=re.I):
        balance = "downsample"
    else:
        balance = None

    def mentioned(pattern: str) -> bool:
        return re.search(pattern, text, flags=re.I) is not None

    models = []
    if mentioned(r"clinical model"):
        models.append("clinical")
    if mentioned(r"radiomics"):
        models.append("radiomics")
    if mentioned(r"nomogram"):
        models.append("nomogram")
    metrics = []
    if mentioned(r"\bROC\b"):
        metrics.append("roc")
    if mentioned(r"calibration"):
        metrics.append("calibration")
    if mentioned(r"\bDCA\b|decision curve"):
        metrics.append("dca")
    if mentioned(r"\bNRI\b"):
        metrics.append("nri")
    if mentioned(r"\bIDI\b"):
        metrics.append("idi")
    explain = []
    if mentioned(r"\bDAG\b"):
        explain.append("dag")
    if mentioned(r"mediation"):
        explain.append("mediation")

    if missing:
        raise AuditError(
            "Materials and Methods does not state: " + "; ".join(missing) + ". Do not invent n."
        )

    spec = {
        "kind": "study",
        "source": "materials-and-methods",
        "period": period,
        "screened_n": int(screened.group(1)),
        "inclusion": inclusion,
        "exclusion": exclusion,
        "included_n": int(included_n.group(1)),
        "included_positive": included_pair["positive"],
        "included_negative": included_pair["negative"],
        "included_positive_label": included_pair["positive_label"],
        "included_negative_label": included_pair["negative_label"],
        "training": training,
        "test": test,
        "balance": balance,
        "lasso": bool(re.search(r"\bLASSO\b", text)),
        "models": models,
        "metrics": metrics,
        "explain": explain,
    }
    if validation:
        spec["validation"] = validation
    return spec


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Draw the Figure 1 study flowchart from Materials and Methods"
    )
    ap.add_argument("--json", help="Path to spec JSON")
    ap.add_argument("--docx", help="Manuscript .docx. Figure text is read from Materials and Methods only.")
    ap.add_argument("--out", required=True, help="Output PDF or PNG")
    ap.add_argument("--kind", choices=("auto", "study"), default="auto")
    ap.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip n-audit. Only for documented historical figures whose printed n do not add up.",
    )
    args = ap.parse_args(argv)
    if bool(args.json) == bool(args.docx):
        ap.error("Pass either --json or --docx")
    try:
        if args.docx:
            spec = spec_from_methods(methods_section(docx_paragraphs(args.docx)))
        else:
            spec = json.loads(Path(args.json).read_text(encoding="utf-8"))
            if isinstance(spec, dict) and "spec" in spec and "screened_n" not in spec and "analyzed" not in spec:
                spec = spec["spec"]
            if spec.get("kind") not in (None, "", "study") or "screened_n" not in spec:
                raise AuditError(
                    "The enrollment-only figure has been removed. "
                    "Pass --docx so the flowchart is read from Materials and Methods, "
                    "or a study-flow JSON with screened_n."
                )
        print(draw_study_flow(spec, args.out, audit=not args.no_audit))
    except AuditError as err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
