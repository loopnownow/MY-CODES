"""
批量从 cln/one 下所有 .docx 文件提取出院记录数据，输出单宽表 CSV。
改进点：
  1. 精确 LAB_DICT 字典匹配常见医学指标（不再用通用正则）
  2. 新增 parse_treatment() 结构化提取化疗方案/疗效/不良反应/支持治疗
  3. 完全自由列输出，移除 SAMPLE_CSV 模板依赖
"""

import os
import re
import glob
import pandas as pd
from docx import Document

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
INPUT_DIR   = r'{lab_root}\_DataBase\LC\lxf_LUNG\clinical_originals\beng1'
OUTPUT_FILE = r'{lab_root}\pre-submit\lxf_LUNG\ref\tmp\results_wide.csv'

# ── 输入模式 ──────────────────────────────────────
# 'docx' : 从 .docx Word 文件提取（原有逻辑）
# 'txt'  : 从 .txt 纯文本文件提取（适配出院小结类文本）
INPUT_MODE = 'docx'

# ──────────────────────────────────────────────
# 实验室指标标准名称字典
# key = 标准列名（CSV 中的列头）
# value = 文本中可能出现的别名列表（按长度降序，优先匹配更长的）
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
for std_name, aliases in LAB_DICT.items():
    for alias in aliases:
        _ALIAS_MAP[alias] = std_name
_SORTED_ALIASES = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────
def clean_text(text):
    return text.strip().replace('\xa0', ' ').replace('\u3000', ' ')


def extract_nested_tables(doc):
    """提取嵌套在主表单元格中的11个核心数据子表"""
    if not doc.tables:
        return None
    main_table = doc.tables[0]
    nested_cell = None
    for row in main_table.rows:
        for cell in row.cells:
            if len(cell.tables) >= 10:
                nested_cell = cell
                break
        if nested_cell:
            break
    return nested_cell.tables if nested_cell else None


# ──────────────────────────────────────────────
# 实验室指标解析
# ──────────────────────────────────────────────
def _find_std_name(candidate: str) -> str | None:
    """在别名表中匹配指标名称，返回标准名或 None"""
    candidate = candidate.strip()
    for alias in _SORTED_ALIASES:
        if alias in candidate or candidate in alias:
            return _ALIAS_MAP[alias]
    return None


def parse_labs(text: str) -> dict:
    """
    从住院经过/入院查体文本中解析实验室指标。
    支持两种常见格式：
      格式A: 2024-04-02,血常规:白细胞计数 10.24 ×10^9/L,中性粒细胞计数 7.20 ×10^9/L。
      格式B: 2025-02-08,血常规:单核细胞计数 1.03 ↑ ×10^9/L,淋巴细胞 16.7 ↓ %;
    返回字典：{标准指标名: "日期:值单位;日期:值单位"}
    """
    labs = {}   # {std_name: [(date, value_str)]}

    # 将文本分割成"日期 + 内容"片段
    # 匹配 YYYY-MM-DD 或 YYYY.MM.DD
    date_pat = re.compile(r'(\d{4}[-./]\d{2}[-./]\d{2})')
    segments = date_pat.split(text)

    current_date = None
    for seg in segments:
        # 判断是否为日期
        m = date_pat.fullmatch(seg.strip())
        if m:
            current_date = seg.strip().replace('.', '-').replace('/', '-')
            continue
        if current_date is None:
            continue
        # 在当前内容中提取指标
        _extract_items_in_segment(seg, current_date, labs)

    # 合并同名指标为分号分隔字符串
    return {k: ';'.join(f"{d}:{v}" for d, v in sorted(pairs)) for k, pairs in labs.items()}


