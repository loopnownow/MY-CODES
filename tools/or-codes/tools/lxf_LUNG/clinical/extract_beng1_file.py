"""
从合并后的 Word 文档（linai）提取出院记录数据。
- 使用 pandoc 将 docx 转为 Markdown 文本
- 切分规则：每个病例以「蚌埠医科大学/医学院第一附属医院」开头
- 解析逻辑：复用 extract_beng1xianai copy.py 的高精度字典和提取器
"""

import os
import re
import subprocess
import pandas as pd
from markitdown import MarkItDown

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
INPUT_DOCX  = r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1\linai2\鳞癌.docx'
TEMP_MD     = r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1\linai2\data_temp.md'
OUTPUT_FILE = r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1\beng1linai2.csv'

# 核心关键字列表（仅这些关键字的加粗文本触发字段切分）
CORE_HEADERS = [
    '姓名', '性别', '年龄', '出院科别', '出院科室', '床号', '住院号',
    '入院时间', '出院时间', '住院天数', '入院诊断', '出院诊断',
    '治疗结果', '入院时情况', '住院经过', '出院时情况', '出院医嘱',
    '医师签名', '记录时间'
]

# 标准列名映射
HEADER_MAPPING = {
    '姓名':     '患者姓名',
    '性别':     '性别',
    '年龄':     '年龄(岁)',
    '出院科别': '出院科室',
    '出院科室': '出院科室',
    '床号':     '床号',
    '住院号':   '住院号',
    '入院时间': '入院时间',
    '出院时间': '出院时间',
    '住院天数': '住院天数',
    '入院诊断': '入院诊断',
    '出院诊断': '出院诊断',
    '治疗结果': '治疗结果',
    '入院时情况': '入院时情况(全文)',
    '住院经过':  '住院经过(全文)',
    '出院时情况':'出院时情况(全文)',
    '出院医嘱':  '出院医嘱',
    '医师签名':  '医师签名',
    '记录时间':  '记录时间',
}

