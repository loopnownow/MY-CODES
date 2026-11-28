# =============================================================================
# step5_export.py — 模型导出（LoRA 合并 → GGUF）
# 运行方式：python step5_export.py
# 依赖：step3_train.py 训练完成并保存 LoRA adapter
#
# 导出路径：
#   方案 A（推荐）：LoRA adapter → 合并全量模型 → GGUF → LM Studio
#   方案 B（备选）：仅保存 LoRA adapter（直接推理服务使用）
# =============================================================================

import os
import shutil
import time
import json

# Windows：conda 的 libiomp5md.dll 与 torch 自带副本重复，import torch 会以 OMP Error #15 直接退出
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch

import config
from step2_load_model import load_model_and_processor
from unsloth import FastVisionModel


# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def check_disk_space(required_gb: float, path: str = "."):
    """检查磁盘可用空间是否满足要求"""
    total, used, free = shutil.disk_usage(path)
    free_gb = free / 1e9
    if free_gb < required_gb:
        raise RuntimeError(
            f"磁盘空间不足！\n"
            f"  需要: {required_gb:.0f} GB\n"
            f"  可用: {free_gb:.1f} GB\n"
            f"  请清理磁盘后重试"
        )
    print(f"  磁盘空间: {free_gb:.1f} GB 可用（需要 {required_gb:.0f} GB）✅")


def get_dir_size(path: str) -> float:
    """计算目录总大小（GB）"""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.exists(fp):
                total += os.path.getsize(fp)
    return total / 1e9


def verify_adapter_exists():
    """确认 LoRA adapter 已训练保存"""
    adapter_dir = config.LORA_OUTPUT_DIR
    adapter_files = [
        os.path.join(adapter_dir, "adapter_model.safetensors"),
        os.path.join(adapter_dir, "adapter_model.bin"),
    ]
    config_file = os.path.join(adapter_dir, "adapter_config.json")

    found_adapter = any(os.path.exists(f) for f in adapter_files)
    found_config  = os.path.exists(config_file)

    if not found_adapter or not found_config:
        raise FileNotFoundError(
            f"LoRA adapter 不完整！\n"
            f"  目录: {adapter_dir}\n"
            f"  adapter 权重: {'✅' if found_adapter else '❌ 缺失'}\n"
            f"  adapter_config.json: {'✅' if found_config else '❌ 缺失'}\n"
            f"  请先运行 step3_train.py"
        )

    # 读取 adapter 配置并打印
    with open(config_file, encoding="utf-8") as f:
        adapter_cfg = json.load(f)
    print(f"  LoRA rank: {adapter_cfg.get('r', 'N/A')}")
    print(f"  LoRA alpha: {adapter_cfg.get('lora_alpha', 'N/A')}")
    print(f"  目标模块: {adapter_cfg.get('target_modules', 'N/A')}")
    print(f"  ✅ LoRA adapter 验证通过")


# ─────────────────────────────────────────────────────────────────────────────
# 方案 A：LoRA → 合并全量模型 → GGUF
# ─────────────────────────────────────────────────────────────────────────────

