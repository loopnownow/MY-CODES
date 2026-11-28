#!/usr/bin/env python3
# =============================================================================
# generate_sample_data.py — 生成合成样本数据（仅用于调试流程）
# 运行方式：python {or_codes}\tools\vlm\medgemma\generate_sample_data.py
#
# 用途：在没有真实皮肤数据时，生成合成的 JSONL 文件和占位图像，
#       用于验证整个训练/评估流程端到端可运行。
#
# 路径口径：产物写 {lab_root}\pre-submit\mlh_SIX\results\medgemma\sample\，
# 不写 G:\SCC\mlh_SIX\data（避免覆盖真实的 train.jsonl / test.jsonl）。
# 需要联调时把 config.TRAIN_JSONL / TEST_JSONL 指到 sample\ 下。
#
# 输出：
#   {lab_root}\pre-submit\mlh_SIX\results\medgemma\sample\train.jsonl
#   {lab_root}\pre-submit\mlh_SIX\results\medgemma\sample\test.jsonl
#   {lab_root}\pre-submit\mlh_SIX\results\medgemma\sample\images\*.jpg
#
# ⚠️  合成数据仅用于调试流程，不能产生有意义的分类结果
# =============================================================================

import os
import json
import random
import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import config

PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")
SAMPLE_DIR   = Path(r"{lab_root}\pre-submit\mlh_SIX\results\medgemma\sample")
TRAIN_SAMPLE = SAMPLE_DIR / "train.jsonl"
TEST_SAMPLE  = SAMPLE_DIR / "test.jsonl"
IMAGE_DIR    = SAMPLE_DIR / "images"

# ─────────────────────────────────────────────────────────────────────────────
# 类别特征定义（用于生成有区分度的合成图像颜色）
# ─────────────────────────────────────────────────────────────────────────────

