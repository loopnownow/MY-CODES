"""C2: MODULES_DIR / RAD_MODULES provenance record + strict block."""
from __future__ import annotations

import os
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest import mock

from modules import __version__
from modules.provenance import collect, enforce, format_banner, format_html_note


class TestProvenance(unittest.TestCase):
    def test_canonical_record(self) -> None:
        here = Path(__file__).resolve().parents[1]
        rec = collect(here, version=__version__)
        self.assertTrue(rec["canonical"])
        self.assertEqual(rec["source"], "package")
        self.assertEqual(rec["version"], __version__)
        self.assertIn("modules_dir", rec)
        self.assertIn("code_root", rec)
        banner = format_banner(rec)
        self.assertIn("canonical", banner)
        html = format_html_note(rec)
        self.assertIn("modules-provenance", html)
        self.assertIn(__version__, html)

    def test_non_canonical_warns(self) -> None:
        here = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "modules"
            other.mkdir()
            (other / "__init__.py").write_text("# stub\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"RAD_MODULES": str(other)}, clear=False):
                os.environ.pop("RAD_PROJECT", None)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    rec = collect(here, version=__version__)
                    self.assertFalse(rec["canonical"])
                    self.assertEqual(rec["source"], "RAD_MODULES")
                    enforce(rec, strict=False)
                self.assertTrue(any(issubclass(w.category, UserWarning) for w in caught))

    def test_strict_blocks(self) -> None:
        rec = {
            "source": "RAD_MODULES",
            "modules_dir": r"D:\tmp\other\modules",
            "code_root": r"D:\tmp\other",
            "version": __version__,
            "commit": "",
            "canonical": False,
            "stats_root_env": "",
        }
        with self.assertRaises(RuntimeError):
            enforce(rec, strict=True)



    def test_format_banner_real_newlines(self) -> None:
        """N15: banner must join with real newlines, not literal backslash-n."""
        from modules.provenance import format_banner

        rec = {
            "source": "package",
            "modules_dir": "/tmp/modules",
            "code_root": "/tmp",
            "version": "0.0.0",
            "commit": "abc",
            "canonical": True,
            "stats_root_env": "",
        }
        banner = format_banner(rec)
        self.assertIn(chr(10), banner)
        self.assertNotIn("\\n", banner)  # no literal backslash-n sequence as sole separator
        self.assertGreaterEqual(banner.count(chr(10)), 3)

    def test_emit_strict_raises_runtime_error(self) -> None:
        """N6: emit_to_stdout must raise RuntimeError under RAD_MODULES_STRICT."""
        here = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "modules"
            other.mkdir()
            (other / "__init__.py").write_text("# stub\n", encoding="utf-8")
            env = {"RAD_MODULES": str(other), "RAD_MODULES_STRICT": "1"}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("RAD_PROJECT", None)
                from modules.provenance import emit_to_stdout

                with self.assertRaises(RuntimeError):
                    emit_to_stdout(here, version=__version__)

    def test_pipeline_style_handler_propagates_runtime_error(self) -> None:
        """N6: pipeline/__init__ style handler must re-raise RuntimeError, not swallow."""

        def _handler_like_pipeline(emit_fn):
            try:
                emit_fn()
            except RuntimeError:
                raise
            except Exception as _prov_err:
                return f"skipped:{_prov_err}"
            return "ok"

        def boom_runtime():
            raise RuntimeError("strict non-canonical")

        def boom_other():
            raise ValueError("soft")

        with self.assertRaises(RuntimeError):
            _handler_like_pipeline(boom_runtime)
        self.assertTrue(str(_handler_like_pipeline(boom_other)).startswith("skipped:"))


if __name__ == "__main__":
    unittest.main()
