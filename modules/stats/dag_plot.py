"""
dag_plot.py — 假设因果 DAG（完整领域结构 + 中介边高亮）
"""
from __future__ import annotations
from typing import Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import networkx as nx
import numpy as np

from modules import config as cfg

Edge = Tuple[str, str]

# ── 发表级柔和配色 ──
_COLOR_CLIN = "#7EB6A4"
_COLOR_RAD = "#6BA3D0"
_COLOR_OUT = "#E8A060"
_COLOR_EDGE = "#94A3B8"
_COLOR_HI = "#D64550"
_COLOR_TEXT = "#1F2937"
_COLOR_NODE_EDGE = "#334155"
_COLOR_BG = "#FAFBFC"


def _outcome_names() -> set:
    names = {"Dedifferentiated", "Group", "Recurrence"}
    outcome = str(getattr(cfg, "GROUP_SOURCE_COL", "") or "")
    if outcome:
        names.add(outcome)
    for o in getattr(cfg, "OUTCOME_COLS", []) or []:
        names.add(str(o))
    return names


def _wrap_label(name: str) -> str:
    s = str(name)
    fixed = {
        "Surrounding involving": "Surrounding\ninvolving",
        "Satellite nodules": "Satellite\nnodules",
        "Tumor_Size_MRI": "Tumor Size\nMRI",
        "Metabolic_Syndrome": "Metabolic\nSyndrome",
        "Reproductive_History": "Reproductive\nHistory",
        "IHC_Ki67": "IHC Ki67",
        "Pathologic_Grade": "Pathologic\nGrade",
        "Pathologic_MI": "Pathologic\nMI",
        "Pathologic_CSI": "Pathologic\nCSI",
        "Pathologic_LNM": "Pathologic\nLNM",
        "Pathologic_LVSI": "Pathologic\nLVSI",
        "Pathologic_EU": "Pathologic\nEU",
    }
    if s in fixed:
        return fixed[s]
    if s.startswith("Pathologic_"):
        return "Pathologic\n" + s.split("_", 1)[1]
    if "_" in s and len(s) > 12:
        a, b = s.split("_", 1)
        return f"{a}\n{b}"
    if " " in s and len(s) > 12:
        parts = s.split(" ", 1)
        return parts[0] + "\n" + parts[1]
    return s


def _node_size(label: str) -> Tuple[float, float]:
    """数据坐标下的节点宽高（随行数/字数略调）。"""
    lines = label.split("\n")
    max_chars = max(len(ln) for ln in lines) if lines else 4
    n_lines = len(lines)
    w = min(0.26, max(0.16, 0.011 * max_chars + 0.09))
    h = min(0.13, max(0.075, 0.042 * n_lines + 0.035))
    return w, h


def _layout(nodes: Sequence[str]) -> dict:
    """
    三列布局（左临床 → 中 RadScore → 右结局），
    临床节点按数量自适应竖向间距，保证盒体不重叠。
    """
    nodes = list(nodes)
    outcome_set = _outcome_names()
    rad_set = {"RadScore"}

    outcomes = [n for n in nodes if n in outcome_set]
    rads = [n for n in nodes if n in rad_set]
    clin = [n for n in nodes if n not in outcome_set and n not in rad_set]

    clin_pref = [
        "Age", "IHC_Ki67", "CA125", "Tumor_Size_MRI",
        "Metabolic_Syndrome", "Reproductive_History",
        "KI67", "MDM2", "Surrounding involving", "Tumor density",
        "Radiologists", "Satellite nodules",
    ]
    clin_sorted = [c for c in clin_pref if c in clin]
    clin_sorted += [c for c in clin if c not in clin_sorted]

    pos: dict = {}
    n_c = len(clin_sorted)
    # 竖向均匀排布，顶/底留白；中心间距 ≥ 单盒高度 + 间隙
    if n_c == 0:
        pass
    elif n_c == 1:
        pos[clin_sorted[0]] = (0.14, 0.50)
    else:
        y_top, y_bot = 0.93, 0.07
        for i, name in enumerate(clin_sorted):
            y = y_top - i * (y_top - y_bot) / (n_c - 1)
            pos[name] = (0.14, y)

    for i, name in enumerate(rads):
        pos[name] = (0.52, 0.50 if len(rads) == 1 else 0.68 - 0.36 * i)

    n_o = len(outcomes)
    for i, name in enumerate(outcomes):
        if n_o == 1:
            y = 0.50
        else:
            y = 0.78 - i * (0.56 / max(n_o - 1, 1))
        pos[name] = (0.90, y)

    for i, n in enumerate(nodes):
        if n not in pos:
            pos[n] = (0.52, 0.12 * (i + 1))
    return pos


