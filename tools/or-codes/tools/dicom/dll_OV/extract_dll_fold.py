# -*- coding: utf-8 -*-
"""
从 clinical informations 下所有 .docx 文件提取临床信息、实验室检查结果、病理结果，输出单宽表 CSV。

文件结构（复旦大学附属妇产科医院）：
  - 病理报告（表格：基本信息/肉眼所见/病理诊断/免疫组化/报告医生/时间）
  - 出院记录（段落：姓名/年龄/床号/住院号/入院出院日期/诊断/症状体征/诊疗过程/治疗结果/手术/医嘱/医师签名）
  - 实验室检查表（tab 分隔：日期 + 项目名称\\t结果\\t单位\\t参考值）

提取策略：
  1. 遍历 docx 的表格单元格 + 段落，合并为全文（保留 tab 分隔符以识别 lab 表）
  2. 按关键词切分「病理块 / 出院块 / 实验室表块」（解决 N01/N20 顺序不同问题）
  3. 病理解析：基本信息从表格取；肉眼所见/病理诊断/免疫组化从段落正则提取
  4. 出院解析：正则提取 `标签：[值]` / `标签:值` 型字段
  5. 实验室双通道：
     - 出院块"实验室检查及主要会诊"内的 `检查组名：日期 (指标：值单位...)` 紧凑格式
     - 末尾 tab 表 `项目名称\\t结果\\t单位\\t参考值` 结构化主通道
  6. 每个实验室项目一列，多时间结果用 `时间:检测值` 分号连接
"""

import os
import re
import glob
import pandas as pd
from docx import Document

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
INPUT_DIR   = r'clinical informations'
OUTPUT_FILE = r'clinical informations\clinical_extracted.csv'

