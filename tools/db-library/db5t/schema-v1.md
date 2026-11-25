# 五表骨架 v1（1+N 文件结构）— 设计草案

状态：草案 v1.3，2026-11-24。依据用户拍板：一子工程一库、扁平落点、id 家族级唯一、保留无 PHI 的 person 占位表、时点以采集月为原子（栅格 1/3/6 月可选）、同月多次采集在 clin 聚合为一行、无日期记 `UNK`。

v1.3 修正（据 21 库日期普查，census\CENSUS-REPORT.md）：① clin 主键加 `unk_seq`——UNK 行不能按 `(family_uid,'UNK')` 聚合，否则同一人多条无日期记录会被压成一条；② 见 §7，普查推翻了「多时点普遍存在」这个前提。

v1.2 修正（据 Aitor 草案复核）：① 组学长表改名，消掉 `modal_radiomics_exam` 未定义的自引用；② 补旧库与新家族库**同名撞车**的并存方案；③ 记明 verify_db 实际只实现 D1–D9 / D19–D22，D10–D18 是人工注记码，新码从 D23 起编。

v1.4 增补（影像库 AITEE，据用户「按设备切库 + 考虑 ComBat + 组学在范围内」）：① 家族库加 `ref_study`（研究目的与结局状态，D58）与 `ref_batch`（批次 = 设备 + 重建参数集，D59–D61）；② `modal_<mod>` 加 `batch_id`，影像模态必填，扫描参数只落 `ref_batch` 不落子库；③ `qc.code` 补影像专有码；④ 组学 `radi_val.entity` 用 ROI 名、无 ROI 轴填 `'global'`。**建库前必须重读本节：v1.3 的 DDL 不含这三张影像表。**

## 0. 文件布局

```text
_DataBase\<family>\
  _db\<family>db                 家族库：meta / id / id_number / dict / ref_project   ← 唯一 PHI 落点
  _db\<family>db.raw6t           迁移期旧库（core-6t 冻结只读），验收后移走
  <slug>\<slug>db                子工程库：meta / src / person / clin / modal_<mod> / qc  ← 零 PHI
  <slug>\目录说明.md
  _share\                        原始档（不动）
```

- 库文件无扩展名，与现行 `<family>db` 同风格。
- **同名撞车**：新家族库沿用 `<family>db` 这个名字，旧库在切换日**改名** `<family>db.raw6t`（不是删除、不是移动目录）。新库先在 `{DEL}` 批次目录里 stage 建好、跑完门禁，再一次性换入。换入当天即旧库改名日，需当轮点名授权。
- 家族库与子库各自独立可 VACUUM、可单独备份、可单独外发（子库外发不需要脱敏处理）。
- 跨库只有一个引用：`family_uid`。SQLite 无跨库外键 → 由 `verify_db` 以 `ATTACH` 校验。

## 1. 家族库 DDL

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- schema_version=5t/1, kind=family, family=ACN, built_at, rule=..., source_db=<旧库相对路径>,
-- source_sha256=..., period_units=1,3,6

CREATE TABLE id(
  family_uid TEXT PRIMARY KEY,          -- ACN-000137
  name       TEXT,                      -- 允许 NULL
  sex        TEXT,                      -- M/F/U
  birth_date TEXT,                      -- ISO 或 NULL；与年龄互校
  id_card    TEXT,
  phone      TEXT,
  address    TEXT,
  id_status  TEXT NOT NULL,             -- keyed_full | keyed_partial | keyed_animal | label_only | label_only_omics
  subject_label TEXT,                   -- 原始院号/样本号占位
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
  kind       TEXT NOT NULL,   -- mrn | inpatient | outpatient | imaging | pathology | sample | other
  value      TEXT NOT NULL,
  issuer     TEXT,            -- 医院/科室/系统
  valid_from TEXT, valid_to TEXT,
  note       TEXT,
  PRIMARY KEY (family_uid, kind, value)
);
CREATE INDEX ix_idnum_value ON id_number(value);

