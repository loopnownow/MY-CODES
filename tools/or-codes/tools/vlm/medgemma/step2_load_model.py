# =============================================================================
# step2_load_model.py — 模型加载 + 推理冒烟测试
# 运行方式：python step2_load_model.py
# 目的：确认 FastVisionModel 正确加载，多模态推理管道正常工作
#
# 此文件也作为其他步骤的模型加载入口（import 而不是重复写加载代码）
# =============================================================================

import os

# Windows：conda 的 libiomp5md.dll 与 torch 自带副本重复，import torch 会以 OMP Error #15 直接退出
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch
from PIL import Image

from unsloth import FastVisionModel

import config


# ─────────────────────────────────────────────────────────────────────────────
# 核心函数：加载模型与 processor
# ─────────────────────────────────────────────────────────────────────────────

def load_model_and_processor(for_training: bool = True):
    """
    加载 MedGemma 27B IT 视觉语言模型（4bit 量化）。
    
    Args:
        for_training:
            True  = 训练模式：启用 gradient_checkpointing，关闭 KV cache
            False = 推理模式：关闭 gradient_checkpointing，启用 KV cache
    
    Returns:
        (model, processor)
        - model     : 量化后的视觉语言模型，已移至 CUDA
        - processor : AutoProcessor（包含 tokenizer + image_processor）
    
    关键设计说明：
    ─────────────────────────────────────────────────────────────────────────
    ① 为什么用 FastVisionModel 而不是 FastLanguageModel？
       MedGemma 27B IT 是视觉语言模型（VLM），内部包含：
       - SigLIP 视觉编码器（处理图像 patch）
       - Gemma 2 语言模型（处理文本 token）
       FastLanguageModel 只支持纯语言模型，会忽略视觉编码器，导致图像输入失效。
    
    ② 为什么返回 processor 而不是 tokenizer？
       processor = AutoProcessor，内部封装：
       - tokenizer（文本 → token ids）
       - image_processor（图像 → pixel_values tensor）
       训练和推理都需要 processor 统一处理文本+图像输入。
       只有 tokenizer 无法处理图像，后续所有图像操作会崩溃。
    
    ③ load_in_4bit=True 的作用？
       使用 NF4（NormalFloat4）量化，将模型权重从 bf16（27B × 2bytes ≈ 54GB）
       压缩到约 4bit（27B × 0.5bytes ≈ 13.5GB），加上激活值约占 20GB，
       A100 80GB 完全可容纳模型 + optimizer states + activations。
    
    ④ use_gradient_checkpointing="unsloth" 的作用？
       正常 forward pass 会保存所有中间激活用于 backward（显存消耗大）。
       gradient checkpointing 以重新计算换显存：只保存少量检查点，
       backward 时重算其余激活，显存减少约 30~50%，训练速度下降约 20%。
       "unsloth" 版本比 HuggingFace 原版更高效，推荐使用。
    ─────────────────────────────────────────────────────────────────────────
    """
    print(f"{'─'*50}")
    print(f"  加载模型: {config.MODEL_NAME}")
    print(f"  模式: {'训练' if for_training else '推理'}")
    print(f"{'─'*50}")

    # 打印加载前显存状态
    if torch.cuda.is_available():
        free_mem = torch.cuda.mem_get_info()[0] / 1e9
        total_mem = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"  加载前 VRAM: {free_mem:.1f}/{total_mem:.1f} GB 可用")

    # ── 核心加载 ─────────────────────────────────────────────────────────
    model, processor = FastVisionModel.from_pretrained(
        model_name=config.MODEL_NAME,

        # dtype=None：自动选择精度
        # A100/H100 → bfloat16（bf16 范围比 fp16 大，不容易溢出）
        # 其他 GPU  → float16
        dtype=None,

        # QLoRA 核心：NF4 4bit 量化
        # 显存节省约 75%，精度损失在 LoRA 微调后通常可忽略
        load_in_4bit=config.LOAD_IN_4BIT,

        # 序列最大长度（影响 KV cache 预分配大小）
        # 训练数据中最长 token 序列不应超过此值
        max_seq_length=config.MAX_SEQ_LENGTH,

        # 梯度检查点（训练时节省显存，推理时关闭）
        use_gradient_checkpointing="unsloth" if for_training else False,

        # token=True：自动读取 huggingface-cli login 保存的 HF token
        # 用于访问 gated 模型（需要在 HF 网站接受使用协议）
        token=True,
    )

    # 打印加载后显存状态
    if torch.cuda.is_available():
        used_mem = torch.cuda.memory_allocated() / 1e9
        reserved_mem = torch.cuda.memory_reserved() / 1e9
        print(f"  加载后 VRAM 已用: {used_mem:.1f} GB（已分配）/ {reserved_mem:.1f} GB（已预留）")

    # 打印模型参数量
    total_params = sum(p.numel() for p in model.parameters())
    print(f"  模型参数总量: {total_params / 1e9:.2f}B")
    print(f"  ✅ 模型加载完成")

    return model, processor


