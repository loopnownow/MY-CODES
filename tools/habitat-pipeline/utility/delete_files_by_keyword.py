import os
import argparse
import csv
import sys
import re

# ================= 配置区域 (软编码) =================
# 1. 目标目录路径
TARGET_DIR = os.environ.get("HABITAT_DELETE_TARGET", "results/checkpoints")

# 2. 报错记录文件路径 (用于读取 Patient_ID)
ERR_CSV_PATH = os.environ.get("HABITAT_DELETE_ERR_CSV", "out/cac_err.csv")

# 3. 关键词设置 (针对文件名的第二层过滤)
# 只有同时包含 Patient_ID 且包含以下任一关键词的文件才会被处理
DEFAULT_KEYWORDS = ("aligned", "habitat", "features")  # "aligned", "habitat", "features"

# 4. 运行模式设置
# True: 模拟运行(仅打印列表) | False: 实际执行删除
DEFAULT_DRY_RUN = True
# ====================================================

def load_patient_ids(csv_path):
    """从 CSV 文件加载 Patient_ID 列表并去重"""
    if not os.path.exists(csv_path):
        print(f"警告: 报错记录文件 '{csv_path}' 不存在。")
        return []
    
    patient_ids = []
    try:
        # 使用 utf-8-sig 以自动剥离文件开头的 BOM (\ufeff)。
        # 若用普通 'utf-8' 打开带 BOM 的 CSV，第一列表头会变成 '\ufeffPatient_ID'，
        # 导致 row.get('Patient_ID') 恒为 None，从而永远加载不到任何 ID。
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:

            reader = csv.DictReader(f)
            for row in reader:
                pid = row.get('Patient_ID')
                if pid:
                    patient_ids.append(pid.strip())
    except Exception as e:
        print(f"错误: 无法读取 CSV 文件: {e}")
    
    unique_ids = list(set(patient_ids))
    print(f"成功从 CSV 加载了 {len(unique_ids)} 个报错患者 ID。")
    return unique_ids

def delete_files_with_keywords(directory, keywords, patient_ids=None, dry_run=True):
    """
    双重过滤删除逻辑：
    1. 必须包含 keywords 中的任一关键词。
    2. 如果提供了 patient_ids，则文件名也必须包含其中任一 ID。
    """
    if not os.path.exists(directory):
        print(f"错误: 目录 '{directory}' 不存在。")
        return

    # 统一转换为元组/列表处理
    if isinstance(keywords, str):
        keywords = (keywords,)
    keywords = [k for k in keywords if k]

    if not keywords:
        print("错误: 未指定关键词，操作已取消。")
        return

    mode_str = "模拟" if dry_run else "实际"
    print(f"【{mode_str}运行模式】")
    print(f"目标目录: {directory}")
    print(f"关键词过滤: {keywords}")
    if patient_ids:
        print(f"患者 ID 过滤: {len(patient_ids)} 个 ID 待匹配")
    print("-" * 40)

    count = 0
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if not os.path.isfile(file_path):
            continue
            
        # 匹配逻辑：(文件名含任一关键词) AND (如果没有ID列表 OR 文件名含任一ID)
        keyword_match = any(k in filename for k in keywords)
        patient_match = True
        # 注意: 必须用 "is not None" 而非直接判断真值。
        # 当 patient_ids 为空列表 [] 时 (例如 CSV 读取失败/无有效ID)，
        # 应视为"仍需过滤但无任何ID可匹配"，从而 any([]) 恒为 False，不匹配任何文件（安全）。
        # 若写成 `if patient_ids:`，空列表会被当作 False，导致跳过过滤、退化为仅按关键词匹配（危险）。
        if patient_ids is not None:
            patient_match = any(
                re.search(rf"(?<![A-Za-z0-9]){re.escape(pid)}(?![A-Za-z0-9])", filename)
                for pid in patient_ids
            )

        
        if keyword_match and patient_match:
            if dry_run:
                print(f"[待删除] {filename}")
            else:
                try:
                    os.remove(file_path)
                    print(f"[已删除] {filename}")
                except Exception as e:
                    print(f"[失败] 无法删除 {filename}: {e}")
            count += 1

    print("-" * 40)
    if dry_run:
        print(f"模拟运行结束。找到 {count} 个匹配文件。")
        if count > 0:
            print("提示: 若要实际执行，请修改脚本顶部 DEFAULT_DRY_RUN = False 或使用 --execute 参数。")
    else:
        print(f"操作完成。共处理 {count} 个文件。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="删除指定目录下带有关键词及 Patient_ID 的文件")
    parser.add_argument("keyword", nargs="*", help="命令行传入关键词 (可选，若不传则使用脚本默认设置)")
    parser.add_argument("--dir", default=TARGET_DIR, help=f"目标目录 (默认: {TARGET_DIR})")
    parser.add_argument("--execute", action="store_true", help="实际执行删除操作 (覆盖默认设置)")
    parser.add_argument("--no_id", action="store_true", help="忽略 Patient_ID 过滤，仅按关键词清理")

    args = parser.parse_args()

    # 1. 确定关键词
    final_keywords = args.keyword if args.keyword else DEFAULT_KEYWORDS
    
    # 2. 确定运行模式
    final_dry_run = False if args.execute else DEFAULT_DRY_RUN

    # 3. 加载患者 ID (除非指定 --no_id)
    target_patient_ids = None
    if not args.no_id:
        target_patient_ids = load_patient_ids(ERR_CSV_PATH)
        if not target_patient_ids:
            print("提示: 未能从 CSV 获取到 Patient_ID，本次运行不会匹配任何文件（安全兜底）。")
            # target_patient_ids 为空列表 []，配合 delete_files_with_keywords 中
            # `if patient_ids is not None:` 的判断，any([]) 恒为 False，不会匹配任何文件。
            # 若为真实删除模式(--execute)，直接终止，避免用户误以为在执行清理却空跑。
            if final_dry_run is False:
                print("错误: 真实删除模式下未能获取有效 Patient_ID 列表，已终止运行。")
                print("请检查 CSV 文件，或使用 --no_id 参数改为仅按关键词清理。")
                sys.exit(1)

    # 4. 真实删除前的二次确认
    if not final_dry_run:
        confirm = input(
            f"【警告】即将在真实模式下删除 '{args.dir}' 中匹配的文件，此操作不可逆。"
            f"确认执行请输入 y/yes: "
        ).strip().lower()
        if confirm not in ("y", "yes"):
            print("操作已取消。")
            sys.exit(0)

    delete_files_with_keywords(
        directory=args.dir, 
        keywords=final_keywords, 
        patient_ids=target_patient_ids, 
        dry_run=final_dry_run
    )

