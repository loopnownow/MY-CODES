# -*- coding: utf-8 -*-
"""c4 case: whitespace-only subject_id on an orphan-candidate source row.

Old `phase1_real_merge` used `pd.notna(sr.get("subject_id"))`, so a '   ' value
counts as present (and `break`s). A port that uses `is_blank` there would instead
skip it. This rewrites 0del/0list.xlsx so the orphan candidate has subject_id='   '.

usage: python make_c4_ws_orphan.py <fixture_root>
"""
import sys
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1])
lst = root / "0del" / "0list.xlsx"

sheets = {}
with pd.ExcelFile(lst) as xl:
    for s in xl.sheet_names:
        sheets[s] = pd.read_excel(lst, sheet_name=s)

ssd = sheets["SSD"]
mask = ssd["n"].astype(str).str.contains("Chen", case=False, na=False)
ssd.loc[mask, "num"] = "   "          # 空白串：pd.notna 为真，is_blank 为假
sheets["SSD"] = ssd
print("patched rows:", int(mask.sum()), "->", ssd.loc[mask, "num"].tolist())

with pd.ExcelWriter(lst, engine="openpyxl") as w:
    for s, d in sheets.items():
        d.to_excel(w, sheet_name=s, index=False)
print("rewritten:", lst)