# ──────────────────────────────────────────────
# 实验室指标标准名称字典（来自 extract_beng1xianai copy.py）
# ──────────────────────────────────────────────
LAB_DICT = {
    # 血常规
    '白细胞计数':             ['白细胞计数', 'WBC计数', '白细胞'],
    '中性粒细胞计数':         ['中性粒细胞计数', '中性粒细胞绝对值'],
    '中性粒细胞百分比':       ['中性粒细胞百分比', '中性粒细胞%', '中性粒细胞 %'],
    '淋巴细胞计数':           ['淋巴细胞计数', '淋巴细胞绝对值'],
    '淋巴细胞百分比':         ['淋巴细胞百分比', '淋巴细胞%', '淋巴细胞 %', '淋巴细胞'],
    '单核细胞计数':           ['单核细胞计数', '单核细胞绝对值'],
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
    '纤维蛋白降解产物':       ['纤维蛋白(原)降解产物', '纤维蛋白原降解产物', 'FDP'],
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
    '钾离子':                 ['钾离子', 'K+', '血钾', 'K'],
    '钠离子':                 ['钠离子', 'Na+', '血钠', 'Na'],
    '氯离子':                 ['氯离子', 'Cl-', '血氯', 'Cl'],
    '总钙':                   ['总钙离子', '总钙', 'Ca²+', 'Ca2+', 'Ca'],
    '磷':                     ['无机磷', '血磷', 'P'],
    '碳酸氢盐':               ['碳酸氢盐(HCO3-)', '碳酸氢盐', 'HCO3-', 'HCO3'],
    '阴离子间隙':             ['阴离子间隙', 'AG'],
    '葡萄糖':                 ['葡萄糖', 'GLU', 'Glu'],
    # 血脂
    '总胆固醇':               ['总胆固醇', 'TC', 'CHOL'],
    '甘油三酯':               ['甘油三酯', 'TG', 'TGC'],
    '高密度脂蛋白胆固醇':     ['高密度脂蛋白胆固醇', 'HDL-C', 'HDL'],
    '低密度脂蛋白胆固醇':     ['低密度脂蛋白胆固醇', 'LDL-C', 'LDL'],
    '脂蛋白a':                ['脂蛋白a', 'LP(a)', 'Lp(a)'],
    # 心肌酶/心肌标志物
    '乳酸脱氢酶':             ['乳酸脱氢酶', 'LDH'],
    '肌酸激酶':               ['肌酸激酶', 'CK', 'CPK'],
    '肌酸激酶同工酶':         ['肌酸激酶同工酶', 'CK-MB', 'CKMB'],
    '肌钙蛋白I':              ['肌钙蛋白I', 'cTnI', 'TnI'],
    '肌钙蛋白T':              ['肌钙蛋白T', 'cTnT', 'TnT'],
    '肌红蛋白':               ['肌红蛋白', 'MYO', 'Mb'],
    'N端脑钠肽前体':          ['N端脑钠肽前体', 'NT-proBNP', 'BNP'],
    # 炎症/免疫
    'C反应蛋白':              ['C反应蛋白', 'CRP', 'hs-CRP'],
    '血清淀粉样蛋白A':        ['血清淀粉样蛋白A', '血清淀粉样蛋白', 'SAA'],
    '降钙素原':               ['降钙素原', 'PCT'],
    '白介素6':                ['白介素6', 'IL-6', 'IL6'],
    '铁蛋白':                 ['铁蛋白', 'Ferritin'],
    '红细胞沉降率':           ['红细胞沉降率', 'ESR', '血沉'],
    # 甲状腺功能
    '促甲状腺激素':           ['促甲状腺激素', 'TSH'],
    '三碘甲腺原氨酸':         ['三碘甲腺原氨酸', 'T3', 'FT3', '游离T3'],
    '甲状腺素':               ['甲状腺素', 'T4', 'FT4', '游离T4'],
    # 肿瘤标志物
    '癌胚抗原CEA':            ['癌胚抗原CEA(发光)', '癌胚抗原CEA', 'CEA'],
    '细胞角化蛋白19片段':     ['细胞角化蛋白19片段(发光)', '细胞角化蛋白19片段', 'CYFRA21-1', 'Cyfra'],
    '神经元特异性烯醇化酶':   ['神经元特异性烯醇化酶(发光)', '神经元特异性烯醇化酶', 'NSE'],
    '甲胎蛋白':               ['甲胎蛋白', 'AFP'],
    '糖类抗原199':            ['糖类抗原199', 'CA199', 'CA-199', 'CA19-9'],
    '糖类抗原125':            ['糖类抗原125', 'CA125', 'CA-125'],
    '糖类抗原153':            ['糖类抗原153', 'CA153', 'CA-153'],
    '糖类抗原724':            ['糖类抗原724', 'CA724', 'CA72-4'],
    '鳞状细胞癌抗原':         ['鳞状细胞癌抗原', 'SCC'],
    '前列腺特异性抗原':       ['前列腺特异性抗原', 'PSA'],
    '人绒毛膜促性腺激素':     ['人绒毛膜促性腺激素', 'HCG', 'β-HCG'],
    'ProGRP':                 ['胃泌素释放肽前体', 'ProGRP'],
    '血管内皮生长因子':       ['血管内皮生长因子', 'VEGF'],
    # 血气分析
    'pH值':                   ['PH值', 'pH值', 'pH'],
    '动脉氧分压':             ['氧分压', 'PaO2', 'PO2'],
    '动脉二氧化碳分压':       ['二氧化碳分压', 'PaCO2', 'PCO2'],
    '血氧饱和度':             ['血氧饱和度', 'SaO2', 'SpO2'],
    '氧含量':                 ['氧含量'],
    '二氧化碳总量':           ['二氧化碳总量(tCO2)', '二氧化碳总量', 'tCO2'],
    'P50':                    ['P50.e', 'P50'],
    # 乙肝五项
    '乙肝表面抗原':           ['乙肝表面抗原(发光)', '乙肝表面抗原', 'HBsAg'],
    '乙肝表面抗体':           ['乙肝表面抗体(发光)', '乙肝表面抗体', 'HBsAb', 'Anti-HBs'],
    '乙肝e抗原':              ['乙肝e抗原(发光)', '乙肝e抗原', 'HBeAg'],
    '乙肝e抗体':              ['乙肝e抗体(发光)', '乙肝e抗体', 'HBeAb'],
    '乙肝核心抗体':           ['乙肝核心抗体(发光)', '乙肝核心抗体', 'HBcAb', 'Anti-HBc'],
    'HBV-DNA':                ['HBV-DNA定量', 'HBV DNA', 'HBV-DNA'],
    # 糖代谢
    '糖化血红蛋白':           ['糖化血红蛋白', 'HbA1c', 'A1c'],
    '糖化白蛋白':             ['糖化白蛋白', 'GA'],
    '空腹血糖':               ['空腹血糖', 'FBG', 'FPG'],
    # 尿液检测
    '尿微量白蛋白':           ['尿微量白蛋白', '尿白蛋白', 'mAlb', 'MALB'],
    '尿肌酐':                 ['尿肌酐', 'UCr', 'U-Cr'],
}

# 建立别名→标准名的反向查找表，按别名长度降序排列以优先匹配较长字符串
_ALIAS_MAP = {}
for _std_name, _aliases in LAB_DICT.items():
    for _alias in _aliases:
        _ALIAS_MAP[_alias] = _std_name
_SORTED_ALIASES = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)