CLASS_FEATURES = {
    "Melanoma": {
        "colors": [(40, 20, 20), (80, 40, 30), (60, 30, 25)],
        "description": "irregular darkly pigmented lesion with asymmetric borders",
        "histories": [
            "68-year-old male, rapidly changing mole on back, family history of melanoma.",
            "52-year-old female, dark irregular lesion on upper arm noticed 3 months ago.",
            "75-year-old male, asymmetric pigmented lesion on shoulder with color variation.",
        ],
        "confidence_range": (75, 95),
        "reasoning": "Asymmetric dark pigmentation with irregular borders and color variegation consistent with melanoma morphology.",
    },
    "Basal Cell Carcinoma": {
        "colors": [(200, 180, 170), (210, 190, 175), (190, 175, 165)],
        "description": "pearly nodule with rolled borders and central ulceration",
        "histories": [
            "65-year-old male, slowly growing pearly nodule on right cheek for 6 months.",
            "70-year-old female, shiny translucent bump on nose, occasional bleeding.",
            "78-year-old male, non-healing sore on forehead with raised rolled edges.",
        ],
        "confidence_range": (80, 95),
        "reasoning": "Pearly translucent appearance with rolled borders and telangiectasia consistent with nodular BCC.",
    },
    "Squamous Cell Carcinoma": {
        "colors": [(220, 150, 120), (200, 140, 110), (210, 145, 115)],
        "description": "scaly erythematous plaque with hyperkeratotic surface",
        "histories": [
            "72-year-old farmer, rough scaly lesion on lower lip with ulceration.",
            "68-year-old female, rapidly growing keratotic nodule on dorsal hand.",
            "80-year-old male, chronic non-healing ulcer on sun-exposed forearm skin.",
        ],
        "confidence_range": (72, 92),
        "reasoning": "Erythematous scaly plaque with indurated base and central ulceration consistent with SCC.",
    },
    "Benign Keratosis": {
        "colors": [(160, 120, 80), (150, 115, 75), (170, 125, 85)],
        "description": "well-defined stuck-on plaque with waxy surface",
        "histories": [
            "60-year-old female, multiple brown warty lesions on back present for years.",
            "65-year-old male, rough brown patch on temple, not growing, no symptoms.",
            "70-year-old female, seborrheic keratosis on face, recently irritated by clothing.",
        ],
        "confidence_range": (80, 95),
        "reasoning": "Stuck-on appearance with well-defined borders and hyperkeratotic waxy surface consistent with seborrheic keratosis.",
    },
    "Actinic Keratosis": {
        "colors": [(230, 190, 160), (220, 185, 155), (235, 195, 165)],
        "description": "rough scaly patch on sun-damaged skin",
        "histories": [
            "65-year-old outdoor worker, rough scaly patches on bald scalp for 2 years.",
            "62-year-old female, multiple erythematous scaly spots on forearms and face.",
            "73-year-old male, gritty rough patches on cheeks and nose, sun-exposed area.",
        ],
        "confidence_range": (70, 90),
        "reasoning": "Erythematous rough scaly patch on chronically sun-damaged skin consistent with actinic keratosis.",
    },
    "Dermatofibroma": {
        "colors": [(180, 140, 120), (170, 135, 115), (185, 145, 125)],
        "description": "firm dermal nodule with dimple sign",
        "histories": [
            "35-year-old female, firm button-like nodule on lower leg present for 2 years.",
            "42-year-old female, slightly pigmented dermal nodule on thigh, not growing.",
            "28-year-old male, painless firm nodule on leg appearing after insect bite.",
        ],
        "confidence_range": (78, 92),
        "reasoning": "Firm dermal nodule with characteristic dimple sign on lateral compression consistent with dermatofibroma.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# 合成图像生成
# ─────────────────────────────────────────────────────────────────────────────

def generate_synthetic_skin_image(
    class_name: str,
    size: int = 224,
    seed: int = 0
) -> Image.Image:
    """
    生成一张合成皮肤病变图像（用于调试流程，非真实数据）。
    
    不同类别使用不同的颜色主题，并添加随机纹理和形状，
    使图像具有一定区分度（但分类精度不保证）。
    """
    random.seed(seed)
    features = CLASS_FEATURES[class_name]

    # 创建皮肤色背景
    bg_color = (230, 200, 185)   # 淡粉色皮肤背景
    img = Image.new("RGB", (size, size), bg_color)
    draw = ImageDraw.Draw(img)

    # 随机选择病变颜色
    lesion_color = random.choice(features["colors"])

    # 绘制中央病变（不规则椭圆）
    cx, cy = size // 2, size // 2
    rx = int(size * random.uniform(0.20, 0.35))
    ry = int(size * random.uniform(0.18, 0.32))
    bbox = [cx - rx, cy - ry, cx + rx, cy + ry]
    draw.ellipse(bbox, fill=lesion_color)

    # 添加随机纹理点（模拟皮肤纹理）
    for _ in range(50):
        x = random.randint(0, size - 1)
        y = random.randint(0, size - 1)
        r = random.randint(1, 3)
        noise_color = tuple(max(0, min(255, c + random.randint(-20, 20))) for c in bg_color)
        draw.ellipse([x-r, y-r, x+r, y+r], fill=noise_color)

    # 黑色素瘤：添加不规则边界
    if class_name == "Melanoma":
        for i in range(5):
            ox = random.randint(-rx//2, rx//2)
            oy = random.randint(-ry//2, ry//2)
            sr = random.randint(rx//4, rx//2)
            satellite_bbox = [cx+ox-sr, cy+oy-sr, cx+ox+sr, cy+oy+sr]
            dark_color = tuple(max(0, c - 20) for c in lesion_color)
            draw.ellipse(satellite_bbox, fill=dark_color)

    # 鳞状细胞癌 / 光化性角化病：添加鳞屑纹理
    if class_name in ["Squamous Cell Carcinoma", "Actinic Keratosis"]:
        for _ in range(20):
            x = cx + random.randint(-rx, rx)
            y = cy + random.randint(-ry, ry)
            line_len = random.randint(5, 15)
            draw.line([x, y, x + line_len, y + random.randint(-3, 3)],
                      fill=(255, 255, 255), width=1)

    # 高斯模糊（柔化边界）
    img = img.filter(ImageFilter.GaussianBlur(radius=1.5))

    return img


# ─────────────────────────────────────────────────────────────────────────────
# JSONL 数据生成
# ─────────────────────────────────────────────────────────────────────────────

def generate_standard_output(class_name: str, seed: int) -> str:
    """生成符合格式要求的 standard_output"""
    random.seed(seed)
    features = CLASS_FEATURES[class_name]
    conf_min, conf_max = features["confidence_range"]
    confidence = random.randint(conf_min, conf_max)

    return (
        f"Class: {class_name}\n"
        f"Confidence: {confidence}%\n"
        f"Reasoning: {features['reasoning']}"
    )


def generate_dataset(output_jsonl: str, image_dir: str, n_per_class: int, split: str, seed: int = 42):
    """
    生成合成数据集。
    
    Args:
        output_jsonl : 输出 JSONL 文件路径
        image_dir    : 图像保存目录
        n_per_class  : 每个类别生成的样本数
        split        : "train" 或 "test"（用于文件名区分）
        seed         : 随机种子
    """
    os.makedirs(image_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_jsonl), exist_ok=True)

    records = []
    global_idx = 0

    for class_name in config.CLASSES:
        features = CLASS_FEATURES[class_name]
        histories = features["histories"]

        for i in range(n_per_class):
            sample_seed = seed + global_idx

            # 生成合成图像
            img = generate_synthetic_skin_image(class_name, size=224, seed=sample_seed)

            # 图像文件名（基于 hash 避免冲突）
            img_filename = f"{split}_{class_name.replace(' ', '_')}_{i:04d}.jpg"
            img_path = os.path.join(image_dir, img_filename)
            img.save(img_path, "JPEG", quality=90)

            # 选择病史文本（循环使用）
            history = histories[i % len(histories)]
            # 添加轻微变化（改变年龄）使每条记录不完全相同
            age_offset = (sample_seed % 20) - 10
            history = history.replace(
                history.split("-year-old")[0],
                str(int(history.split("-year-old")[0]) + age_offset)
            ) if "-year-old" in history else history

            record = {
                "image_path"      : os.path.abspath(img_path),
                "history"         : history,
                "label"           : class_name,
                "standard_output" : generate_standard_output(class_name, sample_seed),
            }
            records.append(record)
            global_idx += 1

    # 随机打乱
    random.seed(seed)
    random.shuffle(records)

    # 写入 JSONL
    with open(output_jsonl, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return len(records)


# ─────────────────────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  生成合成样本数据")
    print("=" * 50)
    print("  ⚠️  合成数据仅用于调试流程，不产生有意义的分类结果")
    print(f"  类别：{config.CLASSES_STR}")

    image_dir = str(IMAGE_DIR)

    # 生成训练集（每类 80 条 → 总计 480 条）
    print(f"\n  生成训练集...")
    n_train = generate_dataset(
        output_jsonl=str(TRAIN_SAMPLE),
        image_dir=image_dir,
        n_per_class=80,
        split="train",
        seed=3407,
    )
    print(f"  ✅ 训练集：{n_train} 条 → {TRAIN_SAMPLE}")

    # 生成测试集（每类 20 条 → 总计 120 条）
    print(f"\n  生成测试集...")
    n_test = generate_dataset(
        output_jsonl=str(TEST_SAMPLE),
        image_dir=image_dir,
        n_per_class=20,
        split="test",
        seed=1234,
    )
    print(f"  ✅ 测试集：{n_test} 条 → {TEST_SAMPLE}")

    # 验证第一条记录
    with open(TRAIN_SAMPLE, encoding="utf-8") as f:
        first = json.loads(f.readline())

    print(f"\n  样本示例（第 1 条）:")
    print(f"    image_path : {first['image_path']}")
    print(f"    label      : {first['label']}")
    print(f"    history    : {first['history'][:80]}...")
    print(f"    standard_output:")
    for line in first['standard_output'].split('\n'):
        print(f"      {line}")

    # 图像统计
    n_images = len([f for f in os.listdir(image_dir) if f.endswith(".jpg")])
    print(f"\n  生成图像数: {n_images} 张 → {image_dir}/")
    print(f"\n  ✅ 数据生成完成。联调时把 config.TRAIN_JSONL / TEST_JSONL 指到 {SAMPLE_DIR} 再跑 step0_check_env.py")


if __name__ == "__main__":
    main()
