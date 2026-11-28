"""SkinVL-MM 诊断评估：以病理诊断为金标准，9 类体系。

三层口径（用户已确认）：
  主  二分类：恶性 = BCC+SCC+MM+AK；良性 = NV+CY+SK+BN；UN 排除
  次  多分类：9 类混淆矩阵 + 宏平均指标
  附  MM 单列（n 很小，只做病例描述）

同时输出 MedGemma（main_diagnosis，22 类）折叠到同一 9 类体系后的指标，
保证两个模型在相同口径下可比。

输入：infer_batch.py 的 diagnosis 模式输出（含 name / label）
输出：xlsx，含 逐例对照表、指标表、混淆矩阵三个 sheet
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)

LABELS = ["BCC", "SCC", "MM", "NV", "CY", "SK", "AK", "BN", "UN"]
MALIGNANT = {"BCC", "SCC", "MM", "AK"}
BENIGN = {"NV", "CY", "SK", "BN"}

# ---------------------------------------------------------------------------
# 中文病理诊断 -> 9 类。规则顺序即优先级，先剥"补充诊断"、再截主诊断短语。
# 全部样本（4044）都能落到某类，未匹配落到 BN。
# ---------------------------------------------------------------------------
PATHOLOGY_RULES: list[tuple[str, list[str]]] = [
    ("MM", ["恶性黑色素瘤", "肢端黑色素瘤", "黑色素瘤", "恶性黑素瘤", "黑素瘤"]),
    ("SCC", ["鳞状细胞癌", "鳞癌", "鲍恩病", "bowen", "原位鳞"]),
    ("BCC", ["基底细胞癌", "基底细胞上皮瘤"]),
    ("AK", ["日光性角化", "光化性角化"]),
    ("SK", ["脂溢性角化"]),
    ("CY", ["表皮样囊肿", "表皮囊肿", "皮脂腺囊肿", "囊肿"]),
    ("NV", ["痣"]),
]
UN_MARKERS = ["待查", "无法明确", "需做免疫组化", "需免疫组化", "请结合临床"]

# ---------------------------------------------------------------------------
# MedGemma main_diagnosis（22 类英文）-> 9 类。
# 注意：skin lesion / nodule / skin cancer 等是描述性词而非诊断，统一归 UN，
# 否则会把"没给出诊断"算成正确。
# ---------------------------------------------------------------------------
MEDGEMMA_MAP: dict[str, str] = {
    "mole": "NV",
    "nevus": "NV",
    "melanoma": "MM",
    "basal cell carcinoma": "BCC",
    "squamous cell carcinoma": "SCC",
    "cyst": "CY",
    "seborrheic keratosis": "SK",
    "actinic keratosis": "AK",
    "skin tag": "BN",
    "wart": "BN",
    "psoriasis": "BN",
    "eczema": "BN",
    "dermatitis": "BN",
    "acne": "BN",
    "pimple": "BN",
    "ringworm": "BN",
    "skin lesion": "UN",
    "skin cancer": "UN",
    "unknown": "UN",
    "nodule": "UN",
    "pustule": "UN",
    "hyperpigmented lesion": "UN",
}


def prep_pathology(text: str) -> str:
    s = "" if text is None else str(text).strip()
    if "补充诊断" in s:
        s = s.split("补充诊断")[-1]
    s = s.lstrip("：:")
    for sep in ["\r", "\n", "。", "，", ";", "；"]:
        if sep in s:
            s = s.split(sep)[0]
    s = re.sub(r"^（[^）]*）", "", s)
    s = re.sub(r"^\([^)]*\)", "", s)
    return s.replace(" ", "").strip()


def map_pathology(text: str) -> str:
    t = prep_pathology(text)
    if t in ("", "#N/A", "nan"):
        return "UN"
    low = t.lower()
    for label, keys in PATHOLOGY_RULES:
        if any(k in low for k in keys):
            return label
    if any(k in t for k in UN_MARKERS):
        return "UN"
    return "BN"


def map_medgemma(text: str) -> str:
    key = "" if text is None else str(text).strip().lower()
    return MEDGEMMA_MAP.get(key, "UN")


def binary_metrics(y_true: list[int], y_pred: list[int]) -> dict:
    """恶性=1 / 良性=0 的二分类指标。"""
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    sens = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    ppv = tp / (tp + fp) if (tp + fp) else float("nan")
    npv = tn / (tn + fn) if (tn + fn) else float("nan")
    return {
        "n": len(y_true),
        "TP": int(tp),
        "FP": int(fp),
        "FN": int(fn),
        "TN": int(tn),
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "sensitivity": round(sens, 4),
        "specificity": round(spec, 4),
        "ppv": round(ppv, 4),
        "npv": round(npv, 4),
        "f1": round(f1_score(y_true, y_pred, zero_division=0), 4),
    }


def multiclass_metrics(y_true: list[str], y_pred: list[str], labels: list[str]):
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=labels, zero_division=0)
    per_class = pd.DataFrame(
        {"label": labels, "precision": p.round(4), "recall": r.round(4), "f1": f.round(4), "support": s}
    )
    overall = {
        "n": len(y_true),
        "accuracy": round(accuracy_score(y_true, y_pred), 4),
        "macro_f1": round(float(f.mean()), 4),
        "weighted_f1": round(
            f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0), 4
        ),
    }
    return overall, per_class


def to_bin(label: str):
    if label in MALIGNANT:
        return 1
    if label in BENIGN:
        return 0
    return None


def build_merged(pred: pd.DataFrame, label_xlsx: Path, sheet: str, image_col: str, label_col: str, mg_col: str) -> pd.DataFrame:
    lab = pd.read_excel(label_xlsx, sheet_name=sheet)
    for col in (image_col, label_col):
        if col not in lab.columns:
            raise SystemExit(f"标注表缺列 {col!r}")
    keep = [image_col, label_col] + ([mg_col] if mg_col in lab.columns else [])
    lab = lab[keep].copy()
    lab["name"] = lab[image_col].astype(str).map(lambda s: Path(str(s).strip()).name)
    lab["name_key"] = lab["name"].str.lower()
    lab["path_label_raw"] = lab[label_col].astype(str)
    lab["path_label"] = lab[label_col].map(map_pathology)
    lab["medgemma_label"] = (
        lab[mg_col].map(map_medgemma) if mg_col in lab.columns else "UN"
    )
    lab = lab.drop_duplicates("name_key")

    pred = pred.copy()
    pred["name_key"] = pred["name"].astype(str).str.lower()
    out = pred.merge(
        lab[["name_key", "path_label_raw", "path_label", "medgemma_label"]],
        on="name_key",
        how="left",
    )
    out["skinvl_label"] = out["label"].astype(str).str.upper().str.strip()
    out["agree_path"] = out["skinvl_label"] == out["path_label"]
    out["agree_medgemma"] = out["medgemma_label"] == out["path_label"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SkinVL-MM vs pathology ground truth")
    ap.add_argument("--pred", required=True, help="infer_batch.py 的 diagnosis 模式输出")
    ap.add_argument("--label-xlsx", default=r"{lab_root}\pre-submit\mlh_SIX\data\wj_SC.xlsx")
    ap.add_argument("--label-sheet", default="jpgnames")
    ap.add_argument("--image-col", default="files")
    ap.add_argument("--label-col", default="病理诊断")
    ap.add_argument("--medgemma-col", default="main_diagnosis")
    ap.add_argument("--out", default=r"{lab_root}\pre-submit\mlh_SIX\results\skinvl\skinvl_eval.xlsx")
    args = ap.parse_args()

    pred = pd.read_excel(args.pred)
    for col in ("name", "label", "status"):
        if col not in pred.columns:
            raise SystemExit(f"预测表缺列 {col!r}")
    pred = pred[pred["status"].astype(str).str.lower() == "ok"].copy()
    if pred.empty:
        raise SystemExit("预测表没有 status=ok 的行")

    merged = build_merged(
        pred, Path(args.label_xlsx), args.label_sheet, args.image_col, args.label_col, args.medgemma_col
    )
    n_missing_truth = int(merged["path_label"].isna().sum())

    # 主口径：二分类，排除任一方 UN
    sub = merged[merged["path_label"].isin(MALIGNANT | BENIGN)].copy()
    sub["y_true"] = sub["path_label"].map(to_bin)
    rows = []

    sv = sub[sub["skinvl_label"].isin(MALIGNANT | BENIGN)].copy()
    if len(sv) >= 2:
        m = binary_metrics(sv["y_true"].astype(int).tolist(), sv["skinvl_label"].map(to_bin).astype(int).tolist())
        m["model"] = "SkinVL-MM"
        rows.append(m)
    else:
        print(f"[warn] SkinVL 二分类可用样本 {len(sv)} < 2")

    mg = sub[sub["medgemma_label"].isin(MALIGNANT | BENIGN)].copy()
    if len(mg) >= 2:
        m = binary_metrics(mg["y_true"].astype(int).tolist(), mg["medgemma_label"].map(to_bin).astype(int).tolist())
        m["model"] = "MedGemma"
        rows.append(m)
    binary_df = pd.DataFrame(rows)

    # 次口径：多分类
    mc_rows = []
    for model, col in (("SkinVL-MM", "skinvl_label"), ("MedGemma", "medgemma_label")):
        d = merged[merged[col].isin(LABELS)]
        if len(d) < 2:
            continue
        overall, _ = multiclass_metrics(d["path_label"].tolist(), d[col].tolist(), LABELS)
        overall["model"] = model
        mc_rows.append(overall)
    multiclass_df = pd.DataFrame(mc_rows)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        cols = [
            "name", "path_label", "path_label_raw", "skinvl_label",
            "medgemma_label", "agree_path", "agree_medgemma", "answer",
        ]
        merged[[c for c in cols if c in merged.columns]].to_excel(
            writer, sheet_name="per_case", index=False
        )
        binary_df.to_excel(writer, sheet_name="binary", index=False)
        multiclass_df.to_excel(writer, sheet_name="multiclass", index=False)
        row = 0
        for model, col in (("SkinVL-MM", "skinvl_label"), ("MedGemma", "medgemma_label")):
            d = merged[merged[col].isin(LABELS)]
            if len(d) < 2:
                continue
            cm = confusion_matrix(d["path_label"], d[col], labels=LABELS)
            cm_df = pd.DataFrame(
                cm, index=[f"true_{l}" for l in LABELS], columns=[f"pred_{l}" for l in LABELS]
            )
            cm_df.to_excel(writer, sheet_name="confusion", startrow=row)
            writer.sheets["confusion"].cell(row=row + 1, column=len(LABELS) + 2, value=model)
            row += len(LABELS) + 3

    print(f"[eval] 预测 n={len(merged)}，缺金标准 {n_missing_truth}")
    print(binary_df.to_string(index=False) if len(binary_df) else "binary: 无可用样本")
    print(multiclass_df.to_string(index=False) if len(multiclass_df) else "multiclass: 无可用样本")
    print(f"[eval] 输出: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