CREATE TABLE dict(
  scope       TEXT NOT NULL,        -- 'family' | 'proj:<slug>'
  table_name  TEXT NOT NULL,
  column_name TEXT NOT NULL,
  role        TEXT NOT NULL,        -- key | phi | quasi | outcome | feature | covariate | derived | text | aux
  dtype       TEXT,                 -- int | real | text | date | enum | bool（必须按值推断，不许一律 text）
  aliases     TEXT,                 -- 合并进本列的全部源表头，'|' 分隔（EC: 'CA125 | old-CA125'）
  unit        TEXT,
  source      TEXT,                 -- 旧 item.name / 原表列名
  missing_rule TEXT,                -- 空值语义：未采集/未测/不适用
  description TEXT,
  added_on    TEXT NOT NULL,
  PRIMARY KEY (scope, table_name, column_name),
  CHECK (role IN ('key','phi','quasi','outcome','feature','covariate','derived','text','aux'))
);
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
```

`dict` 落在家族库、用 `scope` 区分家族级与子库级（用户拍板 dict 放家族目录）。代价：子库单独打开时词典不可见，靠 `meta.dict_owner` + ATTACH。

## 2. 子工程库 DDL

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- schema_version=5t/1, kind=project, family=ACN, slug=lxj_ACN, built_at, rule=...,
-- period_unit=6（整数语义；k/v 里只能落文本，读侧一律 int() 后比较）,
--   无时间轴的项目 **不写该键**（v 是 NOT NULL，写不进 NULL）→ 缺键即 NULL，
--   同时写 period_unit_missing_reason 说明理由，见 §3, dict_owner=..\_db\ACNdb, dict_sha256=..., source_db=旧库, source_sha256=...

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

CREATE TABLE clin(                   -- 一人一时点一行；同月多次采集在迁移期合并
  family_uid    TEXT NOT NULL REFERENCES person(family_uid) ON DELETE CASCADE,
  period_month  TEXT NOT NULL,      -- 'YYYY-MM' 或 'UNK'
  month_ix      INTEGER,            -- year*12+month-1；UNK 时为 NULL
  period_source TEXT NOT NULL,      -- 精度：exact | approx | unknown
  period_origin TEXT,               -- 事件来源：exam_date | surgery_date | admission_date | report_date | derived | unknown
                                    -- 与 period_source 分工：source 答「这个月有多准」，origin 答「这个月是哪件事的」
                                    -- 实例：EC 的 S 队列=surgery_date、A 队列=admission_date；BT/影像项目=exam_date
  unk_seq       INTEGER NOT NULL DEFAULT 0,  -- 仅 UNK 行使用：同一人第 2、3 条无日期记录
  observed_on   TEXT,               -- 精确日期；同月多值取最早，全量留 qc 明细
  merged_n      INTEGER NOT NULL DEFAULT 1,   -- 该行由几条来源记录合并而来
  src_id        INTEGER REFERENCES src(src_id),
  src_row       INTEGER,            -- 原表行号，溯源/改名/重号回溯用
  cohort        TEXT,               -- 多队列项目必填（EC: 'S' 手术/'A' 入院）；单队列留 NULL
                                    -- 枚举必须登记 dict(role='key')，且 meta.cohort_split 同步写人话
  -- ……项目变量列（由原表表头生成，逐列登记 dict）……
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

CREATE TABLE modal_<mod>(            -- 检查级：mod = radiomics | us | mri | ct | pet | path | report
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
CREATE INDEX ix_modal_month ON modal_<mod>(family_uid, month_ix);

CREATE TABLE <mod>_val(              -- 通用组学长表：不建 1300 列宽表
  exam_uid TEXT NOT NULL REFERENCES modal_<mod>(exam_uid) ON DELETE CASCADE,
  entity   TEXT NOT NULL,            -- 该模态矩阵的分组轴；无分组轴时填 'global'
  feature  TEXT NOT NULL,            -- 该模态的测量项名称
  value    REAL,
  unit     TEXT,
  PRIMARY KEY (exam_uid, entity, feature)
);
-- 实例：
--   radiomics_val  entity=ROI 名（列名 `指标__ROI` 的 `__` 之后）；无 ROI 的全局指标 entity='global'
--   lipid_val      entity='global' 或脂质大类；feature=脂质种名（783 列）
--   metab_val      entity='global'；feature=代谢物名（59 列）
--   radi_val       entity='global'；feature=影像特征名（109 列）
-- 注：exam 级表名与长表名成对（modal_radiomics ↔ radiomics_val），不重名。

-- 一张模态一张 val 表，不共用：每个模态必须有自己的一对 modal_<mod> + <mod>_val，
-- entity 的语义与 feature 的含义逐模态写进 dict。
-- 理由：radiomics_val.entity 的取值是 ROI 名，lipid_val.feature 的取值是脂质种名——
-- 两者形状相同但语义不同；硬塞一张表会让「按 ROI 汇总」在脂质数据上静默返回空表，
-- 或更糟：把脂质名当成 ROI 混进统计。禁止把物种名塞进 radiomics_val.feature 了事。

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
```

