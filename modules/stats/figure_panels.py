"""
figure_panels.py — 将结局目录下单图自动拼接为多面板图

输出（图总闸 DO_FIGURE_PANELS 开时写 PNG；DO_PLOT_TIFF / DO_PLOT_PDF 时另写投稿格式）：
  panel_roc.png
  panel_dca.png
  panel_nomogram_calibration.png
  panel_shap.png
  panel_survival.png
  panel_mediation.png
"""
from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

from modules import config as cfg

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore


def _png_dir(out_dir: str) -> str:
    return os.path.join(out_dir, "PNG")


def _exists(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def _load(path: str) -> "Image.Image":
    im = Image.open(path).convert("RGBA")
    return im


def _resample():
    try:
        return Image.Resampling.LANCZOS
    except Exception:
        return getattr(Image, "LANCZOS", Image.BICUBIC)


def _resize_to_height(im: "Image.Image", target_h: int) -> "Image.Image":
    if im.height == target_h or target_h <= 0:
        return im
    w = max(1, int(round(im.width * (target_h / im.height))))
    return im.resize((w, target_h), _resample())


def _resize_to_width(im: "Image.Image", target_w: int) -> "Image.Image":
    if im.width == target_w or target_w <= 0:
        return im
    h = max(1, int(round(im.height * (target_w / im.width))))
    return im.resize((target_w, h), _resample())


def _trim_whitespace(im: "Image.Image", threshold: int = 250, pad: int = 8) -> "Image.Image":
    """裁掉近白边，保留 pad 像素，便于 A 与下方 BC 按内容两端对齐。"""
    if im is None:
        return im
    gray = im.convert("L")
    mask = gray.point(lambda p: 255 if p < int(threshold) else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return im
    l, t, r, b = bbox
    l = max(0, l - pad)
    t = max(0, t - pad)
    r = min(im.width, r + pad)
    b = min(im.height, b + pad)
    if r <= l or b <= t:
        return im
    return im.crop((l, t, r, b))


def _font(size: int = 28):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _norm_label_pos(pos: str) -> str:
    p = str(pos or "lower_left").strip().lower().replace("-", "_")
    if p in {"top_left", "upper_left"}:
        return "upper_left"
    return "lower_left"


def _draw_label(im: "Image.Image", text: str, pos: str = "lower_left") -> "Image.Image":
    """在角标位置画 A/B/C…（默认左下）。"""
    if not text:
        return im
    pos = _norm_label_pos(pos)
    out = im.copy()
    draw = ImageDraw.Draw(out)
    font = _font(max(22, im.height // 28))
    pad = max(8, im.height // 80)
    # text bbox
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = 20, 24
    if pos == "lower_left":
        x, y = pad, im.height - th - pad * 2
    elif pos == "upper_left":
        x, y = pad, pad
    else:
        x, y = pad, im.height - th - pad * 2
    # 半透明白底
    box = [x - 4, y - 2, x + tw + 8, y + th + 6]
    try:
        overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rectangle(box, fill=(255, 255, 255, 200))
        out = Image.alpha_composite(out, overlay)
        draw = ImageDraw.Draw(out)
    except Exception as e:
        print(f"  ⚠ panel label backdrop skipped: {e}", flush=True)
    draw.text((x, y), text, fill=(0, 0, 0, 255), font=font)
    return out


def _save_panel(im: "Image.Image", stem: str, out_dir: str) -> None:
    """写 PNG 到 PNG/；DO_PLOT_TIFF / DO_PLOT_PDF 时额外写到 OUT_DIR。总闸关则不写。"""
    from modules.utils import dry_run as _dry
    if not cfg.figures_on():
        print(f"  · {stem}: DO_FIGURE_PANELS=false — 不写 PNG/TIFF/PDF", flush=True)
        return
    dpi = int(getattr(cfg, "FIGURE_PANEL_DPI", 150) or 150)
    png_dir = _png_dir(out_dir)
    png_path = os.path.join(png_dir, f"{stem}.png")
    if _dry.skip_write(png_path, what="figure panel"):
        return
    os.makedirs(png_dir, exist_ok=True)
    rgb = im.convert("RGB")
    rgb.save(png_path, format="PNG", dpi=(dpi, dpi))
    print(f"  ✓ {stem}.png saved", flush=True)

    if cfg.want_tiff():
        try:
            tiff_path = os.path.join(out_dir, f"{stem}.tiff")
            rgb.save(tiff_path, format="TIFF", dpi=(dpi, dpi))
            print(f"  ✓ {stem}.tiff saved", flush=True)
        except Exception as e:
            print(f"  ⚠ {stem}.tiff failed: {e}", flush=True)
    if cfg.want_pdf():
        try:
            pdf_path = os.path.join(out_dir, f"{stem}.pdf")
            rgb.save(pdf_path, format="PDF", resolution=float(dpi))
            print(f"  ✓ {stem}.pdf saved", flush=True)
        except Exception as e:
            print(f"  ⚠ {stem}.pdf failed: {e}", flush=True)


def _labels_for(n: int) -> List[str]:
    if not bool(getattr(cfg, "FIGURE_PANEL_LABELS", True)):
        return [""] * n
    return [chr(ord("A") + i) for i in range(n)]


def _label_pos() -> str:
    return _norm_label_pos(getattr(cfg, "FIGURE_PANEL_LABEL_POS", "lower_left"))


def _expect_internal_test() -> bool:
    """训练比例为 1 且没有拆分列时，不把遗留的 *_test.png 拼进 panel。"""
    if str(getattr(cfg, "SPLIT_COL", "") or "").strip():
        return True
    try:
        ratio = float(getattr(cfg, "TRAIN_RATIO", 0.7) or 0.7)
    except (TypeError, ValueError):
        return True
    return ratio < 1.0


def stitch_row(
    paths: Sequence[str],
    out_stem: str,
    out_dir: str,
    labels: Optional[Sequence[str]] = None,
) -> bool:
    """1×n 横排拼接。"""
    if Image is None:
        print("  ⚠ Pillow 不可用，跳过拼板", flush=True)
        return False
    valid = [p for p in paths if _exists(p)]
    if len(valid) < 1:
        print(f"  ⚠ {out_stem}: no source images — skip", flush=True)
        return False
    gap = int(getattr(cfg, "FIGURE_PANEL_GAP_PX", 12) or 12)
    labs = list(labels) if labels is not None else _labels_for(len(valid))
    while len(labs) < len(valid):
        labs.append("")
    imgs = [_load(p) for p in valid]
    th = max(im.height for im in imgs)
    imgs = [_resize_to_height(im, th) for im in imgs]
    pos = _label_pos()
    imgs = [_draw_label(im, lab, pos) for im, lab in zip(imgs, labs)]
    total_w = sum(im.width for im in imgs) + gap * (len(imgs) - 1)
    canvas = Image.new("RGBA", (total_w, th), (255, 255, 255, 255))
    x = 0
    for im in imgs:
        canvas.paste(im, (x, 0), im)
        x += im.width + gap
    _save_panel(canvas, out_stem, out_dir)
    return True


def stitch_nomogram_cal(
    nomo_path: str,
    cal_tr_path: str,
    cal_te_path: str,
    out_dir: str,
    out_stem: str = "panel_nomogram_calibration",
) -> bool:
    """上行 nomogram A；下行 cal_train B | cal_test C。A 左右两端与 BC 外沿对齐。"""
    if Image is None:
        print("  ⚠ Pillow 不可用，跳过拼板", flush=True)
        return False
    if not any(_exists(p) for p in (nomo_path, cal_tr_path, cal_te_path)):
        print(f"  ⚠ {out_stem}: no source images — skip", flush=True)
        return False
    gap = int(getattr(cfg, "FIGURE_PANEL_GAP_PX", 12) or 12)
    labs = _labels_for(3)
    pos = _label_pos()

    nom = _trim_whitespace(_load(nomo_path)) if _exists(nomo_path) else None
    cals: List[Image.Image] = []
    if _exists(cal_tr_path):
        cals.append(_trim_whitespace(_load(cal_tr_path)))
    if _exists(cal_te_path):
        cals.append(_trim_whitespace(_load(cal_te_path)))
    if nom is None and not cals:
        return False

    if cals:
        th = max(im.height for im in cals)
        cals = [_resize_to_height(im, th) for im in cals]
        cal_w = sum(im.width for im in cals) + gap * max(len(cals) - 1, 0)
        cal_h = cals[0].height
    else:
        cal_w, cal_h = 0, 0

    if nom is not None and cal_w > 0:
        nom = _resize_to_width(nom, cal_w)
        if nom.width != cal_w:
            nom = nom.resize((cal_w, max(1, nom.height)), _resample())
        total_w = cal_w
    elif nom is not None:
        total_w = nom.width
    else:
        total_w = cal_w

    if nom is not None:
        nom = _draw_label(nom, labs[0], pos)
    cals = [
        _draw_label(im, labs[i + 1] if i + 1 < len(labs) else "")
        for i, im in enumerate(cals)
    ]

    row1_h = nom.height if nom is not None else 0
    cal_h = cals[0].height if cals else 0
    total_h = row1_h + (gap if row1_h and cal_h else 0) + cal_h
    canvas = Image.new("RGBA", (max(total_w, 1), max(total_h, 1)), (255, 255, 255, 255))
    y = 0
    if nom is not None:
        x0 = max(0, (total_w - nom.width) // 2) if nom.width != total_w else 0
        canvas.paste(nom, (x0, y), nom)
        y += nom.height + (gap if cal_h else 0)
    if cals:
        x = 0
        for im in cals:
            canvas.paste(im, (x, y), im)
            x += im.width + gap
    _save_panel(canvas, out_stem, out_dir)
    return True


def stitch_grid(
    paths: Sequence[str],
    out_stem: str,
    out_dir: str,
    ncols: int = 2,
    labels: Optional[Sequence[str]] = None,
) -> bool:
    """网格拼接，最多 ncols 列。"""
    if Image is None:
        print("  ⚠ Pillow 不可用，跳过拼板", flush=True)
        return False
    valid = [p for p in paths if _exists(p)]
    if not valid:
        print(f"  ⚠ {out_stem}: no source images — skip", flush=True)
        return False
    ncols = max(1, int(ncols))
    gap = int(getattr(cfg, "FIGURE_PANEL_GAP_PX", 12) or 12)
    labs = list(labels) if labels is not None else _labels_for(len(valid))
    while len(labs) < len(valid):
        labs.append("")
    pos = _label_pos()
    imgs = [_draw_label(_load(p), lab, pos) for p, lab in zip(valid, labs)]

    # 每行统一高度
    rows: List[List[Image.Image]] = []
    for i in range(0, len(imgs), ncols):
        chunk = imgs[i : i + ncols]
        th = max(im.height for im in chunk)
        chunk = [_resize_to_height(im, th) for im in chunk]
        rows.append(chunk)

    # 每列最大宽（全局）便于对齐
    col_w = [0] * ncols
    for chunk in rows:
        for j, im in enumerate(chunk):
            col_w[j] = max(col_w[j], im.width)

    row_imgs: List[Image.Image] = []
    for chunk in rows:
        # pad each cell to col width (center)
        cells = []
        for j, im in enumerate(chunk):
            if im.width < col_w[j]:
                cell = Image.new("RGBA", (col_w[j], im.height), (255, 255, 255, 255))
                cell.paste(im, ((col_w[j] - im.width) // 2, 0), im)
                cells.append(cell)
            else:
                cells.append(im)
        tw = sum(col_w[j] for j in range(len(cells))) + gap * (len(cells) - 1)
        th = cells[0].height
        row = Image.new("RGBA", (tw, th), (255, 255, 255, 255))
        x = 0
        for j, cell in enumerate(cells):
            row.paste(cell, (x, 0), cell)
            x += col_w[j] + gap
        row_imgs.append(row)

    total_w = max(r.width for r in row_imgs)
    total_h = sum(r.height for r in row_imgs) + gap * (len(row_imgs) - 1)
    canvas = Image.new("RGBA", (total_w, total_h), (255, 255, 255, 255))
    y = 0
    for r in row_imgs:
        x0 = max(0, (total_w - r.width) // 2)
        canvas.paste(r, (x0, y), r)
        y += r.height + gap
    _save_panel(canvas, out_stem, out_dir)
    return True


def _mediation_paths(png_dir: str) -> List[str]:
    if not os.path.isdir(png_dir):
        return []
    files = []
    for name in os.listdir(png_dir):
        m = re.match(r"mediation_M(\d+)\.png$", name, re.I)
        if m:
            files.append((int(m.group(1)), os.path.join(png_dir, name)))
    files.sort(key=lambda t: t[0])
    return [p for _, p in files]


def stitch_external_panels(
    out_dir: str | None = None,
    tokens: Optional[Sequence[str]] = None,
) -> None:
    """多条外验：ROC / 校准 / DCA 按名称横拼，标 A、B、C…。一条不拼。"""
    out_dir = out_dir or cfg.OUT_DIR
    png = _png_dir(out_dir)
    if Image is None or not os.path.isdir(png):
        return
    found: list[str] = []
    if tokens:
        found = [str(t) for t in tokens if t]
    else:
        seen: set[str] = set()
        for name in os.listdir(png):
            m = re.match(r"(?:roc|calibration|dca)_ext_(.+)\.png$", name, re.I)
            if not m:
                continue
            tok = m.group(1)
            if tok not in seen:
                seen.add(tok)
                found.append(tok)
    if len(found) < 2:
        return
    kinds = (
        ("roc", "panel_roc_ext"),
        ("calibration", "panel_cal_ext"),
        ("dca", "panel_dca_ext"),
    )
    if not bool(getattr(cfg, "DO_ROC", True)):
        kinds = tuple(item for item in kinds if item[0] != "roc")
    if not bool(getattr(cfg, "DO_DCA", True)):
        kinds = tuple(item for item in kinds if item[0] != "dca")
    for kind, stem_prefix in kinds:
        stitch_row(
            [os.path.join(png, f"{kind}_ext_{t}.png") for t in found],
            stem_prefix,
            out_dir,
        )


def _time_token_key(token: str):
    try:
        return (0, float(token))
    except (TypeError, ValueError):
        return (1, str(token).lower())


def stitch_train_test_roc(prefix: str, panel_prefix: str, out_dir: str) -> None:
    """每个时间点把 train、test 两张 ROC 左右拼成一张，标 A、B。"""
    png = _png_dir(out_dir)
    if Image is None or not os.path.isdir(png):
        return
    pat = re.compile(rf"^{re.escape(prefix)}(.+)_(train|test)\.png$", re.I)
    tokens: list[str] = []
    for name in os.listdir(png):
        m = pat.match(name)
        if m and m.group(1) not in tokens:
            tokens.append(m.group(1))
    for token in sorted(tokens, key=_time_token_key):
        stitch_row(
            [
                os.path.join(png, f"{prefix}{token}_train.png"),
                os.path.join(png, f"{prefix}{token}_test.png"),
            ],
            f"{panel_prefix}{token}",
            out_dir,
        )


def stitch_subgroup_train_test(out_dir: str | None = None) -> None:
    """每个亚组：ROC / 校准 / DCA 的 train | test 横拼，标 A、B。"""
    out_dir = out_dir or cfg.OUT_DIR
    png = _png_dir(out_dir)
    if Image is None or not os.path.isdir(png):
        return
    tokens: set[str] = set()
    for name in os.listdir(png):
        m = re.match(r"(?:roc|calibration|dca)_sg_(.+)_train\.png$", name, re.I)
        if m:
            tokens.add(m.group(1))
        m = re.match(r"(?:roc|calibration|dca)_sg_(.+)_test\.png$", name, re.I)
        if m:
            tokens.add(m.group(1))
    kinds = (
        ("roc", "panel_roc_sg"),
        ("calibration", "panel_cal_sg"),
        ("dca", "panel_dca_sg"),
    )
    if not bool(getattr(cfg, "DO_ROC", True)):
        kinds = tuple(item for item in kinds if item[0] != "roc")
    if not bool(getattr(cfg, "DO_DCA", True)):
        kinds = tuple(item for item in kinds if item[0] != "dca")
    for token in sorted(tokens):
        for kind, stem_prefix in kinds:
            stitch_row(
                [
                    os.path.join(png, f"{kind}_sg_{token}_train.png"),
                    os.path.join(png, f"{kind}_sg_{token}_test.png"),
                ],
                f"{stem_prefix}_{token}",
                out_dir,
            )


def build_outcome_panels(out_dir: str | None = None) -> None:
    """对单个结局 OUT_DIR 生成全部 panel_*。图总闸关则跳过。"""
    if not cfg.figures_on():
        print("  DO_FIGURE_PANELS=false — skip panels", flush=True)
        return
    out_dir = out_dir or cfg.OUT_DIR
    png = _png_dir(out_dir)
    print("\n═══ FIGURE PANELS ═══", flush=True)
    if Image is None:
        print("  ⚠ Pillow not installed — skip figure panels", flush=True)
        return
    if not os.path.isdir(png):
        print(f"  ⚠ PNG dir missing: {png}", flush=True)
        return

    def p(name: str) -> str:
        return os.path.join(png, name)

    if bool(getattr(cfg, "DO_ROC", True)):
        stitch_row(
            [p("roc_train.png"), p("roc_test.png")],
            "panel_roc", out_dir,
        )
    else:
        print("  ⚠ panel_roc: DO_ROC off — skip", flush=True)
    if cfg.landmark_roc_on():
        stitch_train_test_roc("landmark_roc_", "panel_landmark_roc_", out_dir)
    else:
        print("  ⚠ panel_landmark_roc: DO_LANDMARK_ROC off — skip", flush=True)
    if cfg.ipcw_roc_on():
        stitch_train_test_roc("ipcw_roc_", "panel_ipcw_roc_", out_dir)
    else:
        print("  ⚠ panel_ipcw_roc: DO_TIME_DEPENDENT_ROC off — skip", flush=True)
    if bool(getattr(cfg, "DO_DCA", True)):
        dca_paths = [p("dca_train.png")]
        if _expect_internal_test():
            dca_paths.append(p("dca_test.png"))
        stitch_row(dca_paths, "panel_dca", out_dir)
    else:
        print("  ⚠ panel_dca: DO_DCA off — skip", flush=True)
    do_nomo = bool(getattr(cfg, "DO_NOMOGRAM", False))
    do_cal = bool(getattr(cfg, "DO_CALIBRATION", False))
    if do_nomo or do_cal:
        stitch_nomogram_cal(
            p("nomogram.png") if do_nomo else "",
            p("calibration_train.png") if do_cal else "",
            p("calibration_test.png") if do_cal else "",
            out_dir,
        )
    else:
        print("  ⚠ panel_nomogram_calibration: DO_NOMOGRAM and DO_CALIBRATION off — skip", flush=True)
    if bool(getattr(cfg, "DO_SHAP", False)):
        stitch_row(
            [p("shap_bar.png"), p("shap_summary.png")],
            "panel_shap", out_dir,
        )
    else:
        print("  ⚠ panel_shap: DO_SHAP off — skip", flush=True)
    km_pair = [p("km_overall.png"), p("km_by_combined_risk.png")]
    if sum(1 for x in km_pair if _exists(x)) >= 2:
        stitch_row(km_pair, "panel_survival", out_dir)
    else:
        print("  ⚠ panel_survival: need km_overall + km_by_combined_risk — skip", flush=True)
    med_paths = _mediation_paths(png)
    ncols = int(getattr(cfg, "FIGURE_PANEL_MEDIATION_NCOLS", 2) or 2)
    if med_paths:
        stitch_grid(med_paths, "panel_mediation", out_dir, ncols=ncols)
    else:
        print("  ⚠ panel_mediation: no mediation_M*.png — skip", flush=True)
    stitch_subgroup_train_test(out_dir)
    stitch_external_panels(out_dir)
    print("  FIGURE PANELS done", flush=True)
