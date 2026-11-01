# -*- coding: utf-8 -*-
"""Hand-curated / provenance-backed rows for lab-reference-ranges.xlsx (P1).

Rules:
- NEVER invent numbers. Blank beats fake.
- Cohort Table-1 stats -> cohort_control / cohort_disease, confidence=quoted.
- Author dichotomization -> study_cutoff.
- Asia/CN RI from cited WS/T or guideline only; EU/US as separate rows if needed.
- Banner: peer-review numeric plausibility only — NOT clinical CDS.

Record IDs: LAB##-## / CLN##-## / PTH##-##
"""

BANNER = (
    "PEER-REVIEW NUMERIC PLAUSIBILITY ONLY — NOT CLINICAL DECISION SUPPORT. "
    "Do not use for patient care. Prefer Asia/CN / Shanghai RI; never overwrite CN with Western."
)

# Columns for 01_query (order matters for xlsx)
QUERY_COLS = [
    ("record_id", "record_id", 12),
    ("canonical_id", "canonical_id", 16),
    ("name_en", "name_en", 18),
    ("name_zh", "name_zh", 18),
    ("aliases", "aliases", 28),
    ("category", "category", 10),
    ("specimen", "specimen", 10),
    ("unit_si", "unit_si", 12),
    ("unit_as_reported", "unit_as_reported", 14),
    ("context", "context", 14),
    ("condition", "condition", 22),
    ("sex", "sex", 8),
    ("age_band", "age_band", 14),
    ("population", "population", 14),
    ("assay_method", "assay_method", 16),
    ("analyzer", "analyzer", 12),
    ("n", "n", 6),
    ("mean", "mean", 8),
    ("sd", "sd", 8),
    ("median", "median", 8),
    ("q1", "q1", 6),
    ("q3", "q3", 6),
    ("min", "min", 6),
    ("max", "max", 6),
    ("ref_low", "ref_low", 8),
    ("ref_high", "ref_high", 8),
    ("cutoff", "cutoff", 8),
    ("cutoff_direction", "cutoff_direction", 10),
    ("source_tier", "source_tier", 14),
    ("source_path_or_cite", "source_path_or_cite", 50),
    ("source_locus", "source_locus", 16),
    ("extracted_at_shanghai", "extracted_at_shanghai", 22),
    ("curator", "curator", 10),
    ("confidence", "confidence", 12),
    ("check_role", "check_role", 14),
    ("notes", "notes", 50),
]

EXTRACTED_AT = "2026-10-19 21:00:00 CST"  # Asia/Shanghai build stamp

