#!/usr/bin/env python3
"""
sync_modules.py — 各项目只留 settings.py + console，算法一律用 modules。

用法:
  python 0doc/_code/02-data-processing/_shared/sync/sync_modules.py            # dry-run (all stage roots; ZERAD_ROOT/ORAD_ROOT = one root)
  python 0doc/_code/02-data-processing/_shared/sync/sync_modules.py --apply
  python 0doc/_code/02-data-processing/_shared/sync/sync_modules.py --check
  python 0doc/_code/02-data-processing/_shared/sync/sync_modules.py --keys
  python 0doc/_code/02-data-processing/_shared/sync/sync_modules.py --check
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import shutil
import sys
import time
import os
from pathlib import Path

# ── CONFIG ─────────────────────────────────────────────────────
# 算法库根（2026-10-25）：独立为 GitHub 主库（运行时 + 本地 git 同源），不由 __file__ 反推
STATS_ROOT = Path(os.environ.get("STATS_ROOT") or Path(__file__).resolve().parents[2])  # 仓根（本脚本在 tools/sync/ 下）
if str(STATS_ROOT) not in sys.path:
    sys.path.insert(0, str(STATS_ROOT))
from modules.console.paths import grok_root  # GROK_ROOT env -> frozen lab default (C1)
GROK_ROOT = grok_root(STATS_ROOT / "modules")  # 阶段根父目录；不由 STATS_ROOT 反推
# Projects live in stage folders under GROK_ROOT (same names as modules/console/paths.py).
STAGE_NAMES = (
    "drafting", "pre-submit", "in-review", "accepted",
)
_rad = os.environ.get("ZERAD_ROOT") or os.environ.get("ORAD_ROOT")
RAD_ROOTS = [Path(_rad)] if _rad else [GROK_ROOT / n for n in STAGE_NAMES if (GROK_ROOT / n).is_dir()]
if not RAD_ROOTS:
    raise SystemExit(f"未找到任何阶段目录（{', '.join(STAGE_NAMES)}）于 {GROK_ROOT}")
if str(STATS_ROOT) not in sys.path:
    sys.path.insert(0, str(STATS_ROOT))
from modules.config.ini_io import read_ini, write_ini  # noqa: E402

GOLD = STATS_ROOT / "modules"
SHIM = Path(__file__).resolve().parent / "project-shim"  # 随本脚本一起搬
BAK_ROOT = Path(os.environ.get("DEL_ROOT") or r"D:\0del") / "harvested" / "modules_bak"  # t1490u
# 2026-10-25：_code 副本已退役（指针见 02-data-processing\stats-analysis\README.md），
# 不再有 GOLD↔MASTER 双份；GOLD 即主库 modules 本身。

_MODULES_DIR_ASSIGN = (
    'MODULES_DIR = "'
    + str(GOLD).replace("\\", "\\\\")
    + '"'
)
SHARED_KEY_LINES = [
    ("MODULES_DIR", _MODULES_DIR_ASSIGN),
    ("DO_SURVIVAL", "DO_SURVIVAL: bool = False"),
    ("SURVIVAL_TIME_COL", "SURVIVAL_TIME_COL: str = ''"),
    ("READER_ALL_IN_ROC", "READER_ALL_IN_ROC: bool = False"),
    ("NRI_OLD_COL", "NRI_OLD_COL: str = ''"),
    ("NRI_NEW_COL", "NRI_NEW_COL: str = ''"),
    ("GROUP_KEEP", "GROUP_KEEP: List = []"),
    ("PAIRWISE_GROUPS", "PAIRWISE_GROUPS: List = []"),
    ("PAIRWISE_ALL", "PAIRWISE_ALL: bool = False"),
    ("GROUP_FROM_ID", "GROUP_FROM_ID: bool = False"),
    ("GROUP_FROM_ID_SEP", "GROUP_FROM_ID_SEP: str = '_'"),
]
NEW_SETTINGS_DOC = (
    '"""项目配置（只改本文件）。\n'
    "数据 / 结局 / 开关见下方各节。\n"
    "运行: <工程名>.html 开关 或  python -m modules.pipeline\n"
    "配置: config/settings.py\n"
    f"算法: {GOLD}\n"
    '"""'
)
READER_ALL_IN_ROC_PROJECTS = {"lya_WML", "ly_SCH_HE"}

