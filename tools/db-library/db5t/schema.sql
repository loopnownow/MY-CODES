-- schema.sql — 由 schema-v1.md 反编译生成，不要手改。
-- 生成器：tools/db-library/db5t/mkschema.py
-- source: schema-v1.md
-- source_sha256: 78664694aa6d856c43c2830f0245a03ce164ae0207afcb33b91f714861c5745f
-- 用法：newdb.py 按 @section 标记切分，家族库段落已剔除 dict 表（契 3）。
-- @section family
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- schema_version=5t/1, kind=family, family=ACN, built_at, rule=..., source_db=<旧库相对路径>,
-- source_sha256=..., period_units=1,3,6

CREATE TABLE id(
  family_uid TEXT PRIMARY KEY,          -- ACN-000137
  patient_id TEXT,                      -- 项目/流水线编号（如 habitat `1-CAO-YAN`）；缺则 NULL，不含 PHI
  subject_label TEXT,                   -- 【v2 兼容列】迁移时原值原样留档，**不再写新值**；不是键、不保证唯一。
                                        -- 姓名在 name、号码在 id_number / *_no 列，不许只靠本列承载。
  name       TEXT,                      -- 允许 NULL；中文姓名或已脱敏形态
  name_pinyin TEXT,                     -- 大写、单空格分隔；「序号-姓名」串的序号进 patient_id
  sex        TEXT,                      -- M/F/U
  birth_date TEXT,                      -- ISO 或 YYYY-MM；与年龄互校
  id_card    TEXT,                      -- 脱敏：前 3 + 11* + 后 4，不存明文
  phone      TEXT,                      -- 脱敏：前 3 + **** + 后 4
  address    TEXT,                      -- 只到区/县
  inpatient_no TEXT,
  outpatient_no TEXT,
  mrn        TEXT,
  imaging_no TEXT,
  pathology_no TEXT,
  sample_no  TEXT,
  study_uid  TEXT,                      -- DICOM StudyInstanceUID 等
  misc_id    TEXT,                      -- 杂号；更多号进 id_number(kind='other')
  id_status  TEXT NOT NULL,             -- keyed_full | keyed_partial | keyed_animal | label_only | label_only_omics
  identity_state TEXT,                  -- resolved | unresolved（逐人；无依据则 NULL）
  source     TEXT,                      -- 本条身份的来源登记（blob: 或表名 + 日期）
  created_on TEXT NOT NULL,
  updated_on TEXT,
  note       TEXT,
  CHECK (family_uid GLOB '[A-Z0-9_]*-[0-9][0-9][0-9][0-9][0-9][0-9]'),
  CHECK (id_status IN ('keyed_full','keyed_partial','keyed_animal','label_only','label_only_omics'))
);
CREATE UNIQUE INDEX ux_id_card  ON id(id_card)  WHERE id_card  IS NOT NULL;
CREATE UNIQUE INDEX ux_id_phone ON id(phone)    WHERE phone    IS NOT NULL;

CREATE TABLE id_number(
  family_uid TEXT NOT NULL REFERENCES id(family_uid) ON DELETE CASCADE,
  kind       TEXT NOT NULL,   -- mrn | inpatient | outpatient | imaging | pathology | sample | study | other
  value      TEXT NOT NULL,
  issuer     TEXT,            -- 医院/科室/系统
  valid_from TEXT, valid_to TEXT,
  note       TEXT,
  PRIMARY KEY (family_uid, kind, value)
);
CREATE INDEX ix_idnum_value ON id_number(value);

-- （dict 表已剔除：契 3 目标态家族库不留 dict，词典读 Family\dict.db）
-- 约束：role='phi' 只允许 scope='family'

