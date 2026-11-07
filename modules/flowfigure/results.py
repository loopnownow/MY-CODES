# -*- coding: utf-8 -*-
"""Results HTML 的 Figure 1 区块：渲染 markup、幂等插入、原子写回。

与 ``modules.stats.report.figure1_section_html`` 共用同一个 helper，控制台
回填与 Results 生成不会漂移。
"""
from __future__ import annotations

from pathlib import Path

ANCHOR_TEXT = "A. All Data — Baseline"
SECTION_ID = "figure1-flow"


def section_html(out_dir) -> str:
    """Figure 1 区块 markup（force_img：保存前 PNG 已先落盘）。"""
    from modules.stats.report import figure1_section_html

    return figure1_section_html(out_dir, force_img=True)


def ensure_section(html_path) -> bool:
    """幂等插入 Figure 1 区块：已有 id 返回 False，插入并落盘返回 True。

    插入点：包含 ``A. All Data — Baseline`` 的 ``<section>`` 之前；
    找不到该段时插到 ``</body>`` 之前；两者都没有则 raise（不猜位置）。
    """
    path = Path(html_path)
    if not path.is_file():
        raise FileNotFoundError(f"Results HTML 不存在: {path}")
    text = path.read_text(encoding="utf-8")
    if f'id="{SECTION_ID}"' in text:
        return False
    markup = section_html(path.parent)
    idx = text.find(ANCHOR_TEXT)
    if idx >= 0:
        sec = text.rfind("<section", 0, idx)
        pos = sec if sec >= 0 else idx
    else:
        body = text.lower().rfind("</body>")
        if body < 0:
            raise ValueError(f"Results HTML 找不到 Figure 1 插入点: {path}")
        pos = body
    atomic_write_text(path, text[:pos] + markup + text[pos:])
    return True


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免写一半的源文件。"""
    import os

    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)