# ──────────────────────────────────────────────
# 基因检测指标字典
# ──────────────────────────────────────────────
GENE_DICT = {
    'EGFR突变状态':        ['EGFR基因', 'EGFR突变', 'EGFR'],
    'EGFR_19外显子缺失':   ['19号外显子缺失', '19del', 'exon19缺失', 'Exon 19 del', '第19外显子缺失'],
    'EGFR_20外显子插入':   ['20号外显子插入', 'exon20ins', 'Exon 20 ins', '20外显子插入'],
    'EGFR_21外显子L858R':  ['L858R', '21号外显子', 'exon21', 'Exon 21'],
    'EGFR_T790M':          ['T790M'],
    'ALK融合状态':         ['ALK融合', 'ALK重排', 'ALK基因', 'ALK-EML4', 'EML4-ALK', 'ALK阳性', 'ALK阴性', 'ALK'],
    'ROS1融合状态':        ['ROS1融合', 'ROS1重排', 'ROS1基因', 'ROS1'],
    'RET融合状态':         ['RET融合', 'RET重排', 'RET基因', 'RET'],
    'MET状态':             ['MET扩增', 'MET跳跃突变', 'METex14', 'MET14', 'MET基因', 'MET'],
    'KRAS突变状态':        ['KRAS突变', 'KRAS基因', 'KRAS G12C', 'KRAS'],
    'BRAF突变状态':        ['BRAF V600E', 'BRAF突变', 'BRAF基因', 'BRAF'],
    'HER2状态':            ['HER2扩增', 'HER2突变', 'ERBB2扩增', 'ERBB2突变', 'HER2', 'ERBB2'],
    'NTRK融合状态':        ['NTRK融合', 'NTRK1融合', 'NTRK2融合', 'NTRK3融合', 'NTRK'],
    'PIK3CA突变状态':      ['PIK3CA突变', 'PIK3CA基因', 'PIK3CA'],
    'TP53突变状态':        ['TP53突变', 'TP53基因', 'TP53'],
    'STK11突变状态':       ['STK11突变', 'STK11基因', 'STK11', 'LKB1'],
    'KEAP1突变状态':       ['KEAP1突变', 'KEAP1基因', 'KEAP1'],
    'PD-L1_TPS':           ['TPS', 'PD-L1 TPS', 'PD-L1表达（TPS）'],
    'PD-L1_CPS':           ['CPS', 'PD-L1 CPS', 'PD-L1表达（CPS）'],
    'PD-L1表达状态':       ['PD-L1表达', 'PD-L1阳性', 'PD-L1阴性', 'PD-L1（22C3）',
                            'PD-L1（SP142）', 'PD-L1（28-8）', 'PD-L1'],
    'MSI状态':             ['MSI-H', 'MSI-L', 'MSS', '微卫星不稳定', '微卫星稳定',
                            'dMMR', 'pMMR', 'MMR缺失', 'MSI'],
    'TMB':                 ['TMB-H', 'TMB-L', '肿瘤突变负荷', 'TMB'],
    'BRCA1状态':           ['BRCA1突变', 'BRCA1基因', 'BRCA1'],
    'BRCA2状态':           ['BRCA2突变', 'BRCA2基因', 'BRCA2'],
    '基因检测方法':        ['NGS', '二代测序', '全基因组测序', 'WGS', '全外显子组测序', 'WES',
                            '液态活检', '血液ctDNA', '荧光原位杂交', 'FISH',
                            '免疫组织化学法', 'ARMS-PCR', 'qPCR'],
    '基因检测样本':        ['血液样本', '组织样本', '痰液', 'ctDNA', '血浆', '石蜡切片'],
}

# ──────────────────────────────────────────────
# 治疗药物关键词
# ──────────────────────────────────────────────
CHEMO_DRUGS = [
    '培美曲塞', '卡铂', '顺铂', '奈达铂', '洛铂', '奥沙利铂',
    '紫杉醇', '多西他赛', '白蛋白紫杉醇', '吉西他滨', '长春瑞滨',
    '依托泊苷', '伊立替康', '替吉奥', '卡培他滨',
    '信迪利单抗', '帕博利珠单抗', '替雷利珠单抗', '阿替利珠单抗',
    '度伐利尤单抗', '卡瑞利珠单抗', '纳武利尤单抗', '舒格利单抗',
    '贝莫苏拜单抗', '派安普利单抗', '恩沃利单抗',
    '厄洛替尼', '吉非替尼', '阿法替尼', '奥希替尼', '达可替尼',
    '克唑替尼', '阿来替尼', '洛拉替尼', '恩沙替尼', '布格替尼',
    '仑伐替尼', '索拉非尼', '贝伐珠单抗', '安罗替尼', '阿帕替尼',
    '唑来膦酸', '地舒单抗', '伊班膦酸',
]