def _extract_items_in_segment(seg: str, date: str, labs: dict):
    """
    从单个日期段内容中提取所有实验室指标值。
    格式：指标名 数值 [↑↓]? 单位
    """
    # 指标项之间用逗号或分号分隔，先按这些分隔符切分
    items = re.split(r'[,，;；]', seg)
    for item in items:
        item = item.strip()
        if not item:
            continue
        # 跳过检查组名行（含冒号但冒号后面紧接多个指标）
        # 先处理"检查组名:指标项"的格式，取冒号后面
        if '：' in item or ':' in item:
            colon_pos = max(item.find('：'), item.find(':'))
            tail = item[colon_pos + 1:].strip()
            if tail:
                item = tail

        # 匹配：指标名 + 空格 + 数值(可含<>=) + 可选箭头 + 单位
        # 单位允许含数字（如 ×10^9/L, μmol/L, ng/ml 等）
        m = re.match(
            r'^(.+?)\s+((?:[<>≥≤=]?\s*)?(?:>|<|≥|≤)?(?:\d+\.?\d*(?:e[+-]?\d+)?))\s*[↑↓]?\s*([^\s,，;；。\n\r↑↓]{0,30})$',
            item.strip()
        )
        if not m:
            continue
        name_candidate, value, unit = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

        std = _find_std_name(name_candidate)
        if std is None:
            continue  # 不在字典中的指标丢弃

        val_str = f"{value}{unit}" if unit else value
        if std not in labs:
            labs[std] = []
        # 去重：同一天同一值不重复添加
        if (date, val_str) not in labs[std]:
            labs[std].append((date, val_str))


# ──────────────────────────────────────────────
# 治疗经过结构化提取
# ──────────────────────────────────────────────

# 常见化疗/免疫/靶向药物关键词（用于识别治疗方案行）
CHEMO_DRUGS = [
    '培美曲塞', '卡铂', '顺铂', '奈达铂', '洛铂', '奥沙利铂',
    '紫杉醇', '多西他赛', '白蛋白紫杉醇', '吉西他滨', '长春瑞滨',
    '依托泊苷', '伊立替康', '替吉奥', '卡培他滨',
    # 免疫检查点抑制剂
    '信迪利单抗', '帕博利珠单抗', '替雷利珠单抗', '阿替利珠单抗',
    '度伐利尤单抗', '卡瑞利珠单抗', '纳武利尤单抗', '舒格利单抗',
    '贝莫苏拜单抗', '派安普利单抗', '恩沃利单抗',
    # 靶向药物
    '厄洛替尼', '吉非替尼', '阿法替尼', '奥希替尼', '达可替尼',
    '克唑替尼', '阿来替尼', '洛拉替尼', '恩沙替尼', '布格替尼',
    '仑伐替尼', '索拉非尼', '贝伐珠单抗',
    '安罗替尼', '阿帕替尼',
    # 骨改良药物
    '唑来膦酸', '地舒单抗', '伊班膦酸',
]

# 疗效评估关键词
EFFICACY_PATTERNS = {
    'CR': [r'完全缓解', r'\bCR\b'],
    'PR': [r'部分缓解', r'缩小', r'\bPR\b'],
    'SD': [r'疾病稳定', r'稳定（小）', r'大致相仿', r'\bSD\b'],
    'PD': [r'疾病进展', r'进展', r'\bPD\b'],
}

# 不良反应关键词
ADR_KEYWORDS = [
    '骨髓抑制', '粒细胞减少', '白细胞减少', '血小板减少', '贫血',
    '恶心', '呕吐', '腹泻', '腹痛', '胃肠道反应',
    '发热', '乏力', '皮疹', '口腔黏膜炎', '神经毒性',
    '肝功能损害', '肾功能损害', '心脏毒性',
]

# 支持治疗关键词
SUPPORT_KEYWORDS = [
    '止吐', '护胃', '抑酸', '补液', '升白针', '重组人粒细胞',
    '唑来膦酸', '地舒单抗', '镇痛', '止痛', '抗感染',
    '雾化排痰', '化痰', '保肝', '降糖', '纠酸',
]