# ──────────────────────────────────────────────
# 实验室指标标准名称字典
# key = 标准列名（CSV 中的列头）
# value = 文本中可能出现的别名列表（按长度降序，优先匹配更长的）
# ──────────────────────────────────────────────
LAB_DICT = {
    # 血常规
    '白细胞计数':             ['白细胞计数', 'WBC计数', '白细胞'],
    '中性粒细胞计数':         ['中性粒细胞计数', '中性粒细胞绝对值', '中性细胞数', '中性粒细胞数'],
    '中性粒细胞百分比':       ['中性粒细胞百分比', '中性粒细胞%', '中性粒细胞 %', '中性细胞%', '中性粒细胞'],
    '淋巴细胞计数':           ['淋巴细胞计数', '淋巴细胞绝对值', '淋巴细胞数'],
    '淋巴细胞百分比':         ['淋巴细胞百分比', '淋巴细胞%', '淋巴细胞 %', '淋巴细胞'],
    '单核细胞计数':           ['单核细胞计数', '单核细胞绝对值', '单核细胞数'],
    '单核细胞百分比':         ['单核细胞百分比', '单核细胞%', '单核细胞 %', '单核细胞'],
    '嗜酸细胞计数':           ['嗜酸细胞计数', '嗜酸粒细胞计数', '嗜酸细胞绝对值', '嗜酸性粒细胞数'],
    '嗜碱细胞计数':           ['嗜碱细胞计数', '嗜碱粒细胞计数', '嗜碱性粒细胞数'],
    '嗜酸性粒细胞百分比':     ['嗜酸性粒细胞', '嗜酸性粒细胞%', '嗜酸细胞%'],
    '嗜碱性粒细胞百分比':     ['嗜碱性粒细胞', '嗜碱性粒细胞%', '嗜碱细胞%'],
    '红细胞计数':             ['红细胞计数', 'RBC计数', '红细胞'],
    '血红蛋白':               ['血红蛋白', 'Hb', 'HGB', 'HgB'],
    '红细胞比积':             ['红细胞比积', '红细胞压积', 'HCT'],
    '平均红细胞体积':         ['平均红细胞体积', 'MCV'],
    '平均血红蛋白含量':       ['平均血红蛋白含量', 'MCH'],
    '平均血红蛋白浓度':       ['平均血红蛋白浓度', 'MCHC'],
    '红细胞分布宽度':         ['红细胞分布宽度', 'RDW'],
    '血小板计数':             ['血小板计数', 'PLT', '血小板'],
    '平均血小板体积':         ['平均血小板体积', 'MPV'],
    '血小板分布宽度':         ['血小板分布宽度', 'PDW'],
    '血小板比积':             ['血小板比积', 'PCT'],
    '大血小板比率':           ['大血小板比率'],
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
    '抗凝血酶活性':           ['抗凝血酶活性', '抗凝血酶III', 'AT-III'],

    # 生化 - 肝功能
    '丙氨酸氨基转移酶':       ['丙氨酸氨基转移酶', '谷丙转氨酶', 'ALT', 'GPT'],
    '天门冬氨酸转移酶':       ['天门冬氨酸转移酶', '天门冬氨酸氨基转移酶', '谷草转氨酶', 'AST', 'GOT'],
    '碱性磷酸酶':             ['碱性磷酸酶', 'ALP', 'AKP'],
    'γ谷氨酰转肽酶':          ['r-谷氨酰基转移酶', 'γ-谷氨酰转肽酶', '谷氨酰氨基转移酶', 'GGT', 'γ-GT'],
    '总胆红素':               ['总胆红素', 'TBIL', 'TBil'],
    '直接胆红素':             ['直接胆红素', 'DBIL', 'DBil'],
    '间接胆红素':             ['间接胆红素', 'IBIL'],
    '总蛋白':                 ['总蛋白', 'TP'],
    '白蛋白':                 ['白蛋白', 'ALB', 'Alb'],
    '球蛋白':                 ['球蛋白', 'GLB'],
    '白球比':                 ['白球比', 'A/G'],
    '胆碱酯酶':               ['胆碱酯酶', 'CHE'],
    '总胆汁酸':               ['总胆汁酸', 'TBA'],
    '前白蛋白':               ['前白蛋白', 'PA'],
    '甘胆酸':                 ['甘胆酸', 'CG'],

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
    '碳酸氢盐':               ['碳酸氢盐(HCO3-)', '碳酸氢盐', 'HCO3-', 'HCO3', '二氧化碳结合力'],
    '阴离子间隙':             ['阴离子间隙', 'AG'],
    '葡萄糖':                 ['葡萄糖', 'GLU', 'Glu'],
    '镁':                     ['镁', 'Mg'],
    '一氧化氮浓度':           ['一氧化氮浓度', 'NO'],

    # 血脂
    '总胆固醇':               ['总胆固醇', 'TC', 'CHOL'],
    '甘油三酯':               ['甘油三酯', 'TG', 'TGC'],
    '高密度脂蛋白胆固醇':     ['高密度脂蛋白胆固醇', 'HDL-C', 'HDL'],
    '低密度脂蛋白胆固醇':     ['低密度脂蛋白胆固醇', 'LDL-C', 'LDL'],
    '脂蛋白a':                ['脂蛋白a', 'LP(a)', 'Lp(a)'],

    # 心肌酶/心肌标志物
    '乳酸脱氢酶':             ['乳酸脱氢酶', 'LDH'],
    '肌酸激酶':               ['肌酸激酶', 'CK', 'CPK'],
    '肌酸激酶同工酶':         ['肌酸激酶同工酶', 'CK-MB', 'CKMB', 'CKMB质量'],
    '肌钙蛋白I':              ['肌钙蛋白I', 'cTnI', 'TnI'],
    '肌钙蛋白T':              ['肌钙蛋白T', 'cTnT', 'TnT'],
    '肌红蛋白':               ['肌红蛋白', 'MYO', 'Mb'],
    'N端脑钠肽前体':          ['N端脑钠肽前体', 'NT-proBNP', 'BNP', 'B型钠尿肽', 'N末端脑利钠肽前体', 'B型钠尿肽前体（PRO-BNP）'],

    # 炎症/免疫
    'C反应蛋白':              ['C反应蛋白', 'CRP', 'hs-CRP'],
    '血清淀粉样蛋白A':        ['血清淀粉样蛋白A', '血清淀粉样蛋白', 'SAA'],
    '降钙素原':               ['降钙素原', 'PCT'],
    '白介素6':                ['白介素6', 'IL-6', 'IL6'],
    '铁蛋白':                 ['铁蛋白', 'Ferritin'],
    '红细胞沉降率':           ['红细胞沉降率', 'ESR', '血沉'],

    # 甲状腺功能
    '促甲状腺激素':           ['促甲状腺激素', 'TSH'],
    '三碘甲腺原氨酸':         ['三碘甲腺原氨酸', 'TT3', 'T3'],
    '甲状腺素':               ['甲状腺素', 'TT4', 'T4'],
    '游离三碘甲腺原氨酸':     ['游离三碘甲腺原氨酸', '游离T3', 'FT3'],
    '游离甲状腺素':           ['游离甲状腺素', '游离T4', 'FT4'],
    '甲状腺过氧化物酶抗体':   ['甲状腺过氧化物酶抗体', 'Anti-TPO', 'TPOAb'],
    '甲状腺球蛋白抗体':       ['甲状腺球蛋白抗体', 'Anti-Tg', 'TgAb'],
    '促甲状腺素受体抗体':     ['促甲状腺素受体抗体', 'Anti-TSHR', 'TRAb'],
    '降钙素':                 ['降钙素', 'hCT', 'CT'],

    # 肿瘤标志物（妇科重点）
    '癌胚抗原CEA':            ['癌胚抗原CEA(发光)', '癌胚抗原CEA', '癌胚抗原 CEA', 'CEA', '癌胚抗原'],
    '细胞角化蛋白19片段':     ['细胞角化蛋白19片段(发光)', '细胞角化蛋白19片段', '细胞角化蛋白19片段 CYFRA 21-1', 'CYFRA21-1', 'Cyfra', '非小细胞肺癌相关抗原21-1'],
    '神经元特异性烯醇化酶':   ['神经元特异性烯醇化酶(发光)', '神经元特异性烯醇化酶', 'NSE', '神经元特异烯醇化酶测定'],
    '甲胎蛋白':               ['甲胎蛋白', '甲胎蛋白 AFP', 'AFP', '甲胎蛋白测定(AFP)'],
    '糖类抗原199':            ['糖类抗原199', '糖类抗原19-9', 'CA199', 'CA-199', 'CA19-9'],
    '糖类抗原125':            ['糖类抗原125', 'CA125', 'CA-125', '糖类抗原（CA-125）'],
    '糖类抗原153':            ['糖类抗原153', '糖类抗原15-3', 'CA153', 'CA-153', '糖类抗原（CA15-3）'],
    '糖类抗原724':            ['糖类抗原724', '糖类抗原72-4', 'CA724', 'CA72-4'],
    '鳞状细胞癌抗原':         ['鳞状细胞癌抗原', '鳞状细胞癌相关抗原', 'SCC'],
    '前列腺特异性抗原':       ['前列腺特异性抗原', 'PSA'],
    '人绒毛膜促性腺激素':     ['人绒毛膜促性腺激素', '总β亚单位人绒毛膜促性腺激素', 'β-HCG', 'HCG'],
    '人附睾蛋白4':            ['人附睾蛋白4', 'HE4', 'HE-4'],
    '罗马指数(绝经后)':       ['罗马指数(绝经后)', 'ROMA(绝经后)', 'ROMA指数(绝经后)', '绝经后ROMA值'],
    '罗马指数(绝经前)':       ['罗马指数(绝经前)', 'ROMA(绝经前)', 'ROMA指数(绝经前)', '绝经前ROMA值'],
    '抗缪勒氏管激素':         ['抗缪勒氏管激素', 'AMH'],
    'ProGRP':                 ['胃泌素释放肽前体', 'ProGRP'],
    '血管内皮生长因子':       ['血管内皮生长因子', 'VEGF'],

    # 尿液检测
    '尿微量白蛋白':           ['尿微量白蛋白', '尿白蛋白', 'mAlb', 'MALB', '尿微量蛋白'],
    '尿肌酐':                 ['尿肌酐', 'UCr', 'U-Cr'],
    '尿微量白蛋白/尿肌酐':    ['尿微量白蛋白/尿肌酐', '尿微量白蛋白/尿肌酐比值', 'ACR'],

    # 感染/免疫
    '人类免疫缺陷病毒抗体':   ['人类免疫缺陷病毒抗体', 'HIV抗体', 'HIV'],
    '乙肝表面抗原':           ['乙肝表面抗原(发光)', '乙肝表面抗原', 'HBsAg'],
    '乙肝表面抗体':           ['乙肝表面抗体(发光)', '乙肝表面抗体', 'HBsAb', 'Anti-HBs'],
    '乙肝e抗原':              ['乙肝e抗原(发光)', '乙肝e抗原', 'HBeAg'],
    '乙肝e抗体':              ['乙肝e抗体(发光)', '乙肝e抗体', 'HBeAb'],
    '乙肝核心抗体':           ['乙肝核心抗体(发光)', '乙肝核心抗体', 'HBcAb', 'Anti-HBc'],
}

