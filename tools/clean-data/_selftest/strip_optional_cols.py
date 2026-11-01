# -*- coding: utf-8 -*-
"""Strip the optional columns (height_cm/weight_kg in main, 随访备注 in 0list).

Regression case for the divergence where the old `load_all_sources` produced a
source_long.csv WITHOUT those columns (they only appear when some row has a value),
while a naive port that pre-seeds the column list always emits them as all-NaN.

usage: python strip_optional_cols.py <fixture_root>
"""
import sys
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1])

main = root / "clinical_merged_en.xlsx"
df = pd.read_excel(main, sheet_name=0)
before = list(df.columns)
df = df.drop(columns=[c for c in ("height_cm", "weight_kg") if c in df.columns])
df.to_excel(main, sheet_name="clinical", index=False)
print("main:", len(before), "->", len(df.columns), "cols; dropped height_cm/weight_kg")

lst = root / "0del" / "0list.xlsx"
sheets = {}
with pd.ExcelFile(lst) as xl:
    for s in xl.sheet_names:
        d = pd.read_excel(lst, sheet_name=s)
        if "随访备注" in d.columns:
            d = d.drop(columns=["随访备注"])
        sheets[s] = d
with pd.ExcelWriter(lst, engine="openpyxl") as w:
    for s, d in sheets.items():
        d.to_excel(w, sheet_name=s, index=False)
print("0list: dropped 随访备注 from", list(sheets))