def parse_treatment(process_text: str) -> dict:
    """
    从住院经过文本中结构化提取治疗信息，返回字典。
    提取内容：
      - 化疗_方案: 完整方案描述（药物+剂量）
      - 化疗_日期: 开始化疗日期
      - 化疗_药物列表: 识别到的药物名称（逗号分隔）
      - 疗效评估: CR/PR/SD/PD
      - 不良反应: 出现的不良反应关键词
      - 支持治疗: 支持治疗药物/措施关键词
      - 治疗类型: 化疗/免疫治疗/靶向治疗/手术/放疗
    """
    result = {}
    text = process_text

    # 1. 提取治疗方案（化疗日期 + 方案描述）
    # 模式：于YYYY-MM-DD/于YYYY.MM.DD + 方案描述（到句号或行尾）
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
        chemo_date = m.group(1).replace('.', '-').replace('/', '-')
        scheme_text = m.group(2).strip()
        result['化疗_日期'] = chemo_date
        result['化疗_方案'] = scheme_text

    # 2. 在全文中扫描药物名称
    found_drugs = []
    for drug in CHEMO_DRUGS:
        if drug in text:
            # 尝试提取剂量
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

    # 3. 判断治疗类型
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
    # 先在"疗效"/"评价"/"评估"相关句子中查找
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
# 基因相关检测指标配置与提取
# ──────────────────────────────────────────────

# 基因检测指标字典
# key = 标准列名，value = 文本中可能出现的别名/关键词
GENE_DICT = {
    # EGFR 突变
    'EGFR突变状态':        ['EGFR基因', 'EGFR突变', 'EGFR'],
    'EGFR_18外显子':       ['18号外显子', 'exon18', 'Exon 18', '第18外显子'],
    'EGFR_19外显子缺失':   ['19号外显子缺失', '19del', 'exon19缺失', 'Exon 19 del', '第19外显子缺失'],
    'EGFR_20外显子插入':   ['20号外显子插入', 'exon20ins', 'Exon 20 ins', '20外显子插入'],
    'EGFR_21外显子L858R':  ['L858R', '21号外显子', 'exon21', 'Exon 21'],
    'EGFR_T790M':          ['T790M'],
    'EGFR_C797S':          ['C797S'],

    # ALK 融合/重排
    'ALK融合状态':         ['ALK融合', 'ALK重排', 'ALK基因', 'ALK-EML4', 'EML4-ALK', 'ALK阳性', 'ALK阴性', 'ALK'],

    # ROS1 融合
    'ROS1融合状态':        ['ROS1融合', 'ROS1重排', 'ROS1基因', 'ROS1'],

    # RET 融合
    'RET融合状态':         ['RET融合', 'RET重排', 'RET基因', 'RET'],

    # MET 扩增/跳跃
    'MET状态':             ['MET扩增', 'MET跳跃突变', 'METex14', 'MET14', 'MET基因', 'MET'],

    # KRAS 突变
    'KRAS突变状态':        ['KRAS突变', 'KRAS基因', 'KRAS G12C', 'KRAS'],

    # BRAF 突变
    'BRAF突变状态':        ['BRAF V600E', 'BRAF突变', 'BRAF基因', 'BRAF'],

    # HER2 (ERBB2)
    'HER2状态':            ['HER2扩增', 'HER2突变', 'ERBB2扩增', 'ERBB2突变', 'HER2', 'ERBB2'],

    # NTRK 融合
    'NTRK融合状态':        ['NTRK融合', 'NTRK1融合', 'NTRK2融合', 'NTRK3融合', 'NTRK'],

    # FGFR 变异
    'FGFR状态':            ['FGFR1扩增', 'FGFR2扩增', 'FGFR3融合', 'FGFR突变', 'FGFR'],

    # PIK3CA 突变
    'PIK3CA突变状态':      ['PIK3CA突变', 'PIK3CA基因', 'PIK3CA'],

    # TP53 突变
    'TP53突变状态':        ['TP53突变', 'TP53基因', 'TP53'],

    # STK11 突变
    'STK11突变状态':       ['STK11突变', 'STK11基因', 'STK11', 'LKB1'],

    # KEAP1 突变
    'KEAP1突变状态':       ['KEAP1突变', 'KEAP1基因', 'KEAP1'],

    # PD-L1 表达
    'PD-L1_TPS':           ['TPS', 'PD-L1 TPS', 'PD-L1表达（TPS）'],
    'PD-L1_CPS':           ['CPS', 'PD-L1 CPS', 'PD-L1表达（CPS）'],
    'PD-L1表达状态':       ['PD-L1表达', 'PD-L1阳性', 'PD-L1阴性', 'PD-L1（22C3）',
                            'PD-L1（SP142）', 'PD-L1（28-8）', 'PD-L1'],

    # MSI 微卫星不稳定
    'MSI状态':             ['MSI-H', 'MSI-L', 'MSS', '微卫星不稳定', '微卫星稳定',
                            'dMMR', 'pMMR', 'MMR缺失', 'MSI'],

    # TMB 肿瘤突变负荷
    'TMB':                 ['TMB-H', 'TMB-L', '肿瘤突变负荷', 'TMB'],

    # BRCA1/2
    'BRCA1状态':           ['BRCA1突变', 'BRCA1基因', 'BRCA1'],
    'BRCA2状态':           ['BRCA2突变', 'BRCA2基因', 'BRCA2'],

    # 基因检测方法
    '基因检测方法':        ['NGS', '二代测序', '全基因组测序', 'WGS', '全外显子组测序', 'WES',
                            '液态活检', '血液ctDNA', '荧光原位杂交', 'FISH',
                            '免疫组织化学法', 'ARMS-PCR', 'qPCR'],

    # 基因检测样本类型
    '基因检测样本':        ['血液样本', '组织样本', '痰液', 'ctDNA', '血浆', '石蜡切片'],
}

