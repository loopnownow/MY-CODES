# -*- coding: utf-8 -*-
"""刷新 `G:\\README.md` 的「现有工程登记」表（只改 REGISTRY 标记块内）。

用法：
    python refresh_g_root_readme.py            # dry-run，打印新表
    python refresh_g_root_readme.py --execute   # 写回 G:\\README.md
    python refresh_g_root_readme.py --check     # 只校验：与实盘不一致则退出码 1

表内数字全部来自实盘：settings.ini 的路径键 + G: 工程根文件数 + _Codes\\tools 下可运行脚本数。
"""
from __future__ import annotations
import os

import argparse
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, r"{or_codes}")
from habitat_core.ini_config import read_ini  # noqa: E402

ROOT = Path(os.environ.get("ROOT", ""))
CORE = Path(r"{or_codes}")
README = ROOT / "README.md"
BEGIN = "<!-- REGISTRY:BEGIN"
END = "<!-- REGISTRY:END -->"
FIXED_ORDER = ["fyh_CAC", "fyh_CCEC", "ybc_EC", "xlm_LUNG", "dll_OV",
               "llj_OV", "zyp_OV", "yt_RDL", "dyf_SSD", "jfl_SSD"]
KIND = {"dyf_SSD": "MATLAB T1-VBM", "jfl_SSD": "Python 双数据集"}


def projects() -> list[str]:
    direct = [p.parent.name for p in ROOT.glob("*/settings.ini")]
    nested = [p.parent.name for p in ROOT.glob("*/*/settings.ini")]
    found = sorted(set(direct) | set(nested))
    ordered = [p for p in FIXED_ORDER if p in found]
    return ordered + [p for p in found if p not in ordered]


def runnable_tools(proj: str) -> int:
    tools_root = CORE / "tools"
    if not tools_root.is_dir():
        return 0
    count = 0
    for group in tools_root.iterdir():
        d = group / proj
        if not d.is_dir():
            continue
        for script in d.glob("*.py"):
            try:
                if "__main__" in script.read_text(encoding="utf-8", errors="replace"):
                    count += 1
            except OSError:
                pass
    return count


def build_table() -> str:
    lines = ["| 工程 | 类型 | 数据表 | 01–03（处理盘） | 04–06 / QC（归档） | 工具数 | G: 根文件 |",
             "|---|---|---|---|---|---|---|"]
    for proj in projects():
        ini = ROOT / proj / "settings.ini"
        if not ini.is_file():
            hits = sorted(ROOT.glob(f"*/{proj}/settings.ini"))
            if hits:
                ini = hits[0]
        v = read_ini(ini)
        data = str(v.get("DATA_TABLE_PATH") or "—")
        if proj == "jfl_SSD":
            data = "—（JFL / ZX 各一份 combined.csv）"
            data_cell = data
        else:
            data_cell = f"`{data}`"
        out = str(v.get("OUTPUT_ROOT") or "—")
        arch = str(v.get("ARCHIVE_ROOT") or "—")
        proj_dir = ini.parent
        roots = len([p for p in proj_dir.iterdir() if p.is_file()])
        lines.append(f"| `{proj}` | {KIND.get(proj, 'habitat')} | "
                     f"{data_cell} | `{out}` | `{arch}` | {runnable_tools(proj)} | {roots} |")
    return "\n".join(lines)


def current_block(text: str) -> tuple[int, int]:
    start = text.find(BEGIN)
    end = text.find(END)
    return (start, end + len(END) if end >= 0 else -1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if not README.is_file():
        print(f"找不到 {README}；先创建（含 REGISTRY 标记块）再跑本脚本")
        return 2
    text = README.read_text(encoding="utf-8")
    start, end = current_block(text)
    if start < 0 or end < 0:
        print("README 里没有 REGISTRY 标记块")
        return 2
    head = text[:start]
    tail = text[end:]
    new_block = f"{BEGIN} 由 _Codes\\tools\\registry\\refresh_g_root_readme.py 生成，勿手改 -->\n{build_table()}\n{END}"
    new_text = head + new_block + tail

    print(build_table())
    if args.check:
        same = new_text.strip() == text.strip()
        print("\n校验:", "一致" if same else "与实盘不一致（请 --execute 刷新）")
        return 0 if same else 1
    if not args.execute:
        print("\ndry-run：加 --execute 写回 G:\\README.md")
        return 0
    README.write_text(new_text, encoding="utf-8")
    print(f"\n已写回 {README}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
