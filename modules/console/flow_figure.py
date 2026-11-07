# -*- coding: utf-8 -*-
"""Figure 1（研究流程图）单一源：读 / 校验 / 重画 / 回填 Results 页。

单一源 = ``<项目>/ref/pipeline/figure1_strobe.json``，canonical 结构由
``modules.flowfigure.spec`` 定义；渲染走 ``modules.flowfigure.layout`` +
``render_mpl``（进程内，不再起子进程）。审计/渲染失败时不写任何项目文件。

运行: 由 modules.console.http_impl 的 /flow_figure、/api/flow_figure 调用。
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

from modules.flowfigure import audit as flow_audit
from modules.flowfigure import layout as flow_layout
from modules.flowfigure import overrides as flow_overrides
from modules.flowfigure import render_mpl, results as flow_results, spec as flow_spec

FIG1_SPEC_REL = "ref/pipeline/figure1_strobe.json"
FIG1_PNG_NAME = "figure1_flow.png"
FIG1_PDF_NAME = "figure1_flow.pdf"
FIG1_PNG_REL = f"PNG/{FIG1_PNG_NAME}"
FIG1_PDF_REL = f"PNG/{FIG1_PDF_NAME}"
ANCHOR_TEXT = "A. All Data — Baseline"


def spec_path(proj) -> Path:
    """项目内 Figure 1 spec 的绝对路径。"""
    return Path(proj) / FIG1_SPEC_REL


def load_figure1_spec(proj) -> dict:
    """读 Figure 1 spec（原样）；文件缺失抛 FileNotFoundError，JSON 损坏抛 ValueError。"""
    path = spec_path(proj)
    if not path.is_file():
        raise FileNotFoundError(f"缺少 Figure 1 spec: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Figure 1 spec JSON 解析失败: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Figure 1 spec 必须是对象: {path}")
    return data


def _is_results_html(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith("results") and name.endswith(".html")


def results_dirs(proj) -> list[Path]:
    """含 Results*.html 的结果夹（递归，跳过 ref/ 与点目录）；第一个为主结果夹。"""
    base = Path(proj)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [
            n for n in dirnames
            if not n.startswith(".") and n.lower() != "ref"
        ]
        here = Path(dirpath)
        if any(_is_results_html(here / fn) for fn in filenames):
            found.append(here)
    found.sort(
        key=lambda p: (len(p.relative_to(base).parts), p.relative_to(base).as_posix().lower())
    )
    return found


def _dir_rel(proj, path: Path) -> str:
    rel = path.relative_to(Path(proj)).as_posix()
    return "" if rel == "." else rel


def _join_rel(base: str, tail: str) -> str:
    return tail if not base else f"{base}/{tail}"


def figure1_png_rel(proj) -> str:
    """主结果夹里的 Figure 1 PNG 相对路径（无结果夹时返回空串）。"""
    dirs = results_dirs(proj)
    if not dirs:
        return ""
    return _join_rel(_dir_rel(proj, dirs[0]), FIG1_PNG_REL)


def ensure_figure1_section(html_path) -> bool:
    """幂等插入 Figure 1 区块（实现见 ``modules.flowfigure.results``）。"""
    return flow_results.ensure_section(html_path)


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免写一半的源文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dst)


def prepare_geometry(spec: dict) -> tuple[dict, dict]:
    """normalize -> validate -> audit -> layout；返回 (canonical, geometry)。

    审计失败抛 AuditError（ValueError 子类），调用方不得落盘。
    """
    if not isinstance(spec, dict) or not spec:
        raise ValueError("Figure 1 spec 必须是非空对象")
    canonical = flow_spec.normalize(spec)
    flow_spec.validate(canonical)
    flow_audit.audit(canonical)
    geometry = flow_layout.build(canonical)
    geometry = flow_overrides.apply_overrides(geometry, canonical.get("overrides") or {})
    return canonical, geometry


def render_figure1(proj, spec) -> dict:
    """校验 spec → 临时目录渲染 PNG+PDF → 写回单一源与各结果夹 → 回填 Results 页。

    渲染成功后才动项目文件：先原子写 canonical spec，再把 PNG/PDF 复制到每个
    结果夹的 PNG/，最后幂等插入 Figure 1 区块。审计/渲染失败时项目文件不变。
    """
    proj = Path(proj)
    canonical, geometry = prepare_geometry(spec)
    with tempfile.TemporaryDirectory(prefix="figure1_flow_") as td:
        tmp = Path(td)
        png_tmp = tmp / FIG1_PNG_NAME
        pdf_tmp = tmp / FIG1_PDF_NAME
        render_mpl.render(geometry, png_tmp)
        render_mpl.render(geometry, pdf_tmp)

        target = spec_path(proj)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, flow_spec.to_json_text(canonical))

        dirs = results_dirs(proj)
        results: list[str] = []
        patched: list[str] = []
        for res_dir in dirs:
            rel = _dir_rel(proj, res_dir)
            results.append(rel)
            png_dir = res_dir / "PNG"
            png_dir.mkdir(parents=True, exist_ok=True)
            _atomic_copy(png_tmp, png_dir / FIG1_PNG_NAME)
            _atomic_copy(pdf_tmp, png_dir / FIG1_PDF_NAME)
            for html in sorted(res_dir.glob("Results*.html")):
                if ensure_figure1_section(html):
                    patched.append(html.relative_to(proj).as_posix())

    pdf_rel = ""
    if dirs:
        pdf_rel = _join_rel(_dir_rel(proj, dirs[0]), FIG1_PDF_REL)
    return {
        "spec_rel": FIG1_SPEC_REL,
        "png_rel": figure1_png_rel(proj),
        "pdf_rel": pdf_rel,
        "results": results,
        "patched": patched,
        "page_cm": geometry["page_cm"],
        "warnings": geometry["warnings"],
    }


def to_legacy_shape(canonical: dict) -> dict:
    """canonical v2 -> 旧 spec shape（只给 legacy.html 的旧 Figure 1 面板读）。"""
    arms = canonical.get("presplit") or []
    cohorts = canonical.get("cohorts") or []
    downsample = canonical.get("downsample") or []
    presplit = {
        "arm_" + str(index): {
            "label": arm.get("label") or ("Arm " + str(index + 1)),
            "n": arm.get("n"),
            "pp": arm.get("pp"),
            "non_pp": arm.get("non_pp"),
        }
        for index, arm in enumerate(arms)
    }
    return {
        "layout": canonical.get("layout"),
        "screened": str((canonical.get("screened") or {}).get("text") or ""),
        "inclusion": list((canonical.get("criteria") or {}).get("inclusion") or []),
        "presplit": presplit,
        "downsample": (
            {"n": downsample[0].get("n"), "reason": downsample[0].get("reason") or ""}
            if downsample else {}
        ),
        "analyzed": str((canonical.get("analyzed") or {}).get("text") or ""),
        "pos_name": canonical.get("pos_name") or "",
        "notes": canonical.get("notes") or "",
        "splits": [
            {
                "label": cohort.get("label") or ("Cohort " + str(index + 1)),
                "n": cohort.get("n"),
                "pp": cohort.get("pp"),
                "non_pp": cohort.get("non_pp"),
            }
            for index, cohort in enumerate(cohorts)
        ],
        "font_size": canonical.get("font_size"),
        "overrides": canonical.get("overrides") or {"boxes": {}, "edges": {}, "hidden": []},
    }


def read_figure1_state(proj) -> dict:
    """GET 用：canonical spec + geometry + 产物路径/是否存在。"""
    proj = Path(proj)
    canonical = flow_spec.normalize(load_figure1_spec(proj))
    geometry = flow_layout.build(canonical)
    geometry = flow_overrides.apply_overrides(geometry, canonical.get("overrides") or {})
    png_rel = figure1_png_rel(proj)
    return {
        "spec": canonical,
        "spec_rel": FIG1_SPEC_REL,
        "png_rel": png_rel,
        "png_exists": bool(png_rel) and (proj / png_rel).is_file(),
        "geometry": geometry,
        "warnings": geometry["warnings"],
        "page_cm": geometry["page_cm"],
    }


def preview_figure1(proj, spec: dict, overrides=None) -> dict:
    """POST /api/flow_preview 用：临时目录渲染，不写项目；审计失败返回结构化错误。

    返回 {'ok': True, 'png_base64', 'geometry', 'warnings', 'page_cm'}
    或 {'ok': False, 'error', 'problems'}。
    """
    del proj  # 预览不读写项目文件；project 仅用于路由校验
    try:
        canonical = flow_spec.normalize(spec)
        flow_spec.validate(canonical)
        flow_audit.audit(canonical)
        applied = canonical.get("overrides") if overrides is None else overrides
        geometry = flow_layout.build(canonical)
        geometry = flow_overrides.apply_overrides(geometry, applied or {})
        with tempfile.TemporaryDirectory(prefix="figure1_preview_") as td:
            png = Path(td) / FIG1_PNG_NAME
            render_mpl.render(geometry, png, quiet=True)
            encoded = base64.b64encode(png.read_bytes()).decode("ascii")
    except (ValueError, flow_audit.AuditError) as err:
        return {"ok": False, "error": str(err), "problems": [str(err)]}
    return {
        "ok": True,
        "png_base64": encoded,
        "geometry": geometry,
        "warnings": geometry["warnings"],
        "page_cm": geometry["page_cm"],
    }
