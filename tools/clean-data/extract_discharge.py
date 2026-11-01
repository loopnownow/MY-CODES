"""出院小结 .txt → 宽表 CSV（金山格式）。

默认路径走 ``tidy_yt_rdl``（Results 解包目录 / 0del CSV）。
可用 ``--input-dir`` / ``--output`` 或环境变量覆盖。

用法::

    python extract_discharge.py
    python extract_discharge.py --input-dir <dir> --output <csv>
"""

from __future__ import annotations

import os
import re
import glob
import argparse
import sys
from pathlib import Path

import pandas as pd

# 确保与其它 tidy 脚本同目录可互相 import
_TIDY = Path(__file__).resolve().parent
if str(_TIDY) not in sys.path:
    sys.path.insert(0, str(_TIDY))
import tidy_yt_rdl as PATHS  # noqa: E402

# ==================== CONFIG ====================
INPUT_DIR = os.environ.get("DISCHARGE_TXT_DIR", str(PATHS.RESULTS_TXT))
OUTPUT_FILE = os.environ.get("DISCHARGE_CSV", str(PATHS.JINSHAN_CSV))
DRY_RUN = False
# ================================================

# 记录头：[N] 科室(住院) / 科室（住院），科室名不含空白
REC_HEAD = re.compile(r'(?m)^\[(\d+)\]\s*(\S+?)[（(]住院[）)]')

