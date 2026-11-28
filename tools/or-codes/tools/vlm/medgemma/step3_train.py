# =============================================================================
# step3_train.py — LoRA 配置 + 完整训练流程
# 运行方式：python step3_train.py
# 依赖：step1_prepare_data.py（数据格式化）
#        step2_load_model.py（模型加载）
# =============================================================================

import os
import time
import math

# Windows：conda 的 libiomp5md.dll 与 torch 自带副本重复，import torch 会以 OMP Error #15 直接退出
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch

from unsloth import FastVisionModel
from unsloth.trainer import UnslothVisionDataCollator   # ← 多模态训练的核心 collator
from trl import SFTTrainer
from transformers import TrainingArguments

import config
from step1_prepare_data import load_and_format_dataset, validate_jsonl
from step2_load_model import load_model_and_processor


# ─────────────────────────────────────────────────────────────────────────────
# LoRA 配置
# ─────────────────────────────────────────────────────────────────────────────

def apply_lora(model):
    """
    在基础模型上附加 LoRA adapter。
    
    LoRA 原理简述：
    ─────────────────────────────────────────────────────────────────────────
    传统微调：直接修改所有权重 W（显存 + 计算量巨大）
    LoRA    ：冻结原始权重 W，在旁路添加低秩矩阵 ΔW = A × B
              其中 A ∈ R^(d×r)，B ∈ R^(r×k)，r << min(d,k)
              推理时：W_new = W + (lora_alpha/r) × A × B
              可训练参数量减少约 99%
    
    参数说明：
    ─────────────────────────────────────────────────────────────────────────
    finetune_vision_layers:
        True  = 对视觉编码器（SigLIP）也加 LoRA，适合图像特征需要大幅适配的任务
        False = 只训语言层，显存更少，速度更快，适合图像质量已很好的情况
        推荐：True（皮肤病变分类高度依赖视觉特征）
    
    finetune_language_layers / finetune_attention_modules / finetune_mlp_modules:
        控制语言模型中哪些子模块参与 LoRA，全部 True = 最完整的适配能力
    
    r（rank）：
        LoRA 的秩，控制可训练参数量：
        r=8  → 约 20M 可训练参数，适合数据 < 300 条
        r=16 → 约 40M，平衡选择（推荐起点）
        r=32 → 约 80M，数据 > 1000 条时考虑
    
    lora_alpha：
        缩放系数，实际学习率缩放 = lora_alpha / r
        alpha=16, r=16 → 缩放 = 1.0（无额外缩放）
        alpha=32, r=16 → 缩放 = 2.0（相当于增大 LoRA 权重的学习率）
    
    lora_dropout=0：
        QLoRA 原论文发现 dropout=0 在微调阶段效果更稳定
        数据量很小（< 100 条）时可尝试 0.05~0.1
    
    bias="none"：
        不对 bias 参数加 LoRA，减少参数量，通常不影响效果
    ─────────────────────────────────────────────────────────────────────────
    """
    print(f"\n{'─'*50}")
    print("  配置 LoRA Adapter")
    print(f"{'─'*50}")
    print(f"  rank={config.LORA_R}, alpha={config.LORA_ALPHA}, dropout={config.LORA_DROPOUT}")
    print(f"  视觉层: {config.FINETUNE_VISION}, 语言层: {config.FINETUNE_LANGUAGE}")
    print(f"  注意力模块: {config.FINETUNE_ATTENTION}, MLP 模块: {config.FINETUNE_MLP}")

    model = FastVisionModel.get_peft_model(
        model,
        finetune_vision_layers=config.FINETUNE_VISION,
        finetune_language_layers=config.FINETUNE_LANGUAGE,
        finetune_attention_modules=config.FINETUNE_ATTENTION,
        finetune_mlp_modules=config.FINETUNE_MLP,
        r=config.LORA_R,
        lora_alpha=config.LORA_ALPHA,
        lora_dropout=config.LORA_DROPOUT,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=config.RANDOM_SEED,
    )

    # 统计并打印可训练参数量
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    frozen = total - trainable
    print(f"\n  参数统计:")
    print(f"    可训练（LoRA）: {trainable / 1e6:.1f}M  ({trainable/total*100:.2f}%)")
    print(f"    冻结（原始权重）: {frozen / 1e9:.2f}B  ({frozen/total*100:.2f}%)")
    print(f"  ✅ LoRA 配置完成")

    return model


# ─────────────────────────────────────────────────────────────────────────────
# 训练器配置
# ─────────────────────────────────────────────────────────────────────────────

