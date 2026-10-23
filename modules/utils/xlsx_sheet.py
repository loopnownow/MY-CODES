"""Read .xlsx sheets from the zip, without building an openpyxl cell for every value.

Wide radiomics sheets are tens of megabytes of XML. openpyxl spends several
seconds on those cells. Preview only needs sheet size, the header, and one
or two columns; a modeling load needs the whole sheet once. Both go through
this module. If the file is not an xlsx or the XML is unexpected, callers
fall back to pandas.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

_CELL = re.compile(br'<c r="([A-Z]+)(\d+)"([^>/]*?)(?:/>|>(.*?)</c>)')
_DIM = re.compile(br'<dimension[^>]*r?ef="([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?"')


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_index(label: bytes) -> int:
    n = 0
    for ch in label:
        n = n * 26 + (ch - 64)
    return n - 1


def _sheet_members(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    root = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target: dict[str, str] = {}
    for rel in rels:
        if _local(rel.tag) != "Relationship":
            continue
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target") or ""
        if rid:
            rid_to_target[rid] = target
    out: list[tuple[str, str]] = []
    for node in root.iter():
        if _local(node.tag) != "sheet":
            continue
        name = node.attrib.get("name") or ""
        rid = ""
        for key, val in node.attrib.items():
            if key.endswith("}id") or key == "id":
                rid = val
                break
        target = rid_to_target.get(rid, "").lstrip("/")
        if not target:
            continue
        member = target if target.startswith("xl/") else "xl/" + target
        out.append((name, member))
    return out


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        raw = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(raw)
    out: list[str] = []
    for node in root:
        if _local(node.tag) != "si":
            continue
        parts: list[str] = []
        for child in node.iter():
            if _local(child.tag) == "t" and child.text:
                parts.append(child.text)
        out.append("".join(parts))
    return out


def _cell_value(attrs: bytes, inner: bytes, shared: list[str]):
    if b't="inlineStr"' in attrs or b"<is>" in inner:
        i = inner.find(b"<t")
        if i < 0:
            return None
        a = inner.find(b">", i)
        b = inner.find(b"</t>", a + 1)
        if a < 0 or b < 0:
            return None
        return inner[a + 1:b].decode("utf-8", errors="replace")
    i = inner.find(b"<v>")
    if i < 0:
        return None
    j = inner.find(b"</v>", i + 3)
    if j < 0:
        return None
    raw = inner[i + 3:j]
    if b't="s"' in attrs:
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return None
    if b't="b"' in attrs:
        return raw == b"1"
    text = raw.decode("ascii", errors="replace")
    if any(ch in text for ch in ".eE"):
        try:
            return float(text)
        except ValueError:
            return text
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _dimension(z: zipfile.ZipFile, member: str) -> tuple[int, int]:
    """Data rows (excluding the header) and column count. (-1, -1) if unknown."""
    with z.open(member) as handle:
        head = handle.read(65536)
    m = _DIM.search(head)
    if not m:
        return -1, -1
    r1 = int(m.group(2))
    if m.group(4):
        c2 = _col_index(m.group(3)) + 1
        r2 = int(m.group(4))
    else:
        c2 = _col_index(m.group(1)) + 1
        r2 = r1
    n_rows = max(0, r2 - r1) if r1 <= 1 else max(0, r2 - 1)
    if r1 == 1:
        n_rows = max(0, r2 - 1)
    return n_rows, c2


def workbook_info(path: str | Path) -> list[dict]:
    """Sheet name plus size. Does not decompress worksheet bodies."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        out = []
        for name, member in _sheet_members(z):
            n_rows, n_cols = _dimension(z, member)
            out.append({
                "name": name,
                "member": member,
                "n_rows": n_rows,
                "n_cols": n_cols,
            })
        return out


