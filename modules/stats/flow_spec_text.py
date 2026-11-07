# -*- coding: utf-8 -*-
"""Manuscript text -> Figure 1 study-flow spec, field by field.

Standard library only (no matplotlib): the console flowchart tab imports this
module to prefill the form from a manuscript.

    from modules.stats.flow_spec_text import docx_paragraphs, methods_section, spec_from_text
    out = spec_from_text(methods_section(docx_paragraphs("Manuscript_x_V1.docx")))
    out["spec"]      # keys that the Materials and Methods actually state
    out["missing"]   # fields it does not state; never invented
    out["evidence"]  # field -> source sentence, for the reader to check

`spec_from_text` never raises on a short Methods: absent fields are listed in
`missing` and left out of `spec`. The strict gate used by the pipeline CLI stays
in figure_strobe_flow.spec_from_methods, which raises AuditError instead.
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

MAX_ITEMS = 6
MAX_ITEM_CHARS = 220
EVIDENCE_CHARS = 240
CRITERIA_WINDOW = 1600

_N_EQ = re.compile(r"\bn\s*=\s*(\d+)\b", re.I)
_DATE = re.compile(
    r"(?:\d{1,2}[\s,]+)?(?:[A-Z][a-z]+[\s,]+)?(?:\d{1,2}[\s,]+)?\d{4}"
)
_LABEL_PAIR = re.compile(
    r"([A-Za-z][A-Za-z0-9\-/ ]{0,40}?),?\s*n\s*=\s*(\d+)\s*;\s*"
    r"([A-Za-z][A-Za-z0-9\-/ ]{0,40}?),?\s*n\s*=\s*(\d+)"
)
_BARE_PAIR = re.compile(
    r"\(?\b([A-Za-z][A-Za-z0-9\-]{1,24})\s+(\d{1,6})\s*[;,]\s*"
    r"([A-Za-z][A-Za-z0-9\-]{1,24})\s+(\d{1,6})\b"
)
_BAD_LABEL = re.compile(
    r"^(?:with|and|or|to|of|the|a|an|in|on|at|for|was|were|is|are|by|from|than)$", re.I
)
_PERIOD_BETWEEN = re.compile(
    r"\bbetween\s+(.{3,40}?)\s+and\s+(.{3,40}?)(?:\.|,|\s+were|\s+was|\s+who|\s+with|$)", re.I | re.M
)
_PERIOD_FROM = re.compile(
    r"\bfrom\s+((?:[A-Z][a-z]+ )?\d{1,2},?\s+?\d{4}|(?:[A-Z][a-z]+ )?\d{4})\s+(?:to|through)\s+"
    r"((?:[A-Z][a-z]+ )?\d{1,2},?\s+?\d{4}|(?:[A-Z][a-z]+ )?\d{4})"
)
_SCREENED = re.compile(
    r"(\d[\d,]*)\s+(?:[A-Za-z\-]+\s+){0,6}(?:patients?|nodules?|cases?|subjects?|records?|women|men)"
    r"[^.\n]{0,160}?\b(?:were|was)\s+(?:screened|identified|recruited|reviewed|collected|drawn)",
    re.I,
)
_ENROLLED = re.compile(
    r"(\d[\d,]*)\s+(?:[A-Za-z\-]+\s+){0,6}(?:patients?|nodules?|cases?|subjects?|records?|women|men)"
    r"[^.\n]{0,120}?\b(?:were|was)\s+(?:analyzed|analysed|included|enrolled)"
    r"|(\d[\d,]*)\s+(?:[A-Za-z\-]+\s+){0,6}(?:patients?|nodules?|cases?|subjects?|records?)"
    r"[^.\n]{0,120}?\b(?:entered|remained in|comprised|met all)\b",
    re.I,
)
_COHORT_KEY = r"(training|test|validation|external|derivation)"
_COHORT_NOUN = r"(?:patients?|nodules?|cases?|subjects?|records?|women|men|examinations?|scans?)"
# training set (n = 216; positive n = 91; negative n = 125)
_COHORT_PAREN = re.compile(
    _COHORT_KEY + r"\s+(?:cohort|set|group)[^.\n]{0,20}?\(\s*n\s*=\s*(\d+)[^)]*\)", re.I
)
# 186 patients were assigned to the training cohort
_COHORT_ASSIGNED = re.compile(
    r"(\d[\d,]*)\s+" + _COHORT_NOUN + r"\b[^.\n]{0,60}?\b" + _COHORT_KEY
    + r"\s+(?:cohort|set|group)", re.I
)
# the training cohort comprised 186 patients
_COHORT_CONTAINS = re.compile(
    _COHORT_KEY + r"\s+(?:cohort|set|group)[^.\n]{0,40}?(\d[\d,]*)\s+" + _COHORT_NOUN, re.I
)
# divided into a development set and a validation set (191 and 85 examinations, respectively)
_COHORT_RESPECTIVELY = re.compile(
    r"(?:development|training)\s+(?:set|cohort)[^.\n]{0,80}?(?:validation|test)\s+(?:set|cohort)"
    r"[^.\n]{0,80}?\((\d[\d,]*)\s+and\s+(\d[\d,]*)\s+" + _COHORT_NOUN + r"[^)]*\)",
    re.I,
)
_ALLOC = re.compile(
    r"(?:In total|A total of)[,\s]+(\d[\d,]*)\s+patients[^.]{0,160}?"
    r"(?:training|derivation)\s+(?:cohort|set)[^.]{0,40}?\(\s*n\s*=\s*(\d+)\s*\)"
    r"[^.]{0,160}?(?:validation|test)\s+(?:cohort|set)[^.]{0,40}?\(\s*n\s*=\s*(\d+)\s*\)",
    re.I,
)
_FINAL_TRAIN = re.compile(
    r"(?:final training cohort of|leaving)\s+(\d[\d,]*)\s+(?:training\s+)?patients",
    re.I,
)
_ANALYZED_FILE = re.compile(
    r"(?:analysis file|analytic sample)[^.]{0,80}?comprised\s+(\d[\d,]*)\s+patients"
    r"|comprised\s+(\d[\d,]*)\s+patients",
    re.I,
)
_CRITERIA_HARD_STOPS = (
    r"\bExclusion criteria\b",
    r"\bInclusion criteria\b",
    r"\bModel (?:building|development)\b",
    r"\bStatistical analysis\b",
    r"\bAmong (?:them|these)\b",
    r"\bFinally,?\s+a total\b",
    r"\bFinally,?\s+\d",
    r"\b[Pp]atients? (?:were|was) (?:assigned|enrolled|divided|analy[sz]ed)\b",
)
_BULLET_START = re.compile(r"^(?:\(?\d+[.)]|\(?[A-H][.)]|[-鈥?路])")



class AuditError(ValueError):
    """n-audit failed; CLI exits nonzero."""


def docx_paragraphs(path) -> list[str]:
    """Paragraph text from a docx. Insertions are kept. Deletions are not."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    paragraphs = []
    for para in root.iter(ns + "p"):
        parts = []
        for node in para.iter():
            if node.tag == ns + "delText":
                continue
            if node.tag == ns + "t" and node.text:
                parts.append(node.text)
        line = "".join(parts).strip()
        if line:
            paragraphs.append(line)
    return paragraphs


