import os
import sys
import subprocess
import time
import argparse
from datetime import datetime
from pathlib import Path

# =============================================================================
# derpipline.py — 完整流程一键运行脚本 (Python 版)
# 用法：python {or_codes}\tools\vlm\medgemma\derpipline.py [--skip-check] [--skip-export] [--steps 0,1,2,3,4,5]
#
# 路径基准：脚本本体在 _Codes，项目根在 {lab_root}\pre-submit\mlh_SIX（数据在 G:\SCC\mlh_SIX\data），
# 三者分离，故 PROJECT_ROOT 显式写死。
#   SCRIPT_DIR   = {or_codes}\tools\vlm\medgemma
#   PROJECT_ROOT = {lab_root}\pre-submit\mlh_SIX
# =============================================================================

SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")
LOG_ROOT     = Path(r"{lab_root}\pre-submit\mlh_SIX\logs")
RESULT_ROOT  = Path(r"{lab_root}\pre-submit\mlh_SIX\results\medgemma")

# ─────────────────────────────────────────────────────────────────────────────
# 颜色输出工具
# ─────────────────────────────────────────────────────────────────────────────
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
BLUE = '\033[0;34m'
NC = '\033[0m'

def log_info(msg):    print(f"{BLUE}[INFO]{NC} {msg}")
def log_success(msg): print(f"{GREEN}[OK]{NC}   {msg}")
def log_warn(msg):    print(f"{YELLOW}[WARN]{NC} {msg}")
def log_error(msg):   print(f"{RED}[ERROR]{NC} {msg}")

def print_separator():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# ─────────────────────────────────────────────────────────────────────────────
# 核心执行函数
# ─────────────────────────────────────────────────────────────────────────────
def run_command_with_tee(cmd, log_path, env=None):
    """模拟 shell 的 tee 命令：同时输出到终端和文件"""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 确保子进程也使用 UTF-8
    if env is None:
        env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    with open(log_path, 'w', encoding='utf-8') as f:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            encoding='utf-8',
            errors='replace'
        )
        
        for line in iter(process.stdout.readline, ''):
            # 处理 Windows 控制台编码问题
            try:
                sys.stdout.write(line)
            except UnicodeEncodeError:
                sys.stdout.write(line.encode(sys.stdout.encoding, errors='replace').decode(sys.stdout.encoding))
            sys.stdout.flush()
            f.write(line)
            
        process.stdout.close()
        return_code = process.wait()
        return return_code

def run_step(step_num, step_name, script_name, skip_check, run_steps):
    step_num_str = str(step_num)
    
    # 检查是否在运行列表中
    if step_num_str not in run_steps and not skip_check:
        log_warn(f"跳过阶段 {step_num}（不在 --steps 列表中）")
        return True

    print_separator()
    log_info(f"阶段 {step_num}：{step_name}")
    print_separator()

    start_ts = time.time()
    
    # 脚本与本文件同在 _Codes\tools\vlm\medgemma 下；路径锚定文件位置，不依赖当前工作目录
    script_path = SCRIPT_DIR / script_name
    if not script_path.is_file():
        log_error(f"找不到脚本: {script_path}")
        return False

    log_file = LOG_ROOT / f"step{step_num}.log"
    
    # 执行命令；Python 运行时会把脚本所在目录（_Codes\tools\vlm\medgemma）自动加入 sys.path[0]，
    # 因此 step*.py 里的 import config / from step1_prepare_data import ... 仍可用
    cmd = [sys.executable, str(script_path)]
    
    exit_code = run_command_with_tee(cmd, log_file)
    
    elapsed = int(time.time() - start_ts)
    
    if exit_code == 0:
        log_success(f"阶段 {step_num} 完成（耗时 {elapsed}s）")
        return True
    else:
        log_error(f"阶段 {step_num} 失败（退出码: {exit_code}）")
        log_error(f"查看日志: {log_file}")
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="MedGemma Pipeline Control (Python Version)")
    parser.add_argument("--skip-check", action="store_true", help="跳过环境检查（阶段 0）")
    parser.add_argument("--skip-export", action="store_true", help="跳过导出（阶段 5）")
    parser.add_argument("--steps", default="0,1,2,3,4,5", help="运行指定阶段（逗号分隔，如 3,4）")
    
    args = parser.parse_args()
    
    run_steps_list = args.steps.split(",")
    
    # 环境信息
    log_info(f"工作目录: {os.getcwd()}")
    log_info(f"Python: {sys.version.split()[0]}")
    log_info(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 检测 Conda
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    if not conda_env:
        log_warn("未检测到 conda 环境，建议先激活：conda activate medgemma-ft")
    else:
        log_info(f"当前 conda 环境: {conda_env}")

    # 创建必要的目录（日志 → mlh_SIX\logs，结果 → mlh_SIX\results\medgemma）
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)

    pipeline_start = time.time()

    # 阶段定义 (编号, 名称, 脚本名)
    steps = [
        (0, "环境检查与依赖验证", "step0_check_env.py"),
        (1, "数据准备与格式验证", "step1_prepare_data.py"),
        (2, "模型加载与推理验证", "step2_load_model.py"),
        (3, "LoRA 微调训练", "step3_train.py"),
        (4, "测试集评估", "step4_evaluate.py"),
        (5, "模型导出（GGUF）", "step5_export.py"),
    ]

    for s_num, s_name, s_file in steps:
        # 特殊跳过逻辑
        if s_num == 0 and args.skip_check:
            log_warn("跳过环境检查（--skip-check）")
            continue
        if s_num == 5 and args.skip_export:
            log_warn("跳过导出（--skip-export）")
            continue
            
        success = run_step(s_num, s_name, s_file, args.skip_check, run_steps_list)
        if not success:
            sys.exit(1)

    # 完成汇总
    pipeline_end = time.time()
    total_elapsed = int(pipeline_end - pipeline_start)
    total_min = total_elapsed // 60

    print("")
    print_separator()
    log_success(f"全流程完成！总耗时：{total_min} 分钟")
    print("")
    print("  输出文件汇总:")
    print(f"    LoRA adapter  : {RESULT_ROOT / 'medgemma-lora'}")
    print(f"    GGUF 文件     : {RESULT_ROOT / 'medgemma-gguf'}")
    print(f"    评估报告      : {LOG_ROOT / 'evaluation_results.json'}")
    print(f"    各步骤日志    : {LOG_ROOT / 'step*.log'}")
    print_separator()

if __name__ == "__main__":
    # 开启 ANSI 转义支持（Windows）
    if os.name == 'nt':
        os.system('')
    main()
