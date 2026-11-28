import pandas as pd
import os

# ==========================================
# 配置区 (软编码)
# ==========================================
CONFIG = {
    "input_file": r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1\beng1linai2.csv',
    "output_file": r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1\beng1linai2_compressed.csv',
    # 分组列，可以指定多列，例如 ["患者姓名", "性别"]
    "group_cols": ["患者姓名"],
    # 合并不同数据时的分隔符
    "separator": " | ",
    # 文件编码 (utf-8-sig 兼容 Excel)
    "encoding": "utf-8-sig"
}

def compress_data():
    print(f"正在读取文件: {CONFIG['input_file']} ...")
    
    if not os.path.exists(CONFIG['input_file']):
        print(f"错误: 找不到输入文件 {CONFIG['input_file']}")
        return

    # 读取 CSV
    # 使用 low_memory=False 防止大文件警告
    df = pd.read_csv(CONFIG['input_file'], encoding=CONFIG['encoding'], low_memory=False)
    
    print(f"原始总行数: {len(df)}")
    
    # 清理数据：去除列名和字符串单元格前后的空格
    df.columns = df.columns.str.strip()
    # 仅对字符串列执行 strip
    df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)

    # 聚合函数逻辑：
    # 1. 过滤空值 (None, NaN, 空字符串)
    # 2. 去重，保持原始顺序
    # 3. 合并为字符串
    def combine_unique(series):
        # 过滤掉缺失值和空字符串
        valid_values = series.dropna().astype(str).unique()
        valid_values = [v for v in valid_values if v != '']
        
        if not valid_values:
            return ""
        if len(valid_values) == 1:
            return valid_values[0]
        
        return CONFIG['separator'].join(valid_values)

    print(f"正在按 {CONFIG['group_cols']} 进行合并压缩...")
    
    # 执行分组聚合
    # 分组列本身会自动保留，不需要在 agg 中处理
    df_compressed = df.groupby(CONFIG['group_cols'], as_index=False).agg(combine_unique)

    print(f"合并后总行数 (唯一组数): {len(df_compressed)}")

    # 保存结果
    df_compressed.to_csv(CONFIG['output_file'], index=False, encoding=CONFIG['encoding'])
    print(f"压缩完成！结果已保存至: {CONFIG['output_file']}")

if __name__ == "__main__":
    compress_data()
