#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline tests for bin-tools/verify-bib.py.

No network is used: Crossref and OpenAlex are replaced by a fake transport
that answers with JSON shaped like the real API responses.

Run from the repository root:
    pytest acceptance_tests/test_verify_bib_offline.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import bibtexparser
import pytest

HERE = Path(__file__).resolve().parent
CANDIDATES = [HERE / "verify-bib.py", HERE.parent / "bin-tools" / "verify-bib.py"]
SCRIPT = next(p for p in CANDIDATES if p.is_file())
spec = importlib.util.spec_from_file_location("verify_bib", SCRIPT)
vb = importlib.util.module_from_spec(spec)
sys.modules["verify_bib"] = vb
spec.loader.exec_module(vb)


# =============================================================================
# Fake Crossref / OpenAlex
# =============================================================================

ABSTRACT = ("Open source software ecosystems are sustained by firms that both "
            "collaborate and compete. We study how rival firms contribute to a "
            "shared code base and how their collaboration network evolves over time.")

WORKS = {
    "10.1000/coopetition": {
        "title": "Collaboration in the open-source arena",
        "subtitle": "The WebKit case",
        "authors": [("Jose", "Teixeira"), ("Tingting", "Lin")],
        "abstract": ABSTRACT,
        "year": 2014,
    },
    "10.1000/forks": {
        "title": "Why do open source projects fork? Évidence from the commons",
        "authors": [("Gregorio", "Robles"), ("Jesús M.", "González-Barahona"),
                    ("Wil", "van der Aalst")],
        "abstract": "",
        "year": 2012,
    },
    "10.1000/other": {
        "title": "A completely unrelated paper about soil chemistry",
        "authors": [("Ann", "Soil")],
        "abstract": "",
        "year": 2001,
    },
}


def crossref_message(doi: str) -> dict:
    w = WORKS[doi]
    msg = {
        "DOI": doi.upper(),          # Crossref keeps the registered case
        "type": "journal-article",
        "title": [w["title"]],
        "author": [{"given": g, "family": f, "sequence": "additional", "affiliation": []}
                   for g, f in w["authors"]],
        "issued": {"date-parts": [[w["year"], 5]]},
    }
    if w.get("subtitle"):
        msg["subtitle"] = [w["subtitle"]]
    if w["abstract"]:
        msg["abstract"] = f"<jats:title>Abstract</jats:title><jats:p>{w['abstract']}</jats:p>"
    return msg


def openalex_work(doi: str) -> dict:
    w = WORKS[doi]
    title = w["title"] + (": " + w["subtitle"] if w.get("subtitle") else "")
    inverted: dict[str, list[int]] = {}
    for pos, word in enumerate(w["abstract"].split()):
        inverted.setdefault(word, []).append(pos)
    return {
        "id": "https://openalex.org/W" + str(abs(hash(doi)) % 10**9),
        "doi": "https://doi.org/" + doi,
        "display_name": title,
        "publication_year": w["year"],
        "type": "article",
        "authorships": [
            {"author_position": "middle",
             "author": {"id": "https://openalex.org/A1", "display_name": f"{g} {f}"},
             "raw_author_name": f"{g} {f}"}
            for g, f in w["authors"]
        ],
        "abstract_inverted_index": inverted or None,
    }