# 建立别名→标准名的反向查找表，按别名长度降序排列以优先匹配较长字符串
_ALIAS_MAP = {}
for std_name, aliases in LAB_DICT.items():
    for alias in aliases:
        _ALIAS_MAP[alias] = std_name
_SORTED_ALIASES = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def clean_text(text):
    """清理文本：去空白字符、换行、全角空格"""
    if not text:
        return ''
    return text.strip().replace('\xa0', ' ').replace('\u3000', ' ').replace('\r', ' ')


def _find_std_name(candidate: str) -> str | None:
    """
    在别名表中匹配指标名称，返回标准名或 None。
    采用精确匹配：候选名与别名完全相等，或候选名以别名开头且候选名是别名+后缀（如"绝经前ROMA值"匹配"ROMA(绝经前)"）。
    避免短别名（如"钙""磷""血小板"）误匹配到包含它的长指标名（如"肌钙蛋白I""碱性磷酸酶""平均血小板体积"）。
    """
    candidate = candidate.strip()
    # 指标名通常较短；长文本（如病理诊断全文）直接丢弃，避免误匹配
    if not candidate or len(candidate) > 30:
        return None
    for alias in _SORTED_ALIASES:
        # 完全相等，或候选名以别名开头（候选名 = 别名 + 后缀，如"绝经前ROMA值" = "ROMA(绝经前)" 不成立，但"ROMA值"类需特殊处理）
        if candidate == alias:
            return _ALIAS_MAP[alias]
        # 候选名以别名开头且别名长度 >= 2（避免单字别名如"钙""磷"误匹配）
        if len(alias) >= 2 and candidate.startswith(alias):
            return _ALIAS_MAP[alias]
    return None


def _normalize_date(d: str) -> str:
    """标准化日期为 YYYY-MM-DD"""
    d = d.strip().replace('.', '-').replace('/', '-')
    parts = d.split('-')
    if len(parts) == 3 and len(parts[0]) == 2:
        parts[0] = '20' + parts[0]
        d = '-'.join(parts)
    return d


# ──────────────────────────────────────────────
# 文档全文提取
# ──────────────────────────────────────────────
def extract_docx_text(filepath: str) -> str:
    """
    遍历 docx 的表格单元格 + 段落，合并为全文。
    保留 tab 分隔符（实验室表用），段落间用换行分隔。
    """
    doc = Document(filepath)
    parts = []

    # 收集所有元素（段落 + 表格）按文档顺序
    from docx.oxml.ns import qn
    body = doc.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            # 段落
            from docx.text.paragraph import Paragraph
            p = Paragraph(child, doc)
            txt = p.text.strip()
            if txt:
                parts.append(txt)
        elif child.tag == qn('w:tbl'):
            # 表格
            from docx.table import Table
            t = Table(child, doc)
            for row in t.rows:
                cells = []
                for cell in row.cells:
                    cell_text = cell.text.strip().replace('\n', ' ').replace('\xa0', ' ')
                    cells.append(cell_text)
                # 跳过全空行
                if any(cells):
                    parts.append('\t'.join(cells))

    return '\n'.join(parts)


# ──────────────────────────────────────────────
# 区块切分
# ──────────────────────────────────────────────
def _is_lab_table_line(line: str) -> bool:
    """
    判断一行是否为真正的实验室表数据行（指标名\t数值\t单位\t参考值）或纯日期行。
    用于区分「实验室表」与「标签:长文本」段落（如"实验室检查及主要会诊\t2024.07.26生化-东..."），
    后者应归入出院块，以便 parse_discharge 提取紧凑格式并触发 parse_labs_compact。
    """
    line = line.strip()
    if not line:
        return False
    # 纯日期行（实验室表的日期分组）
    if re.match(r'^\d{4}[-./]\d{1,2}[-./]\d{1,2}$', line):
        return True
    if '\t' not in line:
        return False
    cells = line.split('\t')
    if len(cells) < 2:
        return False
    # 表头行
    if '项目名称' in cells[0] and '结果' in cells[1]:
        return True
    # 数据行：第二列为纯数值（可含 <>= 等符号，但不得含中文/字母/冒号等长文本特征）
    result = cells[1].strip()
    if re.match(r'^[<>≥≤=]?\s*[<>≥≤=]?\s*\d+\.?\d*', result) and not re.search(r'[\u4e00-\u9fff：:]', result):
        return True
    return False


