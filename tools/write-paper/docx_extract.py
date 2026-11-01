"""Extract plain text from .docx / .doc files.

Reusable helper for bank building. Reads paragraphs, table cells, and
(optionally) Word comments.

.doc uses Word COM (Word.Application), which this machine has.
Call close_word() when finished to release the Word process.
"""
from __future__ import annotations

import os
import re

def close_word():
    """No-op, kept for API compatibility.

    .doc extraction now uses one Word instance per file, so nothing to release.
    """


def _norm(text: str) -> str:
    # Word Content.Text uses \x07 as a table-cell marker; \x0b/\x0c as breaks.
    text = text.replace("\x07", "\t").replace("\x0b", "\n").replace("\x0c", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Drop any remaining control characters (keep tab and newline).
    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)


def _extract_docx(path: str, include_comments: bool) -> str:
    import docx

    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.append("\t".join(c.text.strip() for c in row.cells))
    text = "\n".join(parts)
    if include_comments:
        comments = _docx_comments(path)
        if comments.strip():
            text = text + "\n\n[Word comments]\n" + comments
    return text


def _docx_comments(path: str) -> str:
    """Read word/comments.xml if present (Word comment text)."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            if "word/comments.xml" not in z.namelist():
                return ""
            xml = z.read("word/comments.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    chunks = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S)
    return "".join(chunks)


def _extract_doc(path: str, include_comments: bool) -> str:
    """Extract .doc text via Word COM.

    Uses a fresh Word instance per file. Reusing one instance crashes it on this
    machine (WPS-backed Word), which then fails every later file with
    'RPC server unavailable'.
    """
    import win32com.client as win32

    app = win32.Dispatch("Word.Application")
    app.Visible = False
    app.DisplayAlerts = 0
    doc = None
    try:
        doc = app.Documents.Open(os.path.abspath(path), ReadOnly=True)
        return _norm(doc.Content.Text)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        try:
            app.Quit()
        except Exception:
            pass


def extract_text(path: str, include_comments: bool = False) -> str:
    """Return plain text from a .docx or .doc file.

    Raises ValueError for other extensions.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return _extract_docx(path, include_comments)
    if ext == ".doc":
        return _extract_doc(path, include_comments)
    raise ValueError("unsupported extension: " + ext)
