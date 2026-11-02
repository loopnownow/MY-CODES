"""Frozen output shapes for the `tools/verify-refs` + `tools/lit-fulltext` packs.

Step 4 of the library swap replaces the hand-written HTTP clients in these
scripts with `pyalex` / `habanero` / `arxiv` / `bibtexparser`. `biopython`
(Bio.Entrez) and `unpywall` were assessed and left out: both demand an email
identity the current CLIs do not, so the hand-written `requests`/`urllib` paths
stay for PubMed and Unpaywall. Nothing may change on the outside: CLI arguments,
exit codes, output file names, column names, `manual_needed.txt` behaviour and
the network gate are a lab contract. This module freezes that outside offline,
in two layers, so each swap commit can be judged without a socket:

  * L1 normalisation - canned API payloads (or a library-shaped record) fed
    straight into the script's mapping functions. Survives a client swap
    because no HTTP is involved.
  * L2 request layer - `requests.Session.request` and
    `urllib.request.urlopen` are intercepted, so a fixture body is served
    instead of a real response and an unrouted URL fails the test.

Every payload under `fixtures/refs/` is synthetic: no patient data, no copied
publisher record, no real request captured.

Snapshots live in `fixtures/refs/expected_snapshots.json`. Regenerate and
review the diff before committing:

    UPDATE_SNAPSHOTS=1 python -B -m pytest modules/tests/test_tools_refs_snapshots.py -q

Run: python -B -m pytest modules/tests/test_tools_refs_snapshots.py -q
"""
from __future__ import annotations

import contextlib
import difflib
import builtins
import importlib.util
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
VERIFY_REFS = TOOLS / "verify-refs"
LIT_FULLTEXT = TOOLS / "lit-fulltext"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "refs"
SNAPSHOTS = FIXTURES / "expected_snapshots.json"
UPDATE = os.environ.get("UPDATE_SNAPSHOTS", "").strip() == "1"
EMAIL = "user@example.org"
# A minimal but genuinely valid PDF header; the pack rejects anything under 10 KiB.
PDF_BYTES = b"%PDF-1.4\n" + b"0" * (12 * 1024)

_LOADED: dict[str, object] = {}


