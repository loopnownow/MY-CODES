"""控制台设置 / 路径 / 会话层（B4c2：从 console.py 逐字搬出）。

依赖 const（常量）、schema / schema_ui（静态表与设置归一）、ops（叶层）与 paths；
不 import console，也不 import http_impl；console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from modules.console import paths as _paths
from modules.console.const import (
    DEFAULT_PORT,
    DEFAULT_PROJECT,
    EXTRA_PROJECT_ROOTS,
    GROK_ROOT,
    OVERRIDE_PATH,
    PORT_FILE,
    RAD_ROOT,
    TUPLE_LIST_KEYS,
)
from modules.console.ops import _overlay_path, project_settings, sync_external_tests
from modules.console.schema import _PICK_XLSX_SCRIPT, _RESULT_SKIP_DIRS
from modules.console.schema_ui import hydrate_console_settings

def project_roots() -> list[Path]:
    return _paths.collect_project_roots(RAD_ROOT, EXTRA_PROJECT_ROOTS)

def under_root(p: str | Path) -> Path:
    # Use live GROK_ROOT so tests can monkeypatch settings_layer.GROK_ROOT.
    return _paths.under_root(p, grok_root=GROK_ROOT)

def under_project(proj: Path, p: str | Path) -> Path:
    return _paths.under_project(proj, p, grok_root=GROK_ROOT)

def project_dir(name: str, explicit_dir: str = "") -> Path:
    return _paths.find_project_dir(
        name,
        grok_root=GROK_ROOT,
        project_roots=project_roots(),
        explicit_dir=explicit_dir,
    )

def load_settings_module(settings_path: Path):
    settings_path = under_root(settings_path)
    if settings_path.suffix.lower() == ".ini":
        from modules.config.ini_io import ensure_settings_ini, load_ini_namespace
        ensure_settings_ini(settings_path)
        return load_ini_namespace(settings_path)
    key = "radset_" + hashlib.md5(str(settings_path).encode("utf-8")).hexdigest()[:12]
    sys.modules.pop(key, None)
    spec = importlib.util.spec_from_file_location(key, settings_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {settings_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def normalize_overlay(ov: dict[str, Any]) -> dict[str, Any]:
    from modules.config.ini_io import migrate_retired_settings
    out = migrate_retired_settings(ov or {})
    for k in TUPLE_LIST_KEYS:
        if k in out and isinstance(out[k], list):
            out[k] = [tuple(x) if isinstance(x, list) else x for x in out[k]]
    return sync_external_tests(hydrate_console_settings(out))

def unwrap_overlay_file(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("overlay"), dict):
        raw = dict(obj["overlay"])
    else:
        raw = {k: v for k, v in obj.items()
               if k not in ("project", "data", "settings", "name", "saved_at")}
    return normalize_overlay(raw)

def save_session(payload: dict[str, Any]) -> None:
    OVERRIDE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

def load_session() -> dict[str, Any] | None:
    if not OVERRIDE_PATH.is_file():
        return None
    try:
        return json.loads(OVERRIDE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

def format_py_value(val: Any, indent: int = 0) -> str:
    pad = "    "
    if isinstance(val, bool):
        return "True" if val else "False"
    if val is None:
        return "None"
    if isinstance(val, int) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        return repr(val)
    if isinstance(val, str):
        return repr(val)
    if isinstance(val, tuple):
        if len(val) == 1:
            return f"({format_py_value(val[0])},)"
        return "(" + ", ".join(format_py_value(x) for x in val) + ")"
    if isinstance(val, list):
        if not val:
            return "[]"
        simple = all(
            isinstance(x, (str, int, float, bool, tuple)) or x is None
            for x in val
        )
        if simple and len(val) <= 4 and not any(isinstance(x, tuple) for x in val):
            return "[" + ", ".join(format_py_value(x) for x in val) + "]"
        inner = ",\n".join(pad + format_py_value(x) for x in val)
        return "[\n" + inner + ",\n]"
    if isinstance(val, dict):
        if not val:
            return "{}"
        parts = []
        for k, v in val.items():
            parts.append(f"{pad}{format_py_value(k)}: {format_py_value(v)}")
        return "{\n" + ",\n".join(parts) + ",\n}"
    return repr(val)

def writeback_settings(
    settings_path: Path,
    overlay: dict[str, Any],
    *,
    normalize: bool = True,
) -> dict[str, Any]:
    settings_path = under_root(settings_path)
    if "MODULES_DIR" in overlay:
        modules_dir = str(overlay.get("MODULES_DIR") or "").strip()
        if not modules_dir or not Path(modules_dir).is_dir():
            raise ValueError(f"MODULES_DIR 无效（未填写或不是现存目录）: {modules_dir!r}")
    if settings_path.suffix.lower() == ".ini":
        from modules.config.ini_io import ensure_settings_ini, read_ini, write_ini
        existed = settings_path.is_file()
        if not existed:
            ensure_settings_ini(settings_path)
        cur = read_ini(settings_path) if settings_path.is_file() else {}
        ov = normalize_overlay(overlay) if normalize else dict(overlay)
        updated, appended = [], []
        for k, v in ov.items():
            if not str(k).isidentifier() or str(k).startswith("_"):
                continue
            if k in cur:
                updated.append(k)
            else:
                appended.append(k)
            cur[k] = v
        bak = settings_path.with_suffix(settings_path.suffix + ".bak")
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            shutil.copy2(settings_path, bak)
            backup = str(bak)
        else:
            backup = ""
        write_ini(settings_path, cur)
        return {
            "updated": sorted(updated),
            "appended": appended,
            "backup": backup,
        }
    src = settings_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    ov = normalize_overlay(overlay) if normalize else dict(overlay)
    spans: list[tuple[int, int, str, str]] = []

    def _rhs_span(node: ast.AST) -> tuple[int, int] | None:
        if not hasattr(node, "lineno"):
            return None
        # replace from first '=' on start line through end_lineno
        return int(node.lineno), int(getattr(node, "end_lineno", node.lineno) or node.lineno)

    assigned: set[str] = set()
    for node in tree.body:
        key = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            key = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            t0 = node.targets[0]
            if isinstance(t0, ast.Name):
                key = t0.id
        if key is None or key not in ov or key.startswith("_"):
            continue
        span = _rhs_span(node)
        if span is None:
            continue
        assigned.add(key)
        spans.append((span[0], span[1], key, format_py_value(ov[key])))

    spans.sort(key=lambda x: x[0], reverse=True)
    for start, end, key, rhs in spans:
        i0, i1 = start - 1, end
        block = "".join(lines[i0:i1])
        if "=" not in block:
            continue
        left, _old = block.split("=", 1)
        new_block = left.rstrip() + " = " + rhs
        if not new_block.endswith("\n"):
            new_block += "\n"
        lines[i0:i1] = [new_block]

    missing = [k for k in ov if k not in assigned and not str(k).startswith("_")
               and str(k).isidentifier()]
    if missing:
        extra = ["\n", "# ── console.html 追加 ──\n"]
        for k in missing:
            extra.append(f"{k} = {format_py_value(ov[k])}\n")
        lines.extend(extra)

    bak = settings_path.with_suffix(settings_path.suffix + ".bak")
    shutil.copy2(settings_path, bak)
    settings_path.write_text("".join(lines), encoding="utf-8")
    return {
        "updated": sorted(assigned),
        "appended": missing,
        "backup": str(bak),
    }

def discover_projects() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    dirs: list[Path] = []
    for root in project_roots():
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir() or p.name.startswith((".", "0", "_")):
                continue
            if p.name in seen:
                continue
            dirs.append(p)
            seen.add(p.name)
    for p in dirs:
        settings = project_settings(p)
        xlsx = sorted(str(x) for x in p.glob("*.xlsx") if not x.name.startswith("~"))
        if not settings.is_file() and not xlsx:
            continue
        default_xlsx = ""
        try:
            mod = load_settings_module(settings)
            inp = str(getattr(mod, "INPUT", "") or "")
            if inp:
                cand = Path(inp)
                if not cand.is_absolute():
                    cand = p / inp
                if cand.is_file():
                    default_xlsx = str(cand.resolve())
        except Exception:
            default_xlsx = ""
        if not default_xlsx and xlsx:
            default_xlsx = xlsx[0]
        items.append({
            "name": p.name,
            "dir": str(p),
            "settings": str(settings),
            "xlsx": xlsx,
            "default_xlsx": default_xlsx,
        })
    items.sort(key=lambda x: (0 if x["name"] == DEFAULT_PROJECT else 1, x["name"]))
    return items

def read_project_overlay(proj: Path) -> dict[str, Any]:
    path = _overlay_path(proj)
    if not path.is_file():
        old = Path(proj) / ".rad_console_overlay.json"
        path = old if old.is_file() else path
    if not path.is_file():
        return {}
    try:
        return unwrap_overlay_file(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}

def find_results(project: str) -> list[dict[str, Any]]:
    from modules.config.outcome import is_results_html_name
    from modules.utils.u_qc import is_qc_html_name, relocate_legacy_qc_html
    proj = project_dir(project)
    relocate_legacy_qc_html(proj)
    hits = []
    for p in proj.rglob("*.html"):
        try:
            rel_parts = p.relative_to(proj).parts
        except ValueError:
            continue
        name_l = p.name.lower()
        in_ref_qc = (
            len(rel_parts) == 2
            and rel_parts[0].lower() == "ref"
            and is_qc_html_name(p.name)
        )
        if any(part in _RESULT_SKIP_DIRS for part in rel_parts[:-1]) and not in_ref_qc:
            continue
        if name_l in {
            f"{project.lower()}.html", "console.html", "flow_chart.html",
            "index.html", "project.html",
        }:
            continue
        is_qc = in_ref_qc
        is_res = is_results_html_name(p.name)
        if not (is_qc or is_res):
            continue
        rel = p.relative_to(proj).as_posix()
        hits.append({
            "name": p.name,
            "rel": rel,
            "kind": "qc" if is_qc else "results",
            "mtime": p.stat().st_mtime,
            "size": p.stat().st_size,
        })
    def _qc_rank(item: dict[str, Any]) -> tuple:
        if item["kind"] != "qc":
            return (1, 0, -item["mtime"])
        name = str(item.get("name") or "").lower()
        if name == "qc.html":
            return (0, 0, -item["mtime"])
        return (0, 1, -item["mtime"])
    hits.sort(key=_qc_rank)
    return hits[:80]

def open_result_folder(project: str, rel: str) -> dict[str, Any]:
    proj = project_dir(project)
    rel_s = str(rel or "").strip().replace("\\", "/")
    if not rel_s or rel_s.startswith("/") or ".." in Path(rel_s).parts:
        raise ValueError(f"结果相对路径无效: {rel!r}")
    target = under_project(proj, Path(rel_s))
    if target.is_file():
        folder = target.parent
    elif target.is_dir():
        folder = target
    else:
        raise FileNotFoundError(f"结果路径不存在: {target}")
    folder = under_project(proj, folder)
    if sys.platform == "win32":
        os.startfile(str(folder))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(folder)], check=True)
    else:
        subprocess.run(["xdg-open", str(folder)], check=True)
    return {"ok": True, "folder": str(folder), "rel": rel_s}

def pick_file_dialog(initial_dir: str, title: str = "选择 Excel") -> dict[str, Any]:
    raw_dir = str(initial_dir or "").strip()
    if not raw_dir:
        raise ValueError("缺少 dir：起始页须用 file:// 打开，才能定位 html 所在文件夹")
    start = Path(raw_dir).resolve()
    if not start.is_dir():
        raise FileNotFoundError(f"起始文件夹不存在: {start}")
    proc = subprocess.run(
        [sys.executable, "-c", _PICK_XLSX_SCRIPT, str(start), str(title or "选择 Excel")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"文件对话框失败: {proc.stderr.strip()}")
    picked_raw = proc.stdout.strip()
    if not picked_raw:
        return {"ok": True, "path": "", "rel": "", "dir": str(start)}
    picked = Path(picked_raw).resolve()
    if picked.is_relative_to(start):
        rel = picked.relative_to(start).as_posix()
    else:
        rel = str(picked)
    return {"ok": True, "path": str(picked), "rel": rel, "dir": str(start)}

def load_preferred_port() -> int:
    if PORT_FILE.is_file():
        try:
            obj = json.loads(PORT_FILE.read_text(encoding="utf-8"))
            n = int(obj.get("port") or DEFAULT_PORT)
            if 1024 <= n <= 65535:
                return n
        except Exception as e:
            print(f"  ⚠ console.port.json ignored: {e}", flush=True)
    return DEFAULT_PORT

def save_preferred_port(port: int) -> None:
    PORT_FILE.write_text(
        json.dumps({"port": int(port)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