# 结果关键词：用于判断基因检测结果是阳性、阴性还是具体值
_POSITIVE_WORDS = ['阳性', '突变', '融合', '扩增', '重排', '插入', '缺失', 'positive', 'mut', 'del', 'ins', 'H']
_NEGATIVE_WORDS = ['阴性', '未检出', '野生型', '未突变', '阴', 'negative', 'WT', 'wild']


def _extract_gene_result(context: str) -> str:
    """
    从检测结果上下文中提取简洁的结果描述。
    优先提取数值（如TPS 60%、TMB 10 mut/Mb），其次返回阳性/阴性/野生型。
    """
    # 提取百分比数值（PD-L1 TPS/CPS 常见）
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', context)
    if m:
        return m.group(0)
    # 提取 TMB 数值
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:mut/Mb|个/Mb|mutations/Mb)', context, re.I)
    if m:
        return m.group(0)
    # 正负判断
    for w in _POSITIVE_WORDS:
        if w in context:
            # 尝试带上突变具体描述
            mut_m = re.search(r'([A-Z]\d+[A-Z]|exon\s*\d+|第\d+外显子|[a-z]{1,10}\d+)', context, re.I)
            suffix = f'({mut_m.group(0)})' if mut_m else ''
            return f'阳性{suffix}'
    for w in _NEGATIVE_WORDS:
        if w in context:
            return '阴性/野生型'
    # 截取前40字符作为原始描述
    return context.strip()[:40]


