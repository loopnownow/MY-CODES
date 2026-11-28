# =============================================================================
# step1_prepare_data.py — 数据准备、格式化与验证
# 运行方式：python step1_prepare_data.py
# 输入：config.py 中指定的 train.jsonl / test.jsonl
# 输出：内存中的 HuggingFace Dataset 对象（供后续步骤直接调用）
#        + 本地验证报告
# =============================================================================

import os
import re
import json
import random
from collections import Counter
from typing import Optional

from PIL import Image
from datasets import load_dataset, Dataset

import config   # 从 config.py 读取所有配置

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def build_messages(history: str, image: Image.Image, standard_output: Optional[str] = None) -> list:
    """
    构造 chat messages 列表（OpenAI messages 格式）。
    
    Args:
        history        : 病史文本字符串
        image          : 已加载的 PIL Image 对象（RGB 模式）
        standard_output: 训练时使用，推理时传 None
    
    Returns:
        messages list，格式为：
        [
            {"role": "system",    "content": str},
            {"role": "user",      "content": [{"type": "image", ...}, {"type": "text", ...}]},
            {"role": "assistant", "content": str}   # 仅训练时存在
        ]
    
    注意：
    - 图像必须放在 user content 列表中（dict with type="image"）
    - system prompt 使用 str 类型（不是 list），部分 processor 版本要求如此
    - add_generation_prompt 的值取决于是否包含 assistant 轮
    """
    user_text = config.USER_PROMPT_TEMPLATE.format(
        history=history,
        classes_str=config.CLASSES_STR
    )

    messages = [
        {
            "role": "system",
            "content": config.SYSTEM_PROMPT
        },
        {
            "role": "user",
            "content": [
                # 图像放在文本前面（符合 MedGemma 的推荐输入顺序）
                {"type": "image", "image": image},
                {"type": "text",  "text": user_text}
            ]
        }
    ]

    # 训练时加入 assistant 标准答案；推理时不加（让模型自己生成）
    if standard_output is not None:
        messages.append({
            "role": "assistant",
            "content": standard_output.strip()
        })

    return messages


def format_for_training(example: dict, processor) -> dict:
    """
    将一条原始 JSONL 记录转换为模型训练所需格式。
    
    输入字段（与 JSONL 对应）：
        image_path      : 图像文件绝对或相对路径
        history         : 病史文本
        label           : 类别标签（用于评估，训练时不直接使用）
        standard_output : assistant 侧的目标输出文本
    
    输出字段：
        text  : apply_chat_template 格式化后的字符串（含所有特殊 token）
        image : PIL Image 对象（供 UnslothVisionDataCollator 提取 pixel_values）
    
    关键说明：
    - apply_chat_template 将 messages 转成带 <start_of_turn>/<end_of_turn> 的字符串
    - tokenize=False：返回字符串而非 token ids（collator 统一处理）
    - add_generation_prompt=False：训练用，已包含完整 assistant 轮
    """
    # 加载图像，统一转 RGB（避免 RGBA、L、P 模式导致的 shape 不匹配）
    image = Image.open(example["image_path"]).convert("RGB")

    messages = build_messages(
        history=example["history"],
        image=image,
        standard_output=example["standard_output"]
    )

    # apply_chat_template 是格式化的核心步骤
    # 不同模型的 chat template 不同（Gemma 用 <start_of_turn>user\n...）
    # 必须通过 processor 调用，不能手动拼接字符串
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=False   # False = 训练模式，包含完整对话
    )

    return {
        "text": text,    # str：格式化后的完整对话文本
        "image": image,  # PIL Image：供 collator 处理为 pixel_values tensor
    }


# ─────────────────────────────────────────────────────────────────────────────
# 验证函数
# ─────────────────────────────────────────────────────────────────────────────

