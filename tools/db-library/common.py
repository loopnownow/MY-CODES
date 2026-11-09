# -*- coding: utf-8 -*-
"""db-library 公共件：哈希 / 归一化 / 读表 / 写工作簿 / 台账。

只放三个 CLI 共用的最小函数；去重、DDL、宽表规则仍留在 build_code_db.py。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def sha256(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def sha256_12(p):
    return sha256(p)[:12]


def norm(v):
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def read_any(p):
    """返回 [(sheet_name, rows: list[list[str]])]，尾部空行裁掉。

    .csv -> utf-8-sig 再退 gbk；.xls -> xlrd（失败按文本 TSV/CSV 回退）；
    .xlsx -> openpyxl read_only。
    """
    p = Path(p)
    out = []
    if p.suffix.lower() == ".csv":
        rows = []
        for enc in ("utf-8-sig", "gbk"):
            try:
                with p.open(encoding=enc) as f:
                    rows = [[norm(c) for c in line.rstrip("\n").split(",")] for line in f]
                break
            except Exception:
                rows = []
        out.append(("csv", rows))
    elif p.suffix.lower() == ".xls":
        import xlrd
        try:
            b = xlrd.open_workbook(str(p))
            for s in b.sheets():
                out.append((s.name, [[norm(s.cell_value(r, c)) for c in range(s.ncols)]
                                     for r in range(s.nrows)]))
        except Exception:
            # 伪装成 .xls 的文本（TSV/CSV）
            txt = ""
            for enc in ("utf-8-sig", "gbk", "latin-1"):
                try:
                    txt = p.read_text(encoding=enc)
                    break
                except Exception:
                    pass
            sep = "\t" if "\t" in txt.splitlines()[0] else ","
            out.append(("txt", [[norm(c) for c in ln.split(sep)]
                                 for ln in txt.splitlines() if ln.strip()]))
    else:
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        for ws in wb.worksheets:
            rows = [[norm(v) for v in r] for r in ws.iter_rows(values_only=True)]
            out.append((ws.title, rows))
        wb.close()
    for i, (n, rows) in enumerate(out):
        while rows and not any(rows[-1]):
            rows.pop()
        out[i] = (n, rows)
    return out


def write_workbook(path, sheets):
    """sheets: [{'name','note','header','rows'}] -> xlsx（write_only）。"""
    import openpyxl
    wb = openpyxl.Workbook(write_only=True)
    seen = set()
    for s in sheets:
        name = s["name"][:31]
        k = 1
        base = name
        while name in seen:
            name = f"{base[:28]}_{k}"
            k += 1
        seen.add(name)
        ws = wb.create_sheet(name)
        if s.get("note"):
            ws.append([s["note"]])
        if s.get("header"):
            ws.append(list(s["header"]))
        for r in s["rows"]:
            ws.append(list(r))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return path


def ledger_write(path, header, rows):
    """写 TSV 台账（UTF-8，\\n 行尾；父目录自动创建）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    return path
