# -*- coding: utf-8 -*-
"""Build the patient-level ROI QC ledger for the XK (chest hospital) cohort.

Why
    nnU-Net produced 422 auto-ROIs but XK has no manual ground truth, and the
    cohort carries a real domain shift: the 5 mm recon is a local thin-slab
    (z coverage median 86 mm vs 315 mm in the training cohorts). Before any
    delta-habitat feature is computed we need one frozen table that says, per
    patient, whether the pre/post pair is usable and, if not, why.

Inputs
    _pairing/roi_pred_lung.csv             collect_pred_table.py --execute
    qc_roi_jpg/roi_review_index.csv        render_roi_review_jpg.py (flag, n_slices, jpg)
    _pairing/roi_components.csv            select_primary_lesion.py --execute
    predTs_primary/<pid>_pre|post.nii.gz   only read for cases flagged EDGE_Z
    _pairing/post_empty_report_reason.csv  optional, scan_post_empty_reports.py

Outputs
    _pairing/lung_roi_qc.csv       one row per patient (211)
    _pairing/roi_review_todo.csv   review queue sorted by priority

Column rules
    empty_class          none / pre_miss / post_empty / both_empty
    edge_ends_pre/post   0/1/2 z-ends touched by the kept mask; 2 = likely truncated slab
    decision             use / exclude / pending
    review_priority      1 = pre_miss or both_empty, 2 = EDGE_Z, 3 = multi_review, '' = clean
    hash_id              sha256(patient_id)[:8] for tables that must not carry the MRN

Usage
    python build_lung_roi_qc.py                  # dry-run, prints the summary only
    python build_lung_roi_qc.py --execute        # write both CSVs
"""
from __future__ import annotations

import argparse
import hashlib
import os

import pandas as pd
import SimpleITK as sitk

CONFIG = {
    "PRED_TABLE": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\roi_pred_lung.csv",
    "QC_INDEX": os.environ.get("CONFIG", ""),
    "PRED_DIR": os.environ.get("CONFIG_2", ""),
    "REASON_CSV": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\post_empty_report_reason.csv",
    "OUT_QC": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\lung_roi_qc.csv",
    "OUT_TODO": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\roi_review_todo.csv",
    # 250 cm3 = 250,000 mm3；超过即视为可疑（假阳吞并肺/实变），只标复核不自动排除
    "VOL_MAX_MM3": 250000,
}

VOL_MAX_MM3 = CONFIG["VOL_MAX_MM3"]

QC_COLS = [
    "patient_id", "hash_id", "archive", "interval_days",
    "pre_n_slices", "post_n_slices", "pre_vol_mm3", "post_vol_mm3", "vol_ratio",
    "pre_flag", "post_flag", "pre_n_comp", "post_n_comp", "pre_n_kept", "post_n_kept",
    "edge_ends_pre", "edge_ends_post", "multi_review",
    "empty_class", "post_empty_reason", "decision", "review_priority", "note",
]

TODO_COLS = ["priority", "patient_id", "case", "exam", "reason",
             "flag", "vol_mm3", "n_comp", "jpg"]


def hash_id(pid: str) -> str:
    return hashlib.sha256(pid.encode("utf-8")).hexdigest()[:8]


def count_edge_ends(mask_path: str) -> str:
    """Number of z-ends (first / last slice) touched by the mask: 0 / 1 / 2."""
    if not os.path.exists(mask_path):
        return ""
    arr = sitk.GetArrayFromImage(sitk.ReadImage(mask_path)) > 0
    if not arr.any():
        return "0"
    return str(int(arr[0].any()) + int(arr[-1].any()))


def load_optional_reason(path: str) -> dict:
    """patient_id -> post_empty_reason, if the report scan has been run."""
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    key = "patient_id" if "patient_id" in df.columns else df.columns[0]
    val = "post_empty_reason" if "post_empty_reason" in df.columns else df.columns[-1]
    return {str(r[key]).strip(): str(r[val]).strip() for r in df.to_dict("records")}


