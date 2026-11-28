# =============================================================================
# process_csv.py — 预测 CSV 关键词后处理
# 运行方式：python {or_codes}\tools\vlm\medgemma\process_csv.py
#
# 路径口径：与 predict_medgemma.py 成对，输入输出都在 mlh_SIX\results\medgemma。
#   输入   {lab_root}\pre-submit\mlh_SIX\results\medgemma\newpredictions.csv
#   输出   {lab_root}\pre-submit\mlh_SIX\results\medgemma\newpredictions_processed.csv
# =============================================================================

import csv
import re
from pathlib import Path

PROJECT_ROOT = Path(r"{lab_root}\pre-submit\mlh_SIX")
OUTPUT_ROOT  = Path(r"{lab_root}\pre-submit\mlh_SIX\results\medgemma")
IN_CSV       = OUTPUT_ROOT / "newpredictions.csv"
OUT_CSV      = OUTPUT_ROOT / "newpredictions_processed.csv"


def extract_keywords(text):
    if not text:
        return "Unknown", ""
    
    # 定义常见的诊断关键词（按重要性排序，核心疾病在前）
    keywords_pool = [
        "melanoma", "basal cell carcinoma", "squamous cell carcinoma", "skin cancer",
        "keratoacanthoma", "seborrheic keratosis", "actinic keratosis",
        "psoriasis", "ringworm", "tinea corporis", "eczema", "dermatitis", "acne",
        "cyst", "nodule", "mole", "nevus", "skin tag", "wart", "pimple", "papule", "pustule",
        "hyperpigmented lesion", "skin lesion"
    ]
    
    found = []
    text_lower = text.lower()
    
    for kw in keywords_pool:
        # 使用正则表达式匹配，确保是独立单词或短语
        if re.search(r'\b' + re.escape(kw) + r'\b', text_lower):
            found.append(kw)
            
    if not found:
        return "Unknown", ""
    
    main_diag = found[0]
    others = ", ".join(found[1:]) if len(found) > 1 else ""
    
    return main_diag, others

def process(input_file: Path = IN_CSV, output_file: Path = OUT_CSV):
    """读取预测 CSV，追加 main_diagnosis / other_keywords 两列后另存。"""
    input_file = Path(input_file)
    output_file = Path(output_file)
    if not input_file.is_file():
        raise SystemExit(f"输入 CSV 不存在: {input_file}（先跑 predict_medgemma.py）")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # 使用 gb18030 兼容大多数中文字符集，并添加 errors='replace' 防止因损坏字符崩溃
    rows = []
    try:
        with open(input_file, mode='r', encoding='gb18030', errors='replace') as infile:
            reader = csv.reader(infile)
            header = next(reader)
            new_header = header + ['main_diagnosis', 'other_keywords']

            for row in reader:
                if len(row) >= 2:
                    main, others = extract_keywords(row[1])
                    rows.append(row + [main, others])
                elif len(row) == 1:
                    rows.append(row + ["Unknown", ""])
                else:
                    continue

        with open(output_file, mode='w', encoding='utf-8', newline='') as outfile:
            writer = csv.writer(outfile)
            writer.writerow(new_header)
            writer.writerows(rows)
        print(f"处理完成，结果已保存至 {output_file}")

    except Exception as e:
        print(f"处理出错: {e}")


if __name__ == "__main__":
    process()