def _rect_edge_point(
    center: Tuple[float, float],
    wh: Tuple[float, float],
    toward: Tuple[float, float],
    pad: float = 0.008,
) -> Tuple[float, float]:
    """从矩形中心指向 toward 时与矩形边界的交点（外扩 pad）。"""
    cx, cy = center
    w, h = wh
    hw, hh = w / 2 + pad, h / 2 + pad
    dx = toward[0] - cx
    dy = toward[1] - cy
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return cx + hw, cy
    # 射线与轴对齐矩形相交
    scale_x = hw / abs(dx) if abs(dx) > 1e-12 else np.inf
    scale_y = hh / abs(dy) if abs(dy) > 1e-12 else np.inf
    t = min(scale_x, scale_y)
    return cx + t * dx, cy + t * dy


def _draw_rounded_node(ax, xy, label: str, facecolor: str, fs: float, wh: Tuple[float, float]) -> None:
    x, y = xy
    w, h = wh
    box = FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.008,rounding_size=0.025",
        facecolor=facecolor,
        edgecolor=_COLOR_NODE_EDGE,
        linewidth=1.5,
        alpha=0.97,
        zorder=3,
    )
    ax.add_patch(box)
    from modules.config.style import fig_font_chain
    ax.text(
        x, y, label, ha="center", va="center",
        fontsize=fs, color=_COLOR_TEXT, fontweight="medium",
        zorder=4, linespacing=1.12,
        fontfamily=fig_font_chain(),
    )


def _draw_edge(
    ax,
    p0: Tuple[float, float],
    p1: Tuple[float, float],
    wh0: Tuple[float, float],
    wh1: Tuple[float, float],
    color: str,
    lw: float,
    rad: float = 0.0,
) -> None:
    """边从源盒边界到目标盒边界，带箭头。"""
    s = _rect_edge_point(p0, wh0, p1)
    e = _rect_edge_point(p1, wh1, p0)
    arrow = FancyArrowPatch(
        s, e,
        connectionstyle=f"arc3,rad={rad:.3f}",
        arrowstyle="-|>",
        mutation_scale=16 if lw < 2.5 else 19,
        linewidth=lw,
        color=color,
        alpha=0.90,
        zorder=2,
        shrinkA=0,
        shrinkB=0,
    )
    ax.add_patch(arrow)


