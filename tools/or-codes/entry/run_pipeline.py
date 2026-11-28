# -*- coding: utf-8 -*-
"""跑某个工程的 habitat 流水线（共享入口）。

工程根只留一个 .bat 调用本脚本，不再每工程放一份 run_pipeline.py。

用法：
    python run_pipeline.py --ini G:\\CC\\fyh_CAC\\settings.ini
    python run_pipeline.py --ini ... --data other.csv --debug
不给 --ini 时，从当前目录向上找 settings.ini / ref/settings.ini。
"""
from __future__ import annotations

import argparse
import os
import sys

CORE = os.environ.get("HABITAT_CORE_ROOT") or r"{or_codes}"
if CORE not in sys.path:
    sys.path.insert(0, CORE)

try:  # 老控制台是 GBK，打印箭头/生僻符号会崩
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="生境影像组学流水线（共享入口）")
    parser.add_argument("--ini", default=None, help="工程 settings.ini；省略则向上查找")
    parser.add_argument("--data", default=None, help="数据表 CSV；省略取 ini 的 DATA_TABLE_PATH")
    parser.add_argument("--labels", default=None, help="标签表 CSV；省略取 ini 的 LABELS_TABLE_PATH")
    parser.add_argument("--debug", action="store_true", help="DEBUG 日志")
    args = parser.parse_args()

    if args.ini:
        os.environ["HABITAT_INI"] = str(args.ini)

    import logging

    from habitat_core import config
    from habitat_core.pipeline import run_pipeline

    print(f"工程 {config.PROJECT}  ini {config.INI_PATH}")
    print(f"  数据 {args.data or config.DATA_TABLE_PATH}")
    print(f"  中间件 {config.OUTPUT_ROOT}   结果归档 {config.ARCHIVE_ROOT}")

    run_pipeline(
        data_table_path=args.data or str(config.DATA_TABLE_PATH),
        labels_path=args.labels or (str(config.LABELS_TABLE_PATH) if config.LABELS_TABLE_PATH else None),
        log_level=logging.DEBUG if args.debug else logging.INFO,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