def split_sections(full_text: str) -> dict:
    """
    将全文切分为「病理块 / 出院块 / 实验室表块」。
    返回 {'pathology': str, 'discharge': str, 'lab_table': str}
    """
    sections = {'pathology': '', 'discharge': '', 'lab_table': ''}

    # 1. 病理块：从"病理号[...]报告"到"报告时间"结束
    patho_pat = re.compile(
        r'(病理号\[[^\]]+\]报告.*?报告时间\s*\n?\s*[^\n]*)',
        re.DOTALL
    )
    m = patho_pat.search(full_text)
    if m:
        sections['pathology'] = m.group(1)
        # 从全文移除病理块
        full_text = full_text[:m.start()] + '\n' + full_text[m.end():]

    # 2. 实验室表块：仅保留真正的实验室表行（指标名\t数值\t单位\t参考值）和纯日期行
    #    标签:长文本段落（如"实验室检查及主要会诊\t2024.07.26生化-东..."）归入出院块，
    #    以便 parse_discharge 提取紧凑格式并触发 parse_labs_compact
    lab_lines = []
    remaining = []
    for line in full_text.split('\n'):
        if _is_lab_table_line(line):
            lab_lines.append(line)
        else:
            remaining.append(line)
    sections['lab_table'] = '\n'.join(lab_lines)
    full_text = '\n'.join(remaining)

    # 3. 出院块：剩余部分
    sections['discharge'] = full_text

    return sections


# ──────────────────────────────────────────────
# 病理解析
# ──────────────────────────────────────────────
# 免疫组化标记标准列名 → 别名
IHC_DICT = {
    '病理_ER':       ['ER'],
    '病理_PR':       ['PR'],
    '病理_Ki67':     ['Ki-67', 'Ki67', 'Ki-67'],
    '病理_p53':      ['P53', 'p53'],
    '病理_PAX8':     ['PAX-8', 'PAX8'],
    '病理_WT1':      ['WT-1', 'WT1'],
    '病理_CK7':      ['CK7'],
    '病理_CK20':     ['CK20'],
    '病理_P16':      ['P16'],
    '病理_MLH1':     ['MLH1'],
    '病理_MSH2':     ['MSH2'],
    '病理_MSH6':     ['MSH6'],
    '病理_PMS2':     ['PMS2'],
    '病理_PTEN':     ['PTEN'],
    '病理_NapsinA':  ['NapsinA', 'Napsin-A'],
    '病理_HNF1β':    ['HNF-1β', 'HNF1β', 'HNF-1B', 'HNF1B'],
    '病理_ARID1a':   ['ARID1a', 'ARID1A'],
    '病理_SATB2':    ['SATB2'],
    '病理_CDX2':     ['CDX-2', 'CDX2'],
    '病理_CA125':    ['CA125'],
    '病理_CA19-9':   ['CA19-9', 'CA199'],
    '病理_CEA':      ['CEA'],
    '病理_CD10':     ['CD10'],
    '病理_CDX-2':    ['CDX-2'],
    '病理_EMA':      ['EMA'],
    '病理_Vimentin': ['Vimentin', 'VIM'],
    '病理_Calretinin': ['Calretinin', 'CR'],
    '病理_D2-40':    ['D2-40'],
    '病理_Inhibin':  ['Inhibin', '抑制素'],
    '病理_CD99':     ['CD99'],
    '病理_CD56':     ['CD56'],
    '病理_Syn':      ['Syn', 'Synaptophysin'],
    '病理_CgA':      ['CgA', 'Chromogranin'],
    '病理_CD117':    ['CD117', 'c-kit'],
    '病理_CD34':     ['CD34'],
    '病理_S100':     ['S100'],
    '病理_HMB45':    ['HMB45'],
    '病理_MelanA':   ['MelanA', 'MART-1'],
    '病理_Desmin':   ['Desmin'],
    '病理_SMA':      ['SMA', '平滑肌肌动蛋白'],
    '病理_MyoD1':    ['MyoD1'],
    '病理_Myogenin': ['Myogenin'],
    '病理_CD3':      ['CD3'],
    '病理_CD20':     ['CD20'],
    '病理_CD45':     ['CD45', 'LCA'],
    '病理_CD30':     ['CD30'],
    '病理_CD15':     ['CD15'],
    '病理_ALK':      ['ALK'],
    '病理_BCL2':     ['BCL2', 'Bcl-2'],
    '病理_BCL6':     ['BCL6'],
    '病理_MUM1':     ['MUM1'],
    '病理_CD138':    ['CD138'],
    '病理_Kappa':    ['Kappa', 'κ'],
    '病理_Lambda':   ['Lambda', 'λ'],
    '病理_CD79a':    ['CD79a'],
    '病理_PAX5':     ['PAX5'],
    '病理_TdT':      ['TdT'],
    '病理_MPO':      ['MPO'],
    '病理_CD68':     ['CD68'],
    '病理_CD163':    ['CD163'],
    '病理_Langerin': ['Langerin'],
    '病理_CD1a':     ['CD1a'],
    '病理_CD4':      ['CD4'],
    '病理_CD8':      ['CD8'],
    '病理_FoxP3':    ['FoxP3'],
    '病理_PD-L1':    ['PD-L1'],
    '病理_MSI':      ['MSI'],
    '病理_MMR':      ['MMR'],
    '病理_HER2':     ['HER2', 'ERBB2'],
    '病理_EGFR':     ['EGFR'],
    '病理_VEGF':     ['VEGF'],
    '病理_CD31':     ['CD31'],
    '病理_CDX2':     ['CDX-2'],
    '病理_GCDFP15':  ['GCDFP-15', 'GCDFP15'],
    '病理_Mammaglobin': ['Mammaglobin'],
    '病理_GATA3':    ['GATA3'],
    '病理_TTF1':     ['TTF-1', 'TTF1'],
    '病理_NapsinA':  ['NapsinA'],
    '病理_P40':      ['P40'],
    '病理_P63':      ['P63'],
    '病理_CK5/6':    ['CK5/6', 'CK5', 'CK6'],
    '病理_CK8/18':   ['CK8/18', 'CK8', 'CK18'],
    '病理_CK19':     ['CK19'],
    '病理_CK20':     ['CK20'],
    '病理_CK7':      ['CK7'],
    '病理_CAM5.2':   ['CAM5.2'],
    '病理_AE1/AE3':  ['AE1/AE3', 'AE1', 'AE3'],
    '病理_34βE12':   ['34βE12', '34BE12'],
    '病理_HMWCK':    ['HMWCK'],
    '病理_LMWCK':    ['LMWCK'],
    '病理_CK':       ['CK'],
    '病理_EMA':      ['EMA'],
    '病理_Vimentin': ['Vimentin'],
    '病理_Desmin':   ['Desmin'],
    '病理_SMA':      ['SMA'],
    '病理_MyoD1':    ['MyoD1'],
    '病理_Myogenin': ['Myogenin'],
    '病理_CD34':     ['CD34'],
    '病理_CD117':    ['CD117'],
    '病理_S100':     ['S100'],
    '病理_HMB45':    ['HMB45'],
    '病理_MelanA':   ['MelanA'],
    '病理_CD31':     ['CD31'],
    '病理_CD68':     ['CD68'],
    '病理_CD163':    ['CD163'],
    '病理_CD1a':     ['CD1a'],
    '病理_Langerin': ['Langerin'],
    '病理_CD4':      ['CD4'],
    '病理_CD8':      ['CD8'],
    '病理_FoxP3':    ['FoxP3'],
    '病理_PD-L1':    ['PD-L1'],
    '病理_MSI':      ['MSI'],
    '病理_MMR':      ['MMR'],
    '病理_HER2':     ['HER2'],
    '病理_EGFR':     ['EGFR'],
    '病理_VEGF':     ['VEGF'],
    '病理_GCDFP15':  ['GCDFP-15'],
    '病理_Mammaglobin': ['Mammaglobin'],
    '病理_GATA3':    ['GATA3'],
    '病理_TTF1':     ['TTF-1'],
    '病理_P40':      ['P40'],
    '病理_P63':      ['P63'],
    '病理_CK5/6':    ['CK5/6'],
    '病理_CK8/18':   ['CK8/18'],
    '病理_CK19':     ['CK19'],
    '病理_CAM5.2':   ['CAM5.2'],
    '病理_AE1/AE3':  ['AE1/AE3'],
    '病理_34βE12':   ['34βE12'],
    '病理_HMWCK':    ['HMWCK'],
    '病理_LMWCK':    ['LMWCK'],
    '病理_CK':       ['CK'],
}