EFFICACY_PATTERNS = {
    'CR': [r'完全缓解', r'\bCR\b'],
    'PR': [r'部分缓解', r'缩小', r'\bPR\b'],
    'SD': [r'疾病稳定', r'稳定（小）', r'大致相仿', r'\bSD\b'],
    'PD': [r'疾病进展', r'进展', r'\bPD\b'],
}

ADR_KEYWORDS = [
    '骨髓抑制', '粒细胞减少', '白细胞减少', '血小板减少', '贫血',
    '恶心', '呕吐', '腹泻', '腹痛', '胃肠道反应',
    '发热', '乏力', '皮疹', '口腔黏膜炎', '神经毒性',
    '肝功能损害', '肾功能损害', '心脏毒性',
]

SUPPORT_KEYWORDS = [
    '止吐', '护胃', '抑酸', '补液', '升白针', '重组人粒细胞',
    '唑来膦酸', '地舒单抗', '镇痛', '止痛', '抗感染',
    '雾化排痰', '化痰', '保肝', '降糖', '纠酸',
]

# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def clean_text(text):
    """清理常见特殊空白字符"""
    return text.strip().replace('\xa0', ' ').replace('\u3000', ' ')


def _find_std_name(candidate: str):
    """在别名表中匹配指标名称，返回标准名或 None"""
    candidate = candidate.strip()
    for alias in _SORTED_ALIASES:
        if alias in candidate or candidate in alias:
            return _ALIAS_MAP[alias]
    return None


# ──────────────────────────────────────────────
# 实验室指标解析（高精度版，来自 extract_beng1xianai copy.py）
# ──────────────────────────────────────────────
def parse_labs(text: str) -> dict:
    """
    从文本中解析实验室指标。
    支持格式：2021-08-16 血常规(5分类)：白细胞计数 6.28 ×10^9/L，...
    返回 {标准指标名: "日期:值单位;日期:值单位"}
    """
    labs = {}
    date_pat = re.compile(r'(\d{4}[-./]\d{2}[-./]\d{2})')
    segments = date_pat.split(text)
    current_date = None
    for seg in segments:
        m = date_pat.fullmatch(seg.strip())
        if m:
            current_date = seg.strip().replace('.', '-').replace('/', '-')
            continue
        if current_date is None:
            continue
        _extract_items_in_segment(seg, current_date, labs)
    return {k: ';'.join(f"{d}:{v}" for d, v in sorted(pairs)) for k, pairs in labs.items()}


def _extract_items_in_segment(seg: str, date: str, labs: dict):
    """从单个日期段内容中提取所有实验室指标值"""
    # 先清理 pandoc markdown 转义字符（如 \^）
    seg = seg.replace(r'\^', '^').replace('\\', '')
    items = re.split(r'[,，;；]', seg)
    for item in items:
        item = item.strip()
        if not item:
            continue
        # 处理"检查组名:指标项"的格式，取冒号后面
        if '：' in item or ':' in item:
            colon_pos = max(item.find('：'), item.find(':'))
            tail = item[colon_pos + 1:].strip()
            if tail:
                item = tail

        # 匹配：指标名 + 空格 + 数值 + 可选箭头 + 单位
        m = re.match(
            r'^(.+?)\s+((?:[<>≥≤=]?\s*)?(?:>|<|≥|≤)?(?:\d+\.?\d*(?:e[+-]?\d+)?))\s*[↑↓]?\s*([^\s,，;；。\n\r↑↓]{0,30})$',
            item.strip()
        )
        if not m:
            continue
        name_candidate, value, unit = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

        std = _find_std_name(name_candidate)
        if std is None:
            continue

        val_str = f"{value}{unit}" if unit else value
        if std not in labs:
            labs[std] = []
        if (date, val_str) not in labs[std]:
            labs[std].append((date, val_str))


# ──────────────────────────────────────────────
# 体格检查提取
# ──────────────────────────────────────────────
def parse_anthropometry(text: str) -> dict:
    """从文本中提取身高、体重、BMI"""
    res = {}
    clean_t = text.replace('|', ' ').replace('\n', ' ')
    m_h = re.search(r'身高[：: ]?\s*(\d+(?:\.\d+)?)\s*cm', clean_t, re.I)
    if m_h:
        res['身高(cm)'] = m_h.group(1)
    m_w = re.search(r'体重[：: ]?\s*(\d+(?:\.\d+)?)\s*kg', clean_t, re.I)
    if m_w:
        res['体重(kg)'] = m_w.group(1)
    m_bmi = re.search(r'BMI[：: ]?\s*(\d+(?:\.\d+)?)', clean_t, re.I)
    if m_bmi:
        res['BMI'] = m_bmi.group(1)
    elif '身高(cm)' in res and '体重(kg)' in res:
        try:
            h, w = float(res['身高(cm)']) / 100, float(res['体重(kg)'])
            if h > 0:
                res['BMI'] = round(w / (h * h), 1)
        except Exception:
            pass
    return res