def export_merged_and_gguf(model, processor):
    """
    步骤：
    1. 将 LoRA adapter 权重合并回基础模型（得到完整权重的 safetensors 文件）
    2. 将合并后的模型导出为 GGUF 格式（可直接加载到 LM Studio）
    
    为什么需要"合并"步骤？
    ────────────────────────────────────────────────────────────────────────
    LoRA 训练后有两部分权重：
    - 基础模型权重 W（冻结，约 54GB for 27B）
    - LoRA adapter 权重 A, B（小，约 300MB）
    
    推理时计算：W_effective = W + (alpha/r) × A × B
    
    "合并"就是预先计算这个加法，得到单一的 W_merged。
    这样导出的 GGUF 是完整的、自包含的模型文件，LM Studio 可以直接加载。
    
    如果不合并直接导出 GGUF，则需要 LM Studio 同时加载基础模型和 adapter，
    目前大多数 GGUF 工具链不支持这种方式。
    ────────────────────────────────────────────────────────────────────────
    
    量化方法选择（影响文件大小和推理精度）：
    ────────────────────────────────────────────────────────────────────────
    q4_k_m : ~16GB，速度最快，精度损失约 1-2%，内存有限时使用
    q5_k_m : ~20GB，精度/速度平衡（推荐），分类任务足够
    q8_0   : ~28GB，最接近原始精度，但需要更多内存（LM Studio 可能较慢）
    f16    : ~54GB，完整精度（不推荐，GGUF 主要用于量化推理）
    ────────────────────────────────────────────────────────────────────────
    """

    # ── Step A1：合并 LoRA 到基础模型 ────────────────────────────────────
    print(f"\n  Step A1：合并 LoRA adapter → {config.MERGED_OUTPUT_DIR}")
    print(f"  注意：合并后全量模型约 54GB（bf16），需要足够磁盘空间")

    # 预检磁盘空间（合并模型约 54GB + GGUF 约 20GB = 74GB）
    check_disk_space(required_gb=80, path=".")

    t0 = time.time()

    # save_pretrained_merged 做以下操作：
    # 1. 反量化基础模型权重（4bit → bf16）
    # 2. 计算 W_merged = W + (alpha/r) × A × B（对每个 LoRA 层）
    # 3. 将 W_merged 保存为 safetensors 格式
    model.save_pretrained_merged(
        config.MERGED_OUTPUT_DIR,
        processor,
        save_method="merged_16bit",   # bf16 精度合并
        # save_method="merged_4bit"   # 保持 4bit 量化（文件更小，但精度略低）
    )

    merged_size = get_dir_size(config.MERGED_OUTPUT_DIR)
    print(f"  ✅ 合并完成，耗时 {(time.time()-t0)/60:.1f} 分钟，大小 {merged_size:.1f} GB")

    # ── Step A2：导出 GGUF ────────────────────────────────────────────────
    print(f"\n  Step A2：导出 GGUF → {config.GGUF_OUTPUT_DIR}")
    print(f"  量化方法: {config.GGUF_QUANTIZATION}")

    t0 = time.time()

    # save_pretrained_gguf 使用 llama.cpp 的 convert 工具将 safetensors 转为 GGUF
    # 并进行指定量化（k-quants 方法，比简单的 round-to-nearest 精度更好）
    model.save_pretrained_gguf(
        config.GGUF_OUTPUT_DIR,
        processor,
        quantization_method=config.GGUF_QUANTIZATION,
    )

    gguf_size = get_dir_size(config.GGUF_OUTPUT_DIR)
    print(f"  ✅ GGUF 导出完成，耗时 {(time.time()-t0)/60:.1f} 分钟，大小 {gguf_size:.1f} GB")

    # 找到 GGUF 文件路径
    gguf_files = [
        os.path.join(config.GGUF_OUTPUT_DIR, f)
        for f in os.listdir(config.GGUF_OUTPUT_DIR)
        if f.endswith(".gguf")
    ]

    if gguf_files:
        gguf_path = gguf_files[0]
        gguf_mb = os.path.getsize(gguf_path) / 1e6
        print(f"\n  GGUF 文件路径: {gguf_path}")
        print(f"  GGUF 文件大小: {gguf_mb:.0f} MB ({gguf_mb/1024:.1f} GB)")
        return gguf_path
    else:
        print("  ⚠️  未找到 .gguf 文件，请检查输出目录")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 方案 B：仅保存 LoRA adapter（用于直接推理服务）
# ─────────────────────────────────────────────────────────────────────────────

def export_adapter_only(model, processor):
    """
    仅保存 LoRA adapter 权重（不合并到基础模型）。
    
    适用场景：
    - GGUF 多模态支持有问题时的备选方案
    - 使用 vLLM / Ollama / Text Generation Inference 部署时
    - 快速测试 LoRA 效果（无需等待合并和量化）
    
    加载方式（推理服务）：
        from unsloth import FastVisionModel
        from peft import PeftModel
        
        base_model, processor = FastVisionModel.from_pretrained(
            config.MODEL_NAME, load_in_4bit=True
        )
        model = PeftModel.from_pretrained(base_model, adapter_dir)
        FastVisionModel.for_inference(model)
    """
    adapter_only_dir = os.path.join(config.LORA_OUTPUT_DIR, "adapter_only")
    os.makedirs(adapter_only_dir, exist_ok=True)

    print(f"\n  保存 LoRA adapter → {adapter_only_dir}")
    model.save_pretrained(adapter_only_dir)
    processor.save_pretrained(adapter_only_dir)

    # 计算 adapter 大小
    adapter_size = get_dir_size(adapter_only_dir)
    print(f"  ✅ LoRA adapter 已保存 ({adapter_size*1024:.0f} MB)")

    # 生成加载说明文档
    readme_content = f"""# MedGemma 27B 皮肤分类 LoRA Adapter

## 基础模型
{config.MODEL_NAME}

## 任务
皮肤肿物 6 分类：{config.CLASSES_STR}

## 加载方式

```python
from unsloth import FastVisionModel
from peft import PeftModel
import config

# 1. 加载基础模型
model, processor = FastVisionModel.from_pretrained(
    model_name="{config.MODEL_NAME}",
    load_in_4bit=True,
    max_seq_length={config.MAX_SEQ_LENGTH},
)

# 2. 加载 LoRA adapter
model = PeftModel.from_pretrained(model, "{adapter_only_dir}")

# 3. 切换推理模式
FastVisionModel.for_inference(model)

# 4. 推理（参考 step4_evaluate.py 中的 classify_single 函数）
```

## LoRA 配置
- rank: {config.LORA_R}
- alpha: {config.LORA_ALPHA}
- 视觉层微调: {config.FINETUNE_VISION}
"""
    readme_path = os.path.join(adapter_only_dir, "README.md")
    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"  加载说明已生成: {readme_path}")
    return adapter_only_dir