def parse_gene_report(text: str) -> dict:
    """
    从住院经过/病理报告文本中结构化提取基因检测相关指标。

    支持格式：
      (a) 标准化报告行：  EGFR基因：19外显子缺失突变（阳性）
      (b) NGS报告行：     检测到 EGFR L858R 突变，ALK 阴性，PD-L1 TPS 60%
      (c) 免疫组化行：    PD-L1(22C3) TPS 80% 阳性
      (d) 自由文本：      基因检测示EGFR 19del突变，ALK融合阴性，MSI-H

    返回字典：{标准指标名: 结果描述字符串}
    """
    gene_res = {}

    # 1. 定位基因检测相关段落（提升精度，减少误匹配）
    gene_section_pat = re.compile(
        r'(?:基因(?:检测|测序|分型|突变)|NGS|PD-L1|ALK|EGFR|ROS1|RET|MET|MSI|TMB|免疫组化)'
        r'.{0,800}',
        re.DOTALL
    )
    sections = [m.group(0) for m in gene_section_pat.finditer(text)]
    search_text = '\n'.join(sections) if sections else text

    # 2. 逐指标在文本中搜索
    for std_name, aliases in GENE_DICT.items():
        # 按别名长度降序优先匹配较长的
        sorted_aliases = sorted(aliases, key=len, reverse=True)
        for alias in sorted_aliases:
            # 在别名后抓取上下文（≤80字符）
            pat = re.compile(
                re.escape(alias) + r'[\s：:(（【\[]?(.{0,80}?)(?=[,，。；\n]|$)',
                re.IGNORECASE
            )
            m = pat.search(search_text)
            if m:
                context = m.group(0)  # 包含别名本身
                result_str = _extract_gene_result(context)
                if result_str:
                    gene_res[std_name] = result_str
                break  # 找到后不再尝试其他别名

    # 3. 专项：PD-L1 TPS/CPS 数值（优先级高，覆盖上面的通用结果）
    for label, col in [('TPS', 'PD-L1_TPS'), ('CPS', 'PD-L1_CPS')]:
        m = re.search(
            label + r'\s*[：:=]?\s*(\d+(?:\.\d+)?)\s*%?',
            search_text, re.IGNORECASE
        )
        if m:
            gene_res[col] = m.group(1) + '%'

    # 4. 专项：TMB 数值
    m = re.search(
        r'TMB\s*[：:=]?\s*(\d+(?:\.\d+)?)\s*(?:mut/Mb|个/Mb)?',
        search_text, re.IGNORECASE
    )
    if m:
        gene_res['TMB'] = m.group(1) + ' mut/Mb'

    # 5. MSI 状态精确匹配
    for msi_val in ['MSI-H', 'MSI-L', 'MSS', 'dMMR', 'pMMR']:
        if msi_val in search_text:
            gene_res['MSI状态'] = msi_val
            break

    # 6. 基因检测方法识别（取所有匹配）
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
    """
    从住院经过文本中提取影像/病理报告。
    格式：[YYYY-MM-DD]-检查项目名称 报告内容
    返回字典：{检查项目: "日期:报告内容"}
    """
    reports = {}
    # 匹配 [日期]-检查名称 内容块
    pat = re.compile(
        r'\[(\d{4}-\d{2}-\d{2})\]-([^\[]+?)(?=\[\d{4}-\d{2}-\d{2}\]|$)',
        re.DOTALL
    )
    for m in pat.finditer(process_text):
        date = m.group(1)
        content = m.group(2).strip().replace('\n', ' ')
        # 提取检查名称（取内容第一行的首个句子/短语）
        title_m = re.match(r'^([^：:。，\n]{2,40})', content)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        # 报告正文（冒号/分号后的内容，或整段）
        body = content[len(title):].lstrip('：: ').strip()
        if not body:
            body = content

        # 标准化检查项目名称到宽列
        col = _normalize_exam_title(title)
        entry = f"{date}:{body[:200]}"  # 截断过长内容
        if col not in reports:
            reports[col] = []
        reports[col].append(entry)

    return {k: ';'.join(v) for k, v in reports.items()}


def _normalize_exam_title(title: str) -> str:
    """将检查名称标准化为宽列名"""
    t = title
    # ECT骨显像
    if 'ECT' in t or '骨显像' in t:
        return 'ECT全身骨显像'
    # 病理/免疫组化
    if '免疫组织化学' in t or '免疫组化' in t:
        return '免疫组化诊断'
    if '石蜡切片' in t or '快速石蜡' in t or '穿刺' in t:
        return '病理活检'
    # 基因/PD-L1
    if '基因' in t or 'PD-L1' in t or 'MSI' in t or 'NGS' in t:
        return '基因/PD-L1检测'
    # 头颅MRI/磁共振
    if ('磁共振' in t or 'MRI' in t) and ('颅' in t or '脑' in t or '头' in t):
        return '头颅MRI'
    if '磁共振' in t or 'MRI' in t:
        return '磁共振检查'
    # 超声
    if '超声' in t and ('甲状腺' in t or '颈部' in t):
        return '甲状腺颈部超声'
    if '超声' in t and ('肝' in t or '胆' in t or '腹' in t):
        return '腹部超声'
    if '彩超' in t and '心' in t or '心功能' in t or '心脏' in t:
        return '心脏彩超'
    if '超声' in t or '彩超' in t:
        return '超声检查'
    # CT
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
    # 心电图
    if '心电图' in t:
        return '心电图'
    # 支气管镜
    if '支气管镜' in t or '气管镜' in t:
        return '支气管镜检查'
    # 骨扫描/骨显像
    if '骨' in t:
        return 'ECT全身骨显像'
    # 肺功能
    if '肺功能' in t:
        return '肺功能检查'
    # 默认用原标题
    return title[:30]