CREATE TABLE ref_project(           -- 家族库登记子库，跨库校验的清单来源
  slug        TEXT PRIMARY KEY,
  db_rel_path TEXT NOT NULL,        -- 相对 _DataBase 的路径
  person_count INTEGER,             -- person 表行数（含占位，**不是队列人数**）
  clin_person_n INTEGER,            -- 有 ≥1 条 clin 行的人数 = 队列人数口径（报告只报这个）
  longitudinal_n INTEGER,           -- 镜像自子库：有 ≥2 个不同 period_month 的人数（0 = 纯横断面）
  multi_record_n INTEGER,           -- 镜像自子库：>1 条 clin 行的人数（不论有无日期）
  period_unit INTEGER,              -- 镜像自子库 meta；NULL = 该项目无时间轴；∈{1,3,6}，整数不放文本
  built_at    TEXT,
  sha256      TEXT
);

CREATE TABLE ref_study(             -- 研究目的登记：结局不是数据属性，是研究目的的函数
  study_code   TEXT PRIMARY KEY,    -- 'AITEE-2027-01'
  question     TEXT NOT NULL,       -- 这个研究问什么
  outcome_definition TEXT,          -- 结局定义/起点/时间单位；未定义写 'not_defined'
  outcome_status TEXT NOT NULL DEFAULT 'not_collected',  -- not_collected | collected
  harmonization TEXT,               -- none | combat | zscore_within_batch
  harmonization_fit_on TEXT,        -- train（ComBat 只许在训练队列 fit，验证队列 apply）
  registered_on TEXT NOT NULL,
  note TEXT,
  CHECK (outcome_status IN ('not_collected','collected'))
);

CREATE TABLE ref_batch(             -- 影像批次 = 设备 + 重建参数集（D59–D61）
  batch_id     TEXT PRIMARY KEY,    -- 与子库 slug 同名：aitee_ge_discovery
  vendor       TEXT,
  model        TEXT,
  scanner_software TEXT,            -- 同型号软件大版本变更 = 新批次
  field_strength TEXT,              -- MR：1.5T / 3.0T
  kvp          TEXT,                -- CT
  kernel       TEXT,                -- CT 重建核
  slice_thickness TEXT,
  pixel_spacing TEXT,
  site         TEXT,
  n_series     INTEGER,
  n_patients   INTEGER,
  date_from    TEXT,
  date_to      TEXT,
  extraction_params TEXT,           -- JSON：bin_width / resample_mm / roi_strategy / software+version / binarize
  extraction_sha256 TEXT,           -- Params.yaml（或结果表）的 sha256，D60 逐批比对
  note         TEXT
);

-- @section project
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- schema_version=5t/1, kind=project, family=ACN, slug=lxj_ACN, built_at, rule=...,
-- period_unit=6（整数语义；k/v 里只能落文本，读侧一律 int() 后比较）,
--   无时间轴的项目 **不写该键**（v 是 NOT NULL，写不进 NULL）→ 缺键即 NULL，
--   同时写 period_unit_missing_reason 说明理由，见 §3, dict_owner=..\_PHI\ACNdb, dict_sha256=..., source_db=旧库, source_sha256=...

CREATE TABLE src(                    -- 溯源
  src_id   INTEGER PRIMARY KEY,
  kind     TEXT NOT NULL,            -- old_db_blob | workbook | archive_member | excluded
  status   TEXT NOT NULL DEFAULT 'active',  -- active | excluded | duplicate
                                     -- excluded=病种/范围剔除（如 EC 的骨肿瘤表），duplicate=重复副本
                                     -- 剔除不删行：src 留痕 + note 写理由，下游查询默认 WHERE status='active'
  rel_path TEXT NOT NULL,
  sha256   TEXT,
  rows     INTEGER,
  note     TEXT
);

CREATE TABLE person(                 -- 无 PHI 占位表
  family_uid   TEXT PRIMARY KEY,
  subject_label TEXT,
  id_status    TEXT,
  visit_n      INTEGER,             -- 该子工程内的时点数
  first_month  TEXT,                -- 最早 period_month（UNK 不计）
  last_month   TEXT,
  note         TEXT
);