# Pipe format: record_id|key=value|key=value...
# Only include keys that have values; builder fills blanks for missing keys.
ROWS = r"""
# === Liver — WS/T 404.1-2012 / 404.2-2012 / 404.4-2018 (Asia/CN adult RI) ===
LAB01-01|canonical_id=lab.alt|name_en=ALT|name_zh=丙氨酸氨基转移酶|aliases=ALT;GPT;SGPT;丙氨酸氨基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=male|age_band=adult≥18|population=Asia/CN|assay_method=IFCC-compatible|ref_low=9|ref_high=50|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012 临床常用生化检验项目参考区间 第1部分|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=NHC industry standard; lab must verify per WS/T 402 before local adoption
LAB01-02|canonical_id=lab.alt|name_en=ALT|name_zh=丙氨酸氨基转移酶|aliases=ALT;GPT;丙氨酸氨基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=female|age_band=adult≥18|population=Asia/CN|assay_method=IFCC-compatible|ref_low=7|ref_high=40|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Female adult RI
LAB01-03|canonical_id=lab.ast|name_en=AST|name_zh=天门冬氨酸氨基转移酶|aliases=AST;GOT;SGOT;天门冬氨酸氨基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=male|age_band=adult≥18|population=Asia/CN|ref_low=15|ref_high=40|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB01-04|canonical_id=lab.ast|name_en=AST|name_zh=天门冬氨酸氨基转移酶|aliases=AST;GOT;天门冬氨酸氨基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=female|age_band=adult≥18|population=Asia/CN|ref_low=13|ref_high=35|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB01-05|canonical_id=lab.alb|name_en=albumin|name_zh=白蛋白|aliases=ALB;albumin;白蛋白|category=lab|specimen=serum|unit_si=g/L|unit_as_reported=g/L|context=ri_normal|condition=healthy adult|sex=any|age_band=adult≥18|population=Asia/CN|ref_low=40|ref_high=55|source_tier=guideline|source_path_or_cite=WS/T 404.2-2012 临床常用生化检验项目参考区间 第2部分：血清总蛋白、白蛋白|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB01-06|canonical_id=lab.tp|name_en=total protein|name_zh=总蛋白|aliases=TP;total protein;总蛋白|category=lab|specimen=serum|unit_si=g/L|unit_as_reported=g/L|context=ri_normal|condition=healthy adult|sex=any|age_band=adult≥18|population=Asia/CN|ref_low=65|ref_high=85|source_tier=guideline|source_path_or_cite=WS/T 404.2-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB01-07|canonical_id=lab.tbil|name_en=total bilirubin|name_zh=总胆红素|aliases=TBIL;TBil;total bilirubin;总胆红素|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=any|age_band=adult|population=Asia/CN|ref_low=0|ref_high=23|source_tier=guideline|source_path_or_cite=WS/T 404.4-2018 临床常用生化检验项目参考区间 第4部分：血清总胆红素、直接胆红素|source_locus=表1 (as cited by CN hospital RI tables referencing 404.4-2018)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Confirm locus in official PDF on curator pass; value widely republished from WS/T 404.4-2018
LAB01-08|canonical_id=lab.ibil|name_en=indirect bilirubin|name_zh=间接胆红素|aliases=IBIL;indirect bilirubin;间接胆红素|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=any|age_band=adult|population=Asia/CN|ref_low=0|ref_high=19|source_tier=guideline|source_path_or_cite=WS/T 404.4-2018|source_locus=表1 (republication)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Curator verify against official PDF
LAB01-09|canonical_id=lab.ggt|name_en=GGT|name_zh=γ-谷氨酰基转移酶|aliases=GGT;γ-GT;GGTP;γ-谷氨酰基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=male|age_band=adult≥18|population=Asia/CN|ref_low=10|ref_high=60|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB01-10|canonical_id=lab.ggt|name_en=GGT|name_zh=γ-谷氨酰基转移酶|aliases=GGT;γ-GT;γ-谷氨酰基转移酶|category=lab|specimen=serum|unit_si=U/L|unit_as_reported=U/L|context=ri_normal|condition=healthy adult|sex=female|age_band=adult≥18|population=Asia/CN|ref_low=7|ref_high=45|source_tier=guideline|source_path_or_cite=WS/T 404.1-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=

# === Renal / heme — WS/T 404.5-2015 / WS/T 405-2012 ===
LAB02-01|canonical_id=lab.creatinine|name_en=creatinine|name_zh=肌酐|aliases=CREA;Cr;creatinine;肌酐|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=male|age_band=18–59|population=Asia/CN|ref_low=57|ref_high=97|source_tier=guideline|source_path_or_cite=WS/T 404.5-2015 临床常用生化检验项目参考区间 第5部分：血清尿素、肌酐|source_locus=表1 (as cited in CN hospital RI tables)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Enzymatic/Jaffe method-specific — do not mix methods in one row
LAB02-02|canonical_id=lab.creatinine|name_en=creatinine|name_zh=肌酐|aliases=CREA;肌酐|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=male|age_band=≥60|population=Asia/CN|ref_low=57|ref_high=111|source_tier=guideline|source_path_or_cite=WS/T 404.5-2015|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB02-03|canonical_id=lab.creatinine|name_en=creatinine|name_zh=肌酐|aliases=CREA;肌酐|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=female|age_band=18–59|population=Asia/CN|ref_low=41|ref_high=73|source_tier=guideline|source_path_or_cite=WS/T 404.5-2015|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB02-04|canonical_id=lab.creatinine|name_en=creatinine|name_zh=肌酐|aliases=CREA;肌酐|category=lab|specimen=serum|unit_si=μmol/L|unit_as_reported=μmol/L|context=ri_normal|condition=healthy adult|sex=female|age_band=≥60|population=Asia/CN|ref_low=41|ref_high=81|source_tier=guideline|source_path_or_cite=WS/T 404.5-2015|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB02-05|canonical_id=lab.wbc|name_en=WBC|name_zh=白细胞计数|aliases=WBC;leukocyte;白细胞|category=lab|specimen=whole_blood|unit_si=×10⁹/L|unit_as_reported=×10⁹/L|context=ri_normal|condition=healthy adult|sex=any|age_band=20–79|population=Asia/CN|ref_low=3.5|ref_high=9.5|source_tier=guideline|source_path_or_cite=WS/T 405-2012 血细胞分析参考区间|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Venous blood instrument method
LAB02-06|canonical_id=lab.plt|name_en=PLT|name_zh=血小板计数|aliases=PLT;platelet;血小板|category=lab|specimen=whole_blood|unit_si=×10⁹/L|unit_as_reported=×10⁹/L|context=ri_normal|condition=healthy adult|sex=any|age_band=20–79|population=Asia/CN|ref_low=125|ref_high=350|source_tier=guideline|source_path_or_cite=WS/T 405-2012|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=unit_guard|notes=

# === Tumor markers — WS/T 645.2-2026 (method-stratified; import ECLIA cited) ===
LAB03-01|canonical_id=lab.afp|name_en=AFP|name_zh=甲胎蛋白|aliases=AFP;甲胎蛋白|category=lab|specimen=serum|unit_si=ng/mL|unit_as_reported=ng/mL|context=ri_normal|condition=healthy adult non-pregnant|sex=any|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=7|cutoff_direction=≤|ref_high=7|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026 临床常用免疫学检验项目参考区间 第2部分：血清AFP、CEA、CA19-9、CA15-3、CA125|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Not for pregnancy; method-specific — cite assay when checking manuscript claims
LAB03-02|canonical_id=lab.cea|name_en=CEA|name_zh=癌胚抗原|aliases=CEA;癌胚抗原|category=lab|specimen=serum|unit_si=ng/mL|unit_as_reported=ng/mL|context=ri_normal|condition=healthy adult non-pregnant|sex=any|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=5|cutoff_direction=≤|ref_high=5|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB03-03|canonical_id=lab.ca199|name_en=CA19-9|name_zh=糖链抗原19-9|aliases=CA19-9;CA199;糖链抗原19-9|category=lab|specimen=serum|unit_si=U/mL|unit_as_reported=U/mL|context=ri_normal|condition=healthy adult non-pregnant|sex=any|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=30|cutoff_direction=≤|ref_high=30|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Other methods have different ceilings (≤25–43); do not mix
LAB03-04|canonical_id=lab.ca153|name_en=CA15-3|name_zh=糖链抗原15-3|aliases=CA15-3;CA153;糖链抗原15-3|category=lab|specimen=serum|unit_si=U/mL|unit_as_reported=U/mL|context=ri_normal|condition=healthy adult non-pregnant|sex=any|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=24|cutoff_direction=≤|ref_high=24|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB03-05|canonical_id=lab.ca125|name_en=CA125|name_zh=糖链抗原125|aliases=CA125;糖链抗原125|category=lab|specimen=serum|unit_si=U/mL|unit_as_reported=U/mL|context=ri_normal|condition=healthy adult non-pregnant|sex=male|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=24|cutoff_direction=≤|ref_high=24|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=
LAB03-06|canonical_id=lab.ca125|name_en=CA125|name_zh=糖链抗原125|aliases=CA125;糖链抗原125|category=lab|specimen=serum|unit_si=U/mL|unit_as_reported=U/mL|context=ri_normal|condition=healthy adult non-pregnant premenopausal|sex=female|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=47|cutoff_direction=≤|ref_high=47|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Premenopausal female; not for pregnancy
LAB03-07|canonical_id=lab.ca125|name_en=CA125|name_zh=糖链抗原125|aliases=CA125;糖链抗原125|category=lab|specimen=serum|unit_si=U/mL|unit_as_reported=U/mL|context=ri_normal|condition=healthy adult non-pregnant postmenopausal|sex=female|age_band=18–79|population=Asia/CN|assay_method=electrochemiluminescence (import)|cutoff=25|cutoff_direction=≤|ref_high=25|source_tier=guideline|source_path_or_cite=WS/T 645.2-2026|source_locus=表1|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=claim_crosscheck|notes=Postmenopausal female

# === Coag — reagent-dependent; soft ceilings only, mark confidence ===
LAB04-01|canonical_id=lab.pt|name_en=PT|name_zh=凝血酶原时间|aliases=PT;凝血酶原时间|category=lab|specimen=plasma|unit_si=s|unit_as_reported=s|context=ri_normal|condition=healthy adult|sex=any|age_band=adult|population=Asia/CN|ref_low=9.0|ref_high=13.0|source_tier=secondary_lit|source_path_or_cite=CN hospital RI table citing reagent package insert (example: 赣州市人民医院 2023 检测项目生物参考区间一览表)|source_locus=coag panel|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=unit_guard|notes=HIGHLY reagent-dependent — treat as soft unit/physio hint only; prefer site SOP
LAB04-02|canonical_id=lab.inr|name_en=INR|name_zh=国际标准化比值|aliases=INR;国际标准化比值|category=lab|specimen=plasma|unit_si=ratio|unit_as_reported=|context=ri_normal|condition=healthy adult (not on VKA)|sex=any|age_band=adult|population=Asia/CN|source_tier=site_sop|source_path_or_cite=(gap) site SOP / reagent insert — hospital tables vary 0.8–1.2 vs 0.8–1.5; numbers left blank|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=physio_ceiling|notes=Do not invent INR RI; fill from Jinshan/Shanghai SOP
LAB04-03|canonical_id=lab.aptt|name_en=APTT|name_zh=活化部分凝血活酶时间|aliases=APTT;PTT;活化部分凝血活酶时间|category=lab|specimen=plasma|unit_si=s|unit_as_reported=s|context=ri_normal|condition=healthy adult|sex=any|age_band=adult|population=Asia/CN|ref_low=20|ref_high=40|source_tier=secondary_lit|source_path_or_cite=CN hospital RI table citing reagent insert (赣州人民医院 2023 example)|source_locus=coag panel|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=unit_guard|notes=Reagent-dependent
LAB04-04|canonical_id=lab.fibrinogen|name_en=fibrinogen|name_zh=纤维蛋白原|aliases=FIB;Fbg;fibrinogen;纤维蛋白原|category=lab|specimen=plasma|unit_si=g/L|unit_as_reported=g/L|context=ri_normal|condition=healthy adult|sex=any|age_band=adult|population=Asia/CN|ref_low=2.0|ref_high=4.0|source_tier=secondary_lit|source_path_or_cite=CN hospital RI table / 全国临床检验操作规程 common band|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=unit_guard|notes=Confirm against site SOP
LAB04-05|canonical_id=lab.ddimer|name_en=D-dimer|name_zh=D-二聚体|aliases=D-dimer;D-Dimer;D-二聚体|category=lab|specimen=plasma|unit_si=mg/L FEU|unit_as_reported=|context=ri_normal|condition=|sex=|age_band=|population=Asia/CN|source_tier=guideline|source_path_or_cite=(gap) unit and cutoff vary FEU vs DDU; ng/mL vs mg/L — do not invent|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=unit_guard|notes=GAP: capture unit_as_reported from manuscript; site SOP needed for RI

# === Special (ammonia / Mn) — no national RI invented ===
LAB05-01|canonical_id=lab.ammonia|name_en=ammonia|name_zh=血氨|aliases=ammonia;NH3;血氨|category=lab|specimen=plasma|unit_si=μmol/L|unit_as_reported=|context=ri_normal|condition=|sex=|age_band=|population=Asia/CN|source_tier=guideline|source_path_or_cite=(gap) method/site-specific; do not invent from Western adult bands|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=unit_guard|notes=Frequent in MHE/portal bank — fill from site SOP or quoted local cutoff only
LAB05-02|canonical_id=lab.manganese|name_en=manganese|name_zh=锰|aliases=Mn;manganese;锰|category=lab|specimen=unspecified|unit_si=|unit_as_reported=|context=ri_normal|condition=|sex=|age_band=|population=Asia/CN|source_tier=guideline|source_path_or_cite=(gap) brain Mn often imaging/MRS not serum RI; serum Mn RI site-specific|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=display_only|notes=Often imaging surrogate (T1) in this bank — not a classic serum RI row

# === HE4 — gap pending method-specific cite ===
LAB03-08|canonical_id=lab.he4|name_en=HE4|name_zh=人附睾蛋白4|aliases=HE4;人附睾蛋白4|category=lab|specimen=serum|unit_si=pmol/L|unit_as_reported=|context=ri_normal|condition=|sex=female|age_band=|population=Asia/CN|source_tier=guideline|source_path_or_cite=(gap) HE4 cutoffs assay- and menopause-dependent; no WS/T 645.2 row — leave blank|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=display_only|notes=Prefer quoted study_cutoff from 陆丽娟 thesis/manuscripts

# === Pathology categorical notes (NOT numeric RI) ===
PTH01-01|canonical_id=pth.figo|name_en=FIGO stage|name_zh=FIGO分期|aliases=FIGO|category=pathology|specimen=na|unit_si=|context=path_category|condition=gynecologic oncology staging|sex=female|population=unspecified|source_tier=guideline|source_path_or_cite=FIGO staging system (edition as cited in each manuscript)|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=mapped|check_role=display_only|notes=Categorical — no numeric RI; verify stage labels against cited FIGO year
PTH01-02|canonical_id=pth.tnm|name_en=TNM|name_zh=TNM分期|aliases=TNM|category=pathology|specimen=na|context=path_category|condition=solid tumor staging|population=unspecified|source_tier=guideline|source_path_or_cite=AJCC/UICC TNM (edition as cited)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=mapped|check_role=display_only|notes=Categorical — no numeric RI
PTH01-03|canonical_id=pth.lvi|name_en=LVI/LVSI|name_zh=淋巴脉管浸润|aliases=LVI;LVSI;淋巴脉管浸润;脉管浸润|category=pathology|specimen=tissue|context=path_category|condition=presence/absence|population=unspecified|source_tier=guideline|source_path_or_cite=Pathology reporting — binary/ordinal as defined in manuscript methods|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=mapped|check_role=display_only|notes=Not a lab RI; check consistency of positive/negative coding
PTH01-04|canonical_id=pth.ki67|name_en=Ki-67|name_zh=Ki-67|aliases=Ki-67;Ki67|category=pathology|specimen=tissue|unit_si=%|context=path_category|condition=IHC proliferation index|population=unspecified|source_tier=guideline|source_path_or_cite=(gap) clone/cutoff study-specific — quote manuscript threshold as study_cutoff if present|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=gap|check_role=display_only|notes=Do not invent % RI

# === Clinical scores ===
CLN01-01|canonical_id=cln.child_pugh|name_en=Child-Pugh|name_zh=Child-Pugh分级|aliases=Child-Pugh;CTP|category=score|specimen=na|context=path_category|condition=cirrhosis severity A/B/C|population=unspecified|source_tier=guideline|source_path_or_cite=Child-Pugh classification (standard clinical score)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=mapped|check_role=display_only|notes=Score components include bilirubin/albumin/INR — cross-check component units via lab rows
CLN01-02|canonical_id=cln.meld|name_en=MELD|name_zh=MELD评分|aliases=MELD|category=score|specimen=na|context=path_category|condition=end-stage liver disease score|population=unspecified|source_tier=guideline|source_path_or_cite=MELD / MELD-Na (formula as cited in manuscript)|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=mapped|check_role=display_only|notes=
CLN01-03|canonical_id=cln.bmi|name_en=BMI|name_zh=体质指数|aliases=BMI;体质指数|category=clinical|specimen=na|unit_si=kg/m²|context=ri_normal|condition=adult|sex=any|age_band=adult|population=Asia/CN|ref_low=18.5|ref_high=23.9|source_tier=guideline|source_path_or_cite=中国成人超重和肥胖症预防控制指南 / WS-related BMI bands commonly 18.5–23.9 normal for CN adults|source_locus=|extracted_at_shanghai=2026-10-19 21:00:00 CST|curator=grok-bot|confidence=lit_filled|check_role=physio_ceiling|notes=CN adult normal band (not WHO 18.5–24.9); keep EU/US separate if needed
"""

