# =============================================================================
# step4_evaluate.py — 测试集评估 + 混淆矩阵 + 错误分析
# 运行方式：python step4_evaluate.py
# 依赖：step3_train.py 训练完成并保存 LoRA adapter
# =============================================================================

import os
import re
import json
import time
import warnings

# Windows：conda 的 libiomp5md.dll 与 torch 自带副本重复，import torch 会以 OMP Error #15 直接退出
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import torch
from typing import Optional
from collections import defaultdict

from PIL import Image
from tqdm import tqdm
from datasets import load_dataset
from peft import PeftModel
from sklearn.metrics import (
    accuracy_score, f1_score, classification_report,
    confusion_matrix, ConfusionMatrixDisplay
)

import config
from step1_prepare_data import build_messages
from step2_load_model import load_model_and_processor
from unsloth import FastVisionModel

warnings.filterwarnings("ignore", category=UserWarning)


# ─────────────────────────────────────────────────────────────────────────────
# 加载微调后的模型（基础模型 + LoRA adapter）
# ─────────────────────────────────────────────────────────────────────────────

def load_finetuned_model(adapter_dir: str):
    """
    加载基础模型并附加训练好的 LoRA adapter。
    
    工作流程：
    1. 用 load_model_and_processor(for_training=False) 加载基础模型
    2. 用 PeftModel.from_pretrained() 在基础模型上加载 LoRA adapter
    3. FastVisionModel.for_inference() 切换为推理模式
    
    注意：
    - adapter 目录必须包含 adapter_model.safetensors（或 .bin）
      以及 adapter_config.json
    - for_training=False 会关闭 gradient_checkpointing，启用 KV cache
    
    Returns:
        (model, processor)
    """
    print(f"  加载基础模型...")
    model, processor = load_model_and_processor(for_training=False)

    print(f"  加载 LoRA adapter: {adapter_dir}")
    if not os.path.exists(adapter_dir):
        raise FileNotFoundError(
            f"LoRA adapter 目录不存在: {adapter_dir}\n"
            f"请先运行 step3_train.py 完成训练"
        )

    # PeftModel 将 LoRA adapter 权重合并到基础模型的计算图中
    # 推理时：W_effective = W_frozen + (alpha/r) × A × B
    model = PeftModel.from_pretrained(model, adapter_dir)

    # 切换推理模式：
    # - 启用 KV cache（缓存 attention key/value，加速自回归生成）
    # - 关闭 dropout（确保推理结果确定性）
    # - 优化内存布局（减少 GPU 显存碎片）
    FastVisionModel.for_inference(model)

    print(f"  ✅ 微调模型加载完成")
    return model, processor


# ─────────────────────────────────────────────────────────────────────────────
# 单样本推理
# ─────────────────────────────────────────────────────────────────────────────

def classify_single(
    model,
    processor,
    image_path: str,
    history: str
) -> dict:
    """
    对单条皮肤病变图像+病史做推理，返回完整结果字典。
    
    Returns:
        {
            "raw_response"  : str,   # 模型原始输出文本
            "predicted_class": str,  # 解析出的类别名（或 "Unknown"）
            "confidence"    : int,   # 解析出的置信度（0-100，解析失败为 -1）
            "reasoning"     : str,   # 解析出的推理说明
            "parse_success" : bool,  # 是否成功解析格式
        }
    
    关键参数说明：
    - temperature=0.1, do_sample=False → 贪心解码
      分类任务不需要多样性，低温度使输出更确定，方便格式解析
    - max_new_tokens=128 足够容纳固定格式的输出（约 30-50 tokens）
    """
    image = Image.open(image_path).convert("RGB")

    messages = build_messages(
        history=history,
        image=image,
        standard_output=None   # 推理模式，不包含答案
    )

    # add_generation_prompt=True：在末尾追加 <start_of_turn>model\n
    # 这告诉模型现在轮到它生成回复
    text = processor.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    inputs = processor(
        images=image,
        text=text,
        return_tensors="pt"
    ).to("cuda")

    with torch.no_grad():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=config.INFER_MAX_NEW_TOKENS,
            temperature=config.INFER_TEMPERATURE,
            do_sample=config.INFER_DO_SAMPLE,
        )

    # 只解码新生成的部分（排除输入 prompt 的 token）
    prompt_len = inputs["input_ids"].shape[1]
    raw_response = processor.decode(
        output_ids[0][prompt_len:],
        skip_special_tokens=True
    ).strip()

    # 解析结构化输出
    result = parse_model_output(raw_response)
    return result


