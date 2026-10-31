"""控制台启动预热：后台把首屏要用的重库与第一个项目预览先准备好。

运行：由 console.cmd_serve 起后台线程调用（daemon），不单独执行。
字段：STATE["done"] 预热结束、STATE["error"] 失败栈、STATE["detail"] 结果摘要。
"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any

STATE: dict[str, Any] = {"done": False, "error": "", "detail": ""}


def run(project: str | None, data: str | None) -> None:
    try:
        import pandas  # noqa: F401  预览要 pandas
        import sklearn  # noqa: F401  预览链会拉 sklearn
        from modules.utils import u_qc  # noqa: F401  外部验证规格解析
        from modules.console.ops import project_settings
        from modules.console.preview import preview_workbook
        from modules.console.schema_ui import public_settings
        from modules.console.settings_layer import load_settings_module, project_dir

        detail = "imports ok"
        if project:
            proj = project_dir(str(project))
            settings = public_settings(load_settings_module(project_settings(proj)))
            name = str(data or "").strip() or f"{project}.xlsx"
            target = Path(name)
            if not target.is_absolute():
                target = proj / target
            if target.is_file():
                prev = preview_workbook(target, dict(settings))
                detail = f"preview {target.name}: {len(prev.get('sheets') or [])} sheets"
            else:
                detail = f"imports ok（数据未就绪: {target}）"
        STATE["detail"] = detail
    except Exception:
        STATE["error"] = traceback.format_exc(limit=3)
    finally:
        STATE["done"] = True
