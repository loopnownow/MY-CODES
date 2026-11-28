import os
import glob
import csv
import re

# ================= 配置区 (Top-level Config) =================
SOURCE_DIR = os.environ.get("SOURCE_DIR", "")
OUTPUT_CSV = 'patient_info_summary.csv'
FILE_PATTERN = '**/*.txt'
ENCODING = 'utf-8'  # 经探测，文件为 UTF-8 编码
# =============================================================

def parse_txt_content(file_path):
    """
    解析 patient_info.txt 文件内容为字典。
    文件格式示例:
    Patient Name: xxx
    Patient ID: yyy
    ...
    """
    data = {}
    try:
        with open(file_path, 'r', encoding=ENCODING, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or ':' not in line:
                    continue
                
                # 使用第一个冒号分割键和值
                parts = line.split(':', 1)
                key = parts[0].strip()
                value = parts[1].strip()
                data[key] = value
                
        # 添加文件路径信息以便追溯
        data['Source_File_Path'] = file_path
        return data
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return None

def main():
    print(f"正在搜索目录: {SOURCE_DIR}")
    
    # 获取所有匹配的文件路径
    search_path = os.path.join(SOURCE_DIR, FILE_PATTERN)
    txt_files = glob.glob(search_path, recursive=True)
    
    if not txt_files:
        print("未找到任何符合条件的文件。请检查 SOURCE_DIR。")
        return

    print(f"找到 {len(txt_files)} 个文件。正在处理...")

    all_data = []
    all_keys = set()

    for f_path in txt_files:
        parsed_data = parse_txt_content(f_path)
        if parsed_data:
            all_data.append(parsed_data)
            all_keys.update(parsed_data.keys())

    if not all_data:
        print("没有提取到有效数据。")
        return

    # 排序 keys，确保 Source_File_Path 在最后，或者根据需要调整顺序
    header = sorted(list(all_keys))
    if 'Source_File_Path' in header:
        header.remove('Source_File_Path')
        header.append('Source_File_Path')

    print(f"正在写入结果到: {OUTPUT_CSV}")
    try:
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=header)
            writer.writeheader()
            for row in all_data:
                writer.writerow(row)
        print("处理完成！")
    except Exception as e:
        print(f"写入 CSV 时出错: {e}")

if __name__ == "__main__":
    main()