def classify(row) -> tuple:
    """Return (empty_class, decision, review_priority, note)."""
    pre_ok = str(row["pre_vol_mm3"]).strip() != ""
    post_ok = str(row["post_vol_mm3"]).strip() != ""
    if not pre_ok and not post_ok:
        empty_class = "both_empty"
    elif not pre_ok:
        empty_class = "pre_miss"
    elif not post_ok:
        empty_class = "post_empty"
    else:
        empty_class = "none"

    notes = []
    if empty_class != "none":
        notes.append("no ROI on " + ("both exams" if empty_class == "both_empty"
                                     else "pre" if empty_class == "pre_miss" else "post"))

    if empty_class in ("both_empty", "pre_miss", "post_empty"):
        priority = 1
        decision = "exclude"
    elif int(row["edge_ends_pre"] or 0) >= 2 or int(row["edge_ends_post"] or 0) >= 2:
        priority = 2
        decision = "pending"
        notes.append("mask touches both z-ends (likely truncated slab)")
    elif "EDGE_Z" in (str(row["pre_flag"]) + str(row["post_flag"])):
        priority = 2
        decision = "pending"
        notes.append("mask touches one z-end")
    elif VOL_MAX_MM3 and max(float(row["pre_vol_mm3"] or 0), float(row["post_vol_mm3"] or 0)) > VOL_MAX_MM3:
        priority = 2
        decision = "pending"
        notes.append("volume >%d cm3 (implausible, review)" % int(VOL_MAX_MM3 // 1000))
    elif int(row["multi_review"]) == 1:
        priority = 3
        decision = "use"
        notes.append("secondary blob >=25% of largest, review later")
    else:
        priority = ""
        decision = "use"

    if int(row["pre_n_comp"] or 0) >= 2 or int(row["post_n_comp"] or 0) >= 2:
        notes.append("multi-component raw pred")
    return empty_class, decision, priority, "; ".join(notes)


def build_rows(tab, qc_idx, reasons, pred_dir):
    rows, todos = [], []
    for r in tab.to_dict("records"):
        pid = str(r["patient_id"]).strip()
        out = {"patient_id": pid, "hash_id": hash_id(pid),
               "archive": r["archive"], "interval_days": r["interval_days"],
               "pre_vol_mm3": r["pre_vol_mm3"], "post_vol_mm3": r["post_vol_mm3"],
               "vol_ratio": r["vol_ratio"],
               "pre_flag": r["pre_flag"], "post_flag": r["post_flag"],
               "pre_n_comp": r["pre_n_comp"], "post_n_comp": r["post_n_comp"],
               "pre_n_kept": r["pre_n_kept"], "post_n_kept": r["post_n_kept"],
               "multi_review": r["multi_review"]}
        for tag in ("pre", "post"):
            case = pid + "_" + tag
            out[tag + "_n_slices"] = (qc_idx.get(case) or {}).get("n_slices", "")
            out["edge_ends_" + tag] = (count_edge_ends(os.path.join(pred_dir, case + ".nii.gz"))
                                       if "EDGE_Z" in r[tag + "_flag"] else "0")

        out["post_empty_reason"] = reasons.get(pid, "")
        empty_class, decision, priority, note = classify(out)
        if empty_class == "post_empty" and out["post_empty_reason"]:
            note = (note + "; report: " + out["post_empty_reason"]).strip("; ")
        out.update({"empty_class": empty_class, "decision": decision,
                    "review_priority": priority, "note": note})
        rows.append(out)

        if priority == "":
            continue
        for tag in ("pre", "post"):
            case = pid + "_" + tag
            reason = []
            if str(out[tag + "_vol_mm3"]).strip() == "":
                reason.append("EMPTY")
            if int(out["edge_ends_" + tag] or 0) >= 2:
                reason.append("EDGE_Z2")
            if int(out[tag + "_n_comp"] or 0) >= 2:
                reason.append("MULTI%d" % int(out[tag + "_n_comp"]))
            if not reason:
                continue
            if tag == "post" and out["post_empty_reason"]:
                reason.append("report:" + out["post_empty_reason"])
            todos.append({"priority": priority, "patient_id": pid, "case": case,
                          "exam": tag, "reason": "+".join(reason),
                          "flag": out[tag + "_flag"], "vol_mm3": out[tag + "_vol_mm3"],
                          "n_comp": out[tag + "_n_comp"],
                          "jpg": (qc_idx.get(case) or {}).get("jpg", "")})
    return rows, todos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build lung ROI QC ledger (default dry-run)")
    ap.add_argument("--pred-table", default=CONFIG["PRED_TABLE"])
    ap.add_argument("--qc-index", default=CONFIG["QC_INDEX"])
    ap.add_argument("--pred-dir", default=CONFIG["PRED_DIR"])
    ap.add_argument("--reason-csv", default=CONFIG["REASON_CSV"])
    ap.add_argument("--out-qc", default=CONFIG["OUT_QC"])
    ap.add_argument("--out-todo", default=CONFIG["OUT_TODO"])
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.pred_table, args.qc_index):
        if not os.path.exists(p):
            raise SystemExit("[stop] input missing: " + p)

    tab = pd.read_csv(args.pred_table, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    qc = pd.read_csv(args.qc_index, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    qc_idx = {r["case"]: r for r in qc.to_dict("records")}
    reasons = load_optional_reason(args.reason_csv)

    rows, todos = build_rows(tab, qc_idx, reasons, args.pred_dir)
    qc_out = pd.DataFrame(rows, columns=QC_COLS)
    todo_out = (pd.DataFrame(todos, columns=TODO_COLS)
                .sort_values(["priority", "patient_id", "exam"], kind="stable"))

    usable = int(qc_out["decision"].isin(["use", "pending"]).sum())
    print("[input ] %d patients, %d exams in qc index" % (len(qc_out), len(qc)))
    print("[usable] decision use+pending %d / %d" % (usable, len(qc_out)))
    print("[class ] " + ", ".join("%s %d" % (k, v) for k, v in
                                  qc_out["empty_class"].value_counts().items()))
    print("[decide] " + ", ".join("%s %d" % (k, v) for k, v in
                                  qc_out["decision"].value_counts().items()))
    prio = qc_out["review_priority"].replace("", "clean").value_counts()
    print("[review] " + ", ".join("%s %d" % (k, v) for k, v in prio.items()))
    e1 = int((qc_out["edge_ends_pre"] == "1").sum() + (qc_out["edge_ends_post"] == "1").sum())
    edge2 = int((qc_out["edge_ends_pre"] == "2").sum() + (qc_out["edge_ends_post"] == "2").sum())
    print("[edge   ] masks touching one / both z-ends: %d / %d exams" % (e1, edge2))
    print("[todo   ] %d exam rows" % len(todo_out))
    if not reasons:
        print("[note   ] post_empty_report_reason.csv absent, reasons left blank")

    if not args.execute:
        print("[dry-run] nothing written. add --execute")
        return 0
    for p in (args.out_qc, args.out_todo):
        if os.path.exists(p):
            print("[stop] output exists, not overwritten: " + p)
            return 1
    qc_out.to_csv(args.out_qc, index=False, encoding="utf-8-sig")
    todo_out.to_csv(args.out_todo, index=False, encoding="utf-8-sig")
    print("[write ] %s  %d rows" % (args.out_qc, len(qc_out)))
    print("[write ] %s  %d rows" % (args.out_todo, len(todo_out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
