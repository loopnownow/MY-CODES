# -*- coding: utf-8 -*-
"""NaT case: a datetime64 column (exam_date) with missing values.

`pd.isna(NaT)` is True but `is_blank(NaT)` is False, so a port that swapped the old
`pd.isna` check for `is_blank` silently stops back-filling date columns from the
sources. This case makes exam_date a real datetime column with NaT so the two
behaviours can be compared.

usage: python make_nat_case.py <fixture_root>
"""
import sys
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1])
main = root / "clinical_merged_en.xlsx"

df = pd.read_excel(main, sheet_name=0)
df["exam_date"] = pd.to_datetime(df["exam_date"], errors="coerce")
nan_idx = df.index[df["name_en"].isin(["ZHANG SAN", "LI SI"])]
df.loc[nan_idx, "exam_date"] = pd.NaT
df.to_excel(main, sheet_name="clinical", index=False)

print("exam_date dtype:", pd.read_excel(main, sheet_name=0)["exam_date"].dtype)
print("NaT rows:", df.loc[nan_idx, "name_en"].tolist())
