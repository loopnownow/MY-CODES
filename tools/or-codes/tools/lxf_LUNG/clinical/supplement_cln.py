# -*- coding: utf-8 -*-
"""
从 PD_L1 目录的医疗文本中提取临床信息，补充 lxf_js.xlsx 中 cln 工作单的缺失值。
- 匹配方式：身份证号直接匹配文件名；带下划线(住院号_日期)通过住院号定位文本
- 仅填补缺失单元格，不覆盖已有值
- 写入新文件 lxf_js_filled.xlsx，保留原文件
"""
import os
import sys, os, re, glob
import pandas as pd

XLSX = os.environ.get("XLSX", "")
OUT  = os.environ.get("OUT", "")
PDIR = os.environ.get("PDIR", "")

# ──────────────────────────────────────────────
# 药物中文名 -> 英文小写名 映射（与已有 chemo_drugs 格式一致）
# ──────────────────────────────────────────────
DRUG_MAP = [
    # (中文关键词, 英文名)  注意：长关键词在前，避免子串误匹配（如"紫杉醇"先于"白蛋白紫杉醇"）
    ('卡瑞利珠单抗', 'camrelizumab'),
    ('特瑞普利单抗', 'toripalimab'),
    ('替雷利珠单抗', 'tislelizumab'),
    ('信迪利单抗',  'sintilimab'),
    ('帕博利珠单抗', 'pembrolizumab'),
    ('纳武利尤单抗', 'nivolumab'),
    ('白蛋白紫杉醇', 'nab-paclitaxel'),
    ('紫杉醇',      'paclitaxel'),
    ('多西他赛',    'docetaxel'),
    ('培美曲塞',    'pemetrexed'),
    ('吉西他滨',    'gemcitabine'),
    ('泽菲',        'gemcitabine'),
    ('卡铂',        'carboplatin'),
    ('顺铂',        'cisplatin'),
    ('贝伐珠单抗',  'bevacizumab'),
    ('安罗替尼',    'anlotinib'),
    ('阿帕替尼',    'apatinib'),
    ('阿美替尼',    'almonertinib'),
    ('奥希替尼',    'osimertinib'),
    ('吉非替尼',    'gefitinib'),
    ('厄洛替尼',    'erlotinib'),
    ('克唑替尼',    'crizotinib'),
    ('阿来替尼',    'alectinib'),
    ('恩曲替尼',    'entrectinib'),
    ('曲美替尼',    'trametinib'),
    ('达拉非尼',    'dabrafenib'),
    ('替吉奥',      'tegafur'),
    ('卡培他滨',    'capecitabine'),
    ('依托泊苷',    'etoposide'),
    ('依托伯苷',    'etoposide'),
    ('VP-16',       'etoposide'),
]

# ── 基于英文药物名的治疗类型分类（与 chemo_drugs 提取结果一致）──
CHEMO_DRUGS_SET   = {'paclitaxel','docetaxel','pemetrexed','gemcitabine','carboplatin','cisplatin','capecitabine','tegafur','etoposide','vinorelbine','doxorubicin','epirubicin','cyclophosphamide'}
TARGETED_DRUGS_SET = {'anlotinib','apatinib','almonertinib','osimertinib','gefitinib','erlotinib','crizotinib','alectinib','entrectinib','trametinib','dabrafenib','bevacizumab','sorafenib','lenvatinib'}
IMMUNO_DRUGS_SET =  {'camrelizumab','toripalimab','tislelizumab','sintilimab','pembrolizumab','nivolumab'}

# WBC / 中性粒 项目名 正则
# WBC：匹配 "白细胞X 10^9/L"（血常规），排除镜检"白细胞X /HP"
WBC_RE     = re.compile(r'白细胞\s*([<>≥≤]?\s*\d+\.?\d*)\s*10\^?9\s*/?\s*L')
# 中性粒细胞绝对值：匹配 "中性(粒)细胞数X" 或 "中性粒细胞绝对值/计数X"，排除"比率/%"
NEUT_RE    = re.compile(r'中性(?:粒)?细胞(?:数|计数|绝对值)\s*([<>≥≤]?\s*\d+\.?\d*)\s*')