# ──────────────────────────────────────────────
# 治疗经过结构化提取
# ──────────────────────────────────────────────
def parse_treatment(text: str) -> dict:
    """结构化提取化疗方案/疗效/不良反应/支持治疗"""
    result = {}

    # 1. 化疗日期+方案
    chemo_date_pat = re.compile(
        r'(?:于|遂于|起行|开始|自)\s*'
        r'(\d{4}[-./]\d{2}[-./]\d{2})'
        r'[^\n。；]*?'
        r'(?:行|给予|予以|予|起行|开始)?'
        r'\s*["""「『]?(.{5,120}?)["""」』]?'
        r'(?:方案|联合|治疗)',
        re.DOTALL
    )
    m = chemo_date_pat.search(text)
    if m:
        result['化疗_日期'] = m.group(1).replace('.', '-').replace('/', '-')
        result['化疗_方案'] = m.group(2).strip()

    # 2. 药物列表
    found_drugs = []
    for drug in CHEMO_DRUGS:
        if drug in text:
            dose_pat = re.compile(
                re.escape(drug) + r'\s*(\d+\.?\d*\s*(?:mg|g|ml|IU|U)(?:/(?:m²|m2|kg))?)?'
                r'(?:\s*[Dd]\d+)?'
            )
            dm = dose_pat.search(text)
            if dm:
                dose = dm.group(1).strip() if dm.group(1) else ''
                entry = f"{drug} {dose}".strip() if dose else drug
                if drug not in [d.split()[0] for d in found_drugs]:
                    found_drugs.append(entry)
    if found_drugs:
        result['化疗_药物列表'] = ','.join(found_drugs)

    # 3. 治疗类型
    types = []
    if any(d in text for d in ['化疗', '培美曲塞', '卡铂', '顺铂', '吉西他滨', '紫杉醇', '依托泊苷']):
        types.append('化疗')
    if any(d in text for d in ['信迪利单抗', '帕博利珠单抗', '替雷利珠单抗', '阿替利珠单抗',
                                '卡瑞利珠单抗', '纳武利尤单抗', '免疫治疗', '免疫检查点']):
        types.append('免疫治疗')
    if any(d in text for d in ['奥希替尼', '厄洛替尼', '吉非替尼', '阿法替尼', '克唑替尼',
                                '阿来替尼', '靶向治疗', '靶向药']):
        types.append('靶向治疗')
    if any(k in text for k in ['手术', '切除', '胸腔镜', '开胸']):
        types.append('手术')
    if any(k in text for k in ['放疗', '放射治疗', '伽马刀', '射波刀']):
        types.append('放疗')
    if types:
        result['治疗类型'] = '+'.join(types)

    # 4. 疗效评估
    efficacy_context = re.search(
        r'(?:疗效|病情|评估|评价|疾病|病灶|复查|缩小|稳定|进展).{0,100}', text
    )
    ctx_text = efficacy_context.group(0) if efficacy_context else text
    found_efficacy = []
    for code, patterns in EFFICACY_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, ctx_text):
                found_efficacy.append(code)
                break
    if found_efficacy:
        result['疗效评估'] = '/'.join(found_efficacy)

    # 5. 不良反应
    found_adrs = [k for k in ADR_KEYWORDS if k in text]
    if found_adrs:
        result['不良反应'] = ','.join(found_adrs)

    # 6. 支持治疗
    found_support = [k for k in SUPPORT_KEYWORDS if k in text]
    if found_support:
        result['支持治疗'] = ','.join(found_support)

    return result


# ──────────────────────────────────────────────
# 基因检测状态提取
# ──────────────────────────────────────────────
_POSITIVE_WORDS = ['阳性', '突变', '融合', '扩增', '重排', '插入', '缺失', 'positive', 'mut', 'del', 'ins', 'H']
_NEGATIVE_WORDS = ['阴性', '未检出', '野生型', '未突变', '阴', 'negative', 'WT', 'wild']


def _extract_gene_result(context: str) -> str:
    """从上下文中提取简洁的基因检测结果描述"""
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', context)
    if m:
        return m.group(0)
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:mut/Mb|个/Mb|mutations/Mb)', context, re.I)
    if m:
        return m.group(0)
    for w in _POSITIVE_WORDS:
        if w in context:
            mut_m = re.search(r'([A-Z]\d+[A-Z]|exon\s*\d+|第\d+外显子|[a-z]{1,10}\d+)', context, re.I)
            suffix = f'({mut_m.group(0)})' if mut_m else ''
            return f'阳性{suffix}'
    for w in _NEGATIVE_WORDS:
        if w in context:
            return '阴性/野生型'
    return context.strip()[:40]


