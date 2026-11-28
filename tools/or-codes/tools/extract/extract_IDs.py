"""
批量从 L:\\HE_nii_ly\\IDs 下所有 .txt 文件提取数据，输出宽表 CSV。

文件结构：
  === 出院小结 === 段（可多条）：基本信息、诊断、化验文本、影像报告、出院带药等
  === 检验报告 === 段（可大量）：竖排结构化报告单（含日期、项目名、结果值、单位）

提取内容：
  1. 患者基本信息（姓名、性别、年龄、科室、住院号、入院/出院日期等）
  2. 诊断信息（门诊/入院/出院诊断、OCSP/TOAST 分型）
  3. 脑卒中专项指标（NIHSS、mRS、溶栓、抗栓等）
  4. 实验室化验（双来源：出院小结文本 + 结构化检验报告单）
  5. 血压（入院/出院）
  6. 影像/超声/病理检查报告
  7. 出院带药列表
  8. 治疗结果
  9. 临床诊断从诊断/病史文本中提取
"""

import os

import re
import glob
import sys
import pandas as pd

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────
# 使用相对路径以适应当前工作环境
INPUT_DIR   = os.environ.get("INPUT_DIR", "")
OUTPUT_FILE = r'{lab_root}\_DataBase\MHE_PA\_share\HE_nii_ly\extracted_IDs.csv'   # 表不进 L:

# 设为 None 则提取全部化验指标；设为列表则只提取指定英文列名
# 示例：SELECTED_LABS = ['Blood Ammonia (NH3, μmol/L)', 'Total Bilirubin (TBIL, μmol/L)'] or None
SELECTED_LABS = ['Blood Ammonia (NH3, μmol/L)']

# ── 软编码关键词（统一置顶，便于维护） ──────────────────
# 检验报告段中需跳过的报告块（防止培养/药敏数据污染化验结果）
SKIP_LAB_KEYWORDS = ['培养', '药敏']

# 出院小结中化验段的起始/结束标签
LAB_SECTION_START_KW = ['主要化验结果', '主要化验', '化验结果', '检验结果', '住院期间主要化验']
LAB_SECTION_END_KW   = ['特殊检查', '诊疗经过', '出院时情况', '出院后用药']

# 影像/超声列名关键词（用于输出列排序时识别检查类列）
EXAM_KEYWORDS = ['CT', 'MRI', 'CTA', '超声', '心电图', '心超', '胸片', 'ECT', 'DSA', 'TCD',
                 '肺功能', '脑电图', '肌电图']

# 结构化检查报告标题（影像提取 pat2 使用）
STRUCTURED_EXAM_TITLES = ['头颅MRI', '头颅CTA', '颈部CTA', '头颅MRA', '颈部MRA',
                          'DSA', 'TCD', '颈动脉超声', '心电图', '心脏超声']