## 3. 时点规则（月为原子，clin 聚合）

- 存储原子 = `period_month`（`YYYY-MM`）+ `month_ix`（整数，排序/窗口用）。
- 栅格由 `meta.period_unit ∈ {1,3,6}` 声明，派生不落盘：`bin_ix = month_ix // period_unit`（SQLite 整除）。视图里给 `bin_start_month`。
- **`period_unit = NULL` 表示该项目没有时间轴**（不是「还没定」）。这是**唯一标记**，不再另设 `no_time_axis` 开关——两个标记必然漂移。局面翻转靠镜像：子库 `meta.period_unit` 是真相，家族库 `ref_project.period_unit` 是副本（与 `person_count`/`sha256` 同一套镜像模式），两者不一致由门禁报 P0。此时该库 `clin` 的每一行必须是 `period_month='UNK'`。把无时点的项目写成 `period_unit=6` 是伪造结构：六个月栅格一天都不存在，却会让下游以为可以按窗取值。MCI `tyt_SCD` 就属于这一类（230 行全 UNK，0 个日期）。
- 落盘编码：`ref_project.period_unit` 是 **INTEGER**，不放文本；子库 `meta` 是 k/v 且 `v NOT NULL`，**无时间轴 = 不写该键**（缺键即 NULL），另写 `period_unit_missing_reason` 记理由。镜像门一律 `int(float(v))` 后比较——试点实测三家曾全部存成文本 `'6'`，`'6' != 6` 会让镜像门假失败。
- 第三种情形：**库里确有日期，但逐人归组未定**（LC `qrx_LUNG_his`：808 行中 253 行有日期，`identity_state='unresolved'`，每人只剩 1 行，连不成序列）。此时 `period_unit` 照常声明（LC 写 6 = 「将来恢复纵向时的粒度政策」），同时必须写 qc `LONGITUDINAL_UNAVAILABLE`(P2) 并保持 `identity_state='unresolved'`。理由：`NULL` 只能表示「库里没有时间」；拿它表示「有时间但连不成序列」，会让下游把数据缺失与待人工裁决混为一谈——前者无解，后者有 `identity_candidates.csv` 这条可回滚路径。
- `observed_on` 保留精确日期；`period_month` 由它截断到月，不一致由 D30 报错。
- **同月多次采集**：`clin` 只留一行，`PRIMARY KEY (family_uid, period_month)`；迁移期按来源时间序合并——后到的非空值覆盖先到的空值，两个非空值不同则保留先到值、冲突写 `qc(code='CONFLICT')`，`merged_n` 记合并条数，`observed_on` 取当月最早。`modal_*` 不聚合，保留 `exam_uid` 级明细。
- **无日期**：`period_month='UNK'`、`month_ix=NULL`、`period_source='unknown'`；排序用 `COALESCE(month_ix, 999999)`。`UNK` 行不参与时间序列统计，只能进横断面分析，且必须能在 `qc` 里查到原因。
- **`period_source` 与 `period_origin` 分工**（v1.4 增列，起因：BT 的日期本质是「影像检查时间」，写进 `period_source='exact'` 就只能靠 `meta` 猜）：`period_source` 答**这个月有多准**（`exact|approx|unknown`），`period_origin` 答**这个月是哪件事的**（`exam_date|surgery_date|admission_date|report_date|derived|unknown`，UNK 行可为 NULL）。两者不许合并成一个枚举——「影像检查的准确日期」与「手术的准确日期」对 `生存起点` 的意义完全不同，合并后 `period_source='exact'` 这一格在 BT 与 EC 里指两件事，下游按它算随访间隔必然混。已知取值：BT/`ybc_EC_img`=`exam_date`；EC `S` 队列=`surgery_date`、`A` 队列=`admission_date`；STWZ=`surgery_date`；相对随访月换算=`derived`。
- clin 有行 = 该时点采集过；列 NULL = 采了但缺；无行 = 未采集。三者不可混。

## 4. 旧库映射（core-6t → 5t）