# 去重（保留第一个出现的标准名）
_IHC_DICT_DEDUP = {}
for k, v in IHC_DICT.items():
    if k not in _IHC_DICT_DEDUP:
        _IHC_DICT_DEDUP[k] = v
IHC_DICT = _IHC_DICT_DEDUP


def _parse_ihc_result(marker: str, context: str) -> str:
    """
    从免疫组化上下文中提取结果。
    格式：ER（+，约80%，强） / Ki-67（+，约10%） / P53（野生型高表达）
    返回：+ 80% 强 或 原始描述
    """
    # 匹配 标记（结果）
    pat = re.compile(
        re.escape(marker) + r'\s*[（(]\s*([^）)]+?)\s*[）)]'
    )
    m = pat.search(context)
    if m:
        return m.group(1).strip()
    # 匹配 标记：结果
    pat2 = re.compile(
        re.escape(marker) + r'\s*[：:]\s*([^,，;；。\n]+)'
    )
    m2 = pat2.search(context)
    if m2:
        return m2.group(1).strip()
    return ''


def parse_pathology(patho_text: str) -> dict:
    """
    从病理块文本中提取病理信息。
    返回字典：{病理_xxx: 值}
    """
    res = {}

    # 病理号
    m = re.search(r'病理号\[([^\]]+)\]', patho_text)
    if m:
        res['病理号'] = m.group(1).strip()

    # 基本信息（表格行：标签\t值）
    # 姓名/性别/年龄/门诊号/病人编号/病区/住院号/收到日期/送检科室/送检单位/送检医生/标本名称/临床诊断
    basic_map = {
        '姓': '病理_姓名', '性': '病理_性别', '年': '病理_年龄',
        '门': '病理_门诊号', '病人编号': '病理_病人编号',
        '病': '病理_病区', '住': '病理_住院号',
        '收到日期': '病理_收到日期', '送检科室': '病理_送检科室',
        '送检单位': '病理_送检单位', '送检医生': '病理_送检医生',
        '标本名称': '病理_标本名称', '临床诊断': '病理_临床诊断',
    }
    # 标签首字集合：用于区分"标签"和"值"（值通常是长文本，首字不会是标签首字）
    label_first_chars = set(k[0] for k in basic_map.keys())
    for line in patho_text.split('\n'):
        if '\t' not in line:
            continue
        cells = line.split('\t')
        if len(cells) < 2:
            continue
        # 表格每行是"标签 值 标签 值 ..."格式，但合并单元格会导致值重复。
        # 遍历所有成对单元格，但只处理"标签"（首字匹配标签首字），避免把重复值误当标签。
        for i in range(0, len(cells) - 1, 2):
            key = cells[i].strip()
            val = cells[i + 1].strip()
            if not key or not val:
                continue
            # 只处理标签行（key 较短，且首字符是标签首字）
            if len(key) > 10:
                continue
            key_first = key.replace(' ', '')[:1]
            if key_first not in label_first_chars:
                continue
            # 匹配标签
            for label, col in basic_map.items():
                if label in key and val:
                    res[col] = val
                    break

    # 肉眼所见
    m = re.search(r'肉眼所见\s*\n?\s*([^\n]*(?:[^\n]*\n[^\n]*){0,5})', patho_text)
    if m:
        res['病理_肉眼所见'] = m.group(1).strip()

    # 病理诊断（到"报告医生"或"杨浦免疫结果"前）
    m = re.search(r'病理诊断\s*\n?\s*(.+?)(?=\n\s*报告医生|\n\s*杨浦免疫结果|\n\s*免疫组化|\Z)', patho_text, re.DOTALL)
    if m:
        res['病理_诊断全文'] = m.group(1).strip()

    # 免疫组化结果（"杨浦免疫结果：" 或 "免疫组化：" 后）
    ihc_text = ''
    m = re.search(r'(?:杨浦免疫结果|免疫组化结果|免疫组化)[：:]\s*(.+?)(?=\n\s*报告医生|\n\s*审核医生|\Z)', patho_text, re.DOTALL)
    if m:
        ihc_text = m.group(1).strip()
        res['病理_免疫组化全文'] = ihc_text

    # 逐标记提取
    for col, markers in IHC_DICT.items():
        for marker in markers:
            val = _parse_ihc_result(marker, ihc_text)
            if val:
                res[col] = val
                break

    # 报告医生/审核医生/复诊医生/报告时间
    m = re.search(r'报告医生\s*\n?\s*([^\n]+)', patho_text)
    if m:
        res['病理_报告医生'] = m.group(1).strip()
    m = re.search(r'审核医生\s*\n?\s*([^\n]+)', patho_text)
    if m:
        res['病理_审核医生'] = m.group(1).strip()
    m = re.search(r'复诊医生\s*\n?\s*([^\n]+)', patho_text)
    if m:
        res['病理_复诊医生'] = m.group(1).strip()
    m = re.search(r'报告时间\s*\n?\s*([^\n]+)', patho_text)
    if m:
        res['病理_报告时间'] = m.group(1).strip()

    # 分期/分级/浸润/淋巴结转移（从病理诊断中提取）
    diag = res.get('病理_诊断全文', '')
    # 分期（FIGO 分期）
    m = re.search(r'(FIGO[^\s，。；]*|[ⅠII][A-Ca-c]?期|I[A-Ca-c]?期|II[A-Ca-c]?期|III[A-Ca-c]?期|IV[A-Ca-c]?期)', diag)
    if m:
        res['病理_分期'] = m.group(1).strip()
    # 分级
    m = re.search(r'([ⅠII]级|[1-3]级|低级别|高级别|低度恶性|高度恶性)', diag)
    if m:
        res['病理_分级'] = m.group(1).strip()
    # 浸润
    m = re.search(r'([^。；]*浸润[^。；]*)', diag)
    if m:
        res['病理_浸润'] = m.group(1).strip()
    # 淋巴结转移
    m = re.search(r'([^。；]*(?:淋巴结|淋巴)[^。；]*(?:转移|癌累及|未见)[^。；]*)', diag)
    if m:
        res['病理_淋巴结转移'] = m.group(1).strip()

    return res


