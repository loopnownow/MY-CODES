"""全量 caption + 评估，一条龙后台执行。

用法（脚本已移 _Codes；venv 需按 mlh_SIX/ref/env-skinvl.md 重建）：
    Start-Process <venv>/Scripts/python.exe -ArgumentList `
        '{lab_root}/_Codes/tools/vlm/skinvl/run_full.py' -WindowStyle Hidden

日志：{lab_root}/mlh_SIX/logs/full_run.log
中断后重跑本脚本即可：infer_batch 带 --resume，会跳过 status=ok 的行。
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent      # = _Codes\tools\vlm\skinvl
PY = Path(r"{lab_root}\pre-submit\mlh_SIX\.venv\Scripts\python.exe")   # 需按 ref\env-skinvl.md 重建
OUT_DIR = Path(r"{lab_root}\pre-submit\mlh_SIX\results\skinvl")
LOG = Path(r"{lab_root}\pre-submit\mlh_SIX\logs") / "full_run.log"
CAPTION_OUT = OUT_DIR / "skinvl_caption.xlsx"
EVAL_OUT = OUT_DIR / "skinvl_eval.xlsx"

ENV = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE")
ENV.pop("SKINVL_ALLOW_DOWNLOAD", None)  # 全量阶段不需要联网


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(script: str, *args: str) -> int:
    cmd = [str(PY), str(SCRIPT_DIR / script), *args]
    log("run: " + " ".join(cmd))
    with LOG.open("a", encoding="utf-8") as fh:
        proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT, env=ENV, cwd=str(SCRIPT_DIR))
        proc.wait()
    log(f"exit={proc.returncode}")
    return proc.returncode


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("=== full run start ===")

    rc = run(
        "infer_batch.py",
        "--prompt-mode", "caption",
        "--batch-size", "16",
        "--out", str(CAPTION_OUT),
        "--resume",
    )
    if rc != 0:
        log(f"caption failed rc={rc}, abort eval")
        return rc

    rc = run(
        "eval_mapping.py",
        "--pred", str(CAPTION_OUT),
        "--out", str(EVAL_OUT),
    )
    log(f"=== full run done rc={rc} ===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
