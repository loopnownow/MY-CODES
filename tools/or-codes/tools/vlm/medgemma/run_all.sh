#!/bin/bash
# =============================================================================
# run_all.sh — 完整流程一键运行脚本
# 用法：bash run_all.sh [--skip-check] [--skip-export] [--steps 0,1,2,3,4,5]
#
# 默认执行顺序：0（环境检查）→ 1（数据验证）→ 2（模型加载测试）
#               → 3（训练）→ 4（评估）→ 5（导出）
# =============================================================================

set -e   # 任何命令失败立即退出
set -u   # 使用未定义变量时报错

# ─────────────────────────────────────────────────────────────────────────────
# 路径基准：脚本本体在 _Codes，项目数据根在 E:，二者分离，故 PROJECT_ROOT 显式写死
#   SCRIPT_DIR   = {lab_root}\_Codes\tools\vlm\medgemma
#   PROJECT_ROOT = {lab_root}\pre-submit\mlh_SIX
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="/e/mlh/medgemma"
LOG_DIR="/d/MyProjects/mlh_SIX/logs"

# ─────────────────────────────────────────────────────────────────────────────
# 颜色输出
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'   # No Color（重置）

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[OK]${NC}   $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*"; }

# ─────────────────────────────────────────────────────────────────────────────
# 参数解析
# ─────────────────────────────────────────────────────────────────────────────
SKIP_CHECK=false
SKIP_EXPORT=false
RUN_STEPS="0,1,2,3,4,5"   # 默认运行全部步骤

for arg in "$@"; do
    case $arg in
        --skip-check)   SKIP_CHECK=true ;;
        --skip-export)  SKIP_EXPORT=true ;;
        --steps=*)      RUN_STEPS="${arg#--steps=}" ;;
        -h|--help)
            echo "用法: bash run_all.sh [选项]"
            echo "  --skip-check      跳过环境检查（阶段 0）"
            echo "  --skip-export     跳过导出（阶段 5）"
            echo "  --steps=3,4       只运行指定阶段（逗号分隔）"
            exit 0 ;;
    esac
done

should_run() {
    # 检查给定阶段编号是否在 RUN_STEPS 中
    local step=$1
    echo "$RUN_STEPS" | grep -q "$step"
}

# ─────────────────────────────────────────────────────────────────────────────
# 环境检查
# ─────────────────────────────────────────────────────────────────────────────
log_info "工作目录: $(pwd)"
log_info "项目根  : $PROJECT_ROOT"
log_info "Python: $(python --version 2>&1)"
log_info "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"

# 确认 conda 环境
if [ -z "${CONDA_DEFAULT_ENV:-}" ]; then
    log_warn "未检测到 conda 环境，建议先激活：conda activate medgemma-ft"
else
    log_info "当前 conda 环境: $CONDA_DEFAULT_ENV"
fi

# 创建日志目录
mkdir -p "$LOG_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# 计时辅助函数
# ─────────────────────────────────────────────────────────────────────────────
run_step() {
    local step_num="$1"
    local step_name="$2"
    local script="$3"

    if ! should_run "$step_num" && [ "$SKIP_CHECK" = "false" ]; then
        log_warn "跳过阶段 ${step_num}（不在 --steps 列表中）"
        return 0
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    log_info "阶段 ${step_num}：${step_name}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    local start_ts
    start_ts=$(date +%s)

    # 运行脚本，tee 同时输出到终端和日志文件（绝对路径，日志落在项目根 logs\）
    python "$SCRIPT_DIR/$script" 2>&1 | tee "$LOG_DIR/step${step_num}.log"
    local exit_code=${PIPESTATUS[0]}

    local end_ts
    end_ts=$(date +%s)
    local elapsed=$(( end_ts - start_ts ))

    if [ $exit_code -eq 0 ]; then
        log_success "阶段 ${step_num} 完成（耗时 ${elapsed}s）"
    else
        log_error "阶段 ${step_num} 失败（退出码: ${exit_code}）"
        log_error "查看日志: $LOG_DIR/step${step_num}.log"
        exit $exit_code
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 执行各阶段
# ─────────────────────────────────────────────────────────────────────────────

PIPELINE_START=$(date +%s)

# 阶段 0：环境检查
if [ "$SKIP_CHECK" = "false" ]; then
    run_step 0 "环境检查与依赖验证" "step0_check_env.py"
else
    log_warn "跳过环境检查（--skip-check）"
fi

# 阶段 1：数据验证
run_step 1 "数据准备与格式验证" "step1_prepare_data.py"

# 阶段 2：模型加载冒烟测试
run_step 2 "模型加载与推理验证" "step2_load_model.py"

# 阶段 3：训练
run_step 3 "LoRA 微调训练" "step3_train.py"

# 阶段 4：评估
run_step 4 "测试集评估" "step4_evaluate.py"

# 阶段 5：导出
if [ "$SKIP_EXPORT" = "false" ]; then
    run_step 5 "模型导出（GGUF）" "step5_export.py"
else
    log_warn "跳过导出（--skip-export）"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 完成汇总
# ─────────────────────────────────────────────────────────────────────────────
PIPELINE_END=$(date +%s)
TOTAL_ELAPSED=$(( PIPELINE_END - PIPELINE_START ))
TOTAL_MIN=$(( TOTAL_ELAPSED / 60 ))

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_success "全流程完成！总耗时：${TOTAL_MIN} 分钟"
echo ""
echo "  输出文件汇总:"
echo "    LoRA adapter  : /d/MyProjects/mlh_SIX/results/medgemma/medgemma-lora/"
echo "    GGUF 文件     : /d/MyProjects/mlh_SIX/results/medgemma/medgemma-gguf/"
echo "    评估报告      : $LOG_DIR/evaluation_results.json"
echo "    各步骤日志    : $LOG_DIR/step*.log"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