# 项目 modules 下这些是共享代码副本，应删除
STRAY_FILES = (
    "pipeline.py",
    "config/style.py",
)
STRAY_DIRS = ("stats", "utils")


def _stash(path: Path, bak: Path, proj: Path) -> None:
    """Never delete: move path into bak (under 0del), keeping its project-relative path."""
    dest = bak / path.relative_to(proj)
    k = 1
    while dest.exists():
        dest = dest.with_name(f"{path.name}.{k}")
        k += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))


def merge_root_png(proj: Path, src: Path, bak: Path) -> None:
    """Move proj/PNG[/endpoint]/Figure1_* into <subdir>/PNG/. Replaced files and the emptied src go to bak."""
    if not src.is_dir():
        return
    for f in list(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        if len(rel.parts) == 1:
            dest = proj / "PNG" / rel.name
        else:
            ep = rel.parts[0]
            name = rel.name
            preferred = proj / "all" / ep / "PNG" / name
            direct = proj / ep / "PNG" / name
            dest = preferred if preferred.parent.is_dir() else direct
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            _stash(dest, bak, proj)
        shutil.move(str(f), str(dest))
    if src.exists():
        _stash(src, bak, proj)


def settings_path(proj: Path) -> Path:
    for ini in (proj / "ref" / "settings.ini", proj / "settings.ini"):
        if ini.is_file():
            return ini
    for cand in (proj / "config" / "settings.py", proj / "modules" / "config" / "settings.py"):
        if cand.is_file():
            return cand
    return ini


def discover_projects() -> list[Path]:
    out = []
    for root in RAD_ROOTS:
        for p in sorted(root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir() or p.name.startswith((".", "0")):
                continue
            if settings_path(p).is_file():
                out.append(p)
    return out


def assigned_names(settings_path: Path) -> set[str]:
    src = settings_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def extra_key_lines(project: str, have: set[str]) -> list[str]:
    lines = []
    for key, line in SHARED_KEY_LINES:
        if key in have:
            continue
        if key == "READER_ALL_IN_ROC" and project in READER_ALL_IN_ROC_PROJECTS:
            line = "READER_ALL_IN_ROC: bool = True"
        lines.append(line)
    return lines


def gold_console_html() -> str:
    """起始页金版：MY-CODES/modules/console/project.html（必需）。无课题名占位，拷到任意子工程即可。"""
    gold = GOLD / "console" / "project.html"
    if not gold.is_file():
        raise FileNotFoundError(f"缺少起始页金版: {gold}")
    return gold.read_text(encoding="utf-8")


def public_from_py(path: Path) -> dict:
    import importlib.util
    import inspect
    import types

    spec = importlib.util.spec_from_file_location("_rad_set", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    skip = {"os", "copy", "List", "Optional", "Dict", "Any", "annotations", "PNG_DIR"}
    out = {}
    for k, v in vars(mod).items():
        if k.startswith("_") or k in skip:
            continue
        if inspect.isfunction(v) or inspect.ismodule(v) or inspect.isclass(v):
            continue
        if isinstance(v, types.ModuleType):
            continue
        out[k] = v
    return out


def plan(proj: Path) -> list[dict]:
    rows = []
    cons_html = gold_console_html()

    def want(path: Path, content: str, kind: str) -> None:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            action = "same"
        elif path.is_file():
            action = "overwrite"
        else:
            action = "create"
        rows.append({"kind": kind, "path": path, "content": content, "action": action})

    py_set = proj / "config" / "settings.py"
    ini = proj / "ref" / "settings.ini"
    if py_set.is_file():
        rows.append({
            "kind": "to_ini", "path": ini, "src": py_set,
            "content": None, "action": "to_ini",
        })
    if (proj / "config").is_dir():
        rows.append({
            "kind": "remove_dir", "path": proj / "config",
            "content": None, "action": "remove_dir",
        })
    want(proj / f"{proj.name}.html", cons_html, "console")
    old_cons = proj / "console.html"
    if old_cons.is_file():
        rows.append({"kind": "remove", "path": old_cons, "content": None, "action": "remove"})
    fc = proj / "flow_chart.html"
    if fc.is_file():
        rows.append({"kind": "remove", "path": fc, "content": None, "action": "remove"})
    if (proj / "run.bat").is_file():
        rows.append({"kind": "remove", "path": proj / "run.bat", "content": None, "action": "remove"})
    pbg = proj / "PBG"
    if pbg.is_dir():
        rows.append({
            "kind": "rename_dir", "path": proj / "PNG", "src": pbg,
            "content": None, "action": "rename_dir",
        })
    for leftover in (
        proj / "console.bat",
        proj / "打开流程图工具.bat",
    ):
        if leftover.is_file():
            rows.append({"kind": "remove", "path": leftover, "content": None, "action": "remove"})
    strobe_dir = proj / "strobe-flowchart"
    if strobe_dir.is_dir():
        rows.append({"kind": "remove_dir", "path": strobe_dir, "content": None, "action": "remove_dir"})
    if (proj / "modules").is_dir():
        rows.append({"kind": "remove_dir", "path": proj / "modules", "content": None, "action": "remove_dir"})
    return rows


def strip_dead_set_outcome(settings_path: Path) -> list[str]:
    """删掉 settings.py 里不再使用的 set_outcome / _replace_group_token。"""
    src = settings_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    drop: list[tuple[int, int, str]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in (
            "set_outcome", "_replace_group_token",
        ):
            start = node.lineno - 1
            end = int(getattr(node, "end_lineno", node.lineno) or node.lineno)
            drop.append((start, end, node.name))
    if not drop:
        return []
    for start, end, _name in sorted(drop, reverse=True):
        # also drop one leading blank if present
        while start > 0 and lines[start - 1].strip() == "":
            start -= 1
        del lines[start:end]
    settings_path.write_text("".join(lines), encoding="utf-8")
    return [n for _a, _b, n in drop]


def rewrite_settings_header(settings_path: Path) -> bool:
    src = settings_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    if not tree.body or not isinstance(tree.body[0], ast.Expr):
        return False
    doc = tree.body[0]
    if not isinstance(getattr(doc, "value", None), ast.Constant):
        return False
    if not isinstance(doc.value.value, str):
        return False
    if doc.value.value.startswith("项目配置（只改本文件）"):
        return False
    lines = src.splitlines(keepends=True)
    start, end = doc.lineno - 1, int(doc.end_lineno or doc.lineno)
    new = NEW_SETTINGS_DOC
    if not new.endswith("\n"):
        new += "\n"
    lines[start:end] = [new]
    settings_path.write_text("".join(lines), encoding="utf-8")
    return True


def apply_keys(dry: bool) -> list[str]:
    log = []
    for proj in discover_projects():
        sp = settings_path(proj)
        if sp.suffix.lower() == ".ini":
            have = set(read_ini(sp))
        else:
            have = assigned_names(sp)
        extra = extra_key_lines(proj.name, have)
        if not extra:
            continue
        log.append(f"{proj.name}: +{len(extra)}")
        if not dry:
            if sp.suffix.lower() == ".ini":
                data = read_ini(sp)
                for key, line in SHARED_KEY_LINES:
                    if key in data:
                        continue
                    if key == "READER_ALL_IN_ROC" and proj.name in READER_ALL_IN_ROC_PROJECTS:
                        data[key] = True
                    else:
                        data[key] = ast.literal_eval(line.split("=", 1)[1].strip())
                write_ini(sp, data)
            else:
                block = (
                    "\n\n# ── modules 共享默认（sync_modules 追加缺失键，不改已有赋值）──\n"
                    + "\n".join(extra)
                    + "\n"
                )
                with sp.open("a", encoding="utf-8") as f:
                    f.write(block)
    return log


def apply_rows(all_rows: list[tuple[Path, list[dict]]], stamp: str) -> int:
    """Never delete: replaced or removed items are moved into BAK_ROOT (under 0del)."""
    n = 0
    for proj, rows in all_rows:
        bak = BAK_ROOT / f"{proj.name}_{stamp}"
        for r in rows:
            if r["action"] == "same":
                continue
            path: Path = r["path"]
            kind = r.get("kind") or r["action"]
            if r["action"] in ("create", "overwrite"):
                if path.is_file():
                    dest = bak / path.relative_to(proj)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(r["content"], encoding="utf-8", newline="\n")
                n += 1
            elif r["action"] == "move":
                src: Path = r["src"]
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.is_file():
                    _stash(path, bak, proj)
                shutil.move(str(src), str(path))
                n += 1
            elif r["action"] == "to_ini":
                src = r["src"]
                dest = bak / src.relative_to(proj)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                write_ini(path, public_from_py(src))
                n += 1
            elif r["action"] == "merge_png":
                src = r["src"]
                if src.is_dir():
                    merge_root_png(proj, src, bak)
                    n += 1
            elif r["action"] == "rename_dir":
                src = r["src"]
                if not src.is_dir():
                    continue
                if path.exists():
                    for f in list(src.rglob("*")):
                        if not f.is_file():
                            continue
                        t = path / f.relative_to(src)
                        t.parent.mkdir(parents=True, exist_ok=True)
                        if t.exists():
                            _stash(t, bak, proj)
                        shutil.move(str(f), str(t))
                    _stash(src, bak, proj)
                else:
                    src.rename(path)
                n += 1
            elif kind == "remove" or (r["action"] == "remove" and path.is_file()):
                _stash(path, bak, proj)
                n += 1
            elif kind == "remove_dir" or path.is_dir():
                _stash(path, bak, proj)
                n += 1
        # __pycache__ is left in place: never delete; Python rebuilds it when needed.
    return n


def cmd_check() -> int:
    rc = 0
    for proj in discover_projects():
        problems = []
        if (proj / "modules").is_dir():
            problems.append("仍有 modules/")
        if (proj / "config").is_dir():
            problems.append("仍有 config/")
        if not settings_path(proj).is_file():
            problems.append("缺少 settings.ini")
        if (proj / "PBG").is_dir():
            problems.append("仍有 PBG/")
        if not (proj / f"{proj.name}.html").is_file():
            problems.append(f"缺少 {proj.name}.html")
        if (proj / "console.bat").is_file():
            problems.append("仍有 console.bat")
        if (proj / "run.bat").is_file():
            problems.append("仍有 run.bat")
        if (proj / "flow_chart.html").is_file():
            problems.append("仍有 flow_chart.html")
        if (proj / "打开流程图工具.bat").is_file():
            problems.append("仍有 打开流程图工具.bat")
        if (proj / "strobe-flowchart").is_dir():
            problems.append("仍有 strobe-flowchart/")
        if problems:
            rc = 1
            print(f"{proj.name}: FAIL")
            for x in problems:
                print("   ", x)
        else:
            print(f"{proj.name}: OK")
    if rc == 0:
        print("CHECK OK: 配置在项目内，脚本在 modules")
    return rc


def _sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="项目只留 settings + console，脚本用 modules")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--keys", action="store_true")
    args = p.parse_args(argv)

    if args.check:
        return cmd_check()

    if args.keys:
        log = apply_keys(dry=not args.apply)
        print(("APPLY" if args.apply else "DRY-RUN") + " settings keys")
        print("\n".join("  " + x for x in log) if log else "  无需追加")
        return 0

    if not SHIM.is_dir():
        raise SystemExit(f"缺少 shim: {SHIM}")

    packed = [(proj, plan(proj)) for proj in discover_projects()]
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{mode}  layout  gold={GOLD}  roots={[str(r) for r in RAD_ROOTS]}")
    for proj, rows in packed:
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["action"]] = counts.get(r["action"], 0) + 1
        print(f"  {proj.parent.name + '/' + proj.name:28s}  " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for r in rows:
            if r["action"] != "same":
                print(f"      {r['action']:9s}  {r['path'].relative_to(proj)}")

    key_log = apply_keys(dry=not args.apply)
    if key_log:
        print("settings keys:")
        for line in key_log:
            print(" ", line)

    if not args.apply:
        print("\n未写入。确认后加 --apply")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    n = apply_rows(packed, stamp)
    print(f"\n已处理 {n} 项。备份: {BAK_ROOT}\\<项目>_{stamp}\\")
    return 0


if __name__ == "__main__":
    sys.exit(main())