def validate_jsonl(filepath: str, split_name: str) -> bool:
    """
    对原始 JSONL 文件做全量验证：
    1. 文件存在性
    2. JSON 格式合法性（逐行解析）
    3. 必要字段完整性
    4. 类别合法性
    5. 图像文件可访问性（抽样 20%）
    6. standard_output 格式符合规范
    
    Returns:
        True = 验证通过，False = 存在错误
    """
    print(f"\n{'─'*40}")
    print(f"  验证 {split_name}：{filepath}")
    print(f"{'─'*40}")

    # ── 检查 1：文件存在 ──────────────────────────────────────────────────
    if not os.path.exists(filepath):
        print(f"  ❌ 文件不存在: {filepath}")
        return False

    # ── 检查 2：逐行解析 JSON ──────────────────────────────────────────────
    records = []
    parse_errors = []
    with open(filepath, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue   # 跳过空行
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                parse_errors.append(f"第 {line_no} 行: {e}")

    if parse_errors:
        print(f"  ❌ JSON 解析错误 ({len(parse_errors)} 处):")
        for err in parse_errors[:5]:
            print(f"     {err}")
        return False

    print(f"  ✅ 共 {len(records)} 条记录，JSON 格式正确")

    # ── 检查 3：必要字段 ──────────────────────────────────────────────────
    REQUIRED = {"image_path", "history", "label", "standard_output"}
    field_errors = []
    for i, row in enumerate(records):
        missing = REQUIRED - set(row.keys())
        if missing:
            field_errors.append(f"第 {i+1} 条缺少字段: {missing}")

    if field_errors:
        print(f"  ❌ 字段缺失 ({len(field_errors)} 条):")
        for err in field_errors[:5]:
            print(f"     {err}")
        return False
    print(f"  ✅ 所有记录字段完整")

    # ── 检查 4：类别合法性 ──────────────────────────────────────────────────
    label_counts = Counter(row["label"] for row in records)
    invalid_labels = {lb for lb in label_counts if lb not in config.CLASSES}

    if invalid_labels:
        print(f"  ❌ 非法类别: {invalid_labels}")
        print(f"     合法类别: {config.CLASSES}")
        return False

    print(f"  ✅ 类别分布:")
    max_count = max(label_counts.values())
    for cls in config.CLASSES:
        count = label_counts.get(cls, 0)
        bar = "█" * (count * 20 // max(max_count, 1))
        print(f"     {cls:<35} {count:>4} 条  {bar}")

    # 不平衡告警（最多/最少 > 5 倍）
    if label_counts:
        min_count = min(label_counts.values())
        ratio = max_count / max(min_count, 1)
        if ratio > 5:
            print(f"  ⚠️  类别不平衡（最大/最小 = {ratio:.1f}x），建议对少数类过采样")

    # ── 检查 5：图像文件（抽样 20%）──────────────────────────────────────
    sample_size = max(1, len(records) // 5)
    sampled = random.sample(records, sample_size)
    img_errors = []
    for row in sampled:
        path = row["image_path"]
        if not os.path.exists(path):
            img_errors.append(f"图像不存在: {path}")
        else:
            try:
                img = Image.open(path)
                img.verify()   # 验证文件完整性（不加载像素）
            except Exception as e:
                img_errors.append(f"图像损坏 {path}: {e}")

    if img_errors:
        print(f"  ❌ 图像错误（抽样 {sample_size} 张，发现 {len(img_errors)} 个问题）:")
        for err in img_errors[:5]:
            print(f"     {err}")
        return False
    print(f"  ✅ 图像抽样验证通过（{sample_size} 张）")

    # ── 检查 6：standard_output 格式 ─────────────────────────────────────
    # 期望格式：
    #   Class: <类别名>
    #   Confidence: <数字>%
    #   Reasoning: <句子>
    OUTPUT_PATTERN = re.compile(
        r"Class:\s*(.+?)\s*\n"
        r"Confidence:\s*(\d{1,3})%\s*\n"
        r"Reasoning:\s*.+",
        re.DOTALL
    )
    format_errors = []
    for i, row in enumerate(records):
        output = row["standard_output"].strip()
        if not OUTPUT_PATTERN.match(output):
            format_errors.append(i + 1)

    if format_errors:
        count = len(format_errors)
        pct = count / len(records) * 100
        print(f"  ⚠️  {count} 条 ({pct:.1f}%) standard_output 格式不标准")
        print(f"     期望格式: Class: <类>\nConfidence: <数字>%\nReasoning: <句子>")
        if pct > 20:
            print(f"  ❌ 格式错误率 > 20%，请修正数据后继续")
            return False
    else:
        print(f"  ✅ standard_output 格式全部规范")

    return True


def load_and_format_dataset(jsonl_path: str, processor, split_name: str) -> Dataset:
    """
    加载 JSONL 并格式化为训练所需的 HuggingFace Dataset。
    
    关键参数说明：
    - num_proc=1：PIL Image 不可跨进程 pickle，必须单进程
    - remove_columns：删除原始字段，只保留 text 和 image
    
    Returns:
        Dataset，每条记录包含 "text"（str）和 "image"（PIL.Image）
    """
    print(f"\n  加载 {split_name}...")
    raw = load_dataset("json", data_files=jsonl_path, split="train")
    print(f"  原始记录数: {len(raw)}")

    # 使用闭包捕获 processor（避免传参问题）
    def _format(example):
        return format_for_training(example, processor)

    # ⚠️ num_proc=1 是必须的：PIL Image 在多进程间无法序列化
    formatted = raw.map(
        _format,
        num_proc=1,
        remove_columns=raw.column_names,   # 清除所有原始列
        desc=f"格式化 {split_name}"
    )

    print(f"  ✅ {split_name} 格式化完成：{len(formatted)} 条")
    return formatted


# ─────────────────────────────────────────────────────────────────────────────
# 主函数：单独运行时用于验证数据集
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """独立验证模式：不加载模型，只验证 JSONL 文件格式"""
    print("=" * 50)
    print("  数据集验证（独立模式，不加载模型）")
    print("=" * 50)

    config.make_dirs()
    config.print_config()

    all_ok = True
    for path, name in [(config.TRAIN_JSONL, "训练集"), (config.TEST_JSONL, "测试集")]:
        ok = validate_jsonl(path, name)
        all_ok = all_ok and ok

    print("\n" + "=" * 50)
    if all_ok:
        print("  ✅ 数据验证全部通过")
        print("  → 下一步运行：python step2_load_model.py")
    else:
        print("  ❌ 数据验证失败，请修正后继续")


if __name__ == "__main__":
    main()
