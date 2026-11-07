# -*- coding: utf-8 -*-
"""geometry -> PNG（300 dpi）/ PDF（矢量）渲染。

输入是 ``modules.flowfigure.layout.build`` 产出的 geometry，不再重新排版；
因此预览（geometry）与交付图（PNG/PDF）来自同一份坐标。

用 ``Figure + FigureCanvasAgg`` 对象 API，不碰 pyplot 全局状态：控制台在请求
线程里调用也不会和后端/其他线程抢 pyplot。
"""
from __future__ import annotations

from pathlib import Path

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

from modules.flowfigure import spec as spec_mod
from modules.flowfigure.layout import FONT

# -- CONFIG -------------------------------------------------------------------
EDGE = "#000000"
FACE = "#FFFFFF"
LW = 1.05
DPI = 300


def render(geometry: dict, out_path, quiet: bool = False) -> Path:
    """按 geometry 画一张图；页宽/页高即 figsize，PNG 300 dpi，PDF 矢量。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page_w, page_h = geometry["page_in"]
    fig = Figure(figsize=(page_w, page_h), dpi=DPI)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.0, 0.0, 1.0, 1.0))
    ax.set_xlim(0, page_w)
    ax.set_ylim(page_h, 0)
    ax.set_aspect("auto")
    ax.axis("off")
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    for box in geometry["boxes"]:
        x, y = box["x"], box["y"]
        w, h = box["w"], box["h"]
        ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h, boxstyle="square,pad=0",
                linewidth=LW, edgecolor=EDGE, facecolor=FACE, joinstyle="miter",
            )
        )
        if box.get("lines"):
            align = box.get("align") or "center"
            ax.text(
                x + (0.14 if align == "left" else w / 2),
                y + h / 2,
                "\n".join(box["lines"]),
                ha="left" if align == "left" else "center",
                va="center",
                fontsize=box["fs"],
                fontfamily=FONT,
                color=EDGE,
                linespacing=1.42,
            )

    for label in geometry.get("labels") or []:
        align = label.get("align") or "center"
        ax.text(
            label["x"], label["y"], label["text"],
            ha="left" if align == "left" else "center",
            va="top", fontsize=label["fs"], fontfamily=FONT,
            color=EDGE, linespacing=1.42,
        )

    for edge in geometry["edges"]:
        (x1, y1), (x2, y2) = edge["a"], edge["b"]
        if edge["kind"] == "dashed":
            ax.add_patch(
                FancyArrowPatch(
                    (x1, y1), (x2, y2), arrowstyle="-", mutation_scale=1,
                    linewidth=0.9, color=EDGE, shrinkA=0, shrinkB=0,
                    linestyle=(0, (4, 3)),
                )
            )
        elif edge["kind"] == "arrow":
            ax.add_patch(
                FancyArrowPatch(
                    (x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=11,
                    linewidth=LW, color=EDGE, shrinkA=0.6, shrinkB=0.6,
                )
            )
        else:
            ax.add_patch(
                FancyArrowPatch(
                    (x1, y1), (x2, y2), arrowstyle="-", mutation_scale=1,
                    linewidth=LW, color=EDGE, shrinkA=0, shrinkB=0,
                )
            )

    fig.savefig(out_path, dpi=DPI, facecolor="white")
    if quiet:
        return out_path
    page_cm = [page_w * spec_mod.CM_PER_INCH, page_h * spec_mod.CM_PER_INCH]
    print(
        f"  Figure 1 page: {page_cm[0]:.1f} × {page_cm[1]:.1f} cm "
        f"(target {spec_mod.TARGET_WIDTH_CM:g}, max {spec_mod.MAX_WIDTH_CM:g})",
        flush=True,
    )
    for warning in geometry.get("warnings") or []:
        print(f"  ⚠ {warning}", flush=True)
    return out_path
