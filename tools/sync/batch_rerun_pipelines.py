# -*- coding: utf-8 -*-
"""
顺序重跑各阶段目录（drafting/pre-submit/…）下课题的 pipeline（ref/settings.ini，--no-overlay）。

  python 0doc/_code/02-data-processing/_shared/sync/batch_rerun_pipelines.py
  python 0doc/_code/02-data-processing/_shared/sync/batch_rerun_pipelines.py --only lya_CTA,dll_OV
  python 0doc/_code/02-data-processing/_shared/sync/batch_rerun_pipelines.py --resume

断点：0del/harvested/batch_rerun_status.json 里 status=ok 的课题 --resume 会跳过。
算法库由各课题 ref/settings.ini 的 MODULES_DIR 决定（modules/__init__.py 读取）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

STATS_ROOT = Path(os.environ.get("STATS_ROOT") or Path(__file__).resolve().parents[2])  # 算法库根 = 仓根（tools/sync/ 上两级）
if str(STATS_ROOT) not in sys.path:
    sys.path.insert(0, str(STATS_ROOT))
from modules.console.paths import grok_root  # GROK_ROOT env -> frozen lab default (C1)
GROK_ROOT = grok_root(STATS_ROOT / "modules")  # 阶段根父目录
_IMPORT = STATS_ROOT  # import root of the modules package
# Same stage names as MY-CODES/modules/console/paths.py.
STAGE_NAMES = (
    "drafting", "pre-submit", "in-review", "accepted",
)
_rad = os.environ.get("ZERAD_ROOT") or os.environ.get("ORAD_ROOT")
STAGE_ROOTS = [Path(_rad)] if _rad else [GROK_ROOT / n for n in STAGE_NAMES if (GROK_ROOT / n).is_dir()]
_DEL_ROOT = Path(os.environ.get("DEL_ROOT") or r"D:\0del")  # 与 {DEL} / tidy_paths.DEL_ROOT 对齐
STATUS_PATH = _DEL_ROOT / "harvested" / "batch_rerun_status.json"  # t1490u
BATCH_LOG = _DEL_ROOT / "harvested" / "batch_rerun.log"  # t1490u

# 先短后长；三个 SSD 串行
ORDER = [
    "lya_CTA",
    "fyh_CCEC",
    "zyp_OV",
    "llj_OV_1",
    "llj_OV_2",
    "jfl_SSD_ASL",
    "jfl_SSD_DKI",
    "jfl_SSD_DTI_IVIM",
    "lya_WML",
    "ly_SCH_HE",
    "xlm_LG",
    "yt_RDL",
    "dll_OV",
    "fyh_CAC",
    "lxf_RP",
    "ybc_EC",
]


def project_path(name: str) -> Path:
    hits = [r / name for r in STAGE_ROOTS if (r / name).is_dir()]
    if not hits:
        raise SystemExit(f"课题不存在: {name}（已查 {', '.join(str(r) for r in STAGE_ROOTS)}）")
    if len(hits) > 1:
        raise SystemExit(f"课题重名: {name} → {', '.join(str(h) for h in hits)}")
    return hits[0]


def _load_status() -> dict:
    if STATUS_PATH.is_file():
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    return {"started_at": None, "projects": {}}


def _save_status(st: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    BATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_one(name: str) -> dict:
    proj = project_path(name)
    if not (proj / "ref" / "settings.ini").is_file():
        return {"status": "skip", "reason": "no settings.ini"}
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_IMPORT)
    env["RAD_PROJECT"] = str(proj)
    env.pop("RAD_MODULES", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("RAD_CONSOLE_OVERLAY", None)
    cmd = [
        sys.executable, "-u", "-m", "modules.pipeline",
        "--no-overlay", "--log", "ref/pipeline_run.log",
    ]
    t0 = time.time()
    _log(f"START {name}")
    proc = subprocess.run(
        cmd,
        cwd=str(proj),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    dt = time.time() - t0
    tail = (proc.stdout or "")[-4000:]
    rec = {
        "status": "ok" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "seconds": round(dt, 1),
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log": str(proj / "ref" / "pipeline_run.log"),
        "tail": tail,
    }
    _log(f"END   {name}  {rec['status']}  {rec['seconds']}s  rc={proc.returncode}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔课题名")
    ap.add_argument("--resume", action="store_true", help="跳过本次状态里已 ok 的")
    args = ap.parse_args()
    names = ORDER
    if args.only.strip():
        names = [x.strip() for x in args.only.split(",") if x.strip()]
    for name in names:
        project_path(name)  # fail fast on unknown/moved project names
    st = _load_status()
    if not st.get("started_at"):
        st["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.setdefault("projects", {})
    _save_status(st)
    _log(f"BATCH begin n={len(names)} resume={args.resume}")
    for name in names:
        prev = st["projects"].get(name) or {}
        if args.resume and prev.get("status") == "ok":
            _log(f"SKIP  {name} (resume ok)")
            continue
        rec = run_one(name)
        if rec.get("status") == "ok":
            rec.pop("tail", None)
        else:
            rec["tail"] = (rec.get("tail") or "")[-2000:]
        st["projects"][name] = rec
        _save_status(st)
    ok = sum(1 for n in names if (st["projects"].get(n) or {}).get("status") == "ok")
    fail = [n for n in names if (st["projects"].get(n) or {}).get("status") == "fail"]
    st["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st["summary"] = {"ok": ok, "fail": fail, "n": len(names)}
    _save_status(st)
    _log(f"BATCH done ok={ok}/{len(names)} fail={fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