def parse_gene_report(text: str) -> dict:
    """从住院经过/病理报告文本中结构化提取基因检测相关指标"""
    gene_res = {}
    gene_section_pat = re.compile(
        r'(?:基因(?:检测|测序|分型|突变)|NGS|PD-L1|ALK|EGFR|ROS1|RET|MET|MSI|TMB|免疫组化)'
        r'.{0,800}',
        re.DOTALL
    )
    sections = [m.group(0) for m in gene_section_pat.finditer(text)]
    search_text = '\n'.join(sections) if sections else text

    for std_name, aliases in GENE_DICT.items():
        sorted_aliases = sorted(aliases, key=len, reverse=True)
        for alias in sorted_aliases:
            pat = re.compile(
                re.escape(alias) + r'[\s：:(（【\[]?(.{0,80}?)(?=[,，。；\n]|$)',
                re.IGNORECASE
            )
            m = pat.search(search_text)
            if m:
                context = m.group(0)
                result_str = _extract_gene_result(context)
                if result_str:
                    gene_res[std_name] = result_str
                break

    for label, col in [('TPS', 'PD-L1_TPS'), ('CPS', 'PD-L1_CPS')]:
        m = re.search(
            label + r'\s*[：:=]?\s*(\d+(?:\.\d+)?)\s*%?',
            search_text, re.IGNORECASE
        )
        if m:
            gene_res[col] = m.group(1) + '%'

    m = re.search(
        r'TMB\s*[：:=]?\s*(\d+(?:\.\d+)?)\s*(?:mut/Mb|个/Mb)?',
        search_text, re.IGNORECASE
    )
    if m:
        gene_res['TMB'] = m.group(1) + ' mut/Mb'

    for msi_val in ['MSI-H', 'MSI-L', 'MSS', 'dMMR', 'pMMR']:
        if msi_val in search_text:
            gene_res['MSI状态'] = msi_val
            break

    methods = []
    for kw in ['NGS', '二代测序', 'WGS', 'WES', 'FISH', 'ARMS-PCR', 'qPCR', '液态活检', 'ctDNA']:
        if kw in search_text:
            methods.append(kw)
    if methods:
        gene_res['基因检测方法'] = ','.join(methods)

    return gene_res


# ──────────────────────────────────────────────
# 影像/病理报告提取
# ──────────────────────────────────────────────
def parse_reports(process_text: str) -> dict:
    """从住院经过文本中提取影像/病理报告"""
    reports = {}
    pat = re.compile(
        r'\[(\d{4}-\d{2}-\d{2})\]-([^\[]+?)(?=\[\d{4}-\d{2}-\d{2}\]|$)',
        re.DOTALL
    )
    for m in pat.finditer(process_text):
        date = m.group(1)
        content = m.group(2).strip().replace('\n', ' ')
        title_m = re.match(r'^([^：:。，\n]{2,40})', content)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        body = content[len(title):].lstrip('：: ').strip()
        if not body:
            body = content
        col = _normalize_exam_title(title)
        entry = f"{date}:{body[:200]}"
        if col not in reports:
            reports[col] = []
        reports[col].append(entry)
    return {k: ';'.join(v) for k, v in reports.items()}


def _normalize_exam_title(title: str) -> str:
    """将检查名称标准化为宽列名"""
    t = title
    if 'ECT' in t or '骨显像' in t:
        return 'ECT全身骨显像'
    if '免疫组织化学' in t or '免疫组化' in t:
        return '免疫组化诊断'
    if '石蜡切片' in t or '快速石蜡' in t or '穿刺' in t:
        return '病理活检'
    if '基因' in t or 'PD-L1' in t or 'MSI' in t or 'NGS' in t:
        return '基因/PD-L1检测'
    if ('磁共振' in t or 'MRI' in t) and ('颅' in t or '脑' in t or '头' in t):
        return '头颅MRI'
    if '磁共振' in t or 'MRI' in t:
        return '磁共振检查'
    if '超声' in t and ('甲状腺' in t or '颈部' in t):
        return '甲状腺颈部超声'
    if '超声' in t and ('肝' in t or '胆' in t or '腹' in t):
        return '腹部超声'
    if ('彩超' in t and '心' in t) or '心功能' in t or '心脏' in t:
        return '心脏彩超'
    if '超声' in t or '彩超' in t:
        return '超声检查'
    if 'CT' in t or '薄层' in t:
        if '胸部' in t or '肺' in t:
            return '胸部CT'
        if '腹部' in t or '腹' in t:
            return '腹部CT'
        if '颈部' in t:
            return '颈部CT'
        if 'HRCT' in t:
            return '胸部HRCT'
        return 'CT检查'
    if '心电图' in t:
        return '心电图'
    if '支气管镜' in t or '气管镜' in t:
        return '支气管镜检查'
    if '骨' in t:
        return 'ECT全身骨显像'
    if '肺功能' in t:
        return '肺功能检查'
    return title[:30]


