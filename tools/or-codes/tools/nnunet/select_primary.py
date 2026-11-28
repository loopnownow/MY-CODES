#!/usr/bin/env python
"""Build the ZS phase map and pick per-case primary / check series.

Rules (see the project plan, section 3.5)
  period is derived from _series.tsv series_desc, in this priority order:
    arterial   : 'a-phase' | 'arterial' | desc starts with 'cta'
    venous     : 'v-phase' | 'venous' | 'delay'
    unknown    : 'bodyangio'
    plain      : 'non contrast' | starts with 'native' | starts with abdomen/pelvis (no 'phase')
    plain      : desc == '5.0 x 5.0' and it is the first series of the case (uCT 760 convention)
    unknown    : 'dual phase' | '5.0 x 5.0' repeated without A/V markers | 'body ... ce'
    plain      : starts with 'body' and has no ' ce'
    unknown    : everything else
  pick_primary = smallest period_rank (plain 0 < venous 1 < arterial 2 < unknown 3),
                 tie broken by smallest series_number
  pick_check   = next smallest rank (only when the case has >= 2 series)

Cases without any plain series are flagged PHASE_MISMATCH. With --exclude-no-plain they are
also marked EXCLUDED so downstream staging / inference skips them.

Output: ZS-PHASE-MAP.tsv (read-only over L, writes only the tsv).

Usage
  python select_primary.py                 # write the tsv
  python select_primary.py --exclude-no-plain
  python select_primary.py --stats         # print distribution only, no write
"""
from __future__ import annotations
import os

import argparse
import csv
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

CONFIG = {
    "src_root": os.environ.get("CONFIG", ""),
    "sample_glob": "zjl_CT_ZS_*",
    "out_tsv": r"{lab_root}\drafting\zjl_RDL\ref\code-gates\ZS-PHASE-MAP.tsv",
}
RANK = {"plain": 0, "venous": 1, "arterial": 2, "unknown": 3}


def classify(desc: str, idx: int, n: int) -> tuple[str, str]:
    """Return (period, rule) for one series description."""
    d = (desc or "").strip().lower()
    if "a-phase" in d or "arterial" in d or d.startswith("cta"):
        return "arterial", "kw_arterial"
    if "v-phase" in d or "venous" in d or "delay" in d:
        return "venous", "kw_venous"
    if "bodyangio" in d:
        return "unknown", "kw_bodyangio"
    if ("non contrast" in d) or d.startswith("native"):
        return "plain", "kw_noncontrast"
    if (d.startswith("abdomen") or d.startswith("pelvis")) and "phase" not in d:
        return "plain", "kw_abdomen_plain"
    if d == "5.0 x 5.0":
        if idx == 0:
            return "plain", "uct_first_series"
        return "unknown", "uct_no_marker"
    if "dual phase" in d:
        return "unknown", "kw_dualphase"
    if d.startswith("body") and " ce" not in d:
        return "plain", "kw_body_plain"
    return "unknown", "fallback"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build ZS phase map and pick series")
    ap.add_argument("--src-root", default=CONFIG["src_root"])
    ap.add_argument("--out-tsv", default=CONFIG["out_tsv"])
    ap.add_argument("--exclude-no-plain", action="store_true",
                    help="mark cases without plain series as EXCLUDED")
    ap.add_argument("--stats", action="store_true", help="print distribution, do not write")
    args = ap.parse_args(argv)

    root = Path(args.src_root) / "img"
    cases = sorted(p for p in root.glob(CONFIG["sample_glob"]) if p.is_dir())
    if not cases:
        print(f"[FAIL] no case dirs under {root}")
        return 1

    rows: list[dict] = []
    combo = Counter()
    bucket = Counter()
    for case_dir in cases:
        tsv = case_dir / "_series.tsv"
        if not tsv.exists():
            rows.append({"sample": case_dir.name, "seq": "", "series_number": "",
                         "series_desc": "", "model": "", "thickness": "", "spacing": "",
                         "period": "", "period_rank": "", "rule": "",
                         "pick_primary": 0, "pick_check": 0, "flag": "NO_SERIES_TSV", "note": ""})
            continue
        with open(tsv, encoding="utf-8-sig", newline="") as f:
            series = list(csv.DictReader(f, delimiter="\t"))
        for i, s in enumerate(series):
            period, rule = classify(s.get("series_desc", ""), i, len(series))
            s["_period"], s["_rule"] = period, rule
            bucket[period] += 1
        combo["+".join(sorted(x["_period"] for x in series))] += 1
        ranked = sorted(range(len(series)),
                        key=lambda i: (RANK[series[i]["_period"]], int(series[i].get("series_number") or 10**6)))
        primary_idx = ranked[0]
        check_idx = ranked[1] if len(ranked) >= 2 else None
        has_plain = any(x["_period"] == "plain" for x in series)
        for i, s in enumerate(series):
            flag = ""
            note = ""
            if i == primary_idx:
                note = "primary"
            if check_idx is not None and i == check_idx:
                note = (note + ";check") if note else "check"
            if not has_plain:
                flag = "PHASE_MISMATCH"
                if args.exclude_no_plain:
                    flag = "EXCLUDED_PHASE_MISMATCH"
                    note = ""
            rows.append({
                "sample": case_dir.name, "seq": s.get("seq", ""),
                "series_number": s.get("series_number", ""),
                "series_desc": s.get("series_desc", ""),
                "model": s.get("manufacturer_model", ""),
                "thickness": s.get("thickness", ""), "spacing": s.get("spacing", ""),
                "period": s["_period"], "period_rank": RANK[s["_period"]], "rule": s["_rule"],
                "pick_primary": int((has_plain or not args.exclude_no_plain) and i == primary_idx),
                "pick_check": int((has_plain or not args.exclude_no_plain) and check_idx == i),
                "flag": flag, "note": note,
            })

    print(f"[info] cases {len(cases)}  series {sum(bucket.values())}")
    print(f"[info] series-level periods: {dict(bucket)}")
    print("[info] case-level combos:")
    for k, v in combo.most_common(10):
        print(f"  {v:4d}  {k}")
    n_primary = sum(r["pick_primary"] for r in rows)
    n_check = sum(r["pick_check"] for r in rows)
    n_mismatch = len({r["sample"] for r in rows if r["flag"].startswith(("PHASE_MISMATCH", "EXCLUDED"))})
    print(f"[plan] primary {n_primary}  check {n_check}  inference {n_primary + n_check}  "
          f"cases flagged {n_mismatch}")

    if args.stats:
        print("[stats] nothing written")
        return 0

    out = Path(args.out_tsv)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["sample", "seq", "series_number", "series_desc", "model", "thickness", "spacing",
            "period", "period_rank", "rule", "pick_primary", "pick_check", "flag", "note"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    print(f"[done] {len(rows)} rows -> {out}")
    print(f"[done] at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
