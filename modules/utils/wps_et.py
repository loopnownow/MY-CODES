"""Drive WPS Spreadsheets (ET) to recalculate an .xlsx in place.

The lab PC uses WPS, not LibreOffice. openpyxl writes formulas as strings with no
cached value; KET.Application opens the book, CalculateFull, and saves so a later
data-only scan can see computed values and Excel error literals.
"""
from __future__ import annotations

import winreg
from pathlib import Path

_APP_PATH_KEYS = (
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths\et.exe"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths\et.exe"),
)
_PROGID_KEYS = (
    (winreg.HKEY_CURRENT_USER, r"Software\Classes\KET.Application"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes\KET.Application"),
)


def _first_existing(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def _reg_default(root: int, key: str) -> str | None:
    try:
        with winreg.OpenKey(root, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "")
    except OSError:
        return None
    text = str(value or "").strip().strip('"')
    return text or None


def _ket_progid_registered() -> bool:
    for root, key in _PROGID_KEYS:
        try:
            winreg.OpenKey(root, key)
            return True
        except OSError:
            continue
    return False


def find_et_exe() -> Path | None:
    found: list[Path] = []
    for root, key in _APP_PATH_KEYS:
        raw = _reg_default(root, key)
        if not raw:
            continue
        path = Path(raw)
        # App Paths may point at wps.exe; prefer sibling et.exe.
        if path.name.lower() != "et.exe":
            sibling = path.with_name("et.exe")
            found.append(sibling)
        found.append(path)
    which = __import__("shutil").which("et")
    if which:
        found.append(Path(which))
    office_root = Path(r"D:\WPS Office")
    if office_root.is_dir():
        versions = sorted(
            (p for p in office_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        for ver in versions:
            found.append(ver / "office6" / "et.exe")
    return _first_existing(found)


def wps_et_available() -> bool:
    return find_et_exe() is not None or _ket_progid_registered()


def recalculate_xlsx(path: Path) -> None:
    """Recalculate every formula in path and save in place.

    Raises OSError if WPS cannot be started, or RuntimeError if Calculate/Save fails.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if not wps_et_available():
        raise OSError("WPS Spreadsheets (ET) is not installed")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    wb = None
    try:
        app = win32com.client.DispatchEx("KET.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        wb = app.Workbooks.Open(str(path), UpdateLinks=False, ReadOnly=False)
        # CalculateFull is the Excel-compatible full rebuild; fall back to Calculate.
        try:
            app.CalculateFull()
        except Exception:
            app.Calculate()
        wb.Save()
        wb.Close(SaveChanges=True)
        wb = None
        app.Quit()
        app = None
    except Exception as exc:
        raise RuntimeError(f"WPS failed to recalculate {path}: {exc}") from exc
    finally:
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
