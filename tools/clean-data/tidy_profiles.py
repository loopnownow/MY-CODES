# -*- coding: utf-8 -*-
"""tidy_profiles — 两个子工程的声明式配置。

合并来源：
  * `lxj_AN/build_merge.py` / `build_complete.py` / `verify_full.py` 里的常量
  * `ssd/clinical_recover_sources.py` 的路径、论文靶表、``load_all_sources`` 列映射
  * `ssd/clinical_recover_phases.py` 的补缺变量与阈值
  * `ssd/impute_ssd_dti_ivim.py` / `split_ssd_imputed.py` 的输入输出约定

这里只放"是什么"，不放"怎么做"：算法在 `tidy_merge` / `tidy_complete` / `tidy_verify`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union

from tidy_paths import project_root, source_dir

# ═════════════════════════════════════════════════════════════════
# 通用小数据结构
# ═════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class SheetSpec:
    """一张表的定位：主键列、组别列、要追加的列、追加列前缀。"""

    sheet: str
    key_col: int
    group_col: Optional[int] = None
    extras: Tuple[Tuple[int, Optional[str]], ...] = ()
    prefix: str = ""


@dataclass(frozen=True)
class KeyedSource:
    """按主键对齐的源文件（.xls，lxj_AN 侧）。"""

    tag: str
    filename: str
    sheets: Tuple[SheetSpec, ...] = ()
    key_col: int = 0
    header_row: int = 0
    skip_keys: Tuple[str, ...] = ()
    #: 提升到主表前部的列：(源列序号, 主表列名)
    promoted: Tuple[Tuple[int, str], ...] = ()
    #: 追加列的源列区间 (起, 止)；止为 None 表示到最后一列
    extra_range: Optional[Tuple[int, Optional[int]]] = None
    extra_prefix: str = ""


@dataclass(frozen=True)
class SourceSpec:
    """SSD 侧的"规范化长记录"来源：一张表 → 若干行 canonical 字段。"""

    path: str
    sheet: Union[str, int]
    source: str
    cohort: Optional[str] = None
    cohort_col: Optional[str] = None
    name_en: Optional[str] = None
    name_cn: Optional[str] = None
    subject_id: Optional[str] = None
    sex: Optional[str] = None
    numeric: Tuple[Tuple[str, str], ...] = ()
    text: Tuple[Tuple[str, str], ...] = ()
    required: bool = True


# ═════════════════════════════════════════════════════════════════
# lxj_AN —— 单课题合并工程（3 个旧 .xls → 零缺失完整表）
# ═════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class LxjAnProfile:
    name: str = "lxj_AN"
    root_profile: str = "lxj_AN"

    # ── 输入 ──
    file_a: str = "2026碳纤维部数据-- (3).xls"
    file_b: str = "测试数据记录_20260306164426.xls"
    file_c: str = "患者列表.xls"
    skip_keys: Tuple[str, ...] = ("1",)  # B/C 的测试记录（就诊号=1 / 姓名=测试）

    #: A 表 7 张工作表：(表名, 主键列, 组别列, 追加列, 前缀)
    a_sheets: Tuple[SheetSpec, ...] = (
        SheetSpec("MR", 2, 1, ((3, "检查结果"),), "MR_"),
        SheetSpec("Q16问卷", 1, None, tuple((c, None) for c in range(2, 18)), "Q16_"),
        SheetSpec("认知量表得分", 1, 0, ((6, "MMSE总分"), (7, "MoCA总分")), ""),
        SheetSpec("MMSE详细", 2, 1, tuple((c, None) for c in range(8, 13)), "MMSE_"),
        SheetSpec("MoCA详细", 1, 0, tuple((c, None) for c in range(7, 15)), "MoCA_"),
        SheetSpec("工人体检系统数据", 1, 0, tuple((c, None) for c in range(4, 12)), "体检_"),
        SheetSpec("对照组数据", 1, 0, (), ""),
    )

    #: 主表前部固定列（组别/姓名/性别/年龄 由 A、B、C 冲突解决后填入）
    head_cols: Tuple[str, ...] = (
        "编号", "组别", "姓名", "性别", "年龄", "年龄_A表", "出生日期",
        "教育程度", "文化程度", "是否为右利手", "数据来源",
    )
    #: A 表内定位「文化程度 / 是否为右利手」的表（按此顺序取首个非空）
    a_field_sheets: Tuple[str, ...] = ("认知量表得分", "MMSE详细", "MoCA详细")
    field_names: Tuple[Tuple[str, str], ...] = (
        ("文化程度", "文化程度"),
        ("是否为右利手", "是否为右利手？"),
    )
    #: A 表内取年龄的表（按此顺序）
    a_age_sheets: Tuple[str, ...] = ("认知量表得分", "工人体检系统数据")
    #: 性别/年龄 缺失时回退查 A 表的顺序
    a_demo_sheets: Tuple[str, ...] = (
        "认知量表得分", "工人体检系统数据", "MMSE详细", "MoCA详细",
    )

    out_merge: str = "lxj_AN.xlsx"
    out_complete: str = "lxj_AN_complete.xlsx"

    # ── 零缺失门槛（build_complete） ──
    edu_map: Tuple[Tuple[str, str], ...] = (("专科", "大专"),)
    edu_a_col: str = "文化程度"
    edu_c_col: str = "教育程度"
    edu_out_col: str = "受教育程度"
    core_cols: Tuple[str, ...] = (
        "组别", "姓名", "性别", "年龄", "是否为右利手", "MMSE总分", "MoCA总分",
    )
    eye_prefixes: Tuple[str, ...] = ("0_", "1_", "2_")
    time_cols: Tuple[str, ...] = ("测试开始时间", "测试结束时间")
    always_exclude: Tuple[str, ...] = ("教育程度", "文化程度", "数据来源")
    id_order: Tuple[str, ...] = (
        "编号", "组别", "姓名", "性别", "年龄", "年龄_A表", "出生日期",
        "受教育程度", "是否为右利手", "MMSE总分", "MoCA总分",
    )
    complete_sheet: str = "完整数据"
    #: 可选 cln/rad/qc 三表输出（下游 RAD console 约定：CLIN_SHEET/RAD_SHEET/QC_SHEET）
    layout_id_col: str = "Pateint_ID"  # 拼写沿用 ref/settings.ini，勿"修正"
    layout_clin_cols: Tuple[str, ...] = (
        "Pateint_ID", "组别", "性别", "年龄", "受教育程度", "MMSE总分", "MoCA总分",
    )
    #: qc = 前置列 + 眼动元信息（范式名称/参数） + 后置列，顺序对齐现存 lxj_AN_complete.xlsx
    layout_qc_pre: Tuple[str, ...] = ("姓名", "年龄_A表", "是否为右利手", "出生日期")
    layout_qc_post: Tuple[str, ...] = (
        "测试开始时间", "测试结束时间", "创建时间", "C_系统内码", "C_创建时间",
    )
    layout_qc_extra_contains: Tuple[str, ...] = ("范式名称", "参数")
    #: 输出时的列改名：主表列名 → cln/rad/qc 表列名（RAD console 按 Group 取结局）
    layout_rename: Tuple[Tuple[str, str], ...] = (("组别", "Group"),)

    # ── 逐格校验（verify_full） ──
    verify_sheets: Tuple[Tuple[str, int, Dict[int, str]], ...] = (
        ("MR", 2, {3: "MR_检查结果"}),
        ("Q16问卷", 1, {c: "@" for c in range(2, 18)}),
        ("认知量表得分", 1, {6: "MMSE总分", 7: "MoCA总分"}),
        ("MMSE详细", 2, {c: "@" for c in range(8, 13)}),
        ("MoCA详细", 1, {c: "@" for c in range(7, 15)}),
        ("工人体检系统数据", 1, {c: "@" for c in range(4, 12)}),
        ("对照组数据", 1, {}),
    )
    verify_prefix: Tuple[Tuple[str, str], ...] = (
        ("Q16问卷", "Q16_"), ("MMSE详细", "MMSE_"),
        ("MoCA详细", "MoCA_"), ("工人体检系统数据", "体检_"),
    )
    verify_b_cols: Tuple[Tuple[int, str], ...] = ((1, "姓名"), (2, "性别"), (3, "年龄"))
    verify_c_cols: Tuple[Tuple[int, str], ...] = ((5, "出生日期"), (6, "教育程度"))
    master_sheet: str = "总表"
    log_sheet: str = "冲突与来源日志"

    @property
    def root(self):
        return project_root(self.root_profile)

    @property
    def ref(self):
        return source_dir(self.root_profile)

    def path(self, filename: str):
        return self.ref / filename


# ═════════════════════════════════════════════════════════════════
# SSD —— jfl_SSD 临床资料恢复 / 插补 / 拆分
# ═════════════════════════════════════════════════════════════════
#: canonical 长表字段（顺序即 source_long 的列序）
CANON_FIELDS: Tuple[str, ...] = (
    "name_en", "name_cn", "subject_id", "cohort_src", "source",
    "age", "sex", "education_years", "mmse", "phq15", "sss", "sss8", "gad7",
    "sds", "sas", "scl90", "bmi", "exam_date", "chief_complaint", "image_quality",
    "marital_status", "family_history", "occupation_status", "blood_type",
    "past_history", "onset_time", "disease_duration", "visit_count", "medication",
    "blood_routine", "ecg", "address", "cost", "treatment_course", "severity",
    "imaging_findings", "phone",
)
#: 原实现额外带出的非 canonical 列；**按需出现**（所有源都没值时不出现在
#: `source_long.csv` 里），这里只作文档，不预置进行字典
CANON_EXTRA: Tuple[str, ...] = ("height_cm", "weight_kg", "followup")
#: 需要按数值解析的 canonical 字段
NUMERIC_FIELDS: Tuple[str, ...] = (
    "age", "education_years", "mmse", "phq15", "sss", "sss8", "gad7",
    "sds", "sas", "scl90", "bmi",
)
#: 参与真实合并的字段（phase1）
MERGE_VALUE_FIELDS: Tuple[str, ...] = (
    "age", "sex", "education_years", "mmse", "phq15", "sss", "sss8", "gad7",
    "sds", "sas", "scl90", "bmi", "exam_date", "chief_complaint", "image_quality",
    "marital_status", "family_history", "occupation_status", "blood_type",
    "past_history", "onset_time", "disease_duration", "visit_count", "medication",
    "blood_routine", "ecg", "address", "cost", "treatment_course", "severity",
    "imaging_findings", "phone", "name_cn",
)

_SSD_WXT_TEXT: Tuple[Tuple[str, str], ...] = (
    ("marital_status", "婚姻情况"), ("family_history", "家族史"),
    ("occupation_status", "工作情况"), ("blood_type", "血型"),
    ("chief_complaint", "主要症状"), ("past_history", "既往史"),
    ("onset_time", "首发时间"), ("disease_duration", "病程"),
    ("visit_count", "就诊次数"), ("medication", "药物"),
    ("treatment_course", "疗程"), ("cost", "检查治疗费用"),
    ("imaging_findings", "影像学检查"), ("blood_routine", "血常规"),
    ("ecg", "心电图"), ("phone", "电话"), ("address", "地址"),
    ("exam_date", "时间"),
)
_SSD_MAIN_TEXT: Tuple[Tuple[str, str], ...] = (
    ("exam_date", "exam_date"), ("chief_complaint", "chief_complaint"),
    ("image_quality", "image_quality"), ("marital_status", "marital_status"),
    ("family_history", "family_history"), ("occupation_status", "occupation_status"),
    ("blood_type", "blood_type"), ("past_history", "past_history"),
    ("onset_time", "onset_time"), ("disease_duration", "disease_duration"),
    ("visit_count", "visit_count"), ("medication", "medication"),
    ("blood_routine", "blood_routine"), ("ecg", "ecg"), ("address", "address"),
    ("cost", "cost"), ("treatment_course", "treatment_course"),
    ("severity", "severity"), ("imaging_findings", "imaging_findings"),
    ("phone", "phone"),
)


@dataclass(frozen=True)
class SsdProfile:
    name: str = "ssd"
    root_profile: str = "ssd"
    out_subdir: str = "clinical_recovery"
    seed: int = 66666

    # ── 主表 + 多源 ──
    main_file: str = "clinical_merged_en.xlsx"
    main_cohort_col: str = "cohort"
    #: 主库（已存在的中文/英文临床库），作为合并基底
    main_spec: SourceSpec = SourceSpec(
        path="clinical_merged_en.xlsx", sheet=0, source="clinical_merged_en",
        cohort_col="cohort", name_en="name_en", subject_id="subject_id", sex="sex",
        numeric=(("age", "age"), ("education_years", "education_years"),
                 ("mmse", "mmse"), ("phq15", "phq15"), ("sss", "sss"),
                 ("sss8", "sss8"), ("gad7", "gad7"), ("sds", "sds"),
                 ("sas", "sas"), ("scl90", "scl90"), ("bmi", "bmi"),
                 ("height_cm", "height_cm"), ("weight_kg", "weight_kg")),
        text=_SSD_MAIN_TEXT,
    )
    source_specs: Tuple[SourceSpec, ...] = (
        SourceSpec(
            path="0del/0list.xlsx", sheet="SSD", source="0list_SSD", cohort="SSD",
            name_en="n", name_cn="name", subject_id="num", sex="sex",
            numeric=(("age", "age"), ("education_years", "受教育年限"),
                     ("mmse", "MMSE"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sss8", "SSS-8"), ("gad7", "GAD-7")),
            text=(("exam_date", "日期"), ("chief_complaint", "主诉症状"),
                  ("image_quality", "图像质量"), ("followup", "随访备注")),
        ),
        SourceSpec(
            path="0del/0list.xlsx", sheet="HC", source="0list_HC", cohort="HC",
            name_en="n", name_cn="name", subject_id="num", sex="sex",
            numeric=(("age", "age"), ("education_years", "受教育年限"),
                     ("mmse", "MMSE"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sss8", "SSS-8"), ("gad7", "GAD-7")),
            text=(("exam_date", "日期"), ("chief_complaint", "主诉症状"),
                  ("image_quality", "图像质量"), ("followup", "随访备注")),
        ),
        SourceSpec(
            path="0del/0list.xlsx", sheet="wen_clin", source="0list_wen_clin",
            cohort="SSD", name_en="name", name_cn="姓名", subject_id="序号", sex="性别",
            numeric=(("age", "年龄"), ("education_years", "受教育年限"),
                     ("bmi", "BMI"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sds", "SDS"), ("sas", "SAS"), ("scl90", "SCL-90")),
            text=(("marital_status", "婚姻情况"), ("family_history", "家族史"),
                  ("occupation_status", "工作情况"), ("blood_type", "血型"),
                  ("chief_complaint", "主要症状"), ("past_history", "既往史"),
                  ("onset_time", "首发时间"), ("disease_duration", "病程"),
                  ("visit_count", "就诊次数"), ("medication", "药物"),
                  ("treatment_course", "疗程"), ("cost", "检查治疗费用"),
                  ("imaging_findings", "影像学检查"), ("blood_routine", "血常规"),
                  ("ecg", "心电图"), ("phone", "电话"), ("address", "地址"),
                  ("exam_date", "时间")),
        ),
        SourceSpec(
            path="0del/SSD患者数据合并结果.xlsx", sheet="dyf", source="ssd_merge_dyf",
            cohort="SSD", name_en="姓名", name_cn="name", subject_id="NUM", sex="sex",
            numeric=(("age", "age"), ("education_years", "受教育年限"),
                     ("mmse", "MMSE"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sss8", "SSS-8"), ("gad7", "GAD-7")),
            text=(("exam_date", "日期"), ("chief_complaint", "主诉症状")),
        ),
        SourceSpec(
            path="0del/SSD患者数据合并结果.xlsx", sheet="wxt", source="ssd_merge_wxt",
            cohort="SSD", name_en="姓名", name_cn="name", subject_id="序号", sex="性别",
            numeric=(("age", "年龄"), ("education_years", "受教育年限"),
                     ("mmse", "MMSE__wxt_HC"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sss8", "SSS-8"), ("gad7", "GAD-7"), ("sds", "SDS"),
                     ("sas", "SAS"), ("scl90", "SCL-90"), ("bmi", "BMI")),
            text=_SSD_WXT_TEXT,
        ),
        SourceSpec(
            path="0del/SSD患者数据合并结果.xlsx", sheet="HC", source="ssd_merge_HC",
            cohort="HC", name_en="姓名", name_cn="name", subject_id="序号", sex="性别",
            numeric=(("age", "年龄"), ("education_years", "受教育年限"),
                     ("mmse", "MMSE__wxt_HC"), ("phq15", "PHQ-15"), ("sss", "SSS"),
                     ("sds", "SDS"), ("sas", "SAS"), ("scl90", "SCL-90"),
                     ("bmi", "BMI")),
            text=_SSD_WXT_TEXT,
        ),
        SourceSpec(
            path="SSD.xlsx", sheet="clinical", source="SSD_xlsx_clinical",
            cohort_col="Group", name_en="Name", sex="sex",
            numeric=(("age", "age"), ("education_years", "education_years"),
                     ("mmse", "mmse"), ("phq15", "phq15"), ("sss", "sss"),
                     ("sss8", "sss8"), ("gad7", "gad7"), ("sds", "sds"),
                     ("sas", "sas"), ("scl90", "scl90"), ("bmi", "bmi")),
            text=(("exam_date", "exam_date"), ("chief_complaint", "chief_complaint"),
                  ("image_quality", "image_quality"),
                  ("marital_status", "marital_status"),
                  ("family_history", "family_history"),
                  ("occupation_status", "occupation_status"),
                  ("blood_type", "blood_type"), ("past_history", "past_history"),
                  ("onset_time", "onset_time"),
                  ("disease_duration", "disease_duration"),
                  ("visit_count", "visit_count"), ("medication", "medication"),
                  ("blood_routine", "blood_routine"), ("ecg", "ecg"),
                  ("address", "address"), ("phone", "phone")),
            required=False,
        ),
    )

    # ── cohort 回推阈值（phase1） ──
    valid_cohorts: Tuple[str, ...] = ("SSD", "HC")
    infer_ssd_phq_min: float = 5.0
    infer_hc_phq_max: float = 3.0
    infer_hc_sss_max: float = 24.0

    # ── 论文靶表（补缺 / 纯合成 / 校验） ──
    paper1: Dict[str, Any] = field(default_factory=lambda: {
        "name": "paper1_应用研究_表1",
        "SSD": {"n": 101, "male": 29, "female": 72, "age": (47.30, 13.60),
                "education_years": (11.56, 3.29), "phq15": (8.60, 3.45),
                "sss": (33.52, 6.05)},
        "HC": {"n": 36, "male": 18, "female": 18, "age": (44.19, 12.22),
               "education_years": (13.14, 2.94), "phq15": (1.81, 1.90),
               "sss": (22.28, 2.56)},
    })
    paper2: Dict[str, Any] = field(default_factory=lambda: {
        "name": "paper2_毕业论文part1_表1-1",
        "SSD": {"n": 56, "male": 25, "female": 31, "age": (47.63, 12.17),
                "education_years": (10.82, 3.37), "mmse": (27.25, 1.27),
                "phq15": (8.32, 2.56), "sss": (35.48, 3.02),
                "gad7": (5.36, 3.98), "hamd": (6.66, 2.98)},
        "HC": {"n": 46, "male": 23, "female": 23, "age": (42.74, 14.69),
               "education_years": (11.04, 3.85), "mmse": (27.70, 1.11),
               "phq15": (1.35, 0.73), "sss": (22.26, 0.74),
               "gad7": (3.98, 1.56), "hamd": (4.13, 1.75)},
    })
    fill_cont_vars: Tuple[str, ...] = (
        "age", "education_years", "mmse", "phq15", "sss", "sss8", "gad7",
        "sds", "sas", "bmi", "hamd",
    )
    fill_cat_vars: Tuple[str, ...] = ("sex",)
    core_fill_vars: Tuple[str, ...] = (
        "age", "sex", "education_years", "mmse", "phq15", "sss", "gad7",
    )
    never_synth: Tuple[str, ...] = (
        "subject_id", "name_en", "name_cn", "cohort", "exam_date",
        "chief_complaint", "image_quality", "followup_note", "followup_result",
        "height_cm", "weight_kg", "marital_status", "family_history",
        "occupation_status", "blood_type", "past_history", "onset_time",
        "disease_duration", "visit_count", "medication", "blood_routine", "ecg",
        "address", "cost", "treatment_course", "severity", "imaging_findings",
        "phone", "neurotrophic_factor", "is_ssd_clinical", "data_sources",
        "mmse_note", "phq15_note", "sss_note", "sss8_note", "gad7_note",
        "sds_note", "sas_note", "scl90_note", "depression_scale", "scl90",
    )
    bounds: Tuple[Tuple[str, float, float], ...] = (
        ("age", 18, 80), ("education_years", 0, 22), ("mmse", 24, 30),
        ("phq15", 0, 30), ("sss", 20, 80), ("sss8", 0, 32), ("gad7", 0, 21),
        ("sds", 20, 80), ("sas", 20, 80), ("bmi", 15, 40), ("hamd", 0, 30),
    )
    fallback_moments: Tuple[Tuple[str, float, float], ...] = (
        ("age", 45, 12), ("education_years", 11, 3.5), ("mmse", 27.5, 1.2),
        ("phq15", 5, 3), ("sss", 28, 6), ("sss8", 8, 4), ("gad7", 4, 3),
        ("sds", 40, 8), ("sas", 40, 8), ("bmi", 23, 3), ("hamd", 5, 2.5),
    )
    int_like_vars: Tuple[str, ...] = (
        "age", "mmse", "phq15", "sss", "sss8", "gad7", "hamd",
    )
    one_decimal_vars: Tuple[str, ...] = ("education_years", "sds", "sas")

    # ── 输出文件名 ──
    out_real: str = "clinical_real_merged.xlsx"
    out_filled: str = "clinical_filled_paper_aware.xlsx"
    out_syn1: str = "clinical_synthetic_paper1.xlsx"
    out_syn2: str = "clinical_synthetic_paper2.xlsx"
    out_inventory: str = "00_data_inventory.md"
    out_validation: str = "validation_vs_paper.md"
    out_readme: str = "README_clinical_recovery.md"
    out_source_long: str = "source_long.csv"
    out_fill_summary: str = "fill_summary.csv"
    main_sheet_name: str = "clinical"

    # ── 插补（impute_ssd_dti_ivim） ──
    impute_profile: str = "ssd_dti_ivim"
    impute_file: str = "jfl_SSD_DTI_IVIM.xlsx"
    impute_cln_sheet: str = "cln"
    impute_rad_sheet: str = "rad"
    impute_qc_sheet: str = "impute_qc"
    impute_group_col: str = "Group"
    impute_method: str = "mice"
    impute_backup_name: str = "SSD_DTI_IVIM_pre_impute.xlsx"

    # ── 拆分（split_ssd_imputed） ──
    split_src_names: Tuple[str, ...] = ("0del/SSD_imputed.xlsx", "SSD_imputed.xlsx")
    #: 盘点报告 §6 的登记顺序（与原实现一致；tidy_split 按名字显式取表，与此无关）
    split_sheets: Tuple[str, ...] = ("perfusion", "DTI", "DKI", "IVIM")
    split_img_sep: str = "__"
    split_legacy_outs: Tuple[Tuple[str, str], ...] = (
        ("ssd", "SSD_DTI.xlsx"),
        ("ssd", "SSD_IVIM_DKI.xlsx"),
    )

    @property
    def root(self):
        return project_root(self.root_profile)

    @property
    def out(self):
        return self.root / self.out_subdir

    @property
    def main_path(self):
        return self.root / self.main_file

    def rel(self, rel_path: str):
        return self.root / rel_path

    def bound(self, var: str):
        for name, low, high in self.bounds:
            if name == var:
                return low, high
        return None, None

    @property
    def synth_note(self) -> str:
        """盘点报告 §7 的可合成变量清单（与旧实现逐字一致，注意 sex 排在 age 之后）。"""
        rest = [v for v in self.fill_cont_vars if v != "age"]
        return ", ".join(["age"] + list(self.fill_cat_vars) + rest)

    def fallback(self, var: str):
        for name, m, sd in self.fallback_moments:
            if name == var:
                return m, sd
        return 0.0, 1.0


SSD = SsdProfile()
LXJ_AN = LxjAnProfile()

#: profile 名 → 配置对象；供 CLI 统一取用
PROFILES: Dict[str, Any] = {"lxj_AN": LXJ_AN, "ssd": SSD}


def get_profile(name: str):
    """按名字取配置对象（大小写不敏感，兼容 ``lxj_an`` / ``SSD``）。"""
    if name in PROFILES:
        return PROFILES[name]
    for k, v in PROFILES.items():
        if k.lower() == str(name).lower():
            return v
    raise KeyError(f"unknown profile {name!r}; known: {sorted(PROFILES)}")
