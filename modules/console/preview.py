"""控制台工作簿预览层（B4c3：从 console.py 逐字搬出）。

依赖 schema / schema_ui（静态表）、settings_layer（路径）与 state（预览缓存）；
不 import console，也不 import http_impl；console 反向回导本模块的同名符号。
运行: 由 console.py / http_impl.py 导入，不单独执行。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from modules.console import state as _state
from modules.console.schema import _PREVIEW_HEAD_MAX_COLS, _SHEET_ALIASES
from modules.console.schema_ui import (
    _as_name_list,
    _columns_open_for_analysis,
    _id_dropdown_columns,
    _measure_cols,
    _rad_meta_cols,
    _uniq_keep,
)
from modules.console.settings_layer import under_root

_PREVIEW_CACHE = _state.PREVIEW_CACHE  # 与 console 同一份 dict（测试断言 identity）

def project_xlsx_names(proj: Path) -> list[str]:
    return [
        p.name for p in sorted(proj.glob("*.xlsx"))
        if p.is_file() and not p.name.startswith("~")
    ]

def _exclude_cell_text(raw) -> str:
    if raw is None:
        return ""
    try:
        if raw != raw:  # NaN
            return ""
    except Exception as e:
        print(f"  ⚠ exclude cell NaN check: {e}", flush=True)
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""
    return s

def xlsx_meta(path: Path, sheet: str = "", col: str = "") -> dict[str, Any]:
    import pandas as pd

    out: dict[str, Any] = {
        "file": str(path),
        "exists": path.is_file(),
        "sheets": [],
        "columns": [],
        "values": [],
        "sheet": "",
        "col": "",
    }
    if not path.is_file():
        return out
    xl = pd.ExcelFile(path)
    sheets = [str(s) for s in xl.sheet_names]
    out["sheets"] = sheets
    if not sheet:
        return out
    hit = sheet if sheet in sheets else next(
        (s for s in sheets if s.lower() == sheet.lower()), None
    )
    if hit is None:
        return out
    df0 = xl.parse(hit, nrows=0)
    cols = [str(c) for c in df0.columns]
    out["columns"] = cols
    out["sheet"] = hit
    if not col:
        return out
    use = col if col in df0.columns else (cols[0] if cols else None)
    if use is None:
        return out
    df = xl.parse(hit, usecols=[use])
    counts: dict[str, int] = {}
    order: list[str] = []
    for v in df[use].tolist():
        s = _exclude_cell_text(v)
        if not s:
            continue
        if s not in counts:
            counts[s] = 0
            order.append(s)
        counts[s] += 1
    order.sort(key=lambda x: (-counts[x], x))
    out["values"] = order[:80]
    out["col"] = str(use)
    return out

def exclude_select_options(
    proj: Path,
    settings: dict[str, Any],
    data_path: Path | None,
    preview: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    files = project_xlsx_names(proj)
    meta = (preview or {}).get("exclude") or {}
    sheets = list(meta.get("sheets") or [])
    cols = list(meta.get("columns") or [])
    rows = list(meta.get("values") or [])
    if not sheets and preview:
        sheets = list(preview.get("sheets") or [])
    if not cols:
        sh = str(settings.get("EXCLUDE_SHEET") or "").strip()
        info = ((preview or {}).get("sheet_info") or {}).get(sh) or {}
        cols = list(info.get("columns") or [])
        if not cols and sh and sh == str(settings.get("CLIN_SHEET") or "").strip():
            cols = list((preview or {}).get("clin_cols") or [])
    from modules.config.ini_io import name_choice
    packed_files = name_choice(settings.get("EXCLUDE_FILE"))
    cur_files = packed_files if isinstance(packed_files, list) else (
        [packed_files] if packed_files else []
    )
    for cur_file in reversed(cur_files):
        base = Path(str(cur_file)).name
        if base and base not in files:
            files = [base] + files
    return {
        "EXCLUDE_FILE": files,
        "EXCLUDE_SHEET": sheets,
        "EXCLUDE_COL": cols,
        "EXCLUDE_ROW": rows,
    }

def _info_from_frame(df, name: str, max_rows: int = 12) -> dict[str, Any]:
    """Preview rows for a sheet already in memory. Every column is included."""
    all_cols = [str(c) for c in df.columns]
    n = len(df)
    miss = {
        str(c): (0 if n == 0 else int(df[c].isna().sum()))
        for c in df.columns
    }
    head = []
    for _, row in df.head(max_rows).iterrows():
        rec = {}
        for c in df.columns:
            v = row[c]
            if v is None or (isinstance(v, float) and v != v):
                rec[str(c)] = None
            else:
                s = str(v)
                rec[str(c)] = s if len(s) < 80 else s[:77] + "..."
        head.append(rec)
    return {
        "name": name,
        "n_rows": int(n),
        "n_cols": len(all_cols),
        "columns": all_cols,
        "missing": miss,
        "head": head,
    }

def resolve_sheet_name(name: str, sheets: list[str]) -> str | None:
    """Match a configured sheet to a workbook name (case, cln↔clin)."""
    raw = str(name or "").strip()
    if not raw:
        return None
    if raw in sheets:
        return raw
    low = raw.lower()
    for s in sheets:
        if s.lower() == low:
            return s
    for alt in _SHEET_ALIASES.get(low, ()):
        for s in sheets:
            if s.lower() == alt.lower():
                return s
    return None

def _preview_one_workbook(data_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    import pandas as pd
    from modules.utils.xlsx_sheet import read_columns, read_header, read_sheet, workbook_info

    data_path = under_root(data_path)
    try:
        book = workbook_info(data_path)
    except Exception:
        book = []
    sheets = [str(s["name"]) for s in book]
    size = {str(s["name"]): s for s in book}
    if not sheets:
        xl = pd.ExcelFile(data_path)
        sheets = [str(s) for s in xl.sheet_names]
        size = {}
    frames: dict[str, pd.DataFrame] = {}
    headers: dict[str, list[str]] = {}

    def _wide(name: str) -> bool:
        return int((size.get(name) or {}).get("n_cols") or 0) > 40

    def _frame(name: str) -> pd.DataFrame:
        got = frames.get(name)
        if got is None:
            got = read_sheet(data_path, name)
            frames[name] = got
        return got

    def _header(name: str) -> list[str]:
        got = headers.get(name)
        if got is None:
            if _wide(name):
                got = read_header(data_path, name)
            else:
                got = [str(c) for c in _frame(name).columns]
            headers[name] = got
        return got

    def _cols(name: str, columns: list[str]) -> pd.DataFrame:
        if _wide(name):
            return read_columns(data_path, name, columns)
        df = _frame(name)
        use = [c for c in columns if c in df.columns]
        return df.loc[:, use] if use else df.iloc[:, 0:0]

    info: dict[str, Any] = {}
    for sname in sheets:
        meta = size.get(sname) or {}
        info[sname] = {
            "name": sname,
            "n_rows": int(meta.get("n_rows") or 0),
            "n_cols": int(meta.get("n_cols") or 0),
            "columns": [],
            "missing": {},
            "head": [],
        }

    clin_sheet = str(settings.get("CLIN_SHEET") or "").strip()
    rad_sheet = str(settings.get("RAD_SHEET") or "").strip()
    qc_sheet = str(settings.get("QC_SHEET") or "").strip()
    icc_sheet = str(settings.get("ICC_SHEET") or "").strip()
    exc_sheet = str(settings.get("EXCLUDE_SHEET") or "").strip()
    clin_id = str(settings.get("CLIN_ID_COL") or "PatientID")
    rad_id = str(settings.get("RAD_ID_COL") or "patient_id")

    def _hit(name: str) -> str | None:
        return resolve_sheet_name(name, sheets)

    clin_cols: list[str] = []
    rad_id_cols: list[str] = []
    icc_cols: list[str] = []
    outcome_dist: dict[str, Any] = {}
    id_check: dict[str, Any] = {}
    exclude: dict[str, Any] = {"n": 0, "ids": []}
    qc_sum: dict[str, Any] = {}
    icc_sum: dict[str, Any] = {}

    def _id_col(df) -> str | None:
        for cand in (
            settings.get("RAD_ID_COL"), settings.get("CLIN_ID_COL"),
            "Name", "PatientID", "patient_id", "Patient_ID", "ID", "Unnamed: 0",
        ):
            if cand and cand in df.columns:
                return str(cand)
        if len(df.columns) and not str(df.columns[0]).startswith("h"):
            return str(df.columns[0])
        return None

    def _levels_from_id(df) -> tuple[list[str], dict[str, int]]:
        col = _id_col(df)
        if not col:
            return [], {}
        sep = str(settings.get("GROUP_FROM_ID_SEP") or "_")
        pref = df[col].astype(str).str.strip().str.split(sep, n=1).str[0]
        vc = pref.value_counts()
        return [str(x) for x in vc.index.tolist()], {str(k): int(v) for k, v in vc.to_dict().items()}

    ch = _hit(clin_sheet)
    group_levels: list[str] = []
    clin_dist: dict[str, Any] = {}
    preview_group_source = ""
    outcomes = [str(c) for c in (settings.get("OUTCOME_COLS") or []) if str(c).strip()]
    cdf = None
    if ch:
        cdf = _frame(ch)
        if len(cdf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[ch] = _info_from_frame(cdf, ch)
        else:
            info[ch]["n_rows"] = int(len(cdf))
            info[ch]["n_cols"] = int(len(cdf.columns))
        clin_cols = [str(c) for c in cdf.columns]
        for col in clin_cols:
            vc = cdf[col].value_counts(dropna=False).to_dict()
            clin_dist[col] = {str(k): int(v) for k, v in vc.items()}
        seen = set()
        for col in outcomes:
            if col in seen or col not in cdf.columns:
                continue
            seen.add(col)
            outcome_dist[col] = dict(clin_dist.get(col) or {})
        gcol = None
        sub = str(settings.get("SUBGROUP_COL") or "").strip()
        src = str(settings.get("GROUP_SOURCE_COL") or "").strip()
        if sub and sub in cdf.columns:
            gcol = sub
        elif src and src in cdf.columns:
            gcol = src
        elif len(outcomes) == 1 and outcomes[0] in cdf.columns:
            gcol = outcomes[0]
        elif "Group" in cdf.columns:
            gcol = "Group"
        elif "group" in cdf.columns:
            gcol = "group"
        if gcol:
            group_levels = [str(x) for x in cdf[gcol].dropna().astype(str).unique().tolist()]
            preview_group_source = gcol
        elif settings.get("GROUP_FROM_ID"):
            group_levels, vc_id = _levels_from_id(cdf)
            if vc_id:
                outcome_dist["Group(from ID)"] = vc_id
                preview_group_source = "Group(from ID)"

    rad_meta_cols: list[str] = []
    id_options: list[str] = []
    rh = _hit(rad_sheet)
    rad_all: list[str] = []
    if rh:
        rad_all = _header(rh)
        info[rh]["n_cols"] = len(rad_all) or info[rh]["n_cols"]
        info[rh]["columns"] = rad_all[:16] if len(rad_all) > 40 else rad_all
        rad_meta_cols = _rad_meta_cols(rad_all)
        rad_id_cols = _id_dropdown_columns(rad_all, str(settings.get("RAD_ID_COL") or ""))
        if not group_levels:
            rdf_names = pd.DataFrame(columns=rad_all)
            idc = _id_col(rdf_names)
            gcol = None
            src = str(settings.get("GROUP_SOURCE_COL") or "").strip()
            if src and src in rdf_names.columns:
                gcol = src
            elif len(outcomes) == 1 and outcomes[0] in rdf_names.columns:
                gcol = outcomes[0]
            elif "Group" in rdf_names.columns:
                gcol = "Group"
            elif "group" in rdf_names.columns:
                gcol = "group"
            usecols = [c for c in (idc, gcol) if c]
            if not usecols and rad_all:
                usecols = [rad_all[0]]
            rdf_g = _cols(rh, usecols) if usecols else pd.DataFrame()
            if gcol and gcol in rdf_g.columns:
                group_levels = [str(x) for x in rdf_g[gcol].dropna().astype(str).unique().tolist()]
                outcome_dist[str(gcol)] = {
                    str(k): int(v) for k, v in rdf_g[gcol].astype(str).value_counts().to_dict().items()
                }
            elif settings.get("GROUP_FROM_ID"):
                lv, vc_id = _levels_from_id(rdf_g)
                if lv:
                    group_levels = lv
                    outcome_dist["Group(from ID)"] = vc_id

    if ch and rh and cdf is not None:
        rad_id_name = rad_id if rad_id in rad_all else (rad_all[0] if rad_all else "")
        rdf = _cols(rh, [rad_id_name]) if rad_id_name else pd.DataFrame()
        cs = cdf[clin_id] if clin_id in cdf.columns else (
            cdf.iloc[:, 0] if len(cdf.columns) else None
        )
        rs = rdf[rad_id_name] if rad_id_name and rad_id_name in rdf.columns else (
            rdf.iloc[:, 0] if len(rdf.columns) else None
        )
        if cs is not None and rs is not None:
            cset = {str(x).strip() for x in cs.dropna().tolist()}
            rset = {str(x).strip() for x in rs.dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": len(rset),
                "overlap": len(cset & rset),
                "clin_only": len(cset - rset),
                "rad_only": len(rset - cset),
                "clin_id_col": clin_id if clin_id in cdf.columns else str(cdf.columns[0]),
                "rad_id_col": rad_id_name if rad_id_name in rdf.columns else (
                    str(rdf.columns[0]) if len(rdf.columns) else rad_id
                ),
            }
        elif cs is not None:
            cset = {str(x).strip() for x in cs.dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": 0,
                "overlap": 0,
                "clin_only": len(cset),
                "rad_only": 0,
                "clin_id_col": clin_id if clin_id in cdf.columns else str(cdf.columns[0]),
                "rad_id_col": "（无组学列）",
            }
    elif ch and cdf is not None:
        cid = clin_id if clin_id in cdf.columns else _id_col(cdf)
        if cid:
            cset = {str(x).strip() for x in cdf[cid].dropna().tolist()}
            id_check = {
                "clin_n": len(cset),
                "rad_n": 0,
                "overlap": 0,
                "clin_only": len(cset),
                "rad_only": 0,
                "clin_id_col": cid,
                "rad_id_col": "（无组学表）",
            }
    elif rh:
        rad_id_name = rad_id if rad_id in rad_all else (rad_all[0] if rad_all else "")
        rdf = _cols(rh, [rad_id_name]) if rad_id_name else pd.DataFrame()
        rid = rad_id_name if rad_id_name in rdf.columns else (
            str(rdf.columns[0]) if len(rdf.columns) else None
        )
        if rid:
            rset = {str(x).strip() for x in rdf[rid].dropna().tolist()}
            id_check = {
                "clin_n": 0,
                "rad_n": len(rset),
                "overlap": len(rset),
                "clin_only": 0,
                "rad_only": 0,
                "clin_id_col": "（无临床表）",
                "rad_id_col": rid,
            }

    eh = _hit(exc_sheet)
    try:
        from modules.stats.data_io import (
            resolve_exclude_files,
            normalize_exclude_rows,
            _keys_from_exclude_frame,
        )
        paths: list[Path] = []
        for exclude_file in resolve_exclude_files(settings):
            ex_p = Path(str(exclude_file))
            if not ex_p.is_absolute():
                ex_p = data_path.parent / ex_p
            paths.append(ex_p)
        ecol = str(settings.get("EXCLUDE_COL") or "exc")
        row_vals = normalize_exclude_rows(settings.get("EXCLUDE_ROW"))
        sheets_u: list[str] = []
        columns_u: list[str] = []
        values_u: list[str] = []
        all_keys: set[str] = set()
        n_raw_total = 0
        id_used = ""
        hit_sheet = exc_sheet or ""
        used_col = ecol
        for i, ex_p in enumerate(paths):
            meta = xlsx_meta(ex_p, exc_sheet or "exc", ecol) if ex_p.is_file() else {}
            for s in meta.get("sheets") or []:
                if s not in sheets_u:
                    sheets_u.append(str(s))
            if i == 0:
                columns_u = [str(c) for c in (meta.get("columns") or [])]
                values_u = [str(v) for v in (meta.get("values") or [])]
                hit_sheet = str(meta.get("sheet") or (exc_sheet or ""))
                used_col = str(meta.get("col") or ecol)
            if not (ex_p.is_file() and meta.get("sheet")):
                continue
            edf = pd.ExcelFile(ex_p).parse(meta["sheet"])
            use = ecol if ecol in edf.columns else (
                edf.columns[0] if len(edf.columns) else None
            )
            if use is None:
                continue
            keys, n_raw, id_one = _keys_from_exclude_frame(
                edf, str(use), row_vals, settings
            )
            all_keys |= {str(x) for x in keys if str(x).strip()}
            n_raw_total += int(n_raw)
            if id_one:
                id_used = str(id_one)
            if i == 0:
                used_col = str(use)
        file_label = (
            " + ".join(p.name for p in paths)
            if len(paths) > 1
            else (str(paths[0]) if paths else "")
        )
        exclude = {
            "n": int(n_raw_total),
            "ids": sorted(all_keys)[:80],
            "file": file_label,
            "files": [str(p) for p in paths],
            "sheet": hit_sheet,
            "col": used_col,
            "sheets": sheets_u,
            "columns": columns_u,
            "values": values_u,
            "row": row_vals[0] if len(row_vals) == 1 else row_vals,
            "missing": not any(p.is_file() for p in paths),
            "id_col": id_used,
        }
        if not exclude.get("sheets"):
            exclude["sheets"] = list(sheets)
            if eh:
                inf = info.get(eh) or {}
                exclude["columns"] = list(inf.get("columns") or [])
    except Exception:
        if eh:
            edf = _frame(eh)
            ecol = str(settings.get("EXCLUDE_COL") or "exc")
            use = ecol if ecol in edf.columns else (
                edf.columns[0] if len(edf.columns) else None
            )
            ids = []
            if use is not None:
                ids = [str(x) for x in edf[use].dropna().tolist() if str(x).strip()]
            exclude = {
                "n": len(ids), "ids": ids[:80], "col": str(use) if use else "",
                "sheets": list(sheets),
                "columns": [str(c) for c in (edf.columns if use is not None else [])],
                "values": [],
            }

    qh = _hit(qc_sheet)
    if qh:
        qdf = _frame(qh)
        if len(qdf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[qh] = _info_from_frame(qdf, qh)
        qc_sum = {"n": int(len(qdf)), "columns": [str(c) for c in qdf.columns[:20]]}
        for cand in ("QC_Status", "Status", "QC_MI_Warn"):
            if cand in qdf.columns:
                qc_sum[cand] = {str(k): int(v) for k, v in
                                qdf[cand].value_counts(dropna=False).head(12).to_dict().items()}

    ih = _hit(icc_sheet)
    if ih:
        idf = _frame(ih)
        if len(idf.columns) <= _PREVIEW_HEAD_MAX_COLS:
            info[ih] = _info_from_frame(idf, ih)
        icc_cols = [str(c) for c in idf.columns]
        thr = float(settings.get("ICC_THRESHOLD") or 0.75)
        intra_c = str(settings.get("ICC_INTRA_COL") or "intra_ICC")
        inter_c = str(settings.get("ICC_INTER_COL") or "inter_ICC")
        icc_sum = {"n": int(len(idf)), "threshold": thr}
        if intra_c in idf.columns and inter_c in idf.columns:
            intra = pd.to_numeric(idf[intra_c], errors="coerce")
            inter = pd.to_numeric(idf[inter_c], errors="coerce")
            both = (intra >= thr) & (inter >= thr)
            icc_sum["both_ge_thr"] = int(both.sum())
            icc_sum["missing_icc"] = int((intra.isna() | inter.isna()).sum())

    after_n = None
    if id_check:
        if ch and rh:
            base = id_check.get("overlap", 0)
        elif ch:
            base = id_check.get("clin_n", 0)
        else:
            base = id_check.get("rad_n", 0)
        exc_n = int(exclude.get("n") or 0) if settings.get("DO_EXCLUDE_SAMPLES") else 0
        after_n = max(0, int(base or 0) - exc_n)

    if not clin_cols and rad_meta_cols:
        clin_cols = list(rad_meta_cols)
        for extra in ("Name", "Group"):
            if extra not in clin_cols:
                clin_cols.append(extra)

    idc_pick = ""
    id_src = None
    if ch:
        try:
            id_src = cdf
            idc_pick = str(settings.get("CLIN_ID_COL") or "")
            if id_src is not None and idc_pick not in id_src.columns:
                idc_pick = _id_col(id_src) or ""
        except Exception:
            id_src = None
    elif rh:
        idc_pick = str(settings.get("RAD_ID_COL") or "")
        if idc_pick not in (rad_meta_cols or rad_id_cols):
            for cand in ("Name", "ID", "PatientID", "patient_id", "Patient_ID"):
                if cand in rad_meta_cols:
                    idc_pick = cand
                    break
            else:
                idc_pick = rad_meta_cols[0] if rad_meta_cols else ""
        if idc_pick:
            try:
                id_src = _cols(rh, [idc_pick])
            except Exception:
                id_src = None
    if id_src is not None and idc_pick and idc_pick in id_src.columns:
        raw_ids = [str(x).strip() for x in id_src[idc_pick].dropna().tolist() if str(x).strip()]
        id_options = _uniq_keep(raw_ids)[:400]

    return {
        "data": str(data_path),
        "sheets": sheets,
        "sheet_info": info,
        "clin_cols": clin_cols,
        "rad_id_cols": rad_id_cols,
        "rad_meta_cols": rad_meta_cols,
        "measure_cols": _measure_cols(
            clin_cols,
            _columns_open_for_analysis(
                rad_all, clin_cols,
                _as_name_list(settings.get("RAD_FEATURE_PREFIX_EXCLUDE")),
                rad_id_cols,
            ),
        ),
        "rad_cols": rad_all,
        "id_options": id_options,
        "icc_cols": icc_cols,
        "outcome_dist": outcome_dist,
        "clin_dist": clin_dist,
        "id_check": id_check,
        "exclude": exclude,
        "qc": qc_sum,
        "icc": icc_sum,
        "n_after_exclude_est": after_n,
        "group_levels": group_levels,
        "group_source": preview_group_source,
        "resolved_sheets": {
            "CLIN_SHEET": ch or "",
            "RAD_SHEET": rh or "",
            "QC_SHEET": qh or "",
            "ICC_SHEET": ih or "",
        },
        "sheet_rule": (
            "CLIN_SHEET 与 RAD_SHEET 必须二选一"
            if not (ch or rh)
            else ""
        ),
    }

def _resolve_ext_xlsx(file: str, data_path: Path) -> Path:
    src = Path(str(file or "").strip())
    if not src.is_absolute():
        src = data_path.parent / src
    return under_root(src)

def _preview_external_one(
    data_path: Path, settings: dict[str, Any], spec: dict[str, str]
) -> dict[str, Any]:
    from modules.utils.u_qc import external_qc_html_name, qc_html_rel, _pick_named_sheet

    name = str(spec.get("name") or "ext")
    rec: dict[str, Any] = {
        "name": name,
        "qc_html": qc_html_rel(external_qc_html_name(name)),
        "file": str(spec.get("file") or ""),
    }
    try:
        path = _resolve_ext_xlsx(spec["file"], data_path)
    except Exception as e:
        rec["error"] = str(e)
        return rec
    rec["file"] = str(path)
    if not path.is_file():
        rec["error"] = f"missing file: {path}"
        return rec
    try:
        from modules.utils.xlsx_sheet import workbook_info
        sheets = [str(s["name"]) for s in workbook_info(path)]
    except Exception as e:
        rec["error"] = str(e)
        return rec
    st = dict(settings)
    st["CLIN_SHEET"] = spec.get("clin_sheet") or ""
    st["RAD_SHEET"] = spec.get("rad_sheet") or ""
    if spec.get("rad_id_col"):
        st["RAD_ID_COL"] = spec["rad_id_col"]
    st["QC_SHEET"] = _pick_named_sheet(
        sheets, spec.get("qc_sheet", ""), settings.get("QC_SHEET"), "qc",
    )
    st["ICC_SHEET"] = _pick_named_sheet(
        sheets, spec.get("icc_sheet", ""), settings.get("ICC_SHEET"), "icc",
    )
    st["DO_EXCLUDE_SAMPLES"] = False
    st["EXCLUDE_FILE"] = ""
    try:
        one = _preview_one_workbook(path, st)
    except Exception as e:
        rec["error"] = str(e)
        return rec
    one.update(rec)
    one["has_qc_sheet"] = bool(st.get("QC_SHEET"))
    return one

def _preview_cache_key(data_path: Path, settings: dict[str, Any]) -> tuple:
    data_path = Path(data_path)
    st = data_path.stat()
    keep = (
        "CLIN_SHEET", "RAD_SHEET", "QC_SHEET", "ICC_SHEET",
        "CLIN_ID_COL", "RAD_ID_COL", "LABEL_COL",
        "OUTCOME_COLS", "SUBGROUP_COL", "GROUP_SOURCE_COL",
        "GROUP_FROM_ID", "GROUP_FROM_ID_SEP",
        "DO_EXCLUDE_SAMPLES", "EXCLUDE_FILE", "EXCLUDE_SHEET",
        "EXCLUDE_COL", "EXCLUDE_ROW",
        "ICC_THRESHOLD", "ICC_INTRA_COL", "ICC_INTER_COL",
        "EXTERNAL_FILE", "EXTERNAL_CLIN_SHEET", "EXTERNAL_RAD_SHEET",
        "EXTERNAL_TESTS",
    )
    blob = []
    for key in keep:
        blob.append((key, json.dumps(settings.get(key), ensure_ascii=False, sort_keys=True, default=str)))
    extras = []
    names = []
    raw_ext = settings.get("EXTERNAL_TESTS") or []
    if isinstance(raw_ext, list):
        for spec in raw_ext:
            if isinstance(spec, dict) and spec.get("file"):
                names.append(str(spec.get("file")))
    if settings.get("EXTERNAL_FILE"):
        names.append(str(settings.get("EXTERNAL_FILE")))
    for name in names:
        p = Path(name)
        if not p.is_absolute():
            p = data_path.parent / p
        if p.is_file():
            est = p.stat()
            extras.append((str(p), est.st_mtime_ns, est.st_size))
    return (str(data_path.resolve()), st.st_mtime_ns, st.st_size, tuple(blob), tuple(extras))

def preview_workbook(data_path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    from modules.utils.u_qc import _external_specs_from_settings

    data_path = Path(data_path)
    try:
        key = _preview_cache_key(data_path, settings)
    except OSError:
        key = None
    if key is not None and key in _PREVIEW_CACHE:
        return _PREVIEW_CACHE[key]
    main = _preview_one_workbook(data_path, settings)
    externals: list[dict[str, Any]] = []
    for spec in _external_specs_from_settings(settings):
        externals.append(_preview_external_one(data_path, settings, spec))
    main["external"] = externals
    if key is not None:
        if len(_PREVIEW_CACHE) > 8:
            _PREVIEW_CACHE.clear()
        _PREVIEW_CACHE[key] = main
    return main