# ──────────────────────────────────────────────
# 实验室指标字典（key: 英文全称+缩写+单位；value: 中文/英文别名列表）
# ──────────────────────────────────────────────
LAB_DICT = {
    # 血常规
    'White Blood Cell Count (WBC, ×10⁹/L)':        ['白细胞计数', 'WBC计数', '白细胞', 'WBC'],
    'Neutrophil Count (NEUT#, ×10⁹/L)':            ['中性粒细胞计数', '中性细胞数', '中性粒细胞绝对值', '中性粒细胞数', 'NEUT#'],
    'Neutrophil Percentage (NEUT%, %)':             ['中性粒细胞百分比', '中性细胞比率', '中性粒细胞%', '中性粒细胞 %', 'NEUT%'],
    'Lymphocyte Count (LYMPH#, ×10⁹/L)':           ['淋巴细胞计数', '淋巴细胞数', '淋巴细胞绝对值', 'LYMPH#'],
    'Lymphocyte Percentage (LYMPH%, %)':            ['淋巴细胞百分比', '淋巴细胞比率', '淋巴细胞%', '淋巴细胞 %', 'LYMPH%'],
    'Monocyte Count (MONO#, ×10⁹/L)':              ['单核细胞计数', '单核细胞数', '单核细胞绝对值', 'MONO#'],
    'Monocyte Percentage (MONO%, %)':               ['单核细胞百分比', '单核细胞比率', '单核细胞%', '单核细胞 %', 'MONO%'],
    'Eosinophil Count (EO#, ×10⁹/L)':              ['嗜酸性粒细胞数', '嗜酸细胞计数', '嗜酸粒细胞计数', '嗜酸细胞绝对值', 'EO#'],
    'Eosinophil Percentage (EO%, %)':               ['嗜酸性粒细胞比率', '嗜酸细胞百分比', '嗜酸粒细胞%', 'EO%'],
    'Basophil Count (BASO#, ×10⁹/L)':              ['嗜碱性粒细胞数', '嗜碱细胞计数', '嗜碱粒细胞计数', 'BASO#'],
    'Basophil Percentage (BASO%, %)':               ['嗜碱性粒细胞比率', '嗜碱细胞百分比', 'BASO%'],
    'Red Blood Cell Count (RBC, ×10¹²/L)':         ['红细胞计数', '红细胞', 'RBC计数', 'RBC'],
    'Hemoglobin (HGB, g/L)':                        ['血红蛋白', 'HGB', 'Hb', 'HgB'],
    'Hematocrit (HCT, %)':                          ['红细胞压积', 'HCT', 'Hct'],
    'Mean Corpuscular Volume (MCV, fL)':            ['红细胞平均体积', '平均红细胞体积', 'MCV'],
    'Mean Corpuscular Hemoglobin (MCH, pg)':        ['平均血红蛋白量', '平均血红蛋白含量', 'MCH'],
    'Mean Corpuscular Hemoglobin Conc (MCHC, g/L)':['平均血红蛋白浓度', 'MCHC'],
    'RBC Distribution Width-SD (RDW-SD, fL)':      ['红细胞分布宽度', 'RDW-SD', 'RDW'],
    'RBC Distribution Width-CV (RDW-CV, %)':        ['红细胞分布宽度CV', 'RDW-CV'],
    'Platelet Count (PLT, ×10⁹/L)':                ['血小板计数', '血小板', 'PLT'],
    'Mean Platelet Volume (MPV, fL)':               ['平均血小板体积', 'MPV'],
    'Platelet Distribution Width (PDW, fL)':        ['血小板分布宽度', 'PDW'],
    'Platelet-Large Cell Ratio (P-LCR, %)':         ['大型血小板比率', 'P-LCR'],
    'Reticulocyte Percentage (RET%, %)':            ['网织红细胞百分比', '网织红细胞%', 'RET%'],
    'Reticulocyte Count (RET#, ×10⁹/L)':           ['网织红细胞计数', '网织红细胞绝对值', 'RET#'],

    # 凝血
    'Prothrombin Time (PT, s)':                     ['凝血酶原时间(PT)', '凝血酶原时间', 'PT'],
    'Prothrombin Activity (PTA, %)':                ['凝血酶原活动度', 'PT活动度', 'PTA'],
    'Activated Partial Thromboplastin Time (APTT, s)':['活化部分凝血活酶时间', '部分凝血酶原时间', 'APTT'],
    'Thrombin Time (TT, s)':                        ['凝血酶时间(TT)', '凝血酶时间', 'TT'],
    'Fibrinogen (FIB, g/L)':                        ['纤维蛋白原(CFib)', '纤维蛋白原', 'FI', 'Fib', 'CFib'],
    'D-Dimer (mg/L FEU)':                           ['D-D二聚体', 'D-D(五联)', '血浆D-二聚体测定', 'D-二聚体', 'D二聚体', 'D-dimer'],
    'Fibrin Degradation Products (FDP, μg/mL)':     ['纤维蛋白(原)降解产物', '血浆FDP', 'FDP'],
    'International Normalized Ratio (INR)':         ['国际标准化比值', 'INR'],
    'Antithrombin Activity (AT, %)':                ['抗凝血酶活性', 'AT'],

    # 生化-肝功
    'Alanine Aminotransferase (ALT, U/L)':          ['丙氨酸氨基转移酶', '谷丙转氨酶', 'ALT', 'GPT', 'AUTO1476'],
    'Aspartate Aminotransferase (AST, U/L)':        ['天门冬氨酸氨基转移酶', '天门冬氨酸转移酶', '谷草转氨酶', 'AST', 'GOT'],
    'Alkaline Phosphatase (ALP, U/L)':              ['碱性磷酸酶', 'ALP', 'AKP'],
    'Gamma-Glutamyl Transferase (GGT, U/L)':        ['r-谷氨酰基转移酶', 'γ-谷氨酰转肽酶', 'GGT', 'γ-GT'],
    'Total Bilirubin (TBIL, μmol/L)':               ['总胆红素', 'TBIL', 'TB'],
    'Direct Bilirubin (DBIL, μmol/L)':              ['直接胆红素', 'DBIL', 'DB'],
    'Indirect Bilirubin (IBIL, μmol/L)':            ['间接胆红素', 'IBIL'],
    'Total Protein (TP, g/L)':                      ['总蛋白', 'TP'],
    'Albumin (ALB, g/L)':                           ['白蛋白', 'ALB', 'Alb'],
    'Globulin (GLO, g/L)':                          ['球蛋白', 'GLO', 'GLB'],
    'Albumin/Globulin Ratio (A/G)':                 ['白球比', 'A/G'],
    'Prealbumin (PA, mg/L)':                        ['前白蛋白', 'PA'],
    'Cholinesterase (CHE, U/L)':                    ['胆碱酯酶', 'CHE'],
    'Total Bile Acids (TBA, μmol/L)':               ['总胆汁酸', 'TBA'],
    'Lactate Dehydrogenase (LDH, U/L)':             ['乳酸脱氢酶', 'LDH'],

    # 生化-肾功/电解质
    'Creatinine (CREA, μmol/L)':                    ['肌酐', 'CREA', 'Scr', 'Cr', 'CRE'],
    'Blood Urea Nitrogen (BUN, mmol/L)':            ['尿素', 'UREA', 'BUN', 'Urea'],
    'Uric Acid (UA, μmol/L)':                       ['尿酸', 'UA', 'SUA'],
    'Cystatin C (CysC, mg/L)':                      ['胱抑素C', 'CYCS', 'CysC', 'Cysc', '胱抑素-C'],
    'Estimated GFR (eGFR, mL/min/1.73m²)':         ['肾小球滤过率', '估算肾小球滤过率', 'eGFR', 'GFR'],
    'Potassium (K, mmol/L)':                        ['钾', 'K+', '血钾', 'K'],
    'Sodium (Na, mmol/L)':                          ['钠', 'Na+', '血钠', 'NA', 'Na'],
    'Chloride (Cl, mmol/L)':                        ['氯', 'Cl-', '血氯', 'CL', 'Cl'],
    'Calcium (Ca, mmol/L)':                         ['总钙', '钙', 'Ca²+', 'Ca2+', 'CA', 'Ca'],
    'Phosphorus (P, mmol/L)':                       ['无机磷', '血磷', 'P'],
    'Magnesium (Mg, mmol/L)':                       ['镁', 'MG', 'Mg'],
    'Carbon Dioxide (CO2, mmol/L)':                 ['二氧化碳', 'CO2'],
    'Bicarbonate (HCO3-, mmol/L)':                  ['实际碳酸氢根', '碳酸氢盐', 'HCO3-', 'HCO3'],

    # 血脂
    'Total Cholesterol (TC, mmol/L)':               ['总胆固醇', 'CHOL', 'TC'],
    'Triglycerides (TG, mmol/L)':                   ['甘油三酯', 'TG', 'TGC'],
    'HDL Cholesterol (HDL-C, mmol/L)':              ['高密度脂蛋白胆固醇', 'HDLC', 'HDL-C', 'HDL'],
    'LDL Cholesterol (LDL-C, mmol/L)':              ['低密度脂蛋白胆固醇', 'LDLC', 'LDL-C', 'LDL'],
    'Lipoprotein(a) (Lp(a), mg/L)':                ['脂蛋白(a)', '脂蛋白a', 'LPa', 'LP(a)', 'Lp(a)'],
    'Homocysteine (Hcy, μmol/L)':                   ['同型半胱氨酸', 'HCY', 'Hcy'],
    'Apolipoprotein B (ApoB, g/L)':                 ['载脂蛋白B', 'APOB'],
    'Small Dense LDL Cholesterol (sdLDL-C, mmol/L)':['小而密低密度脂蛋白胆固醇', 'sdLDL-C', 'sdLDL'],

    # 心肌酶/心功能
    'NT-proBNP / BNP (pg/mL)':                     ['N端脑钠肽前体', 'B型钠尿肽', 'NT-proBNP', 'BNP'],
    'Creatine Kinase (CK, U/L)':                    ['肌酸激酶', 'CK', 'CPK'],
    'Creatine Kinase-MB (CK-MB, U/L)':              ['肌酸激酶同工酶', 'CKMB含量', 'CK-MB', 'CKMB'],
    'Cardiac Troponin I (cTnI, ng/mL)':             ['肌钙蛋白I', 'TNI(三联)', 'cTnI', 'TnI'],
    'Cardiac Troponin T (cTnT, ng/mL)':             ['肌钙蛋白T', 'cTnT', 'TnT'],
    'Myoglobin (MYO, ng/mL)':                       ['肌红蛋白', 'MYO(三联)', 'MYO', 'Mb'],

    # 炎症/免疫
    'C-Reactive Protein (CRP, mg/L)':              ['超敏C反应蛋白', 'C反应蛋白', 'CRP', 'hs-CRP'],
    'Procalcitonin (PCT, ng/mL)':                   ['降钙素原', 'PCT'],
    'Interleukin-6 (IL-6, pg/mL)':                  ['白介素6', 'IL-6', 'IL6'],
    'Ferritin (ng/mL)':                             ['铁蛋白', 'Ferritin'],
    'Erythrocyte Sedimentation Rate (ESR, mm/h)':   ['红细胞沉降率', 'ESR', '血沉'],
    'Serum Amyloid A (SAA, mg/L)':                  ['血清淀粉样蛋白A', 'SAA'],

    # 甲状腺功能
    'Thyroid-Stimulating Hormone (TSH, mIU/L)':     ['促甲状腺激素', 'TSH'],
    'Free Triiodothyronine (FT3, pmol/L)':          ['游离三碘甲状腺原氨酸', '游离T3', 'FT3'],
    'Free Thyroxine (FT4, pmol/L)':                 ['游离甲状腺素', '游离T4', 'FT4'],
    'Triiodothyronine (T3, nmol/L)':                ['三碘甲状腺原氨酸', 'T3'],
    'Thyroxine (T4, nmol/L)':                       ['甲状腺素', 'T4'],

    # 肿瘤标志物
    'Carcinoembryonic Antigen (CEA, ng/mL)':        ['癌胚抗原CEA(发光)', '癌胚抗原CEA', '癌胚抗原', 'CEA'],
    'Cytokeratin 19 Fragment (CYFRA21-1, ng/mL)':   ['细胞角化蛋白19片段(发光)', '非小细胞肺癌相关抗原21-1',
                                                      '细胞角蛋白19片段', 'CY21-1', 'CYFRA21-1', 'Cyfra'],
    'Neuron-Specific Enolase (NSE, ng/mL)':         ['神经元特异烯醇化酶测定', '神经元特异性烯醇化酶(发光)',
                                                      '神经元特异性烯醇化酶', 'NSE'],
    'Alpha-Fetoprotein (AFP, ng/mL)':               ['甲胎蛋白测定(AFP)', '甲胎蛋白', 'AFP'],
    'Carbohydrate Antigen 19-9 (CA19-9, U/mL)':    ['糖类抗原（CA19-9）', '糖类抗原199', 'CA199', 'CA-199', 'CA19-9'],
    'Carbohydrate Antigen 125 (CA125, U/mL)':       ['糖类抗原（CA-125）', '糖类抗原125', 'CA125', 'CA-125'],
    'Carbohydrate Antigen 153 (CA153, U/mL)':       ['糖类抗原153', 'CA153', 'CA-153'],
    'Carbohydrate Antigen 72-4 (CA72-4, U/mL)':    ['糖类抗原724', 'CA72-4', 'CA724'],
    'Squamous Cell Carcinoma Antigen (SCC, ng/mL)': ['鳞癌抗原SCC', '鳞状细胞癌相关抗原测定(SCC)', '鳞状细胞癌抗原', 'SCC'],
    'Pro-Gastrin-Releasing Peptide (ProGRP, pg/mL)':['胃泌素释放肽前体', 'ProGRP'],

    # 血气分析
    'pH':                                           ['酸碱度', 'PH值', 'pH值', 'PH', 'pH'],
    'Arterial Oxygen Partial Pressure (PaO2, mmHg)':['血氧分压', '氧分压', 'PaO2', 'PO2'],
    'Arterial CO2 Partial Pressure (PaCO2, mmHg)':  ['二氧化碳分压', 'PaCO2', 'PCO2'],
    'Oxygen Saturation (SaO2, %)':                  ['血氧饱和度', '氧饱和度', 'SaO2', 'SpO2', 'SO2'],
    'Total CO2 (tCO2, mmol/L)':                     ['二氧化碳总量(tCO2)', '总二氧化碳', '二氧化碳总量', 'tCO2'],
    'Standard Bicarbonate (SB, mmol/L)':            ['标准碳酸氢根'],
    'Base Excess (BE, mmol/L)':                     ['实际剩余碱', '标准剩余碱', 'SBE', 'BE'],

    # 乙肝五项
    'Hepatitis B Surface Antigen (HBsAg)':          ['乙肝表面抗原(发光)', '乙肝表面抗原', 'HBsAg'],
    'Hepatitis B Surface Antibody (HBsAb)':         ['乙肝表面抗体(发光)', '乙肝表面抗体', 'HBsAb', 'Anti-HBs'],
    'Hepatitis B e-Antigen (HBeAg)':                ['乙肝e抗原(发光)', '乙肝e抗原', 'HBeAg'],
    'Hepatitis B e-Antibody (HBeAb)':               ['乙肝e抗体(发光)', '乙肝e抗体', 'HBeAb'],
    'Hepatitis B Core Antibody (HBcAb)':            ['乙肝核心抗体(发光)', '乙肝核心抗体', 'HBcAb', 'Anti-HBc'],

    # 糖代谢
    'Glycated Hemoglobin (HbA1c, %)':               ['糖化血红蛋白', 'HbA1c', 'A1c'],
    'Glycated Albumin (GA, %)':                     ['糖化白蛋白', 'GA-L', 'GA'],
    'Glucose (GLU, mmol/L)':                        ['葡萄糖', '空腹葡萄糖', 'GLU', 'Glu'],

    # 肝性脑病相关
    'Blood Ammonia (NH3, μmol/L)':                  ['血氨', '氨', 'NH3', '血液氨', '动脉血氨'],

    # 腹水常规/生化
    'Ascitic WBC (×10⁶/L)':                        ['腹水白细胞', '腹腔积液白细胞', '腹水有核细胞'],
    'Ascitic RBC (×10⁶/L)':                        ['腹水红细胞', '腹腔积液红细胞'],
    'Ascitic Protein (g/L)':                        ['腹水总蛋白', '腹腔积液蛋白', '腹水蛋白', '腹腔蛋白'],
    'Ascitic LDH (U/L)':                            ['腹水乳酸脱氢酶', '腹腔积液LDH', '腹水LDH'],
    'Ascitic Albumin (g/L)':                        ['腹水白蛋白', '腹腔积液白蛋白'],

    # 其他常见
    'Beta-2 Microglobulin (β2-MG, mg/L)':          ['β2-微球蛋白', 'β2-MG'],
    'Retinol-Binding Protein (RBP, mg/L)':          ['视黄醇结合蛋白', 'RBP1'],
    'Folate (μmol/L)':                              ['叶酸'],
    'Vitamin B12 (pmol/L)':                         ['维生素B12'],
}

