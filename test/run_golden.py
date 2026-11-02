"""One command to re-run the bundled test project and diff it against the golden snapshot.

配置
----
PROJECT_DIR   本目录（源夹具：`test.xlsx` + `ref/settings.ini` + overlay）
EXPECTED      数字基线 `expected/golden.json`
RUN_FILES     复制到临时工作区后才会写盘的输入件
RESULT_HTML   期望产出的报告相对路径
WORKDIR_NAME  临时工作区目录名（默认放系统 TEMP，仓库内不留运行产物）

运行
----
    python -B test/run_golden.py              # 跑 pipeline → 对拍 → 有 FAIL 则 exit 1
    python -B test/run_golden.py --refresh    # 有意改动后重写 expected/golden.json
    python -B test/run_golden.py --no-run     # 不重跑，只对拍临时区现有产出
    python -B test/run_golden.py --workdir <TEMP>\\golden-run

产物（Group/、ref/cache/、PNG 等）只落在临时工作区，本仓 `test/` 保持干净。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_DIR.parent
EXPECTED = PROJECT_DIR / "expected" / "golden.json"
RUN_FILES = ("test.xlsx", "ref/settings.ini", "ref/.rad_console_overlay.json")
RESULT_HTML = Path("Group") / "ResultsGroup.html"
WORKDIR_NAME = "mycodes-golden-run"
PYTHON_BIN = sys.executable


def _prepare_workdir(workdir: Path) -> Path:
    if workdir.exists():
        shutil.rmtree(workdir)
    for rel in RUN_FILES:
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_DIR / rel, target)
    return workdir


def _run_pipeline(workdir: Path) -> None:
    env = dict(os.environ)
    env["RAD_PROJECT"] = str(workdir)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [PYTHON_BIN, "-B", "-m", "modules.pipeline"],
        cwd=str(workdir),
        env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"pipeline failed with exit {proc.returncode}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the bundled test project and diff against golden.json")
    ap.add_argument("--refresh", action="store_true", help="rewrite expected/golden.json")
    ap.add_argument("--no-run", action="store_true", help="reuse the existing workdir output")
    ap.add_argument("--workdir", default=None,
                    help="scratch dir (default: TEMP/%s)" % WORKDIR_NAME)
    args = ap.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    from modules.tests import golden_tools as gt

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.gettempdir()) / WORKDIR_NAME
    if not args.no_run:
        _prepare_workdir(workdir)
        _run_pipeline(workdir)

    html = workdir / RESULT_HTML
    if not html.is_file():
        raise SystemExit(f"missing result html: {html} (run without --no-run first)")

    current = gt.extract(html)
    if args.refresh:
        EXPECTED.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"golden refreshed: {EXPECTED} tables={len(current['tables'])}")
        return 0

    if not EXPECTED.is_file():
        raise SystemExit(f"missing golden: {EXPECTED} (run with --refresh first)")

    golden = json.loads(EXPECTED.read_text(encoding="utf-8"))
    report = gt.render_report(gt.compare(golden, current), EXPECTED, html)
    print(report)
    return 1 if "FAIL: **0**" not in report else 0


if __name__ == "__main__":
    raise SystemExit(main())
