# -*- coding: utf-8 -*-
"""工程 settings.ini 的定位与读写。

值与 MY-CODES modules/config/ini_io.py 同口径：分段 INI + JSON 标量/列表/对象，
所以同一个 settings.ini 两边都能读。

定位顺序（fail loud，不猜）：
  1. 环境变量 HABITAT_INI 指向的文件；
  2. 从 start（默认 cwd）向上找 settings.ini 或 ref/settings.ini；
  3. 都没有 -> 报错，不回退默认路径。

运行: 由 habitat_core 内各模块 import，不单独执行。
"""
from __future__ import annotations

import configparser
import json
import os
from pathlib import Path
from typing import Any

INI_NAME = "settings.ini"
ENV_INI = "HABITAT_INI"
ENV_CORE = "HABITAT_CORE_ROOT"
DEFAULT_CORE_ROOT = r"{or_codes}"


class SettingsNotFound(RuntimeError):
    """找不到 settings.ini。"""


def core_root() -> Path:
    """共享代码根：HABITAT_CORE_ROOT 优先，否则本文件的上两级。"""
    env = os.environ.get(ENV_CORE)
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def find_ini(start: Path | str | None = None) -> Path:
    """按定位顺序返回 settings.ini；找不到抛 SettingsNotFound。"""
    env = os.environ.get(ENV_INI)
    if env:
        p = Path(env)
        if p.is_file():
            return p
        raise SettingsNotFound(f"{ENV_INI} 指向的文件不存在: {p}")
    cur = Path(start or os.getcwd()).resolve()
    for base in (cur, *cur.parents):
        for cand in (base / INI_NAME, base / "ref" / INI_NAME):
            if cand.is_file():
                return cand
    raise SettingsNotFound(
        f"从 {cur} 向上未找到 {INI_NAME}；用 {ENV_INI} 指定，或在工程根放 settings.ini"
    )


def decode_value(raw: str) -> Any:
    """INI 值按 JSON 解码。

    兼容手工编辑：Windows 路径常被写成 "G:\\a\\b"（单反斜杠），不是合法 JSON；
    这类带引号的字符串去掉外层引号并还原双反斜杠，避免路径里残留引号。
    """
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    text = raw.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        inner = text[1:-1]
        inner = inner.replace("\\\\", "\\") if text[0] == '"' else inner
        return inner
    return raw


def encode_value(val: Any) -> str:
    """写回 INI 用 JSON：中文不转义，保证人工可读。"""
    return json.dumps(val, ensure_ascii=False)


def read_ini(path: Path | str) -> dict[str, Any]:
    """读全部分段为扁平 dict（键名大小写敏感）。"""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    cp.read_string(text)
    out: dict[str, Any] = {}
    for sec in cp.sections():
        for key, raw in cp.items(sec):
            if key.startswith("#"):
                continue
            out[key] = decode_value(raw)
    return out


def write_ini(path: Path | str, values: dict[str, Any],
              sections: list[tuple[str, str, list[str]]], header: list[str] | None = None) -> None:
    """按 sections 顺序写 INI；未列进 sections 的键写入 [other]。"""
    path = Path(path)
    known: set[str] = set()
    lines: list[str] = list(header or [])
    if lines:
        lines.append("")
    for sid, title, keys in sections:
        items = [(k, values[k]) for k in keys if k in values]
        if not items:
            continue
        known.update(k for k, _ in items)
        lines.append(f"[{sid}]  ; {title}")
        lines.extend(f"{k} = {encode_value(v)}" for k, v in items)
        lines.append("")
    extra = [(k, v) for k, v in values.items() if k not in known]
    if extra:
        lines.append("[other]  ; 未归类键")
        lines.extend(f"{k} = {encode_value(v)}" for k, v in sorted(extra))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def read_ini_by_section(path: Path | str) -> dict[str, dict[str, Any]]:
    """按段读取：{段名: {键: 值}}。同一段内键名重复以最后一次为准。"""
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str
    text = Path(path).read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]
    cp.read_string(text)
    out: dict[str, dict[str, Any]] = {}
    for sec in cp.sections():
        out[sec] = {k: decode_value(raw) for k, raw in cp.items(sec) if not k.startswith("#")}
    return out


def require(data: dict[str, Any], key: str) -> Any:
    """取必填键；缺失直接抛错（不静默回退）。"""
    if key not in data or data[key] in (None, ""):
        raise KeyError(f"settings.ini 缺少必填键 {key}")
    return data[key]