def methods_section(paragraphs: list[str]) -> str:
    """Joined Materials and Methods paragraphs. Raises AuditError when absent."""
    start = None
    for i, line in enumerate(paragraphs):
        if re.fullmatch(r"(Materials and Methods|Methods)", line, flags=re.I):
            start = i + 1
            break
    if start is None:
        raise AuditError(
            "Materials and Methods was not found. The figure text is taken only from that section."
        )
    end = len(paragraphs)
    for i in range(start, len(paragraphs)):
        if re.fullmatch(r"Results", paragraphs[i], flags=re.I):
            end = i
            break
    text = "\n".join(paragraphs[start:end]).strip()
    if not text:
        raise AuditError("Materials and Methods is empty.")
    return text

def _snippet(text: str, start: int, end: int) -> str:
    line = text[max(0, start):min(len(text), end)]
    line = re.sub(r"\s+", " ", line).strip()
    return line[:EVIDENCE_CHARS]


def _count(value: str) -> int:
    return int(str(value).replace(",", ""))


def _pair(block: str) -> dict | None:
    """'growth, n = 207; non-growth, n = 368' or '(growth 207; non-growth 368)'."""
    for match in (
        _LABEL_PAIR.search(block),
        _BARE_PAIR.search(block),
    ):
        if not match:
            continue
        positive_label = match.group(1).strip()
        negative_label = match.group(3).strip()
        if _BAD_LABEL.match(positive_label) or _BAD_LABEL.match(negative_label):
            continue
        return {
            "positive_label": positive_label,
            "positive": _count(match.group(2)),
            "negative_label": negative_label,
            "negative": _count(match.group(4)),
        }
    return None


