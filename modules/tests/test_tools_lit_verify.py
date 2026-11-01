"""t1521u P3-B: verify-refs / lit-fulltext scripts live under tools/ behind a network gate.

The gate is the reason these packs may sit in a public repo: every script that reaches
PubMed / OpenAlex / Crossref / Unpaywall / Retraction Watch must refuse to run until the
caller opts in (`--allow-network` or `SKILLS_ALLOW_NETWORK=1`). The refusals fire right
after `parse_args()`, so these tests never touch the network.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
VERIFY_REFS = TOOLS / "verify-refs"
LIT_FULLTEXT = TOOLS / "lit-fulltext"

PACK_FILES = {
    VERIFY_REFS: (
        "_common.py", "doi_to_bibtex.py", "extract_metadata.py", "format_bibtex.py",
        "retraction_scan.py", "search_google_scholar.py", "search_openalex.py",
        "search_pubmed.py", "validate_citations.py", "LICENSE_NOTE.txt",
    ),
    LIT_FULLTEXT: ("fetch.py", "LICENSE_NOTE.txt"),
}

# (relative script, argv that satisfies argparse) — the gate runs before any I/O.
NETWORK_SCRIPTS = (
    ("verify-refs/doi_to_bibtex.py", ()),
    ("verify-refs/extract_metadata.py", ()),
    ("verify-refs/retraction_scan.py", ("--input", "missing.bib")),
    ("verify-refs/search_openalex.py", ("dummy query",)),
    ("verify-refs/search_pubmed.py", ()),
    ("verify-refs/validate_citations.py", ("missing.bib",)),
    ("lit-fulltext/fetch.py", ("missing.tsv",)),
)
OFFLINE_SCRIPTS = ("verify-refs/format_bibtex.py", "verify-refs/search_google_scholar.py")
NETWORK_ENV = "SKILLS_ALLOW_NETWORK"


def _run(relative: str, argv: tuple[str, ...]) -> subprocess.CompletedProcess:
    environment = {key: value for key, value in os.environ.items() if key != NETWORK_ENV}
    return subprocess.run(
        [sys.executable, "-B", str(TOOLS / relative), *argv],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )


def _load_common():
    spec = importlib.util.spec_from_file_location("verify_refs_common_under_test", VERIFY_REFS / "_common.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackLayout(unittest.TestCase):
    def test_pack_files_present(self) -> None:
        missing = [
            str(root.name) + "/" + name
            for root, names in PACK_FILES.items()
            for name in names
            if not (root / name).is_file()
        ]
        self.assertEqual(missing, [], "missing tools files:\n" + "\n".join(missing))


class NetworkGate(unittest.TestCase):
    def test_network_scripts_refuse_by_default(self) -> None:
        for relative, argv in NETWORK_SCRIPTS:
            with self.subTest(script=relative):
                done = _run(relative, argv)
                self.assertNotEqual(done.returncode, 0, f"{relative} ran without opt-in")
                self.assertIn("network disabled", done.stderr, f"{relative}: {done.stderr[:200]}")

    def test_allow_network_flag_reaches_help(self) -> None:
        for relative, _argv in NETWORK_SCRIPTS:
            with self.subTest(script=relative):
                done = _run(relative, ("--help",))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("--allow-network", done.stdout)

    def test_offline_scripts_carry_no_gate(self) -> None:
        for relative in OFFLINE_SCRIPTS:
            with self.subTest(script=relative):
                source = (TOOLS / relative).read_text(encoding="utf-8")
                self.assertNotIn("require_network", source)

    def test_common_gate_accepts_opt_in(self) -> None:
        common = _load_common()
        previous = os.environ.pop(NETWORK_ENV, None)
        try:
            with self.assertRaises(SystemExit):
                common.require_network()
            os.environ[NETWORK_ENV] = "yes"
            self.assertIsNone(common.require_network())
        finally:
            os.environ.pop(NETWORK_ENV, None)
            if previous is not None:
                os.environ[NETWORK_ENV] = previous


if __name__ == "__main__":
    unittest.main()