CREATE TABLE clin(                   -- v2：一人一时点一行；头 11 列按名按序（D87 指纹校验）
  family_uid    TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  observed_on   TEXT,               -- 精确日期；同月多值取最早，全量留 qc 明细
  period_month  TEXT NOT NULL,      -- 'YYYY-MM' 或 'UNK'
  cohort        TEXT,               -- 多队列项目必填（EC: 'S' 手术/'A' 入院）；单队列留 NULL
                                    -- 枚举必须登记 dict(role='key')，且 meta.cohort_split 同步写人话
  group_v       TEXT,               -- 分组变量：0=对照/1=实验，或 DC/PD 等；枚举登记 dict
  subgroup      TEXT,               -- 亚组；无则 NULL
  src_id        INTEGER REFERENCES src(src_id),
  src_row       INTEGER,            -- 原表行号，溯源/改名/重号回溯用
  merged_n      INTEGER NOT NULL DEFAULT 1,   -- 该行由几条来源记录合并而来
  unk_seq       INTEGER NOT NULL DEFAULT 0,  -- 仅 UNK 行使用：同一人第 2、3 条无日期记录
  schema_block  TEXT DEFAULT 'clin-v2',      -- 块标识：本表恒 'clin-v2'
  -- —— 以下 3 列是 v1 头遗留的时点精度三元组，降为变量列保留（保住 period_origin 这类既有证据）——
  -- —— 新项目不再写这三列；NULL 即「未记录」——
  month_ix      INTEGER,            -- year*12+month-1；UNK 时为 NULL
  period_source TEXT,               -- 精度：exact | approx | unknown（v1 头为 NOT NULL，v2 放开）
  period_origin TEXT,               -- 事件来源：exam_date | surgery_date | admission_date | report_date | derived | unknown
                                    -- 与 period_source 分工：source 答「这个月有多准」，origin 答「这个月是哪件事的」
                                    -- 实例：EC 的 S 队列=surgery_date、A 队列=admission_date；BT/影像项目=exam_date
  -- ……项目变量列（由原表表头生成，逐列登记 dict；变量区小写缩写、按字母序）……
  -- ……病理段（由病理原文拆分而来；原文与扫描件不入库，靠 src_id+src_row 回溯）……
  -- ihc_<marker> / ihc_panel_raw 待病理拆列时 ALTER ADD 到表尾（marker 动态，不固定在此）
  path_kind      TEXT,   -- 病理类型：surgery | curettage | biopsy | consult | other
  path_on        DATE,   -- 病理报告日期
  path_histology TEXT,   -- 组织学类型（清洗后标准词）
  path_grade     TEXT,   -- 病理分级（FIGO grade 1/2/3）
  path_mi        TEXT,   -- 肌层浸润：none | lt50 | ge50 | full | unk
  path_mi_mm     INTEGER,-- 肌层浸润深度 mm（原文给出数值时）
  path_lvsi      TEXT,   -- 脉管癌栓：0 | 1 | unk
  path_csi       TEXT,   -- 宫颈间质受累：0 | 1 | unk
  path_ln_pos    INTEGER,-- 阳性淋巴结枚数
  path_ln_total  INTEGER,-- 检出淋巴结总枚数
  path_lnm       TEXT,   -- 淋巴结转移：0 | 1 | unk
  path_eu        TEXT,   -- 宫外/附件受累：0 | 1 | unk
  path_figo      TEXT,   -- 病理 FIGO 分期（规范化后）
  path_dx        TEXT,   -- 诊断结论句（≤1000 字；超长截断留 qc）
  path_other     TEXT,   -- 残余病理要点（≤200 字）
  PRIMARY KEY (family_uid, period_month, unk_seq),
  CHECK (period_month='UNK' OR period_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
  CHECK (period_source IN ('exact','approx','unknown')),
  CHECK (period_origin IS NULL OR period_origin IN ('exam_date','surgery_date','admission_date','report_date','derived','unknown')),
  CHECK (period_month<>'UNK' OR (month_ix IS NULL AND period_source='unknown')),
  CHECK (period_month='UNK' OR month_ix IS NOT NULL),
  CHECK (period_month<>'UNK' OR unk_seq >= 0),
  CHECK (period_month='UNK' OR unk_seq = 0)
);
CREATE UNIQUE INDEX ux_clin_month ON clin(family_uid, period_month) WHERE period_month<>'UNK';
CREATE INDEX ix_clin_month ON clin(month_ix);

-- ---------- mod: radiomics ----------
CREATE TABLE _modules_radiomics(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_radiomics_month ON _modules_radiomics(family_uid, month_ix);

CREATE TABLE radiomics_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_radiomics(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: us ----------
CREATE TABLE _modules_us(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_us_month ON _modules_us(family_uid, month_ix);

CREATE TABLE us_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_us(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: mri ----------
CREATE TABLE _modules_mri(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_mri_month ON _modules_mri(family_uid, month_ix);

CREATE TABLE mri_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_mri(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: ct ----------
CREATE TABLE _modules_ct(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_ct_month ON _modules_ct(family_uid, month_ix);

CREATE TABLE ct_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_ct(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: pet ----------
CREATE TABLE _modules_pet(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_pet_month ON _modules_pet(family_uid, month_ix);

CREATE TABLE pet_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_pet(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: path ----------
CREATE TABLE _modules_path(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_path_month ON _modules_path(family_uid, month_ix);

CREATE TABLE path_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_path(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。


-- ---------- mod: report ----------
CREATE TABLE _modules_report(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
  exam_uid     TEXT PRIMARY KEY,     -- <MOD>-<YYYYMMDD>-<n>；无日期时 <MOD>-UNK-<n>
  family_uid   TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month TEXT, month_ix INTEGER,
  observed_on  TEXT,
  device TEXT, site TEXT, sequence TEXT, roi_n INTEGER,
  batch_id TEXT,                    -- 影像模态必填（D59）：指向家族库 ref_batch；非影像模态留 NULL
  src_id INTEGER REFERENCES src(src_id),
  src_row INTEGER,
  note TEXT
);
CREATE INDEX ix_modal_report_month ON _modules_report(family_uid, month_ix);

CREATE TABLE report_value(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES _modules_report(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_value  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_value      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_value      entity='global'；feature=代谢物名（59 列）
--   radi_value       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（_modules_radiomics ↔ radiomics_value），不重名。

-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_value.entity 的取值是 ROI 名，lipid_value.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_value.feature 了事。

CREATE TABLE qc(
  qc_id        INTEGER PRIMARY KEY,
  period_month TEXT,                -- 空 = 库级/结构级门禁
  scope        TEXT NOT NULL,       -- schema | clin | modal | person
  table_name   TEXT, column_name TEXT,
  code         TEXT NOT NULL,       -- MISSING | OUTLIER | UNIT | DATE_INVALID | DUP | CONFLICT | PHI | FK
                                     -- 结构/溯源：ROW_DROPPED | HEADER_LOST | PHI_COLUMN | SOURCE_BLOB_ONLY
                                     -- 身份：ID_REUSED | NAME_CONFLICT | NAME_LINK | ID_ATTRIB_UNSURE
                                     -- 编码：ENUM_UNMAPPED | ENUM_REMAP | GROUP_INFERRED | VALUE_NORMALIZED
                                     -- 影像：DICOM_NO_DATE | DICOM_ID_CONFLICT | DUP_SERIES | MIXED_MODALITY_IN_STUDY
                                     --      PIXEL_PHI_RISK | ANON_NAME | PRIVATE_TAGS_IGNORED | BATCH_SMALL
                                     --      BATCH_CONFOUNDED | EXTRACTION_PARAM_MISMATCH | ROI_MISSING
  severity     TEXT NOT NULL,       -- P0 | P1 | P2
  status       TEXT NOT NULL,       -- open | fixed | waived
  detail       TEXT,
  decided_by   TEXT, decided_on TEXT,
  CHECK (severity IN ('P0','P1','P2'))
);