def _first_row_xml(z: zipfile.ZipFile, member: str) -> bytes:
    with z.open(member) as handle:
        buf = b""
        while True:
            chunk = handle.read(262144)
            if not chunk:
                return buf
            buf += chunk
            end = buf.find(b"</row>")
            if end != -1:
                return buf[: end + 6]


def _header_from_xml(blob: bytes, shared: list[str], n_cols: int) -> list[str]:
    headers: dict[int, str] = {}
    width = 0
    for match in _CELL.finditer(blob):
        col_b, row_b, attrs, inner = match.groups()
        inner = inner or b""
        if row_b != b"1":
            break
        idx = _col_index(col_b)
        val = _cell_value(attrs, inner, shared)
        headers[idx] = "" if val is None else str(val)
        if idx + 1 > width:
            width = idx + 1
    width = max(width, n_cols if n_cols > 0 else 0)
    return [headers.get(i, f"Unnamed: {i}") if not headers.get(i) else headers[i] for i in range(width)]


def read_header(path: str | Path, sheet: str) -> list[str]:
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        member = _member(z, sheet)
        _n_rows, n_cols = _dimension(z, member)
        shared = _shared_strings(z)
        return _header_from_xml(_first_row_xml(z, member), shared, n_cols)


def _member(z: zipfile.ZipFile, sheet: str) -> str:
    for name, member in _sheet_members(z):
        if name == sheet:
            return member
    low = sheet.lower()
    for name, member in _sheet_members(z):
        if name.lower() == low:
            return member
    raise KeyError(sheet)


def _scan(
    xml: bytes,
    shared: list[str],
    *,
    keep_cols: set[int] | None,
    preview_rows: int = 0,
    preview_cols: int = 0,
) -> tuple[list[str], dict[int, list], list[list], int]:
    """Header, per-column values aligned to data rows, preview grid, n data rows seen."""
    header_cells: dict[int, str] = {}
    header_width = 0
    col_vals: dict[int, list] = {}
    preview: list[list] = []
    max_data_row = -1
    if keep_cols is None:
        keep_cols = set()
        keep_all = True
    else:
        keep_all = False

    for match in _CELL.finditer(xml):
        col_b, row_b, attrs, inner = match.groups()
        inner = inner or b""
        row_n = int(row_b)
        col_i = _col_index(col_b)
        if row_n == 1:
            val = _cell_value(attrs, inner, shared)
            header_cells[col_i] = "" if val is None else str(val)
            if col_i + 1 > header_width:
                header_width = col_i + 1
            continue
        data_i = row_n - 2
        if data_i > max_data_row:
            max_data_row = data_i
        want = keep_all or col_i in keep_cols
        in_preview = (
            preview_rows > 0
            and data_i < preview_rows
            and col_i < preview_cols
        )
        if not want and not in_preview:
            continue
        val = _cell_value(attrs, inner, shared)
        if want:
            bucket = col_vals.get(col_i)
            if bucket is None:
                bucket = []
                col_vals[col_i] = bucket
            while len(bucket) <= data_i:
                bucket.append(None)
            bucket[data_i] = val
        if in_preview:
            while len(preview) <= data_i:
                preview.append([])
            row = preview[data_i]
            while len(row) <= col_i:
                row.append(None)
            row[col_i] = val
    n_rows = max_data_row + 1
    header = [
        header_cells.get(i) or f"Unnamed: {i}"
        for i in range(header_width)
    ]
    for bucket in col_vals.values():
        while len(bucket) < n_rows:
            bucket.append(None)
    return header, col_vals, preview, n_rows


def _align_column(values: list, n_rows: int) -> list:
    if len(values) < n_rows:
        return values + [None] * (n_rows - len(values))
    return values[:n_rows]


def _as_series(values: list) -> pd.Series:
    s = pd.Series(values, dtype="object")
    num = pd.to_numeric(s, errors="coerce")
    present = s.map(lambda v: v is not None and not (isinstance(v, float) and np.isnan(v)))
    if int(present.sum()) == 0:
        return s
    # Keep text columns (IDs). A numeric column may still hold None for blanks.
    if int(num.notna().sum()) == int(present.sum()):
        return num
    return s