# ──────────────────────────────────────────────
# 出院记录解析
# ──────────────────────────────────────────────
def parse_discharge(discharge_text: str) -> dict:
    """
    从出院块文本中提取出院记录信息。
    返回字典：{字段名: 值}
    """
    res = {}

    # 姓名/年龄/床号/住院号（格式：姓名：[xxx]）
    for label, col in [
        ('姓名', '患者姓名'), ('年龄', '年龄(岁)'), ('床号', '床号'),
        ('住院号', '住院号'), ('入院日期', '入院时间'), ('出院日期', '出院时间'),
    ]:
        m = re.search(re.escape(label) + r'\s*[：:]\s*\[([^\]]+)\]', discharge_text)
        if m:
            val = m.group(1).strip()
            if label == '年龄':
                val = ''.join(re.findall(r'\d+', val))
            res[col] = val

    # 门诊号（病理块中可能没有，从出院块补充）
    m = re.search(r'门诊号\s*[：:]\s*(\S+)', discharge_text)
    if m:
        res['门诊号'] = m.group(1).strip()

    # 入院诊断
    m = re.search(r'入院诊断\s*[：:]?\s*\[?([^\]]+?)\]?\s*(?=\n|出院诊断|入院时)', discharge_text, re.DOTALL)
    if m:
        res['入院诊断'] = m.group(1).strip()

    # 出院诊断
    m = re.search(r'出院诊断\s*[：:]?\s*\[?([^\]]+?)\]?\s*(?=\n|手术日期|出院医嘱|备注)', discharge_text, re.DOTALL)
    if m:
        res['出院诊断'] = m.group(1).strip()

    # 入院时主要症状及体征
    m = re.search(r'入院时主要症状及体征\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*实验室检查|\n\s*诊疗过程|\Z)', discharge_text, re.DOTALL)
    if m:
        res['入院时情况(全文)'] = m.group(1).strip()

    # 实验室检查及主要会诊（紧凑格式）
    m = re.search(r'实验室检查及主要会诊\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*诊疗过程|\n\s*出院时情况|\Z)', discharge_text, re.DOTALL)
    if m:
        res['实验室检查及主要会诊(全文)'] = m.group(1).strip()

    # 诊疗过程
    m = re.search(r'诊疗过程\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*出院时情况|\n\s*治疗结果|\Z)', discharge_text, re.DOTALL)
    if m:
        res['住院经过(全文)'] = m.group(1).strip()

    # 出院时情况
    m = re.search(r'出院时情况\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*治疗结果|\n\s*出院诊断|\Z)', discharge_text, re.DOTALL)
    if m:
        res['出院时情况(全文)'] = m.group(1).strip()

    # 治疗结果
    m = re.search(r'治疗结果\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*出院诊断|\n\s*手术日期|\Z)', discharge_text, re.DOTALL)
    if m:
        res['治疗结果'] = m.group(1).strip()

    # 手术日期
    m = re.search(r'手术日期\s*[：:]?\s*\[?([^\]]+?)\]?\s*(?=\n|麻醉方式)', discharge_text)
    if m:
        res['手术日期'] = m.group(1).strip()

    # 麻醉方式
    m = re.search(r'麻醉方式\s*[：:]?\s*\[?([^\]]+?)\]?\s*(?=\n|手术名称)', discharge_text)
    if m:
        res['麻醉方式'] = m.group(1).strip()

    # 手术名称
    m = re.search(r'手术名称\s*[：:]?\s*\[?([^\]]+?)\]?\s*(?=\n|术中所见)', discharge_text, re.DOTALL)
    if m:
        res['手术名称'] = m.group(1).strip()

    # 术中所见
    m = re.search(r'术中所见\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*特殊检查|\n\s*出院检查|\Z)', discharge_text, re.DOTALL)
    if m:
        res['术中所见'] = m.group(1).strip()

    # 特殊检查
    m = re.search(r'特殊检查\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*出院检查|\n\s*出院医嘱|\Z)', discharge_text, re.DOTALL)
    if m:
        res['特殊检查'] = m.group(1).strip()

    # 出院检查
    m = re.search(r'出院检查\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*出院医嘱|\n\s*备注|\Z)', discharge_text, re.DOTALL)
    if m:
        res['出院检查'] = m.group(1).strip()

    # 出院医嘱
    m = re.search(r'出院医嘱\s*\n?\s*\[?(.+?)\]?\s*(?=\n\s*备注|\Z)', discharge_text, re.DOTALL)
    if m:
        res['出院医嘱'] = m.group(1).strip()

    # 医师签名：格式复杂（住院医师:『带教医生』[ ]『』），且非核心字段，跳过
    # （避免引入错误数据）

    # 签名时间
    m = re.search(r'签名时间\s*[：:]\s*\[?([^\]]+?)\]?', discharge_text)
    if m:
        res['签名时间'] = m.group(1).strip()

    # 医院
    m = re.search(r'\[([^\]]*医院[^\]]*)\]', discharge_text)
    if m:
        res['医院'] = m.group(1).strip()

    return res


