import os
import re
import csv
import glob

# ================= 配置置顶 (软编码) =================
# 输入目录：包含 .txt 文件的文件夹
INPUT_DIR = os.path.join("clinnical_information", "jinshan", "CTMRIDs")
# 输出文件：生成的 .csv 文件路径
OUTPUT_FILE = os.path.join("clinnical_information", "jinshan", "ct_info.csv")

# 正则表达式说明：
# ^(\d+)                : 匹配行首的数字 ID
# \s+(.+?)              : 匹配姓名（非贪婪，处理中间可能有空格的情况）
# \s+(CT\d+)            : 匹配 CT 开头的登录号
# \s+(\d{4}-\d{2}-\d{2}): 匹配检查日期 (YYYY-MM-DD)
# \s+(.+?)              : 匹配时间描述（如：下午2点59分57秒）
# \s+(\d{17}[\dX])      : 匹配 18 位身份证号
# \s+(CT)\b             : 匹配结尾的 CT 标识
CT_PATTERN = re.compile(r"^(\d+)\s+(.+?)\s+(CT\d+)\s+(\d{4}-\d{2}-\d{2})\s+(.+?)\s+(\d{17}[\dX])\s+(CT)\b")

# CSV 表头
CSV_HEADER = ["ID", "Name", "AccessionNumber", "Date", "Time", "IDCard", "Modality"]

# 支持的文件编码尝试列表
ENCODINGS = ['utf-8', 'gbk', 'gb2312']
# =====================================================

def extract_from_file(file_path):
    """从单个文件中提取匹配的行"""
    results = []
    content = None
    
    # 尝试不同编码读取文件
    for encoding in ENCODINGS:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.readlines()
            break # 读取成功
        except (UnicodeDecodeError, Exception):
            continue
            
    if not content:
        print(f"警告: 无法读取文件 {file_path}")
        return results

    for line in content:
        line = line.strip()
        if not line:
            continue
            
        match = CT_PATTERN.match(line)
        if match:
            # 提取捕获组：ID, Name, Accession, Date, Time, IDCard, Modality
            results.append(match.groups())
            
    return results

def main():
    # 获取所有 .txt 文件
    search_path = os.path.join(INPUT_DIR, "*.txt")
    txt_files = glob.glob(search_path)
    
    if not txt_files:
        print(f"未在 {INPUT_DIR} 找到任何 .txt 文件。")
        return

    print(f"开始处理，共发现 {len(txt_files)} 个文件...")
    
    total_extracted = 0
    
    # 写入 CSV 文件
    try:
        with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(CSV_HEADER)
            
            for file_path in txt_files:
                extracted_data = extract_from_file(file_path)
                if extracted_data:
                    writer.writerows(extracted_data)
                    total_extracted += len(extracted_data)
                    
        print(f"提取完成！")
        print(f"处理文件数: {len(txt_files)}")
        print(f"提取记录数: {total_extracted}")
        print(f"结果已保存至: {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"写入 CSV 时发生错误: {e}")

if __name__ == "__main__":
    main()
