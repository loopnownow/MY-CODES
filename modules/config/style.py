"""modules.config.style — 绘图主题与保存。"""
from __future__ import annotations
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import settings as _S

OUT_DIR = _S.OUT_DIR
PNG_DIR = _S.PNG_DIR
DO_PLOT = _S.DO_PLOT
FIG_FONT_SIZE = _S.FIG_FONT_SIZE
FIG_FONT_FAMILY = str(getattr(_S, "FIG_FONT_FAMILY", "") or "Arial").strip() or "Arial"

# 只放一个中文字体。Arial 在前，缺字再落到它。钉死单一 fontname 会丢掉这层回退。
_CJK_FONT_PREFERENCE = (
    "Microsoft YaHei",
    "Noto Sans SC",
    "Source Han Sans SC",
    "SimHei",
    "Noto Sans CJK SC",
)


def _installed_font_names() -> set[str]:
    from matplotlib import font_manager
    return {f.name for f in font_manager.fontManager.ttflist}


def fig_font_chain(family: str | None = None) -> list[str]:
    """Latin 用指定字体；中文落到本机已安装的一个 CJK 字体。"""
    fam = str(family or getattr(_S, "FIG_FONT_FAMILY", "") or FIG_FONT_FAMILY).strip() or "Arial"
    have = _installed_font_names()
    cjk = next((n for n in _CJK_FONT_PREFERENCE if n in have), "")
    chain: list[str] = []
    for n in (fam, cjk, "DejaVu Sans"):
        if n and n not in chain:
            chain.append(n)
    return chain