# ──────────────────────────────────────────────
# 实验室解析
# ──────────────────────────────────────────────
def _extract_items_in_segment(seg: str, date: str, labs: dict):
    """
    从单个日期段内容中提取所有实验室指标值。
    支持两种格式：
      - 指标名 数值 [↑↓]? 单位（空格分隔）
      - 指标名:数值单位（冒号分隔，如"谷丙转氨酶:110U/L↑"）
    """
    # 指标项之间用逗号或分号分隔，先按这些分隔符切分
    items = re.split(r'[,，;；]', seg)
    for item in items:
        item = item.strip()
        if not item:
            continue

        # 尝试匹配 指标名:数值单位（冒号分隔，无空格），箭头(↑↓)在单位之后。
        # 用正则直接匹配"指标名:值"，让 (.+?) 匹配到指标名（可能含检查组名前缀，如"凝血常规-东(19987048)：D-二聚体测定 :4.71mg/L↑"，
        # 此时 (.+?) 会匹配到最后一个冒号前的"凝血常规-东(19987048)：D-二聚体测定"，再经 _find_std_name 匹配指标名）。
        m = re.match(
            r'^(.+?)\s*[：:]\s*((?:[<>≥≤=]?\s*)?(?:>|<|≥|≤)?(?:\d+\.?\d*(?:e[+-]?\d+)?))\s*([^\s,，;；。\n\r↑↓]{0,30})\s*[↑↓]?$',
            item
        )
        if m:
            name_candidate, value, unit = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        else:
            # 尝试匹配 指标名 数值单位（空格分隔），箭头(↑↓)在单位之后
            m = re.match(
                r'^(.+?)\s+((?:[<>≥≤=]?\s*)?(?:>|<|≥|≤)?(?:\d+\.?\d*(?:e[+-]?\d+)?))\s*([^\s,，;；。\n\r↑↓]{0,30})\s*[↑↓]?$',
                item
            )
            if not m:
                continue
            name_candidate, value, unit = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

        # 若候选名含检查组名前缀（如"凝血常规-东(19987048)：D-二聚体测定"），
        # 尝试从候选名中提取真正的指标名（最后一个冒号后的部分）
        if '：' in name_candidate or ':' in name_candidate:
            colon_pos = max(name_candidate.rfind('：'), name_candidate.rfind(':'))
            tail = name_candidate[colon_pos + 1:].strip()
            if tail:
                name_candidate = tail

        std = _find_std_name(name_candidate)
        if std is None:
            continue  # 不在字典中的指标丢弃

        val_str = f"{value}{unit}" if unit else value
        if std not in labs:
            labs[std] = []
        # 去重：同一天同一值不重复添加
        if (date, val_str) not in labs[std]:
            labs[std].append((date, val_str))


def parse_labs_compact(text: str) -> dict:
    """
    从"实验室检查及主要会诊"紧凑格式中解析实验室指标。
    格式：检查组名：日期 (指标：值单位 指标：值单位 ...)
    返回：{标准指标名: [(date, value_str)]}
    """
    labs = {}

    # 匹配 检查组名：日期 (指标：值单位 ...)
    # 日期格式：YYYY-MM-DD 或 YYYY.MM.DD
    date_pat = re.compile(r'(\d{4}[-./]\d{2}[-./]\d{2})')
    segments = date_pat.split(text)

    current_date = None
    for seg in segments:
        m = date_pat.fullmatch(seg.strip())
        if m:
            current_date = _normalize_date(seg.strip())
            continue
        if current_date is None:
            continue
        _extract_items_in_segment(seg, current_date, labs)

    return labs


def parse_lab_table(lab_table_text: str) -> dict:
    """
    从 tab 分隔的实验室表解析指标。
    格式：
      日期
      项目名称\t结果\t单位\t参考值
      指标名\t值\t单位\t参考值
    返回：{标准指标名: [(date, value_str)]}
    """
    labs = {}
    current_date = None

    for line in lab_table_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        # 日期行
        m = re.match(r'^(\d{4}[-./]\d{1,2}[-./]\d{1,2})$', line)
        if m:
            current_date = _normalize_date(m.group(1))
            continue

        # 表头行（跳过）
        if '项目名称' in line and '结果' in line:
            continue

        # 数据行：指标名\t结果\t单位\t参考值
        if '\t' in line:
            cells = line.split('\t')
            if len(cells) < 2:
                continue
            name = cells[0].strip()
            result = cells[1].strip()
            if not name or not result:
                continue
            # 单位（可选）
            unit = cells[2].strip() if len(cells) > 2 else ''

            std = _find_std_name(name)
            if std is None:
                continue

            val_str = f"{result}{unit}" if unit else result
            if std not in labs:
                labs[std] = []
            if (current_date, val_str) not in labs[std]:
                labs[std].append((current_date, val_str))

    return labs