def _pair_summing_to(text: str, total: int) -> dict | None:
    """First label/count pair whose two counts add up to `total`."""
    for regex in (_LABEL_PAIR, _BARE_PAIR):
        for match in regex.finditer(text):
            positive_label = match.group(1).strip()
            negative_label = match.group(3).strip()
            if _BAD_LABEL.match(positive_label) or _BAD_LABEL.match(negative_label):
                continue
            positive = _count(match.group(2))
            negative = _count(match.group(4))
            if positive + negative != total:
                continue
            return {
                "positive_label": positive_label,
                "positive": positive,
                "negative_label": negative_label,
                "negative": negative,
            }
    return None


def _is_date(value: str) -> bool:
    return bool(_DATE.fullmatch(value.strip()))


def _period(text: str) -> tuple[str, int, int] | None:
    """Study period as 'start to end', validated against a year in both ends."""
    for match in _PERIOD_BETWEEN.finditer(text):
        start, end = match.group(1).strip(), match.group(2).strip()
        if _is_date(start) and _is_date(end):
            return f"{start} to {end}", match.start(), match.end()
    match = _PERIOD_FROM.search(text)
    if match:
        start, end = match.group(1).strip(), match.group(2).strip()
        if _is_date(start) and _is_date(end):
            return f"{start} to {end}", match.start(), match.end()
    return None



def _item_n(item: str) -> int | None:
    match = _N_EQ.search(item)
    if match:
        return int(match.group(1))
    match = re.search(r"(\d[\d,]*)\s+(?:patients?|nodules?|cases?|subjects?)", item, flags=re.I)
    return _count(match.group(1)) if match else None


def _split_items(block: str) -> list[str]:
    head = block.strip()
    numbered = re.split(r"\(\d\)\s*", head)
    if len(numbered) > 1:
        parts = numbered[1:]
    else:
        lettered = re.split(r"(?:^|\s)\(?[A-H][.)]\s+", head)
        if len(lettered) > 1:
            parts = lettered[1:]
        else:
            parts = re.split(r"(?<=[.;])\s+", head)
    items = []
    for part in parts:
        part = re.sub(r"^(?:and|or)\s+", "", part.strip(), flags=re.I)
        part = re.sub(r"[\s;,]*(?:and|or)$", "", part.strip(" ;.\n"), flags=re.I)
        part = part.strip(" ;.,\n")
        if len(part) < 4:
            continue
        items.append(part[:MAX_ITEM_CHARS])
    return items




def _heading_like(line: str) -> bool:
    """A short title line: 'Clinical information', not a criteria bullet."""
    text = line.strip()
    if not text or len(text) > 80 or _BULLET_START.match(text):
        return False
    if not text[0].isupper() or re.search(r"[;:,]$", text):
        return False
    return len(text.split()) <= 8