def build_trainer(model, processor, train_dataset):
    """
    构建 SFTTrainer（监督微调训练器）。
    
    关键设计说明：
    ─────────────────────────────────────────────────────────────────────────
    ① 为什么 tokenizer= 传 processor 而非 tokenizer？
       SFTTrainer 内部会调用 tokenizer 的方法处理输入。
       对于视觉语言模型，必须传 processor（包含图像处理能力）。
       只传 tokenizer 会导致图像 token 无法被正确处理。
    
    ② 为什么需要 UnslothVisionDataCollator？
       默认的 DataCollator 只处理文本 tensor（input_ids, attention_mask 等）。
       视觉模型还需要将图像转为 pixel_values tensor 并与文本 batch 对齐。
       UnslothVisionDataCollator 做了以下工作：
       - 调用 processor 提取 pixel_values
       - 将 input_ids 和 pixel_values 对齐为统一 batch
       - 自动将 assistant 部分以外的 token label 设为 -100
         （-100 是 PyTorch 交叉熵的 ignore_index，只对 assistant 回复计算 loss）
    
    ③ dataset_text_field="text" 而非 "messages"？
       SFTTrainer 期望一个包含已格式化字符串的字段名。
       step1 中 format_for_training 已通过 apply_chat_template 生成了 "text" 字段。
       若传 "messages"（字典列表字段），SFTTrainer 无法解析，图像会丢失。
    
    ④ packing=False 对视觉数据是必须的
       packing=True 会将多条短样本拼接成一条长序列（提升 GPU 利用率）。
       但对于多模态数据，每条样本的图像数量和 token 数不固定，
       拼接后图像与文本的对应关系会错乱，导致训练错误。
    
    ⑤ 有效 batch size = per_device_batch_size × gradient_accumulation_steps
       由于显存限制，per_device_batch_size=1（每次只处理 1 张图+对应文本）。
       gradient_accumulation_steps=8 意味着累积 8 步的梯度后才更新一次权重，
       等效于 batch_size=8 的训练效果，但显存峰值只需 batch_size=1。
    
    ⑥ 学习率调度
       HuggingFace 默认使用 linear warmup + cosine decay。
       warmup_steps 期间学习率从 0 线性增至 learning_rate，
       之后余弦衰减，防止训练初期梯度过大导致的 loss 震荡。
    ─────────────────────────────────────────────────────────────────────────
    """
    print(f"\n{'─'*50}")
    print("  构建训练器")
    print(f"{'─'*50}")

    training_args = TrainingArguments(
        # 每设备 batch size（每步处理的样本数）
        per_device_train_batch_size=config.PER_DEVICE_BATCH_SIZE,

        # 梯度累积步数
        # 有效 batch = per_device_batch_size × gradient_accumulation_steps
        gradient_accumulation_steps=config.GRADIENT_ACCUMULATION_STEPS,

        # 线性预热步数：防止训练初期 loss 剧烈震荡
        warmup_steps=config.WARMUP_STEPS,

        # 训练步数控制（二选一）
        # max_steps > 0：按步数训练（小步验证时使用）
        # max_steps = -1 + num_train_epochs：按 epoch 训练（全量训练时使用）
        max_steps=config.MAX_STEPS,
        # num_train_epochs=config.NUM_TRAIN_EPOCHS,  # 全量训练时取消注释，注释 max_steps

        # 学习率
        learning_rate=config.LEARNING_RATE,

        # bfloat16 精度（A100/H100 原生支持，比 fp16 数值更稳定）
        bf16=config.BF16,
        fp16=not config.BF16,   # 不支持 bf16 的 GPU 自动回退到 fp16

        # 日志频率（每 N 步打印一次 loss）
        logging_steps=config.LOGGING_STEPS,

        # 日志保存路径
        logging_dir=config.LOG_DIR,

        # checkpoint 保存目录
        output_dir=config.LORA_OUTPUT_DIR,

        # 8bit AdamW 优化器
        # 相比 fp32 AdamW，优化器状态（m, v）从 float32 压缩到 int8
        # 显存节省约 75%（optimizer states 通常约占 12GB for 3B model）
        optim=config.OPTIMIZER,

        # 每 N 步保存一次 checkpoint
        save_strategy="steps",
        save_steps=config.SAVE_STEPS,

        # 最多保留 N 个最新 checkpoint（防止磁盘爆满）
        save_total_limit=config.SAVE_TOTAL_LIMIT,

        # 不上报到 wandb/tensorboard（改为 "wandb" 并设置 WANDB_API_KEY 可启用）
        report_to="none",

        # 数据加载并行线程数
        dataloader_num_workers=config.DATALOADER_NUM_WORKERS,

        # 固定随机种子
        seed=config.RANDOM_SEED,

        # 在训练结束时自动加载最优 checkpoint（需要设置 metric_for_best_model）
        # load_best_model_at_end=True,  # 需要验证集才能启用
    )

    trainer = SFTTrainer(
        model=model,

        # ⚠️ 传 processor 而非 tokenizer（包含图像处理能力）
        tokenizer=processor,

        train_dataset=train_dataset,

        # 指向 format_for_training 输出的 "text" 字段（已格式化的字符串）
        dataset_text_field="text",

        # ⚠️ 视觉训练必须使用此 collator（处理图像 + 文本的 batch 组装）
        data_collator=UnslothVisionDataCollator(model, processor),

        max_seq_length=config.MAX_SEQ_LENGTH,

        # packing=False：视觉数据不适合样本拼接
        packing=False,

        args=training_args,
    )

    print(f"  有效 batch size: {config.PER_DEVICE_BATCH_SIZE * config.GRADIENT_ACCUMULATION_STEPS}")
    print(f"  训练步数: {config.MAX_STEPS if config.MAX_STEPS > 0 else '按 epoch'}")
    print(f"  ✅ 训练器构建完成")

    return trainer