# ──────────────────────────────────────────────
# 核心提取引擎
# ──────────────────────────────────────────────
def _locate_core_headers(t: str) -> list:
    """
    在预处理后的平面文本 t 中，定位所有核心关键字的位置。
    优先使用带 ** 的加粗格式，若找不到则回退到纯文本首现位置。
    返回 [(key_name, key_end_pos), ...] 按 key_end_pos 排序。
    """
    found = {}  # key_name -> (start, end)

    # 第一遍：尝试加粗语法匹配
    for m in re.finditer(r'\*\*([^*]{1,20}?)[:：]?\*\*', t):
        candidate = m.group(1).strip()
        if candidate in CORE_HEADERS and candidate not in found:
            found[candidate] = (m.start(), m.end())

    # 第二遍：对仍未找到的核心关键字，尝试纯文本首现匹配
    for header in CORE_HEADERS:
        if header in found:
            continue
        # 在文本中寻找 "关键字" 后紧跟 冒号/空格/非文字 的位置
        pat = re.compile(re.escape(header) + r'(?:\s*[:：])?')
        m = pat.search(t)
        if m:
            found[header] = (m.start(), m.end())

    # 按位置排序，过滤掉位置相同/重叠的（以先找到的为准）
    sorted_items = sorted(found.items(), key=lambda x: x[1][0])

    # 去除位置可能的重叠（简单保留先出现的）
    result = []
    last_end = -1
    for key_name, (start, end) in sorted_items:
        if start >= last_end:
            result.append((key_name, end))
            last_end = end

    return result


