# =============================================================================
# config.py — 全局配置文件
# 所有路径、超参数、类别定义均集中于此，修改时只需改这一个文件
# =============================================================================

import os
from pathlib import Path

# -----------------------------------------------------------------------------
# 模型配置
# -----------------------------------------------------------------------------
MODEL_NAME = "unsloth/medgemma-27b-it"
# 若想用官方版本（未经 unsloth 优化），改为：
# MODEL_NAME = "google/medgemma-27b-it"
# 本地权重在 E:\_models\medgemma-1.5-4b-it（google/medgemma-1.5-4b-it 快照，4B，约 8.6 GB），与 27B 的
# 显存预算和 LoRA 参数不通用；确需离线跑小模型时再改为：
# MODEL_NAME = str(MODEL_DIR)

# -----------------------------------------------------------------------------
# 路径配置（脚本本体在 _Codes，项目数据根在 E:，二者分离）
# -----------------------------------------------------------------------------
# 基准目录
PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")           # 项目根（数据在 G:\SCC\mlh_SIX\data，权重在 E:\_models）
MODEL_DIR    = Path(os.environ.get("MODEL_DIR", ""))    # 本地权重快照
DATA_DIR     = Path(os.environ.get("DATA_DIR", ""))                 # 只读输入（处理盘）：train.jsonl / test.jsonl / 图片

# 数据文件路径
TRAIN_JSONL = str(DATA_DIR / "train.jsonl")          # 训练集
TEST_JSONL  = str(DATA_DIR / "test.jsonl")           # 测试集

# 输出目录（模型产物 → {lab_root}\pre-submit\mlh_SIX\results\medgemma）
RESULTS_ROOT       = Path(r"{lab_root}\pre-submit\mlh_SIX\results\medgemma")
OUTPUT_ROOT        = RESULTS_ROOT
LORA_OUTPUT_DIR    = str(OUTPUT_ROOT / "medgemma-lora")     # LoRA adapter 保存路径
MERGED_OUTPUT_DIR  = str(OUTPUT_ROOT / "medgemma-merged")   # 合并后全量模型路径
GGUF_OUTPUT_DIR    = str(OUTPUT_ROOT / "medgemma-gguf")     # GGUF 文件路径

# 日志目录（各步骤日志、evaluation_results.json）
LOG_DIR = str(Path(r"{lab_root}\pre-submit\mlh_SIX\logs"))

# -----------------------------------------------------------------------------
# 皮肤肿物 6 分类——替换为你实际使用的类别名（必须与 JSONL 中 label 字段完全一致）
# -----------------------------------------------------------------------------
CLASSES = [
    "Melanoma",
    "Basal Cell Carcinoma",
    "Squamous Cell Carcinoma",
    "Seborrheic Keratosis",
    "Actinic Keratosis",
    "Dermatofibroma",
]
CLASSES_STR = ", ".join(CLASSES)   # 逗号分隔字符串，用于 prompt

# -----------------------------------------------------------------------------
# 模型加载配置
# -----------------------------------------------------------------------------
LOAD_IN_4BIT      = True      # QLoRA：4bit NF4 量化，节省约 75% VRAM
MAX_SEQ_LENGTH    = 4096      # 序列最大长度；若病史文本极长可调至 6144
DTYPE             = None      # None = 自动选择（A100 自动用 bf16）

# -----------------------------------------------------------------------------
# LoRA 超参数
# -----------------------------------------------------------------------------
LORA_R              = 16      # LoRA rank；数据 < 500 条用 8，> 1000 条可试 32
LORA_ALPHA          = 16      # 缩放因子；通常 = r 或 2*r
LORA_DROPOUT        = 0       # QLoRA 原论文推荐 0
FINETUNE_VISION     = True    # 是否对视觉编码器（SigLIP）也加 LoRA
FINETUNE_LANGUAGE   = True    # 是否对语言模型层加 LoRA（几乎总是 True）
FINETUNE_ATTENTION  = True    # 是否对注意力模块加 LoRA
FINETUNE_MLP        = True    # 是否对 MLP 模块加 LoRA
RANDOM_SEED         = 3407    # 全局随机种子

