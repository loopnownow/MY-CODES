#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_glossary_index.py -- rebuild `<0doc>\\_library\\glossary\\index.csv` from the four md layers.

The md files are the SSOT for glossary entries; `index.csv` is the machine index.
This script parses `### <entry>` blocks plus the `- 用法/证据/频次/判断/bucket/规则：` fields,
then rewrites index.csv (12 fixed columns). Fenced code blocks (the entry template) are ignored.
Offline, stdlib only, read-only except with `--apply`.

Run (PowerShell, Python 3.13.9):

    $env:GLOSSARY_ROOT = "<0doc>\\_library\\glossary"
    python .\\sync_glossary_index.py            # dry-run: print rows
    python .\\sync_glossary_index.py --apply    # rewrite index.csv

Gate: `test\\structure_check.py` S19 validates the same contract (layer/sub enums,
sub2 only on sentences/review, evidence_ref shape, verdict/rule_ptr).
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

# ---------------------------------------------------------------- CONFIG
# GLOSSARY_ROOT: env naming the glossary seat; md layers and index.csv live under it (no lab default).
GLOSSARY_ROOT_ENV = "GLOSSARY_ROOT"
INDEX_NAME = "index.csv"
# LAYERS: sub-directory order; also the legal `layer` values.
LAYERS = ("words", "sentences", "paragraphs", "sections")
# COLUMNS: index.csv header, must stay in sync with test\structure_check.py S19.
COLUMNS = ("layer", "sub", "sub2", "entry", "lang", "example", "evidence_ref",
           "freq", "verdict", "rule_ptr", "note", "updated_at")
UPDATED = "2026-11-01"
CJK = re.compile(r"[\u4e00-\u9fff]")
FIELD = re.compile(r"^-\s*(用法|证据|频次|判断|bucket|规则)：(.*)$")
BACKTICK = re.compile(r"`([^`]+)`")
TRIM = " `（(，,；;"


def glossary_root() -> Path:
    value = (os.environ.get(GLOSSARY_ROOT_ENV) or "").strip()
    if not value:
        raise SystemExit("set %s to the 0doc glossary seat (<0doc>\\_library\\glossary)" % GLOSSARY_ROOT_ENV)
    return Path(value)


def entries(md: Path) -> list[dict[str, str]]:
    """`### <entry>` blocks with their `- 字段：值` lines; fenced examples skipped."""
    out: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    in_fence = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("### "):
            cur = {"entry": line[4:].strip()}
            out.append(cur)
            continue
        if cur is None:
            continue
        m = FIELD.match(line.strip())
        if m:
            cur[m.group(1)] = m.group(2).strip()
    return out


def build_rows(root: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for layer in LAYERS:
        for md in sorted((root / layer).glob("*.md")):
            for item in entries(md):
                use = item.get("用法", "")
                ev = BACKTICK.search(item.get("证据", ""))
                rule = BACKTICK.search(item.get("规则", ""))
                freq = re.match(r"(\d+)", item.get("频次", ""))
                rows.append([
                    layer,
                    md.stem,
                    item.get("bucket", ""),
                    item["entry"],
                    "zh" if CJK.search(use) else "en",
                    use[:60].rstrip(TRIM),
                    ev.group(1) if ev else "",
                    freq.group(1) if freq else "",
                    item.get("判断", ""),
                    rule.group(1) if rule else "",
                    "",
                    UPDATED,
                ])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="rewrite index.csv (default dry-run)")
    args = ap.parse_args(argv)

    root = glossary_root()
    if not root.is_dir():
        raise SystemExit("%s does not resolve to a directory: %s" % (GLOSSARY_ROOT_ENV, root))

    rows = build_rows(root)
    bad = [r for r in rows if not r[6] or not r[8] or not r[9]]
    if bad:
        for r in bad:
            print("INCOMPLETE %s/%s :: %s" % (r[0], r[1], r[3]))
        raise SystemExit("%d entrie(s) missing evidence_ref / verdict / rule_ptr" % len(bad))

    print("rows=%d" % len(rows))
    for r in rows:
        print("  %s | %s | %s | %s | %s" % (r[0], r[1], r[3], r[6], r[8]))
    if not args.apply:
        print("dry-run: add --apply to rewrite %s" % INDEX_NAME)
        return 0

    text = ",".join(COLUMNS) + "\n"
    for r in rows:
        text += ",".join('"' + c.replace('"', '""') + '"' for c in r) + "\n"
    (root / INDEX_NAME).write_text(text, encoding="utf-8", newline="\n")
    print("-> %s" % (root / INDEX_NAME))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