def extract_record_smart(hospital_name: str, rt: str) -> dict:
    """
    基于核心关键字的稳健提取，兼容 pandoc 生成的 Markdown 表格格式。
    同时兼容字段标题为加粗格式（**姓名**）和非加粗格式（姓名）两种情况。
    hospital_name: 从正则切分中捕获的医院名称
    rt: 本病例对应的文本内容
    """
    # 1. 预处理文本：彻底移除表格框线并合并空白
    t = re.sub(r'[\+\-]{5,}', ' ', rt)
    t = t.replace('|', ' ')
    t = t.replace('\xa0', ' ')
    # 清理 pandoc 转义字符
    t = t.replace(r'\^', '^')
    t = re.sub(r'\s+', ' ', t).strip()

    # 2. 多级首现定位：优先加粗格式，降级到纯文本首现
    header_positions = _locate_core_headers(t)

    res = {}

    # 3. 提取有效标题之间的一切内容
    for i, (key_name, key_end_pos) in enumerate(header_positions):
        # 计算本字段内容的结束位置（下一个字段的开始位置）
        if i + 1 < len(header_positions):
            next_start = header_positions[i + 1][1]
            # 找到下一个字段标题的起始位置（往前回退到 ** 或者纯关键字开始）
            next_key = header_positions[i + 1][0]
            # 精确找到下一个标题的 start，使用_locate_core_headers内搜索
            pat_bold = re.compile(r'\*\*' + re.escape(next_key) + r'[:：]?\*\*')
            pat_plain = re.compile(re.escape(next_key) + r'(?:\s*[:：])?')
            mb = pat_bold.search(t, key_end_pos)
            mp = pat_plain.search(t, key_end_pos)
            # 选取最先出现的那个
            candidates = [(m.start(), m) for m in [mb, mp] if m is not None]
            if candidates:
                end_pos = min(candidates, key=lambda x: x[0])[0]
            else:
                end_pos = len(t)
        else:
            end_pos = len(t)

        content = t[key_end_pos:end_pos].strip()
        content = re.sub(r'^[：:\s]+', '', content).strip()

        std_key = HEADER_MAPPING.get(key_name)
        if std_key:
            if std_key in res:
                res[std_key] = res[std_key] + ' ' + content
            else:
                res[std_key] = content

    # 4. 体格检查：身高/体重/BMI
    proc_text = res.get('住院经过(全文)', '')
    adm_text = res.get('入院时情况(全文)', '')
    combined = (adm_text or '') + ' ' + (proc_text or '')
    res.update(parse_anthropometry(combined))

    # 5. 实验室指标（从住院经过）
    if proc_text:
        labs = parse_labs(proc_text)
        res.update(labs)

    # 6. 影像/病理报告（从住院经过）
    if proc_text:
        reports = parse_reports(proc_text)
        res.update(reports)

    # 7. 治疗经过结构化提取（从住院经过）
    if proc_text:
        treatment = parse_treatment(proc_text)
        res.update(treatment)

    # 8. 基因检测指标（从住院经过 + 入院时情况）
    if combined.strip():
        gene_info = parse_gene_report(combined)
        res.update(gene_info)

    # 9. 年龄清洗（只保留数字）
    if '年龄(岁)' in res:
        res['年龄(岁)'] = ''.join(re.findall(r'\d+', str(res['年龄(岁)'])))

    # 10. ID 与元数据
    out_date = str(res.get('出院时间', '')).split(' ')[0].replace('-', '').replace('/', '')[:8]
    res['record_id'] = f"{res.get('住院号', 'UNK')}_{out_date}"
    res['医院'] = hospital_name.strip()
    res['记录类型'] = '出院记录'
    res['记录日期'] = str(res.get('出院时间', '')).split(' ')[0]
    res['路径'] = os.path.abspath(INPUT_DOCX)
    res['文件名'] = os.path.splitext(os.path.basename(INPUT_DOCX))[0]
    res['分组'] = 'linai'
    res['患者文件夹'] = 'linai'

    return res


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def main():
    if not os.path.exists(INPUT_DOCX):
        print(f"[ERROR] 输入文件不存在: {INPUT_DOCX}")
        return

    # Step 1: 将 doc/docx 转为 Markdown
    if not os.path.exists(TEMP_MD):
        print(f"转换 {os.path.basename(INPUT_DOCX)} → Markdown ...")
        try:
            md = MarkItDown()
            result = md.convert(INPUT_DOCX)
            with open(TEMP_MD, 'w', encoding='utf-8') as f:
                f.write(result.text_content)
            print("转换完成。")
        except Exception as e:
            print(f"[ERROR] 转换失败: {e}")
            return

    else:
        print(f"复用已有 Markdown: {TEMP_MD}")

    with open(TEMP_MD, 'r', encoding='utf-8') as f:
        content = f.read()

    # Step 2: 使用捕获组切分，获取每条病例的医院名称 + 对应文本
    HOSPITAL_PAT = r'(蚌\s*埠\s*医\s*(?:学\s*院|科\s*大\s*学)\s*第\s*一\s*附\s*属\s*医\s*院)'
    parts = re.split(HOSPITAL_PAT, content)
    # parts[0] = 文档开头部分（无关内容）
    # parts[1], parts[2] = 第1条医院名称, 第1条正文
    # parts[3], parts[4] = 第2条医院名称, 第2条正文 ...

    print(f"切分后得到 {(len(parts) - 1) // 2} 个病例段落（含无效）")

    # Step 3: 逐条提取
    all_data = []
    skipped = 0
    for i in range(1, len(parts), 2):
        hospital_name = parts[i]
        raw_text = parts[i + 1] if i + 1 < len(parts) else ''
        if '姓名' not in raw_text or '住院号' not in raw_text:
            skipped += 1
            continue
        try:
            record = extract_record_smart(hospital_name, raw_text)
            all_data.append(record)
        except Exception as e:
            print(f"  [WARN] 第 {i//2+1} 条提取失败: {e}")
            skipped += 1

    print(f"有效记录: {len(all_data)} 条，跳过: {skipped} 条")

    if not all_data:
        print("未提取到任何数据，请检查文件结构。")
        return

    df = pd.DataFrame(all_data)

    # Step 4: 列排序
    FIXED_COLS = [
        'record_id', '患者姓名', '性别', '年龄(岁)', '住院号',
        '出院科室', '床号', '医院', '入院时间', '出院时间', '住院天数',
        '身高(cm)', '体重(kg)', 'BMI',
        '记录类型', '记录日期', '记录时间', '医师签名',
        '入院诊断', '出院诊断', '治疗结果',
        '治疗类型', '化疗_方案', '化疗_日期', '化疗_药物列表',
        '疗效评估', '不良反应', '支持治疗',
    ]
    lab_cols  = sorted([c for c in df.columns if c in LAB_DICT])
    gene_cols = sorted([c for c in df.columns if c in GENE_DICT])
    _exclude = (
        set(FIXED_COLS) | set(lab_cols) | set(gene_cols)
        | {'入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)',
           '出院医嘱', '路径', '分组', '患者文件夹', '文件名'}
    )
    report_cols = sorted([c for c in df.columns if c not in _exclude])
    meta_cols   = ['分组', '患者文件夹', '文件名', '路径']
    text_cols   = ['入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)', '出院医嘱']

    ordered = (
        [c for c in FIXED_COLS if c in df.columns]
        + [c for c in lab_cols  if c not in FIXED_COLS]
        + [c for c in gene_cols if c not in FIXED_COLS]
        + [c for c in report_cols]
        + [c for c in text_cols if c in df.columns]
        + [c for c in meta_cols if c in df.columns]
    )
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

    gene_in_df = [c for c in GENE_DICT if c in df.columns]
    if gene_in_df:
        g_filled = df[gene_in_df].notna().sum()
        print(f"\n基因检测指标填充情况（非空记录数）：")
        for col in gene_in_df:
            cnt = int(g_filled[col])
            if cnt > 0:
                print(f"   {col}: {cnt}/{len(df)}")


if __name__ == '__main__':
    main()
