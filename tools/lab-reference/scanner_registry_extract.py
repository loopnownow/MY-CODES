"""Crawl 0doc manuscripts for imaging-scanner descriptions.

Stage --extract
    Read 05-manuscript thesis + manuscripts (.docx/.doc), dump plain text to
    {OUT}/txt, and write {OUT}/index.csv.
    Text extraction reuses docx_extract.extract_text (python-docx for .docx,
    one Word COM instance per .doc).

Stage --candidates
    For every extracted text, write {OUT}/candidates/<tier>__<name>.md holding
    (a) tables that look like scanner / parameter tables and (b) paragraphs that
    hit vendor or acquisition keywords. These files are the evidence base for the
    hand-built registry workbook at 0doc/_library/scanner-registry/scanner-parameter-registry.xlsx (restored t1553u; see MY-SKILLS scanner-registry-digest).

Source Word files were archived out of the tree on 2026-10-22: set ODOC_WORD if the
archive root is not D:/00db/_lab/doc-word.

Both stages are read-only with respect to 0doc; output goes to %TEMP%/lab-scratch (SCRATCH_ROOT overrides).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile

# docx_extract lives at 05-manuscript/write-paper/ (rehome 2026-10-23)
_CODE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_CODE_ROOT, "05-manuscript", "write-paper"))
from docx_extract import extract_text  # noqa: E402

# --- configuration -----------------------------------------------------------
TIERS = {
    "01": os.path.join(os.environ.get("ODOC_WORD") or r"D:\00db\_lab\doc-word",
                       "05-manuscript", "thesis"),
    "02": os.path.join(os.environ.get("ODOC_WORD") or r"D:\00db\_lab\doc-word",
                       "05-manuscript", "manuscripts"),
}
DOC_EXT = {".docx", ".doc"}
SCRATCH_ROOT = os.environ.get("SCRATCH_ROOT") or os.path.join(tempfile.gettempdir(), "lab-scratch")
OUT = os.path.join(SCRATCH_ROOT, "scanner-registry")
TXT_DIR = os.path.join(OUT, "txt")
CAND_DIR = os.path.join(OUT, "candidates")

VENDOR = re.compile(
    r"\b(?:Siemens|Philips|Canon|Toshiba|Hitachi|Aloka|Esaote|Mindray|Supersonic|"
    r"Aixplorer|LOGIQ|EPIQ|Voluson|Resona|Aquilion|Revolution|Ingenuity|Optima|"
    r"Discovery|Ingenia|Prisma|Skyra|Avanto|Verio|Achieva|Intera|TrioTim|"
    r"SOMATOM|Brilliance|LightSpeed|Signa|Titan|Vevo|MyLab|Aplio|Xario|ACUSON|"
    r"Affiniti|DermaScan|DUB)\b|"
    r"GE Healthcare|General Electric|United Imaging|Samsung Medison|"
    r"联影|迈瑞|西门子|飞利浦|佳能|东芝|日立",
    re.I,
)
PARAM = re.compile(
    r"TR\s*/\s*TE|repetition time|echo time|kVp?\b|\bmAs\b|collimation|"
    r"slice thickness|matrix|FOV|field of view|b\s*[=＝]\s*\d{2,4}|"
    r"mmol/kg|mL/s|ml/s|MHz|probe|transducer|探头|频率|"
    r"arterial phase|portal venous|delayed phase|delay",
    re.I,
)
MODALITY = re.compile(
    r"ultrasound|ultrasonograph|ultrasonic|超声|computed tomography|\bCT\b|"
    r"magnetic resonance|\bMRI\b|\bMR\b|spectroscopy|\bMRS\b",
    re.I,
)
REF = re.compile(r"\[J\]|et al\.?|doi\.org|\bdoi:\s*10\.|参考文献|References", re.I)

MAX_PARA = 80
MAX_PARA_CHARS = 1500
MAX_TABLE_ROWS = 40


def safe(name: str) -> str:
    """Filesystem-safe stem that keeps author/version clues readable."""
    return re.sub(r'[\\/:*?"<>|\s]+', "_", os.path.splitext(name)[0])[:120]


def iter_files(tier: str):
    root = TIERS[tier]
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() not in DOC_EXT:
                continue
            yield os.path.join(dirpath, fn)



def extract(tiers):
    os.makedirs(TXT_DIR, exist_ok=True)
    rows = []
    for tier in tiers:
        for path in iter_files(tier):
            name = os.path.basename(path)
            target = os.path.join(TXT_DIR, f"{tier}__{safe(name)}.txt")
            try:
                text = extract_text(path)
            except Exception as exc:  # keep going, record the failure
                rows.append([tier, name, path, 0, "ERR " + str(exc)[:120]])
                print(f"[ERR] {tier} {name}: {exc}")
                continue
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text)
            rows.append([tier, name, path, len(text), ""])
            print(f"[ok] {tier} {len(text):>8} {name[:80]}")
    with open(os.path.join(OUT, "index.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["tier", "file", "abspath", "chars", "note"])
        w.writerows(rows)
    print(f"extracted {len(rows)} files -> {TXT_DIR}")


def split_blocks(text: str):
    """Split text into ('table'|'para', content); tables are tab-joined rows."""
    blocks = []
    buf = []
    for line in text.splitlines():
        if "\t" in line:
            buf.append(line)
        else:
            if len(buf) >= 2:
                blocks.append(("table", buf))
            elif buf:
                blocks.append(("para", "\n".join(buf)))
            buf = []
            if line.strip():
                blocks.append(("para", line))
    if len(buf) >= 2:
        blocks.append(("table", buf))
    return blocks


def is_ref_table(rows) -> bool:
    """Reference lists also come out as tab tables; drop them."""
    hits = 0
    for row in rows[:10]:
        if re.match(r"\s*\[\d+\]", row) or "[J]" in row:
            hits += 1
    return hits >= 3


def candidates(tiers):
    os.makedirs(CAND_DIR, exist_ok=True)
    for tier in tiers:
        for path in iter_files(tier):
            name = os.path.basename(path)
            src = os.path.join(TXT_DIR, f"{tier}__{safe(name)}.txt")
            if not os.path.exists(src):
                continue
            text = open(src, encoding="utf-8").read()
            out = [
                f"# {tier} / {name}",
                f"- source: {path}",
                f"- chars: {len(text)}",
                "",
            ]
            n_table = n_para = 0
            for kind, content in split_blocks(text):
                if kind == "table":
                    if is_ref_table(content):
                        continue
                    joined = " ".join(content)
                    evidence = VENDOR.search(joined) or (
                        re.search(r"TR\s*/\s*TE", joined) and re.search(r"FOV|matrix|thickness", joined, re.I)
                    )
                    if evidence and not (REF.search(joined) and not VENDOR.search(joined)):
                        n_table += 1
                        out.append(f"## TABLE {n_table} ({len(content)} rows)")
                        for row in content[:MAX_TABLE_ROWS]:
                            out.append("| " + row.replace("\t", " | "))
                        out.append("")
                else:
                    if n_para >= MAX_PARA:
                        continue
                    if REF.search(content) and not VENDOR.search(content):
                        continue
                    if len(content) < 150 and not VENDOR.search(content):
                        continue
                    if VENDOR.search(content) or (PARAM.search(content) and MODALITY.search(content)):
                        n_para += 1
                        out.append(f"## PARA {n_para}")
                        out.append(content[:MAX_PARA_CHARS].replace("\n", " "))
                        out.append("")
            if n_table or n_para:
                dst = os.path.join(CAND_DIR, f"{tier}__{safe(name)}.md")
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(out))
                print(f"[cand] {tier} tables={n_table} paras={n_para} {name[:70]}")


def sentences(tiers):
    """One compact evidence file: keyword-bearing sentences per source file."""
    os.makedirs(CAND_DIR, exist_ok=True)
    out = []
    for tier in tiers:
        for path in iter_files(tier):
            name = os.path.basename(path)
            src = os.path.join(TXT_DIR, f"{tier}__{safe(name)}.txt")
            if not os.path.exists(src):
                continue
            text = open(src, encoding="utf-8").read()
            hits = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "\t" in line:  # table row: keep whole row
                    if VENDOR.search(line) or re.search(r"TR\s*/\s*TE", line):
                        hits.append("[T] " + re.sub(r"\t+", " | ", line)[:600])
                    continue
                for sent in re.split(r"(?<=[。！？；;])", line):
                    sent = sent.strip()
                    if len(sent) < 15 or len(hits) > 120:
                        continue
                    if VENDOR.search(sent) or (PARAM.search(sent) and MODALITY.search(sent)):
                        hits.append(sent[:600])
            if hits:
                out.append(f"\n===== {tier} :: {name}")
                out.extend("- " + h for h in hits)
    dst = os.path.join(OUT, "evidence_sentences.md")
    body = "\n".join(out)
    with open(dst, "w", encoding="utf-8") as fh:
        fh.write(body)
    print(f"sentences -> {dst} chars={len(body)} lines={body.count(chr(10))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["extract", "candidates", "sentences", "all"], default="all")
    ap.add_argument("--tier", choices=["01", "02", "all"], default="all")
    args = ap.parse_args()
    tiers = list(TIERS) if args.tier == "all" else [args.tier]
    os.makedirs(OUT, exist_ok=True)
    if args.stage in ("extract", "all"):
        extract(tiers)
    if args.stage in ("candidates", "all"):
        candidates(tiers)
    if args.stage in ("sentences", "all"):
        sentences(tiers)


if __name__ == "__main__":
    main()