# ──────────────────────────────────────────────
# 实验室指标标准名称字典 (从 extract_beng1_data.py 复制并微调)
# ──────────────────────────────────────────────
LAB_DICT = {
    # 血常规
    '白细胞计数':             ['白细胞计数', 'WBC计数', '白细胞'],
    '中性粒细胞计数':         ['中性粒细胞计数', '中性粒细胞绝对值', '中性细胞数'],
    '中性粒细胞百分比':       ['中性粒细胞百分比', '中性粒细胞%', '中性粒细胞 %', '中性细胞%'],
    '淋巴细胞计数':           ['淋巴细胞计数', '淋巴细胞绝对值', '淋巴细胞数'],
    '淋巴细胞百分比':         ['淋巴细胞百分比', '淋巴细胞%', '淋巴细胞 %', '淋巴细胞'],
    '单核细胞计数':           ['单核细胞计数', '单核细胞绝对值', '单核细胞数'],
    '单核细胞百分比':         ['单核细胞百分比', '单核细胞%', '单核细胞 %', '单核细胞'],
    '嗜酸细胞计数':           ['嗜酸细胞计数', '嗜酸粒细胞计数', '嗜酸细胞绝对值'],
    '嗜碱细胞计数':           ['嗜碱细胞计数', '嗜碱粒细胞计数'],
    '红细胞计数':             ['红细胞计数', 'RBC计数'],
    '血红蛋白':               ['血红蛋白', 'Hb', 'HGB', 'HgB'],
    '平均红细胞体积':         ['平均红细胞体积', 'MCV'],
    '平均血红蛋白含量':       ['平均血红蛋白含量', 'MCH'],
    '平均血红蛋白浓度':       ['平均血红蛋白浓度', 'MCHC'],
    '红细胞分布宽度':         ['红细胞分布宽度', 'RDW'],
    '血小板计数':             ['血小板计数', 'PLT'],
    '平均血小板体积':         ['平均血小板体积', 'MPV'],
    '网织红细胞百分比':       ['网织红细胞百分比', '网织红细胞%'],
    '网织红细胞计数':         ['网织红细胞计数', '网织红细胞绝对值'],

    # 凝血功能
    '凝血酶原时间':           ['凝血酶原时间(PT)', '凝血酶原时间', 'PT'],
    '凝血酶原活动度':         ['凝血酶原活动度', 'PT活动度'],
    '凝血酶时间':             ['凝血酶时间(TT)', '凝血酶时间', 'TT'],
    '活化部分凝血活酶时间':   ['活化部分凝血活酶时间', 'APTT'],
    '纤维蛋白原':             ['纤维蛋白原(CFib)', '纤维蛋白原', 'Fib', 'CFib'],
    'D二聚体':                ['血浆D-二聚体测定', 'D-二聚体', 'D二聚体', 'D-dimer'],
    '纤维蛋白降解产物':       ['纤维蛋白(原)降解产物', '纤维蛋白原降解产物', 'FDP', '血浆FDP'],
    '国际标准化比值':         ['国际标准化比值', 'INR'],

    # 生化 - 肝功能
    '丙氨酸氨基转移酶':       ['丙氨酸氨基转移酶', '谷丙转氨酶', 'ALT', 'GPT'],
    '天门冬氨酸转移酶':       ['天门冬氨酸转移酶', '天门冬氨酸氨基转移酶', '谷草转氨酶', 'AST', 'GOT'],
    '碱性磷酸酶':             ['碱性磷酸酶', 'ALP', 'AKP'],
    'γ谷氨酰转肽酶':          ['r-谷氨酰基转移酶', 'γ-谷氨酰转肽酶', 'GGT', 'γ-GT'],
    '总胆红素':               ['总胆红素', 'TBIL', 'TBil'],
    '直接胆红素':             ['直接胆红素', 'DBIL', 'DBil'],
    '间接胆红素':             ['间接胆红素', 'IBIL'],
    '总蛋白':                 ['总蛋白', 'TP'],
    '白蛋白':                 ['白蛋白', 'ALB', 'Alb'],
    '球蛋白':                 ['球蛋白', 'GLB'],
    '白球比':                 ['白球比', 'A/G'],
    '胆碱酯酶':               ['胆碱酯酶', 'CHE'],

    # 生化 - 肾功能/电解质
    '肌酐':                   ['肌酐', 'Scr', 'Cr', 'CRE'],
    '尿素氮':                 ['尿素氮', '尿素', 'BUN', 'Urea'],
    '尿酸':                   ['尿酸', 'UA', 'SUA'],
    '胱抑素C':                ['胱抑素C', 'Cystatin C', 'CysC', 'Cysc', '胱抑素-C'],
    '肾小球滤过率':           ['估算肾小球滤过率', '肾小球滤过率', 'eGFR', 'GFR'],
    '钾离子':                 ['钾离子', 'K+', '血钾', 'K', '钾'],
    '钠离子':                 ['钠离子', 'Na+', '血钠', 'Na', '钠'],
    '氯离子':                 ['氯离子', 'Cl-', '血氯', 'Cl', '氯'],
    '总钙':                   ['总钙离子', '总钙', 'Ca²+', 'Ca2+', 'Ca', '钙'],
    '磷':                     ['无机磷', '血磷', 'P'],
    '碳酸氢盐':               ['碳酸氢盐(HCO3-)', '碳酸氢盐', 'HCO3-', 'HCO3'],
    '阴离子间隙':             ['阴离子间隙', 'AG'],
    '葡萄糖':                 ['葡萄糖', 'GLU', 'Glu'],

    # 心肌酶/心肌标志物
    '乳酸脱氢酶':             ['乳酸脱氢酶', 'LDH'],
    '肌酸激酶':               ['肌酸激酶', 'CK', 'CPK'],
    '肌酸激酶同工酶':         ['肌酸激酶同工酶', 'CK-MB', 'CKMB', 'CKMB质量'],
    '肌钙蛋白I':              ['肌钙蛋白I', 'cTnI', 'TnI'],
    '肌钙蛋白T':              ['肌钙蛋白T', 'cTnT', 'TnT'],
    '肌红蛋白':               ['肌红蛋白', 'MYO', 'Mb'],
    'N端脑钠肽前体':          ['N端脑钠肽前体', 'NT-proBNP', 'BNP', 'B型钠尿肽'],

    # 肿瘤标志物
    '癌胚抗原CEA':            ['癌胚抗原CEA(发光)', '癌胚抗原CEA', 'CEA', '癌胚抗原'],
    '细胞角化蛋白19片段':     ['细胞角化蛋白19片段(发光)', '细胞角化蛋白19片段', 'CYFRA21-1', 'Cyfra', '非小细胞肺癌相关抗原21-1'],
    '神经元特异性烯醇化酶':   ['神经元特异性烯醇化酶(发光)', '神经元特异性烯醇化酶', 'NSE', '神经元特异烯醇化酶测定'],
    '甲胎蛋白':               ['甲胎蛋白', 'AFP', '甲胎蛋白测定(AFP)'],
    '糖类抗原199':            ['糖类抗原199', 'CA199', 'CA-199', 'CA19-9'],
    '糖类抗原125':            ['糖类抗原125', 'CA125', 'CA-125', '糖类抗原（CA-125）'],
    '糖类抗原153':            ['糖类抗原153', 'CA153', 'CA-153', '糖类抗原（CA15-3）'],
}