| 旧 | 新 | 说明 |
|---|---|---|
| `person.person_id` | `id.family_uid` | 需人工/规则建映射；`person` 表为空的库要从行级 PHI 反推 |
| `item.name` | `dict.column_name`（clin 列名）或 `radiomics_val.feature` | 组学类按长表，不进 clin |
| `val.num/text` | `clin.<col>` / `radiomics_val.value` | 同 person 同月多行 → 合并 + `qc CONFLICT` |
| `val.row_ix` + 日期 item | `period_month` | 日期缺失 → `UNK` |
| `src` | `src`（同名沿用） | 保留 sha256 溯源 |
| 旧 `qc` 工作表 | 仅作证据，不导入 | 新 qc 由机器门 + 人工裁决产生 |
| 旧 `doc/sheet/entity/fact` 视图 | 不迁移 | 兼容层随旧库冻结 |

## 5. 迁移顺序

**源优先级：原文件 > `src.blob` 快照 > curated 表。** 旧库 `src.blob` 不是缩略图，而是每张 sheet 的 zlib+TSV 全量快照（含表头行），连 `is_wide=1`、EAV 跳过的宽表都在。也就是说 curated 表丢失的、loader 跳过的宽表，都能从 blob 逐格重建——curated 只在原始 sheet 也无法还原时才作为最后手段，且必须逐格核对重叠部分。凡内容仅存于 blob 的表，在 `src` 登记 `kind='old_db_blob'`（附 blob 的 sha256），并在 `qc` 留 `SOURCE_BLOB_ONLY`(P2) 一条说明：原文件不可再取，重建来源是旧库内的快照。

1. 家族库先建（在 `{DEL}` 批次目录 stage）：`id` + `id_number` 从旧 `person/person_key` 与行级 PHI 项（姓名/身份证/电话/地址/各种号）抽取。
2. 子库逐个建：先 `person` 占位（family_uid 关联），再 `clin`，再 `modal_*`。
3. 门禁全 PASS 后切换日：旧库改名 `<family>db.raw6t`，staged 库换入 `<family>db`；旧库 attrib +R 冻结，`meta.source_sha256` 记录其 sha256；`ATTACH` 对账 6 项。
4. 对账通过后才允许把 `.raw6t` 移入 `{DEL}`（用户口径：冻结并存，验收后再谈 10 年归档）。

## 6. 阻塞项（落 RULES 或动库之前必须解决）

- [ ] **S28 会崩**：`structure_check.py:1187-1191` 查 `doc/project/sheet`，5t 家族库没有这些表 → 可能整条检查抛 `no such table` 而非单条 fail；S31（`:1351`）只统计 `_db\<family>db`，子库不可见。必须与建库同批改。
- [ ] **D21 与 D28 判级冲突**：D21 现为「明文 PHI 仅 P2」，子库零 PHI 需 P0；执行时容易被 D21 当免死金牌。
- [ ] verify_db 实际只有 D1–D9 / D19–D22，D10–D18 是人工注记码；新码从 D23 起编，不与旧号复用。
- [ ] 子库是否需要自己的 `dict` 只读副本（外发自描述）。
- [ ] `exam_uid` 生成规则：同一人同一天两次检查的 `n` 如何稳定分配（按扫描时间还是按来源行序）。
- [ ] `person` 表为空的 4 个库（OC / RDL / PHT / SSD）如何从「姓名存疑 / 姓名(crosswalk)」多版本列定人。
- [ ] `_share` 是否收子库归档（现在 `_share` 只收原始件，收库会打乱「原始件池」语义）。

## 7. 普查结论对设计的修订（21 库全量日期普查，2,744,042 行）

普查推翻了「历史数据普遍有多时点」这个前提，三条事实：

1. **person_id 不是没填，而是只填主表**：`val.person_id` 空率 **95.2%**（2,613,102/2,744,042）；23,403 条日期行里仅 **707 条**（3.0%）带 person_id。所以「日期没挂到人」是结构性的，不是脏数据。
2. **月轴只在 7 个项目里成立**：1 月栅格 **0 个项目**；3 月栅格仅 EC `ybc_EC_his`（114 人，中位间隔 108 天）与 EGVB `ybc_STWZ_his`（26 人，111 天）；6 月栅格 5 个项目（LC、MCI、PCOS、RDL、EC 主表）。**57/64 项目只能 UNK**。
3. **能救人，靠 ID 列不靠 person_id**：改用 影像号/住院号/门诊号 聚类，EC 得 3,346 人、EGVB 171、LC 221、RDL 310；person=0 的库里 OC 139、RDL 313、PCOS 116、HZ 91、SSD 7、PHT 13；**H / NAFLD / CC 既无 person 也无 ID/姓名列，当前无法重建**。

