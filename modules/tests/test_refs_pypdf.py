"""retraction_scan.py reads PDFs with `pypdf` (PyPDF2 is unmaintained).

The PDF here is assembled byte-by-byte with a correct xref table so the test needs
no extra dependency, and the module is loaded by path because `tools/verify-refs`
is not an importable package name.

Run: python -B -m pytest modules/tests/test_refs_pypdf.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY_REFS = ROOT / "tools" / "verify-refs"
SCRIPT = VERIFY_REFS / "retraction_scan.py"
SAMPLE_TEXT = "Hello Retraction"


def _minimal_pdf(text: str) -> bytes:
    page_stream = ("BT /F1 24 Tf 20 100 Td (%s) Tj ET" % text).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(page_stream)).encode() + b" >>\nstream\n"
        + page_stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    size = len(objs) + 1
    out += b"xref\n0 " + str(size).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += b"trailer\n<< /Size " + str(size).encode() + b" /Root 1 0 R >>\n"
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    return bytes(out)


def _load_module():
    sys.path.insert(0, str(VERIFY_REFS))
    spec = importlib.util.spec_from_file_location("retraction_scan_pypdf", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_uses_pypdf_only() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import pypdf" in source
    assert "PyPDF2" not in source


def test_read_file_extracts_text_from_pdf(tmp_path: Path) -> None:
    module = _load_module()
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(_minimal_pdf(SAMPLE_TEXT))
    text = module.read_file(str(pdf))
    assert SAMPLE_TEXT.replace(" ", "") in text.replace(" ", "").replace("\n", "")


def test_read_file_still_reads_plain_text(tmp_path: Path) -> None:
    module = _load_module()
    txt = tmp_path / "refs.txt"
    txt.write_text("Smith J. 2020. A title.\n", encoding="utf-8")
    assert "A title" in module.read_file(str(txt))


def test_read_file_raises_on_missing_file() -> None:
    module = _load_module()
    try:
        module.read_file("definitely-missing.pdf")
    except FileNotFoundError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected FileNotFoundError")