# 基因检测指标字典
GENE_DICT = {
    'EGFR突变状态':        ['EGFR基因', 'EGFR突变', 'EGFR'],
    'ALK融合状态':         ['ALK融合', 'ALK重排', 'ALK基因', 'ALK阳性', 'ALK阴性', 'ALK'],
    'ROS1融合状态':        ['ROS1融合', 'ROS1重排', 'ROS1基因', 'ROS1'],
    'PD-L1_TPS':           ['TPS', 'PD-L1 TPS'],
    'PD-L1_CPS':           ['CPS', 'PD-L1 CPS'],
    'PD-L1表达状态':       ['PD-L1表达', 'PD-L1阳性', 'PD-L1阴性', 'PD-L1'],
}

# 建立别名映射
_ALIAS_MAP = {}
for std_name, aliases in LAB_DICT.items():
    for alias in aliases:
        _ALIAS_MAP[alias] = std_name
_SORTED_ALIASES = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def clean_text(text):
    if not text: return ""
    return text.strip().replace('\xa0', ' ').replace('\u3000', ' ').replace('\n', ' ')

def _find_std_name(candidate: str) -> str | None:
    candidate = candidate.strip()
    for alias in _SORTED_ALIASES:
        if alias in candidate:
            return _ALIAS_MAP[alias]
    return None

def parse_labs(text: str) -> dict:
    labs = {}
    # 支持 YY-MM-DD
    date_pat = re.compile(r'(\d{2,4}[-./]\d{2}[-./]\d{2})')
    segments = date_pat.split(text)
    
    current_date = None
    for seg in segments:
        m = date_pat.fullmatch(seg.strip())
        if m:
            d = seg.strip().replace('.', '-').replace('/', '-')
            if len(d.split('-')[0]) == 2:
                d = "20" + d # 补全年份
            current_date = d
            continue
        if current_date is None: continue
        _extract_items_in_segment(seg, current_date, labs)
    
    return {k: ';'.join(f"{d}:{v}" for d, v in sorted(pairs)) for k, pairs in labs.items()}

def _extract_items_in_segment(seg: str, date: str, labs: dict):
    # 金山格式通常是 "项目A: 指标1 值, 指标2 值"
    parts = re.split(r'[:：]', seg, 1)
    content = parts[1] if len(parts) > 1 else parts[0]
    
    items = re.split(r'[,，;；]', content)
    for item in items:
        item = item.strip()
        if not item: continue
        
        # 清除常见的状态词干扰
        item_clean = re.sub(r'(偏高|偏低|↑|↓|阳性|阴性|正常)', '', item).strip()
        
        # 匹配：指标名 + 空格 + 数值 + 单位
        m = re.match(r'^(.+?)\s+([<>≥≤=]?\s*\d+\.?\d*(?:e[+-]?\d+)?)\s*(.*)$', item_clean)
        if not m: continue
        
        name_candidate, value, unit = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        std = _find_std_name(name_candidate)
        if std:
            val_str = f"{value}{unit}" if unit else value
            if std not in labs: labs[std] = []
            if (date, val_str) not in labs[std]:
                labs[std].append((date, val_str))