# 组织学关键词
HIST_PATTERNS = [
    ('小细胞',           'small cell carcinoma'),
    ('神经内分泌',       'small cell carcinoma'),
    ('腺鳞癌',           'adenosquamous carcinoma'),
    ('腺癌',             'adenocarcinoma'),
    ('鳞癌',             'squamous'),
    ('大细胞',           'large cell carcinoma'),
]

def attach_output():
    """确保 stdout 是 utf-8（Windows 控制台兼容）"""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def load_pd_files():
    """读取 PD_L1 所有 txt，建立索引：文件名->文本；住院号->[(文件名,出院日期)]"""
    files = glob.glob(os.path.join(PDIR, '*.txt'))
    text_map = {}
    zyh_index = {}  # 住院号 -> list of (文件名, 出院日期yyyymmdd)
    for fp in files:
        fn = os.path.basename(fp)
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            t = f.read()
        text_map[fn] = t
        base = os.path.splitext(fn)[0]
        # 建立 住院号 -> 映射（用第一个出现的记录；同一文件可能有多次入院）
        for m in re.finditer(r'住院号:(\d+)', t):
            zyh = m.group(1)
            # 找该住院号对应的出院日期（往在该住院号出现后的第一个出院日期）
            after = t[m.end():]
            dm = re.search(r'出院日期:\[?(\d{4}[-/]\d{2}[-/]\d{2})', after)
            out_date = dm.group(1).replace('-', '') if dm else ''
            if zyh not in zyh_index:
                zyh_index[zyh] = (fn, out_date)
    return text_map, zyh_index

def locate_file(record_id, text_map, zyh_index):
    """返回 (文件名, 匹配方式)。None 表示未匹配"""
    rid = str(record_id).strip()
    # 方式1：身份证号 直接匹配文件名
    if rid + '.txt' in text_map:
        return rid + '.txt', 'record_id-文件名'
    # 方式2：住院号_日期 -> 通过住院号定位
    if '_' in rid:
        zyh = rid.split('_')[0]
        if zyh in zyh_index:
            return zyh_index[zyh][0], f'record_id-住院号({zyh})'
    return None, '未匹配'

def extract_drugs(text):
    """从治疗经过/用药文本中提取化疗药物，返回去重英文逗号分隔"""
    # 优先在 诊疗经过 / 出院后用药 / 出院医嘱 中查找
    process = ''
    m = re.search(r'诊疗经过:(.+?)(?=\n|合并症|出院时情况)', text, re.S)
    if m: process += m.group(1) + ' '
    m = re.search(r'出院后用药建议:(.+?)(?=\n|治疗结果)', text, re.S)
    if m: process += m.group(1) + ' '
    # 若无诊疗经过，退回全文本
    if not process.strip():
        process = text

    drugs = []
    order = []  # 保持出现顺序
    for kw, en in DRUG_MAP:
        if kw in process and en not in drugs:
            drugs.append(en)
            order.append(en)
    return ','.join(order)

def extract_treatment_type(drugs_str):
    """根据提取到的英文药物名判断治疗类型，与已有取值规范对齐。
    仅基于 chemo_drugs 中的英文药名，避免全文噪声。"""
    if not drugs_str:
        return None
    drugs = set(drugs_str.split(','))
    has_chemo   = bool(drugs & CHEMO_DRUGS_SET)
    has_target  = bool(drugs & TARGETED_DRUGS_SET)
    has_immuno  = bool(drugs & IMMUNO_DRUGS_SET)

    parts = []
    if has_chemo:  parts.append('chemotherapy')
    if has_target: parts.append('targeted therapy')
    if has_immuno: parts.append('immunotherapy')
    if not parts:
        return None
    return '+'.join(parts)