# 构建别名→标准名反向映射（按长度降序优先匹配更长别名）
_ALIAS_MAP = {}
for _std, _aliases in LAB_DICT.items():
    for _alias in _aliases:
        _ALIAS_MAP[_alias] = _std
_SORTED_ALIASES = sorted(_ALIAS_MAP.keys(), key=len, reverse=True)


def _find_std_name(candidate: str) -> str | None:
    candidate = candidate.strip()
    for alias in _SORTED_ALIASES:
        if alias in candidate or candidate == alias:
            return _ALIAS_MAP[alias]
    return None


def clean_text(text: str) -> str:
    return text.strip().replace('\xa0', ' ').replace('\u3000', ' ')


# ──────────────────────────────────────────────
# 文件读取
# ──────────────────────────────────────────────
def _read_file(filepath: str) -> str:
    for enc in ('utf-8', 'utf-8-sig', 'gb18030', 'gbk', 'gb2312'):
        try:
            return open(filepath, 'r', encoding=enc, errors='strict').read()
        except (UnicodeDecodeError, ValueError):
            continue
    return open(filepath, 'r', encoding='utf-8', errors='ignore').read()


# ──────────────────────────────────────────────
# 临床诊断提取（肝性脑病 / 腹水 / 黄疸 / 上消化道出血 / 门脉高压 / 肺动脉高压 / 肾病综合征）
# ──────────────────────────────────────────────
# 关键词配置
CLINICAL_DICT = {
    'Hepatic Encephalopathy (HE)': {
        'keywords': ['肝性脑病', '肝脑病', '肝昏迷', '肝性昏迷', 'hepatic encephalopathy'],
        'grade_patterns': [
            (r'(?:I|一|1)\s*期', 'Grade I'),
            (r'(?:II|二|2)\s*期', 'Grade II'),
            (r'(?:III|三|3)\s*期', 'Grade III'),
            (r'(?:IV|四|4)\s*期', 'Grade IV'),
            (r'轻(?:度|型)', 'Grade I'),
            (r'中(?:度|型)', 'Grade II'),
            (r'重(?:度|型)', 'Grade III/IV'),
        ],
    },
    'Ascites': {
        'keywords': ['腹水', '腹腔积液', '腹腔液', '腹腔渗出'],
        'grade_patterns': [
            (r'少(?:量|许)', 'Mild'),
            (r'中(?:量|度)', 'Moderate'),
            (r'大量', 'Severe'),
            (r'张力性', 'Tense'),
        ],
    },
    'Jaundice': {
        'keywords': ['黄疸', '皮肤黄染', '巩膜黄染', '皮巩黄染', '皮肤巩膜黄染', '黄染'],
        'grade_patterns': [
            (r'轻(?:度|型)', 'Mild'),
            (r'中(?:度|型)', 'Moderate'),
            (r'重(?:度|型)', 'Severe'),
            (r'深(?:度)?', 'Severe'),
        ],
    },
    'Upper GI Bleeding (UGIB)': {
        'keywords': ['上消化道出血', '上消道出血', '呕血', '黑便', '食管静脉曲张破裂出血', '消化道出血'],
        'grade_patterns': [
            (r'轻(?:度|型)', 'Mild'),
            (r'中(?:度|型)', 'Moderate'),
            (r'重(?:度|型)', 'Severe'),
            (r'大量', 'Severe'),
        ],
    },
    'Portal Hypertension (PHT)': {
        'keywords': ['门脉高压', '门静脉高压', '门静脉高压症', '门脉高压症'],
        'grade_patterns': [],
    },
    'Pulmonary Arterial Hypertension (PAH)': {
        'keywords': ['肺动脉高压', '肺高压', '肺高血压', 'PAH'],
        'grade_patterns': [
            (r'轻(?:度|型)', 'Mild'),
            (r'中(?:度|型)', 'Moderate'),
            (r'重(?:度|型)', 'Severe'),
        ],
    },
    'Nephrotic Syndrome (NS)': {
        'keywords': ['肾病综合征', '肾综', '肾病综合症'],
        'grade_patterns': [],
    },
}