def load(pack: Path, filename: str):
    """Import a tools script by path; `tools/` directories are not packages."""
    name = "refs_snapshot_" + filename.removesuffix(".py").replace("-", "_")
    if name not in _LOADED:
        sys.path.insert(0, str(pack))
        spec = importlib.util.spec_from_file_location(name, pack / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LOADED[name] = module
    return _LOADED[name]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _render(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _store() -> dict:
    if not SNAPSHOTS.is_file():
        return {}
    return json.loads(SNAPSHOTS.read_text(encoding="utf-8"))


def check_snapshot(name: str, value) -> None:
    """Compare one rendered output against its frozen snapshot."""
    text = _render(value)
    if UPDATE:
        store = _store()
        store[name] = text
        SNAPSHOTS.write_text(
            json.dumps(dict(sorted(store.items())), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return
    store = _store()
    assert name in store, (
        f"no snapshot for {name!r}; run with UPDATE_SNAPSHOTS=1 and review the diff")
    expected = store[name]
    if text == expected:
        return
    diff = "\n".join(difflib.unified_diff(
        expected.splitlines(), text.splitlines(),
        fromfile=f"snapshot:{name}", tofile=f"actual:{name}", lineterm=""))
    raise AssertionError(f"output shape changed for {name!r}\n{diff}")


class _FakeResponse:
    """The slice of `requests.Response` / `urlopen` result the scripts touch."""

    def __init__(self, status: int, body: bytes, url: str = ""):
        self.status_code = status
        self.status = status
        self.reason = "OK"
        self.headers: dict[str, str] = {"Content-Type": "application/json"}
        self.url = url
        self.encoding = "utf-8"
        self._body = body

    @property
    def content(self) -> bytes:
        return self._body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._body.decode("utf-8"))

    @property
    def reason_phrase(self) -> str:
        return "OK" if self.status_code < 400 else "Error"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error for {self.url}")

    def read(self) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.status_code

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class Router:
    """Serve fixture bodies by URL substring; an unrouted URL fails the test."""

    def __init__(self, routes: dict[str, tuple]):
        self._routes = []
        for needle, (payload, status) in routes.items():
            if isinstance(payload, Path):
                body = payload.read_bytes()
            elif isinstance(payload, bytes):
                body = payload
            else:
                body = str(payload).encode("utf-8")
            self._routes.append((needle, status, body))
        self.calls: list[str] = []

    def respond(self, url: str) -> _FakeResponse:
        for needle, status, body in self._routes:
            if needle in url:
                self.calls.append(url)
                return _FakeResponse(status, body, url=url)
        raise AssertionError(f"unrouted request in an offline test: {url}")


@contextlib.contextmanager
def offline(routes: dict[str, tuple]):
    """Run against fixture bodies only: no socket, no sleep."""
    router = Router(routes)

    def fake_request(_session, _method, url, params=None, **_kwargs):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        return router.respond(url)

    def fake_urlopen(request, data=None, *_args, **_kwargs):
        url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
        return router.respond(url)

    def fake_httpx_get(url, params=None, **_kwargs):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
        return router.respond(url)

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(requests.Session, "request", fake_request))
        stack.enter_context(mock.patch.object(urllib.request, "urlopen", fake_urlopen))
        stack.enter_context(mock.patch("time.sleep", lambda *_a, **_k: None))
        # habanero talks httpx, not requests: intercept it too, so a client swap
        # can never turn an offline shape test into a real request.
        try:
            import httpx
        except ImportError:
            pass
        else:
            stack.enter_context(mock.patch.object(httpx, "get", fake_httpx_get))
        yield router


# ============================================================
# _common - the BibTeX layer (bibtexparser replaces parse_bibtex only)
# ============================================================

def _common():
    return load(VERIFY_REFS, "_common.py")


def test_common_parse_bibtex_skips_non_entries_and_keeps_inner_braces() -> None:
    module = _common()
    entries = module.parse_bibtex(fixture("refs_sample.bib"))
    frozen = [{"type": e["type"], "key": e["key"], "fields": dict(e["fields"])} for e in entries]
    check_snapshot("common.parse_bibtex_entries", frozen)
    # @string and @comment are not bibliography entries.
    assert [e["key"] for e in entries] == [
        "Jumper2021alphafold", "jumper_dup", "Muller2024radiomics", "NoYear"]
    # Capitalisation protection survives the round trip.
    assert "{AlphaFold}" in entries[0]["fields"]["title"]


def test_common_render_entry_round_trips() -> None:
    module = _common()
    entry = module.parse_bibtex(fixture("refs_sample.bib"))[0]
    rendered = module.render_entry(entry["type"], entry["key"], entry["fields"])
    check_snapshot("common.render_entry", rendered)
    reparsed = module.parse_bibtex(rendered)
    assert len(reparsed) == 1
    assert dict(reparsed[0]["fields"]) == dict(entry["fields"])


def test_common_parse_bibtex_stops_on_an_unterminated_entry() -> None:
    module = _common()
    entries = module.parse_bibtex("@article{good, title = {A}} @article{broken, title = {B}")
    assert [e["key"] for e in entries] == ["good"]


def test_common_citation_key_scheme() -> None:
    module = _common()
    cases = [
        ("Smith JA", "2020", "Deep learning in radiology"),
        ("John Smith", "2020", "Deep learning in radiology"),
        ("Müller, Anna and Chen, Bo", "2024", "{Deep} radiomics"),
        ("Jumper, John", "2021", "Highly accurate protein structure prediction with AlphaFold"),
        ("", "", ""),
    ]
    check_snapshot("common.citation_key", [module.citation_key(*case) for case in cases])


def test_common_format_pages_and_protect_title() -> None:
    module = _common()
    pages = ["1123-30", "583--589", "e0123456", "pp. 12-19", "100\u2013110", "12, 15-17"]
    check_snapshot("common.format_pages", {value: module.format_pages(value) for value in pages})

    titles = [
        "Highly accurate prediction with AlphaFold",
        "{AlphaFold} already braced",
        "MRI and mRNA in COVID",
    ]
    protected = {value: module.protect_title(value) for value in titles}
    check_snapshot("common.protect_title", protected)
    for value in titles:
        assert module.protect_title(module.protect_title(value)) == protected[value]


# ============================================================
# search_openalex.py - pyalex replaces the hand-written REST client
# ============================================================

def test_openalex_search_normalises_records_and_bibtex() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    with offline({"api.openalex.org/works": (FIXTURES / "openalex_works.json", 200)}) as router:
        searcher = module.OpenAlexSearcher()
        records = searcher.search("radiomics", max_results=10)
        bibtex = [searcher.metadata_to_bibtex(record) for record in records]
    check_snapshot("openalex.records", records)
    check_snapshot("openalex.bibtex", bibtex)
    # Paging contract: the per-page size is capped at 100 and iteration starts at "*".
    assert len(router.calls) == 1
    assert "per-page=10" in router.calls[0]
    assert "cursor=%2A" in router.calls[0]


def test_openalex_request_query_shape_is_preserved() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    with offline({"api.openalex.org/works": (FIXTURES / "openalex_works.json", 200)}) as router:
        module.OpenAlexSearcher(email=EMAIL).search(
            "radiomics", max_results=7, year_start=2019, year_end=2024,
            work_type="journal-article", sort_by="citations")
    parsed = urllib.parse.urlparse(router.calls[0])
    # The query is the part a client swap can silently change: filter separators,
    # sort direction, the paging size, the cursor start and the mailto identity.
    check_snapshot("openalex.request_query",
                   urllib.parse.parse_qs(parsed.query, keep_blank_values=True))
    assert parsed.netloc == "api.openalex.org"
    assert parsed.path == "/works"


def test_openalex_fails_loudly_without_pyalex() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "pyalex" or name.startswith("pyalex."):
            raise ImportError("No module named 'pyalex'")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", missing):
        with pytest.raises(ImportError, match="swap-refs"):
            module.OpenAlexSearcher()


def _openalex_payload(**overrides) -> bytes:
    payload = json.loads(fixture("openalex_works.json"))
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_openalex_rejects_a_repeated_cursor() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    payload = _openalex_payload(meta={"count": 1, "next_cursor": "*"})
    with offline({"api.openalex.org/works": (payload, 200)}):
        with pytest.raises(RuntimeError, match="repeated cursor"):
            module.OpenAlexSearcher().search("radiomics")


def test_openalex_rejects_a_duplicate_work_id() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    works = json.loads(fixture("openalex_works.json"))["results"]
    payload = _openalex_payload(meta={"count": 2, "next_cursor": None}, results=[works[0], works[0]])
    with offline({"api.openalex.org/works": (payload, 200)}):
        with pytest.raises(RuntimeError, match="duplicate work ID"):
            module.OpenAlexSearcher().search("radiomics")


def test_openalex_rejects_an_empty_nonterminal_page() -> None:
    module = load(VERIFY_REFS, "search_openalex.py")
    payload = _openalex_payload(meta={"count": 5, "next_cursor": "next"}, results=[])
    with offline({"api.openalex.org/works": (payload, 200)}):
        with pytest.raises(RuntimeError, match="empty nonterminal page"):
            module.OpenAlexSearcher().search("radiomics")


# ============================================================
# search_pubmed.py - biopython (Bio.Entrez) replaces ESearch/EFetch
# ============================================================

def test_pubmed_metadata_and_bibtex() -> None:
    module = load(VERIFY_REFS, "search_pubmed.py")
    with offline({"eutils.ncbi.nlm.nih.gov": (FIXTURES / "pubmed_efetch.xml", 200)}):
        searcher = module.PubMedSearcher()
        metadata = searcher.fetch_metadata(["11111111", "22222222"])
        bibtex = [searcher.metadata_to_bibtex(record) for record in metadata]
    assert [record["pmid"] for record in metadata] == ["11111111", "22222222"]
    check_snapshot("pubmed.metadata", metadata)
    check_snapshot("pubmed.bibtex", bibtex)


def test_pubmed_never_exports_a_partial_batch() -> None:
    module = load(VERIFY_REFS, "search_pubmed.py")
    with offline({"eutils.ncbi.nlm.nih.gov": (FIXTURES / "pubmed_efetch.xml", 200)}):
        assert module.PubMedSearcher().fetch_metadata(["11111111", "99999999"]) == []


# ============================================================
# extract_metadata.py - habanero / arxiv / Bio.Entrez replace the clients
# ============================================================

def _extractor():
    module = load(VERIFY_REFS, "extract_metadata.py")
    return module, module.MetadataExtractor()


def test_extract_crossref_metadata_and_bibtex() -> None:
    module, extractor = _extractor()
    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_work.json", 200)}):
        metadata = extractor.extract_from_doi("10.1038/s41586-021-03819-2")
    check_snapshot("extract.crossref_metadata", metadata)
    check_snapshot("extract.crossref_bibtex", extractor.metadata_to_bibtex(metadata))


def test_extract_pubmed_metadata() -> None:
    module, extractor = _extractor()
    with offline({"eutils.ncbi.nlm.nih.gov": (FIXTURES / "pubmed_efetch.xml", 200)}):
        metadata = extractor.extract_from_pmid("11111111")
    check_snapshot("extract.pmid_metadata", metadata)


def test_extract_arxiv_article_and_preprint_bibtex() -> None:
    module, extractor = _extractor()
    with offline({"export.arxiv.org/api/query": (FIXTURES / "arxiv_entry.xml", 200)}):
        published = extractor.extract_from_arxiv("2401.01234v1")
    check_snapshot("extract.arxiv_metadata", published)
    check_snapshot("extract.arxiv_bibtex", extractor.metadata_to_bibtex(published))
    # A journal_ref makes it an @article; without one it stays a @misc preprint.
    assert published["entry_type"] == "article"

    with offline({"export.arxiv.org/api/query": (FIXTURES / "arxiv_preprint.xml", 200)}):
        preprint = extractor.extract_from_arxiv("2402.05678v3")
    assert preprint["entry_type"] == "misc"
    check_snapshot("extract.arxiv_preprint_bibtex", extractor.metadata_to_bibtex(preprint))


def test_extract_returns_none_on_an_http_error() -> None:
    module, extractor = _extractor()
    with offline({"api.crossref.org/works/": (b"{}", 404)}):
        assert extractor.extract_from_doi("10.1000/missing.1") is None


# ============================================================
# doi_to_bibtex.py - DOI content negotiation, key regenerated in-house
# ============================================================

def test_doi_to_bibtex_renormalises_the_agency_entry() -> None:
    module = load(VERIFY_REFS, "doi_to_bibtex.py")
    with offline({"doi.org/": (FIXTURES / "content_negotiation.bib", 200)}):
        entry = module.DOIConverter().doi_to_bibtex("https://doi.org/10.1000/xyz.2024.1")
    check_snapshot("doi_to_bibtex.entry", entry)
    # @data is not a BibTeX type; the pack emits @misc instead.
    assert entry.startswith("@misc{Muller2024deep,")


# ============================================================
# retraction_scan.py - Crossref / PubMed verdict mapping
# ============================================================

def test_retraction_parse_citation() -> None:
    module = load(VERIFY_REFS, "retraction_scan.py")
    citation = module.CitationParser.parse_citation(
        "Jumper J, Evans R. Highly accurate protein structure prediction with AlphaFold. "
        "Nature. 2021;596:583-589. doi:10.1038/s41586-021-03819-2", index=3)
    frozen = {field: getattr(citation, field) for field in
              ("index", "doi", "pmid", "title", "authors", "journal", "year")}
    check_snapshot("retraction.parse_citation", frozen)


def test_retraction_crossref_verdicts() -> None:
    module = load(VERIFY_REFS, "retraction_scan.py")

    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_retraction.json", 200)}):
        by_update_type = module.RetractionChecker().check_crossref("10.1038/s41586-021-03819-2")
    assert by_update_type.status == "retracted"
    check_snapshot("retraction.crossref_update_type", asdict(by_update_type))

    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_update_to.json", 200)}):
        by_update_to = module.RetractionChecker().check_crossref("10.1000/xyz.2022.7")
    assert by_update_to.status == "retracted"
    check_snapshot("retraction.crossref_update_to", asdict(by_update_to))

    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_work.json", 200)}):
        assert module.RetractionChecker().check_crossref("10.1038/s41586-021-03819-2") is None


def test_retraction_json_report_shape() -> None:
    module = load(VERIFY_REFS, "retraction_scan.py")
    citations = [
        module.Citation(index=0, raw_text="Ref one.", doi="10.1038/s41586-021-03819-2"),
        module.Citation(index=1, raw_text="Ref two.", pmid="22222222"),
    ]
    record = module.RetractionRecord(
        identifier="10.1038/s41586-021-03819-2",
        identifier_type="doi",
        status="retracted",
        title="Highly accurate protein structure prediction with AlphaFold",
        retraction_date="2024-03-05",
    )
    generator = module.ReportGenerator(citations, {0: record, 1: None})
    with mock.patch("time.strftime", return_value="2026-11-02 00:00"):
        payload = generator.generate_json()
    check_snapshot("retraction.json_report", payload)
    assert json.loads(payload)["metadata"]["stats"]["retracted"] == 1


# ============================================================
# validate_citations.py - entry checks and the three-state DOI verdict
# ============================================================

def _validator():
    module = load(VERIFY_REFS, "validate_citations.py")
    return module, module.CitationValidator()


def test_validate_entry_issues_and_duplicates() -> None:
    module, validator = _validator()
    entries = validator.parse_bibtex_file(str(FIXTURES / "refs_sample.bib"))
    issues = []
    for entry in entries:
        errors, warnings = validator.validate_entry(entry)
        issues.append({"key": entry["key"], "errors": errors, "warnings": warnings})
    check_snapshot("validate.entry_issues", issues)
    check_snapshot("validate.duplicates", validator.detect_duplicates(entries))


def test_validate_doi_verdict_is_three_state() -> None:
    module, validator = _validator()
    doi = "10.1038/s41586-021-03819-2"

    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_work.json", 200)}):
        verdict, metadata = validator.verify_doi(doi)
    assert verdict is True
    check_snapshot("validate.verify_doi_registered", metadata)

    # Crossref 404 but DataCite knows the DOI: registered, no Crossref metadata.
    with offline({"api.crossref.org/works/": (b"{}", 404),
                  "api.datacite.org/dois/": (FIXTURES / "datacite.json", 200)}):
        assert validator.verify_doi("10.1000/xyz.2024.1") == (True, None)

    # Neither agency knows it and the resolver 404s: a bad DOI, not a transport error.
    with offline({"api.crossref.org/works/": (b"{}", 404),
                  "api.datacite.org/dois/": (b"{}", 404),
                  "doi.org/": (b"{}", 404)}):
        assert validator.verify_doi("10.1000/missing.1") == (False, None)

    # A server error is our problem, not evidence the DOI is bad.
    with offline({"api.crossref.org/works/": (b"{}", 500)}):
        assert validator.verify_doi(doi) == (None, None)


# ============================================================
# format_bibtex.py - pure formatting over the shared parser
# ============================================================

def test_format_fix_dedupe_rekey_sort() -> None:
    module = load(VERIFY_REFS, "format_bibtex.py")
    formatter = module.BibTeXFormatter()
    entries = formatter.parse_bibtex_file(str(FIXTURES / "refs_sample.bib"))

    fixed = [dict(formatter.fix_common_issues(entry)["fields"]) for entry in entries]
    check_snapshot("format.fix_common_issues", fixed)

    unique = formatter.deduplicate_entries(entries)
    rekeyed = formatter.rekey_entries(unique)
    assert len(unique) == 3, "the duplicate DOI must be dropped"
    check_snapshot("format.dedupe_rekey_keys", [entry["key"] for entry in rekeyed])
    check_snapshot("format.sort_by_year", [
        entry["key"] for entry in formatter.sort_entries(rekeyed, sort_by="year", descending=True)])


# ============================================================
# lit-fulltext/fetch.py - the arxiv helpers (Unpaywall and PubMed keep urllib)
# ============================================================

def _fetch():
    return load(LIT_FULLTEXT, "fetch.py")


def test_fetch_unpaywall_priority_order() -> None:
    module = _fetch()
    cases = {
        "best_url_for_pdf": ("10.1038/s41586-021-03819-2", "unpaywall_best_pdf.json"),
        "oa_location_url_for_pdf": ("10.1000/xyz.2024.1", "unpaywall_location_pdf.json"),
        "best_url": ("10.1000/xyz.2019.3", "unpaywall_best_url.json"),
        "closed": ("10.1000/xyz.2018.9", "unpaywall_closed.json"),
    }
    found = {}
    for name, (doi, payload) in cases.items():
        with offline({"api.unpaywall.org/v2/": (FIXTURES / payload, 200)}):
            found[name] = module.unpaywall_lookup(doi, EMAIL)
    check_snapshot("fetch.unpaywall_priority", found)
    assert found["best_url_for_pdf"].endswith("/pdf/1.pdf")
    assert found["closed"] is None


def test_fetch_arxiv_identifier_helpers() -> None:
    module = _fetch()
    identifiers = {
        "new_versioned": "10.48550/arXiv.2401.01234v2",
        "old_style": "arXiv:hep-th/9901001",
        "bare_id": "2401.01234",
        "not_arxiv": "10.1038/s41586-021-03819-2",
    }
    check_snapshot("fetch.arxiv_ids", {
        key: [module.arxiv_id_from_doi(value), module.arxiv_pdf_url(value)]
        for key, value in identifiers.items()})


def test_fetch_pmcid_and_oa_candidate_order() -> None:
    module = _fetch()
    with offline({"pmc.ncbi.nlm.nih.gov/tools/idconv": (FIXTURES / "idconv.json", 200)}):
        assert module.id_to_pmcid("11111111", EMAIL) == "PMC7654321"

    with offline({"api.openalex.org/works/": (FIXTURES / "openalex_work.json", 200)}):
        check_snapshot("fetch.openalex_candidates",
                       module.openalex_lookup("10.1038/s41586-021-03819-2", EMAIL))

    with offline({"api.crossref.org/works/": (FIXTURES / "crossref_work.json", 200)}) as router:
        candidates = module.crossref_lookup("10.1038/s41586-021-03819-2", EMAIL)
    check_snapshot("fetch.crossref_candidates", candidates)
    # habanero's works(ids=...) endpoint must stay a single-record fetch.
    assert [call for call in router.calls if "api.crossref.org" in call] == \
        ["https://api.crossref.org/works/10.1038/s41586-021-03819-2"]


def test_fetch_crossref_fails_loudly_without_habanero() -> None:
    module = _fetch()
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "habanero" or name.startswith("habanero."):
            raise ImportError("No module named 'habanero'")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", missing):
        with pytest.raises(ImportError, match="swap-refs"):
            module.crossref_lookup("10.1038/s41586-021-03819-2", EMAIL)


def test_fetch_safe_doi_name_is_stable() -> None:
    module = _fetch()
    examples = ["10.1038/s41586-021-03819-2", "10.1000/xyz.2024.1", "10.5555/a b:c"]
    check_snapshot("fetch.safe_doi_name", {value: module.safe_doi_name(value) for value in examples})


def test_fetch_report_shape(tmp_path: Path) -> None:
    module = _fetch()
    doi = "10.1038/s41586-021-03819-2"
    outdir = tmp_path / "pdfs"
    outdir.mkdir()
    (outdir / f"{module.safe_doi_name(doi)}.pdf").write_bytes(PDF_BYTES)
    records = [
        {"doi": doi, "pmid": "11111111", "first_author": "Jumper",
         "title": "Highly accurate protein structure prediction with AlphaFold"},
        {"doi": "10.1000/xyz.2024.1", "pmid": "", "first_author": "",
         "title": "Deep radiomics"},
    ]
    report = module.build_report(records, {doi: ("oa", "unpaywall")}, outdir)
    check_snapshot("fetch.report", report)
    assert report["schema_version"] == 2
    assert report["counts"]["retrieved"] == 1
    assert report["items"][1]["source_identity"]["reason"] == "pdf_not_available"


def test_fetch_skips_an_existing_pdf_without_touching_the_network(tmp_path: Path) -> None:
    module = _fetch()
    doi = "10.1038/s41586-021-03819-2"
    (tmp_path / f"{module.safe_doi_name(doi)}.pdf").write_bytes(PDF_BYTES)
    # No routes registered: any request attempt fails the test loudly.
    with offline({}):
        assert module.process_doi(doi, tmp_path, EMAIL) == ("skip", "existing")


def test_fetch_replaces_a_stale_stub_before_downloading(tmp_path: Path) -> None:
    module = _fetch()
    doi = "10.1000/xyz.2018.9"
    stub = tmp_path / f"{module.safe_doi_name(doi)}.pdf"
    stub.write_bytes(b"%PDF-1.4\nstub, far below the minimum")
    assert module.existing_pdf_ok(stub) is False
    # Every resolver is empty and the landing page has no PDF link, so the
    # outcome is a hard failure -- and the stale stub is gone.
    with offline({"api.unpaywall.org/v2/": (FIXTURES / "unpaywall_closed.json", 200),
                  "pmc.ncbi.nlm.nih.gov/tools/idconv": (b"{}", 200),
                  "api.openalex.org/works/": (b'{"open_access": {}, "primary_location": {}}', 200),
                  "api.crossref.org/works/": (b'{"message": {}}', 200),
                  "doi.org/": (b"<html><head></head>no pdf here</html>", 200)}) as router:
        assert module.process_doi(doi, tmp_path, EMAIL) == ("fail", "")
    assert stub.exists() is False
    assert [call for call in router.calls if call.startswith("https://doi.org/")] == \
        [f"https://doi.org/{doi}"]