def extract_lab_first(text, regex):
    """提取第一个命中的数值（清理 <,>,± 符号）"""
    m = regex.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    val = re.sub(r'[<>=≥≤]', '', val).strip()
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None

def extract_histology(text):
    """从诊断中提取组织学类型"""
    # 诊断部分
    diag = ''
    for pat_key in ['门(急)诊诊断', '入院诊断', '出院诊断']:
        m = re.search(re.escape(pat_key) + r':(.+?)(?=\n|入院诊断|出院诊断|入院情况)', text, re.S)
        if m:
            diag += m.group(1) + ' '
    if not diag.strip():
        diag = text
    for kw, en in HIST_PATTERNS:
        if kw in diag:
            return en
    return None

def main():
    attach_output()
    df = pd.read_excel(XLSX, 'cln')
    text_map, zyh_index = load_pd_files()

    # 记录补充情况
    report = []
    n_found = 0

    for idx, row in df.iterrows():
        fn, method = locate_file(row['record_id'], text_map, zyh_index)
        if fn is None:
            report.append((row['Patient_ID'], row['record_id'], method, {}))
            continue
        n_found += 1
        text = text_map[fn]

        fills = {}
        # chemo_drugs: 仅当缺失时
        if pd.isna(row['chemo_drugs']) or str(row['chemo_drugs']).strip() == '':
            drugs = extract_drugs(text)
            if drugs:
                df.at[idx, 'chemo_drugs'] = drugs
                fills['chemo_drugs'] = drugs
        # treatment_type
        if pd.isna(row['treatment_type']) or str(row['treatment_type']).strip() == '':
            drugs_now = str(df.at[idx, 'chemo_drugs'])
            tt = extract_treatment_type(drugs_now)
            if tt:
                df.at[idx, 'treatment_type'] = tt
                fills['treatment_type'] = tt
        # WBC
        if pd.isna(row['WBC']):
            v = extract_lab_first(text, WBC_RE)
            if v is not None:
                df.at[idx, 'WBC'] = v
                fills['WBC'] = v
        # neutrophil_count
        if pd.isna(row['neutrophil_count']):
            v = extract_lab_first(text, NEUT_RE)
            if v is not None:
                df.at[idx, 'neutrophil_count'] = v
                fills['neutrophil_count'] = v
        # histology
        if pd.isna(row['histology']):
            h = extract_histology(text)
            if h:
                df.at[idx, 'histology'] = h
                fills['histology'] = h

        report.append((row['Patient_ID'], row['record_id'], method, fills))

    # 写入新文件（保留多 sheet 结构）
    with pd.ExcelWriter(OUT, engine='openpyxl') as writer:
        # 读回所有 sheet
        xl = pd.ExcelFile(XLSX)
        for sh in xl.sheet_names:
            if sh == 'cln':
                df.to_excel(writer, sheet_name=sh, index=False)
            else:
                pd.read_excel(XLSX, sh).to_excel(writer, sheet_name=sh, index=False)

    print(f'匹配到文本的患者数: {n_found}/{len(df)}')
    print(f'输出文件: {OUT}')
    print()
    print('=== 补充明细 ===')
    for pid, rid, method, fills in report:
        if fills:
            parts = ', '.join(f'{k}={v}' for k, v in fills.items())
            print(f'{pid:<22} {rid:<22} [{method}] 补充: {parts}')
        else:
            print(f'{pid:<22} {rid:<22} [{method}] 无补充')

    # 汇总缺失变化
    print('\n=== 缺失值数量变化 ===')
    miss_before = pd.read_excel(XLSX, 'cln').isna().sum()
    miss_after = df.isna().sum()
    for col in ['treatment_type','chemo_drugs','histology','WBC','neutrophil_count']:
        print(f'{col:<20} 补充前缺失:{int(miss_before[col]):>3}  补充后缺失:{int(miss_after[col]):>3}  填补:{int(miss_before[col]-miss_after[col]):>3}')

if __name__ == '__main__':
    main()