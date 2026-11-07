"""Figure 1（研究流程图）单一源：读 / 校验 / 重画 / 回填 Results 页。

单一源 = ``<项目>/ref/pipeline/figure1_strobe.json``。重画用
``tools/draw-flowchart/draw_strobe_flow_expanded.py``（fail-closed n-audit）。
审计或渲染失败时不写任何文件。

运行: 由 modules.console.http_impl 的 /flow_figure、/api/flow_figure 调用，
不单独执行。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FIG1_SPEC_REL = "ref/pipeline/figure1_strobe.json"
FIG1_PNG_NAME = "figure1_flow.png"
FIG1_PDF_NAME = "figure1_flow.pdf"
FIG1_PNG_REL = f"PNG/{FIG1_PNG_NAME}"
FIG1_PDF_REL = f"PNG/{FIG1_PDF_NAME}"
TOOL_PATH = (
    Path(__file__).resolve().parents[2]
    / "tools" / "draw-flowchart" / "draw_strobe_flow_expanded.py"
)
ANCHOR_TEXT = "A. All Data — Baseline"


def spec_path(proj) -> Path:
    """项目内 Figure 1 spec 的绝对路径。"""
    return Path(proj) / FIG1_SPEC_REL


def load_figure1_spec(proj) -> dict:
    """读 Figure 1 spec；文件缺失抛 FileNotFoundError，JSON 损坏抛 ValueError。"""
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


def _figure1_markup(out_dir) -> str:
    """控制台回填用的 Results 区块 markup（与 report.py 共用同一个 helper）。"""
    from modules.stats.report import figure1_section_html

    return figure1_section_html(out_dir, force_img=True)


def ensure_figure1_section(html_path) -> bool:
    """幂等插入 Figure 1 区块：已有 id 返回 False，插入并落盘返回 True。

    插入点：包含 ``A. All Data — Baseline`` 的 ``<section>`` 之前；
    找不到该段时插到 ``</body>`` 之前；两者都没有则 raise（不猜位置）。
    """
    path = Path(html_path)
    if not path.is_file():
        raise FileNotFoundError(f"Results HTML 不存在: {path}")
    text = path.read_text(encoding="utf-8")
    if 'id="figure1-flow"' in text:
        return False
    markup = _figure1_markup(path.parent)
    idx = text.find(ANCHOR_TEXT)
    if idx >= 0:
        sec = text.rfind("<section", 0, idx)
        pos = sec if sec >= 0 else idx
    else:
        body = text.lower().rfind("</body>")
        if body < 0:
            raise ValueError(f"Results HTML 找不到 Figure 1 插入点: {path}")
        pos = body
    _atomic_write_text(path, text[:pos] + markup + text[pos:])
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免写一半的源文件。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _atomic_copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dst)


def _run_tool(spec_tmp: Path, out_tmp: Path) -> None:
    """跑绘图工具一次；returncode 非 0 或没有产物时抛 ValueError（不落盘）。"""
    env = dict(os.environ)
    env["MPLBACKEND"] = "Agg"
    cmd = [
        sys.executable, str(TOOL_PATH),
        "--json", str(spec_tmp), "--out", str(out_tmp),
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(TOOL_PATH.parent), capture_output=True, text=True,
            timeout=180, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Figure 1 渲染超时（180s）: {TOOL_PATH}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ValueError(
            f"Figure 1 渲染失败 (rc={proc.returncode}): {detail or '无错误输出'}"
        )
    if not out_tmp.is_file() or out_tmp.stat().st_size == 0:
        raise ValueError(f"Figure 1 渲染失败：未生成 {out_tmp.name}")


def render_figure1(proj, spec) -> dict:
    """校验 spec → 临时目录渲染 PNG+PDF → 写回单一源与各结果夹 → 回填 Results 页。

    渲染成功后才动项目文件：先原子写 spec，再把 PNG/PDF 复制到每个结果夹的
    PNG/，最后幂等插入 Figure 1 区块。审计/渲染失败时项目文件不变。
    """
    proj = Path(proj)
    if not isinstance(spec, dict) or not spec:
        raise ValueError("Figure 1 spec 必须是非空对象")
    if not TOOL_PATH.is_file():
        raise FileNotFoundError(f"缺少绘图工具: {TOOL_PATH}")

    with tempfile.TemporaryDirectory(prefix="figure1_flow_") as td:
        tmp = Path(td)
        spec_tmp = tmp / "spec.json"
        png_tmp = tmp / FIG1_PNG_NAME
        pdf_tmp = tmp / FIG1_PDF_NAME
        spec_tmp.write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        _run_tool(spec_tmp, png_tmp)
        _run_tool(spec_tmp, pdf_tmp)

        target = spec_path(proj)
        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(target, spec_tmp.read_text(encoding="utf-8"))

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
    }


def read_figure1_state(proj) -> dict:
    """GET 用：当前 spec + 渲染产物路径/是否存在。"""
    spec = load_figure1_spec(proj)
    png_rel = figure1_png_rel(proj)
    return {
        "spec": spec,
        "spec_rel": FIG1_SPEC_REL,
        "png_rel": png_rel,
        "png_exists": bool(png_rel) and (Path(proj) / png_rel).is_file(),
    }