def _extract_clinical_diagnoses(block: str) -> dict:
    """

    从出院小结文本中提取肝性脑病、腹水、黄疸的有无及分级。
    返回字段：'Hepatic Encephalopathy (HE)', 'Ascites', 'Jaundice'
    值格式：'Yes' / 'Yes (Grade II)' / 'No'（未提及则留空）
    """
    res = {}

    # 拼接需要搜索的文本段（诊断 + 病史）
    search_text = block  # 直接搜索整个块，确保不遗漏

    for field_name, cfg in CLINICAL_DICT.items():
        found = False
        grade_str = ''
        for kw in cfg['keywords']:
            if kw in search_text:
                found = True
                # 找到关键词后，提取其周围30字符内的分级描述
                idx = search_text.find(kw)
                context = search_text[max(0, idx - 5): idx + len(kw) + 30]
                for pat, label in cfg['grade_patterns']:
                    if re.search(pat, context):
                        grade_str = label
                        break
                break

        if found:
            res[field_name] = f'Yes ({grade_str})' if grade_str else 'Yes'
        # 未找到则不写入（留空，不写 'No'，避免与真实缺失混淆）

    return res


# ──────────────────────────────────────────────
# 化验值解析（出院小结文本段）
# ──────────────────────────────────────────────
def _extract_labs_from_text(text: str, labs: dict):
    """从出院小结中的化验文本段提取化验值"""
    date_pat = re.compile(r'\b(\d{2,4}[-./]\d{1,2}[-./]\d{1,2})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b')
    segments = date_pat.split(text)
    current_date = None
    for seg in segments:
        dm = date_pat.fullmatch(seg.strip())
        if dm:
            raw = seg.strip().replace('.', '-').replace('/', '-')
            parts = raw.split('-')
            if len(parts[0]) == 2:
                raw = '20' + raw
            current_date = raw
            continue
        if current_date:
            _parse_segment(seg, current_date, labs)


