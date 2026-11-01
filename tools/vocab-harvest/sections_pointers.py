"""sections_pointers.py — 从 `_library\\glossary\\index.csv` 生成各节 md 的指针表块。

运行（2026-11-02 起在本仓 `{code_stats}\\tools\\vocab-harvest\\`，不再从 {DEL} 跑）：
    python -B D:\\0Grok\\0doc\\GitHub\\MY-CODES\\tools\\vocab-harvest\\sections_pointers.py            # dry-run
    python -B D:\\0Grok\\0doc\\GitHub\\MY-CODES\\tools\\vocab-harvest\\sections_pointers.py --apply    # 写回 md

指针块落在 `<!-- POINTERS:BEGIN -->` 与 `<!-- POINTERS:END -->` 之间；不存在时插在第一个 `## ` 标题之前。
**必须先跑 sync_glossary_index.py --apply**，本脚本读 index.csv（串行，勿并行）。
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

# ---------------------------------------------------------------- CONFIG
# GLOSSARY: glossary 席位根（md 四层 + index.csv）。
GLOSSARY = Path(r"D:\0Grok\0doc\_library\glossary")
# SECTIONS: 八个节文件，指针页文件名 == index.csv `sections` 列的取值。
SECTIONS = ("abstract", "intro", "methods", "results", "discussion",
            "limitation", "conclusion", "other")
BEGIN = "<!-- POINTERS:BEGIN -->"
END = "<!-- POINTERS:END -->"
HEADER = ("## 本节点指针（自动生成，勿手改）\n"
          "\n"
          "> 由 `sections_pointers.py` 从 `index.csv` 的 `sections` 列生成；"
          "闸门 `structure_check.py` **S20** 断言本表与 index 双向一致。\n")


def load_rows() -> list[dict[str, str]]:
    with (GLOSSARY / "index.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def render(section: str, rows: list[dict[str, str]]) -> str:
    picked = [r for r in rows
              if section in [t.strip() for t in (r.get("sections") or "").split(";") if t.strip()]]
    picked.sort(key=lambda r: (-int(r.get("freq") or 0), r.get("entry") or ""))

    def cell(value: str) -> str:
        return (value or "").replace("|", "\\|")

    lines = [BEGIN, HEADER, "| 条目 | 层/子类 | freq | evidence_ref |", "|---|---|---|---|"]
    for r in picked:
        lines.append("| %s | %s/%s | %s | %s |" % (
            cell(r["entry"]), r["layer"], r["sub"], r.get("freq") or "", cell(r.get("evidence_ref") or "")))
    lines.append("")
    lines.append("共 %d 条。" % len(picked))
    lines.append(END)
    return "\n".join(lines)


def splice(text: str, block: str) -> str:
    if BEGIN in text and END in text:
        head = text[: text.index(BEGIN)]
        tail = text[text.index(END) + len(END):]
        return head + block + tail
    idx = text.find("\n## ")
    anchor = idx + 1 if idx >= 0 else len(text.rstrip("\n")) + 1
    return text[:anchor] + block + "\n\n" + text[anchor:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 sections/*.md（默认 dry-run）")
    args = ap.parse_args()

    rows = load_rows()
    changed = 0
    for section in SECTIONS:
        md = GLOSSARY / "sections" / ("%s.md" % section)
        if not md.is_file():
            raise SystemExit("缺节文件: %s" % md)
        old = md.read_text(encoding="utf-8")
        new = splice(old, render(section, rows))
        hits = sum(1 for r in rows
                   if section in [t.strip() for t in (r.get("sections") or "").split(";") if t.strip()])
        print("%-11s pointer rows=%3d  changed=%s" % (section, hits, new != old))
        if new != old:
            changed += 1
            if args.apply:
                md.write_text(new, encoding="utf-8", newline="\n")
    print("files changed=%d%s" % (changed, "" if args.apply else " (dry-run)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
