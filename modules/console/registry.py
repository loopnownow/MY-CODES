"""控制台项目注册表：项目 → 目录 / token 哈希 / scopes（per-machine）。

落盘位置：const.REGISTRY_PATH（modules/console/console.projects.json，已进 .gitignore）。
运行：由 http_impl / console.py 导入，不单独执行；
     CLI 见 `python console.py register|unregister|registry`。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from modules.console import const as _const

TZ = timezone(timedelta(hours=8))
VERSION = 1
SCOPES = ("read", "write-settings", "run")
DEFAULT_SCOPES = ("read", "write-settings")


def path() -> Path:
    return Path(_const.REGISTRY_PATH)


def now_iso() -> str:
    return datetime.now(TZ).replace(microsecond=0).isoformat()


def resolve_dir(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def dir_key(value: str | Path) -> str:
    """比较用键：Windows 大小写/点段归一。"""
    return os.path.normcase(str(resolve_dir(value)))


def norm_dir(value: str | Path) -> str:
    return dir_key(value)


def token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


@dataclass
class Entry:
    project: str
    dir: str
    dir_key: str
    token_sha256: str
    scopes: list[str]
    created_at: str
    rotated_at: str = ""
    starter_html: str = ""

    def allows(self, scope: str) -> bool:
        return scope in (self.scopes or [])

    def dir_ok(self) -> bool:
        if not self.dir or not self.dir_key:
            return False
        if not Path(self.dir).is_dir():
            return False
        return dir_key(self.dir) == self.dir_key

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "dir": self.dir,
            "dir_key": self.dir_key,
            "token_sha256": self.token_sha256,
            "scopes": list(self.scopes),
            "created_at": self.created_at,
            "rotated_at": self.rotated_at,
            "starter_html": self.starter_html,
        }


def _entry_from(raw: dict[str, Any], project: str) -> Entry:
    return Entry(
        project=str(raw.get("project") or project),
        dir=str(raw.get("dir") or ""),
        dir_key=str(raw.get("dir_key") or ""),
        token_sha256=str(raw.get("token_sha256") or ""),
        scopes=[str(s) for s in (raw.get("scopes") or [])],
        created_at=str(raw.get("created_at") or ""),
        rotated_at=str(raw.get("rotated_at") or ""),
        starter_html=str(raw.get("starter_html") or ""),
    )


def load() -> dict[str, Any]:
    target = path()
    if not target.is_file():
        return {"version": VERSION, "projects": {}}
    raw = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("projects"), dict):
        raise ValueError(f"注册表格式错误: {target}")
    return {"version": int(raw.get("version") or VERSION), "projects": raw["projects"]}


def save(data: dict[str, Any]) -> Path:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def get(project: str) -> Entry | None:
    key = str(project or "").strip()
    if not key:
        return None
    raw = load()["projects"].get(key)
    return _entry_from(raw, key) if isinstance(raw, dict) else None


def lookup(token: str) -> Entry | None:
    want = token_hash(token or "")
    for key, raw in load()["projects"].items():
        if not isinstance(raw, dict):
            continue
        if secrets.compare_digest(str(raw.get("token_sha256") or ""), want):
            return _entry_from(raw, key)
    return None


def register(
    project: str,
    directory: str | Path,
    *,
    scopes: Iterable[str] | None = None,
    rotate: bool = False,
    starter_html: str = "",
) -> tuple[Entry, str]:
    """登记项目；返回 (entry, 明文 token)。只有新建或 rotate 时才返回 token。"""
    key = str(project or "").strip()
    if not key:
        raise ValueError("未指定 project")
    resolved = resolve_dir(directory)
    if not resolved.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {resolved}")
    resolved_key = dir_key(resolved)
    wanted = [str(s) for s in (scopes if scopes else DEFAULT_SCOPES)]
    bad = [s for s in wanted if s not in SCOPES]
    if bad:
        raise ValueError(f"未知 scope: {', '.join(bad)}（可选 {', '.join(SCOPES)}）")

    data = load()
    raw = data["projects"].get(key)
    token = ""
    if isinstance(raw, dict) and not rotate:
        entry = _entry_from(raw, key)
        entry.dir = str(resolved)
        entry.dir_key = resolved_key
        entry.scopes = wanted
        if starter_html:
            entry.starter_html = starter_html
    else:
        token = secrets.token_urlsafe(24)
        entry = Entry(
            project=key,
            dir=str(resolved),
            dir_key=resolved_key,
            token_sha256=token_hash(token),
            scopes=wanted,
            created_at=now_iso(),
            rotated_at=now_iso() if isinstance(raw, dict) else "",
            starter_html=starter_html,
        )
    data["projects"][key] = entry.as_dict()
    save(data)
    return entry, token


def unregister(project: str) -> bool:
    key = str(project or "").strip()
    data = load()
    if key not in data["projects"]:
        return False
    del data["projects"][key]
    save(data)
    return True


def listing() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, raw in load()["projects"].items():
        if not isinstance(raw, dict):
            continue
        entry = _entry_from(raw, key)
        row = entry.as_dict()
        row.pop("token_sha256", None)
        row["dir_ok"] = entry.dir_ok()
        rows.append(row)
    return sorted(rows, key=lambda r: r["project"])


def inject_token(html_path: str | Path, token: str) -> str:
    """把 token 写进项目启动页；返回动作名（placeholder / meta / inserted）。"""
    target = Path(html_path)
    if not target.is_file():
        raise FileNotFoundError(f"启动页不存在: {target}")
    text = target.read_text(encoding="utf-8")
    if "__CONSOLE_TOKEN__" in text:
        out, action = text.replace("__CONSOLE_TOKEN__", token), "placeholder"
    elif re.search(r'<meta\s+name="console-token"', text):
        out = re.sub(
            r'(<meta\s+name="console-token"\s+content=")[^"]*(")',
            lambda m: m.group(1) + token + m.group(2),
            text,
            count=1,
        )
        action = "meta"
    elif "<head>" in text:
        out = text.replace(
            "<head>",
            f'<head>\n<meta name="console-token" content="{token}"/>',
            1,
        )
        action = "inserted"
    else:
        raise ValueError(f"启动页缺 <head>: {target}")
    target.write_text(out, encoding="utf-8")
    return action


def clear_token(html_path: str | Path) -> str:
    """注销时把启动页里的 token 抹回占位符。"""
    target = Path(html_path)
    if not target.is_file():
        raise FileNotFoundError(f"启动页不存在: {target}")
    text = target.read_text(encoding="utf-8")
    out = re.sub(
        r'(<meta\s+name="console-token"\s+content=")[^"]*(")',
        lambda m: m.group(1) + "__CONSOLE_TOKEN__" + m.group(2),
        text,
        count=1,
    )
    target.write_text(out, encoding="utf-8")
    return "cleared"