def apply_fig_font(family: str | None = None) -> str:
    """图用字体链。font.family 必须是列表，单写 Arial 时中文会变成方框。"""
    chain = fig_font_chain(family)
    plt.rcParams.update({
        "font.family": chain,
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    return chain[0]


apply_fig_font()

# 主色角色（ROC / KM / DCA 前 5 条）：Combined, RadScore, Clinical, Reader, Extra
FIG_MAIN_ROLES = ("Combined", "RadScore", "Clinical", "Reader", "Age")
FIG_EXTRA_ROLES = (
    "Diameter", "Volume", "SCC", "CA125", "DSI", "LNM", "PMI",
    "PCSR", "PMSMR", "LNSMR", "Fertility", "FamilyHistory",
    "Menopause", "FIGO", "Histology",
)

# 20 套投稿配色。每套 5 主色 + 5 辅色。hex 须与 console_schema.js PALETTES 一致。
FIG_PALETTES = {
    "npg": {
        "id": "npg",
        "label": "NPG 现行",
        "tag": "与现图一致（默认）",
        "blurb": "保留当前 Combined / RadScore / Clinical。改稿、返修、补图用这个，颜色不会跳。",
        "main": ["#D55E00", "#B2182B", "#2166AC", "#762A83", "#117733"],
        "aux": ["#44AA99", "#DDCC77", "#EE8866", "#AA4499", "#332288"],
    },
    "okabe": {
        "id": "okabe",
        "label": "Okabe–Ito",
        "tag": "新稿推荐 / 色盲安全",
        "blurb": "Nature Methods 常用。红绿色盲可分，屏幕和印刷都稳。新开课题优先。",
        "main": ["#D55E00", "#0072B2", "#009E73", "#CC79A7", "#E69F00"],
        "aux": ["#56B4E9", "#332288", "#882255", "#44AA99", "#999999"],
    },
    "tol": {
        "id": "tol",
        "label": "Paul Tol",
        "tag": "印刷 / Radiology",
        "blurb": "Paul Tol muted。饱和度低，CMYK 印刷不容易糊。适合 Radiology / Eur Radiol 大图。",
        "main": ["#CC6677", "#332288", "#117733", "#88CCEE", "#DDCC77"],
        "aux": ["#AA4499", "#44AA99", "#882255", "#6699CC", "#999933"],
    },
    "radiology": {
        "id": "radiology",
        "label": "青橙",
        "tag": "本组控制台同色",
        "blurb": "深青 + 砖红 + 陶土橙，和 console 同一套。组会幻灯、系列论文统一视觉用。",
        "main": ["#0F4C5C", "#C44E52", "#3B6EA5", "#E07A3D", "#2F6F4E"],
        "aux": ["#7EB6A4", "#88B4D8", "#D4A373", "#6B5B95", "#8A8A8A"],
    },
    "ibm": {
        "id": "ibm",
        "label": "IBM",
        "tag": "投影 / 幻灯",
        "blurb": "IBM color-blind safe。对比强，投大屏清楚。网页结果 HTML 也好认。",
        "main": ["#DA1E28", "#0F62FE", "#198038", "#8A3FFC", "#B28600"],
        "aux": ["#1192E8", "#009D9A", "#FA4D56", "#A56EFF", "#6F6F6F"],
    },
    "aaas": {
        "id": "aaas",
        "label": "AAAS / Science",
        "tag": "Science 风",
        "blurb": "Science / AAAS 常用离散色。主色偏蓝红绿，适合多曲线对照。",
        "main": ["#3B4992", "#EE0000", "#008B45", "#631879", "#008280"],
        "aux": ["#BB0021", "#5F559B", "#A20056", "#808180", "#1B1919"],
    },
    "nejm": {
        "id": "nejm",
        "label": "NEJM",
        "tag": "新英格兰",
        "blurb": "NEJM 系列色。对比清晰，适合临床结局曲线。",
        "main": ["#BC3C29", "#0072B5", "#E18727", "#20854E", "#7876B1"],
        "aux": ["#6F99AD", "#FFDC91", "#EE4C97", "#7E6148", "#3C5488"],
    },
    "lancet": {
        "id": "lancet",
        "label": "Lancet",
        "tag": "柳叶刀",
        "blurb": "Lancet 风格。红橙青绿，印刷与屏幕都清楚。",
        "main": ["#00468B", "#ED0000", "#42B540", "#0099B4", "#925E9F"],
        "aux": ["#ADA638", "#ADB6B6", "#1B1919", "#FDAF91", "#6A6599"],
    },
    "jama": {
        "id": "jama",
        "label": "JAMA",
        "tag": "JAMA 风",
        "blurb": "JAMA 网络期刊常用色。偏深蓝与暖橙。",
        "main": ["#374E55", "#DF8F44", "#00A1D5", "#B24745", "#79AF97"],
        "aux": ["#6A6599", "#80796B", "#024B7A", "#EFC000", "#868686"],
    },
    "jco": {
        "id": "jco",
        "label": "JCO",
        "tag": "临床肿瘤",
        "blurb": "Journal of Clinical Oncology 系列色。蓝紫绿对比稳。",
        "main": ["#0073C2", "#EFC000", "#868686", "#CD534C", "#7AA6DC"],
        "aux": ["#003C67", "#8F7700", "#3B3B3B", "#A73030", "#4A6990"],
    },
    "d3": {
        "id": "d3",
        "label": "D3 Category10",
        "tag": "通用 / 多模型",
        "blurb": "D3 / matplotlib 默认 10 色。辨识度高，模型多时不撞色。",
        "main": ["#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD"],
        "aux": ["#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF"],
    },
    "tableau": {
        "id": "tableau",
        "label": "Tableau 10",
        "tag": "柔和 / 幻灯",
        "blurb": "Tableau 10 新版。饱和度适中，屏幕和投影都耐看。",
        "main": ["#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F"],
        "aux": ["#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC"],
    },
    "set1": {
        "id": "set1",
        "label": "Brewer Set1",
        "tag": "高对比",
        "blurb": "ColorBrewer Set1。红蓝绿紫橙对比强，适合 3–5 条主曲线。",
        "main": ["#E41A1C", "#377EB8", "#4DAF4A", "#984EA3", "#FF7F00"],
        "aux": ["#A65628", "#F781BF", "#999999", "#1B9E77", "#E6AB02"],
    },
    "dark2": {
        "id": "dark2",
        "label": "Brewer Dark2",
        "tag": "深色 / 印刷",
        "blurb": "ColorBrewer Dark2。偏深，白底印刷不发虚，细线也清楚。",
        "main": ["#1B9E77", "#D95F02", "#7570B3", "#E7298A", "#66A61E"],
        "aux": ["#E6AB02", "#A6761D", "#666666", "#1F78B4", "#B15928"],
    },
    "paired": {
        "id": "paired",
        "label": "Brewer Paired",
        "tag": "主辅成对",
        "blurb": "ColorBrewer Paired。主色深、辅色为同色系浅色，适合训练/验证成对曲线。",
        "main": ["#1F78B4", "#E31A1C", "#33A02C", "#FF7F00", "#6A3D9A"],
        "aux": ["#A6CEE3", "#FB9A99", "#B2DF8A", "#FDBF6F", "#CAB2D6"],
    },
    "tolbright": {
        "id": "tolbright",
        "label": "Tol Bright",
        "tag": "色盲安全 / 明亮",
        "blurb": "Paul Tol bright + dark。色盲可分，比 muted 更亮，适合屏幕阅读。",
        "main": ["#4477AA", "#EE6677", "#228833", "#CCBB44", "#66CCEE"],
        "aux": ["#AA3377", "#BBBBBB", "#222255", "#225555", "#663333"],
    },
    "frontiers": {
        "id": "frontiers",
        "label": "Frontiers",
        "tag": "Frontiers 风",
        "blurb": "Frontiers 系列期刊常用色。红橙绿蓝紫跨度大。",
        "main": ["#D51317", "#F39200", "#95C11F", "#0094CD", "#6F286A"],
        "aux": ["#007B3D", "#31B7BC", "#164194", "#EFD500", "#706F6F"],
    },
    "bmj": {
        "id": "bmj",
        "label": "BMJ",
        "tag": "BMJ 风",
        "blurb": "BMJ 系列色。蓝金品红对比清楚，适合临床对照曲线。",
        "main": ["#2A6EBB", "#F0AB00", "#C50084", "#7D5CC6", "#E37222"],
        "aux": ["#69BE28", "#00B2A9", "#CD202C", "#747678", "#1B1919"],
    },
    "uchicago": {
        "id": "uchicago",
        "label": "UChicago",
        "tag": "沉稳 / 学术",
        "blurb": "UChicago 学术配色。栗红、灰、金为主，低饱和，正式场合用。",
        "main": ["#800000", "#155F83", "#FFA319", "#8A9045", "#767676"],
        "aux": ["#C16622", "#8F3931", "#58593F", "#350E20", "#D6D6CE"],
    },
    "gray": {
        "id": "gray",
        "label": "灰度",
        "tag": "黑白印刷",
        "blurb": "纯灰阶。黑白印刷或复印不丢信息，配合线型区分曲线。",
        "main": ["#000000", "#4D4D4D", "#7F7F7F", "#262626", "#A6A6A6"],
        "aux": ["#595959", "#8C8C8C", "#333333", "#BFBFBF", "#1A1A1A"],
    },
}
DEFAULT_FIG_PALETTE = "npg"

# 未选调色板 / NPG 时沿用的旧命名色，避免返修补图跳色
_LEGACY_NAMED = {
    "RadScore": "#B2182B",
    "Clinical": "#2166AC",
    "Combined": "#D55E00",
    "Age": "#117733",
    "Diameter": "#44AA99",
    "Volume": "#DDCC77",
    "SCC": "#EE8866",
    "CA125": "#AA4499",
    "DSI": "#332288",
    "LNM": "#88CCEE",
    "PMI": "#CC6677",
    "PCSR": "#6699CC",
    "PMSMR": "#882255",
    "LNSMR": "#BBCC33",
    "Fertility": "#77AADD",
    "FamilyHistory": "#AA4466",
    "Menopause": "#228833",
    "FIGO": "#E69F00",
    "Histology": "#999999",
}

_LEGACY_FALLBACK = [
    "#332288", "#117733", "#44AA99", "#88CCEE", "#DDCC77",
    "#CC6677", "#AA4499", "#882255", "#6699CC", "#EE8866",
]

ROC_COLOR_MAP = dict(_LEGACY_NAMED)
ROC_FALLBACK_COLORS = list(_LEGACY_FALLBACK)


def _norm_hex(c, fallback: str = "#888888") -> str:
    s = str(c or "").strip()
    if s.startswith("#") and len(s) == 7:
        try:
            int(s[1:], 16)
            return s.upper()
        except ValueError:
            return fallback
    if s.startswith("#") and len(s) == 4:
        try:
            int(s[1:], 16)
            return ("#" + s[1] * 2 + s[2] * 2 + s[3] * 2).upper()
        except ValueError:
            return fallback
    return fallback


def _pad_hex_list(vals, n: int, preset) -> list:
    out = [_norm_hex(x, preset[i % len(preset)]) for i, x in enumerate(list(vals or []))]
    while len(out) < n:
        out.append(_norm_hex(preset[len(out) % len(preset)]))
    return out[:n]


def colors_from_palette(pal_id: str, main=None, aux=None) -> tuple[list, list]:
    pid = str(pal_id or DEFAULT_FIG_PALETTE).strip().lower()
    if pid == "custom":
        pid = DEFAULT_FIG_PALETTE
    preset = FIG_PALETTES.get(pid) or FIG_PALETTES[DEFAULT_FIG_PALETTE]
    return (
        _pad_hex_list(main, 5, preset["main"]),
        _pad_hex_list(aux, 5, preset["aux"]),
    )


def map_from_palette(main, aux, pal_id: str | None = None) -> dict:
    main = list(main)
    aux = list(aux)
    pid = str(pal_id or "").strip().lower()
    if pid in ("", "npg"):
        out = dict(_LEGACY_NAMED)
        for i, name in enumerate(FIG_MAIN_ROLES):
            out[name] = main[i]
        return out
    out = {}
    for i, name in enumerate(FIG_MAIN_ROLES):
        out[name] = main[i]
    cycle = list(aux) + list(main)
    for i, name in enumerate(FIG_EXTRA_ROLES):
        out[name] = cycle[i % len(cycle)]
    return out


def apply_fig_palette(ns=None) -> dict | None:
    """按 FIG_PALETTE / FIG_COLORS_* 改写 ROC_COLOR_MAP 与 ROC_FALLBACK_COLORS。

    未设置调色板时保持模块默认（与旧图一致）。就地改 dict/list，已 import 的引用一并生效。
    """
    S = ns if ns is not None else _S
    pal_id = str(getattr(S, "FIG_PALETTE", "") or "").strip().lower()
    main_in = getattr(S, "FIG_COLORS_MAIN", None)
    aux_in = getattr(S, "FIG_COLORS_AUX", None)
    if not pal_id and not main_in and not aux_in:
        return None
    if pal_id in FIG_PALETTES and not main_in and not aux_in:
        main, aux = colors_from_palette(pal_id)
    else:
        main, aux = colors_from_palette(pal_id or DEFAULT_FIG_PALETTE, main_in, aux_in)
    new_map = map_from_palette(main, aux, pal_id)
    ROC_COLOR_MAP.clear()
    ROC_COLOR_MAP.update(new_map)
    if pal_id in ("", "npg"):
        ROC_FALLBACK_COLORS[:] = list(_LEGACY_FALLBACK)
    else:
        ROC_FALLBACK_COLORS[:] = list(main) + list(aux)
    try:
        from modules import config as cfg
        cfg.ROC_COLOR_MAP = ROC_COLOR_MAP
        cfg.ROC_FALLBACK_COLORS = ROC_FALLBACK_COLORS
    except Exception as e:
        print(f"  ⚠ apply_fig_palette cfg sync skipped: {e}", flush=True)
    return {"palette": pal_id or DEFAULT_FIG_PALETTE, "main": main, "aux": aux}


apply_fig_palette(_S)


def ensure_output_dirs(out_dir: str = None) -> None:
    """创建 ``{OUT_DIR}/`` 与 ``{OUT_DIR}/PNG/``。

    禁止落到工程根 ``.``：现行分析数字必须在终点夹（见 outcome.normalize_analysis_out_dir）。
    绝对路径（测试 monkeypatch）原样使用。
    """
    from modules.config.outcome import normalize_analysis_out_dir

    if out_dir is None:
        out_dir = getattr(_S, "OUT_DIR", "") or ""
    out_dir = str(out_dir or "").strip()
    if not out_dir or out_dir in (".", "./"):
        raise ValueError(
            "OUT_DIR 未解析，禁止写到工程根；请先 set_outcome，"
            "或设置 SUBPROJECT_BY_OUTCOME / 结局名作终点夹"
        )
    out_dir = normalize_analysis_out_dir(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "PNG"), exist_ok=True)


def figures_on() -> bool:
    """图与显示总闸。False 时不写 PNG、TIFF、PDF。

    读 cfg 上的当前值，这样测试里的 monkeypatch 能生效。
    """
    from modules import config as _cfg
    if hasattr(_cfg, "DO_FIGURE_PANELS"):
        return bool(getattr(_cfg, "DO_FIGURE_PANELS"))
    return bool(getattr(_S, "DO_FIGURE_PANELS", True))


def landmark_roc_on() -> bool:
    """是否跑 Landmark ROC。

    cfg 上已有 DO_LANDMARK_ROC 时只用它。
    属性不在时才回落旧键 DO_TIME_DEPENDENT_ROC（未合并默认的旧调用）。
    两键都在时，时间依赖性 ROC 不会把 Landmark 打开。
    """
    from modules import config as _cfg
    if hasattr(_cfg, "DO_LANDMARK_ROC"):
        return bool(getattr(_cfg, "DO_LANDMARK_ROC"))
    if hasattr(_cfg, "DO_TIME_DEPENDENT_ROC"):
        return bool(getattr(_cfg, "DO_TIME_DEPENDENT_ROC"))
    return bool(getattr(_S, "DO_TIME_DEPENDENT_ROC", False))


def ipcw_roc_on() -> bool:
    from modules import config as _cfg
    return bool(getattr(_cfg, "DO_TIME_DEPENDENT_ROC", False))


def want_tiff() -> bool:
    if hasattr(_S, "DO_PLOT_TIFF"):
        return bool(getattr(_S, "DO_PLOT_TIFF"))
    return bool(getattr(_S, "DO_PLOT", False))


def want_pdf() -> bool:
    if hasattr(_S, "DO_PLOT_PDF"):
        return bool(getattr(_S, "DO_PLOT_PDF"))
    return bool(getattr(_S, "DO_PLOT", False))


def save_plot(filename: str, fig, width: float, height: float,
              dpi: int = 150, out_dir: str = None, do_plot: bool = None,
              do_tiff: bool = None, do_pdf: bool = None) -> bool:
    """写出 PNG，并按开关另写 TIFF / PDF。返回是否写了 PNG。

    DO_FIGURE_PANELS 为 false 时三种格式都不写。
    """
    from modules.utils import dry_run as _dry
    if not figures_on():
        try:
            plt.close(fig)
        except Exception as e:
            print(f"  ⚠ close fig (figures off): {e}", flush=True)
        print(f"  · {filename}: DO_FIGURE_PANELS=false — 不写 PNG/TIFF/PDF", flush=True)
        return False
    if out_dir is None:
        out_dir = _S.OUT_DIR
    if do_tiff is None:
        do_tiff = bool(do_plot) if do_plot is not None else want_tiff()
    if do_pdf is None:
        do_pdf = bool(do_plot) if do_plot is not None else want_pdf()
    if _dry.skip_write(os.path.join(out_dir or ".", "PNG", f"{filename}.png"), what="save_plot"):
        try:
            plt.close(fig)
        except Exception as e:
            print(f"  ⚠ close fig after dry-run: {e}", flush=True)
        return False
    ensure_output_dirs(out_dir)
    fig.set_size_inches(width, height)
    png_path = os.path.join(out_dir, "PNG", f"{filename}.png")
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight")
    if do_tiff:
        tiff_path = os.path.join(out_dir, f"{filename}.tiff")
        try:
            fig.savefig(tiff_path, dpi=dpi, bbox_inches="tight", format="tiff")
        except Exception as e:
            try:
                fig.savefig(tiff_path, dpi=min(dpi, 100), bbox_inches="tight", format="tiff")
            except Exception as e2:
                print(f"  ⚠ TIFF skipped for {filename}: {e}; retry: {e2}")
    if do_pdf:
        pdf_path = os.path.join(out_dir, f"{filename}.pdf")
        try:
            fig.savefig(pdf_path, dpi=dpi, bbox_inches="tight", format="pdf")
        except Exception as e:
            print(f"  ⚠ PDF skipped for {filename}: {e}")
    plt.close(fig)
    return True


def apply_clean_theme(ax, grid: bool = True, legend: bool = True,
                      font_size: float = None) -> None:
    fs = float(_S.FIG_FONT_SIZE if font_size is None else font_size)
    apply_fig_font()
    ax.set_facecolor("white")
    if hasattr(ax, "figure") and ax.figure is not None:
        ax.figure.patch.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_visible(True)
        ax.spines[side].set_color("#222222")
        ax.spines[side].set_linewidth(1.0)
    ax.tick_params(colors="#222222", labelsize=fs, width=0.8, length=3.5)
    for lab in (ax.xaxis.label, ax.yaxis.label, ax.title):
        lab.set_fontsize(fs)
        lab.set_color("#111111")
    ax.title.set_fontweight("normal")
    for t in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
        t.set_fontsize(fs)
    if grid:
        ax.grid(True, linestyle="--", linewidth=0.6, color="#CCCCCC", alpha=0.85)
        ax.set_axisbelow(True)
    if legend and ax.get_legend() is not None:
        leg = ax.get_legend()
        leg.set_frame_on(True)
        frame = leg.get_frame()
        frame.set_facecolor("white")
        frame.set_edgecolor("#DDDDDD")
        frame.set_linewidth(0.8)
        frame.set_alpha(0.95)
        for t in leg.get_texts():
            t.set_fontsize(fs)
        if leg.get_title() is not None:
            leg.get_title().set_fontsize(fs)


def style_new_axes(fig=None, ax=None, figsize=(7, 5.5)):
    if fig is None or ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    return fig, ax