由此定四条：

- `period_unit` 缺省 **6**，不是 3。建库时按项目实测定值写进 `meta.period_unit`，不按愿望填。
- clin 绝大多数行是 `period_month='UNK'`。这不是失败，是现状；`month_ix=NULL` 的行不参与时间序列，只进横断面。任何按时间分箱的分析在执行前必须打印 UNK 占比。
- `id_number` 不是「附赠字段」，**它是重建身份的主键来源**——没有它，95.2% 的 val 行无法归属到人。这条要把优先级提到 id 表之上：先聚类出人，再建 id。
- 新增两个 qc 码：`HEADER_LOST`（`#n` 列名 166 列 / 38,696 行，MHE_AN 29 列 21,649 行、OTHERS 33 列 9,636 行、H 28 列 1,871 行）；`PHI_COLUMN`（列名即患者姓名，EC 53 / MHE_AN 84 / EGVB 34 / LC 29）。后者是 P0：这些列**不许**进 clin，必须转成 `id_number` 行。

另记两条实现坑：EC `图像时间` 有 2,954 条 Excel 序列号（43354–45360），同一列混排 `Y-M-D H:M:S` / `Y-M-D` / 序列号三种形态，只按 text 解析会整列丢日期；英文列名含 `Cau-date` / `runtime` / `updated` 会被 `date` 子串规则误匹配，日期识别必须用词边界 `(?<![a-z])date(?![a-z])`。

## 8. 全局硬规


**8.1 占位人发号从 900001 起。** 无**患者级**身份键的记录（既无 person_id、也无院号/影像号/门诊号/病理号、也无姓名）仍要进库，但其 `family_uid` 一律落在 `<FAMILY>-9xxxxx`（≥900000）段，`id_status='label_only'`，`person.note='placeholder: no patient-level key'`，`meta.person_placeholder_n` 记条数。**样本号/流水号/细胞编号不是患者身份**——这类记录同样落 9 段，号码写进 `id_number(kind='sample', value=编号, note='样本级命名空间，未与患者关联')`，不许占用真实 uid 段（否则「LC 有多少受试者」会被 322 条单细胞记录顶成 494）。任何按人统计必须显式写 `family_uid < 'FAMILY-9'` 才能排除占位。真实受试者永不发到 9 段。

**`label_only_omics` 是第四种占位**：为承接「只存在于组学表、在 id 侧没有任何可归属键」的标签而建（LC 411 条：lipid 51 + metab 141 个键）。它同样落 9 段，但单独成一个 `id_status` 值，因为它的性质与「源表里就没有身份」的那些行不同——这些标签将来若有 crosswalk 是可以接回真人的。两条硬口径：① 不进任何队列 n，报告只报 matched（有 clin 的人数 / 有日期的人数），`id` 行数本身永远不是样本量；② `id.note` 必须记来源 sheet 与行号，`qc(OMICS_KEY_UNMATCHED, P2)` 记总数。

**8.2 姓名只存一份，且不进 `id_number`。** 姓名只许出现在 `id.name`；`id_number` 只收各类号码（mrn/inpatient/outpatient/imaging/pathology/sample/other），**不收姓名**——否则同一 PHI 在同一库里有两份副本，改一处漏一处。姓名唯一性靠 `qc(code='DUP')` 提示，不靠唯一约束（同名不同人客观存在）。
- **姓名的写法判据：非空、非纯数字、含至少一个字母或汉字、长度 ≤ 40。** 实测反例：OTHERS 的 `name` 列里有 `14/27/38/60` 四个纯数字，来源是表头错位后被顶进 name 列的值；若不拦，它们会被 `id_card`/`id_number` 的号码域当成真人，也会让「按姓名连人」把不同人错并。纯数字一律不写 `id.name`（留 `qc(NAME_INVALID, P2)` 并保 `src` 原值），这是 D41「姓名不是键」的前置：连是不是姓名都要先判。

**8.3 一列里混了两套编码时，拆 `_raw` / `_canon`，不许只留一列。** 例：MCI 的 `group_code` 同时出现 `0/1/2` 与 `HC/NC/SCD/MCI` 两套写法。做法是 `group_raw`（原样字符串，永不改）+ `group_canon`（规范值，映射不出来的留 NULL）+ `dict` 里登记 enum 映射表 + `qc(code='ENUM_UNMAPPED')` 报未映射条数。只留一列会让 `GROUP BY` 把人分成 7 组（2 套编码相加），而这种错在结果表里看不出异常。判据：同一列的不同写法**含义是否等价**未知时，一律按混编码处理，不许猜。