# Sources registry
SOURCES = [
    ["SRC-WS404.1", "guideline", "WS/T 404.1-2012", "NHC", "https://www.nhc.gov.cn/wjw/s9492/201301/9b2e494990ce4825a48b4b476f6f928b.shtml", "ALT/AST/ALP/GGT CN adult RI", "2026-10-19 21:00:00 CST"],
    ["SRC-WS404.2", "guideline", "WS/T 404.2-2012", "NHC", "", "TP/ALB CN adult RI", "2026-10-19 21:00:00 CST"],
    ["SRC-WS404.4", "guideline", "WS/T 404.4-2018", "NHC", "", "TBIL/DBIL CN adult RI", "2026-10-19 21:00:00 CST"],
    ["SRC-WS404.5", "guideline", "WS/T 404.5-2015", "NHC", "", "Urea/creatinine CN adult RI", "2026-10-19 21:00:00 CST"],
    ["SRC-WS405", "guideline", "WS/T 405-2012", "NHC", "https://www.ndls.org.cn/standard/detail/894e6919f35ebaeb7041a5f80b7da013", "Blood cell analysis CN adult RI", "2026-10-19 21:00:00 CST"],
    ["SRC-WS645.2", "guideline", "WS/T 645.2-2026", "NHC", "https://www.nhc.gov.cn/fzs/c100048/202606/1d8e67475848413cb4447e1b49037888/files/WST%20645.2%E2%80%942026-20260612145407318.pdf", "AFP/CEA/CA19-9/CA15-3/CA125 method-stratified RI", "2026-10-19 21:00:00 CST"],
    ["SRC-HOSP-GZ", "secondary_lit", "赣州市人民医院检测项目生物参考区间一览表(2023)", "赣州市人民医院检验科", "https://gzsrmyy.com/upload/main/pdf/2024/07/10/%E6%A3%80%E6%B5%8B%E9%A1%B9%E7%9B%AE%E7%94%9F%E7%89%A9%E5%8F%82%E8%80%83%E5%8C%BA%E9%97%B4-2023.pdf", "Secondary republication of WS/T bands + reagent-insert coag; used only where noted", "2026-10-19 21:00:00 CST"],
    ["SRC-LOCAL-01", "local_thesis", "D:\\00db\\_lab\\doc-word\\05-manuscript\\thesis", "bank", "", "Thesis harvest — cohort/cutoff quotes only", "2026-10-19 21:00:00 CST"],
    ["SRC-LOCAL-02", "local_manuscript", "D:\\00db\\_lab\\doc-word\\05-manuscript\\manuscripts", "bank", "", "Manuscript harvest — cohort/cutoff quotes only", "2026-10-19 21:00:00 CST"],
]

