"""
CSV 数据处理综合工具
整合功能：
1. 数据聚合合并 (Merge)：按 ID 合并多行，唯一值用分号连接。
2. 缺失值清理 (Clean)：删除缺失率超过设定阈值的列。
3. 数据分组压缩 (Compress)：按 ID 分组，取每列第一个非空值。
"""

import pandas as pd
import os
from collections import OrderedDict

# =================================================================
# 配置区域 (关键词置顶)
# =================================================================
# 输入输出设置
# 表已入家族 _share 归档（HE_nii_ly-tables-docs-20261115.tar.zst），用时先解包到本目录
INPUT_FILE = r"{lab_root}\_DataBase\MHE_PA\_share\HE_nii_ly\extracted_data.csv"
OUTPUT_MERGED = r"{lab_root}\_DataBase\MHE_PA\_share\HE_nii_ly\data_merged.csv"
OUTPUT_FINAL = r"{lab_root}\_DataBase\MHE_PA\_share\HE_nii_ly\compressed_data.csv"

# 逻辑参数
ID_COLUMN = "文件ID"           # 用于标识唯一行的列名
SEPARATOR = ";"               # 合并文本内容时的分隔符
MISSING_THRESHOLD = 0.5       # 缺失率阈值 (超过此比例的列将被删除)
ENCODINGS = ['utf-8-sig', 'gbk', 'gb18030'] # 读取时尝试的编码格式

# 任务开关 (设置为 True 以执行对应步骤)
DO_MERGE = True               # 执行多行聚合合并
DO_CLEAN = True               # 执行缺失值列清理
DO_COMPRESS = True            # 执行分组取首值压缩
# =================================================================


def read_csv_with_encoding(file_path, encodings=ENCODINGS):
    """
    尝试使用不同的编码格式读取 CSV 文件
    """
    if not os.path.exists(file_path):
        print(f"错误: 找不到文件 -> {file_path}")
        return None
    
    for enc in encodings:
        try:
            df = pd.read_csv(file_path, encoding=enc, low_memory=False)
            print(f"成功使用 {enc} 编码读取文件。")
            return df
        except Exception:
            continue
    print(f"失败: 无法使用提供的编码读取 {file_path}")
    return None


def merge_rows_by_id(df, id_column=ID_COLUMN, separator=SEPARATOR):
    """
    聚合合并逻辑：
    按 ID 分组，将同一 ID 下不同行的非空唯一值用分隔符拼接。
    """
    print(f"正在进行聚合合并 (ID: {id_column})...")
    
    # 清理列名空格
    df.columns = [str(c).strip() for c in df.columns]
    if id_column not in df.columns:
        print(f"错误: 找不到 ID 列 '{id_column}'")
        return df

    # 移除全空列以优化性能
    initial_cols = len(df.columns)
    df = df.dropna(axis=1, how='all')
    print(f"移除全空列: {initial_cols} -> {len(df.columns)}")

    columns = df.columns.tolist()
    id_idx = columns.index(id_column)
    
    # 使用 OrderedDict 保持顺序并进行高效聚合
    data_map = OrderedDict()
    total_rows = len(df)
    
    for idx, row in enumerate(df.itertuples(index=False, name=None)):
        uid = str(row[id_idx]).strip()
        if not uid or uid == 'nan':
            continue
            
        if uid not in data_map:
            # 结构: [ [值列表, 唯一值集合] for 每列 ]
            data_map[uid] = [ [[], set()] for _ in range(len(columns)) ]
        
        group_record = data_map[uid]
        for i, val in enumerate(row):
            if i == id_idx: continue
            if pd.notna(val):
                s_val = str(val).strip()
                if s_val and s_val not in group_record[i][1]:
                    group_record[i][0].append(s_val)
                    group_record[i][1].add(s_val)
        
        if (idx + 1) % 5000 == 0:
            print(f"进度: 已处理 {idx + 1}/{total_rows} 行...")

    # 构建结果数据
    final_rows = []
    for uid, col_info in data_map.items():
        row_data = [uid] if id_idx == 0 else []
        for i in range(len(columns)):
            if i == id_idx:
                if i != 0: row_data.append(uid)
                continue
            row_data.append(separator.join(col_info[i][0]))
        final_rows.append(row_data)
        
    return pd.DataFrame(final_rows, columns=columns)


def clean_missing_columns(df, threshold=MISSING_THRESHOLD):
    """
    清理缺失值列：
    删除缺失值比例超过 threshold 的列。
    """
    print(f"正在清理缺失值超过 {threshold*100}% 的列...")
    total_cols_before = len(df.columns)
    
    # 计算每列缺失值比例 (注意：聚合后空字符串 '' 也应视为空值)
    # 将空字符串转为 NaN 方便计算
    df_temp = df.replace('', pd.NA)
    missing_ratio = df_temp.isnull().mean()
    
    cols_to_drop = missing_ratio[missing_ratio > threshold].index.tolist()
    df_cleaned = df.drop(columns=cols_to_drop)
    
    print(f"清理完成。删除列数: {len(cols_to_drop)}, 剩余列数: {len(df_cleaned.columns)}")
    return df_cleaned


def compress_to_first_value(df, id_column=ID_COLUMN):
    """
    分组压缩逻辑：
    按 ID 分组，每列取第一个出现的非空值 (NaN 会被跳过)。
    """
    print(f"正在进行分组取首值压缩 (ID: {id_column})...")
    
    # 确保 ID 列存在
    if id_column not in df.columns:
        # 如果不存在，尝试使用第一列
        id_column = df.columns[0]
        print(f"警告: 未找到指定 ID 列，使用第一列 '{id_column}' 作为基准。")

    # 替换空字符串为 NaN 确保 first() 逻辑正确
    df_replaced = df.replace('', pd.NA)
    
    # 使用 groupby + first() 自动取第一个非空值
    df_compressed = df_replaced.groupby(id_column, as_index=False).first()
    
    print(f"压缩完成。行数: {len(df)} -> {len(df_compressed)}")
    return df_compressed


def main():
    """
    流水线执行主函数
    """
    # 1. 加载数据
    df = read_csv_with_encoding(INPUT_FILE)
    if df is None: return

    # 2. 执行聚合合并
    if DO_MERGE:
        df = merge_rows_by_id(df)
        # 中间保存，防止后续步骤出错导致数据丢失
        os.makedirs(os.path.dirname(OUTPUT_MERGED), exist_ok=True)
        df.to_csv(OUTPUT_MERGED, index=False, encoding='utf-8-sig')
        print(f"--- 步骤 1 (Merge) 完成，已保存至: {OUTPUT_MERGED}")

    # 3. 执行缺失值清理
    if DO_CLEAN:
        df = clean_missing_columns(df)
        print(f"--- 步骤 2 (Clean) 完成 ---")

    # 4. 执行分组压缩
    if DO_COMPRESS:
        df = compress_to_first_value(df)
        # 最终保存
        os.makedirs(os.path.dirname(OUTPUT_FINAL), exist_ok=True)
        df.to_csv(OUTPUT_FINAL, index=False, encoding='utf-8-sig')
        print(f"--- 步骤 3 (Compress) 完成，已保存至: {OUTPUT_FINAL}")

    print("\n所有处理流程执行完毕。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print(f"程序运行过程中出现异常:\n{e}")
        traceback.print_exc()