**8.4 qc 的 scope/table/column 必须指向真正的落点，不许借位。** 规范：列级缺陷 → `table_name`+`column_name` 都填；行级/表级缺陷（如整行剔除）→ `table_name` 填，`column_name=NULL`，码用 `ROW_DROPPED` 之类；库级缺陷（如跨项目无共同键）→ `scope='family'`，`table_name`/`column_name` 皆 NULL。反例：把「4 行表尾被剔除」记成 `HEADER_LOST` 且 `scope='src', column='family_uid'`——码说的是一件事，落点指的是另一件事，半年后没人能据此复现。`HEADER_LOST` 只用于表头丢失（`#n` 列名），`ROW_DROPPED` 只用于整行剔除，两者不互借。

**8.5 身份不可得也是一种合法状态，但必须显式声明。** 用户定：H / NAFLD / CC 三家既无 person、也无任何号列或姓名列，**照建**。落法：家族库 `id` 表 0 行；该家族全部 clin 行落 9 段占位；`meta.identity_state='unavailable'`；必写 `qc(IDENTITY_UNAVAILABLE, P2)` 记明「源库无任何患者级标识列」；项目报告与小节标题一律注明「身份不可得」，不许只写 `clin_person_n` 不加限定。`clin_person_n` 照常统计（占位行也是行），但**不得**被任何下游当作可回溯到人的队列数——这类库只能做变量分布与影像特征分析，不能做跨库合并。与 LC 那种「有身份线索但未解析」写 `identity_state='unresolved'` 不可混用：`unresolved` 有 `identity_candidates.csv` 这条可回滚路径，`unavailable` 没有。

**8.6 `exam_uid` 标识「模态表的一行」，语义由该模态源表决定。** 影像组学表一行 = 一个病灶的 ROI/特征集合；流式/代谢表一行 = 一次检测。**禁止统一解释成「一次检查/一次随访」**——否则同一受试者多病灶会被当成多次随访，横断面数据会被算成纵向。实测：LC `target` 495 行 / 189 个姓名，每名行数分布 `{2:159, 4:1, 5:1, 6:28}`，**没有一个人只有 1 行**；若把每行读成一次检查，这 189 人会被算成 495 次就诊。这是 LC `longitudinal_n` 必须为 0 的原因之一，也是全量时最容易犯的错。

**8.7 判重键里不许出现 `name`（墓碑规则）。** 实测事故：LC 占位人 `LC-909215` 由两个不同患者共用姓名而并成一条，判重键是 `(模态标签, 归一化姓名)`。并完之后**看起来完全正常**——`name` 本来就空、`subject_label` 一律写 uid、`exam` 数不变；只少了一行 `person`，患者名单里少一个人。只有两种查询能暴露：`Σperson ≠ id`，或「一个占位挂 2 个 exam」。撤回后该家族 `id` 3,050 = Σperson 3,050。**凡缓存 key 或去重键中出现 `name`，一律当 bug 处理**；同名多 uid 必须全部保留（三库实测 293 组同名多 uid 全部保留为独立身份）。

**8.8 一个变量只能有一列；多源表合并靠「规范列映射 + 值级合并」，不靠列名加后缀。** 实测事故：EC 首次重建把 10 张临床表按宽表 join，pandas 给同名列自动加 `_2…_10` 后缀，产出 **1,699 列、其中 491 个变量族带后缀副本共 1,137 个冗余列**（`ca125`/`ca125_2`…`ca125_5` 五列同值 12.5），并且同一人裂成两行——一行 312 个非空列、另一行 1,249 个非空列，两行非空列集只交 8 个系统列。后果：任何 `SELECT ca125` 只读到 10% 的值，`COUNT(DISTINCT …)` 静默偏低，而表看起来「列很全」。规矩：① 每张源表的表头必须先映射到**规范变量名**（`EC.xlsx` 的 `CA125` 与 `EC-OP` 的 `ca125` 是同一个变量，`肌层1` 与 `肌层2` 不是），映射表落 `dict.alias_of`；② 同一 `(person, period_month, unk_seq)` 的多条源记录合并成一行，后到非空补空、双非空冲突保首到并记 `qc(VALUE_CONFLICT)`；③ 列名匹配 `_\d+$` 且无法在 `dict` 中说明是不同的概念，一律判 P0 打回；④ 行数守恒：`Σ merged_n` = 实际使用的源记录数，合并前后可对账。LC 的 `clin`/`clin-thin` 拼列是同一问题的先例，裁决相同。