def _cut_block(rest: str) -> str:
    cut = len(rest)
    for pattern in _CRITERIA_HARD_STOPS:
        found = re.search(pattern, rest, flags=re.I)
        if found and 0 < found.start() < cut:
            cut = found.start()
    offset = 0
    for line in rest.split("\n"):
        if offset and _heading_like(line):
            cut = min(cut, offset)
            break
        offset += len(line) + 1
    return rest[:cut]


def _criteria_items(text: str, label: str) -> list[str]:
    match = re.search(label + r"[^.\n]{0,80}?:", text, flags=re.I)
    if not match:
        return []
    rest = text[match.end():match.end() + CRITERIA_WINDOW]
    return _split_items(_cut_block(rest))[:MAX_ITEMS]


def _cohort_key(raw: str) -> str:
    key = (raw or "").lower()
    if key == "derivation":
        return "training"
    if key == "external":
        return "validation"
    return key


def _cohorts(text: str) -> dict[str, dict]:
    cohorts: dict[str, dict] = {}

    def put(key: str, n: int, start: int, end: int) -> None:
        if not key or key in cohorts:
            return
        window = text[max(0, start - 200):min(len(text), end + 240)]
        pair = _pair(window)
        cohorts[key] = {
            "n": n,
            "positive": pair["positive"] if pair else None,
            "negative": pair["negative"] if pair else None,
            "evidence": _snippet(text, start, end + 240),
        }

    for match in _COHORT_RESPECTIVELY.finditer(text):
        put("training", _count(match.group(1)), match.start(), match.end())
        put("validation", _count(match.group(2)), match.start(), match.end())
    for match in _COHORT_PAREN.finditer(text):
        put(_cohort_key(match.group(1)), int(match.group(2)), match.start(), match.end())
    for match in _COHORT_ASSIGNED.finditer(text):
        put(_cohort_key(match.group(2)), _count(match.group(1)), match.start(), match.end())
    for match in _COHORT_CONTAINS.finditer(text):
        put(_cohort_key(match.group(1)), _count(match.group(2)), match.start(), match.end())
    return cohorts