def _parse_segment(seg: str, date: str, labs: dict):
    lines = re.split(r'\n', seg)
    for line in lines:
        line = re.sub(r'^[【\[].{0,30}[】\]]\s*', '', line.strip())
        if not line:
            continue
        items = re.split(r'[,，;；]', line)
        for item in items:
            item = item.strip()
            if not item:
                continue
            for sep in ('：', ':'):
                if sep in item:
                    pos = item.find(sep)
                    tail = item[pos + 1:].strip()
                    if tail and re.search(r'\d', tail):
                        item = tail
                    break
            m = re.match(
                r'^(.+?)\s*([<>≥≤=]?\s*\d+\.?\d*(?:e[+-]?\d+)?)\s*([↑↓])?\s*'
                r'([^\s,，;；。\n\r↑↓偏]{0,30})?\s*([↑↓])?\s*(?:偏高|偏低|偏|正常)?$',
                item
            )
            if m:
                name = m.group(1).strip()
                val  = m.group(2).strip()
                # 不拼接单位，只保留数值
                std  = _find_std_name(name)
                if std:
                    if std not in labs:
                        labs[std] = []
                    if (date, val) not in labs[std]:
                        labs[std].append((date, val))


# ──────────────────────────────────────────────
# 结构化检验报告解析（=== 检验报告 === 段）
# ──────────────────────────────────────────────
def _parse_structured_report_block(block: str, labs: dict):
    """
    解析单份检验报告单（竖排格式）：
    序号 检验项目 项目代码 结果
    """
    report_date = None
    dm = re.search(r'报告时间[：:\s]*(\d{4}-\d{2}-\d{2})', block)
    if dm:
        report_date = dm.group(1)
    else:
        dm = re.search(r'采样时间[：:\s]*(\d{4}-\d{2}-\d{2})', block)
        if dm:
            report_date = dm.group(1)
    if not report_date:
        dm = re.search(r'(\d{4}-\d{2}-\d{2})', block)
        if dm:
            report_date = dm.group(1)
    if not report_date:
        return

    header_idx = block.find('序号 检验项目 项目代码 结果')
    if header_idx == -1:
        return
    table_text = block[header_idx + len('序号 检验项目 项目代码 结果'):]

    lines = [l.strip() for l in table_text.split('\n') if l.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r'\d+', line):
            if i + 1 >= len(lines):
                break
            item_name = lines[i + 1].strip()
            if i + 2 >= len(lines):
                break
            item_code = lines[i + 2].strip()
            if i + 3 >= len(lines):
                break
            result_line = lines[i + 3].strip()
            result_m = re.match(r'^([<>≥≤]?\s*\d+\.?\d*(?:e[+-]?\d+)?)$', result_line)
            if not result_m:
                i += 1
                continue
            val = result_line  # 只保留数值，不附加单位

            std = _find_std_name(item_name)
            if not std:
                std = _find_std_name(item_code)
            if std:
                if std not in labs:
                    labs[std] = []
                if (report_date, val) not in labs[std]:
                    labs[std].append((report_date, val))
            i += 1
        else:
            i += 1


def _merge_labs(labs: dict) -> dict:
    """合并 labs 字典：同一指标按日期排序，去重后拼接为字符串"""
    merged = {}
    for std, pairs in labs.items():
        seen = set()
        unique = []
        for pair in sorted(pairs):
            if pair not in seen:
                seen.add(pair)
                unique.append(pair)
        merged[std] = ';'.join(f"{d}:{v}" for d, v in unique)
    return merged


# ──────────────────────────────────────────────
# 影像/超声报告提取

# ──────────────────────────────────────────────
_EXAM_COL_MAP = {
    ('CT', '胸部'):      'CT',
    ('CT', '肺'):        'CT',
    ('HRCT',):           'CT',
    ('CT', '头颅'):      'CT',
    ('CT', '脑'):        'CT',
    ('CT', '腹部'):      'CT',
    ('CT', '腹'):        'CT',
    ('CTA', '颈'):       'CTA',
    ('CTA', '头'):       'CTA',
    ('CT', '颈'):        'CT',
    ('CT',):             'CT',
    ('MRI', '头颅'):     'MRI',
    ('MRI', '颅'):       'MRI',
    ('MR', '头颅'):      'MRI',
    ('MRI',):            'MRI',
    ('MR',):             'MRI',
    ('颈动脉', '椎动脉'):  '超声',
    ('颈动脉',):          '超声',
    ('心超',):            '彩超',
    ('心功能',):          '彩超',
    ('腹部', '超声'):     '超声',
    ('彩超',):            '超声',
    ('超声',):            '超声',
    ('心电图',):          '心电图',
    ('动态心电图',):      '心电图',
    ('肺功能',):          '肺功能检查',
    ('脑电图',):          '脑电图',
    ('肌电图',):          '肌电图',
    ('骨显像',):          'ECT',
    ('ECT',):             'ECT',
    ('DSA',):             'DSA',
    ('TCD',):             'TCD',
    ('胸片',):            'X ray',
    # ── 真实检查扩充（方案B）：多关键词组合需排在单关键词回退之前 ──
    ('冠状动脉造影',):    '冠脉造影',
    ('冠脉造影',):        '冠脉造影',
    ('Holter',):          '动态心电图',
    ('动态血压',):        '动态血压',
    ('心脏', 'B超'):      '彩超',
    ('下肢', '动脉'):     '超声',

    ('下肢', '静脉'):     '超声',
    ('下肢血管',):        '超声',
    ('颈部血管',):        '超声',
    ('支气管镜',):        '支气管镜',
    ('胃镜',):            '胃镜',
    ('肠镜',):            '肠镜',
    ('ERCP',):            'ERCP',
    ('冰冻',):            '病理检查',
    ('活检',):            '病理检查',
    ('病理',):            '病理检查',
    ('骨髓',):            '病理检查',
}


