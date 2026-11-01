# -*- coding: utf-8 -*-
"""Build a synthetic-but-schema-faithful SSD project tree for old-vs-new equivalence tests.

Creates (under the given root):
    clinical_merged_en.xlsx              主库
    0del/0list.xlsx                      SSD / HC / wen_clin 名单
    0del/SSD患者数据合并结果.xlsx         dyf / wxt / HC
    SSD.xlsx                             perfusion / DTI / DKI / IVIM / clinical
    SSD_imputed.xlsx                     perfusion / DTI / IVIM / DKI  （供 split 测试）
    DTI_IVIM_SRC/jfl_SSD_DTI_IVIM.xlsx   cln / rad              （供 impute 测试）

Row/column layout mirrors the real files as documented in tidy_profiles.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

MAIN_COLS = [
    "name_en", "subject_id", "cohort", "age", "sex", "education_years", "mmse",
    "phq15", "sss", "sss8", "gad7", "sds", "sas", "scl90", "bmi", "height_cm",
    "weight_kg", "exam_date", "chief_complaint", "image_quality", "marital_status",
    "family_history", "occupation_status", "blood_type", "past_history", "onset_time",
    "disease_duration", "visit_count", "medication", "blood_routine", "ecg",
    "address", "cost", "treatment_course", "severity", "imaging_findings", "phone",
    "data_sources",
]


def main_rows():
    nan = np.nan
    return [
        # 主库已基本吸收各源；这里制造冲突/回填/回推三种情形
        ["ZHANG SAN", "S001", "SSD", 45, "male", 12, 27, 8, 33, 10, nan, nan, nan, nan,
         23.5, 170, 65, "2026-01-05", "痛", "good", "已婚", "无", "在职", "A", "无",
         "2025-12", "1年", 1, "无", "正常", "正常", "addr", 100, "1", "轻", "无", "138",
         "main"],
        ["LI SI", "S002", "SSD", 50, "female", 9, nan, 10, 35, nan, 6, nan, nan, nan,
         21.0, 160, 52, "2026-01-06", "麻", "good", "已婚", "无", "在职", "B", "无",
         "2025-11", "2年", 2, "药", "正常", "正常", "addr", 120, "2", "中", "无", "139",
         "main"],
        ["WANG WU", "S003", "HC", 41, "male", 15, 29, 1, 22, nan, nan, nan, nan, nan,
         24.0, 175, 70, "2026-01-07", "", "good", "未婚", "无", "在职", "O", "无",
         "", "", 0, "", "正常", "正常", "", 0, "", "", "", "",
         "main"],
        ["ZHAO LIU", "S004", "unknown", 38, nan, 11, 28, 2, 20, nan, nan, nan, nan, nan,
         nan, nan, nan, "2026-01-08", "", "fair", "", "", "", "", "", "", "", 0, "",
         "", "", "", 0, "", "", "", "", ""],
        ["SUN QI", "S005", "SSD", 47, "female", nan, nan, 9, 30, nan, nan, nan, nan, nan,
         22.0, 158, 50, "2026-01-09", "胀", "good", "已婚", "", "", "", "", "", "", 1,
         "", "", "", "", 0, "", "", "", "", ""],
        ["ZHOU BA", "S006", "unknown", nan, "male", 10, nan, nan, nan, nan, nan, nan, nan,
         nan, nan, nan, nan, "", "", "", "", "", "", "", "", "", "", 0, "", "", "", "",
         0, "", "", "", "", ""],
    ]


def main_df():
    df = pd.DataFrame(main_rows(), columns=MAIN_COLS)
    # 第 7 行：主库独有、无任何源、量表全缺 → cohort 保持 unknown，
    # 触发 phase2_fill 的 unknown 分支与 exact_mean_sd 补缺路径。
    extra = {c: np.nan for c in MAIN_COLS}
    extra.update(name_en="QIAN JIU", subject_id="S007", cohort="unknown",
                 sex="female", data_sources="main")
    return pd.concat([df, pd.DataFrame([extra], columns=MAIN_COLS)], ignore_index=True)


def list_ssd_df():
    return pd.DataFrame({
        "n": ["Zhang San", "Chen Jiu", "Li Si"],
        "name": ["张三", "陈九", "李四"],
        "num": [101, 102, 103],
        "age": [45, 55, 50],
        "sex": [1, 2, 2],
        "受教育年限": [12, 8, 9],
        "MMSE": [27, 25, 26],
        "PHQ-15": [8, 12, 10],
        "SSS": [33, 40, 35],
        "SSS-8": [10, 12, 11],
        "GAD-7": [5, 7, 6],
        "日期": ["2026-01-05", "2026-01-06", "2026-01-07"],
        "主诉症状": ["痛", "麻", "酸"],
        "图像质量": ["good", "good", "fair"],
        "随访备注": ["无", "", "随访"],
    })


def list_hc_df():
    return pd.DataFrame({
        "n": ["Zhao Liu", "Wang Wu"],
        "name": ["赵六", "王五"],
        "num": [201, 202],
        "age": [38, 41],
        "sex": [1, 1],
        "受教育年限": [11, 15],
        "MMSE": [28, 29],
        "PHQ-15": [2, 1],
        "SSS": [20, 22],
        "SSS-8": [5, 4],
        "GAD-7": [1, 0],
        "日期": ["2026-01-08", "2026-01-07"],
        "主诉症状": ["", ""],
        "图像质量": ["good", "good"],
        "随访备注": ["", ""],
    })


WEN_TEXT = {
    "婚姻情况": "已婚", "家族史": "无", "工作情况": "在职", "血型": "A",
    "主要症状": "痛", "既往史": "无", "首发时间": "2025-12", "病程": "1年",
    "就诊次数": 1, "药物": "无", "疗程": "1", "检查治疗费用": 100,
    "影像学检查": "无", "血常规": "正常", "心电图": "正常", "电话": "138",
    "地址": "addr",
}


def list_wen_df():
    d = dict(WEN_TEXT)
    return pd.DataFrame({
        "name": ["Sun Qi", "Zhou Ba"],
        "姓名": ["孙七", "周八"],
        "序号": [301, 302],
        "年龄": [47, 44],
        "性别": [2, 1],
        "受教育年限": [11, 10],
        "BMI": [22.0, 24.1],
        "PHQ-15": [9, 3],
        "SSS": [30, 23],
        "SDS": [45, 38],
        "SAS": [42, 36],
        "SCL-90": [130, 120],
        "婚姻情况": [d["婚姻情况"]] * 2,
        "家族史": [d["家族史"]] * 2,
        "工作情况": [d["工作情况"]] * 2,
        "血型": [d["血型"]] * 2,
        "主要症状": [d["主要症状"]] * 2,
        "既往史": [d["既往史"]] * 2,
        "首发时间": [d["首发时间"]] * 2,
        "病程": [d["病程"]] * 2,
        "就诊次数": [d["就诊次数"]] * 2,
        "药物": [d["药物"]] * 2,
        "疗程": [d["疗程"]] * 2,
        "检查治疗费用": [d["检查治疗费用"]] * 2,
        "影像学检查": [d["影像学检查"]] * 2,
        "血常规": [d["血常规"]] * 2,
        "心电图": [d["心电图"]] * 2,
        "电话": [d["电话"]] * 2,
        "地址": [d["地址"]] * 2,
        "时间": ["2026-01-09", "2026-01-10"],
    })


def merge_dyf_df():
    return pd.DataFrame({
        "姓名": ["Zhang San", "Qi Qi"],
        "name": ["张三", "齐七"],
        "NUM": ["D1", "D2"],
        "age": [45, 39],
        "sex": [1, 2],
        "受教育年限": [12, 13],
        "MMSE": [27, 28],
        "PHQ-15": [8, 2],
        "SSS": [33, 21],
        "SSS-8": [10, 6],
        "GAD-7": [5, 1],
        "日期": ["2026-01-05", "2026-01-11"],
        "主诉症状": ["痛", ""],
    })


def merge_wxt_df():
    return pd.DataFrame({
        "姓名": ["Sun Qi"],
        "name": ["孙七"],
        "序号": ["W1"],
        "年龄": [47],
        "性别": [2],
        "受教育年限": [11],
        "MMSE__wxt_HC": [26],
        "PHQ-15": [9],
        "SSS": [30],
        "SSS-8": [9],
        "GAD-7": [5],
        "SDS": [45],
        "SAS": [42],
        "SCL-90": [130],
        "BMI": [22.0],
        "婚姻情况": ["已婚"], "家族史": ["无"], "工作情况": ["在职"], "血型": ["A"],
        "主要症状": ["胀"], "既往史": ["无"], "首发时间": ["2025-11"],
        "病程": ["1年"], "就诊次数": [1], "药物": ["无"], "疗程": ["1"],
        "检查治疗费用": [110], "影像学检查": ["无"], "血常规": ["正常"],
        "心电图": ["正常"], "电话": ["137"], "地址": ["addr2"],
        "时间": ["2026-01-09"],
    })


def merge_hc_df():
    return pd.DataFrame({
        "姓名": ["Wang Wu"],
        "name": ["王五"],
        "序号": ["H1"],
        "年龄": [41],
        "性别": [1],
        "受教育年限": [15],
        "MMSE__wxt_HC": [29],
        "PHQ-15": [1],
        "SSS": [22],
        "SDS": [30],
        "SAS": [28],
        "SCL-90": [110],
        "BMI": [24.0],
        "婚姻情况": ["未婚"], "家族史": ["无"], "工作情况": ["在职"], "血型": ["O"],
        "主要症状": [""], "既往史": [""], "首发时间": [""],
        "病程": [""], "就诊次数": [0], "药物": [""], "疗程": [""],
        "检查治疗费用": [0], "影像学检查": [""], "血常规": ["正常"],
        "心电图": ["正常"], "电话": [""], "地址": [""],
        "时间": [""],
    })


def img_df(names, n_img, prefix, groups, seed):
    rng = np.random.RandomState(seed)
    data = {"Name": names, "Group": groups}
    for i in range(n_img):
        data[f"{prefix}__roi_{i:02d}"] = rng.normal(0, 1, len(names)).round(3)
    return pd.DataFrame(data)


def clinical_df():
    return pd.DataFrame({
        "Name": ["Zhou Ba", "Zhang San", "Sun Qi"],
        "Group": ["SSD", "SSD", "SSD"],
        "age": [44, 45, 47],
        "education_years": [10, 12, 11],
        "mmse": [27, 27, 26],
        "phq15": [3, 9, 9],       # Zhang San：主库 8 vs 这里 9 → 冲突日志
        "sss": [23, 33, 30],
        "sss8": [6, 10, 9],
        "gad7": [1, 5, 5],
        "sds": [38, np.nan, 45],
        "sas": [36, np.nan, 42],
        "scl90": [120, 130, 130],
        "bmi": [24.1, 23.5, 22.0],
        "sex": ["male", "male", "female"],
        "exam_date": ["2026-01-10", "2026-01-05", "2026-01-09"],
        "chief_complaint": ["", "痛", "胀"],
        "image_quality": ["good", "good", "good"],
        "marital_status": ["", "已婚", "已婚"],
        "family_history": ["", "无", "无"],
        "occupation_status": ["", "在职", "在职"],
        "blood_type": ["", "A", "A"],
        "past_history": ["", "无", "无"],
        "onset_time": ["", "2025-12", "2025-11"],
        "disease_duration": ["", "1年", "1年"],
        "visit_count": [0, 1, 1],
        "medication": ["", "无", "无"],
        "blood_routine": ["", "正常", "正常"],
        "ecg": ["", "正常", "正常"],
        "address": ["", "addr", "addr2"],
        "phone": ["", "138", "137"],
    })


def impute_cln_df():
    rng = np.random.RandomState(7)
    n = 20
    groups = ["SSD"] * 12 + ["HC"] * 8
    d = {
        "Patient_ID": [f"P{i:03d}" for i in range(1, n + 1)],
        "Group": groups,
        "age": rng.normal(46, 10, n).round(0),
        "sex": ["male"] * 10 + ["female"] * 10,
        "education_years": rng.normal(10, 3, n).round(1),
        "mmse": rng.normal(27, 1.5, n).round(0),
        "phq15": rng.normal(6, 3, n).round(0),
        "sss": rng.normal(30, 6, n).round(0),
        "sss8": rng.normal(9, 4, n).round(0),
        "gad7": rng.normal(4, 3, n).round(0),
    }
    df = pd.DataFrame(d)
    for col, idx in (("mmse", [0, 3, 7]), ("phq15", [1, 5]), ("sss8", [2, 9, 14]),
                     ("education_years", [4])):
        df.loc[idx, col] = np.nan
    return df


def half(x):
    """把一半元素设为 NaN，保证插补真的发生。"""
    x = pd.Series(x, dtype=float).copy()
    x.iloc[::2] = np.nan
    return x


def build(root: Path) -> None:
    root = Path(root)
    (root / "0del").mkdir(parents=True, exist_ok=True)
    (root / "DTI_IVIM_SRC").mkdir(parents=True, exist_ok=True)

    main_df().to_excel(root / "clinical_merged_en.xlsx", index=False, sheet_name="clinical")

    with pd.ExcelWriter(root / "0del" / "0list.xlsx", engine="openpyxl") as w:
        list_ssd_df().to_excel(w, sheet_name="SSD", index=False)
        list_hc_df().to_excel(w, sheet_name="HC", index=False)
        list_wen_df().to_excel(w, sheet_name="wen_clin", index=False)

    with pd.ExcelWriter(root / "0del" / "SSD患者数据合并结果.xlsx", engine="openpyxl") as w:
        merge_dyf_df().to_excel(w, sheet_name="dyf", index=False)
        merge_wxt_df().to_excel(w, sheet_name="wxt", index=False)
        merge_hc_df().to_excel(w, sheet_name="HC", index=False)

    names = ["P001", "P002", "P003", "P004", "P005"]
    groups = ["SSD", "SSD", "HC", "HC", "SSD"]
    with pd.ExcelWriter(root / "SSD.xlsx", engine="openpyxl") as w:
        img_df(names, 3, "perf", groups, 1).to_excel(w, sheet_name="perfusion", index=False)
        img_df(names, 4, "dti", groups, 2).to_excel(w, sheet_name="DTI", index=False)
        img_df(names, 3, "dki", groups, 3).to_excel(w, sheet_name="DKI", index=False)
        img_df(names, 2, "ivim", groups, 4).to_excel(w, sheet_name="IVIM", index=False)
        clinical_df().to_excel(w, sheet_name="clinical", index=False)

    # split 源：DTI 与 IVIM 的 Name 有交集也有各自独有，且共享 clinical 列
    with pd.ExcelWriter(root / "SSD_imputed.xlsx", engine="openpyxl") as w:
        img_df(names, 3, "perf", groups, 1).to_excel(w, sheet_name="perfusion", index=False)
        dti = img_df(["P001", "P002", "P003"], 4, "dti", ["SSD", "SSD", "HC"], 2)
        dti["age"] = [40, 41, 42]
        dti.to_excel(w, sheet_name="DTI", index=False)
        ivim = img_df(["P002", "P003", "P004"], 2, "ivim", ["SSD", "HC", "HC"], 4)
        ivim["age"] = [41, 99, 44]          # P002 与 DTI 冲突 → merge_qc
        ivim["education_years"] = [10, 11, 12]
        ivim.to_excel(w, sheet_name="IVIM", index=False)
        img_df(names, 3, "dki", groups, 3).to_excel(w, sheet_name="DKI", index=False)

    dti_path = root / "DTI_IVIM_SRC" / "jfl_SSD_DTI_IVIM.xlsx"
    cln = impute_cln_df()
    rad = pd.DataFrame({
        "Patient_ID": cln["Patient_ID"],
        "dti__roi_00": half(np.arange(len(cln), dtype=float)),
        "dti__roi_01": np.arange(len(cln), dtype=float) * 2,
        "ivim__roi_00": half(np.arange(len(cln), dtype=float) * 3),
    })
    with pd.ExcelWriter(dti_path, engine="openpyxl") as w:
        cln.to_excel(w, sheet_name="cln", index=False)
        rad.to_excel(w, sheet_name="rad", index=False)

    print("fixtures built at", root)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