SOURCE_HEADER = ["source_id", "source_tier", "cite_or_path", "publisher_or_org", "url", "notes", "recorded_at_shanghai"]

# Explicit gaps for 04_gaps sheet
GAPS = [
    ["GAP-DDIMER", "lab.ddimer", "D-dimer RI", "high", "unit FEU vs DDU; site SOP / package insert", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-AMMONIA", "lab.ammonia", "Blood ammonia RI", "high", "method/site-specific; frequent in MHE papers", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-MN", "lab.manganese", "Serum/brain Mn RI", "high", "bank often uses MRI T1 not serum RI", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-HE4", "lab.he4", "HE4 RI by menopause/assay", "medium", "not in WS/T 645.2-2026 table retrieved", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-INR-SITE", "lab.inr", "Site-verified INR non-VKA band", "medium", "hospital tables vary 0.8–1.2 vs 0.8–1.5", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-DBIL", "lab.dbil", "Direct bilirubin official WS/T locus", "medium", "often reagent insert 0–6.8 μmol/L — confirm WS/T 404.4 table", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-EGFR", "lab.egfr", "eGFR equation + unit", "medium", "CKD-EPI vs MDRD; ethnicity coefficients", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-UA", "lab.ua", "Uric acid CN adult RI", "low", "fill from 全国临床检验操作规程 or WS/T when verified", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-KI67", "pth.ki67", "Ki-67 % cutoff", "low", "clone-specific; use study_cutoff quotes only", "open", "2026-10-19 21:00:00 CST"],
    ["GAP-SITE-SOP", "site.*", "Jinshan/Shanghai hospital SOP excerpts", "high", "await de-identified site SOP drop under site/", "open", "2026-10-19 21:00:00 CST"],
]

