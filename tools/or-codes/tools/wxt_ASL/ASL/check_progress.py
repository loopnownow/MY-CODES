# -*- coding: utf-8 -*-
"""
check_progress.py - ExploreASL 并行进度快照（一键查看）
==========================================================
用法:
    python check_progress.py

输出: 各模块完成数、各窗口当前受试者、内存占用、错误计数、耗时。
"""
from __future__ import annotations

import glob
import os
import sys
import time

try:
    import psutil
except Exception:
    psutil = None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ====================================================================
ROOT = os.environ.get("ROOT", "")
EA = os.path.join(ROOT, "derivatives", "ExploreASL")
LOGS = os.path.join(ROOT, "logs")
N_SUBJECTS = 144
MODULES = ["Structural", "ASL", "Population"]
# ====================================================================


def study_level_ready(module: str) -> bool:
    """研究级模块 (Population) 用单个锁标记, 不按受试者计数。"""
    pat = os.path.join(EA, "lock", f"xASL_module_{module}", f"xASL_module_{module}",
                       "999_ready.status")
    return len(glob.glob(pat)) > 0


def ready_count(module: str) -> int:
    # 注意: ASL 为按 session 的子模块 (xASL_module_ASL_ASL_1),
    # 而 Structural 为 xASL_module_<Module>。用通配匹配两者。
    pat = os.path.join(EA, "lock", f"xASL_module_{module}", "*",
                       f"xASL_module_{module}*", "999_ready.status")
    return len(glob.glob(pat))


def latest_subject_logs(limit: int = 8):
    pat = os.path.join(EA, "log", "*_sub-*_1.log")
    items = []
    for p in glob.glob(pat):
        try:
            items.append((os.path.getmtime(p), os.path.basename(p)))
        except OSError:
            pass
    items.sort(reverse=True)
    return items[:limit]


def controller_errors():
    """统计各窗口控制器日志中的 ERROR / RUN FAILED 次数。"""
    errs = {}
    for p in sorted(glob.glob(os.path.join(LOGS, "run_*_w*.log"))):
        try:
            c = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        n = c.count("ERROR:") + c.count("RUN FAILED")
        errs[os.path.basename(p)] = n
    return errs


def mem():
    if psutil is None:
        return None
    vm = psutil.virtual_memory()
    matlab = 0
    n = 0
    for pr in psutil.process_iter(["name", "memory_info"]):
        try:
            if pr.info["name"] and "matlab" in pr.info["name"].lower():
                matlab += pr.info["memory_info"].rss
                n += 1
        except Exception:
            pass
    return vm, matlab, n


def main():
    now = time.time()
    print("=" * 64)
    print(f"ExploreASL 进度快照   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 64)

    # 各模块完成数 (Population 为研究级: 单标记, 不按受试者计数)
    for m in MODULES:
        if m == "Population":
            done = study_level_ready(m)
            bar = "#" * 30 if done else ""
            print(f"  {m:11s}: {'完成' if done else '未完成'}  [{bar:<30}]")
        else:
            c = ready_count(m)
            bar = "#" * int(30 * c / N_SUBJECTS)
            print(f"  {m:11s}: {c:3d}/{N_SUBJECTS}  [{bar:<30}]")

    # 内存
    r = mem()
    if r:
        vm, matlab, nproc = r
        print(f"\n  内存: 已用 {vm.used/1e9:.1f}GB / 可用 {vm.available/1e9:.1f}GB "
              f"({vm.percent}%)  |  MATLAB {nproc} 进程 共 {matlab/1e9:.1f}GB")
    else:
        print("\n  (内存: 需 psutil; pip install psutil)")

    # 最近更新的受试者日志（反映正在处理谁）
    print("\n  最近活跃的受试者日志:")
    for mt, name in latest_subject_logs():
        print(f"    {name:48s} {round(now - mt):4d}s 前")

    # 控制器错误
    errs = controller_errors()
    tot = sum(errs.values())
    print(f"\n  控制器日志错误总计: {tot}")
    for k, v in errs.items():
        if v:
            print(f"    [!] {k}: {v} 个 ERROR")

    # 耗时（以最早控制器日志起算）
    logs = glob.glob(os.path.join(LOGS, "run_*.log"))
    if logs:
        t0 = min(os.path.getmtime(p) for p in logs)
        el = now - t0
        print(f"\n  运行耗时(自最早日志): {el/3600:.2f} 小时")
    print("=" * 64)


if __name__ == "__main__":
    main()