def merge_labs(*lab_dicts) -> dict:
    """
    合并多个实验室解析结果（按指标名合并，多时间值用分号连接）。
    返回：{标准指标名: "日期:值;日期:值"}
    """
    merged = {}
    for lab_dict in lab_dicts:
        for std, pairs in lab_dict.items():
            if std not in merged:
                merged[std] = []
            for p in pairs:
                if p not in merged[std]:
                    merged[std].append(p)
    # 排序时处理 date 为 None 的情况（实验室表中数据行可能出现在日期行之前）
    return {k: ';'.join(f"{d}:{v}" for d, v in sorted(pairs, key=lambda p: (p[0] or '', p[1]))) for k, pairs in merged.items()}


# ──────────────────────────────────────────────
# 单文件处理
# ──────────────────────────────────────────────
def process_file(filepath: str) -> dict | None:
    try:
        full_text = extract_docx_text(filepath)
        if not full_text:
            return None

        res = {}

        # 路径信息
        res['路径'] = os.path.abspath(filepath)
        parts = os.path.abspath(filepath).split(os.sep)
        # 路径结构：.../clinical informations/分组文件夹/文件名.docx
        res['分组'] = parts[-2] if len(parts) >= 2 else ''
        res['文件名'] = os.path.splitext(parts[-1])[0]
        res['记录类型'] = '出院记录+病理报告'

        # 切分区块
        sections = split_sections(full_text)

        # 病理解析
        if sections['pathology']:
            patho = parse_pathology(sections['pathology'])
            res.update(patho)

        # 出院解析
        if sections['discharge']:
            discharge = parse_discharge(sections['discharge'])
            res.update(discharge)

        # 性别/年龄：出院记录无可提取字段时，从病理块补充
        if '性别' not in res and '病理_性别' in res:
            res['性别'] = res['病理_性别']
        if '年龄(岁)' not in res and '病理_年龄' in res:
            res['年龄(岁)'] = ''.join(re.findall(r'\d+', str(res['病理_年龄'])))

        # 实验室解析（双通道）
        labs_compact = {}
        labs_table = {}
        if '实验室检查及主要会诊(全文)' in res:
            labs_compact = parse_labs_compact(res['实验室检查及主要会诊(全文)'])
        if sections['lab_table']:
            labs_table = parse_lab_table(sections['lab_table'])
        labs = merge_labs(labs_compact, labs_table)
        res.update(labs)

        # record_id：住院号_出院日期
        out_date = res.get('出院时间', '').split(' ')[0].replace('-', '')
        res['record_id'] = f"{res.get('住院号', res.get('文件名', ''))}_{out_date}"

        return res

    except Exception as e:
        print(f"  [ERROR] {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def main():
    files = glob.glob(os.path.join(INPUT_DIR, '**', '*.docx'), recursive=True)
    print(f"发现 {len(files)} 个 .docx 文件。")

    all_data = []
    for f in sorted(files):
        print(f"  处理: {f}")
        data = process_file(f)
        if data:
            all_data.append(data)
        else:
            print(f"  [SKIP] 无法提取: {f}")

    if not all_data:
        print("未提取到任何数据，请检查文件结构。")
        return

    df = pd.DataFrame(all_data)

    # 列排序：固定列在前，实验室指标居中，病理列在后
    FIXED_COLS = [
        'record_id', '患者姓名', '性别', '年龄(岁)', '住院号', '门诊号',
        '床号', '医院', '入院时间', '出院时间',
        '记录类型', '签名时间',
        '入院诊断', '出院诊断', '治疗结果',
        '手术日期', '麻醉方式', '手术名称', '术中所见',
        '特殊检查', '出院检查',
        '入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)', '出院医嘱',
        '实验室检查及主要会诊(全文)',
    ]
    lab_cols  = sorted([c for c in df.columns if c in LAB_DICT])
    patho_cols = sorted([c for c in df.columns if c.startswith('病理_')])

    _exclude = set(FIXED_COLS) | set(lab_cols) | set(patho_cols) | {
        '路径', '分组', '文件名',
    }
    other_cols = sorted([c for c in df.columns if c not in _exclude])
    meta_cols = ['分组', '文件名', '路径']

    ordered = (
        [c for c in FIXED_COLS if c in df.columns]
        + [c for c in lab_cols if c not in FIXED_COLS]
        + [c for c in patho_cols if c not in FIXED_COLS]
        + [c for c in other_cols]
        + [c for c in meta_cols if c in df.columns]
    )
    # 追加任何未在有序列表中的列
    remaining = [c for c in df.columns if c not in ordered]
    ordered += remaining

    df = df[ordered]
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')

    print(f"\n[OK] 成功提取 {len(all_data)} 条记录")
    print(f"     列数: {len(df.columns)}")
    print(f"     输出: {OUTPUT_FILE}")

    # 简要统计实验室指标填充率
    lab_in_df = [c for c in LAB_DICT if c in df.columns]
    if lab_in_df:
        filled = df[lab_in_df].notna().sum()
        print(f"\n实验室指标填充情况（非空记录数）：")
        for col in lab_in_df:
            cnt = int(filled[col])
            if cnt > 0:
                print(f"   {col}: {cnt}/{len(df)}")

    # 统计病理指标填充率
    patho_in_df = [c for c in df.columns if c.startswith('病理_')]
    if patho_in_df:
        p_filled = df[patho_in_df].notna().sum()
        print(f"\n病理指标填充情况（非空记录数）：")
        for col in patho_in_df:
            cnt = int(p_filled[col])
            if cnt > 0:
                print(f"   {col}: {cnt}/{len(df)}")


if __name__ == '__main__':
    main()