class FakeWeb:
    """Stands in for urllib: routes URLs to canned Crossref/OpenAlex answers."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.scripted: list[tuple[int, dict, bytes]] = []   # answers served first
        self.openalex_rejects_select = False
        self.openalex_denies = False

    def __call__(self, url: str, headers: dict, timeout: float):
        self.calls.append(url)
        assert "verify-bib/" in headers["User-Agent"]
        if self.scripted:
            return self.scripted.pop(0)
        parts = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qs(parts.query)
        path = urllib.parse.unquote(parts.path)

        if parts.netloc == "api.crossref.org":
            if path == "/works":
                wanted = vb.norm_text(query["query.bibliographic"][0])
                items = [crossref_message(d) for d, w in WORKS.items()
                         if vb.norm_text(w["title"]) in wanted]
                return self._json({"status": "ok", "message": {"items": items}})
            doi = path[len("/works/"):].lower()
            if doi in WORKS:
                return self._json({"status": "ok", "message": crossref_message(doi)})
            return 404, {}, b"Resource not found."

        if parts.netloc == "api.openalex.org":
            if self.openalex_denies:
                return 403, {}, b'{"error": "invalid api key"}'
            if self.openalex_rejects_select and "select" in query:
                return 403, {}, b'{"error": "Invalid query parameters error."}'
            if path == "/works":
                wanted = query["search"][0]
                results = [openalex_work(d) for d, w in WORKS.items()
                           if vb.norm_text(w["title"]) in wanted]
                return self._json({"meta": {"count": len(results)}, "results": results})
            doi = path[len("/works/doi:"):].lower()
            if doi in WORKS:
                return self._json(openalex_work(doi))
            return 404, {}, b'{"error": "Not found"}'

        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def _json(payload: dict):
        return 200, {"x-rate-limit-limit": "50", "x-rate-limit-interval": "1s"}, \
            json.dumps(payload).encode()


def make_verifier(web: FakeWeb, tmp_path: Path, **settings) -> "vb.Verifier":
    client = vb.ApiClient(mailto="me@example.org", fetch=web, sleep=lambda s: None, delay=0)
    cache = vb.Cache(tmp_path / "cache.json", ttl_days=30, refresh=False)
    registry = vb.Registry(client, cache, mailto="me@example.org", openalex_key="KEY")
    return vb.Verifier(registry, vb.Settings(**settings))


def check(bibtex: str, web: FakeWeb, tmp_path: Path, **settings) -> "vb.EntryResult":
    entry = bibtexparser.parse_string(bibtex).entries[0]
    return make_verifier(web, tmp_path, **settings).check_entry(entry, "test.bib")


GOOD = r"""
@article{TeixeiraLin2014,
  author   = {Teixeira, Jose and Lin, Tingting},
  title    = {Collaboration in the open-source arena: The {WebKit} case},
  abstract = {Open source software ecosystems are sustained by firms that both
              collaborate and compete. We study how rival firms contribute to a
              shared code base and how their collaboration network evolves over time.},
  year     = {2014},
  doi      = {10.1000/coopetition},
}
"""


# =============================================================================
# Normalisation
# =============================================================================

@pytest.mark.parametrize("raw, expected", [
    (r"Izquierdo-Cort{\'a}zar", "Izquierdo-Cortazar"),
    (r"Tun{\c{c}}", "Tunc"),
    (r"Gonz\'alez", "Gonzalez"),
    (r"M{\"u}ller", "Muller"),
    (r"S{\o}rensen", "Sorensen"),
    (r"Stra{\ss}e", "Strasse"),
    (r"Ni{\~n}o", "Nino"),
    (r"\v{S}koda", "Skoda"),
    (r"D\'{\i}az", "Diaz"),
    (r"The {WebKit} case", "The WebKit case"),
    (r"R\&D and \emph{open} source", "R&D and open source"),
    (r"Free/Libre --- Open", "Free/Libre - Open"),
])
def test_delatex(raw, expected):
    assert vb.delatex(raw) == expected


def test_norm_text_ignores_case_accents_punctuation():
    assert vb.norm_text("Évidence: from the “Commons”?") == "evidence from the commons"


def test_strip_markup():
    assert vb.strip_markup("<jats:p>A &amp; B <i>x</i></jats:p>") == "A & B x"


@pytest.mark.parametrize("name, expected", [
    ("Teixeira, Jose", "Teixeira"),
    ("José Apolinário Teixeira", "Teixeira"),
    ("Wil van der Aalst", "van der Aalst"),
    ("van der Aalst, Wil", "van der Aalst"),
    ("Martin Luther King Jr.", "King"),
    ("Linux Foundation", "Foundation"),
])
def test_surname(name, expected):
    assert vb.surname(name) == expected


@pytest.mark.parametrize("raw, expected", [
    ("10.1145/2492517.2492627", "10.1145/2492517.2492627"),
    ("https://doi.org/10.25300/MISQ/2026/18374", "10.25300/misq/2026/18374"),
    ("doi:10.1057/jit.2010.7.", "10.1057/jit.2010.7"),
    (r"10.1000/a\_b", "10.1000/a_b"),
    ("https://aisel.aisnet.org/icis2026/fow/fow/5", ""),
])
def test_clean_doi(raw, expected):
    assert vb.clean_doi(raw) == expected


def test_openalex_abstract_is_rebuilt_in_order():
    assert vb.openalex_abstract({"world": [1, 3], "hello": [0], "again": [2]}) \
        == "hello world again world"


def test_bib_authors_and_others():
    people, others = vb.parse_bib_authors(
        r"Crowston, Kevin and Kangning Wei and {Linux Foundation} and others")
    assert [p.family for p in people] == ["Crowston", "Wei", "Foundation"]
    assert others is True


# =============================================================================
# Entry verification
# =============================================================================

def test_verified_by_doi_in_both_sources(tmp_path):
    res = check(GOOD, FakeWeb(), tmp_path)
    assert res.status == vb.VERIFIED, res.problems
    for source in vb.SOURCES:
        r = res.sources[source]
        assert (r.state, r.how) == ("found", "doi")
        assert [c.status for _, c in r.checks()] == [vb.F_MATCH] * 4
    assert res.problems == []


def test_fabricated_coauthor_is_a_mismatch(tmp_path):
    bib = GOOD.replace("Lin, Tingting", "Lin, Tingting and Phantom, Fabricated")
    res = check(bib, FakeWeb(), tmp_path)
    assert res.status == vb.MISMATCH
    assert res.sources["crossref"].authors.status == vb.F_MISMATCH
    assert any("Fabricated Phantom" in p or "Phantom, Fabricated" in p for p in res.problems)


def test_missing_coauthor_is_a_mismatch(tmp_path):
    res = check(GOOD.replace("Teixeira, Jose and Lin, Tingting", "Teixeira, Jose"),
                FakeWeb(), tmp_path)
    assert res.status == vb.MISMATCH
    assert "Tingting Lin" in res.sources["openalex"].authors.detail


def test_and_others_is_accepted(tmp_path):
    res = check(GOOD.replace("Teixeira, Jose and Lin, Tingting", "Teixeira, Jose and others"),
                FakeWeb(), tmp_path)
    assert res.status == vb.VERIFIED, res.problems


def test_author_order_is_a_warning(tmp_path):
    res = check(GOOD.replace("Teixeira, Jose and Lin, Tingting", "Lin, Tingting and Teixeira, Jose"),
                FakeWeb(), tmp_path)
    assert res.status == vb.WARN
    assert res.sources["crossref"].authors.status == vb.F_NEAR


def test_accents_particles_and_double_surnames(tmp_path):
    bib = r"""
    @article{Robles_et_al2012,
      author = {Robles, Gregorio and Jes{\'u}s M. Gonzalez Barahona and Aalst, Wil},
      title  = {Why Do Open Source Projects Fork? {\'E}vidence from the Commons},
      year   = {2012},
      doi    = {https://doi.org/10.1000/FORKS},
    }"""
    res = check(bib, FakeWeb(), tmp_path)
    assert res.status == vb.VERIFIED, res.problems
    assert res.doi == "10.1000/forks"


def test_missing_subtitle_is_a_warning(tmp_path):
    res = check(GOOD.replace(": The {WebKit} case", ""), FakeWeb(), tmp_path)
    # Crossref has title and subtitle apart, so the bare title matches there;
    # OpenAlex has them joined, so it is only a near match.
    assert res.sources["crossref"].title.status == vb.F_MATCH
    assert res.sources["openalex"].title.status == vb.F_NEAR
    assert res.status == vb.WARN


def test_different_abstract_is_a_mismatch(tmp_path):
    bib = GOOD.replace(
        "Open source software ecosystems are sustained by firms that both",
        "This invented abstract talks about something else entirely, namely soil and")
    bib = bib.replace("shared code base and how their collaboration network evolves over time.",
                      "its chemistry across many decades of careful field measurement.")
    res = check(bib, FakeWeb(), tmp_path)
    assert res.status == vb.MISMATCH
    assert res.sources["crossref"].abstract.status == vb.F_MISMATCH
    assert res.sources["crossref"].title.status == vb.F_MATCH


def test_truncated_abstract_is_a_warning(tmp_path):
    bib = GOOD.replace(" We study how rival firms contribute to a\n", "\n") \
              .replace("              shared code base and how their collaboration network evolves over time.", "")
    res = check(bib, FakeWeb(), tmp_path)
    assert res.sources["crossref"].abstract.status == vb.F_NEAR
    assert res.status == vb.WARN


def test_declared_paraphrase_is_not_compared(tmp_path):
    bib = GOOD.replace("abstract = {Open source", "abstract = {[Paraphrase] The paper argues that open source")
    res = check(bib, FakeWeb(), tmp_path)
    assert res.sources["crossref"].abstract.status == vb.F_PARAPHRASE
    assert res.status == vb.VERIFIED, res.problems


def test_abstract_absent_from_bib_is_not_a_problem(tmp_path):
    lines = [l for l in GOOD.splitlines()
             if "abstract" not in l and "collaborate and compete" not in l
             and "shared code base" not in l]
    res = check("\n".join(lines), FakeWeb(), tmp_path)
    assert res.status == vb.VERIFIED, res.problems
    assert res.sources["crossref"].abstract.status == vb.F_MISSING_BIB


def test_no_doi_is_found_by_title_search(tmp_path):
    web = FakeWeb()
    res = check(GOOD.replace("  doi      = {10.1000/coopetition},\n", ""), web, tmp_path)
    assert res.status == vb.VERIFIED, res.problems
    r = res.sources["crossref"]
    assert r.how == "search"
    assert r.doi.status == vb.F_MISSING_BIB and "10.1000/coopetition" in r.doi.detail
    assert any("query.bibliographic=" in u for u in web.calls)
    assert any("search=" in u for u in web.calls)


def test_doi_of_another_work_is_caught(tmp_path):
    res = check(GOOD.replace("10.1000/coopetition", "10.1000/other"), FakeWeb(), tmp_path)
    assert res.status == vb.WARN
    r = res.sources["crossref"]
    assert r.how == "search" and r.doi.status == vb.F_NEAR
    assert any("belongs to another work" in p for p in res.problems)


def test_doi_of_another_work_without_search_is_a_mismatch(tmp_path):
    res = check(GOOD.replace("10.1000/coopetition", "10.1000/other"), FakeWeb(), tmp_path,
                search=False)
    assert res.status == vb.MISMATCH
    assert res.sources["crossref"].title.status == vb.F_MISMATCH


def test_nonexistent_reference_is_not_found(tmp_path):
    bib = r"""
    @article{Ghost2020,
      author = {Ghost, Casper},
      title  = {A paper that was never written about open source hauntings},
      year   = {2020},
      doi    = {10.9999/does.not.exist},
    }"""
    res = check(bib, FakeWeb(), tmp_path)
    assert res.status == vb.NOT_FOUND
    assert "10.9999/does.not.exist" in res.problems[0]


def test_entry_without_title_is_unchecked(tmp_path):
    web = FakeWeb()
    res = check("@misc{x, author = {A, B}, year = {2020}}", web, tmp_path)
    assert res.status == vb.UNCHECKED and web.calls == []


def test_one_source_only(tmp_path):
    web = FakeWeb()
    res = check(GOOD, web, tmp_path, sources=("crossref",))
    assert list(res.sources) == ["crossref"] and res.status == vb.VERIFIED
    assert all("crossref" in u for u in web.calls)


# =============================================================================
# HTTP behaviour
# =============================================================================

def test_api_key_and_mailto_go_where_they_should(tmp_path):
    web = FakeWeb()
    check(GOOD, web, tmp_path)
    crossref = [u for u in web.calls if "crossref" in u]
    openalex = [u for u in web.calls if "openalex" in u]
    assert all("mailto=me%40example.org" in u for u in crossref)
    assert all("api_key=KEY" in u and "mailto" not in u for u in openalex)


def test_rate_limit_is_retried(tmp_path):
    web = FakeWeb()
    web.scripted = [(429, {"Retry-After": "2"}, b""), (503, {}, b"")]
    slept: list[float] = []
    client = vb.ApiClient(fetch=web, sleep=slept.append, delay=0)
    data = client.get_json("crossref", vb.CROSSREF_API + "/works/10.1000/forks", {}, kind="one")
    assert data["message"]["DOI"] == "10.1000/FORKS"
    assert len(web.calls) == 3 and slept[0] == 2.0


def test_persistent_rate_limit_switches_the_source_off(tmp_path):
    web = FakeWeb()
    web.scripted = [(429, {}, b"")] * 4          # first Crossref call + 3 retries
    verifier = make_verifier(web, tmp_path)
    entry = bibtexparser.parse_string(GOOD).entries[0]
    res = verifier.check_entry(entry, "test.bib")
    assert res.sources["crossref"].state == "error"
    assert "crossref" in verifier.disabled
    assert res.sources["openalex"].state == "found"
    assert res.status == vb.VERIFIED             # still verified by OpenAlex
    again = verifier.check_entry(entry, "test.bib")
    assert again.sources["crossref"].state == "skipped"


def test_openalex_refusal_switches_it_off_but_crossref_goes_on(tmp_path):
    web = FakeWeb()
    web.openalex_denies = True
    verifier = make_verifier(web, tmp_path)
    res = verifier.check_entry(bibtexparser.parse_string(GOOD).entries[0], "test.bib")
    assert "openalex" in verifier.disabled
    assert res.sources["crossref"].state == "found" and res.status == vb.VERIFIED


def test_rejected_select_is_dropped_once(tmp_path):
    web = FakeWeb()
    web.openalex_rejects_select = True
    verifier = make_verifier(web, tmp_path)
    res = verifier.check_entry(bibtexparser.parse_string(GOOD).entries[0], "test.bib")
    assert res.sources["openalex"].state == "found"
    assert "openalex" not in verifier.disabled
    before = len(web.calls)
    other = GOOD.replace("10.1000/coopetition", "10.1000/forks")
    verifier.check_entry(bibtexparser.parse_string(other).entries[0], "test.bib")
    later = [u for u in web.calls[before:] if "openalex" in u]
    assert later and all("select=" not in u for u in later)


def test_network_failure_leaves_entry_unchecked(tmp_path):
    def down(url, headers, timeout):
        raise OSError("connection refused")
    client = vb.ApiClient(fetch=down, sleep=lambda s: None, delay=0, retries=1)
    registry = vb.Registry(client, vb.Cache(None, 30, False))
    verifier = vb.Verifier(registry, vb.Settings())
    res = verifier.check_entry(bibtexparser.parse_string(GOOD).entries[0], "test.bib")
    assert res.status == vb.UNCHECKED
    assert all(r.state == "error" for r in res.sources.values())


def test_cache_prevents_repeat_requests(tmp_path):
    web = FakeWeb()
    entry = bibtexparser.parse_string(GOOD).entries[0]

    first = make_verifier(web, tmp_path)
    first.check_entry(entry, "test.bib")
    first.registry.cache.save()
    assert len(web.calls) == 2                    # one DOI lookup per source

    second = make_verifier(web, tmp_path)         # fresh run, same cache file
    res = second.check_entry(entry, "test.bib")
    assert len(web.calls) == 2 and res.status == vb.VERIFIED
    assert "KEY" not in (tmp_path / "cache.json").read_text()


# =============================================================================
# Command line
# =============================================================================

BAD = GOOD.replace("TeixeiraLin2014", "Wrong2014").replace("Lin, Tingting", "Nobody, Nick")


def run_cli(tmp_path, capsys, bibtex, *extra):
    bib = tmp_path / "sample.bib"
    bib.write_text(bibtex, encoding="utf-8")
    code = vb.main([str(bib), "--cache-file", str(tmp_path / "c.json"), "--delay", "0",
                    "--openalex-key", "KEY", *extra], fetch=FakeWeb())
    return code, capsys.readouterr()


def test_cli_json_and_check_exit_code(tmp_path, capsys):
    code, out = run_cli(tmp_path, capsys, GOOD + BAD, "--json", "--check")
    assert code == 2
    payload = json.loads(out.out)
    assert payload["counts"][vb.VERIFIED] == 1 and payload["counts"][vb.MISMATCH] == 1
    assert payload["field_mismatches"] == {"title": 0, "authors": 1, "abstract": 0}
    entries = {e["key"]: e for e in payload["files"][0]["entries"]}
    assert entries["Wrong2014"]["sources"]["crossref"]["authors"]["status"] == "mismatch"
    assert entries["Wrong2014"]["line"] > entries["TeixeiraLin2014"]["line"]
    assert "KEY" not in out.out + out.err


def test_cli_clean_file_exits_zero(tmp_path, capsys):
    code, _ = run_cli(tmp_path, capsys, GOOD, "--check", "--strict")
    assert code == 0


def test_cli_without_check_never_fails_on_mismatch(tmp_path, capsys):
    code, _ = run_cli(tmp_path, capsys, BAD)
    assert code == 0


def test_cli_filters_and_report(tmp_path, capsys):
    tagged = BAD.replace("year     = {2014},", "year = {2014},\n  comment = {TOP_IS_C, generated_by_x},")
    report = tmp_path / "report.md"
    code, out = run_cli(tmp_path, capsys, GOOD + tagged, "--json", "--tag", "generated_by_x",
                        "--report", str(report))
    payload = json.loads(out.out)
    assert [e["key"] for e in payload["files"][0]["entries"]] == ["Wrong2014"]
    text = report.read_text()
    assert "| MISMATCH | 1 |" in text and "`Wrong2014`" in text

    code, out = run_cli(tmp_path, capsys, GOOD + tagged, "--json", "--limit", "1")
    assert [e["key"] for e in json.loads(out.out)["files"][0]["entries"]] == ["TeixeiraLin2014"]

    code, out = run_cli(tmp_path, capsys, GOOD + tagged, "--json", "--only", "Wrong2014")
    assert [e["key"] for e in json.loads(out.out)["files"][0]["entries"]] == ["Wrong2014"]


def test_cli_gha_annotations(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    code, out = run_cli(tmp_path, capsys, GOOD + BAD, "--gha")
    assert "::error file=" in out.out and "Wrong2014: MISMATCH" in out.out
    assert "## verify-bib" in summary.read_text()


def test_cli_rejects_non_bib(tmp_path, capsys):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    assert vb.main([str(other)], fetch=FakeWeb()) == 1