**8.9 `dict.role` 与 `dict.dtype` 必须真的分类，不许一律填 `covariate`/`TEXT`。** 实测事故：EC v3 的 626 条 dict 里 role 只有两种取值（covariate 196 / key 20，其余为 feature），于是 `影像诊断`（自由文本报告）、`病理`（整段病理描述）、`radscore`/`nomo*`（派生分析产物）、`出生地`/`province`/`area`（准标识）与 `ca125`（测量值）在表里地位相同；`dtype` 则 100% 是 `TEXT`。后果不是立刻出错，而是任何「按角色取样」的自动流程（找结局、找特征、找 PHI、挑可分析数值列）都会拿错列——把病理描述当协变量入模，或者把 `radscore` 当原始测量放进基线表。规矩：① role 取 `key`（主键/时点/队列）、`phi`（身份）、`quasi`（出生地、省、邮编等准标识，可外发但须声明）、`outcome`（结局）、`feature`（组学/影像特征）、`covariate`（原始临床变量）、`derived`（脚本算出来的，如 radscore、nomo）、`text`（自由文本，永不入模）、`aux`（行号、批次等辅助）；② dtype 按列值推断：全是 0/1 → `bool`；可解析为数字且小数位一致 → `int`/`real`；有限取值集合 → `enum`（配 enum 表）；可解析日期 → `date`；其余 `text`；③ 每个项目必须能回答「结局列是哪几个、可入模数值列有多少、text 列有几个」，答不出即 dict 未完成。

---

## 9 用户决议（2026-11-25，试点验收后）

**9.1 结局列：可以没有，先建后补。** EC 159 个变量里无任何复发/死亡/生存/随访，用户裁定「可以无结局列，以后加入」。落法：

- 家族库与项目库 meta 加 `outcome_status='pending'`、`outcome_note='源数据无结局列；结局由研究目的决定，届时补录'`；
- `dict` **不预建**任何 `role='outcome'` 的行（与「结局未产生不得预建」门禁一致：预建一列全 NULL 的 `os_months` 会被下游误当「有结局但全缺失」）；
- 报告与论文口径只许写横断面与影像特征，**不得写 KM/Cox**，除非将来补入并重跑门禁；
- 补录进来时的落法：新增列 → `dict` 补 `role='outcome'` + `unit`/`time_origin` → `outcome_status='available'` → 重跑 gates。

**9.2 骨肿瘤与骨水泥：不单独立族，并入 OTHERS。**

- `ybc_STWZ`（骨水泥椎体成形，EGVB 剔出，15,487 格）与骨肿瘤（EC 剔出 5 张表，207,747 格 + 8 行 `src` 留痕）作为 `_DataBase\OTHERS` 下的两个子工程，**不新建 `BT` 家族目录**；
- 两个子工程各自建库、各自写 `meta.cohort_scope`（骨水泥 vs 骨原发/转移瘤），病种不混；
- 建这两库之前必须先解 `OTHERS` 现有的 33 列无表头 sheet（`#n` 列名 9,636 行），否则重复 MHE_AN 的坑；
- 家族级人数仍不报（两批数据无共同键，与 LC 同口径）。

**9.2.1 OTHERS 是人鼠混装，物种必须做成可查询的第一类属性。** 实测：`OTHERS` 现有 person 38 **全部来自 `ly_OSTEO`**（`person_key(id_type='编号')`，38 值 38 唯一，`person.name` 全空），是小鼠骨/CT 与 qPCR 的样本编号；而新并入的 `ybc_STWZ`（骨水泥，姓名 731 格 / 去重 259）与 `ybc_BONETUMOR`（骨肿瘤，`影像号` 3,416）是人的临床数据。三批键空间零交集（`person` 数 38 + 259 + 3,416），但**同族混物种**的后果是任何「OTHERS 有多少例」都会把小鼠和人加在一起。落法：