# ──────────────────────────────────────────────
# 通用：从文本中提取门诊号 / 身高 / 体重
# ──────────────────────────────────────────────
def _extract_anthropometry(text: str) -> dict:
    """从任意文本中提取门诊号、身高(cm)、体重(kg)"""
    res = {}
    m = re.search(r'门诊号[：:]\s*(\S+)', text)
    if m:
        res['门诊号'] = m.group(1).strip()
    m = re.search(r'身高[：:]?\s*(\d+(?:\.\d+)?)\s*cm', text, re.I)
    if m:
        res['身高(cm)'] = m.group(1)
    m = re.search(r'体重[：:]?\s*(\d+(?:\.\d+)?)\s*kg', text, re.I)
    if m:
        res['体重(kg)'] = m.group(1)
    h, w = res.get('身高(cm)', ''), res.get('体重(kg)', '')
    if h and w:
        try:
            res['BMI'] = round(float(w) / ((float(h) / 100) ** 2), 1)
        except ValueError:
            pass
    return res


# ──────────────────────────────────────────────
# 单文件处理（docx 模式）
# ──────────────────────────────────────────────
def process_file(filepath: str) -> dict | None:
    try:
        doc = Document(filepath)
        nt = extract_nested_tables(doc)
        if not nt:
            return None

        res = {}

        # 路径信息
        res['路径'] = os.path.abspath(filepath)
        parts = os.path.abspath(filepath).split(os.sep)
        # 路径结构：.../cln/one/分组文件夹/患者文件夹/X.docx
        res['分组'] = parts[-3] if len(parts) >= 3 else ''
        res['患者文件夹'] = parts[-2] if len(parts) >= 2 else ''
        res['文件名'] = os.path.splitext(parts[-1])[0]

        # Table 0: 基本信息（姓名/性别/年龄/科室/床号/住院号/门诊号）
        t0 = nt[0].rows[0]
        mapping_t0 = {
            '姓名': '患者姓名', '性别': '性别', '年龄': '年龄(岁)',
            '出院科别': '出院科室', '床号': '床号', '住院号': '住院号',
            '门诊号': '门诊号',
        }
        cells_t0 = t0.cells
        for i in range(0, len(cells_t0) - 1, 2):
            key = clean_text(cells_t0[i].text)
            if key in mapping_t0:
                val = clean_text(cells_t0[i + 1].text)
                if key == '年龄':
                    val = ''.join(re.findall(r'\d+', val))
                res[mapping_t0[key]] = val

        # Table 1: 入院/出院时间/住院天数
        t1 = nt[1].rows[0]
        res['入院时间'] = clean_text(t1.cells[1].text)
        res['出院时间'] = clean_text(t1.cells[3].text)
        res['住院天数'] = ''.join(re.findall(r'\d+', t1.cells[5].text))

        # record_id：住院号_出院日期
        out_date = res.get('出院时间', '').split(' ')[0].replace('-', '')
        res['record_id'] = f"{res.get('住院号', '')}_{out_date}"

        # 医院/科室（主表第一行）
        res['医院'] = clean_text(doc.tables[0].rows[0].cells[0].text)
        res['记录类型'] = '出院记录'
        res['记录日期'] = res.get('出院时间', '').split(' ')[0]

        # Table 2-4: 诊断和治疗结果
        res['入院诊断'] = clean_text(nt[2].rows[0].cells[1].text)
        res['出院诊断'] = clean_text(nt[3].rows[0].cells[1].text)
        res['治疗结果'] = clean_text(nt[4].rows[0].cells[1].text)

        # Table 5-8: 大文本块
        admission_text  = clean_text(nt[5].rows[0].cells[1].text)
        process_text    = clean_text(nt[6].rows[0].cells[1].text)
        discharge_text  = clean_text(nt[7].rows[0].cells[1].text)
        advice_text     = clean_text(nt[8].rows[0].cells[1].text)

        res['入院时情况(全文)'] = admission_text
        res['住院经过(全文)']   = process_text
        res['出院时情况(全文)'] = discharge_text
        res['出院医嘱']         = advice_text

        # 身高/体重/BMI：从入院时情况和住院经过中补充提取
        anthro = _extract_anthropometry(admission_text + '\n' + process_text)
        for k in ('门诊号', '身高(cm)', '体重(kg)', 'BMI'):
            if k not in res and k in anthro:
                res[k] = anthro[k]

        # 提取实验室指标（从住院经过）
        labs = parse_labs(process_text)
        res.update(labs)

        # 提取影像报告（从住院经过）
        reports = parse_reports(process_text)
        res.update(reports)

        # 结构化提取治疗经过
        treatment = parse_treatment(process_text)
        res.update(treatment)

        # 提取基因相关检测指标（从住院经过 + 入院时情况）
        gene_info = parse_gene_report(admission_text + '\n' + process_text)
        res.update(gene_info)

        # Table 9: 医师签名
        if len(nt) > 9:
            t9 = nt[9].rows[0]
            for j, cell in enumerate(t9.cells):
                if '医师签名' in clean_text(cell.text) and j + 1 < len(t9.cells):
                    res['医师签名'] = clean_text(t9.cells[j + 1].text)
                    break

        # Table 10: 记录时间
        if len(nt) > 10:
            t10 = nt[10].rows[0]
            for j, cell in enumerate(t10.cells):
                if '记录时间' in clean_text(cell.text) and j + 1 < len(t10.cells):
                    res['记录时间'] = clean_text(t10.cells[j + 1].text)
                    break

        return res

    except Exception as e:
        print(f"  [ERROR] {filepath}: {e}")
        import traceback
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────
# 单文件处理（txt 模式）
# ──────────────────────────────────────────────
def _read_txt(filepath: str) -> str:
    """多编码尝试读取 txt 文件"""
    for enc in ('gbk', 'gb2312', 'gb18030', 'utf-8'):
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