# ─────────────────────────────────────────────────────────────────────────────
# 训练监控
# ─────────────────────────────────────────────────────────────────────────────

def monitor_vram():
    """打印当前 VRAM 使用情况"""
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1e9
        reserved = torch.cuda.memory_reserved() / 1e9
        total = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"  VRAM: {allocated:.1f}GB 已用 / {reserved:.1f}GB 已预留 / {total:.1f}GB 总量")


def estimate_training_time(trainer, dataset_size: int) -> str:
    """估算总训练时长"""
    steps = config.MAX_STEPS if config.MAX_STEPS > 0 else math.ceil(
        dataset_size / (config.PER_DEVICE_BATCH_SIZE * config.GRADIENT_ACCUMULATION_STEPS)
    ) * config.NUM_TRAIN_EPOCHS
    # 粗估：A100 上每步约 15~30 秒（含图像处理）
    est_sec = steps * 20
    if est_sec < 3600:
        return f"约 {est_sec/60:.0f} 分钟"
    else:
        return f"约 {est_sec/3600:.1f} 小时"


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  阶段 3：LoRA 微调训练")
    print("=" * 50)

    config.make_dirs()
    config.print_config()

    # ── Step 1：验证数据集 ────────────────────────────────────────────────
    print(f"\n[1/5] 验证数据集...")
    ok = validate_jsonl(config.TRAIN_JSONL, "训练集")
    if not ok:
        print("  ❌ 数据验证失败，终止训练")
        return

    # ── Step 2：加载模型（训练模式）──────────────────────────────────────
    print(f"\n[2/5] 加载模型...")
    model, processor = load_model_and_processor(for_training=True)
    monitor_vram()

    # ── Step 3：配置 LoRA ─────────────────────────────────────────────────
    print(f"\n[3/5] 配置 LoRA...")
    model = apply_lora(model)
    monitor_vram()

    # ── Step 4：格式化数据集 ──────────────────────────────────────────────
    print(f"\n[4/5] 格式化训练数据...")
    train_dataset = load_and_format_dataset(config.TRAIN_JSONL, processor, "训练集")
    train_dataset = train_dataset.shuffle(seed=config.RANDOM_SEED)
    print(f"  训练集大小: {len(train_dataset)} 条")
    print(f"  {estimate_training_time(None, len(train_dataset))}")

    # ── Step 5：构建训练器 + 开始训练 ────────────────────────────────────
    print(f"\n[5/5] 开始训练...")
    trainer = build_trainer(model, processor, train_dataset)

    # 打印训练前 VRAM（最高峰在第一个 forward+backward pass）
    print("\n  训练前状态:")
    monitor_vram()

    t0 = time.time()
    print("\n  🚀 训练开始\n")

    trainer_stats = trainer.train()

    elapsed = time.time() - t0
    print(f"\n  ✅ 训练完成")
    print(f"  总时长: {elapsed/60:.1f} 分钟")
    print(f"  最终 loss: {trainer_stats.metrics.get('train_loss', 'N/A'):.4f}")
    print(f"  平均每步时长: {trainer_stats.metrics.get('train_runtime', 0) / max(trainer_stats.metrics.get('train_steps_per_second', 1) * trainer_stats.metrics.get('train_runtime', 1), 1):.1f}s")

    # 打印训练后 VRAM
    print("\n  训练后状态:")
    monitor_vram()

    # ── 保存 LoRA Adapter ─────────────────────────────────────────────────
    print(f"\n  保存 LoRA adapter → {config.LORA_OUTPUT_DIR}")
    model.save_pretrained(config.LORA_OUTPUT_DIR)
    # ⚠️ 保存 processor（含图像处理配置），不仅是 tokenizer
    processor.save_pretrained(config.LORA_OUTPUT_DIR)

    # 验证保存成功
    adapter_file = os.path.join(config.LORA_OUTPUT_DIR, "adapter_model.safetensors")
    if os.path.exists(adapter_file):
        size_mb = os.path.getsize(adapter_file) / 1e6
        print(f"  ✅ adapter_model.safetensors ({size_mb:.1f} MB) 保存成功")
    else:
        # 某些版本使用 .bin 格式
        bin_file = os.path.join(config.LORA_OUTPUT_DIR, "adapter_model.bin")
        if os.path.exists(bin_file):
            size_mb = os.path.getsize(bin_file) / 1e6
            print(f"  ✅ adapter_model.bin ({size_mb:.1f} MB) 保存成功")
        else:
            print("  ⚠️  未找到 adapter 文件，请检查输出目录")

    print(f"\n  → 下一步运行：python step4_evaluate.py")


if __name__ == "__main__":
    main()
