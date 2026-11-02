"""Golden-number extraction and parity diff for pipeline ``Results*.html``.

Config (module-level, edit here only)
-------------------------------------
DEFAULT_TOL    relative/absolute tolerance for deterministic cells (p, mean±SD, coefs)
TABLE_TOL      per-table-id override; bootstrap-derived cells need a looser value
NUM_RE         numeric token pattern used to split a cell (``<0.001``, ``1.2±3.4``,
               ``52.7%``, ``(0.610-0.764)``)
BOUND_MARKS    leading characters that make a cell a bound (``<`` / ``>``) not a point
HEADING_TAGS   tags treated as section headings when scanning run status

Run
---
Extract a golden file from a finished run::

    python -B modules/tests/golden_tools.py extract --html <ResultsX.html> --out <golden.json>

Compare a fresh run against that golden file::

    python -B modules/tests/golden_tools.py compare --golden <golden.json> --html <ResultsX.html> \
        [--report <report.md>] [--tol 1e-10] [--table roc-metrics-train-test]

Exit codes: 0 = no FAIL (WARN allowed), 1 = at least one FAIL, 2 = usage/data error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── config ───────────────────────────────────────────────────────────────────
DEFAULT_TOL = 1e-10
TABLE_TOL: Dict[str, float] = {
    "roc-metrics-train-test": 1e-6,  # bootstrap AUC CI
}
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
BOUND_MARKS = "<>"
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5"}

Diff = Dict[str, str]
Level = str  # "FAIL" | "WARN"


def _norm(text: str) -> str:
    return " ".join(text.split())


class _ResultsParser(HTMLParser):
    """Collects headings and every ``<table id=...>`` as header rows + body rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: List[Tuple[str, str]] = []
        self.tables: Dict[str, Dict[str, Any]] = {}
        self._table: Optional[Dict[str, Any]] = None
        self._in_caption = False
        self._in_thead = False
        self._in_cell = False
        self._cell: List[str] = []
        self._row: List[str] = []
        self._heading_tag: Optional[str] = None
        self._heading: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = dict(attrs)
        if tag in HEADING_TAGS:
            self._heading_tag = tag
            self._heading = []
            return
        if tag == "table":
            tid = (a.get("id") or "").strip() or f"table{len(self.tables) + 1}"
            self._table = {"id": tid, "caption": "", "header": [], "rows": []}
            return
        if self._table is None:
            return
        if tag == "caption":
            self._in_caption = True
            self._table["_caption_parts"] = []
        elif tag == "thead":
            self._in_thead = True
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in HEADING_TAGS and self._heading_tag == tag:
            self.headings.append((tag, _norm("".join(self._heading))))
            self._heading_tag = None
            self._heading = []
            return
        if self._table is None:
            return
        if tag == "caption":
            self._in_caption = False
            self._table["caption"] = _norm("".join(self._table.pop("_caption_parts", [])))
        elif tag == "thead":
            self._in_thead = False
        elif tag in ("td", "th"):
            self._in_cell = False
            self._row.append(_norm("".join(self._cell)))
            self._cell = []
        elif tag == "tr":
            if self._row:
                key = "header" if self._in_thead else "rows"
                self._table[key].append(list(self._row))
            self._row = []
        elif tag == "table":
            tid = self._table["id"]
            self._table.pop("_caption_parts", None)
            self.tables[tid] = self._table
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._heading_tag is not None:
            self._heading.append(data)
        if self._table is None:
            return
        if self._in_caption:
            self._table["_caption_parts"].append(data)
        elif self._in_cell:
            self._cell.append(data)


def extract(path: str | Path) -> Dict[str, Any]:
    """Parse one Results HTML file into ``{"meta": ..., "tables": {id: {...}}}``."""
    p = Path(path)
    raw = p.read_text(encoding="utf-8", errors="replace")
    parser = _ResultsParser()
    parser.feed(raw)
    parser.close()

    run_status = ""
    for _tag, text in parser.headings:
        m = re.match(r"Run status:\s*(\S+)", text)
        if m:
            run_status = m.group(1)
            break

    return {
        "meta": {
            "file": p.name,
            "run_status": run_status,
            "headings": [f"{t}:{x}" for t, x in parser.headings],
            "table_ids": sorted(parser.tables),
        },
        "tables": parser.tables,
    }


def _num_tokens(text: str) -> List[float]:
    return [float(x) for x in NUM_RE.findall(text)]


def _flat_header(rows: List[List[str]]) -> str:
    """Render a (possibly multi-row) header as one report string."""
    return " | ".join(" / ".join(r) for r in rows)


def _col_label(header: List[List[str]], j: int) -> str:
    """Column name for the report; multihead tables use the bottom header row."""
    if not header:
        return str(j)
    last = header[-1]
    return last[j] if j < len(last) else str(j)


def _cell_diff(old: str, new: str, tol: float) -> Optional[Tuple[Level, str]]:
    o, n = old.strip(), new.strip()
    if o == n:
        return None
    on, nn = _num_tokens(o), _num_tokens(n)
    if len(on) != len(nn):
        return ("FAIL", f"numeric token count {len(on)}->{len(nn)}")
    bo = o[:1] if o[:1] in BOUND_MARKS else ""
    bn = n[:1] if n[:1] in BOUND_MARKS else ""
    if bo != bn:
        return ("FAIL", f"bound marker {bo!r}->{bn!r}")
    for a, b in zip(on, nn):
        if not math.isclose(a, b, rel_tol=tol, abs_tol=tol):
            return ("FAIL", f"{a!r}->{b!r} (tol={tol:g})")
    return ("WARN", f"text differs, numbers within {tol:g}")