def _extract_txt_basic(text: str) -> dict:
    """从 txt 出院小结中提取姓名、性别、年龄、住院号、入院/出院诊断等基础字段"""
    res = {}

    # 姓名（定位出院小结段）
    name = ''
    for pos in [m.start() for m in re.finditer(r'出院小结', text)]:
        seg = text[pos:pos + 800]
        m = re.search(r'姓\s*名\s*[：:]\s*([\u4e00-\u9fff]{2,4})\s*(?:科室|性别|病区|床位)', seg)
        if m and '*' not in m.group(1):
            name = m.group(1).strip()
            break
    if not name:
        m = re.search(r'姓\s*名\s*[：:]\s*([\u4e00-\u9fff]{2,4})', text[:5000])
        if m and '*' not in m.group(1):
            name = m.group(1).strip()
    res['患者姓名'] = name

    # 性别
    m = re.search(r'性别[：:]\s*(\S)', text)
    if m:
        v = m.group(1)
        res['性别'] = '女性' if v in ('2', '女') else ('男性' if v in ('1', '男') else v)

    # 年龄
    ages = re.findall(r'当前\s*(\d+)\s*岁', text)
    if ages:
        res['年龄(岁)'] = ages[0]
    else:
        m = re.search(r'年龄[：:]\s*(\d+)', text)
        if m:
            res['年龄(岁)'] = m.group(1)

    # 住院号 / 门诊号
    for label, key in [('住院号', '住院号'), ('病历号', '住院号'), ('门诊号', '门诊号')]:
        m = re.search(label + r'[：:]\s*(\S+)', text)
        if m:
            res[key] = m.group(1).strip()

    # 入院/出院诊断（简单提取最近的一段）
    m = re.search(r'入院诊断[：:]\s*(.+?)(?:\n|出院)', text, re.S)
    if m:
        res['入院诊断'] = m.group(1).strip()[:200]
    m = re.search(r'出院诊断[：:]\s*(.+?)(?:\n\n|\Z)', text, re.S)
    if m:
        res['出院诊断'] = m.group(1).strip()[:200]

    # 入院/出院日期
    m = re.search(r'入院(?:日期|时间)[：:]\s*(\d{4}[-./]\d{2}[-./]\d{2})', text)
    if m:
        res['入院时间'] = m.group(1).replace('.', '-').replace('/', '-')
    m = re.search(r'出院(?:日期|时间)[：:]\s*(\d{4}[-./]\d{2}[-./]\d{2})', text)
    if m:
        res['出院时间'] = m.group(1).replace('.', '-').replace('/', '-')

    return res


