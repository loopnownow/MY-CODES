# -*- coding: utf-8 -*-
"""Check the radiology/clinical text of patients whose auto-ROI came back empty.

Why
    68 of 422 XK exams have an empty nnU-Net prediction. A pre-treatment exam
    cannot be "no tumour", so those are model misses. A post-treatment empty
    exam is much more likely genuine regression. Before we write either number
    into the manuscript we need the text side to say which is which.

Input
    _pairing/roi_pred_lung.csv        collect_pred_table.py --execute
    _clinical/text_index.csv          patient_id, timepoint(q=pre, h=post), txt
    _clinical/text/<pid>_<tp>.txt     extracted report text

Output
    _pairing/post_empty_report_reason.csv
        patient_id, group, report_found, reason, hits

    reason (post) cr_like / shrink_like / progress_like / stable_like /
                  no_lesion_mention / no_hit / no_report
    reason (pre)  lesion_mentioned / no_lesion_mention / no_report

    Only keyword hits are stored; report text never leaves the local disk.

Usage
    python scan_post_empty_reports.py               # dry-run, prints counts
    python scan_post_empty_reports.py --execute
"""
from __future__ import annotations

import argparse
import os

import pandas as pd

CONFIG = {
    "PRED_TABLE": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\roi_pred_lung.csv",
    "TEXT_INDEX": r"{lab_root}\_DataBase\LC\lxf_LUNG\clinical_text\text_index.csv",
    "OUT": r"{lab_root}\pre-submit\lxf_LUNG\ref\data\pairing\post_empty_report_reason.csv",
}

# 词表顺序即优先级：完全消失 > 缩小 > 进展 > 稳定
KW_CR = ["消失", "未见明确", "未见明显", "无明显", "已吸收"]
KW_SHRINK = ["缩小", "变小", "减小"]
KW_PROGRESS = ["增大", "进展", "新发", "增多", "增大增多"]
KW_STABLE = ["相仿", "稳定", "无明显变化"]
KW_LESION = ["病灶", "肿块", "占位", "结节", "肿瘤"]

OUT_COLS = ["patient_id", "group", "report_found", "reason", "hits"]


def read_text(path: str) -> str:
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def hits(text: str, words) -> list:
    return [w for w in words if w in text]


def classify(text: str, group: str) -> tuple:
    """Return (reason, hits string) for one exam."""
    h_cr = hits(text, KW_CR)
    h_shrink = hits(text, KW_SHRINK)
    h_prog = hits(text, KW_PROGRESS)
    h_stable = hits(text, KW_STABLE)
    h_lesion = hits(text, KW_LESION)
    if group == "post_empty":
        if h_cr:
            reason = "cr_like"
        elif h_shrink:
            reason = "shrink_like"
        elif h_prog:
            reason = "progress_like"
        elif h_stable:
            reason = "stable_like"
        elif not h_lesion:
            reason = "no_lesion_mention"
        else:
            reason = "no_hit"
    else:
        reason = "lesion_mentioned" if h_lesion else "no_lesion_mention"
    used = h_cr or h_shrink or h_prog or h_stable or h_lesion
    return reason, ";".join(used[:4])



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="scan reports of empty-ROI exams (default dry-run)")
    ap.add_argument("--pred-table", default=CONFIG["PRED_TABLE"])
    ap.add_argument("--text-index", default=CONFIG["TEXT_INDEX"])
    ap.add_argument("--out", default=CONFIG["OUT"])
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args(argv)

    for p in (args.pred_table, args.text_index):
        if not os.path.exists(p):
            raise SystemExit("[stop] input missing: " + p)

    tab = pd.read_csv(args.pred_table, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    idx = pd.read_csv(args.text_index, dtype=str, encoding="utf-8-sig", keep_default_na=False)
    idx = idx[idx["txt"].str.strip() != ""]
    txt_of = {}
    for r in idx.to_dict("records"):
        tp = "pre" if str(r["timepoint"]).strip().lower() == "q" else "post"
        txt_of[(str(r["patient_id"]).strip(), tp)] = str(r["txt"]).strip()

    rows = []
    for r in tab.to_dict("records"):
        pid = str(r["patient_id"]).strip()
        pre_ok = str(r["pre_vol_mm3"]).strip() != ""
        post_ok = str(r["post_vol_mm3"]).strip() != ""
        if pre_ok and post_ok:
            continue
        group = ("both_empty" if (not pre_ok and not post_ok)
                 else "pre_miss" if not pre_ok else "post_empty")
        for tag in ("pre", "post"):
            if str(r[tag + "_vol_mm3"]).strip() != "":
                continue
            path = txt_of.get((pid, tag), "")
            if not path or not os.path.exists(path):
                rows.append({"patient_id": pid, "group": group, "report_found": 0,
                             "reason": "no_report", "hits": ""})
                continue
            reason, used = classify(read_text(path),
                                    "post_empty" if tag == "post" else "pre_miss")
            rows.append({"patient_id": pid, "group": group, "report_found": 1,
                         "reason": reason, "hits": used})

    out = pd.DataFrame(rows, columns=OUT_COLS)
    print("[input ] %d patients, %d text files indexed" % (len(tab), len(txt_of)))
    print("[target] %d empty exams (%d patients)" % (len(out), out["patient_id"].nunique()))
    for g, sub in out.groupby("group"):
        print("  %-10s n %2d  " % (g, len(sub))
              + ", ".join("%s %d" % (k, v) for k, v in sub["reason"].value_counts().items()))
    n_noreport = int((out["report_found"] == 0).sum())
    print("[report] found %d, missing %d" % (len(out) - n_noreport, n_noreport))

    if not args.execute:
        print("[dry-run] nothing written. add --execute")
        return 0
    if os.path.exists(args.out):
        print("[stop] output exists, not overwritten: " + args.out)
        return 1
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print("[write ] %s  %d rows" % (args.out, len(out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