def _normalize_exam_title(title: str):
    for keys, col in _EXAM_COL_MAP.items():
        if all(k in title for k in keys):
            return col
    return None



def _extract_exam_reports(text: str) -> dict:
    """从特殊检查段、住院经过段提取影像/超声报告"""
    reports = {}
    pat1 = re.compile(
        r'(\d{4}[-./]\d{1,2}[-./]\d{1,2})\s*'
        r'([^\n（(]{2,30}?)(?:[（(][^）)]{0,30}[）)])?'
        r'[：:]\s*(.{10,400}?)(?=\n\d{4}[-./]|\n\n|$)',
        re.S
    )
    for m in pat1.finditer(text):
        date = m.group(1).replace('.', '-').replace('/', '-')
        title = m.group(2).strip()
        content = re.sub(r'\s+', ' ', m.group(3)).strip()[:300]
        col = _normalize_exam_title(title)
        if col is None:
            continue
        entry = f"{date}:{content}"
        if col not in reports:
            reports[col] = []
        if entry not in reports[col]:
            reports[col].append(entry)

    pat2 = re.compile(
        r'(' + '|'.join(STRUCTURED_EXAM_TITLES) + r')'
        r'[：:\[].*?(\d{4}-\d{2}-\d{2})[^\n]*?检查结果[：:\[](.{5,300}?)[』\]]',
        re.S
    )

    for m in pat2.finditer(text):
        title = m.group(1)
        date = m.group(2)
        content = re.sub(r'\s+', ' ', m.group(3)).strip()[:300]
        col = _normalize_exam_title(title)
        if col is None:
            continue
        entry = f"{date}:{content}"

        if col not in reports:
            reports[col] = []
        if entry not in reports[col]:
            reports[col].append(entry)

    return {k: ';'.join(v) for k, v in reports.items()}


# ──────────────────────────────────────────────
# 出院带药提取
# ──────────────────────────────────────────────
def _extract_discharge_meds(text: str) -> str:
    """从出院带药/出院医嘱段提取药物列表"""
    m = re.search(
        r'(?:出院带药|出院后用药建议|出院医嘱)[：:\s]*\[?\s*(.{10,2000}?)(?:\n\n|抗栓药物|调脂类|降压|$)',
        text, re.S
    )
    if not m:
        m = re.search(r'出院带药[：:\s]*(.{10,1000}?)(?:\n\n|$)', text, re.S)
    if m:
        raw = m.group(1).strip().rstrip(']')
        lines = [l.strip() for l in re.split(r'\n|；', raw) if l.strip()]
        meds = [l for l in lines if re.search(r'[\u4e00-\u9fff]{2,}', l) and len(l) < 100]
        return ' | '.join(meds[:20]) if meds else raw[:200]
    return ''