def _col_letters(index: int) -> bytes:
    n = index + 1
    out = bytearray()
    while n:
        n, rem = divmod(n - 1, 26)
        out.append(65 + rem)
    return bytes(reversed(out))


def _scan_named_columns(
    xml: bytes,
    shared: list[str],
    indexes: dict[int, str],
    n_rows: int,
) -> dict[str, list]:
    """Pull a handful of columns with one regex per column letter.

    A full cell walk is about a second on a 6000-column sheet. Matching only
    ``<c r="A12" ...>`` stays on the bytes we need.
    """
    out = {name: [None] * n_rows for name in indexes.values()}
    for idx, name in indexes.items():
        letters = _col_letters(idx)
        pat = re.compile(
            br'<c r="' + letters + br'(\d+)"([^>/]*?)(?:/>|>(.*?)</c>)'
        )
        for row_b, attrs, inner in pat.findall(xml):
            row_n = int(row_b)
            if row_n <= 1:
                continue
            data_i = row_n - 2
            if data_i < 0 or data_i >= n_rows:
                continue
            out[name][data_i] = _cell_value(attrs, inner or b"", shared)
    return out


def read_columns(path: str | Path, sheet: str, columns: list[str]) -> pd.DataFrame:
    """All data rows for the named columns. Missing names are omitted."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        member = _member(z, sheet)
        n_rows, n_cols = _dimension(z, member)
        shared = _shared_strings(z)
        header = _header_from_xml(_first_row_xml(z, member), shared, n_cols)
        wanted = [c for c in columns if c in header]
        indexes = {header.index(c): c for c in wanted}
        xml = z.read(member)
    if n_rows < 0:
        _header, col_vals, _preview, n_rows = _scan(
            xml, shared, keep_cols=set(indexes), preview_rows=0, preview_cols=0,
        )
        data = {
            name: _as_series(_align_column(col_vals.get(i, []), n_rows))
            for i, name in indexes.items()
        }
        return pd.DataFrame(data)
    raw = _scan_named_columns(xml, shared, indexes, n_rows)
    return pd.DataFrame({name: _as_series(raw[name]) for name in wanted})


def read_preview_block(
    path: str | Path,
    sheet: str,
    *,
    n_rows: int = 6,
    n_cols: int = 16,
) -> tuple[list[str], list[list]]:
    """Header and the top-left block. Does not scan past the header when n_rows is 0."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        member = _member(z, sheet)
        _nr, n_dim = _dimension(z, member)
        shared = _shared_strings(z)
        if n_rows <= 0:
            header = _header_from_xml(_first_row_xml(z, member), shared, n_dim)
            return header, []
        xml = z.read(member)
    header, _cols, preview, _seen = _scan(
        xml, shared, keep_cols=set(), preview_rows=n_rows, preview_cols=n_cols,
    )
    width = min(n_cols, len(header))
    block = []
    for row in preview[:n_rows]:
        block.append([(row[i] if i < len(row) else None) for i in range(width)])
    return header, block


def read_sheet(path: str | Path, sheet: str) -> pd.DataFrame:
    """Full sheet. Falls back to pandas if the zip reader cannot parse it."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            member = _member(z, sheet)
            shared = _shared_strings(z)
            xml = z.read(member)
        header, col_vals, _preview, n_rows = _scan(
            xml, shared, keep_cols=None, preview_rows=0, preview_cols=0,
        )
    except (KeyError, zipfile.BadZipFile, ET.ParseError, ValueError):
        return pd.read_excel(path, sheet_name=sheet)
    if not header:
        return pd.read_excel(path, sheet_name=sheet)
    data = {
        name: _as_series(_align_column(col_vals.get(i, []), n_rows))
        for i, name in enumerate(header)
    }
    return pd.DataFrame(data)