# -----------------------------------------------------------------------------
# 训练超参数
# -----------------------------------------------------------------------------
PER_DEVICE_BATCH_SIZE       = 1       # A100 80GB + 4bit + r=16 安全值
GRADIENT_ACCUMULATION_STEPS = 8       # 有效 batch = 1 × 8 = 8
WARMUP_STEPS                = 20      # 线性预热步数（约为总步数的 5~10%）
MAX_STEPS                   = 300     # 小步验证；全量训练改为 -1，用 NUM_EPOCHS
NUM_TRAIN_EPOCHS            = 3       # 全量训练时使用（MAX_STEPS=-1 时生效）
LEARNING_RATE               = 2e-4    # QLoRA 经典范围：1e-4 ~ 3e-4
BF16                        = True    # A100 原生 bfloat16（比 fp16 更稳定）
OPTIMIZER                   = "adamw_8bit"  # 8bit AdamW，节省优化器状态显存
LOGGING_STEPS               = 10      # 每 N 步打印一次 loss
SAVE_STEPS                  = 100     # 每 N 步保存一次 checkpoint
SAVE_TOTAL_LIMIT            = 3       # 最多保留 N 个 checkpoint（防磁盘爆满）
DATALOADER_NUM_WORKERS      = 2       # 数据加载并行线程数

# -----------------------------------------------------------------------------
# 推理配置（评估阶段使用）
# -----------------------------------------------------------------------------
INFER_MAX_NEW_TOKENS = 128    # 输出格式固定，128 token 足够
INFER_TEMPERATURE    = 0.1    # 低温度使分类输出更确定
INFER_DO_SAMPLE      = False  # 贪心解码（分类任务推荐）

# -----------------------------------------------------------------------------
# 导出配置
# -----------------------------------------------------------------------------
# 量化方法：q4_k_m（最小/最快）| q5_k_m（推荐平衡）| q8_0（最高精度）
GGUF_QUANTIZATION = "q5_k_m"

# -----------------------------------------------------------------------------
# System / User prompt 模板
# -----------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "You are an expert dermatologist with 20 years of clinical experience "
    "in skin lesion classification. Analyze both the clinical image and "
    "patient history carefully before making a diagnosis."
)

# {history} 和 {classes_str} 是占位符，在运行时替换
USER_PROMPT_TEMPLATE = (
    "Patient history: {history}\n\n"
    "Classify this skin lesion image into exactly ONE of the following 6 classes:\n"
    "Classes: {classes_str}\n\n"
    "Respond strictly in this format (do not add anything else):\n"
    "Class: <class_name>\n"
    "Confidence: <0-100>%\n"
    "Reasoning: <one concise sentence explaining key visual and clinical features>"
)

# -----------------------------------------------------------------------------
# 内部工具函数
# -----------------------------------------------------------------------------
def make_dirs():
    """创建所有输出目录（若不存在则自动创建）。不创建数据输入目录。"""
    for d in [LORA_OUTPUT_DIR, MERGED_OUTPUT_DIR, GGUF_OUTPUT_DIR, LOG_DIR]:
        os.makedirs(d, exist_ok=True)

def print_config():
    """打印当前配置摘要，便于 debug"""
    print("=" * 60)
    print("当前配置摘要")
    print("=" * 60)
    print(f"  模型       : {MODEL_NAME}")
    print(f"  训练集     : {TRAIN_JSONL}")
    print(f"  测试集     : {TEST_JSONL}")
    print(f"  类别数     : {len(CLASSES)}")
    print(f"  LoRA rank  : {LORA_R}")
    print(f"  Batch size : {PER_DEVICE_BATCH_SIZE} × {GRADIENT_ACCUMULATION_STEPS} (有效={PER_DEVICE_BATCH_SIZE * GRADIENT_ACCUMULATION_STEPS})")
    print(f"  Max steps  : {MAX_STEPS} ({'全量训练用 epochs' if MAX_STEPS == -1 else '小步测试'})")
    print(f"  Learning rate: {LEARNING_RATE}")
    print(f"  GGUF 量化  : {GGUF_QUANTIZATION}")
    print("=" * 60)
