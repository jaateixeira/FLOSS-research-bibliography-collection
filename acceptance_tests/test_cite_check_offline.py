#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline tests for bin-tools/cite-check.py.

No network and no real model is used: Crossref, OpenAlex, Unpaywall and the
language-model endpoints are replaced by fake transports.

Run from the repository root:
    pytest acceptance_tests/test_cite_check_offline.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CANDIDATES = [HERE / "cite-check.py", HERE.parent / "bin-tools" / "cite-check.py"]
SCRIPT = next(p for p in CANDIDATES if p.is_file())
spec = importlib.util.spec_from_file_location("cite_check", SCRIPT)
cc = importlib.util.module_from_spec(spec)
sys.modules["cite_check"] = cc
spec.loader.exec_module(cc)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never touch the real ~/.cache; never read the real API keys."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENALEX_API_KEY", "CROSSREF_MAILTO",
                "CONSENSUS_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"):
        monkeypatch.delenv(var, raising=False)


# =============================================================================
# Fakes
# =============================================================================

COOP_ABSTRACT = ("Open source software ecosystems are sustained by firms that both "
                 "collaborate and compete. We study how rival firms contribute to a "
                 "shared code base and how their collaboration network evolves over time.")
FORK_ABSTRACT = ("Forking is common in free software. We analyse 220 projects and find that "
                 "most forks are abandoned within a year, while a minority develops "
                 "into independent communities.")

WORKS = {
    "10.1000/coop": ("Collaboration in the open source arena", COOP_ABSTRACT),
    "10.1000/forks": ("Why do open source projects fork", FORK_ABSTRACT),
    "10.1000/noabs": ("A paper without any abstract", ""),
    "10.1000/other": ("Soil chemistry of alpine meadows", "Soil is made of minerals and water."),
}


