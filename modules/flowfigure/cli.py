# -*- coding: utf-8 -*-
"""Figure 1 命令行入口：``python -m modules.flowfigure.cli --json spec.json --out out.png``。

``--json`` / ``--out`` / ``--no-audit`` 与旧 tools/draw-flowchart 脚本兼容；
两种版式由 spec.layout 决定，``expected_layout`` 供旧脚本强制检查。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from modules.flowfigure import audit as audit_mod
from modules.flowfigure import layout as layout_mod
from modules.flowfigure import render_mpl
from modules.flowfigure import spec as spec_mod

DEFAULT_DESCRIPTION = "Draw the Figure 1 STROBE flowchart from a canonical/legacy spec JSON"


def render_spec(spec: dict, out_path, audit: bool = True, expected_layout: str | None = None) -> Path:
    """normalize -> validate -> audit -> layout -> render；失败不写文件。"""
    canonical = spec_mod.normalize(spec)
    if expected_layout is not None and canonical["layout"] != expected_layout:
        raise ValueError(
            f"spec.layout={canonical['layout']!r}，此处只接受 {expected_layout!r}"
        )
    spec_mod.validate(canonical)
    if audit:
        audit_mod.audit(canonical)
    geometry = layout_mod.build(canonical)
    return render_mpl.render(geometry, out_path)


def main(argv=None, expected_layout: str | None = None,
         description: str = DEFAULT_DESCRIPTION) -> int:
    """CLI：返回进程退出码；审计/校验失败打印原文并返回 1。"""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--json", required=True, help="Path to spec JSON")
    parser.add_argument("--out", required=True, help="Output PNG (300 dpi) or PDF (vector)")
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip n-audit. Only for documented historical figures whose printed n do not add up.",
    )
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.json).read_text(encoding="utf-8"))
    try:
        out = render_spec(
            spec, args.out, audit=not args.no_audit, expected_layout=expected_layout,
        )
    except (audit_mod.AuditError, ValueError) as err:
        print(err, file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
