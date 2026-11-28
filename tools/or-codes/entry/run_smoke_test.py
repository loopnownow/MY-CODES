# -*- coding: utf-8 -*-
"""跑某个工程的冒烟测试（共享入口）。合成数据，不读 NAS 影像。

用法：
    python run_smoke_test.py --ini G:\\CC\\fyh_CAC\\settings.ini
    python run_smoke_test.py --ini ... --csv other.csv
不给 --ini 时，从当前目录向上找 settings.ini / ref/settings.ini。
"""
from __future__ import annotations

import argparse
import os
import sys

CORE = os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if CORE not in sys.path:
    sys.path.insert(0, CORE)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="生境影像组学冒烟测试（共享入口）")
    parser.add_argument("--ini", default=None, help="工程 settings.ini；省略则向上查找")
    parser.add_argument("--csv", default=None, help="真实数据表 CSV；省略取 ini 的 DATA_TABLE_PATH")
    args = parser.parse_args()

    if args.ini:
        os.environ["HABITAT_INI"] = str(args.ini)

    from habitat_core import config
    from habitat_core import test as smoke

    print(f"工程 {config.PROJECT}  ini {config.INI_PATH}")
    sys.argv = ["run_smoke_test.py", "--csv", str(args.csv or config.DATA_TABLE_PATH)]
    smoke.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