def plot_assumed_dag(
    nodes: Optional[Sequence[str]] = None,
    edges: Optional[Sequence[Edge]] = None,
    highlight_edges: Optional[Sequence[Edge]] = None,
    title: str = "Assumed DAG — mediation-related edges highlighted",
    filename: str = "dag_mediation_paths",
) -> None:
    nodes = list(nodes if nodes is not None else getattr(cfg, "DAG_NODES", []))
    edges = list(edges if edges is not None else getattr(cfg, "DAG_EDGES", []))
    hi_cfg = list(
        highlight_edges if highlight_edges is not None
        else getattr(cfg, "DAG_MEDIATION_HIGHLIGHT", []) or []
    )
    highlight = set(tuple(e) for e in hi_cfg)

    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    for e in edges:
        if len(e) >= 2:
            G.add_edge(e[0], e[1])

    pos = _layout(list(G.nodes()))
    labels = {n: _wrap_label(n) for n in G.nodes()}
    sizes = {n: _node_size(labels[n]) for n in G.nodes()}

    n_clin = sum(1 for n in G.nodes() if n not in _outcome_names() and n != "RadScore")
    # 节点多时加高画布，避免挤压
    fig_h = max(7.2, 1.15 * max(n_clin, 4) + 1.5)
    fig_w = 13.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=120)
    fig.patch.set_facecolor(_COLOR_BG)
    ax.set_facecolor(_COLOR_BG)

    outcome_nodes = _outcome_names()
    rad_nodes = {"RadScore"}

    normal = [(u, v) for u, v in G.edges() if (u, v) not in highlight]
    hi = [(u, v) for u, v in G.edges() if (u, v) in highlight]

    # 弧度：同向多边轻微错开，避免完全重合；过大弧易造成“方向反了”的错觉
    def _rad_for(u, v, i: int, scale: float) -> float:
        yu, yv = pos[u][1], pos[v][1]
        # 源在上则弧向上弯，源在下则向下
        sign = 1.0 if yu > yv + 0.02 else (-1.0 if yu < yv - 0.02 else (1.0 if i % 2 else -1.0))
        return sign * scale * (0.35 + 0.12 * (i % 3))

    for i, (u, v) in enumerate(normal):
        if u not in pos or v not in pos:
            continue
        _draw_edge(
            ax, pos[u], pos[v], sizes[u], sizes[v],
            _COLOR_EDGE, lw=1.6, rad=_rad_for(u, v, i, 0.06),
        )
    for i, (u, v) in enumerate(hi):
        if u not in pos or v not in pos:
            continue
        _draw_edge(
            ax, pos[u], pos[v], sizes[u], sizes[v],
            _COLOR_HI, lw=2.4, rad=_rad_for(u, v, i, 0.09),
        )

    fs = cfg.FIG_FONT_SIZE
    for n in G.nodes():
        if n not in pos:
            continue
        if n in outcome_nodes:
            fc = _COLOR_OUT
        elif n in rad_nodes:
            fc = _COLOR_RAD
        else:
            fc = _COLOR_CLIN
        _draw_rounded_node(ax, pos[n], labels[n], fc, fs, sizes[n])

    ax.set_title(title, fontsize=fs + 1, pad=12, color=_COLOR_TEXT, fontweight="normal")
    ax.axis("off")

    handles = [
        mpatches.Patch(facecolor=_COLOR_CLIN, edgecolor=_COLOR_NODE_EDGE, label="Clinical / pathology"),
        mpatches.Patch(facecolor=_COLOR_RAD, edgecolor=_COLOR_NODE_EDGE, label="Radiomics"),
        mpatches.Patch(facecolor=_COLOR_OUT, edgecolor=_COLOR_NODE_EDGE, label="Outcome"),
        mpatches.Patch(facecolor=_COLOR_EDGE, edgecolor=_COLOR_EDGE, label="Assumed edge"),
    ]
    if hi:
        handles.append(
            mpatches.Patch(facecolor=_COLOR_HI, edgecolor=_COLOR_HI, label="Mediation-related edge")
        )
    leg = ax.legend(
        handles=handles, loc="lower right", fontsize=fs - 0.5, frameon=True,
        borderpad=0.7, labelspacing=0.45, handlelength=1.4,
        title="Legend", title_fontsize=fs - 0.5,
        fancybox=True, framealpha=0.97,
        bbox_to_anchor=(0.995, 0.01),
    )
    frame = leg.get_frame()
    frame.set_facecolor("white")
    frame.set_edgecolor("#D1D5DB")
    frame.set_linewidth(1.0)

    # 根据节点盒体外扩坐标范围
    xs, ys = [], []
    for n, (x, y) in pos.items():
        w, h = sizes[n]
        xs.extend([x - w / 2, x + w / 2])
        ys.extend([y - h / 2, y + h / 2])
    ax.set_xlim(min(xs) - 0.08, max(xs) + 0.12)
    ax.set_ylim(min(ys) - 0.08, max(ys) + 0.10)
    ax.set_aspect("equal", adjustable="box")

    fig.tight_layout()
    wrote = cfg.save_plot(filename, fig, width=fig_w, height=fig_h, dpi=220)
    plt.close(fig)
    if wrote:
        print(
            f"  {filename}.png saved  "
            f"(nodes={G.number_of_nodes()}, edges={G.number_of_edges()}, hi-res)"
        )


