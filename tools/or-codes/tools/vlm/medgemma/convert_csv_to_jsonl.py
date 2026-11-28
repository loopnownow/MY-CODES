# =============================================================================
# convert_csv_to_jsonl.py — CSV 清单 → 训练/测试 JSONL
# 运行方式：python {or_codes}\tools\vlm\medgemma\convert_csv_to_jsonl.py [--csv 路径]
#
# 路径口径：输入默认 {lab_root}\pre-submit\mlh_SIX\data\list.csv（只读；未提供则用 --csv 指定），
# 输出统一走 config.TRAIN_JSONL / config.TEST_JSONL（G:\SCC\mlh_SIX\data\*.jsonl）。
# =============================================================================

import argparse
import json
import os

import pandas as pd
from sklearn.model_selection import train_test_split

import config

DEFAULT_CSV = r"{lab_root}\pre-submit\mlh_SIX\data\list.csv"


def convert(csv_path: str):
    # 读取 CSV，使用 gbk 编码
    if not os.path.isfile(csv_path):
        raise SystemExit(f"CSV 不存在: {csv_path}（用 --csv 指定真实清单）")
    df = pd.read_csv(csv_path, encoding='gbk')
    
    # 打印前几行确认
    print("CSV Head:")
    print(df.head())
    
    # 类别映射确保与 config.py 一致
    # config.py 中的 CLASSES = ["Melanoma", "Basal Cell Carcinoma", "Squamous Cell Carcinoma", "Seborrheic Keratosis", "Actinic Keratosis", "Dermatofibroma"]
    
    # 构造 standard_output 模板
    def make_standard_output(diagnosis):
        return f"Class: {diagnosis}\nConfidence: 95%\nReasoning: Clinical presentation and history are consistent with {diagnosis}."

    # 转换为 JSONL 格式列表
    data_list = []
    for _, row in df.iterrows():
        # 确保路径存在且正确
        img_path = row['file_path']
        # 有时 CSV 里的路径可能包含 g:\mlh_SIX\data\...，我们需要确保脚本能找到
        
        item = {
            "image_path": img_path,
            "history": str(row['history']),
            "label": row['diagnosis'],
            "standard_output": make_standard_output(row['diagnosis'])
        }
        data_list.append(item)
    
    # 划分训练集和测试集 (80/20)
    train_data, test_data = train_test_split(data_list, test_size=0.2, random_state=42)
    
    # 确保输出目录存在（仅写 jsonl，不动图片）
    os.makedirs(os.path.dirname(config.TRAIN_JSONL), exist_ok=True)

    # 写入 JSONL
    with open(config.TRAIN_JSONL, 'w', encoding='utf-8') as f:
        for item in train_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    with open(config.TEST_JSONL, 'w', encoding='utf-8') as f:
        for item in test_data:
            f.write(json.dumps(item, ensure_ascii=False) + '\n')

    print(f"转换完成！训练集: {len(train_data)} 条 → {config.TRAIN_JSONL}")
    print(f"           测试集: {len(test_data)} 条 → {config.TEST_JSONL}")


def main():
    parser = argparse.ArgumentParser(description="CSV 清单 → 训练/测试 JSONL")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help="输入 CSV（含 file_path / history / diagnosis 列）")
    args = parser.parse_args()
    convert(args.csv)


if __name__ == "__main__":
    main()