def parse_model_output(response: str) -> dict:
    """
    从模型输出文本中解析结构化字段。
    
    期望格式（宽容匹配，忽略大小写和多余空格）：
        Class: <class_name>
        Confidence: <0-100>%
        Reasoning: <sentence>
    
    解析策略：
    1. 先尝试精确正则匹配
    2. 失败则逐字段尝试提取
    3. 类别名做模糊匹配（大小写/部分匹配）
    """
    result = {
        "raw_response": response,
        "predicted_class": "Unknown",
        "confidence": -1,
        "reasoning": "",
        "parse_success": False,
    }

    # ── 提取 Class ────────────────────────────────────────────────────────
    class_match = re.search(r"Class:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
    if class_match:
        raw_class = class_match.group(1).strip().rstrip(".,;")
        # 模糊匹配官方类别名（防止大小写、空格差异）
        matched_class = fuzzy_match_class(raw_class)
        result["predicted_class"] = matched_class

    # ── 提取 Confidence ───────────────────────────────────────────────────
    conf_match = re.search(r"Confidence:\s*(\d{1,3})\s*%", response, re.IGNORECASE)
    if conf_match:
        result["confidence"] = int(conf_match.group(1))

    # ── 提取 Reasoning ────────────────────────────────────────────────────
    reason_match = re.search(r"Reasoning:\s*(.+?)(?:\n|$)", response, re.IGNORECASE | re.DOTALL)
    if reason_match:
        result["reasoning"] = reason_match.group(1).strip()

    # 判断解析是否完整成功
    result["parse_success"] = (
        result["predicted_class"] != "Unknown"
        and result["confidence"] >= 0
        and len(result["reasoning"]) > 0
    )

    return result


def fuzzy_match_class(raw: str) -> str:
    """
    模糊匹配类别名。
    
    匹配策略（按优先级）：
    1. 精确匹配（大小写不敏感）
    2. 包含匹配（raw 中包含官方类别名的关键词）
    3. 反向包含（官方类别名包含 raw 的关键词）
    
    Returns:
        匹配到的官方类别名，或 "Unknown"
    """
    raw_lower = raw.lower().strip()

    # 精确匹配
    for cls in config.CLASSES:
        if cls.lower() == raw_lower:
            return cls

    # 包含匹配（从最长类别名开始，避免短名称误匹配）
    sorted_classes = sorted(config.CLASSES, key=lambda x: -len(x))
    for cls in sorted_classes:
        if cls.lower() in raw_lower or raw_lower in cls.lower():
            return cls

    # 关键词匹配（取每个类别名的关键词）
    # 例如："BCC" 匹配 "Basal Cell Carcinoma"
    keyword_map = {
        "melanoma": "Melanoma",
        "mel": "Melanoma",
        "basal": "Basal Cell Carcinoma",
        "bcc": "Basal Cell Carcinoma",
        "squamous": "Squamous Cell Carcinoma",
        "scc": "Squamous Cell Carcinoma",
        "keratosis": "Benign Keratosis",
        "benign": "Benign Keratosis",
        "actinic": "Actinic Keratosis",
        "ak": "Actinic Keratosis",
        "dermatofibroma": "Dermatofibroma",
        "df": "Dermatofibroma",
    }
    for kw, cls in keyword_map.items():
        if kw in raw_lower:
            return cls

    return "Unknown"


# ─────────────────────────────────────────────────────────────────────────────
# 批量评估
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_on_test_set(model, processor) -> dict:
    """
    在完整测试集上做批量推理，收集预测结果并计算评估指标。
    
    Returns:
        results_dict = {
            "true_labels"   : list[str],
            "pred_labels"   : list[str],
            "confidences"   : list[int],
            "parse_failures": list[int],  # 解析失败的样本索引
            "error_cases"   : list[dict], # 预测错误的样本详情
        }
    """
    print(f"\n  加载测试集: {config.TEST_JSONL}")
    test_data = load_dataset("json", data_files=config.TEST_JSONL, split="train")
    n = len(test_data)
    print(f"  测试集大小: {n} 条")

    true_labels   = []
    pred_labels   = []
    confidences   = []
    parse_failures = []
    error_cases   = []

    start_time = time.time()

    # tqdm 显示进度条
    for i, example in enumerate(tqdm(test_data, desc="推理中", ncols=80)):
        try:
            result = classify_single(
                model, processor,
                example["image_path"],
                example["history"]
            )

            true_label = example["label"]
            pred_label = result["predicted_class"]

            true_labels.append(true_label)
            pred_labels.append(pred_label)
            confidences.append(result["confidence"])

            if not result["parse_success"]:
                parse_failures.append(i)

            # 记录错误案例（用于后续错误分析）
            if pred_label != true_label:
                error_cases.append({
                    "index"       : i,
                    "image_path"  : example["image_path"],
                    "history"     : example["history"][:100] + "...",
                    "true_label"  : true_label,
                    "pred_label"  : pred_label,
                    "confidence"  : result["confidence"],
                    "raw_response": result["raw_response"],
                })

        except Exception as e:
            print(f"\n  ⚠️  样本 {i} 推理失败: {e}")
            true_labels.append(example["label"])
            pred_labels.append("Unknown")
            confidences.append(-1)
            parse_failures.append(i)

    elapsed = time.time() - start_time
    print(f"\n  推理完成，耗时 {elapsed/60:.1f} 分钟（平均 {elapsed/n:.1f}s/样本）")

    return {
        "true_labels"   : true_labels,
        "pred_labels"   : pred_labels,
        "confidences"   : confidences,
        "parse_failures": parse_failures,
        "error_cases"   : error_cases,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 报告生成
# ─────────────────────────────────────────────────────────────────────────────

def print_metrics_report(results: dict):
    """
    打印完整评估报告：
    - 格式解析统计
    - Accuracy / Macro F1 / Weighted F1
    - 分类详细报告（precision / recall / F1 / support per class）
    - 混淆矩阵（ASCII 版本，适合终端展示）
    - 高频错误模式分析
    """
    true_labels  = results["true_labels"]
    pred_labels  = results["pred_labels"]
    parse_failures = results["parse_failures"]
    error_cases  = results["error_cases"]
    n = len(true_labels)

    print("\n" + "=" * 60)
    print("  评估报告")
    print("=" * 60)

    # ── 解析失败统计 ──────────────────────────────────────────────────────
    fail_rate = len(parse_failures) / n * 100
    print(f"\n  格式解析:")
    print(f"    成功: {n - len(parse_failures)}/{n} ({100-fail_rate:.1f}%)")
    print(f"    失败: {len(parse_failures)}/{n} ({fail_rate:.1f}%)")
    if fail_rate > 10:
        print(f"    ⚠️  解析失败率 > 10%，建议检查 standard_output 格式和 prompt")

    # ── 过滤 Unknown，只对有效预测计算指标 ───────────────────────────────
    valid_idx   = [i for i, p in enumerate(pred_labels) if p != "Unknown"]
    true_valid  = [true_labels[i] for i in valid_idx]
    pred_valid  = [pred_labels[i] for i in valid_idx]
    n_valid     = len(valid_idx)

    if n_valid == 0:
        print("\n  ❌ 无有效预测，无法计算指标")
        return

    print(f"\n  有效预测数: {n_valid}/{n} ({n_valid/n*100:.1f}%)")

    # ── 主要指标 ──────────────────────────────────────────────────────────
    acc        = accuracy_score(true_valid, pred_valid)
    macro_f1   = f1_score(true_valid, pred_valid, average="macro",    zero_division=0)
    weighted_f1= f1_score(true_valid, pred_valid, average="weighted", zero_division=0)

    print(f"\n  {'─'*40}")
    print(f"  {'指标':<20} {'值':>10}")
    print(f"  {'─'*40}")
    print(f"  {'Accuracy':<20} {acc:>10.4f}  ({acc*100:.1f}%)")
    print(f"  {'Macro F1':<20} {macro_f1:>10.4f}")
    print(f"  {'Weighted F1':<20} {weighted_f1:>10.4f}")
    print(f"  {'─'*40}")

    # ── 分类详细报告 ──────────────────────────────────────────────────────
    print(f"\n  各类别详细指标:")
    report = classification_report(
        true_valid, pred_valid,
        labels=config.CLASSES,
        zero_division=0,
        output_dict=True
    )

    print(f"  {'类别':<35} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Support':>9}")
    print(f"  {'─'*72}")
    for cls in config.CLASSES:
        if cls in report:
            r = report[cls]
            flag = " ⚠️" if r["f1-score"] < 0.5 else ""
            print(
                f"  {cls:<35} "
                f"{r['precision']:>10.3f} "
                f"{r['recall']:>8.3f} "
                f"{r['f1-score']:>8.3f} "
                f"{int(r['support']):>9}"
                f"{flag}"
            )

    # ── ASCII 混淆矩阵 ────────────────────────────────────────────────────
    print(f"\n  混淆矩阵（行=真实，列=预测）:")
    cm = confusion_matrix(true_valid, pred_valid, labels=config.CLASSES)
    short_names = [cls.split()[0][:10] for cls in config.CLASSES]  # 缩短类别名

    # 打印列头
    header = "  " + " " * 15
    for name in short_names:
        header += f"{name[:8]:>9}"
    print(header)
    print(f"  {'─'*80}")

    for i, row_cls in enumerate(config.CLASSES):
        row_str = f"  {row_cls[:14]:<15}"
        for j, val in enumerate(cm[i]):
            if i == j:
                # 对角线（正确预测）加高亮
                row_str += f"\033[92m{val:>9}\033[0m"  # 绿色
            elif val > 0:
                row_str += f"\033[91m{val:>9}\033[0m"  # 红色（误分类）
            else:
                row_str += f"{val:>9}"
        print(row_str)

    # ── 高频错误分析 ──────────────────────────────────────────────────────
    if error_cases:
        print(f"\n  错误分析（共 {len(error_cases)} 个错误）:")
        
        # 统计最常见的混淆对
        confusion_pairs = defaultdict(int)
        for ec in error_cases:
            pair = (ec["true_label"], ec["pred_label"])
            confusion_pairs[pair] += 1

        sorted_pairs = sorted(confusion_pairs.items(), key=lambda x: -x[1])
        print(f"\n  最常见混淆对（真实 → 预测）:")
        for (true_cls, pred_cls), count in sorted_pairs[:5]:
            pct = count / len(error_cases) * 100
            print(f"    {true_cls:<30} → {pred_cls:<30} {count:>3} 次 ({pct:.1f}%)")

        # 打印前 3 个错误案例详情
        print(f"\n  错误案例示例（前 3 个）:")
        for ec in error_cases[:3]:
            print(f"  {'─'*50}")
            print(f"    真实类别: {ec['true_label']}")
            print(f"    预测类别: {ec['pred_label']}  (置信度: {ec['confidence']}%)")
            print(f"    病史: {ec['history']}")
            print(f"    模型输出: {ec['raw_response'][:150]}")

    print("\n" + "=" * 60)


def save_results_to_json(results: dict):
    """将完整预测结果保存到 JSON 文件，供后续分析使用"""
    output_path = os.path.join(config.LOG_DIR, "evaluation_results.json")
    os.makedirs(config.LOG_DIR, exist_ok=True)

    save_data = {
        "config": {
            "model": config.MODEL_NAME,
            "lora_dir": config.LORA_OUTPUT_DIR,
            "test_file": config.TEST_JSONL,
            "classes": config.CLASSES,
        },
        "summary": {
            "n_total": len(results["true_labels"]),
            "n_valid": sum(1 for p in results["pred_labels"] if p != "Unknown"),
            "n_parse_failures": len(results["parse_failures"]),
            "accuracy": accuracy_score(
                [t for t, p in zip(results["true_labels"], results["pred_labels"]) if p != "Unknown"],
                [p for p in results["pred_labels"] if p != "Unknown"]
            ) if any(p != "Unknown" for p in results["pred_labels"]) else 0,
        },
        "per_sample": [
            {
                "true": t,
                "pred": p,
                "confidence": c,
                "correct": t == p
            }
            for t, p, c in zip(
                results["true_labels"],
                results["pred_labels"],
                results["confidences"]
            )
        ],
        "error_cases": results["error_cases"]
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"  ✅ 详细结果已保存: {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  阶段 4：测试集评估")
    print("=" * 60)

    config.make_dirs()

    # ── 加载微调后的模型 ──────────────────────────────────────────────────
    print(f"\n[1/3] 加载微调后的模型...")
    model, processor = load_finetuned_model(config.LORA_OUTPUT_DIR)

    # ── 批量推理 ──────────────────────────────────────────────────────────
    print(f"\n[2/3] 在测试集上推理...")
    results = evaluate_on_test_set(model, processor)

    # ── 打印报告 + 保存结果 ───────────────────────────────────────────────
    print(f"\n[3/3] 生成评估报告...")
    print_metrics_report(results)
    save_results_to_json(results)

    # 给出迭代建议
    valid_preds = [p for p in results["pred_labels"] if p != "Unknown"]
    valid_true  = [t for t, p in zip(results["true_labels"], results["pred_labels"]) if p != "Unknown"]
    if valid_preds:
        macro_f1 = f1_score(valid_true, valid_preds, average="macro", zero_division=0)
        print("\n  迭代建议:")
        if macro_f1 >= 0.80:
            print(f"  ✅ Macro F1 = {macro_f1:.3f}，效果良好，可继续导出（step5_export.py）")
        elif macro_f1 >= 0.65:
            print(f"  ⚠️  Macro F1 = {macro_f1:.3f}，建议:")
            print(f"     1. 增加每类训练样本（目标每类 ≥ 100 条）")
            print(f"     2. 检查并修正标注错误")
            print(f"     3. 延长训练（MAX_STEPS 加倍或用 num_train_epochs=5）")
        else:
            print(f"  ❌ Macro F1 = {macro_f1:.3f}，建议:")
            print(f"     1. 优先检查数据质量（standard_output 格式是否 100% 一致）")
            print(f"     2. 检查图像是否与标签对应")
            print(f"     3. 简化 prompt（减少模型理解歧义）")


if __name__ == "__main__":
    main()
