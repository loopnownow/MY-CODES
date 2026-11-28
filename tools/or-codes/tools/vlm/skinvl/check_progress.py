"""查看全量推理进度。只读，不干扰正在运行的任务。

用法（脚本已移 _Codes；venv 已移入 {DEL}，需按 mlh_SIX/ref/env-skinvl.md 重建）：
    & <venv>/Scripts/python.exe {lab_root}/_Codes/tools/vlm/skinvl/check_progress.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

XLSX = Path(r"{lab_root}\pre-submit\mlh_SIX\results\skinvl\skinvl_caption.xlsx")
EVAL = Path(r"{lab_root}\pre-submit\mlh_SIX\results\skinvl\skinvl_eval.xlsx")
TOTAL = 4044


def gpu() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "n/a"
    except Exception:  # noqa: BLE001
        return "n/a"


def main() -> int:
    if EVAL.is_file():
        print(f"[完成] 评估表已生成: {EVAL}  ({EVAL.stat().st_size/1024:.1f} KB)")
    if not XLSX.is_file():
        print("还没有输出文件，任务可能尚未 flush（每 80 条写一次盘）")
        print(f"GPU: {gpu()}")
        return 0

    try:
        df = pd.read_excel(XLSX)
    except Exception:  # noqa: BLE001
        print("输出文件正在写入，稍后再看")
        print(f"GPU: {gpu()}")
        return 0

    n = len(df)
    ok = int((df["status"].astype(str).str.lower() == "ok").sum()) if "status" in df else 0
    pct = 100 * n / TOTAL
    print(f"已完成 {n}/{TOTAL}  ({pct:.1f}%)   ok={ok}   fail={n-ok}")
    if "label" in df:
        print("标签分布:", df["label"].value_counts().to_dict())
    if n:
        el = pd.to_numeric(df.get("elapsed_s"), errors="coerce").dropna()
        if len(el):
            print(f"平均单图耗时: {el.mean():.2f} s  (中位 {el.median():.2f} s)")
    print(f"输出文件更新时间: {time.strftime('%H:%M:%S', time.localtime(XLSX.stat().st_mtime))}")
    print(f"GPU: {gpu()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
