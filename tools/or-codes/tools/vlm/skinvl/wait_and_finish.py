"""等待 SkinVL-MM 权重下载完成，然后自动补齐 CLIP、跑自检与冒烟。

后台运行（脚本已移 _Codes；venv 需按 mlh_SIX/ref/env-skinvl.md 重建）：
    Start-Process <venv>/Scripts/python.exe -ArgumentList `
        '{lab_root}/_Codes/tools/vlm/skinvl/wait_and_finish.py' -WindowStyle Hidden

日志：{lab_root}/mlh_SIX/logs/finish_YYYYmmdd_HHMMSS.log
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent      # = _Codes\tools\vlm\skinvl
PY = Path(r"{lab_root}\pre-submit\mlh_SIX\.venv\Scripts\python.exe")   # 需按 ref\env-skinvl.md 重建
MODEL_ROOT = Path(os.environ.get("MODEL_ROOT", ""))
LOG_DIR = Path(r"{lab_root}\pre-submit\mlh_SIX\logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / f"finish_{datetime.now():%Y%m%d_%H%M%S}.log"

SHARDS = [
    (MODEL_ROOT / "SkinVL-MM" / "model-00001-of-00003.safetensors", 4_900_000_000),
    (MODEL_ROOT / "SkinVL-MM" / "model-00002-of-00003.safetensors", 4_900_000_000),
    (MODEL_ROOT / "SkinVL-MM" / "model-00003-of-00003.safetensors", 4_500_000_000),
]


def log(msg: str) -> None:
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(script: str, *args: str) -> int:
    cmd = [str(PY), str(SCRIPT_DIR / script), *args]
    log("run: " + " ".join(cmd))
    env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", SKINVL_ALLOW_DOWNLOAD="1")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (proc.stdout or "").splitlines():
        log("  | " + line)
    for line in (proc.stderr or "").splitlines()[-20:]:
        log("  ! " + line)
    log(f"exit code = {proc.returncode}")
    return proc.returncode


def main() -> int:
    log("watcher started")
    while True:
        pending = []
        for path, minimum in SHARDS:
            if not path.is_file():
                pending.append(f"{path.name}:missing")
            elif path.stat().st_size < minimum:
                pending.append(f"{path.name}:{path.stat().st_size/1024**3:.1f}GB")
        if not pending:
            break
        log("pending -> " + "; ".join(pending))
        time.sleep(60)

    log("all shards present; fetching CLIP")
    run("download_files.py", "--task", "clip", "--workers", "4")
    log("env check")
    run("run_env_check.py")
    log("smoke test")
    rc = run("smoke_test.py")
    log(f"done, smoke exit={rc}")
    log(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