1. 每个子工程 `meta.subject_type ∈ {'human','animal'}`；`ly_OSTEO`/`cx_MISC`/`lxj_BMP6` = `animal`，`ybc_STWZ`/`ybc_BONETUMOR` = `human`；
2. `family_uid` 分段（**三段互不重叠**）：人真实身份 `OTHERS-0xxxxx`、**动物真实受试者 `OTHERS-8xxxxx`**、**占位（人与动物共用）`OTHERS-9xxxxx`**，写进 `meta.uid_ranges`。动物有键无姓名 → `id_status='keyed_animal'`（新枚举值，与 `keyed_partial`「有键但缺 PHI」区分开：动物的姓名缺失不是缺陷，硬填 `keyed_partial` 会让「待补身份」清单里永远挂着 38 只小鼠）。门禁 **D63**：`uid` 段 ↔（`meta.subject_type`, `id.status`）三元映射，任一不符 → P0。**注意与 §8.1 的关系**：§8.1 的 9 段定义不变（无患者级身份键 → 占位），动物真实受试者不进 9 段——它们有键，进 9 段等于把「有编号的动物」写成「无身份占位」，并会污染 D53 的占位判据（`uid ≥ 900000`）；
3. 报告与小节标题一律写「OTHERS（人）」/「OTHERS（动物）」，家族级人数不报；
4. 小鼠那 38 行保持现状（编号键、无姓名），不进 `id_number` 的人类号码段。

**9.4 跨族 `blob:` 指针必须自带容器身份。** 子工程数据只能从别的家族的旧库 blob 读时（OTHERS 的 `ybc_STWZ` 取自 EGVB、`ybc_BONETUMOR` 取自 EC），**不复制 blob**（复制 = 同一批数据存两份，将来一份修一份没修，就是 EC src 43 的翻版），但 `rel_path` 必须写成两段式：

```
blob:<容器库文件名>@<容器构建日>:src_id=<旧库 src_id>#<sheet_name>
```

理由：`src_id` 在旧库内是稳定主键，`@构建日` 防止同名库重建后 `src_id` 重新编号（SSD 已重建过一次）；`src.sha256` 必须填容器内该行 blob 的实际 sha256。门禁 **D62**：`blob:` 前缀的 `rel_path`，容器文件必须在、`src_id` 必须可查、`sha256` 必须与容器内 blob 实际哈希一致，任一不满足 → P1。否则「可回溯」只是口号——路径错了不会报错，只是查不到。

**9.5 多行表头可以拼，但拼出来的必须是「读到的名字」。** 处理两类要分开：①qPCR 块表头（首行是 `Block Type / 96-Well 0.2-mL Block`，真表头在其后）→ 按含 `Well` 的行定位真表头，这是读，允许，记 `meta.header_rows=n`；②首行+次行拼接式多行表头（`最终数据` 66 列首行仅 9 非空等 5 张）→ 允许拼，但要满足：列名必须是 `行1|行2` 的确定性结果，不许出现 `未命名列_3`/`Unnamed: 3` 这类占位；拼完仍为空的列不进 clin，进 `excluded_columns`（附 `source_sheet`、`header_row`、`rows_nonnull`）；每张落 `qc(HEADER_MERGED, P2)` 写明「表头跨 2 行」。③纯数值矩阵（`数据.xlsx::信噪比-背景`，836 格）没有列名可读 → 排除，理由写「列名未确认」。判据：**拼出来的名字必须能在源表里指到具体单元格；指不到的就不是名字，是发明。**

**9.6 标「重复」之前必须先逐格比。** 「计算版 / 最终版 / output」这类文件名不是内容断言：它可能是同一批数据的重算，也可能是换了参数（基线、阈值）的第二套结果，后者标 `duplicate` 就是把一套真值藏起来。做法：按自然键（如 qPCR 的 `Well`）连，比共有列的值——差异 0 → 才可标 `duplicate`；有差异 → 两版都 `active`，逐格 `qc(VALUE_CONFLICT, P1)`，报告写清差异集中在哪些列（若某测量列整列不同，基本就是两套参数，必须都留）。这条与 MCI 的 204 行逐格核对同一规矩。

**9.3 五表之外的第六张 `meta` 表是基础设施，不是业务表。** 重申：`meta` k/v 承载 `schema_version`/`built_at`/`period_unit`/镜像四数/`identity_state`/`outcome_status` 等；`v NOT NULL`，因此「无此属性」= **不写该键**（如无时间轴则不写 `period_unit`，另记 `period_unit_missing_reason`），不许写空串或 `'NULL'` 字符串——那会让镜像门拿 `''` 和 `None` 比对。
