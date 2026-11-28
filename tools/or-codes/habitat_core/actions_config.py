# -*- coding: utf-8 -*-
"""控制台动作策略与输出面板配置（每工程两个文件，与 settings.ini 同目录）。

actions.ini
  [confirm]  REQUIRE / PHRASE / SHOW_CMD
  [pipeline] [smoke] [cleanup] [match] [roi]
             ENABLED / DRY_RUN / ARGS /（match、roi 另有 OUTPUT_DIR；roi 另有 LIMIT）

console.ini
  [output]   HEIGHT_PX / FONT_SIZE / POLL_MS / MAX_LINES / MAX_CHARS
             AUTOSCROLL / SHOW_TIMESTAMP / KEEP_LOG_FILES

两个文件首次访问时按默认值生成；已存在则只读，不覆盖用户改动。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .ini_config import read_ini_by_section

ACTIONS_NAME = "actions.ini"
CONSOLE_NAME = "console.ini"

ACTION_SECTIONS: list[tuple[str, str, list[str]]] = [
    ("confirm", "运行前确认", ["REQUIRE", "PHRASE", "SHOW_CMD"]),
    ("pipeline", "跑流水线", ["ENABLED", "DRY_RUN", "ARGS"]),
    ("smoke", "冒烟测试", ["ENABLED", "DRY_RUN", "ARGS"]),
    ("cleanup", "清理 checkpoints", ["ENABLED", "DRY_RUN", "ARGS"]),
    ("match", "影像↔ROI 配对", ["ENABLED", "DRY_RUN", "ARGS", "OUTPUT_DIR"]),
    ("roi", "ROI 外扩", ["ENABLED", "DRY_RUN", "ARGS", "OUTPUT_DIR", "LIMIT"]),
]
CONSOLE_SECTIONS: list[tuple[str, str, list[str]]] = [
    ("output", "底部输出面板", ["HEIGHT_PX", "FONT_SIZE", "POLL_MS", "MAX_LINES",
                                "MAX_CHARS", "AUTOSCROLL", "SHOW_TIMESTAMP", "KEEP_LOG_FILES"]),
]
DEFAULT_CONSOLE: dict[str, Any] = {
    "HEIGHT_PX": 260, "FONT_SIZE": 12, "POLL_MS": 1500, "MAX_LINES": 800,
    "MAX_CHARS": 200000, "AUTOSCROLL": True, "SHOW_TIMESTAMP": True, "KEEP_LOG_FILES": 20,
}
# 每个动作的默认策略（DRY_RUN=True 的动作默认不写盘）
DEFAULT_ACTION: dict[str, dict[str, Any]] = {
    "pipeline": {"ENABLED": True, "DRY_RUN": False, "ARGS": []},
    "smoke":    {"ENABLED": True, "DRY_RUN": False, "ARGS": []},
    "cleanup":  {"ENABLED": True, "DRY_RUN": True, "ARGS": []},
    "match":    {"ENABLED": True, "DRY_RUN": True, "ARGS": [], "OUTPUT_DIR": ""},
    "roi":      {"ENABLED": True, "DRY_RUN": True, "ARGS": [], "OUTPUT_DIR": "", "LIMIT": 1},
}


def _paths(ini: Path) -> tuple[Path, Path]:
    return ini.parent / ACTIONS_NAME, ini.parent / CONSOLE_NAME


def _write_sections(path: Path, title: str,
                    blocks: list[tuple[str, str, dict[str, Any]]]) -> None:
    """按 (段名, 段标题, 键值) 顺序写 ini。"""
    lines = [f"# {title}", "# 值用 JSON：true/false/null、数字、字符串带引号、列表/对象。", ""]
    for sid, sname, items in blocks:
        if not items:
            continue
        lines.append(f"[{sid}]  ; {sname}")
        for k, v in items.items():
            lines.append(f"{k} = {_encode(v)}")
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _encode(val: Any) -> str:
    import json
    return json.dumps(val, ensure_ascii=False)


def ensure_files(ini: Path, project: str) -> tuple[Path, Path]:
    """确保两个配置文件存在（缺则按默认值逐段生成，不覆盖已有）。"""
    actions_path, console_path = _paths(ini)
    if not actions_path.is_file():
        blocks: list[tuple[str, str, dict[str, Any]]] = [
            ("confirm", "运行前确认",
             {"REQUIRE": True, "PHRASE": project, "SHOW_CMD": True}),
        ]
        for sid, sname, _keys in ACTION_SECTIONS[1:]:
            defaults = DEFAULT_ACTION.get(sid)
            if defaults:
                blocks.append((sid, sname, dict(defaults)))
        _write_sections(actions_path, f"{project} 控制台动作策略（可改）", blocks)
    if not console_path.is_file():
        _write_sections(console_path, f"{project} 控制台输出面板（可改）",
                        [("output", "底部输出面板", dict(DEFAULT_CONSOLE))])
    return actions_path, console_path


def load_actions(ini: Path, project: str) -> dict[str, Any]:
    """返回 {"confirm": {...}, "actions": {动作名: {...}}}（缺键用默认值补齐）。"""
    actions_path, _ = ensure_files(ini, project)
    raw = read_ini_by_section(actions_path)

    confirm = {"REQUIRE": True, "PHRASE": project, "SHOW_CMD": True}
    confirm.update({k: v for k, v in raw.get("confirm", {}).items() if k in confirm})
    if not str(confirm.get("PHRASE") or "").strip():
        confirm["PHRASE"] = project

    actions: dict[str, dict[str, Any]] = {}
    for name, defaults in DEFAULT_ACTION.items():
        merged = dict(defaults)
        merged.update({k: v for k, v in raw.get(name, {}).items() if k in defaults})
        actions[name] = merged
    return {"confirm": confirm, "actions": actions}


def load_console(ini: Path, project: str) -> dict[str, Any]:
    _, console_path = ensure_files(ini, project)
    raw = read_ini_by_section(console_path).get("output", {})
    merged = dict(DEFAULT_CONSOLE)
    merged.update({k: v for k, v in raw.items() if k in merged})
    return merged


def action_default(name: str) -> dict[str, Any]:
    return dict(DEFAULT_ACTION.get(name, {}))