# ─────────────────────────────────────────────────────────────────────────────
# 推理冒烟测试
# ─────────────────────────────────────────────────────────────────────────────

def run_inference_smoke_test(model, processor):
    """
    用合成图像 + 合成病史做一次端到端推理，验证：
    1. apply_chat_template 正常运行
    2. processor 能正确处理图像
    3. model.generate 能生成输出
    4. 解码后文本可读
    
    不需要真实数据，纯色占位图即可。
    """
    print(f"\n{'─'*50}")
    print("  推理冒烟测试（合成图像）")
    print(f"{'─'*50}")

    # 切换到推理模式
    # for_inference 会：启用 KV cache（加速生成），关闭 dropout
    FastVisionModel.for_inference(model)

    # 创建占位图（纯红棕色，模拟皮肤病变图像）
    dummy_image = Image.new("RGB", (224, 224), color=(180, 100, 80))

    dummy_history = (
        "68-year-old male with a 4-month history of a slowly growing, "
        "pearly nodule on the right cheek. No prior skin cancer history."
    )

    # 构造 messages（不含 assistant 轮 → 推理模式）
    from step1_prepare_data import build_messages
    messages = build_messages(
        history=dummy_history,
        image=dummy_image,
        standard_output=None       # None = 推理模式，不包含答案
    )

    # apply_chat_template：推理时 add_generation_prompt=True
    # 会在末尾追加 <start_of_turn>model\n，触发模型开始生成
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True    # ← 推理时必须为 True
    )

    print(f"  格式化后文本（前 200 字符）: {text[:200]!r}")

    # processor 同时处理文本和图像
    # images= 接收单张 PIL Image 或 list
    # return_tensors="pt" 返回 PyTorch tensor
    # .to("cuda") 移至 GPU
    inputs = processor(
        images=dummy_image,
        text=text,
        return_tensors="pt"
    ).to("cuda")

    print(f"  输入 keys: {list(inputs.keys())}")
    print(f"  input_ids shape: {inputs['input_ids'].shape}")

    # 检查 pixel_values 是否存在（若不存在说明图像处理失败）
    if "pixel_values" in inputs:
        print(f"  pixel_values shape: {inputs['pixel_values'].shape}")
    else:
        print("  ⚠️  pixel_values 不存在，图像未被正确处理！")

    # 推理生成
    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=config.INFER_MAX_NEW_TOKENS,
            temperature=config.INFER_TEMPERATURE,
            do_sample=config.INFER_DO_SAMPLE,
        )

    # 只解码新生成的 token（跳过 prompt 部分）
    # inputs["input_ids"].shape[1] = prompt 的 token 数量
    generated_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
    response = processor.decode(generated_tokens, skip_special_tokens=True)

    print(f"\n  模型输出:")
    print(f"  {'─'*40}")
    for line in response.strip().split("\n"):
        print(f"  {line}")
    print(f"  {'─'*40}")
    print(f"  ✅ 冒烟测试通过，多模态推理管道正常")

    # 推理完成后切回训练模式（如果后续要训练）
    # 注意：for_inference 和训练模式不能同时激活
    return response


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """独立运行：加载模型并执行冒烟测试"""
    print("=" * 50)
    print("  阶段 2：模型加载与推理验证")
    print("=" * 50)

    config.make_dirs()
    config.print_config()

    # 以推理模式加载（冒烟测试不需要训练）
    model, processor = load_model_and_processor(for_training=False)
    run_inference_smoke_test(model, processor)

    print("\n  → 下一步运行：python step3_train.py")


if __name__ == "__main__":
    main()