class FakeWeb:
    """Routes the URLs verify-bib and cite-check use to canned answers."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pdfs: dict[str, bytes] = {}
        self.unpaywall: dict[str, str] = {}
        self.down: set[str] = set()
        self.headers: list[tuple[str, dict]] = []
        self.consensus: dict[str, object] = {}          # doi -> JSON payload or HTTP status
        self.s2_papers: dict[str, dict] = {}            # doi -> paper record
        self.s2_snippets: list[str] = []                # texts returned for any snippet search

    def __call__(self, url: str, headers: dict, timeout: float):
        self.calls.append(url)
        self.headers.append((url, dict(headers)))
        for host in self.down:
            if host in url:
                return 500, {}, b"{}"
        if url in self.pdfs:
            return 200, {}, self.pdfs[url]
        if "api.consensus.app/v1/papers/full_text" in url:
            import urllib.parse as up
            doi = up.unquote(url.split("dois=")[1].split("&")[0])
            answer = self.consensus.get(doi)
            if isinstance(answer, int):
                return answer, {}, b"{}"
            if answer is None:
                return 404, {}, b"{}"
            return 200, {}, json.dumps(answer).encode()
        if "api.semanticscholar.org/graph/v1/snippet/search" in url:
            data = [{"snippet": {"text": t, "snippetKind": "body"}, "paper": {"corpusId": 1}}
                    for t in self.s2_snippets]
            return 200, {}, json.dumps({"data": data}).encode()
        if "api.semanticscholar.org/graph/v1/paper/DOI:" in url:
            import urllib.parse as up
            doi = up.unquote(url.split("DOI:")[1].split("?")[0])
            if doi in self.s2_papers:
                return 200, {}, json.dumps(self.s2_papers[doi]).encode()
            return 404, {}, b"{}"
        if "api.unpaywall.org" in url:
            doi = url.split("/v2/")[1].split("?")[0]
            if doi in self.unpaywall:
                loc = {"url_for_pdf": self.unpaywall[doi]}
                return 200, {}, json.dumps({"best_oa_location": loc, "oa_locations": [loc]}).encode()
            return 404, {}, b"{}"
        path = url.split("?")[0]
        if "api.crossref.org/works/" in path:
            doi = path.split("/works/", 1)[1]
            if doi in WORKS:
                title, abstract = WORKS[doi]
                msg = {"DOI": doi, "title": [title], "author": [{"given": "A", "family": "Author"}],
                       "issued": {"date-parts": [[2014]]}}
                if abstract:
                    msg["abstract"] = f"<jats:p>{abstract}</jats:p>"
                return 200, {}, json.dumps({"message": msg}).encode()
            return 404, {}, b"{}"
        if "api.openalex.org/works/doi:" in path:
            doi = path.split("doi:", 1)[1]
            if doi in WORKS:
                title, abstract = WORKS[doi]
                inv: dict[str, list[int]] = {}
                for i, w in enumerate(abstract.split()):
                    inv.setdefault(w, []).append(i)
                return 200, {}, json.dumps({
                    "id": "https://openalex.org/W1", "doi": "https://doi.org/" + doi,
                    "display_name": title, "publication_year": 2014,
                    "authorships": [], "abstract_inverted_index": inv or None}).encode()
            return 404, {}, b"{}"
        return 404, {}, b"{}"


class FakeLLM:
    """A model endpoint. `rules` maps a phrase of the prompt to a JSON answer."""

    def __init__(self) -> None:
        self.rules: list[tuple[str, object]] = []
        self.prompts: list[str] = []
        self.requests: list[tuple[str, dict, dict]] = []
        self.scripted: list[tuple[int, bytes]] = []        # served before the rules
        self.default = {"verdict": "cannot_tell", "quote": "", "reason": "nothing"}
        self.style = "anthropic"

    def when(self, phrase: str, verdict: str, quote: str = "", reason: str = "ok") -> None:
        self.rules.append((phrase, {"verdict": verdict, "quote": quote, "reason": reason}))

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float):
        payload = json.loads(body.decode())
        self.requests.append((url, headers, payload))
        if self.scripted:
            return self.scripted.pop(0)
        if "anthropic" in url:
            prompt = payload["messages"][0]["content"]
        else:
            prompt = payload["messages"][-1]["content"]
        self.prompts.append(prompt)
        answer = self.default
        for phrase, ans in self.rules:
            if phrase in prompt:
                answer = ans
                break
        text = answer if isinstance(answer, str) else json.dumps(answer)
        if "anthropic" in url:
            data = {"content": [{"type": "text", "text": text}]}
        else:
            data = {"choices": [{"message": {"content": text}}]}
        return 200, json.dumps(data).encode()


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


BIB = r"""
@article{coop,
  author = {Teixeira, Jose},
  title = {Collaboration in the open source arena},
  year = {2014},
  doi = {10.1000/coop},
}
@article{forks,
  author = {Robles, Gregorio},
  title = {Why do open source projects fork},
  year = {2012},
  doi = {10.1000/forks},
}
@article{noabs,
  author = {Nobody, N.},
  title = {A paper without any abstract},
  year = {2010},
  doi = {10.1000/noabs},
}
@article{nodoi,
  author = {Nobody, N.},
  title = {A paper with neither DOI nor abstract},
  year = {2011},
}
@article{ai,
  author = {Nobody, N.},
  title = {An AI-described paper},
  year = {2011},
  abstract = {[Paraphrase] This paper shows that every project forks.},
  comment = {generated_by_claude_sonnet_5.5},
}
@article{human,
  author = {Someone, Else},
  title = {A paper with a copied abstract},
  year = {2011},
  abstract = {Contributors who stay longer than two years write most of the code in mature projects.},
}
@article{wrongdoi,
  author = {Nobody, N.},
  title = {Quantum gravity in alpine meadows},
  year = {2011},
  doi = {10.1000/other},
}
"""

TEX = r"""\documentclass{article}
\newcommand{\citeme}[1]{\cite{#1}}
\begin{document}
Rival firms both collaborate and compete in open source ecosystems \cite{coop}.
Most forks are abandoned within a year~\citep{forks}. This is a second sentence.

% a comment about \cite{ghost}
Other work is cited elsewhere \cite{missing}.
\bibliography{refs}
\end{document}
"""


def setup(tmp_path: Path, tex: str = TEX, bib: str = BIB) -> Path:
    write(tmp_path / "refs.bib", bib)
    return write(tmp_path / "paper.tex", tex)


def run(tmp_path, capsys, *extra, tex: str = TEX, web=None, llm=None, bib: str = BIB,
        llm_args=("--llm", "anthropic"), env=None, monkeypatch=None):
    """Run main() and return (exit code, parsed JSON payload)."""
    texfile = setup(tmp_path, tex, bib)
    web = web or FakeWeb()
    llm = llm or FakeLLM()
    if monkeypatch and llm_args and llm_args[1] == "anthropic":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    argv = [str(texfile), "--bib", str(tmp_path / "refs.bib"), "--no-cache", "--json",
            "--registry", "crossref", *llm_args, *extra]
    rc = cc.main(argv, fetch=web, post=llm, sleep=lambda s: None)
    out = capsys.readouterr().out
    payload = json.loads(out[out.index("{"):]) if "{" in out else {}
    return rc, payload


def results(payload) -> dict[str, dict]:
    return {r["key"]: r for f in payload["files"] for r in f["citations"]}


# =============================================================================
# LaTeX reading
# =============================================================================

def cites(text: str):
    return [(c.cmd, c.keys) for c in cc.find_cites(text)]


def test_find_cites_variants():
    text = (r"a \cite{x} b \citep[see][p.~3]{y, z} c \citet*{w} d \parencite{v} "
            r"e \cites{p}{q} f \Cite{u}")
    assert cites(text) == [("cite", ["x"]), ("citep", ["y", "z"]), ("citet", ["w"]),
                           ("parencite", ["v"]), ("cites", ["p", "q"]), ("Cite", ["u"])]


def test_find_cites_ignores_author_year_and_nocite():
    assert cites(r"\citeauthor{a} \citeyear{a} \nocite{b} \citeauthor*{c} \cite{d}") == [("cite", ["d"])]


def test_find_cites_multiline_keys():
    assert cites("\\cite{a,\n   b}")[0][1] == ["a", "b"]


def test_comments_verbatim_and_preamble_are_skipped(tmp_path):
    tex = ("\\newcommand{\\x}{\\cite{pre}}\n\\begin{document}\n"
           "Real \\cite{real}. % \\cite{commented}\n"
           "Escaped percent 50\\% \\cite{after}.\n"
           "\\begin{verbatim}\n\\cite{verb}\n\\end{verbatim}\n"
           "\\begin{comment}\n\\cite{hidden}\n\\end{comment}\n"
           "\\end{document}\n\\cite{late}\n")
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    got = [(c.key, c.line) for c in cc.citations_in(tf)]
    assert got == [("real", 3), ("after", 4)]


def test_claim_text_marks_target_and_other_citations(tmp_path):
    tex = ("\\begin{document}\nFirms collaborate~\\cite{a} and compete~\\citep{b}. Next one.\n\\end{document}\n")
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    a, b = cc.citations_in(tf)
    assert a.claim == "Firms collaborate [[CITATION]] and compete [REF]."
    assert b.claim == "Firms collaborate [REF] and compete [[CITATION]]."


def test_sentence_splitting_respects_abbreviations(tmp_path):
    tex = ("\\begin{document}\nIntro sentence here. Smith et al. showed that firms (e.g. Nokia) "
           "compete, cf. Fig.~\\ref{f1}, in many ways~\\cite{k}. Another one.\n\\end{document}\n")
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    (c,) = cc.citations_in(tf)
    assert c.claim.startswith("Smith et al. showed that firms (e.g. Nokia) compete")
    assert c.claim.endswith("in many ways [[CITATION]].")
    assert "Intro sentence" not in c.claim
    assert c.context == "Intro sentence here."


def test_paragraph_and_section_boundaries(tmp_path):
    tex = ("\\begin{document}\nTail of one paragraph without a full stop\n\n"
           "Second paragraph \\cite{k}.\n\\section{Next}\nHeading text \\cite{m}.\n\\end{document}\n")
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    k, m = cc.citations_in(tf)
    assert k.claim == "Second paragraph [[CITATION]]."
    assert m.claim == "Heading text [[CITATION]]."


def test_comment_line_inside_paragraph_does_not_split_it(tmp_path):
    tex = ("\\begin{document}\nA claim that continues\n% a remark\non the next line \\cite{k}.\n\\end{document}\n")
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    (c,) = cc.citations_in(tf)
    assert c.claim == "A claim that continues on the next line [[CITATION]]."


def test_multi_key_command_gives_one_citation_per_key(tmp_path):
    tex = "\\begin{document}\nBoth agree \\cite{a,b}.\n\\end{document}\n"
    tf = cc.read_tex(write(tmp_path / "a.tex", tex))
    got = cc.citations_in(tf)
    assert [c.key for c in got] == ["a", "b"] and got[0].keys_in_cmd == ["a", "b"]


def test_inputs_and_bibliography_are_followed(tmp_path):
    write(tmp_path / "sec.tex", "Inner claim \\cite{k}.\n")
    write(tmp_path / "x.bib", "@article{k, title={T}, author={A, B}, year={2000}}\n")
    main_tex = write(tmp_path / "main.tex",
                     "\\begin{document}\n\\input{sec}\n\\addbibresource{x.bib}\n\\end{document}\n")
    files = cc.resolve_tex_tree([main_tex], follow=True)
    assert [f.path.name for f in files] == ["main.tex", "sec.tex"]
    assert [p.name for p in cc.bib_files_for(files, [])] == ["x.bib"]
    assert [f.path.name for f in cc.resolve_tex_tree([main_tex], follow=False)] == ["main.tex"]


# =============================================================================
# Quotes and excerpts
# =============================================================================

def test_quote_check():
    src = cc.Source("abstract", COOP_ABSTRACT)
    assert cc.quote_in_source("firms that both collaborate and compete", src)
    assert cc.quote_in_source("Firms that BOTH collaborate, and compete!", src)
    assert not cc.quote_in_source("firms that never collaborate or compete", src)
    assert not cc.quote_in_source("collaborate", src)                        # too short
    assert not cc.quote_in_source("Open source firms always cooperate peacefully", src)


def test_excerpt_selection_prefers_relevant_chunks():
    filler = "\n\n".join(f"Paragraph {i} talks about unrelated plumbing topics and pipes." * 8
                         for i in range(60))
    relevant = "Forks are abandoned within a year in most projects we studied."
    text = filler + "\n\n" + relevant + "\n\n" + filler
    excerpt, covered = cc.select_excerpts(text, "Most forks are abandoned within a year [[CITATION]].", 3000)
    assert relevant in excerpt and len(excerpt) <= 3300 and not covered
    short, covered = cc.select_excerpts("tiny text", "anything", 3000)
    assert short == "tiny text" and covered


@pytest.mark.parametrize("verdict,kind,covered,quote_ok,expected", [
    ("supported", "abstract", True, True, (cc.VERIFIED, "")),
    ("supported", "fulltext", True, False, (cc.UNVERIFIED, "QUOTE_NOT_IN_SOURCE")),
    ("partially_supported", "fulltext", False, True, (cc.PARTIAL, "")),
    ("contradicted", "abstract", True, True, (cc.MISMATCH, "")),
    ("contradicted", "abstract", True, False, (cc.UNVERIFIED, "QUOTE_NOT_IN_SOURCE")),
    ("not_supported", "abstract", True, False, (cc.UNVERIFIED, "ABSTRACT_INCONCLUSIVE")),
    ("not_supported", "fulltext", False, False, (cc.UNVERIFIED, "NOT_FOUND_IN_EXCERPTS")),
    ("not_supported", "fulltext", True, False, (cc.MISMATCH, "")),
    ("cannot_tell", "abstract", True, False, (cc.UNVERIFIED, "ABSTRACT_INCONCLUSIVE")),
    ("cannot_tell", "fulltext", True, False, (cc.UNVERIFIED, "CANNOT_TELL")),
])
def test_verdict_mapping(verdict, kind, covered, quote_ok, expected):
    assert cc.map_verdict(verdict, cc.Source(kind, "x"), covered, quote_ok) == expected


def test_parse_verdict():
    assert cc.parse_verdict('Sure! {"verdict": "Partially supported", "quote": "q", "reason": "r"} done') == \
        {"verdict": "partially_supported", "quote": "q", "reason": "r"}
    with pytest.raises(cc.LLMError):
        cc.parse_verdict("I think it is fine.")
    with pytest.raises(cc.LLMError):
        cc.parse_verdict('{"verdict": "maybe"}')


# =============================================================================
# End to end
# =============================================================================

def test_abstract_from_registry_verifies_supported_sentence(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Rival firms both collaborate", "supported", "firms that both collaborate and compete")
    rc, payload = run(tmp_path, capsys, llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["coop"]
    assert rc == 0
    assert (r["status"], r["evidence"], r["line"]) == ("VERIFIED", "abstract (Crossref)", 4)
    assert r["quote"] == "firms that both collaborate and compete"
    assert "[[CITATION]]" in llm.prompts[0] and "<excerpts>" in llm.prompts[0]


def test_model_that_invents_a_quote_is_not_trusted(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms always collaborate and never compete")
    _, payload = run(tmp_path, capsys, llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["coop"]
    assert (r["status"], r["reason"]) == ("UNVERIFIED", "QUOTE_NOT_IN_SOURCE")
    assert r["quote"] == ""


def test_contradiction_is_a_mismatch_and_check_exits_2(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Most forks", "contradicted", "most forks are abandoned within a year", "says the opposite")
    rc, payload = run(tmp_path, capsys, "--check", llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert r["status"] == "MISMATCH" and r["model_reason"] == "says the opposite"
    assert rc == 2


def test_abstract_only_cannot_prove_absence(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Most forks", "not_supported", "", "abstract does not discuss it")
    rc, payload = run(tmp_path, capsys, "--check", "--only", "forks", llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert (r["status"], r["reason"]) == ("UNVERIFIED", "ABSTRACT_INCONCLUSIVE")
    assert rc == 0


def test_partial_support(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Most forks", "partially_supported", "most forks are abandoned within a year")
    _, payload = run(tmp_path, capsys, llm=llm, monkeypatch=monkeypatch)
    assert results(payload)["forks"]["status"] == "PARTIAL"


def test_unverified_reasons(tmp_path, capsys, monkeypatch):
    tex = ("\\begin{document}\nA \\cite{noabs}. B \\cite{nodoi}. C \\cite{ai}. D \\cite{wrongdoi}. "
           "E \\cite{missing}. F \\cite{human}.\n\\end{document}\n")
    llm = FakeLLM()
    llm.when("F [[CITATION]]", "supported", "write most of the code in mature projects")
    _, payload = run(tmp_path, capsys, llm=llm, tex=tex, monkeypatch=monkeypatch)
    r = results(payload)
    assert r["noabs"]["reason"] == "NO_SOURCE_ACCESS"
    assert r["nodoi"]["reason"] == "NO_SOURCE_ACCESS" and "no DOI" in r["nodoi"]["why"]
    assert r["ai"]["reason"] == "AI_ABSTRACT_ONLY"
    assert r["wrongdoi"]["reason"] == "SOURCE_MISMATCH" and "different work" in r["wrongdoi"]["why"]
    assert r["missing"]["reason"] == "NO_BIB_ENTRY"
    assert r["human"]["status"] == "VERIFIED" and r["human"]["evidence"] == "abstract (.bib entry)"
    assert len(llm.prompts) == 1                       # only the checkable citation reached the model


def test_ai_paraphrase_is_never_used_as_evidence(tmp_path, capsys, monkeypatch):
    tex = "\\begin{document}\nEvery project forks \\cite{ai}.\n\\end{document}\n"
    llm = FakeLLM()
    llm.when("forks", "supported", "every project forks")
    _, payload = run(tmp_path, capsys, llm=llm, tex=tex, monkeypatch=monkeypatch)
    assert results(payload)["ai"]["status"] == "UNVERIFIED"
    assert llm.prompts == []


def test_local_fulltext_mismatch_when_read_in_full(tmp_path, capsys, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    write(src / "forks.txt", "Introduction\n\nWe studied forks. Most forks survive for decades.\n")
    llm = FakeLLM()
    llm.when("Most forks", "not_supported", "", "the paper says the opposite is rare")
    _, payload = run(tmp_path, capsys, "--sources", str(src), llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert (r["status"], r["evidence"]) == ("MISMATCH", "full text (forks.txt)")


def test_long_fulltext_not_found_is_not_a_mismatch(tmp_path, capsys, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    write(src / "forks.txt", "\n\n".join(f"Plumbing paragraph number {i} about pipes." * 10 for i in range(80)))
    llm = FakeLLM()
    llm.when("Most forks", "not_supported")
    _, payload = run(tmp_path, capsys, "--sources", str(src), "--max-chars", "2000",
                     llm=llm, monkeypatch=monkeypatch)
    assert results(payload)["forks"]["reason"] == "NOT_FOUND_IN_EXCERPTS"


def test_local_file_found_by_doi_slug_and_file_field(tmp_path, capsys, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    write(src / "10.1000_coop.txt", COOP_ABSTRACT + "\n\nThe full text continues here.")
    write(tmp_path / "forks-copy.txt", FORK_ABSTRACT)
    bib = BIB.replace("doi = {10.1000/forks},", "doi = {10.1000/forks},\n  file = {:forks-copy.txt:TXT},")
    llm = FakeLLM()
    _, payload = run(tmp_path, capsys, "--sources", str(src), llm=llm, bib=bib, monkeypatch=monkeypatch)
    r = results(payload)
    assert r["coop"]["evidence"] == "full text (10.1000_coop.txt)"
    assert r["forks"]["evidence"] == "full text (forks-copy.txt)"


def test_pdf_without_extractor_is_reported(tmp_path, capsys, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "noabs.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "pypdf", None)
    tex = "\\begin{document}\nNothing happened \\cite{noabs}.\n\\end{document}\n"
    _, payload = run(tmp_path, capsys, "--sources", str(src), "--registry", "none",
                     tex=tex, monkeypatch=monkeypatch)
    assert results(payload)["noabs"]["reason"] == "NO_PDF_EXTRACTOR"


def test_fetch_fulltext_via_unpaywall(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.unpaywall["10.1000/noabs"] = "https://oa.example.org/noabs.pdf"
    web.pdfs["https://oa.example.org/noabs.pdf"] = b"%PDF-1.4 fake"
    monkeypatch.setattr(cc, "extract_text",
                        lambda p: ("Open access text: nothing surprising happened in 2010.", ""))
    tex = "\\begin{document}\nNothing surprising happened \\cite{noabs}.\n\\end{document}\n"
    llm = FakeLLM()
    llm.when("Nothing surprising", "supported", "nothing surprising happened in 2010")
    _, payload = run(tmp_path, capsys, "--fetch-fulltext", "--mailto", "me@example.org",
                     "--cache-dir", str(tmp_path / "cc"), web=web, llm=llm, tex=tex, monkeypatch=monkeypatch)
    r = results(payload)["noabs"]
    assert r["status"] == "VERIFIED" and r["evidence"].startswith("full text (open access")
    assert any("api.unpaywall.org" in u for u in web.calls)


def test_fetch_fulltext_needs_mailto_for_unpaywall(tmp_path, capsys, monkeypatch):
    tex = "\\begin{document}\nX \\cite{noabs}.\n\\end{document}\n"
    _, payload = run(tmp_path, capsys, "--fetch-fulltext", "--cache-dir", str(tmp_path / "cc"),
                     tex=tex, monkeypatch=monkeypatch)
    assert "no --mailto" in results(payload)["noabs"]["why"]


def test_registry_outage_is_not_fatal(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.down.add("crossref")
    _, payload = run(tmp_path, capsys, web=web, monkeypatch=monkeypatch)
    r = results(payload)
    assert r["coop"]["reason"] == "NO_SOURCE_ACCESS" and "lookup failed" in r["coop"]["why"]


# =============================================================================
# Models
# =============================================================================

def test_anthropic_request_shape(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    run(tmp_path, capsys, "--model", "claude-test", llm=llm, monkeypatch=monkeypatch)
    url, headers, payload = llm.requests[0]
    assert url == cc.ANTHROPIC_URL
    assert headers["x-api-key"] == "test-key" and headers["anthropic-version"]
    assert payload["model"] == "claude-test" and payload["temperature"] == 0
    assert "untrusted" in payload["system"] and "<excerpts>" in payload["messages"][0]["content"]


def test_ollama_uses_local_endpoint_without_key(tmp_path, capsys):
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms that both collaborate and compete")
    rc, payload = run(tmp_path, capsys, "--model", "llama3.1:8b", llm=llm, llm_args=("--llm", "ollama"))
    url, headers, body = llm.requests[0]
    assert url == "http://localhost:11434/v1/chat/completions" and "Authorization" not in headers
    assert body["model"] == "llama3.1:8b" and body["messages"][0]["role"] == "system"
    assert results(payload)["coop"]["status"] == "VERIFIED"


def test_openai_compatible_with_base_url_and_key(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    llm = FakeLLM()
    run(tmp_path, capsys, "--model", "m", "--base-url", "http://llm.local:8000/v1/", llm=llm,
        llm_args=("--llm", "openai"))
    url, headers, _ = llm.requests[0]
    assert url == "http://llm.local:8000/v1/chat/completions" and headers["Authorization"] == "Bearer sk-test"


def test_ollama_needs_a_model(tmp_path, capsys):
    rc, _ = run(tmp_path, capsys, llm_args=("--llm", "ollama"))
    assert rc == 1


def test_auto_picks_anthropic_when_key_is_set_and_none_otherwise(tmp_path, capsys, monkeypatch):
    rc, payload = run(tmp_path, capsys, llm_args=("--llm", "auto"))
    assert payload["model"] == "none"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    _, payload = run(tmp_path, capsys, llm_args=("--llm", "auto"))
    assert payload["model"] == "anthropic:" + cc.DEFAULT_MODEL


def test_no_model_reports_what_could_be_checked(tmp_path, capsys):
    rc, payload = run(tmp_path, capsys, llm_args=("--llm", "none"))
    r = results(payload)
    assert r["coop"]["reason"] == "NO_LLM" and r["coop"]["evidence"] == "abstract (Crossref)"
    assert r["missing"]["reason"] == "NO_BIB_ENTRY"
    assert rc == 0


def test_retry_after_rate_limit_and_bad_json(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    good = json.dumps({"content": [{"type": "text", "text": json.dumps(
        {"verdict": "supported", "quote": "firms that both collaborate and compete", "reason": "ok"})}]})
    bad = json.dumps({"content": [{"type": "text", "text": "I cannot say"}]})
    llm.scripted = [(429, b"slow down"), (200, bad.encode()), (200, good.encode())]
    _, payload = run(tmp_path, capsys, "--only", "coop", llm=llm, monkeypatch=monkeypatch)
    assert results(payload)["coop"]["status"] == "VERIFIED"
    assert len(llm.requests) == 3


def test_refused_key_and_model_switched_off(tmp_path, capsys, monkeypatch):
    tex = "\\begin{document}\n" + "".join(f"Claim {i} \\cite{{coop}}. " for i in range(6)) + "\n\\end{document}\n"
    llm = FakeLLM()
    llm.scripted = [(401, b"invalid x-api-key")] * 10
    _, payload = run(tmp_path, capsys, llm=llm, tex=tex, monkeypatch=monkeypatch)
    rs = [r for f in payload["files"] for r in f["citations"]]
    assert {r["reason"] for r in rs} == {"LLM_ERROR"}
    assert len(llm.requests) == cc.MAX_LLM_FAILURES      # then it stopped asking


def test_answers_are_cached(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms that both collaborate and compete")
    args = ("--only", "coop", "--cache-dir", str(tmp_path / "cc"))
    texfile = setup(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    argv = [str(texfile), "--json", "--registry", "crossref", "--llm", "anthropic", *args]
    cc.main(argv, fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    cc.main(argv, fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    assert len(llm.requests) == 1
    cc.main(argv + ["--refresh"], fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    assert len(llm.requests) == 2
    capsys.readouterr()


# =============================================================================
# Output
# =============================================================================

def test_json_payload_shape(tmp_path, capsys, monkeypatch):
    _, payload = run(tmp_path, capsys, monkeypatch=monkeypatch)
    assert set(payload) == {"model", "counts", "reasons", "bib_files", "files"}
    c = payload["files"][0]["citations"][0]
    assert set(c) == {"file", "line", "key", "command", "sentence", "status", "reason", "why",
                      "detail", "evidence", "quote", "model_reason"}
    assert sum(payload["counts"].values()) == 3


def test_check_strict_and_limit(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    rc, _ = run(tmp_path, capsys, "--check", llm=llm, monkeypatch=monkeypatch)
    assert rc == 0                                           # nothing contradicts
    rc, _ = run(tmp_path, capsys, "--check", "--strict", llm=llm, monkeypatch=monkeypatch)
    assert rc == 2
    _, payload = run(tmp_path, capsys, "--limit", "1", llm=llm, monkeypatch=monkeypatch)
    assert sum(payload["counts"].values()) == 1


def test_report_and_gha(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Most forks", "contradicted", "most forks are abandoned within a year", "opposite")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report = tmp_path / "report.md"
    texfile = setup(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cc.main([str(texfile), "--no-cache", "--registry", "crossref", "--llm", "anthropic",
             "--gha", "--report", str(report)], fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert "::error file=" in out and "line=5" in out and "forks: MISMATCH" in out
    assert "::notice" in out
    md = report.read_text()
    assert "## cite-check" in md and "`forks`" in md and "NO_BIB_ENTRY" in md
    assert summary.read_text() == md


def test_mark_writes_copy_and_is_idempotent(tmp_path, capsys, monkeypatch):
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms that both collaborate and compete")
    texfile = setup(tmp_path)
    original = texfile.read_text()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    argv = [str(texfile), "--no-cache", "--registry", "crossref", "--llm", "anthropic", "--mark"]
    cc.main(argv, fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    marked = tmp_path / "paper.checked.tex"
    text = marked.read_text()
    assert texfile.read_text() == original
    lines = text.splitlines()
    i = next(n for n, l in enumerate(lines) if "Rival firms" in l)
    assert lines[i + 1] == "% cite-check: coop VERIFIED(abstract (Crossref))"
    assert "% cite-check: missing UNVERIFIED(NO_BIB_ENTRY)" in text
    assert text.count("% cite-check:") == 3
    # running on the marked copy again does not pile up markers
    cc.main([str(marked)] + argv[1:], fetch=FakeWeb(), post=llm, sleep=lambda s: None)
    again = (tmp_path / "paper.checked.checked.tex").read_text()
    assert again.count("% cite-check:") == text.count("% cite-check:")
    capsys.readouterr()


def test_marked_copy_keeps_line_endings(tmp_path):
    tf = cc.read_tex(write(tmp_path / "w.tex", "x\n"))
    (tmp_path / "w.tex").write_bytes(b"\\begin{document}\r\nClaim \\cite{k}.\r\n\\end{document}\r\n")
    tf = cc.read_tex(tmp_path / "w.tex")
    r = cc.Result("w.tex", 2, "k", "cite", "Claim.", cc.UNVERIFIED, "NO_LLM", "why")
    target = cc.write_marked_copy(tf, [r])
    assert target.read_bytes().count(b"\r\n") == 4


def test_errors(tmp_path, capsys):
    rc = cc.main([str(tmp_path / "missing.tex")])
    assert rc == 1
    tex = write(tmp_path / "nobib.tex", "\\begin{document}\nX \\cite{a}.\n\\end{document}\n")
    assert cc.main([str(tex), "--llm", "none"]) == 1
    capsys.readouterr()


# =============================================================================
# Markdown folders
# =============================================================================

MD_FORKS = ("Why do open source projects fork\n\nWe find that most forks are abandoned within a year "
            "after the initial split, whereas a minority becomes independent.\n")


def md_dir(tmp_path: Path) -> Path:
    d = tmp_path / "md"
    d.mkdir()
    return d


def test_markdown_found_by_key_doi_and_title(tmp_path, capsys, monkeypatch):
    d = md_dir(tmp_path)
    write(d / "forks.md", "---\ntitle: x\n---\n# Heading\n\n" + MD_FORKS)             # file name = key
    write(d / "zotero-0001.md", "---\ndoi: https://doi.org/10.1000/coop\n---\n" + COOP_ABSTRACT)
    write(d / "some-export.md", "# A paper with neither DOI nor abstract\n\nSomething was done in 2011.\n")
    write(d / "unrelated.md", "# Soil chemistry of alpine meadows\n\nSoil.\n")
    tex = ("\\begin{document}\nA \\cite{forks}. B \\cite{coop}. C \\cite{nodoi}. D \\cite{noabs}.\n"
           "\\end{document}\n")
    _, payload = run(tmp_path, capsys, "--markdown-dir", str(d), "--llm", "none", tex=tex,
                     llm_args=(), monkeypatch=monkeypatch)
    r = results(payload)
    assert r["forks"]["evidence"] == "full text (markdown: forks.md)"
    assert r["coop"]["evidence"] == "full text (markdown: zotero-0001.md)"
    assert r["nodoi"]["evidence"] == "full text (markdown: some-export.md)"
    assert r["noabs"]["reason"] == "NO_SOURCE_ACCESS"                       # nothing matches


def test_markdown_text_reaches_the_model_without_front_matter_and_images(tmp_path, capsys, monkeypatch):
    d = md_dir(tmp_path)
    write(d / "forks.md", "---\ndoi: 10.1000/forks\nsecret: hidden-metadata\n---\n"
                          "![fig](x.png)\n<!-- note -->\n" + MD_FORKS + "See [the appendix](app.md).\n")
    llm = FakeLLM()
    llm.when("Most forks", "supported", "most forks are abandoned within a year")
    _, payload = run(tmp_path, capsys, "--markdown-dir", str(d), "--only", "forks", llm=llm,
                     monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert r["status"] == "VERIFIED" and r["evidence"] == "full text (markdown: forks.md)"
    prompt = llm.prompts[0]
    assert "hidden-metadata" not in prompt and "x.png" not in prompt and "note" not in prompt.split("<excerpts>")[1]
    assert "the appendix" in prompt and "app.md" not in prompt


def test_markdown_dir_must_exist(tmp_path, capsys):
    rc, _ = run(tmp_path, capsys, "--markdown-dir", str(tmp_path / "nope"))
    assert rc == 1


def test_sources_folder_also_reads_markdown(tmp_path, capsys, monkeypatch):
    d = md_dir(tmp_path)
    write(d / "forks.md", MD_FORKS)
    _, payload = run(tmp_path, capsys, "--sources", str(d), "--llm", "none", llm_args=(),
                     monkeypatch=monkeypatch)
    assert results(payload)["forks"]["evidence"] == "full text (forks.md)"


# =============================================================================
# Consensus
# =============================================================================

CONSENSUS_FORKS = {"papers": [{"doi": "10.1000/forks", "status": "available",
                               "markdown": "# Why forks\n\n" + MD_FORKS,
                               "sections": [{"title": "Intro", "markdown": "Intro."}]}]}


def test_consensus_full_text(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.consensus["10.1000/forks"] = CONSENSUS_FORKS
    llm = FakeLLM()
    llm.when("Most forks", "supported", "most forks are abandoned within a year")
    _, payload = run(tmp_path, capsys, "--consensus", "--consensus-key", "ck", "--only", "forks",
                     web=web, llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert (r["status"], r["evidence"]) == ("VERIFIED", "full text (Consensus)")
    url, headers = next((u, h) for u, h in web.headers if "consensus" in u)
    assert "dois=10.1000%2Fforks" in url and headers["x-api-key"] == "ck"


def test_consensus_sections_only_and_not_available(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.consensus["10.1000/forks"] = {"results": [{"status": "available", "sections": [
        {"title": "Results", "text": "Most forks are abandoned within a year."}]}]}
    web.consensus["10.1000/coop"] = {"papers": [{"doi": "10.1000/coop", "status": "not_available"}]}
    tex = "\\begin{document}\nA \\cite{forks}. B \\cite{coop}.\n\\end{document}\n"
    _, payload = run(tmp_path, capsys, "--consensus", "--consensus-key", "ck", "--llm", "none",
                     web=web, tex=tex, llm_args=(), monkeypatch=monkeypatch)
    r = results(payload)
    assert r["forks"]["evidence"] == "full text (Consensus)"
    assert r["coop"]["evidence"] == "abstract (Crossref)"          # fell back to the abstract


def test_consensus_needs_a_key_and_reads_the_environment(tmp_path, capsys, monkeypatch):
    rc, _ = run(tmp_path, capsys, "--consensus")
    assert rc == 1
    monkeypatch.setenv("CONSENSUS_API_KEY", "from-env")
    web = FakeWeb()
    web.consensus["10.1000/forks"] = CONSENSUS_FORKS
    run(tmp_path, capsys, "--consensus", "--llm", "none", "--only", "forks", web=web, llm_args=(),
        monkeypatch=monkeypatch)
    assert any(h.get("x-api-key") == "from-env" for u, h in web.headers if "consensus" in u)


def test_consensus_refusal_switches_it_off(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.consensus["10.1000/forks"] = 401
    web.consensus["10.1000/coop"] = 401
    tex = "\\begin{document}\nA \\cite{forks}. B \\cite{coop}.\n\\end{document}\n"
    _, payload = run(tmp_path, capsys, "--consensus", "--consensus-key", "bad", "--llm", "none",
                     web=web, tex=tex, llm_args=(), monkeypatch=monkeypatch)
    assert sum("consensus" in u for u in web.calls) == 1            # not asked again
    assert results(payload)["forks"]["evidence"] == "abstract (Crossref)"


def test_consensus_is_off_unless_asked(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CONSENSUS_API_KEY", "set-but-not-asked")
    web = FakeWeb()
    run(tmp_path, capsys, "--llm", "none", web=web, llm_args=(), monkeypatch=monkeypatch)
    assert not any("consensus" in u for u in web.calls)


# =============================================================================
# Semantic Scholar
# =============================================================================

def test_semantic_scholar_abstract_when_registries_are_down(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.down.add("crossref")
    web.s2_papers["10.1000/coop"] = {"title": "Collaboration in the open source arena", "abstract": COOP_ABSTRACT}
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms that both collaborate and compete")
    _, payload = run(tmp_path, capsys, "--semantic-scholar", "--only", "coop", web=web, llm=llm,
                     monkeypatch=monkeypatch)
    assert results(payload)["coop"]["evidence"] == "abstract (Semantic Scholar)"
    url, headers = next((u, h) for u, h in web.headers if "/paper/DOI:" in u)
    assert "x-api-key" not in headers                                  # works without a key


def test_semantic_scholar_snippets_extend_an_abstract(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.s2_snippets = ["In our sample, most forks are abandoned within twelve months of the split."]
    llm = FakeLLM()
    llm.when("Most forks", "supported", "most forks are abandoned within twelve months of the split")
    _, payload = run(tmp_path, capsys, "--semantic-scholar", "--s2-key", "sk", "--only", "forks",
                     web=web, llm=llm, monkeypatch=monkeypatch)
    r = results(payload)["forks"]
    assert (r["status"], r["evidence"]) == ("VERIFIED", "abstract + excerpts (Semantic Scholar)")
    assert "twelve months" in llm.prompts[0] and "220 projects" in llm.prompts[0]     # snippet + abstract
    snippet_url, headers = next((u, h) for u, h in web.headers if "snippet/search" in u)
    assert "paperIds=DOI:10.1000/forks" in snippet_url and "Most+forks" not in snippet_url
    assert "CITATION" not in snippet_url and headers["x-api-key"] == "sk"


def test_passages_are_not_the_whole_paper(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.s2_snippets = ["An unrelated passage about licences."]
    llm = FakeLLM()
    llm.when("Most forks", "not_supported")
    _, payload = run(tmp_path, capsys, "--semantic-scholar", "--only", "forks", web=web, llm=llm,
                     monkeypatch=monkeypatch)
    assert results(payload)["forks"]["reason"] == "NOT_FOUND_IN_EXCERPTS"     # never a MISMATCH


def test_snippets_are_skipped_when_the_whole_text_is_there(tmp_path, capsys, monkeypatch):
    d = md_dir(tmp_path)
    write(d / "forks.md", MD_FORKS)
    web = FakeWeb()
    web.s2_snippets = ["x"]
    run(tmp_path, capsys, "--semantic-scholar", "--markdown-dir", str(d), "--only", "forks",
        web=web, llm=FakeLLM(), monkeypatch=monkeypatch)
    assert not any("snippet" in u for u in web.calls)


def test_no_snippet_requests_without_a_model(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    run(tmp_path, capsys, "--semantic-scholar", "--llm", "none", web=web, llm_args=(), monkeypatch=monkeypatch)
    assert not any("snippet" in u for u in web.calls)


def test_semantic_scholar_open_access_pdf(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.s2_papers["10.1000/noabs"] = {"title": "A paper without any abstract",
                                      "openAccessPdf": {"url": "https://oa.example.org/n.pdf"}}
    web.pdfs["https://oa.example.org/n.pdf"] = b"%PDF-1.4 fake"
    monkeypatch.setattr(cc, "extract_text", lambda p: ("Nothing surprising happened in 2010.", ""))
    tex = "\\begin{document}\nNothing surprising happened \\cite{noabs}.\n\\end{document}\n"
    llm = FakeLLM()
    llm.when("Nothing surprising", "supported", "nothing surprising happened in 2010")
    _, payload = run(tmp_path, capsys, "--semantic-scholar", "--fetch-fulltext",
                     "--cache-dir", str(tmp_path / "cc"), web=web, llm=llm, tex=tex, monkeypatch=monkeypatch)
    r = results(payload)["noabs"]
    assert r["status"] == "VERIFIED" and r["evidence"].startswith("full text (open access")


def test_services_outage_is_not_fatal(tmp_path, capsys, monkeypatch):
    web = FakeWeb()
    web.down.update({"semanticscholar", "consensus"})
    llm = FakeLLM()
    llm.when("Rival firms", "supported", "firms that both collaborate and compete")
    rc, payload = run(tmp_path, capsys, "--semantic-scholar", "--consensus", "--consensus-key", "k",
                      "--only", "coop", web=web, llm=llm, monkeypatch=monkeypatch)
    assert rc == 0 and results(payload)["coop"]["status"] == "VERIFIED"


def test_help_renders(capsys):
    with pytest.raises(SystemExit) as e:
        cc.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--markdown-dir", "--consensus", "--semantic-scholar", "--sources", "--llm", "--mark"):
        assert flag in out