# ──────────────────────────────────────────────
# 出院小结基本信息提取
# ──────────────────────────────────────────────
def _parse_discharge_summary(block: str) -> dict:
    res = {}

    # 姓名
    for pat in [
        r'姓名[：:]\s*([\u4e00-\u9fff]{1,5})\s*(?:科室|性别|病区|床位|年龄|\[)',
        r'姓名\s*[：:]\s*\[?([\u4e00-\u9fff]{2,5})\]?',
        r'(?:^|\n)姓名[：:]\s*([\u4e00-\u9fff]{2,5})',
    ]:
        m = re.search(pat, block)
        if m and '*' not in m.group(1):
            res['患者姓名'] = m.group(1).strip()
            break

    # 性别
    m = re.search(r'性别[：:]\s*(男|女|男性|女性)', block)
    if m:
        v = m.group(1)
        res['性别'] = '男' if '男' in v else '女'

    # 年龄
    m = re.search(r'年龄[：:]\s*\[?(\d{1,3})\]?\s*(?:岁|周岁)?', block)
    if m:
        res['年龄(岁)'] = m.group(1)

    # 住院号
    for label in ['住院号', '病历号']:
        m = re.search(label + r'[：:]\s*\[?(\d{5,10})\]?', block)
        if m:
            res['住院号'] = m.group(1)
            break

    # 科室
    for pat in [
        r'科别[：:]\s*\[?([\u4e00-\u9fff()（）a-zA-Z0-9]{2,20})\]?',
        r'科室[：:]\s*([\u4e00-\u9fff()（）a-zA-Z0-9]{2,20})',
    ]:
        m = re.search(pat, block)
        if m:
            res['科室'] = m.group(1).strip()
            break

    # 入院日期
    m = re.search(r'入院日期(?:时间)?[：:]\s*\[?(\d{4}[-./]\d{1,2}[-./]\d{1,2})', block)
    if m:
        res['入院时间'] = m.group(1).replace('.', '-').replace('/', '-')

    # 出院日期
    m = re.search(r'出院日期[：:]\s*\[?(\d{4}[-./]\d{1,2}[-./]\d{1,2})', block)
    if m:
        res['出院时间'] = m.group(1).replace('.', '-').replace('/', '-')

    # 门诊诊断
    m = re.search(r'门诊诊断[：:]\s*(.{3,200}?)(?:\n|入院)', block, re.S)
    if m:
        res['门诊诊断'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:200]

    # 入院诊断
    m = re.search(r'入院诊断[：:]\s*\[?(.{5,500}?)\]?(?:\n(?:出院|OCSP|TOAST|合并)|$)', block, re.S)
    if m:
        res['入院诊断'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:500]

    # 出院诊断
    m = re.search(r'出院诊断[：:]\s*\[?(.{5,500}?)\]?(?:\nTOAST|\n合并|\n入院情况|\n住院|\n出院时|\n主治|$)',
                  block, re.S)
    if m:
        res['出院诊断'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:500]

    # 治疗结果
    m = re.search(r'治疗结果[：:\s]*[。]?\s*(?:□[^；\n]*?){0,3}?√\s*(治愈|好转|未愈|其他)', block)
    if m:
        res['治疗结果'] = m.group(1)

    m = re.search(r'记录时间[：:]\s*(\d{4}-\d{2}-\d{2})', block)
    if m:
        res['记录时间'] = m.group(1)

    # 入院血压
    for bp_pat in [
        r'(?:T[\d.]+℃[，,]\s*)?BP\s*(\d{2,3})/(\d{2,3})\s*mmHg',
        r'血压[\s：:]*(\d{2,3})[/／](\d{2,3})\s*mmHg',
        r'入院(?:时)?血压[：:\s]*\[?(\d{2,3})\]/\[?(\d{2,3})\]?\s*mmHg',
    ]:
        m = re.search(bp_pat, block, re.I)
        if m:
            res['入院收缩压(mmHg)'] = m.group(1)
            res['入院舒张压(mmHg)'] = m.group(2)
            break

    # 出院血压
    m = re.search(r'出院时血压[：:\s]*\[?(\d{2,3})\]/\[?(\d{2,3})\]?\s*mmHg', block)
    if m:
        res['出院收缩压(mmHg)'] = m.group(1)
        res['出院舒张压(mmHg)'] = m.group(2)

    # 脑卒中专项
    m = re.search(r'OCSP分型[：:\s]*\[?([^\]\n]{2,30})\]?', block)
    if m:
        res['OCSP分型'] = m.group(1).strip()

    for pat in [r'TOAST分型[：:\s]*\[?([^\]\n]{2,30})\]?',
                r'TOAST\s*(?:分型)?[：:\s]*\[?([^\]\n]{2,30})\]?']:
        m = re.search(pat, block)
        if m:
            res['TOAST分型'] = m.group(1).strip()
            break

    m = re.search(r'病理诊断[：:\s]*\[?([^\]\n]{2,60})\]?', block)
    if m:
        res['病理诊断'] = m.group(1).strip()

    m = re.search(r'NIHSS\s*评分[：:\s]*\[?(\d+)\]?', block)
    if m:
        res['NIHSS评分'] = m.group(1)

    m = re.search(r'出院时\s*NIHSS[：:\s]*\[?(\d+)\]?', block)
    if m:
        res['出院NIHSS'] = m.group(1)

    m = re.search(r'(?:出院时)?mRS[：:\s]*\[?(\d+)\]?', block)
    if m:
        res['出院mRS'] = m.group(1)

    m = re.search(r'溶栓治疗[：:\s]*\[?([^\]\n]{1,20})\]?', block)
    if m:
        res['溶栓治疗'] = m.group(1).strip()

    m = re.search(r'抗栓药物[（(]抗凝药物或抗血小板药物[）)][：:\s]*\[?([^\]\n]{1,50})\]?', block)
    if m:
        res['出院抗栓药物'] = m.group(1).strip()

    m = re.search(r'住院期间主要用药[：:\s]*\[?(.{10,500}?)\]?\n', block, re.S)
    if m:
        res['住院主要用药'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:300]

    meds = _extract_discharge_meds(block)
    if meds:
        res['出院带药'] = meds

    m = re.search(r'入院(?:时)?情况[（(]?(?:症状及体征)?[）)]?[：:\s]*(.{20,1000}?)(?:\n主要化验|\n住院|\n诊疗经过)',
                  block, re.S)
    if m:
        res['入院情况'] = re.sub(r'\s+', ' ', m.group(1)).strip()[:500]

    m = re.search(r'合并疾病[：:\s]*\[?([^\]\n]{2,100})\]?', block)
    if m and m.group(1).strip() not in ('无', '[]'):
        res['合并疾病'] = m.group(1).strip()

    # ── 临床诊断：肝性脑病 / 腹水 / 黄疸 ──────────────────
    clinical = _extract_clinical_diagnoses(block)
    res.update(clinical)

    return res


# ──────────────────────────────────────────────
# 身高 / 体重 / BMI 提取
# ──────────────────────────────────────────────
def _extract_anthropometry(text: str) -> dict:
    res = {}
    m = re.search(r'(?:身高|H)[：:\s]*(\d{2,3}(?:\.\d)?)\s*(?:cm|CM|厘米)', text, re.I)
    if m:
        res['身高(cm)'] = m.group(1)
    m = re.search(r'(?:体重|W)[：:\s]*(\d{2,3}(?:\.\d)?)\s*(?:kg|KG|公斤)', text, re.I)
    if m:
        res['体重(kg)'] = m.group(1)
    m = re.search(r'BMI[：:\s]*(\d{1,2}(?:\.\d{1,2})?)\s*(?:kg/m2|kg/m²)?', text, re.I)
    if m:
        res['BMI'] = m.group(1)
    if '身高(cm)' in res and '体重(kg)' in res and 'BMI' not in res:
        try:
            h = float(res['身高(cm)']) / 100
            w = float(res['体重(kg)'])
            if h > 0:
                res['BMI'] = f"{w / (h * h):.1f}"
        except ValueError:
            pass
    return res


