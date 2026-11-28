import os
import argparse

# ================= 配置区域 (软编码) =================
# 1. 目标目录路径
TARGET_DIR = os.environ.get("TARGET_DIR", "")

# 2. 关键词设置 (置顶方便修改)
# 可以是单个字符串，例如: "registration"
# 也可以是元组/列表，例如: ("delta", "features", "habitat")
DEFAULT_KEYWORDS = ("delta", "features", "habitat")

# 3. 运行模式设置
# True: 模拟运行(仅打印列表) | False: 实际执行删除
DEFAULT_DRY_RUN = True
# ====================================================

def delete_files_with_keywords(directory, keywords, dry_run=True):
    """
    删除指定目录下包含特定关键词的文件。
    支持 keywords 为单个字符串或字符串列表。
    """
    if not os.path.exists(directory):
        print(f"错误: 目录 '{directory}' 不存在。")
        return

    # 统一转换为元组处理
    if isinstance(keywords, str):
        keywords = (keywords,)
    
    # 过滤空关键词
    keywords = [k for k in keywords if k]

    mode_str = "模拟" if dry_run else "实际"
    print(f"【{mode_str}运行模式】")
    print(f"目标目录: {directory}")
    print(f"匹配模式: 包含以下任意关键词 {keywords}")
    print("-" * 40)

    if not keywords:
        print("错误: 未指定关键词，操作已取消。")
        return

    count = 0
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        
        # 检查文件名是否包含任一关键词
        match = any(k in filename for k in keywords)
        
        if os.path.isfile(file_path) and match:
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
    parser = argparse.ArgumentParser(description="删除指定目录下带有关键词的文件")
    parser.add_argument("keyword", nargs="*", help="命令行传入关键词 (可选，若不传则使用脚本默认设置)")
    parser.add_argument("--dir", default=TARGET_DIR, help=f"目标目录 (默认: {TARGET_DIR})")
    parser.add_argument("--execute", action="store_true", help="实际执行删除操作 (覆盖默认设置)")

    args = parser.parse_args()

    # 优先顺序: 命令行参数 > 脚本配置
    final_keywords = args.keyword if args.keyword else DEFAULT_KEYWORDS
    
    # 逻辑：如果命令行带了 --execute，则执行删除；否则遵循脚本顶部 DEFAULT_DRY_RUN 的配置
    if args.execute:
        final_dry_run = False
    else:
        final_dry_run = DEFAULT_DRY_RUN
    
    delete_files_with_keywords(args.dir, final_keywords, dry_run=final_dry_run)