def spec_from_text(text: str) -> dict:
    """Tolerant field-by-field prefill spec. Never raises on a short Methods.

    Returns {"spec": {found keys only}, "missing": [field names],
    "evidence": {field: source sentence}}.
    """
    spec: dict = {"kind": "study", "source": "materials-and-methods"}
    evidence: dict[str, str] = {}
    missing: list[str] = []
    allocation = _ALLOC.search(text)

    period = _period(text)
    if period:
        spec["period"], period_start, period_end = period
        evidence["period"] = _snippet(text, period_start, period_end)
    else:
        missing.append("study period")

    screened = _SCREENED.search(text)
    if screened:
        spec["screened_n"] = _count(screened.group(1))
        evidence["screened_n"] = _snippet(text, screened.start(), screened.end())
    elif allocation:
        spec["screened_n"] = _count(allocation.group(1))
        evidence["screened_n"] = _snippet(text, allocation.start(), allocation.end())
    else:
        missing.append("screened n")

    enrolled = _ENROLLED.search(text)
    if enrolled:
        total = _count(enrolled.group(1) or enrolled.group(2))
        spec["included_n"] = total
        window = text[enrolled.start():enrolled.start() + 260]
        pair = _pair_summing_to(window, total) or _pair_summing_to(text, total)
        if pair:
            spec["included_positive"] = pair["positive"]
            spec["included_negative"] = pair["negative"]
            spec["included_positive_label"] = pair["positive_label"]
            spec["included_negative_label"] = pair["negative_label"]
            evidence["included_pair"] = (
                pair["positive_label"] + f" {pair['positive']}; "
                + pair["negative_label"] + f" {pair['negative']}"
            )
        evidence["included_n"] = _snippet(text, enrolled.start(), enrolled.end())
    if "included_n" not in spec:
        missing.append("included n")
    if "included_positive" not in spec:
        missing.append("included positive and negative counts")

    inclusion = _criteria_items(text, r"\bInclusion criteria\b")
    if inclusion:
        spec["inclusion"] = inclusion
        evidence["inclusion"] = inclusion[0]
    elif re.search(r"\bInclusion criteria\b", text, flags=re.I):
        missing.append("inclusion criteria list")

    exclusion = [
        {"reason": item, "n": _item_n(item)}
        for item in _criteria_items(text, r"\bExclusion criteria\b")
    ]
    if exclusion:
        spec["exclusion"] = exclusion
        evidence["exclusion"] = exclusion[0]["reason"]
    elif re.search(r"\bExclusion criteria\b", text, flags=re.I):
        missing.append("exclusion criteria list")

    cohorts = _cohorts(text)
    for key in ("training", "test", "validation"):
        if key in cohorts:
            entry = cohorts[key]
            spec[key] = {
                "n": entry["n"],
                "positive": entry["positive"],
                "negative": entry["negative"],
            }
            evidence[key] = entry["evidence"]
    final_train = _FINAL_TRAIN.search(text)
    analyzed_file = _ANALYZED_FILE.search(text)
    if allocation:
        pre_train = _count(allocation.group(2))
        pre_test = _count(allocation.group(3))
        spec["presplit"] = {
            "training": {"label": "Training set", "n": pre_train},
            "test": {"label": "Test set", "n": pre_test},
        }
        spec.setdefault("training", {"n": pre_train})
        spec.setdefault("test", {"n": pre_test})
        if final_train:
            final_n = _count(final_train.group(1))
            if final_n < pre_train:
                spec["downsample"] = {
                    "n": pre_train - final_n,
                    "reason": "Downsampling for class imbalance",
                    "arm": "Training set",
                }
                spec["splits"] = [
                    {"label": "Training Cohort", "n": final_n},
                    {"label": "Validation Cohort", "n": pre_test},
                ]
                evidence["downsample"] = f"derived: {pre_train} - {final_n} = {pre_train - final_n}"
        if analyzed_file:
            total = _count(analyzed_file.group(1) or analyzed_file.group(2))
            spec["analyzed"] = {"n": total}
            window = text[max(0, analyzed_file.start() - 200):analyzed_file.end() + 260]
            pair = _pair_summing_to(window, total) or _pair_summing_to(text, total)
            if pair:
                spec["analyzed"]["pp"] = pair["positive"]
                spec["analyzed"]["non_pp"] = pair["negative"]
            evidence["analyzed"] = _snippet(text, analyzed_file.start(), analyzed_file.end())
        # "were included and divided" 的 N 是入口人数，不是 included 节点
        if spec.get("included_n") == spec.get("screened_n"):
            spec.pop("included_n", None)
    for key, label in (("training", "training set n"), ("test", "test set n")):
        if key not in spec and allocation is None:
            missing.append(label)

    if re.search(r"\bSMOTE\b", text) and re.search(r"downsampl", text, flags=re.I):
        missing.append("Methods names both SMOTE and downsample; the figure draws only one")
    elif re.search(r"\bSMOTE\b", text):
        spec["balance"] = "smote"
    elif re.search(r"downsampl", text, flags=re.I):
        spec["balance"] = "downsample"

    def mentioned(pattern: str) -> bool:
        return re.search(pattern, text, flags=re.I) is not None

    spec["lasso"] = mentioned(r"\bLASSO\b")
    spec["models"] = [
        key
        for key, pattern in (
            ("clinical", r"clinical model"),
            ("radiomics", r"radiomics"),
            ("nomogram", r"nomogram"),
        )
        if mentioned(pattern)
    ]
    spec["metrics"] = [
        key
        for key, pattern in (
            ("roc", r"\bROC\b"),
            ("calibration", r"calibration"),
            ("dca", r"\bDCA\b|decision curve"),
            ("nri", r"\bNRI\b"),
            ("idi", r"\bIDI\b"),
        )
        if mentioned(pattern)
    ]
    spec["explain"] = [
        key
        for key, pattern in (("dag", r"\bDAG\b"), ("mediation", r"mediation"))
        if mentioned(pattern)
    ]
    return {"spec": spec, "missing": missing, "evidence": evidence}