# ──────────────────────────────────────────────
# 主文件处理
# ──────────────────────────────────────────────
def process_txt_file(filepath: str) -> list[dict]:
    """处理一个 txt 文件，返回记录列表（每条出院小结一条）。"""
    try:
        text = _read_file(filepath)
    except Exception as e:
        print(f"[ERROR] 读取失败 {filepath}: {e}")
        return []

    file_id = os.path.splitext(os.path.basename(filepath))[0]

    summary_section = ''
    lab_section = ''
    split_m = re.search(r'=== 检验报告 ===', text)
    if split_m:
        summary_section = text[:split_m.start()]
        lab_section = text[split_m.start():]
    else:
        summary_section = text

    global_labs: dict = {}

    if lab_section:
        report_blocks = re.split(r'\n\[\d+\]', lab_section)
        for rb in report_blocks[1:]:
            # 跳过培养/药敏报告块，只解析检验报告
            if not re.search('|'.join(SKIP_LAB_KEYWORDS), rb[:100]):

                _parse_structured_report_block(rb, global_labs)

    summ_blocks = re.split(r'(?=\n\[\d+\][\s\S]{0,5}(?:住|门)\n)', summary_section)
    if len(summ_blocks) <= 1:
        summ_blocks = re.split(r'\n(?=\[\d+\])', summary_section)

    records = []
    for block in summ_blocks:
        block = block.strip()
        if not block:
            continue
        if not re.search(r'入院诊断|出院诊断|出院日期|出院小结', block):
            continue

        rec = {'文件ID': file_id, '文件路径': os.path.abspath(filepath)}

        # 1. 基本信息 + 临床诊断（肝性脑病/腹水/黄疸）
        basic = _parse_discharge_summary(block)
        rec.update(basic)

        # 2. 从出院小结文本中提取化验值
        text_labs: dict = {}
        lab_section_text = ''
        for kw in LAB_SECTION_START_KW:
            idx = block.find(kw)
            if idx >= 0:
                end_idx = len(block)
                for ew in LAB_SECTION_END_KW:
                    ei = block.find(ew, idx)
                    if ei > idx:
                        end_idx = min(end_idx, ei)

                lab_section_text = block[idx:end_idx]
                break
        if not lab_section_text:
            lab_section_text = block
        _extract_labs_from_text(lab_section_text, text_labs)

        # 3. 合并化验
        combined_labs: dict = {}
        for std, pairs in text_labs.items():
            combined_labs.setdefault(std, []).extend(pairs)
        for std, pairs in global_labs.items():
            combined_labs.setdefault(std, []).extend(pairs)

        # 4. 按 SELECTED_LABS 过滤（None 则全部保留）
        if SELECTED_LABS is not None:
            combined_labs = {k: v for k, v in combined_labs.items() if k in SELECTED_LABS}

        merged = _merge_labs(combined_labs)
        rec.update(merged)

        # 5. 影像/超声报告
        exam_reports = _extract_exam_reports(block)
        rec.update(exam_reports)

        # 6. 身高 / 体重 / BMI
        anthro = _extract_anthropometry(block)
        rec.update(anthro)

        # 7. 记录专科
        if '脑梗死' in block or '卒中' in block or 'OCSP' in block or 'TOAST' in block:
            rec['记录专科'] = '神经内科-脑卒中'
        else:
            dept = rec.get('科室', '')
            rec['记录专科'] = dept

        records.append(rec)

    if not records and global_labs:
        rec = {'文件ID': file_id, '文件路径': os.path.abspath(filepath)}
        basic = _parse_discharge_summary(summary_section[:3000])
        rec.update(basic)

        filtered_labs = global_labs
        if SELECTED_LABS is not None:
            filtered_labs = {k: v for k, v in global_labs.items() if k in SELECTED_LABS}
        merged = _merge_labs(filtered_labs)
        rec.update(merged)
        records.append(rec)

    return records


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────
def main():
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    files = sorted(glob.glob(os.path.join(INPUT_DIR, '**', '*.txt'), recursive=True))
    print(f"发现 {len(files)} 个 .txt 文件")

    all_data = []
    for i, f in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {os.path.basename(f)}", end='  ')
        recs = process_txt_file(f)
        print(f"→ {len(recs)} 条记录")
        all_data.extend(recs)

    if not all_data:
        print("未提取到任何数据，请检查文件路径和格式。")
        return

    df = pd.DataFrame(all_data)

    # 列排序
    CLINICAL_COLS = list(CLINICAL_DICT.keys())  # 临床诊断列
    FIXED_COLS = [
        '文件ID', '患者姓名', '性别', '年龄(岁)', '住院号', '科室',
        '入院时间', '出院时间', '记录时间', '记录专科',
        '入院收缩压(mmHg)', '入院舒张压(mmHg)', '出院收缩压(mmHg)', '出院舒张压(mmHg)',
        '门诊诊断', '入院诊断', '出院诊断', '病理诊断',
        'OCSP分型', 'TOAST分型', 'NIHSS评分', '出院NIHSS', '出院mRS',
        '溶栓治疗', '出院抗栓药物', '合并疾病',
        '治疗结果', '住院主要用药', '出院带药',
        '入院情况',
    ] + CLINICAL_COLS  # 临床诊断列紧跟基本信息之后

    lab_cols  = sorted([c for c in df.columns if c in LAB_DICT])
    exam_cols = sorted([c for c in df.columns
                        if any(k in c for k in EXAM_KEYWORDS) and c not in FIXED_COLS and c not in lab_cols])

    _exclude = set(FIXED_COLS) | set(lab_cols) | set(exam_cols) | {'文件路径'}
    other_cols = sorted([c for c in df.columns if c not in _exclude])
    meta_cols = ['文件路径']

    ordered = (
        [c for c in FIXED_COLS if c in df.columns]
        + [c for c in lab_cols if c not in FIXED_COLS]
        + [c for c in exam_cols]
        + [c for c in other_cols]
        + [c for c in meta_cols if c in df.columns]
    )
    remaining = [c for c in df.columns if c not in ordered]
    ordered += remaining
    df = df[[c for c in ordered if c in df.columns]]

    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8-sig')
    print(f"\n[OK] 成功提取 {len(df)} 条记录，{len(df.columns)} 列")
    print(f"     输出: {OUTPUT_FILE}\n")

    # 填充率统计
    print("=== 字段填充率（非空 ≥ 10%）===")
    for col in df.columns:
        cnt = df[col].notna().sum()
        pct = cnt / len(df) * 100
        if pct >= 10:
            flag = '✅' if pct >= 50 else '⚠️'
            print(f"  {flag} {col}: {cnt}/{len(df)} ({pct:.0f}%)")

    zero_fill = [col for col in df.columns if df[col].notna().sum() == 0]
    if zero_fill:
        print(f"\n🔴 以下 {len(zero_fill)} 列未提取到任何数据：")
        print('  ' + ', '.join(zero_fill))


if __name__ == '__main__':
    main()