def _extract_txt_lab_section(text: str) -> str:
    """从 txt 中截取化验结果段落供 parse_labs() 使用"""
    for kw in ['主要化验结果', '主要化验', '化验结果', '检验结果', '住院经过']:
        idx = text.find(kw)
        if idx >= 0:
            start = idx + len(kw)
            end = len(text)
            for em in ['特殊检查及重要会诊', '特殊检查', '诊疗经过', '出院后用药', '出院医嘱']:
                ei = text.find(em, start)
                if ei > 0:
                    end = min(end, ei)
            return text[start:end]
    return text   # 兜底：全文


def process_txt_file(filepath: str) -> dict | None:
    """从 .txt 出院小结中提取数据（与 process_file 输出格式兼容）"""
    try:
        text = _read_txt(filepath)
        res = {}

        # 路径元信息
        res['路径'] = os.path.abspath(filepath)
        parts = os.path.abspath(filepath).split(os.sep)
        res['分组'] = parts[-3] if len(parts) >= 3 else ''
        res['患者文件夹'] = parts[-2] if len(parts) >= 2 else ''
        res['文件名'] = os.path.splitext(parts[-1])[0]
        res['记录类型'] = '出院记录(txt)'

        # 基础字段
        res.update(_extract_txt_basic(text))

        # 门诊号 / 身高 / 体重 / BMI
        res.update(_extract_anthropometry(text))

        # record_id
        out_date = res.get('出院时间', '').replace('-', '')
        res['record_id'] = f"{res.get('住院号', res.get('文件名', ''))}_{out_date}"

        # 血压
        for pat in [r'BP\s*[:：]?\s*(\d+)\s*/\s*(\d+)\s*mmHg',
                    r'血压[：:]?\s*(\d+)\s*/\s*(\d+)\s*mmHg']:
            m = re.search(pat, text, re.I)
            if m:
                res['收缩压(mmHg)'] = m.group(1)
                res['舒张压(mmHg)'] = m.group(2)
                break

        # 实验室指标
        lab_text = _extract_txt_lab_section(text)
        labs = parse_labs(lab_text)
        res.update(labs)

        # 影像报告
        reports = parse_reports(text)
        res.update(reports)

        # 治疗经过
        treatment = parse_treatment(text)
        res.update(treatment)

        # 基因相关检测指标（从全文提取）
        gene_info = parse_gene_report(text)
        res.update(gene_info)

        # 全文保留
        res['全文'] = text[:5000]

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
    if INPUT_MODE == 'txt':
        files = glob.glob(os.path.join(INPUT_DIR, '**', '*.txt'), recursive=True)
        processor = process_txt_file
        print(f"[txt 模式] 发现 {len(files)} 个 .txt 文件。")
    else:
        files = glob.glob(os.path.join(INPUT_DIR, '**', '*.docx'), recursive=True)
        processor = process_file
        print(f"[docx 模式] 发现 {len(files)} 个 .docx 文件。")

    all_data = []
    for f in sorted(files):
        print(f"  处理: {f}")
        data = processor(f)
        if data:
            all_data.append(data)
        else:
            print(f"  [SKIP] 无法提取: {f}")

    if not all_data:
        print("未提取到任何数据，请检查文件结构。")
        return

    df = pd.DataFrame(all_data)

    # 列排序：固定列在前，实验室指标居中，其余列在后
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
        '出院医嘱', '路径', '分组', '患者文件夹', '文件名', '全文',
    }
    report_cols = sorted([c for c in df.columns if c not in _exclude])
    meta_cols   = ['分组', '患者文件夹', '文件名', '路径']
    text_cols   = ['入院时情况(全文)', '住院经过(全文)', '出院时情况(全文)', '出院医嘱', '全文']

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

    # 统计基因指标填充率
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