# ──────────────────────────────────────────────
# 核心解析逻辑
# ──────────────────────────────────────────────
def process_jinshan_file(filepath: str) -> list:
    results = []
    filename = os.path.basename(filepath)
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return []

    # 按照记录块分割（任意科室：[N] 科室(住院)）
    parts = REC_HEAD.split(content)
    # parts = [前导, num1, dept1, 正文1, num2, dept2, 正文2, ...]
    records = []
    for i in range(1, len(parts) - 2, 3):
        records.append(f"[{parts[i]}] {parts[i + 1]}(住院)\n" + parts[i + 2])

    for rec in records:
        if "出院小结" not in rec: continue

        data = {}
        data['文件名'] = filename
        data['路径'] = os.path.abspath(filepath)
        data['记录类型'] = '出院小结'
        data['医院'] = '复旦大学附属金山医院'
        m_seq = re.match(r'\[(\d+)\]\s*(\S+?)[（(]住院[）)]', rec)
        if m_seq:
            data['记录序号'] = m_seq.group(1)
            data['记录科室'] = m_seq.group(2)
        
        # 基础信息提取
        m_name = re.search(r'姓名:(\S+)', rec)
        if m_name: data['患者姓名'] = m_name.group(1)
        
        m_sex = re.search(r'性别:(\S+)', rec)
        if m_sex: data['性别'] = m_sex.group(1)
        
        m_age = re.search(r'年龄:(\d+)岁', rec)
        if m_age: data['年龄(岁)'] = m_age.group(1)
        
        m_zyh = re.search(r'住院号:(\d+)', rec)
        if m_zyh: data['住院号'] = m_zyh.group(1)
        
        m_dept = re.search(r'科室:(\S+)', rec)
        if m_dept: data['出院科室'] = m_dept.group(1)
        
        m_bed = re.search(r'床位号:(\d+)', rec)
        if m_bed: data['床号'] = m_bed.group(1)

        m_in_date = re.search(r'入院日期时间:([\d\-\:\s]{10,16})', rec)
        if m_in_date: data['入院时间'] = m_in_date.group(1).strip()
        
        m_out_date = re.search(r'出院日期:\[([\d\-\:\s]{10,16})\]', rec)
        if m_out_date: data['出院时间'] = m_out_date.group(1).strip()
        
        # 诊断
        m_mz_diag = re.search(r'门\(急\)诊诊断:(.+?)(?=\n|入院诊断)', rec, re.S)
        if m_mz_diag: data['门诊诊断'] = clean_text(m_mz_diag.group(1))

        m_in_diag = re.search(r'入院诊断:(.+?)(?=\n|出院诊断)', rec, re.S)
        if m_in_diag: data['入院诊断'] = clean_text(m_in_diag.group(1))
        
        m_out_diag = re.search(r'出院诊断:(.+?)(?=\n|入院情况)', rec, re.S)
        if m_out_diag: data['出院诊断'] = clean_text(m_out_diag.group(1))
        
        # 文本块
        m_in_cond = re.search(r'入院情况（症状及体征）:(.+?)(?=\n|主要化验结果)', rec, re.S)
        if m_in_cond: data['入院时情况(全文)'] = clean_text(m_in_cond.group(1))

        m_out_cond = re.search(r'出院时情况（症状及体征）:(.+?)(?=\n|出院后用药建议)', rec, re.S)
        if m_out_cond: data['出院时情况(全文)'] = clean_text(m_out_cond.group(1))

        m_advice = re.search(r'出院后用药建议:(.+?)(?=\n|治疗结果)', rec, re.S)
        if m_advice: data['出院医嘱'] = clean_text(m_advice.group(1))

        m_process = re.search(r'诊疗经过:(.+?)(?=\n|合并症|出院时情况)', rec, re.S)
        if m_process:
            process_text = clean_text(m_process.group(1))
            data['住院经过(全文)'] = process_text
            
            # 提取化疗方案
            chemo_drugs = ['泽菲', '卡铂', '贝伐珠单抗', '安罗替尼', '白蛋白紫杉醇', '卡培他滨', '培美曲塞', '顺铂', '吉西他滨', '紫杉醇', '多西他赛']
            found_drugs = [d for d in chemo_drugs if d in process_text]
            if found_drugs:
                data['化疗_药物列表'] = ','.join(found_drugs)
                data['治疗类型'] = '化疗/靶向'
            
            # 尝试匹配化疗日期
            m_chemo_date = re.search(r'(\d{1,2}\.\d{1,2})起', process_text)
            if m_chemo_date and '入院时间' in data:
                year = data['入院时间'].split('-')[0]
                data['化疗_日期'] = f"{year}-{m_chemo_date.group(1).replace('.', '-')}"

        m_merged = re.search(r'合并症:(.+?)(?=\n|出院时情况)', rec, re.S)
        if m_merged: data['合并症'] = clean_text(m_merged.group(1))

        m_result = re.search(r'治疗结果：(.*?)(?=\n|$)', rec)
        if m_result: data['治疗结果'] = m_result.group(1).strip()

        # 医师
        m_docs = re.search(r'主治医师:(\S+)\s*住院医师:(\S+)', rec)
        if m_docs:
            data['医师签名'] = f"主治:{m_docs.group(1)}, 住院:{m_docs.group(2)}"
        
        m_rectime = re.search(r'记录时间:([\d\-\:\s]{10,16})', rec)
        if m_rectime: data['记录时间'] = m_rectime.group(1).strip()

        # 化验结果
        m_labs = re.search(r'主要化验结果（注明日期）:(.+?)(?=\n|特殊检查)', rec, re.S)
        if m_labs:
            lab_text = m_labs.group(1)
            lab_data = parse_labs(lab_text)
            data.update(lab_data)
            
        # 影像报告
        m_exam = re.search(r'特殊检查及重要会诊（注明日期及检验号）:(.+?)(?=\n|诊疗经过)', rec, re.S)
        if m_exam:
            exam_text = m_exam.group(1)
            if 'CT' in exam_text: data['胸部CT'] = clean_text(exam_text)
            if 'MRI' in exam_text: data['头颅MRI'] = clean_text(exam_text)

        # record_id
        if '住院号' in data and '出院时间' in data:
            date_part = data['出院时间'].split(' ')[0].replace('-', '')
            data['record_id'] = f"{data['住院号']}_{date_part}"
        elif '文件名' in data:
            data['record_id'] = data['文件名'].replace('.txt', '')
            
        results.append(data)
        
    return results

# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def main():
    global INPUT_DIR, OUTPUT_FILE
    ap = argparse.ArgumentParser(description="金山医院出院小结 .txt → 单宽表 CSV")
    ap.add_argument("--input-dir", default=INPUT_DIR, help=f"输入 .txt 目录（默认 {INPUT_DIR}）")
    ap.add_argument("--output", default=OUTPUT_FILE, help=f"输出 CSV（默认 {OUTPUT_FILE}）")
    args = ap.parse_args()
    INPUT_DIR, OUTPUT_FILE = args.input_dir, args.output

    files = sorted(glob.glob(os.path.join(INPUT_DIR, '*.txt')))
    print(f"发现 {len(files)} 个文件。")
    
    all_data = []
    for f in files:
        recs = process_jinshan_file(f)
        all_data.extend(recs)
        print(f"处理: {os.path.basename(f)} -> {len(recs)} 条")
    
    if not all_data:
        print("未提取到任何数据。")
        return
        
    df = pd.DataFrame(all_data)
    
    # ──────────────────────────────────────────────
    # 列排序：对齐 extract_beng1_data.py
    # ──────────────────────────────────────────────
    FIXED_COLS = [
        'record_id', '患者姓名', '性别', '年龄(岁)', '住院号', '门诊号',
        '出院科室', '床号', '医院', '入院时间', '出院时间', '住院天数',
        '身高(cm)', '体重(kg)', 'BMI', '收缩压(mmHg)', '舒张压(mmHg)',
        '记录类型', '记录日期', '记录时间', '医师签名',
        '入院诊断', '出院诊断', '治疗结果',
        '治疗类型', '化疗_方案', '化疗_日期', '化疗_药物列表',
        '疗效评估', '不良反应', '支持治疗',
    ]
    
    lab_cols  = sorted([c for c in df.columns if c in LAB_DICT])
    gene_cols = sorted([c for c in df.columns if c in GENE_DICT])
    
    _exclude  = set(FIXED_COLS) | set(lab_cols) | set(gene_cols) | {
        '入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)',
        '出院医嘱', '路径', '文件名', '记录内容', '门诊诊断', '合并症'
    }
    
    report_cols = sorted([c for c in df.columns if c not in _exclude])
    text_cols   = ['入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)', '出院医嘱', '门诊诊断', '合并症']
    meta_cols   = ['文件名', '路径']
    
    ordered = (
        [c for c in FIXED_COLS if c in df.columns]
        + [c for c in lab_cols  if c not in FIXED_COLS]
        + [c for c in gene_cols if c not in FIXED_COLS]
        + [c for c in report_cols]
        + [c for c in text_cols if c in df.columns]
        + [c for c in meta_cols if c in df.columns]
    )
    
    # 追加任何未在有序列表中的列
    remaining = [c for c in df.columns if c not in ordered]
    ordered += remaining
    
    df = df[ordered]
    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT_FILE)), exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    
    print(f"\n[OK] 成功提取 {len(all_data)} 条记录")
    print(f"     列数: {len(df.columns)}")
    print(f"     输出: {OUTPUT_FILE}")

if __name__ == '__main__':
    main()