# ─────────────────────────────────────────────────────────────────────────────
# LM Studio 使用说明
# ─────────────────────────────────────────────────────────────────────────────

def print_lm_studio_guide(gguf_path: str):
    """打印 LM Studio 加载和使用指南"""
    print(f"""
{"="*60}
  LM Studio 使用指南
{"="*60}

  1. 打开 LM Studio，点击左侧「My Models」
  2. 点击「Add Model」→「From Local Files」
  3. 选择文件：{gguf_path}
  4. 加载完成后，在聊天界面点击图像图标上传皮肤图像
  5. 在文本框输入病史信息，格式参考：

     Patient history: 68-year-old male, 4-month nodule on right cheek.

  6. 期望输出格式：
     Class: Basal Cell Carcinoma
     Confidence: 88%
     Reasoning: ...

  注意事项：
  ⚠️  LM Studio 版本要求 ≥ 0.3.5（支持 Gemma 3 multimodal GGUF）
  ⚠️  首次加载后用一张测试图验证图像按钮可见
  ⚠️  若图像按钮不可见，说明 GGUF 视觉编码器未被识别，请用方案 B（直接推理服务）
{"="*60}
""")


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  阶段 5：模型导出")
    print("=" * 60)

    config.make_dirs()

    # ── 验证 LoRA adapter 存在 ────────────────────────────────────────────
    print(f"\n[1/4] 验证 LoRA adapter...")
    verify_adapter_exists()

    # ── 加载基础模型（推理模式，将应用 LoRA）────────────────────────────
    print(f"\n[2/4] 加载模型...")
    model, processor = load_model_and_processor(for_training=False)

    # ── 先保存 adapter only（快速备份）──────────────────────────────────
    print(f"\n[3/4] 方案 B：保存 LoRA adapter（备份）...")
    adapter_dir = export_adapter_only(model, processor)

    # ── 导出 GGUF（主要导出目标）─────────────────────────────────────────
    print(f"\n[4/4] 方案 A：合并并导出 GGUF...")

    # 确认用户要继续（GGUF 导出耗时较长）
    print(f"\n  GGUF 导出将：")
    print(f"    - 合并 LoRA adapter（约需 10~20 分钟）")
    print(f"    - 量化为 {config.GGUF_QUANTIZATION}（约需 20~40 分钟）")
    print(f"    - 占用磁盘约 80GB（合并模型 + GGUF）")

    try:
        gguf_path = export_merged_and_gguf(model, processor)

        if gguf_path:
            print_lm_studio_guide(gguf_path)
        else:
            print("\n  GGUF 导出失败，请查看上方报错信息")
            print(f"  LoRA adapter 已保存至 {adapter_dir}，可用于直接推理服务")

    except RuntimeError as e:
        # 磁盘空间不足等可预期错误
        print(f"\n  ❌ GGUF 导出失败: {e}")
        print(f"  LoRA adapter 已保存至 {adapter_dir}，可用于直接推理服务")
        print(f"  参考 {adapter_dir}/README.md 查看推理服务加载方式")

    print(f"\n  ✅ 导出阶段完成")
    print(f"\n  输出目录汇总:")
    print(f"    LoRA adapter  : {config.LORA_OUTPUT_DIR}")
    print(f"    adapter only  : {adapter_dir}")
    print(f"    合并模型      : {config.MERGED_OUTPUT_DIR}")
    print(f"    GGUF          : {config.GGUF_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
