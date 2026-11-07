# -*- coding: utf-8 -*-
"""Published STROBE Figure 1 — patient selection flowchart (type A).

兼容入口：``python draw_strobe_flow.py --json spec.json --out Figure1_flow.png``。
实际实现已收敛到 ``modules.flowfigure``（spec v2 / layout / render_mpl / cli）；
本脚本只做参数转发，强制 published-a 版式。

版式：目标宽 16.0 cm、Arial 12 pt、PNG 300 dpi + 矢量 PDF；n-audit fail-closed。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from modules.flowfigure import cli  # noqa: E402
from modules.flowfigure import spec as spec_mod  # noqa: E402
from modules.flowfigure.audit import AuditError  # noqa: E402

DESCRIPTION = "Draw published STROBE Figure 1 (patient selection; no pipeline row)"


def draw_strobe_flow(spec: dict, out_path, audit: bool = True) -> Path:
    """Programmatic entry（兼容）：强制 published-a 版式，失败抛异常且不落盘。"""
    return cli.render_spec(
        spec, out_path, audit=audit, expected_layout=spec_mod.LAYOUT_PUBLISHED,
    )


def main(argv=None) -> int:
    """CLI：--json / --out / --no-audit；返回进程退出码。"""
    return cli.main(
        argv, expected_layout=spec_mod.LAYOUT_PUBLISHED, description=DESCRIPTION,
    )


__all__ = ["AuditError", "draw_strobe_flow", "main"]


if __name__ == "__main__":
    sys.exit(main())