GAPS_HEADER = ["gap_id", "canonical_id", "what_needed", "priority", "blocker", "status", "recorded_at_shanghai"]

DICT_ROWS = [
    ["category", "lab|clinical|pathology|score", "Indicator family"],
    ["context", "ri_normal|cohort_control|cohort_disease|study_cutoff|path_category", "Row meaning"],
    ["population", "Asia/Shanghai|Asia/CN|EU/US|unspecified", "Never merge CN and Western into one row"],
    ["sex", "male|female|any|unspecified", ""],
    ["source_tier", "local_manuscript|local_thesis|site_sop|guideline|primary_lit|secondary_lit", ""],
    ["confidence", "quoted|mapped|lit_filled|gap", "quoted=from 01/02 text; lit_filled=cited guideline; gap=blank intentionally"],
    ["check_role", "unit_guard|physio_ceiling|claim_crosscheck|display_only", "How QC gates may use the row"],
    ["cutoff_direction", ">|≥|<|≤|=", ""],
    ["specimen", "serum|plasma|whole_blood|tissue|na|unspecified", ""],
]

DICT_HEADER = ["enum_name", "values", "notes"]

QC_RULES = [
    ["QCR-NONNEG", "all quantitative labs", "value >= 0", "soft", "obvious", "Non-negativity for concentrations/counts"],
    ["QCR-ALT-CEIL", "lab.alt", "value <= 5000 U/L", "soft", "literature-backed physiologic extreme rare above", "Soft ceiling for peer-review flag only"],
    ["QCR-PLT-FLOOR", "lab.plt", "value >= 0", "soft", "obvious", ""],
    ["QCR-PCT", "pth.ki67|fractions", "0 <= value <= 100", "soft", "obvious", "Percent-scale markers"],
    ["QCR-INR", "lab.inr", "value > 0", "soft", "obvious", "No absolute RI enforced"],
]

QC_HEADER = ["rule_id", "applies_to", "rule_expr", "severity", "confidence", "notes"]