def _filter_to_available(
    nodes: Sequence[str],
    edges: Sequence[Edge],
    hi: Sequence[Edge],
    available_cols: Optional[Sequence[str]] = None,
) -> tuple[list[str], list[Edge], list[Edge], list[str]]:
    """丢掉当前数据里不存在的节点/边，避免别的课题模板污染 Assumed DAG。"""
    if available_cols is None:
        return list(nodes), [tuple(e[:2]) for e in edges if isinstance(e, (list, tuple)) and len(e) >= 2], [
            tuple(e[:2]) for e in hi if isinstance(e, (list, tuple)) and len(e) >= 2
        ], []
    allow = {str(c).lower(): str(c) for c in available_cols}
    for extra in list(_outcome_names()) + ["RadScore", "Group", "Combined", "ClinicalScore"]:
        allow.setdefault(str(extra).lower(), str(extra))

    def _keep(name) -> Optional[str]:
        if name is None:
            return None
        return allow.get(str(name).strip().lower())

    kept: list[str] = []
    seen: set[str] = set()
    dropped: list[str] = []
    for n in nodes:
        c = _keep(n)
        if not c:
            dropped.append(str(n))
            continue
        if c not in seen:
            seen.add(c)
            kept.append(c)
    out_edges: list[Edge] = []
    eset: set[Edge] = set()
    for e in edges:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        a, b = _keep(e[0]), _keep(e[1])
        if a and b and (a, b) not in eset:
            out_edges.append((a, b))
            eset.add((a, b))
    out_hi: list[Edge] = []
    for e in hi:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        a, b = _keep(e[0]), _keep(e[1])
        if a and b:
            out_hi.append((a, b))
    return kept, out_edges, out_hi, dropped


def run_dag(mediation_paths: Optional[list] = None,
            available_cols: Optional[Sequence[str]] = None) -> dict:
    """绘制完整假设 DAG，中介相关边高亮。"""
    from modules.utils import dry_run as _dry
    print("═══ DAG (assumed, full) ═══")
    if _dry.exit_after_plan("dag_plot", [
        f"nodes={len(list(getattr(cfg, 'DAG_NODES', []) or []))}",
        "plot assumed DAG (skipped)",
    ]):
        return {"nodes": [], "edges": [], "highlight": [], "dry_run": True}
    nodes = list(getattr(cfg, "DAG_NODES", []))
    edges = list(getattr(cfg, "DAG_EDGES", []))
    hi = list(getattr(cfg, "DAG_MEDIATION_HIGHLIGHT", []) or [])
    nodes, edges, hi, dropped = _filter_to_available(nodes, edges, hi, available_cols)
    if dropped:
        print(f"  DAG dropped (not in data): {', '.join(dropped)}")

    if not hi and mediation_paths:
        for p in mediation_paths:
            x, m, y = p.get("x"), p.get("m"), p.get("y")
            y_lab = (
                "Dedifferentiated"
                if str(y).lower() in ("group", "y", "label")
                else y
            )
            if isinstance(m, (list, tuple)):
                for mi in m:
                    hi.append((x, mi))
                    hi.append((mi, y_lab))
            elif m:
                hi.append((x, m))
                hi.append((m, y_lab))

    plot_assumed_dag(
        nodes, edges, highlight_edges=hi,
        title="Assumed DAG — mediation-related edges highlighted",
        filename="dag_mediation_paths",
    )
    plot_assumed_dag(
        nodes, edges, highlight_edges=hi,
        title="Assumed DAG — mediation-related edges highlighted",
        filename="dag_assumed",
    )
    return {"nodes": nodes, "edges": edges, "highlight": hi}
