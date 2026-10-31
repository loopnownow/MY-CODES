"""
ops.py — 控制台叶层操作（无 console 可变状态）。

由 console.py 逐字搬出：不读 `_RUN` / `_RUN_LOCK` / `GROK_ROOT` 等运行期可变名，
因此 http_impl.py 与 console.py 都能静态 import，无需 globals() 注入。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.console import paths as _paths


def allow_browser_origin(origin: str) -> bool:
    return _paths.allow_browser_origin(origin)


def cors_allow_origin(origin: str, *, allow_null: bool = False) -> str | None:
    return _paths.cors_allow_origin(origin, allow_null=allow_null)


def project_settings(proj: Path) -> Path:
    from modules.config.ini_io import resolve_settings_ini
    ini = resolve_settings_ini(proj)
    if ini.is_file():
        return ini
    py = proj / "config" / "settings.py"
    if py.is_file():
        return py
    return ini


def jsonable(v: Any) -> Any:
    if isinstance(v, tuple):
        return [jsonable(x) for x in v]
    if isinstance(v, list):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)


def sync_external_tests(settings: dict[str, Any]) -> dict[str, Any]:
    """EXTERNAL_TESTS 列表优先；否则三格合成一条。全空=不做外验。"""
    raw = settings.get("EXTERNAL_TESTS")
    norm: list[dict[str, str]] = []
    if isinstance(raw, list):
        for i, spec in enumerate(raw):
            if not isinstance(spec, dict):
                continue
            cs = str(spec.get("clin_sheet") or "").strip()
            rs = str(spec.get("rad_sheet") or "").strip()
            f = str(spec.get("file") or "").strip()
            if not f or not cs or not rs:
                continue
            name = str(spec.get("name") or "").strip() or (f"ext{i + 1}" if i else "ext")
            item = {
                "name": name,
                "file": f,
                "clin_sheet": cs,
                "rad_sheet": rs,
            }
            rid = str(spec.get("rad_id_col") or "").strip()
            if rid:
                item["rad_id_col"] = rid
            norm.append(item)
    if norm:
        settings["EXTERNAL_TESTS"] = norm
        s0 = norm[0]
        settings["EXTERNAL_FILE"] = s0["file"]
        settings["EXTERNAL_CLIN_SHEET"] = s0["clin_sheet"]
        settings["EXTERNAL_RAD_SHEET"] = s0["rad_sheet"]
        return settings
    has_ui = any(k in settings for k in (
        "EXTERNAL_FILE", "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
    ))
    if not has_ui:
        return settings
    f = str(settings.get("EXTERNAL_FILE") or "").strip()
    cs = str(settings.get("EXTERNAL_CLIN_SHEET") or "").strip()
    rs = str(settings.get("EXTERNAL_RAD_SHEET") or "").strip()
    if f and cs and rs:
        settings["EXTERNAL_TESTS"] = [{
            "name": "ext",
            "file": f,
            "clin_sheet": cs,
            "rad_sheet": rs,
        }]
    else:
        settings["EXTERNAL_TESTS"] = []
    return settings


def resolve_meta_xlsx(proj: Path, file_s: str, data_path: Path | None = None) -> Path:
    raw = str(file_s or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = proj / raw
        return p
    if data_path is not None:
        return data_path
    return proj / "false_classification.xlsx"


def resolve_run_outcome(body: dict[str, Any], overlay: dict[str, Any]) -> str:
    """子结局页只跑这一项；公共页空字符串=全部 OUTCOME_COLS。"""
    raw = str((body or {}).get("run_outcome") or "").strip()
    if not raw:
        return ""
    cols = [str(x).strip() for x in ((overlay or {}).get("OUTCOME_COLS") or []) if str(x).strip()]
    if cols and raw not in cols:
        raise ValueError(f"run_outcome {raw!r} 不在 OUTCOME_COLS {cols}")
    return raw


def _overlay_path(proj: Path) -> Path:
    from modules.config.ini_io import project_ref_dir
    return project_ref_dir(proj) / ".rad_console_overlay.json"


def write_project_overlay(proj: Path, payload: dict[str, Any]) -> Path:
    path = _overlay_path(proj)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def clear_project_overlay(proj: Path) -> bool:
    path = _overlay_path(proj)
    old = proj / ".rad_console_overlay.json"
    gone = False
    for p in (path, old):
        if p.is_file():
            p.unlink()
            gone = True
    return gone
