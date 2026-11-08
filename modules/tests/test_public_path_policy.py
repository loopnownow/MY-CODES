"""C1: public-path policy — no accidental absolute lab-path sprawl.

Allowlist the documented lab exception in modules.console.paths.DEFAULT_GROK_ROOT.
Everything else under the repo should prefer env / relative / example paths.
Lab topology SSOT remains MY-SKILLS lab-paths.yaml (do not reinvent here).

2026-11-06 QC: the old LAB_ABS missed the `{RAW}` (`00db`) and `{DEL}` (`0del`) roots
and the `R&P` workspace, so hardcoded archive paths stayed green. Those roots are
allowed only on a line that also names an env var (env-overridable default).

2026-11-08 t1823u: also flag ANY absolute drive-letter default at module level
(env-overridable lines pass) and patient PII (all-caps 3-token name next to
imaging paths), so the public repo cannot drift back to a local machine layout.
"""
from __future__ import annotations

import ast
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

# 本机 lab 形状的绝对路径：单反斜杠与 JSON/JS 里的 `\\` 转义写法都要抓住。
LAB_ABS = re.compile(
    r"[A-Za-z]:\\{1,2}(?:0Grok|Users\\{1,2}[^\\]+\\{1,2}(?:0Grok|Documents\\{1,2}GitHub))",
    re.I,
)
# `{RAW}` / `{DEL}` / lab root (`MyProjects`)：仅当同一行点明环境变量名（可覆盖的默认值）才放行。
ROOT_ABS = re.compile(r"[A-Za-z]:[\\/]{1,2}(?:00db|0del|MyProjects)", re.I)
ENV_GUARD = re.compile(
    r"environ|DEL_ROOT|ODOC_WORD|GROK_ROOT|STATS_ROOT|SKILLS_ROOT|MY_SKILLS|GLOSSARY_ROOT",
    re.I,
)
# 本机会话态：console 运行时自己写绝对路径，且已被 .gitignore 忽略（不是仓库内容）。
LOCAL_STATE_FILES = {
    "modules/console/console.override.json",
    "modules/console/console.port.json",
    "modules/console/console.projects.json",
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "egg-info"}
SUFFIXES = {".py", ".md", ".txt", ".toml", ".ini", ".yml", ".yaml", ".cfg", ".json", ".html", ".js", ".css"}

# t1823u: 任意盘符的绝对路径只允许作为 env 可覆盖默认值（同一行须出现 ENV_GUARD 名称）。
DRIVE_LITERAL = re.compile(r"^[A-Za-z]:[\\/]")
# 测试夹具本身使用合成盘符（C:\tmp、D:\fake…），不参与本策略。
TEST_DIRS = {"modules/tests", "test"}
# t1823u: 全大写三连姓名 + 影像路径同现 → 疑似患者 PII 回潮（脱敏后用 patient_A 等占位）。
PII_NAME = re.compile(r"\b[A-Z]{2,}(?: [A-Z]{2,}){2,}\b")
PII_CTX = re.compile(r"\.nii|\.dcm|img[/\\]|roi[/\\]|DICOM", re.I)


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
            if rel.as_posix() in ALLOW_FILES or rel.as_posix() in LOCAL_STATE_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                bad = bool(LAB_ABS.search(line)) or (
                    bool(ROOT_ABS.search(line)) and not ENV_GUARD.search(line)
                )
                if bad:
                    offenders.append(f"{rel.as_posix()}:{i}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "absolute lab paths outside the DEFAULT_GROK_ROOT allowlist "
            "({RAW}/{DEL} roots need an env-overridable default on the same line):\n"
            + "\n".join(offenders),
        )


class TestLabPathRegex(unittest.TestCase):
    """旧 LAB_ABS 只认单反斜杠，JSON/JS 里转义成两个反斜杠的写法会漏网（C1 因此一直绿）。"""

    def test_catches_single_and_escaped(self) -> None:
        drive, sep, sep2 = "D:", "\\", "\\\\"
        samples = [
            f"{drive}{sep}0Grok{sep}0doc",
            f"{drive}{sep2}0Grok{sep2}0doc",
            f"{drive}{sep}Users{sep}lab{sep}0Grok",
            f"{drive}{sep2}Users{sep2}lab{sep2}Documents{sep2}GitHub",
        ]
        for sample in samples:
            self.assertIsNotNone(LAB_ABS.search(sample), sample)

    def test_leaves_generic_examples_alone(self) -> None:
        for sample in (r"C:\tmp\other", r"C:\lab\stats-analysis\modules", r"D:\tmp\other\modules"):
            self.assertIsNone(LAB_ABS.search(sample), sample)


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


class TestNoHardcodedDriveDefaults(unittest.TestCase):
    """t1823u: 模块级常量不得写死本机盘符；env 可覆盖的默认值由同一行的 ENV_GUARD 放行。"""

    def test_module_level_drive_literals_are_env_guarded(self) -> None:
        offenders: list[str] = []
        for path in sorted(ROOT.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            if rel in ALLOW_FILES or any(rel == d or rel.startswith(d + "/") for d in TEST_DIRS):
                continue
            try:
                text = path.read_text(encoding="utf-8")
                tree = ast.parse(text)
            except (UnicodeDecodeError, OSError, SyntaxError):
                continue
            lines = text.splitlines()
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    value = getattr(node, "value", None)
                    if value is None:
                        continue
                    for sub in ast.walk(value):
                        if not (isinstance(sub, ast.Constant) and isinstance(sub.value, str)):
                            continue
                        if not DRIVE_LITERAL.match(sub.value):
                            continue
                        src = lines[sub.lineno - 1] if sub.lineno <= len(lines) else ""
                        if ENV_GUARD.search(src):
                            continue
                        offenders.append(f"{rel}:{sub.lineno}: {src.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "module-level absolute drive literal without an env-overridable default "
            "(add os.environ.get(...) on the same line, or an allowlist entry):\n"
            + "\n".join(offenders),
        )


class TestNoPatientPiiLeak(unittest.TestCase):
    """t1823u: 影像路径与全大写三连姓名同现 → 疑似患者 PII；公开仓只允许合成占位。"""

    def test_no_patient_name_next_to_imaging_paths(self) -> None:
        offenders: list[str] = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel in LOCAL_STATE_FILES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if PII_NAME.search(line) and PII_CTX.search(line):
                    offenders.append(f"{rel}:{i}: {line.strip()[:120]}")
        self.assertEqual(
            offenders,
            [],
            "patient-name-like token next to imaging paths; replace with synthetic "
            "placeholders (patient_A / 张三):\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
