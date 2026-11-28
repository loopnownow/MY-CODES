# -*- coding: utf-8 -*-
"""加工脚本的 ini 读取层（`_Codes\\tools` 下 23 个脚本共用）。

脚本顶部写：
    from habitat_core import tool_ini as _tk
    ROOT_DIR = _tk.get("CLEANUP_TARGET_DIR")      # 缺键报错，不静默回退

取 ini 的顺序（fail loud）：
  1. 命令行 --ini <path>（或 --ini=<path>）
  2. 环境变量 HABITAT_INI
  3. 命令行 --project <name> 或环境变量 HABITAT_PROJECT -> G:\\<name>\\settings.ini
  4. 从当前目录向上找 settings.ini / ref/settings.ini
--ini / --project 会从 sys.argv 摘掉，脚本自己的 argparse 看不到它们。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .ini_config import SettingsNotFound, find_ini, read_ini

ENV_PROJECT = "HABITAT_PROJECT"
LAB_ROOT = Path(os.environ.get("LAB_ROOT", ""))


def _pop_option(names: tuple[str, ...]) -> str | None:
    """把 `--name value` / `--name=value` 从 sys.argv 摘出来并返回其值。"""
    argv = sys.argv[1:]
    value: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        hit = next((n for n in names if arg == n or arg.startswith(n + "=")), None)
        if hit is None:
            rest.append(arg)
            i += 1
            continue
        if arg == hit:
            if i + 1 >= len(argv):
                raise SystemExit(f"{hit} 需要取值")
            value = argv[i + 1]
            i += 2
        else:
            value = arg.split("=", 1)[1]
            i += 1
    sys.argv = [sys.argv[0], *rest]
    return value


class Toolkit:
    """一个工程 ini 的只读视图。"""

    def __init__(self) -> None:
        ini_arg = _pop_option(("--ini",))
        proj_arg = _pop_option(("--project",))
        self.path = self._resolve(ini_arg, proj_arg)
        self._data = read_ini(self.path)

    @staticmethod
    def _resolve(ini_arg: str | None, proj_arg: str | None) -> Path:
        if ini_arg:
            p = Path(ini_arg)
            if not p.is_file():
                raise SettingsNotFound(f"--ini 指向的文件不存在: {p}")
            return p
        env = os.environ.get("HABITAT_INI")
        if env:
            p = Path(env)
            if not p.is_file():
                raise SettingsNotFound(f"HABITAT_INI 指向的文件不存在: {p}")
            return p
        proj = proj_arg or os.environ.get(ENV_PROJECT)
        if proj:
            p = LAB_ROOT / proj / "settings.ini"
            if p.is_file():
                return p
            # 家族层布局：G:\<family>\<proj>\settings.ini
            hits = sorted(LAB_ROOT.glob(f"*/{proj}/settings.ini"))
            if hits:
                return hits[0]
            raise SettingsNotFound(f"工程 {proj} 没有 settings.ini: {p}（含 G:\\<family>\\{proj}）")
        return find_ini()

    @property
    def project(self) -> str:
        return str(self._data.get("PROJECT") or self.path.parent.name)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data and self._data[key] not in (None, ""):
            return self._data[key]
        if default is not None:
            return default
        raise KeyError(
            f"{self.path} 的 [tools] 段缺少 {key}；"
            f"用 --project <工程名> 或 --ini <settings.ini> 指定正确工程"
        )

    def optional(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


tk = Toolkit()


# 模块级便捷函数：脚本里统一写 _tk.get("KEY") / _tk.require("KEY")
def get(key: str, default: Any = None) -> Any:
    return tk.get(key, default)


def require(key: str) -> Any:
    return tk.get(key)


def optional(key: str, default: Any = None) -> Any:
    return tk.optional(key, default)


def project() -> str:
    return tk.project


def ini_path() -> Path:
    return tk.path
