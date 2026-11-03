"""C1: public-path policy — no accidental absolute lab-path sprawl.

Allowlist the documented lab exception in modules.console.paths.DEFAULT_GROK_ROOT.
Everything else under the repo should prefer env / relative / example paths.
Lab topology SSOT remains MY-SKILLS lab-paths.yaml (do not reinvent here).
"""
from __future__ import annotations

import json
import re
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Documented lab exception only (C1).
ALLOW_FILES = {
    "modules/console/paths.py",
}

# Absolute Windows drive paths that look like this lab (not generic C:\Windows).
LAB_ABS = re.compile(
    r"[A-Za-z]:\\(?:0Grok|Users\\[^\\]+\\(?:0Grok|Documents\\GitHub))",
    re.I,
)
# 盘符 + 1~2 个反斜杠：单反斜杠写法与 JSON/JS 里的 `\\` 转义写法都算（旧 LAB_ABS 只认单反斜杠）。
DRIVE_LETTER = re.compile(r"[A-Za-z]:\\{1,2}")
# 模板文件：默认值必须靠 serve 时注入，不许写死本机路径。
TEMPLATE_FILES = (
    "modules/console/project.html",
    "modules/console/console.override.example.json",
)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "egg-info"}
SUFFIXES = {".py", ".md", ".txt", ".toml", ".ini", ".yml", ".yaml", ".cfg", ".json", ".html", ".js", ".css"}


class TestPublicPathPolicy(unittest.TestCase):
    def test_no_lab_absolute_path_sprawl(self) -> None:
        offenders: list[str] = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUFFIXES:
                continue
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            rel = path.relative_to(ROOT)
            if rel.as_posix() in ALLOW_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if LAB_ABS.search(line):
                    offenders.append(f"{rel.as_posix()}:{i}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "absolute lab paths outside the DEFAULT_GROK_ROOT allowlist:\n"
            + "\n".join(offenders),
        )


class TestDriveLetterGuard(unittest.TestCase):
    """C1 加严：盘符路径的两种写法都要抓住（旧 LAB_ABS 只认单反斜杠，转义写法漏网）。"""

    def test_regex_catches_single_and_escaped(self) -> None:
        drive, sep = "D:", "\\"
        single = f"{drive}{sep}0Grok{sep}0doc"
        escaped = f"{drive}{sep * 2}0Grok{sep * 2}0doc"
        for sample in (single, escaped):
            self.assertIsNotNone(DRIVE_LETTER.search(sample), sample)

    def test_console_templates_have_no_drive_letter(self) -> None:
        offenders: list[str] = []
        for rel in TEMPLATE_FILES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), 1):
                if DRIVE_LETTER.search(line):
                    offenders.append(f"{rel}:{i}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "模板不许写死盘符路径（该由 serve 时注入 __MODULES_DEFAULT__）:\n",
        )


class TestModulesDefaultInjection(unittest.TestCase):
    """serve `/project.html` 时把占位换成机器无关的真实 modules 根（本机自启、只绑 127.0.0.1）。"""

    def test_project_html_serves_injected_modules_default(self) -> None:
        from modules.console import console as cons
        from modules.console import state as console_state
        from modules.console.const import MODULES_ROOT

        httpd = None
        port = 0
        for candidate in range(cons.PORT_LO, cons.PORT_HI + 1):
            try:
                httpd = cons.ConsoleServer((cons.HOST, candidate), cons.Handler)
                port = candidate
                break
            except OSError:
                continue
        if httpd is None:
            self.fail("console 端口带 8765-8776 全被占用，无法起服务验证注入")
        console_state.SERVE_PORT = port
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            conn = HTTPConnection(cons.HOST, port, timeout=5)
            conn.request("GET", "/project.html")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            status = resp.status
            conn.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            console_state.SERVE_PORT = None

        literal = json.dumps(str(MODULES_ROOT))
        self.assertEqual(status, 200)
        self.assertNotIn("__MODULES_DEFAULT__", body)
        self.assertIn(f"const DEFAULT_MODULES = {literal}", body)
        self.assertEqual(MODULES_ROOT.name, "modules")


if __name__ == "__main__":
    unittest.main()