def compare(
    golden: Dict[str, Any],
    current: Dict[str, Any],
    tol: Optional[float] = None,
    only: Optional[Iterable[str]] = None,
) -> List[Diff]:
    """Return the list of diffs; an empty list means full parity."""
    base_tol = DEFAULT_TOL if tol is None else float(tol)
    keep = set(only) if only else None
    diffs: List[Diff] = []

    g_meta, c_meta = golden.get("meta", {}), current.get("meta", {})
    if g_meta.get("run_status") != c_meta.get("run_status"):
        diffs.append(
            {
                "level": "FAIL",
                "table": "<meta>",
                "where": "run_status",
                "old": str(g_meta.get("run_status")),
                "new": str(c_meta.get("run_status")),
                "note": "run status changed",
            }
        )

    for tid, gtbl in golden.get("tables", {}).items():
        if keep is not None and tid not in keep:
            continue
        table_tol = TABLE_TOL.get(tid, base_tol)
        ctbl = current.get("tables", {}).get(tid)
        if ctbl is None:
            diffs.append(
                {
                    "level": "FAIL",
                    "table": tid,
                    "where": "-",
                    "old": "present",
                    "new": "missing",
                    "note": "table missing in current run",
                }
            )
            continue

        if gtbl.get("caption") != ctbl.get("caption"):
            diffs.append(
                {
                    "level": "WARN",
                    "table": tid,
                    "where": "caption",
                    "old": gtbl.get("caption", ""),
                    "new": ctbl.get("caption", ""),
                    "note": "caption text differs",
                }
            )

        ghdr, chdr = gtbl.get("header", []), ctbl.get("header", [])
        if ghdr != chdr:
            diffs.append(
                {
                    "level": "FAIL",
                    "table": tid,
                    "where": "header",
                    "old": _flat_header(ghdr),
                    "new": _flat_header(chdr),
                    "note": "header changed",
                }
            )

        grows, crows = gtbl.get("rows", []), ctbl.get("rows", [])
        if len(grows) != len(crows):
            diffs.append(
                {
                    "level": "FAIL",
                    "table": tid,
                    "where": "row_count",
                    "old": str(len(grows)),
                    "new": str(len(crows)),
                    "note": "row count changed",
                }
            )
        for i, grow in enumerate(grows):
            if i >= len(crows):
                break
            crow = crows[i]
            if not grow or not crow:
                continue
            if grow[0] != crow[0]:
                diffs.append(
                    {
                        "level": "FAIL",
                        "table": tid,
                        "where": f"row[{i}].label",
                        "old": grow[0],
                        "new": crow[0],
                        "note": "row label/order changed",
                    }
                )
                continue
            for j, gcell in enumerate(grow):
                ccell = crow[j] if j < len(crow) else "<absent>"
                res = _cell_diff(gcell, ccell, table_tol)
                if res is None:
                    continue
                level, note = res
                col = _col_label(ghdr, j)
                diffs.append(
                    {
                        "level": level,
                        "table": tid,
                        "where": f"{grow[0]} / {col}",
                        "old": gcell,
                        "new": ccell,
                        "note": note,
                    }
                )
    return diffs


def render_report(diffs: List[Diff], golden_path: str | Path, html_path: str | Path) -> str:
    fails = [d for d in diffs if d["level"] == "FAIL"]
    warns = [d for d in diffs if d["level"] == "WARN"]
    lines = [
        "# Golden parity report",
        "",
        f"- golden: `{golden_path}`",
        f"- current: `{html_path}`",
        f"- FAIL: **{len(fails)}**",
        f"- WARN: {len(warns)}",
        "",
    ]
    if not diffs:
        lines.append("Full parity — no cell differs within tolerance.")
        return "\n".join(lines) + "\n"
    lines += ["| level | table | where | old | new | note |", "|---|---|---|---|---|---|"]
    for d in diffs:
        lines.append(
            f"| {d['level']} | {d['table']} | {d['where']} | `{d['old']}` | `{d['new']}` | {d['note']} |"
        )
    return "\n".join(lines) + "\n"


def _cmd_extract(args: argparse.Namespace) -> int:
    data = extract(args.html)
    if not data["tables"]:
        print(f"no <table> found in {args.html}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"golden written: {out}  tables={len(data['tables'])}  run_status={data['meta']['run_status']}")
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    current = extract(args.html)
    diffs = compare(golden, current, tol=args.tol, only=args.table)
    report = render_report(diffs, args.golden, args.html)
    if args.report:
        Path(args.report).write_text(report, encoding="utf-8")
        print(f"report written: {args.report}")
    print(report)
    return 1 if any(d["level"] == "FAIL" for d in diffs) else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Golden-number extraction / parity diff for Results*.html")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="write a golden JSON from one Results HTML")
    ex.add_argument("--html", required=True)
    ex.add_argument("--out", required=True)
    ex.set_defaults(func=_cmd_extract)

    cp = sub.add_parser("compare", help="diff a fresh Results HTML against a golden JSON")
    cp.add_argument("--golden", required=True)
    cp.add_argument("--html", required=True)
    cp.add_argument("--report", default=None)
    cp.add_argument("--tol", type=float, default=None)
    cp.add_argument("--table", action="append", default=None,
                    help="limit to one table id (repeatable)")
    cp.set_defaults(func=_cmd_compare)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
