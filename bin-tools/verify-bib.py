#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify-bib.py — Verify BibTeX entries against Crossref and OpenAlex.

For every entry in one or more .bib files, look the work up in the Crossref
REST API and in the OpenAlex API and check that what the .bib file says
matches what those registries say:

  * title     fuzzy match on a normalised title (LaTeX, case, accents and
              punctuation are ignored; title + subtitle is tried too)
  * authors   same people, same number, same order (compared on surnames;
              "and others" is understood)
  * abstract  word-level similarity (only when both sides have an abstract;
              abstracts marked "[Paraphrase]" in the .bib file are declared
              rewordings and are not compared)
  * doi       the DOI in the .bib file is the DOI of the matched record

Lookup strategy, per source:
  1. by DOI when the entry has one (a `doi` field, or a doi.org `url`);
  2. otherwise — or when the DOI does not resolve, or resolves to a work with
     a different title — by a title search, keeping the best candidate only if
     its title is close enough.

The tool is read-only: it never modifies a .bib file.

Entry status:
  VERIFIED   found in at least one source and every comparable field matches
  WARN       found, but something is only a near match (e.g. subtitle missing,
             author order differs, abstract truncated, DOI differs)
  MISMATCH   found, but title, authors or abstract disagree
  NOT_FOUND  in none of the sources — unindexed venue, or a reference that
             does not exist
  UNCHECKED  could not be checked (network/API failure, no title)

Uses bibtexparser v2 (>=2.0.0b1,<3.0), unidecode and Rich — all already in
requirements.txt for fix-bib.py and gen-bib-keys.py. HTTP is done with the
standard library, so nothing new needs installing.

API access:
  Crossref   no key needed. Pass --mailto (or set CROSSREF_MAILTO) to use the
             "polite pool", which has higher rate limits.
  OpenAlex   an API key is required since February 2026 (free, from
             https://openalex.org/settings/api). Pass --openalex-key or set
             OPENALEX_API_KEY. DOI lookups are free; title searches are
             charged against the daily allowance of the key.

Responses are cached on disk (default ~/.cache/verify-bib/cache.json), so
re-runs and interrupted runs do not repeat requests.

Exit codes:
  0  success (or --check and every entry is VERIFIED or WARN)
  1  error (fatal failure, --strict warnings)
  2  --check and at least one entry is MISMATCH or NOT_FOUND
"""

from __future__ import annotations

import argparse
import html
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Optional

import bibtexparser
from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel
from rich.progress import (BarColumn, MofNCompleteColumn, Progress,
                           TextColumn, TimeElapsedColumn)
from rich.table import Table
from rich.text import Text
from unidecode import unidecode


# =============================================================================
# Constants
# =============================================================================

VERSION = "1.0.0"
PROJECT_URL = "https://github.com/jaateixeira/FLOSS-research-bibliography-collection"

CROSSREF_API = "https://api.crossref.org"
OPENALEX_API = "https://api.openalex.org"
SOURCES = ("crossref", "openalex")
SOURCE_LABEL = {"crossref": "Crossref", "openalex": "OpenAlex"}

# Only ask the APIs for the fields we compare (smaller, faster responses).
CROSSREF_SELECT = "DOI,title,subtitle,author,abstract,issued,type,container-title"
OPENALEX_SELECT = ("id,doi,display_name,publication_year,type,"
                   "authorships,abstract_inverted_index")

# Similarity thresholds (all overridable from the command line).
TITLE_MATCH = 0.95      # >= : titles match
TITLE_NEAR = 0.85       # >= : near match (WARN); also the bar a title-search
                        #      candidate must clear to be accepted at all
ABSTRACT_MATCH = 0.90   # >= : abstracts match

SEARCH_ROWS = 5         # candidates fetched per title search
CACHE_TTL_DAYS = 30
MAX_SOURCE_FAILURES = 3  # consecutive failures before a source is switched off

AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+", re.IGNORECASE)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}]+", re.IGNORECASE)
YEAR_RE = re.compile(r"(\d{4})")
TAG_RE = re.compile(r"<[^>]+>")
# Abstracts in this collection that are rewordings, not the publisher's text,
# start with this marker.
PARAPHRASE_RE = re.compile(r"^\s*\[paraphrase\]", re.IGNORECASE)

# Entry status
VERIFIED = "VERIFIED"
WARN = "WARN"
MISMATCH = "MISMATCH"
NOT_FOUND = "NOT_FOUND"
UNCHECKED = "UNCHECKED"
STATUS_ORDER = (VERIFIED, WARN, MISMATCH, NOT_FOUND, UNCHECKED)
STATUS_STYLE = {
    VERIFIED: "green",
    WARN: "yellow",
    MISMATCH: "bold red",
    NOT_FOUND: "bold magenta",
    UNCHECKED: "dim",
}

# Field-check status
F_MATCH = "match"
F_NEAR = "near"
F_MISMATCH = "mismatch"
F_MISSING_BIB = "missing_bib"   # the source has it, the .bib entry does not
F_MISSING_API = "missing_api"   # the .bib entry has it, the source does not
F_NA = "n/a"                    # neither side has it
F_PARAPHRASE = "paraphrase"     # .bib abstract is a declared paraphrase
F_MARK = {
    F_MATCH: ("✓", "green"),
    F_NEAR: ("≈", "yellow"),
    F_MISMATCH: ("✗", "bold red"),
    F_MISSING_BIB: ("+", "cyan"),
    F_MISSING_API: ("–", "dim"),
    F_NA: ("–", "dim"),
    F_PARAPHRASE: ("~", "dim"),
}

IS_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))
IS_TTY = sys.stdin.isatty() and sys.stdout.isatty()

console: Console = Console()


# =============================================================================
# Rich-based logging (same shim as fix-bib.py / gen-bib-keys.py)
# =============================================================================

class RichLogger:
    """Minimal logger that prints through Rich, with a verbosity gate."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self.verbose = False

    def _emit(self, label: str, style: str, message: str) -> None:
        prefix = f"[{style}]{label}[/]"
        self.console.print(f"{prefix} {message}")

    def debug(self, message: str) -> None:
        if self.verbose:
            self._emit("DEBUG", "dim cyan", message)

    def info(self, message: str) -> None:
        self._emit("INFO", "bold blue", message)

    def warning(self, message: str) -> None:
        self._emit("WARN", "bold yellow", message)

    def error(self, message: str) -> None:
        self._emit("ERROR", "bold red", message)


log: RichLogger = RichLogger(console)


def setup_logging(verbose: bool, output_console: Optional[Console] = None) -> None:
    global console, log
    if output_console is not None:
        console = output_console
    log = RichLogger(console)
    log.verbose = verbose


# =============================================================================
# v2 field helpers (same as fix-bib.py / gen-bib-keys.py)
# =============================================================================

def entry_get(entry, field_name: str, default: str = "") -> str:
    """Read a field value from a bibtexparser v2 Entry object safely."""
    fields = getattr(entry, "fields_dict", {}) or {}
    fld = fields.get(field_name)
    if fld is None:
        return default
    value = getattr(fld, "value", None)
    if value is None:
        return default
    return str(value)


# =============================================================================
# Text normalisation
# =============================================================================

# \'a  {\'a}  \'{a}  \c{c}  \v s  \'{\i}  ...  -> the bare letter
_LATEX_ACCENT_RE = re.compile(
    r"""\\(?:[`'^"~=.]|[uvHcdbkrt](?=[\s{\\]))\s*\{?\s*\\?([A-Za-z])\s*\}?"""
)
_LATEX_LETTER_RE = re.compile(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])")
_LATEX_LETTERS = {
    "ss": "ss", "ae": "ae", "AE": "AE", "oe": "oe", "OE": "OE",
    "aa": "a", "AA": "A", "o": "o", "O": "O", "l": "l", "L": "L",
    "i": "i", "j": "j",
}
_LATEX_ESCAPED_RE = re.compile(r"\\([&%_#$])")
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?")


def delatex(s: str) -> str:
    """Turn BibTeX/LaTeX markup into plain text (good enough for comparing)."""
    if not s:
        return ""
    s = _LATEX_ACCENT_RE.sub(r"\1", s)
    s = _LATEX_LETTER_RE.sub(lambda m: _LATEX_LETTERS[m.group(1)], s)
    s = _LATEX_ESCAPED_RE.sub(r"\1", s)
    s = s.replace("~", " ").replace("---", "-").replace("--", "-")
    s = _LATEX_COMMAND_RE.sub(" ", s)        # \emph{x} -> x, \textit{x} -> x
    s = s.replace("{", "").replace("}", "").replace("$", "")
    return re.sub(r"\s+", " ", s).strip()


def strip_markup(s: str) -> str:
    """Remove the JATS/HTML/MathML tags and entities found in API metadata."""
    if not s:
        return ""
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def norm_text(s: str) -> str:
    """Lower-case ASCII words separated by single spaces."""
    s = unidecode(s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def norm_name(s: str) -> str:
    """A surname as a bare lower-case ASCII string: 'Izquierdo-Cortázar' ->
    'izquierdocortazar'."""
    return re.sub(r"[^a-z]", "", unidecode(s or "").lower())


def name_tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z]+", unidecode(s or "").lower()) if t}


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def surname(name: str) -> str:
    """The family name of a person, following BibTeX's own rules:
    'Last, First' -> Last;  'First von Last' -> 'von Last'."""
    name = (name or "").strip()
    if "," in name:
        return name.split(",", 1)[0].strip()
    parts = name.split()
    while len(parts) > 1 and parts[-1].lower().strip(".") in _NAME_SUFFIXES:
        parts.pop()
    if not parts:
        return ""
    start = len(parts) - 1
    while start > 1 and parts[start - 1][:1].islower():   # von, van der, de la ...
        start -= 1
    return " ".join(parts[start:])


def clean_doi(raw: str) -> str:
    """Extract a bare, lower-case DOI from a doi/url field value."""
    m = DOI_RE.search(delatex(raw or "").replace("\\", ""))
    if not m:
        return ""
    return m.group(0).rstrip(".,;)]").lower()


def shorten(s: str, width: int = 110) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= width else s[: width - 1] + "…"


# =============================================================================
# Data model
# =============================================================================

@dataclass
class Person:
    family: str            # surname as written
    full: str              # the whole name as written

    @property
    def key(self) -> str:
        return norm_name(self.family)

    @property
    def tokens(self) -> set[str]:
        return name_tokens(self.full)


@dataclass
class BibInfo:
    """The parts of a .bib entry that get verified."""
    key: str
    entry_type: str
    title: str
    authors: list[Person]
    has_others: bool
    abstract: str
    doi: str
    year: str
    line: Optional[int] = None


@dataclass
class FieldCheck:
    status: str = F_NA
    score: Optional[float] = None
    detail: str = ""

    def as_dict(self) -> dict:
        d: dict = {"status": self.status}
        if self.score is not None:
            d["score"] = round(self.score, 3)
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class SourceResult:
    source: str
    state: str = "skipped"          # found | not_found | error | skipped
    how: str = ""                   # doi | search
    record: Optional[dict] = None
    title: FieldCheck = field(default_factory=FieldCheck)
    authors: FieldCheck = field(default_factory=FieldCheck)
    abstract: FieldCheck = field(default_factory=FieldCheck)
    doi: FieldCheck = field(default_factory=FieldCheck)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def checks(self) -> list[tuple[str, FieldCheck]]:
        return [("title", self.title), ("authors", self.authors),
                ("abstract", self.abstract), ("doi", self.doi)]

    def as_dict(self) -> dict:
        d: dict = {"state": self.state}
        if self.state == "found":
            rec = self.record or {}
            d.update({
                "matched_by": self.how,
                "id": rec.get("id", ""),
                "doi": rec.get("doi", ""),
                "title": self.title.as_dict(),
                "authors": self.authors.as_dict(),
                "abstract": self.abstract.as_dict(),
                "doi_check": self.doi.as_dict(),
            })
        if self.notes:
            d["notes"] = list(self.notes)
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class EntryResult:
    key: str
    file: str
    line: Optional[int]
    doi: str
    status: str = UNCHECKED
    sources: dict[str, SourceResult] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)   # human-readable lines

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "file": self.file,
            "line": self.line,
            "doi": self.doi,
            "status": self.status,
            "problems": list(self.problems),
            "sources": {s: r.as_dict() for s, r in self.sources.items()},
        }


# =============================================================================
# Reading the .bib side
# =============================================================================

def parse_bib_authors(author_field: str) -> tuple[list[Person], bool]:
    """Split a BibTeX author field into people. Returns (people, has_others)."""
    people: list[Person] = []
    has_others = False
    for raw in AUTHOR_SPLIT_RE.split((author_field or "").strip()):
        chunk = delatex(raw)
        if not chunk:
            continue
        if chunk.lower() in ("others", "et al", "et al."):
            has_others = True
            continue
        family = surname(chunk) or chunk
        people.append(Person(family=family, full=chunk))
    return people, has_others


def bib_info(entry) -> BibInfo:
    key = (getattr(entry, "key", "") or "").strip()
    authors, has_others = parse_bib_authors(entry_get(entry, "author"))
    doi = clean_doi(entry_get(entry, "doi"))
    if not doi:
        url = entry_get(entry, "url")
        if "doi.org/" in url.lower():
            doi = clean_doi(url)
    year_m = YEAR_RE.search(entry_get(entry, "year"))
    start_line = getattr(entry, "start_line", None)
    return BibInfo(
        key=key,
        entry_type=(getattr(entry, "entry_type", "") or "").lower(),
        title=delatex(entry_get(entry, "title")),
        authors=authors,
        has_others=has_others,
        abstract=delatex(entry_get(entry, "abstract")),
        doi=doi,
        year=year_m.group(1) if year_m else "",
        line=(start_line + 1) if isinstance(start_line, int) else None,
    )


def entry_tags(entry) -> set[str]:
    """Tags from the `comment` field, e.g. {TOP_IS_C, FT50, AIS_basket_11}."""
    return {t.strip() for t in entry_get(entry, "comment").split(",") if t.strip()}


# =============================================================================
# Reading the API side — both APIs are mapped onto the same small record
# =============================================================================
#
# record = {
#   "source": "crossref" | "openalex",
#   "id":     DOI or OpenAlex work id,
#   "doi":    bare lower-case DOI ("" if none),
#   "titles": [title, "title subtitle", ...]   (alternatives to compare with)
#   "authors": [{"family": ..., "full": ...}, ...],
#   "authors_truncated": bool,
#   "abstract": plain text ("" if none),
#   "year": "2018" | "",
# }

def crossref_record(msg: dict) -> dict:
    titles = [strip_markup(t) for t in (msg.get("title") or []) if t]
    subtitles = [strip_markup(t) for t in (msg.get("subtitle") or []) if t]
    alternatives = list(titles)
    if titles and subtitles:
        alternatives.append(f"{titles[0]} {subtitles[0]}")

    authors = []
    for a in msg.get("author") or []:
        family = a.get("family") or a.get("name") or ""
        full = " ".join(p for p in (a.get("given"), a.get("family")) if p) \
            or a.get("name") or ""
        if family or full:
            authors.append({"family": family or full, "full": full or family})

    year = ""
    try:
        year = str(msg["issued"]["date-parts"][0][0] or "")
    except (KeyError, IndexError, TypeError):
        pass

    doi = (msg.get("DOI") or "").lower()
    return {
        "source": "crossref",
        "id": doi,
        "doi": doi,
        "titles": alternatives,
        "authors": authors,
        "authors_truncated": False,
        "abstract": strip_markup(msg.get("abstract") or ""),
        "year": year,
    }


def openalex_abstract(inverted_index: Optional[dict]) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted_index:
        return ""
    slots: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions or []:
            slots[pos] = word
    return " ".join(slots[i] for i in sorted(slots))


def openalex_record(work: dict) -> dict:
    title = strip_markup(work.get("display_name") or work.get("title") or "")
    authors = []
    for au in work.get("authorships") or []:
        display = ((au.get("author") or {}).get("display_name") or "").strip()
        raw = (au.get("raw_author_name") or "").strip()
        name = display or raw
        if not name:
            continue
        family = surname(name) or name
        full = name if (not raw or raw == name) else f"{name} {raw}"
        authors.append({"family": family, "full": full})

    doi = clean_doi(work.get("doi") or "")
    return {
        "source": "openalex",
        "id": (work.get("id") or "").rsplit("/", 1)[-1],
        "doi": doi,
        "titles": [title] if title else [],
        "authors": authors,
        # List responses cut the author list at 100.
        "authors_truncated": bool(work.get("is_authors_truncated")) or len(authors) >= 100,
        "abstract": strip_markup(openalex_abstract(work.get("abstract_inverted_index"))),
        "year": str(work.get("publication_year") or ""),
    }


# =============================================================================
# On-disk cache
# =============================================================================

class Cache:
    """A JSON file mapping lookup keys to the (small) parsed API records."""

    MISS = object()

    def __init__(self, path: Optional[Path], ttl_days: float, refresh: bool) -> None:
        self.path = path
        self.ttl = ttl_days * 86400
        self.refresh = refresh
        self.data: dict[str, dict] = {}
        self.dirty = 0
        self.hits = 0
        if path and path.is_file():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                log.warning(f"ignoring unreadable cache {esc(str(path))}: {esc(str(e))}")
                self.data = {}

    def get(self, key: str):
        if self.path is None or self.refresh:
            return self.MISS
        item = self.data.get(key)
        if not item or (time.time() - item.get("t", 0)) > self.ttl:
            return self.MISS
        self.hits += 1
        return item.get("v")

    def put(self, key: str, value) -> None:
        if self.path is None:
            return
        self.data[key] = {"t": int(time.time()), "v": value}
        self.dirty += 1
        if self.dirty >= 50:
            self.save()

    def save(self) -> None:
        if self.path is None or not self.dirty:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = 0
        except OSError as e:
            log.warning(f"could not write cache {esc(str(self.path))}: {esc(str(e))}")


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(Path.home(), ".cache")
    return Path(base) / "verify-bib" / "cache.json"


# =============================================================================
# HTTP client (standard library only)
# =============================================================================

class SourceError(Exception):
    """A lookup failed (network, HTTP error, rate limit) — not a 'not found'."""


class SourceDenied(SourceError):
    """The API refused us (bad/missing key, allowance used up)."""


Fetch = Callable[[str, dict, float], tuple[int, dict, bytes]]


def urllib_fetch(url: str, headers: dict, timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return e.code, dict(e.headers.items()) if e.headers else {}, body


class ApiClient:
    """GET JSON with per-API throttling, retries, back-off and 429 handling."""

    def __init__(self, *, mailto: str = "", timeout: float = 30.0, retries: int = 3,
                 delay: Optional[float] = None, fetch: Fetch = urllib_fetch,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.timeout = timeout
        self.retries = retries
        self.fetch = fetch
        self.sleep = sleep
        self.requests = 0
        contact = f"; mailto:{mailto}" if mailto else ""
        self.headers = {
            "User-Agent": f"verify-bib/{VERSION} ({PROJECT_URL}{contact})",
            "Accept": "application/json",
        }
        # Minimum pause between two requests to the same API, in seconds.
        # Crossref (Dec 2025 limits): public pool 5 single-record and 1 list
        # request per second; polite pool is higher. OpenAlex: 100 req/s.
        if delay is not None:
            self.intervals = {(s, k): delay for s in SOURCES for k in ("one", "list")}
        else:
            self.intervals = {
                ("crossref", "one"): 0.15 if mailto else 0.25,
                ("crossref", "list"): 0.5 if mailto else 1.1,
                ("openalex", "one"): 0.05,
                ("openalex", "list"): 0.1,
            }
        self.fixed_delay = delay is not None
        self.next_ok: dict[str, float] = {}
        self.select_rejected: set[str] = set()   # APIs that refused `select`

    def _throttle(self, source: str, kind: str) -> None:
        now = time.monotonic()
        wait = self.next_ok.get(source, 0.0) - now
        if wait > 0:
            self.sleep(wait)
        self.next_ok[source] = time.monotonic() + self.intervals[(source, kind)]

    def _adapt(self, source: str, kind: str, headers: dict) -> None:
        """Crossref advertises its current limit in response headers — obey it."""
        if self.fixed_delay or source != "crossref":
            return
        h = {k.lower(): v for k, v in headers.items()}
        try:
            limit = float(h["x-rate-limit-limit"])
            interval = float(str(h["x-rate-limit-interval"]).rstrip("s"))
        except (KeyError, ValueError):
            return
        if limit > 0 and interval > 0:
            # Stay 20% under the advertised rate.
            self.intervals[(source, kind)] = max(0.02, 1.2 * interval / limit)

    def get_json(self, source: str, url: str, params: dict, *, kind: str) -> Optional[dict]:
        """Return the decoded JSON body, or None on HTTP 404."""
        params = dict(params)
        if source in self.select_rejected:
            params.pop("select", None)
        last = "no response"
        attempt = 0
        while attempt <= self.retries:
            full = url + ("?" + urllib.parse.urlencode(params) if params else "")
            self._throttle(source, kind)
            self.requests += 1
            try:
                status, headers, body = self.fetch(full, self.headers, self.timeout)
            except (urllib.error.URLError, http.client.HTTPException,
                    TimeoutError, OSError) as e:
                last = f"network error: {getattr(e, 'reason', e)}"
                attempt += 1
                if attempt <= self.retries:
                    self.sleep(min(2 ** attempt, 30))
                continue

            self._adapt(source, kind, headers)

            if status == 200:
                try:
                    return json.loads(body.decode("utf-8"))
                except ValueError as e:
                    raise SourceError(f"invalid JSON from {SOURCE_LABEL[source]}: {e}")
            if status == 404:
                return None
            if status in (400, 403) and "select" in params:
                # A field in `select` was not accepted (OpenAlex answers 403 to
                # invalid parameters): ask for full records from now on.
                log.debug(f"{SOURCE_LABEL[source]}: `select` rejected, retrying without it")
                self.select_rejected.add(source)
                params.pop("select")
                continue
            if status in (401, 403):
                raise SourceDenied(
                    f"HTTP {status} — access refused"
                    + (" (missing or invalid API key?)" if source == "openalex" else "")
                )
            if status == 429 or 500 <= status < 600:
                last = f"HTTP {status}" + (" (rate limited)" if status == 429 else "")
                attempt += 1
                if attempt <= self.retries:
                    h = {k.lower(): v for k, v in headers.items()}
                    try:
                        wait = float(h.get("retry-after", ""))
                    except ValueError:
                        wait = float(2 ** attempt)
                    self.sleep(min(max(wait, 1.0), 60.0))
                continue
            raise SourceError(f"HTTP {status}")

        if "429" in last:
            raise SourceDenied(f"{last} — rate limit or daily allowance exhausted")
        raise SourceError(last)


# =============================================================================
# Lookups
# =============================================================================

class Registry:
    """Looks works up in Crossref and OpenAlex, through the cache."""

    def __init__(self, client: ApiClient, cache: Cache, *, mailto: str = "",
                 openalex_key: str = "") -> None:
        self.client = client
        self.cache = cache
        self.mailto = mailto
        self.openalex_key = openalex_key

    # ---- single record by DOI ----------------------------------------------
    def by_doi(self, source: str, doi: str) -> Optional[dict]:
        ckey = f"{source}:doi:{doi}"
        cached = self.cache.get(ckey)
        if cached is not Cache.MISS:
            return cached

        if source == "crossref":
            url = f"{CROSSREF_API}/works/{urllib.parse.quote(doi, safe='/')}"
            params = {"mailto": self.mailto} if self.mailto else {}
            data = self.client.get_json(source, url, params, kind="one")
            rec = crossref_record(data.get("message") or {}) if data else None
        else:
            url = f"{OPENALEX_API}/works/doi:{urllib.parse.quote(doi, safe='/')}"
            params = {"select": OPENALEX_SELECT}
            if self.openalex_key:
                params["api_key"] = self.openalex_key
            data = self.client.get_json(source, url, params, kind="one")
            rec = openalex_record(data) if data else None

        self.cache.put(ckey, rec)
        return rec

    # ---- candidates by title -------------------------------------------------
    def by_title(self, source: str, bib: BibInfo) -> list[dict]:
        words = norm_text(bib.title)
        first = bib.authors[0].family if bib.authors else ""
        ckey = f"{source}:search:{words}|{norm_name(first)}"
        cached = self.cache.get(ckey)
        if cached is not Cache.MISS:
            return cached or []

        if source == "crossref":
            params = {
                "query.bibliographic": f"{bib.title} {first}".strip(),
                "rows": str(SEARCH_ROWS),
                "select": CROSSREF_SELECT,
            }
            if self.mailto:
                params["mailto"] = self.mailto
            data = self.client.get_json(source, f"{CROSSREF_API}/works", params, kind="list")
            items = ((data or {}).get("message") or {}).get("items") or []
            recs = [crossref_record(it) for it in items]
        else:
            # Plain words only: punctuation has a meaning in OpenAlex search syntax.
            params = {
                "search": words,
                "per_page": str(SEARCH_ROWS),
                "select": OPENALEX_SELECT,
            }
            if self.openalex_key:
                params["api_key"] = self.openalex_key
            data = self.client.get_json(source, f"{OPENALEX_API}/works", params, kind="list")
            recs = [openalex_record(w) for w in (data or {}).get("results") or []]

        self.cache.put(ckey, recs)
        return recs


# =============================================================================
# Comparison
# =============================================================================

def title_similarity(bib_title: str, rec: dict) -> float:
    a = norm_text(bib_title)
    return max((similarity(a, norm_text(t)) for t in rec.get("titles") or []),
               default=0.0)


def compare_title(bib: BibInfo, rec: dict, match_thr: float, near_thr: float) -> FieldCheck:
    titles = rec.get("titles") or []
    if not bib.title and not titles:
        return FieldCheck(F_NA)
    if not titles:
        return FieldCheck(F_MISSING_API)
    if not bib.title:
        return FieldCheck(F_MISSING_BIB, detail=f"title: {shorten(titles[0])}")

    a = norm_text(bib.title)
    score = title_similarity(bib.title, rec)
    if score >= match_thr:
        return FieldCheck(F_MATCH, score)

    theirs = shorten(titles[-1] if len(titles) > 1 else titles[0])
    for t in titles:
        b = norm_text(t)
        short, long_ = sorted((a, b), key=len)
        if len(short) >= 15 and long_.startswith(short):
            return FieldCheck(F_NEAR, score,
                              f"one title is the start of the other (subtitle?): “{theirs}”")
    if score >= near_thr:
        return FieldCheck(F_NEAR, score, f"title differs slightly: “{theirs}”")
    return FieldCheck(F_MISMATCH, score, f"title is “{theirs}”")


def same_person(bib_author: Person, api_author: Person) -> bool:
    """Same surname — tolerant of double surnames, particles and name order."""
    bk, ak = bib_author.key, api_author.key
    if not bk or not ak:
        return False
    if bk == ak:
        return True
    # 'Robles' vs 'Gregorio Robles Martínez'; 'Kang' vs 'Kang Lele'
    if bk in api_author.tokens or ak in bib_author.tokens:
        return True
    # 'van der Aalst' vs 'Aalst'
    return bk.endswith(ak) or ak.endswith(bk) if min(len(bk), len(ak)) >= 4 else False


def compare_authors(bib: BibInfo, rec: dict) -> FieldCheck:
    api = [Person(a.get("family", ""), a.get("full", "")) for a in rec.get("authors") or []]
    ours = bib.authors
    if not ours and not api:
        return FieldCheck(F_NA)
    if not api:
        return FieldCheck(F_MISSING_API)
    if not ours:
        return FieldCheck(F_MISSING_BIB,
                          detail="authors: " + shorten("; ".join(p.full for p in api)))

    # Greedy one-to-one matching, preferring the same position.
    free = list(range(len(api)))
    pairs: list[tuple[int, int]] = []
    unknown: list[Person] = []          # in the .bib, not in the source
    for i, person in enumerate(ours):
        j = i if (i in free and same_person(person, api[i])) else next(
            (k for k in free if same_person(person, api[k])), None)
        if j is None:
            unknown.append(person)
        else:
            free.remove(j)
            pairs.append((i, j))
    omitted = [api[k] for k in free]    # in the source, not in the .bib

    score = len(pairs) / max(len(ours), len(api))
    if unknown:
        detail = "not listed by the source: " + ", ".join(p.full for p in unknown)
        if omitted:
            detail += "; source also lists: " + ", ".join(p.full for p in omitted[:8])
            if len(omitted) > 8:
                detail += f" (+{len(omitted) - 8} more)"
        return FieldCheck(F_MISMATCH, score, detail)

    in_order = all(i == j for i, j in pairs)
    if omitted:
        if bib.has_others and in_order:
            return FieldCheck(F_MATCH, 1.0, f"“and others” stands for {len(omitted)} more author(s)")
        if rec.get("authors_truncated"):
            return FieldCheck(F_MATCH, 1.0)
        detail = (f".bib lists {len(ours)} author(s), source lists {len(api)}; missing: "
                  + ", ".join(p.full for p in omitted[:8]))
        if len(omitted) > 8:
            detail += f" (+{len(omitted) - 8} more)"
        return FieldCheck(F_MISMATCH, score, detail)
    if not in_order:
        return FieldCheck(F_NEAR, score, "same authors, different order: "
                          + ", ".join(p.family for p in api))
    return FieldCheck(F_MATCH, 1.0)


def abstract_words(text: str) -> list[str]:
    words = norm_text(text).split()
    return words[1:] if words[:1] == ["abstract"] else words


def compare_abstract(bib: BibInfo, rec: dict, match_thr: float) -> FieldCheck:
    ours = abstract_words(bib.abstract)
    theirs = abstract_words(rec.get("abstract") or "")
    if not ours and not theirs:
        return FieldCheck(F_NA)
    if not theirs:
        return FieldCheck(F_MISSING_API)
    if not ours:
        return FieldCheck(F_MISSING_BIB)
    if PARAPHRASE_RE.match(bib.abstract):
        return FieldCheck(F_PARAPHRASE)

    if ours == theirs:
        return FieldCheck(F_MATCH, 1.0)
    matcher = SequenceMatcher(None, ours, theirs, autojunk=False)
    score = matcher.ratio()
    if score >= match_thr:
        return FieldCheck(F_MATCH, score)
    # One side may simply be cut short: how much of the shorter one is covered?
    covered = sum(b.size for b in matcher.get_matching_blocks()) / min(len(ours), len(theirs))
    if covered >= match_thr:
        return FieldCheck(F_NEAR, score,
                          f"one abstract is a shortened version of the other "
                          f"({len(ours)} vs {len(theirs)} words)")
    return FieldCheck(F_MISMATCH, score,
                      f"abstract differs ({score:.0%} similar): “{shorten(rec.get('abstract', ''), 90)}”")


def compare_doi(bib: BibInfo, rec: dict) -> FieldCheck:
    theirs = rec.get("doi") or ""
    if not bib.doi and not theirs:
        return FieldCheck(F_NA)
    if not theirs:
        return FieldCheck(F_MISSING_API)
    if not bib.doi:
        return FieldCheck(F_MISSING_BIB, detail=f"DOI available: {theirs}")
    if bib.doi == theirs:
        return FieldCheck(F_MATCH, 1.0)
    return FieldCheck(F_NEAR, 0.0, f"DOI in .bib is {bib.doi}, source has {theirs}")


# =============================================================================
# Verifying one entry
# =============================================================================

@dataclass
class Settings:
    sources: tuple[str, ...] = SOURCES
    title_match: float = TITLE_MATCH
    title_near: float = TITLE_NEAR
    abstract_match: float = ABSTRACT_MATCH
    search: bool = True     # fall back to a title search


class Verifier:
    def __init__(self, registry: Registry, settings: Settings) -> None:
        self.registry = registry
        self.settings = settings
        self.failures = {s: 0 for s in SOURCES}
        self.disabled: dict[str, str] = {}      # source -> reason

    # ---- find the record ----------------------------------------------------
    def _best_candidate(self, source: str, bib: BibInfo) -> Optional[dict]:
        best, best_rank = None, (0.0, 0, 0)
        for rec in self.registry.by_title(source, bib):
            score = title_similarity(bib.title, rec)
            first_ok = int(bool(bib.authors and rec.get("authors") and same_person(
                bib.authors[0], Person(rec["authors"][0].get("family", ""),
                                       rec["authors"][0].get("full", "")))))
            year_ok = int(bool(bib.year and rec.get("year") == bib.year))
            rank = (round(score, 2), first_ok, year_ok)
            if rank > best_rank:
                best, best_rank = rec, rank
        if best is not None and best_rank[0] >= self.settings.title_near:
            return best
        return None

    def _resolve(self, source: str, bib: BibInfo, result: SourceResult) -> Optional[dict]:
        by_doi = None
        if bib.doi:
            by_doi = self.registry.by_doi(source, bib.doi)
            if by_doi and title_similarity(bib.title, by_doi) >= self.settings.title_near:
                result.how = "doi"
                return by_doi

        if self.settings.search and bib.title:
            found = self._best_candidate(source, bib)
            if found and by_doi is not None and found.get("id") == by_doi.get("id"):
                # The search led back to the DOI's own record.
                result.how = "doi"
                return by_doi
            if found:
                result.how = "search"
                if bib.doi and by_doi is None:
                    result.notes.append(
                        f"DOI {bib.doi} is not in {SOURCE_LABEL[source]}; matched by title instead")
                elif bib.doi and by_doi is not None:
                    result.notes.append(
                        f"DOI {bib.doi} belongs to another work in {SOURCE_LABEL[source]} "
                        f"(“{shorten((by_doi.get('titles') or ['?'])[0], 70)}”); matched by title instead")
                return found

        if by_doi is not None:
            # The DOI exists but points at something with a different title.
            result.how = "doi"
            return by_doi
        return None

    # ---- one source ---------------------------------------------------------
    def check_source(self, source: str, bib: BibInfo) -> SourceResult:
        result = SourceResult(source=source)
        if source in self.disabled:
            result.state = "skipped"
            result.error = self.disabled[source]
            return result

        try:
            rec = self._resolve(source, bib, result)
        except SourceDenied as e:
            self.disabled[source] = str(e)
            log.error(f"{SOURCE_LABEL[source]} switched off for the rest of the run: {esc(str(e))}")
            result.state, result.error = "error", str(e)
            return result
        except SourceError as e:
            self.failures[source] += 1
            result.state, result.error = "error", str(e)
            log.warning(f"[cyan]{esc(bib.key)}[/] {SOURCE_LABEL[source]} lookup failed: {esc(str(e))}")
            if self.failures[source] >= MAX_SOURCE_FAILURES:
                self.disabled[source] = f"{MAX_SOURCE_FAILURES} lookups in a row failed ({e})"
                log.error(f"{SOURCE_LABEL[source]} switched off for the rest of the run: "
                          f"{esc(self.disabled[source])}")
            return result
        self.failures[source] = 0

        if rec is None:
            result.state = "not_found"
            return result

        s = self.settings
        result.state = "found"
        result.record = rec
        result.title = compare_title(bib, rec, s.title_match, s.title_near)
        result.authors = compare_authors(bib, rec)
        result.abstract = compare_abstract(bib, rec, s.abstract_match)
        result.doi = compare_doi(bib, rec)
        return result

    # ---- whole entry --------------------------------------------------------
    def check_entry(self, entry, file: str) -> EntryResult:
        bib = bib_info(entry)
        out = EntryResult(key=bib.key, file=file, line=bib.line, doi=bib.doi)

        if not bib.title:
            out.status = UNCHECKED
            out.problems.append("entry has no title — nothing to verify")
            return out

        for source in self.settings.sources:
            out.sources[source] = self.check_source(source, bib)

        found = [r for r in out.sources.values() if r.state == "found"]
        missing = [r for r in out.sources.values() if r.state == "not_found"]
        failed = [r for r in out.sources.values() if r.state in ("error", "skipped")]

        # Findings, with identical ones from both sources reported once.
        findings: dict[tuple[str, str], list[str]] = {}
        for r in out.sources.values():
            label = SOURCE_LABEL[r.source]
            for name, chk in r.checks():
                if chk.status in (F_MISMATCH, F_NEAR) and chk.detail:
                    findings.setdefault((name, chk.detail), []).append(label)
        for (name, detail), labels in findings.items():
            out.problems.append(f"{' + '.join(labels)} {name}: {detail}")
        for r in out.sources.values():
            label = SOURCE_LABEL[r.source]
            out.problems.extend(f"{label}: {n}" for n in r.notes)
            if r.state == "error":
                out.problems.append(f"{label}: lookup failed ({r.error})")

        if found:
            statuses = [chk.status for r in found for _, chk in r.checks()]
            if F_MISMATCH in statuses:
                out.status = MISMATCH
            elif F_NEAR in statuses or any(r.notes for r in found):
                out.status = WARN
            else:
                out.status = VERIFIED
            for r in missing:
                out.problems.append(f"not in {SOURCE_LABEL[r.source]}")
        elif missing and not failed:
            out.status = NOT_FOUND
            where = " or ".join(SOURCE_LABEL[r.source] for r in missing)
            out.problems.append(
                f"not found in {where}" + (f" (DOI {bib.doi} does not resolve there)" if bib.doi else ""))
        else:
            out.status = UNCHECKED
            for r in missing:
                out.problems.append(f"not in {SOURCE_LABEL[r.source]}")
        return out


# =============================================================================
# Display
# =============================================================================

def source_cell(r: Optional[SourceResult]) -> Text:
    if r is None:
        return Text("—", style="dim")
    if r.state == "not_found":
        return Text("not found", style="magenta")
    if r.state == "error":
        return Text("error", style="red")
    if r.state == "skipped":
        return Text("skipped", style="dim")
    cell = Text()
    cell.append("doi " if r.how == "doi" else "search ", style="dim")
    for label, chk in (("T", r.title), ("Au", r.authors), ("Ab", r.abstract), ("D", r.doi)):
        mark, style = F_MARK[chk.status]
        cell.append(f"{label}{mark} ", style=style)
    return cell


def show_report(results: list[EntryResult], sources: tuple[str, ...], title: str,
                problems_only: bool) -> None:
    table = Table(title=title, show_lines=False, header_style="bold",
                  caption="T title · Au authors · Ab abstract · D doi   "
                          "✓ match  ≈ near  ✗ mismatch  + only upstream  ~ paraphrase  – not comparable")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Status", width=10)
    table.add_column("Key", style="cyan", overflow="fold", min_width=14)
    for s in sources:
        table.add_column(SOURCE_LABEL[s], no_wrap=True)
    table.add_column("Notes", style="yellow", overflow="fold", ratio=1)

    shown = 0
    for i, res in enumerate(results, 1):
        if problems_only and res.status == VERIFIED:
            continue
        shown += 1
        table.add_row(
            str(i),
            Text(res.status, style=STATUS_STYLE[res.status]),
            Text(res.key or "[empty]"),
            *[source_cell(res.sources.get(s)) for s in sources],
            Text("\n".join(res.problems) or "—"),
        )
    console.print()
    if shown:
        console.print(table)
    else:
        console.print(f"[green]{esc(title)}: every entry verified.[/]")


def count_statuses(results: list[EntryResult]) -> dict[str, int]:
    counts = {s: 0 for s in STATUS_ORDER}
    for r in results:
        counts[r.status] += 1
    return counts


def count_field_mismatches(results: list[EntryResult]) -> dict[str, int]:
    """Entries with a mismatch on each field, in at least one source."""
    counts = {"title": 0, "authors": 0, "abstract": 0}
    for res in results:
        for name in counts:
            if any(getattr(r, name).status == F_MISMATCH
                   for r in res.sources.values() if r.state == "found"):
                counts[name] += 1
    return counts


def markdown_report(per_file: list[tuple[str, list[EntryResult]]],
                    sources: tuple[str, ...]) -> str:
    """Markdown for --report and for the GitHub Actions step summary."""
    everything = [r for _, rs in per_file for r in rs]
    counts = count_statuses(everything)
    lines = [
        "## verify-bib\n\n",
        f"Checked {len(everything)} entries in {len(per_file)} file(s) against "
        f"{' and '.join(SOURCE_LABEL[s] for s in sources)}.\n\n",
        "| Status | Entries |\n|---|---:|\n",
    ]
    lines += [f"| {s} | {counts[s]} |\n" for s in STATUS_ORDER]
    by_field = count_field_mismatches(everything)
    lines.append("\nMismatches by field: "
                 + ", ".join(f"{name} {n}" for name, n in by_field.items()) + ".\n\n")

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    for path, results in per_file:
        problems = [r for r in results if r.status != VERIFIED]
        if not problems:
            continue
        lines.append(f"### `{path}`\n\n| Key | Status | Details |\n|---|---|---|\n")
        for r in problems:
            lines.append(f"| `{r.key}` | {r.status} | "
                         f"{cell('; '.join(r.problems)) or '—'} |\n")
        lines.append("\n")
    return "".join(lines)


# =============================================================================
# GitHub Actions helpers (same as fix-bib.py)
# =============================================================================

def gha_annotate(level: str, message: str,
                 *, file: Optional[str] = None,
                 line: Optional[int] = None) -> None:
    loc = ""
    if file:
        loc += f" file={file}"
    if line:
        loc += f",line={line}"
    msg = (message
           .replace("%", "%25")
           .replace("\r", "%0D")
           .replace("\n", "%0A"))
    print(f"::{level}{loc}::{msg}", file=sys.stdout, flush=True)


def write_gha_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown)
    except OSError as e:
        log.warning(f"could not write GITHUB_STEP_SUMMARY: {esc(str(e))}")


# =============================================================================
# Per-file processing
# =============================================================================

def select_entries(library, *, only: set[str], tags: set[str],
                   skip_types: set[str]) -> list:
    entries = list(getattr(library, "entries", []) or [])
    if only:
        entries = [e for e in entries if (getattr(e, "key", "") or "") in only]
    if tags:
        entries = [e for e in entries if tags & entry_tags(e)]
    if skip_types:
        entries = [e for e in entries
                   if (getattr(e, "entry_type", "") or "").lower() not in skip_types]
    return entries


def process_file(bibfile: Path, verifier: Verifier, *, only: set[str], tags: set[str],
                 skip_types: set[str], budget: Optional[list[int]], gha: bool,
                 problems_only: bool) -> dict:
    """Verify one .bib file. `budget` is a one-item list holding the number of
    entries still allowed by --limit (shared across files)."""
    result: dict = {"path": str(bibfile), "entries": [], "fatal": None}

    try:
        raw_text = bibfile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["fatal"] = f"cannot read: {e}"
        log.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")
        return result

    try:
        library = bibtexparser.parse_string(raw_text)
    except Exception as e:
        result["fatal"] = f"parse failure: {e}"
        log.error(f"[cyan]{esc(str(bibfile))}[/] parse failure: {esc(str(e))}")
        return result

    failed_blocks = list(getattr(library, "failed_blocks", []) or [])
    if failed_blocks:
        log.warning(f"[cyan]{esc(str(bibfile))}[/] {len(failed_blocks)} block(s) are broken "
                    f"(syntax error or duplicate key) and are not verified — run fix-bib.py first")

    entries = select_entries(library, only=only, tags=tags, skip_types=skip_types)
    if budget is not None:
        entries = entries[: max(budget[0], 0)]
        budget[0] -= len(entries)
    if not entries:
        log.info(f"[cyan]{esc(str(bibfile))}[/]: no entries to verify.")
        return result

    results: list[EntryResult] = []
    progress = Progress(
        TextColumn("[bold blue]{task.description}"), BarColumn(),
        MofNCompleteColumn(), TimeElapsedColumn(),
        console=console, transient=True, disable=not console.is_terminal,
    )
    with progress:
        task = progress.add_task(bibfile.name, total=len(entries))
        for n, entry in enumerate(entries, 1):
            res = verifier.check_entry(entry, str(bibfile))
            results.append(res)
            progress.advance(task)
            log.debug(f"[cyan]{esc(res.key)}[/] → {res.status}")
            if not console.is_terminal and n % 100 == 0:
                log.info(f"{esc(bibfile.name)}: {n}/{len(entries)} entries checked")

    for res in results:
        if res.status == VERIFIED:
            continue
        message = f"{res.key}: {res.status} — " + ("; ".join(res.problems) or "see report")
        if gha:
            level = "error" if res.status == MISMATCH else "warning"
            gha_annotate(level, message, file=str(bibfile), line=res.line)

    show_report(results, verifier.settings.sources,
                title=f"Verification report — {bibfile.name}",
                problems_only=problems_only)
    result["entries"] = results
    return result


# =============================================================================
# Main
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify-bib",
        description="Verify the title, authors and abstract of BibTeX entries "
                    "against Crossref and OpenAlex.",
    )
    parser.add_argument("bibfiles", nargs="+", type=Path,
                        help="One or more .bib files (or directories with --recursive)")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="Recurse into directories looking for *.bib")
    parser.add_argument("--source", choices=("both",) + SOURCES, default="both",
                        help="Which registry to check against (default: both)")
    parser.add_argument("--mailto", default=os.environ.get("CROSSREF_MAILTO", ""),
                        help="Contact e-mail sent to Crossref for its polite pool "
                             "(default: $CROSSREF_MAILTO)")
    parser.add_argument("--openalex-key", default=os.environ.get("OPENALEX_API_KEY", ""),
                        help="OpenAlex API key (default: $OPENALEX_API_KEY)")
    parser.add_argument("--no-search", action="store_true",
                        help="Only look entries up by DOI; never fall back to a title search")

    sel = parser.add_argument_group("entry selection")
    sel.add_argument("--only", nargs="+", metavar="KEY", default=[],
                     help="Verify only the entries with these keys")
    sel.add_argument("--tag", nargs="+", metavar="TAG", default=[],
                     help="Verify only entries whose comment field has one of these tags "
                          "(e.g. TOP_IS_C, generated_by_claude_sonnet_5.5)")
    sel.add_argument("--skip-types", nargs="+", metavar="TYPE", default=[],
                     help="Entry types to leave out (e.g. misc online software)")
    sel.add_argument("--limit", type=int, metavar="N",
                     help="Stop after N entries (handy for a first try)")

    thr = parser.add_argument_group("matching")
    thr.add_argument("--title-threshold", type=float, default=TITLE_MATCH,
                     help=f"Title similarity counted as a match (default {TITLE_MATCH})")
    thr.add_argument("--near-threshold", type=float, default=TITLE_NEAR,
                     help="Title similarity counted as a near match, and the minimum for "
                          f"accepting a title-search result (default {TITLE_NEAR})")
    thr.add_argument("--abstract-threshold", type=float, default=ABSTRACT_MATCH,
                     help=f"Abstract similarity counted as a match (default {ABSTRACT_MATCH})")

    net = parser.add_argument_group("network and cache")
    net.add_argument("--cache-file", type=Path, default=None,
                     help="Cache file (default ~/.cache/verify-bib/cache.json)")
    net.add_argument("--no-cache", action="store_true", help="Do not read or write the cache")
    net.add_argument("--refresh", action="store_true",
                     help="Ignore cached answers and query the APIs again")
    net.add_argument("--cache-days", type=float, default=CACHE_TTL_DAYS,
                     help=f"Days a cached answer stays valid (default {CACHE_TTL_DAYS})")
    net.add_argument("--delay", type=float, default=None, metavar="SECONDS",
                     help="Pause between requests to the same API "
                          "(default: automatic, following each API's limits)")
    net.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    net.add_argument("--retries", type=int, default=3,
                     help="Retries per request on network errors, 429 and 5xx (default 3)")

    out = parser.add_argument_group("output")
    out.add_argument("--problems-only", "-p", action="store_true",
                     help="Leave VERIFIED entries out of the tables")
    out.add_argument("--report", type=Path, metavar="FILE.md",
                     help="Also write a Markdown report to this file")
    out.add_argument("--check", action="store_true",
                     help="Exit 2 if any entry is MISMATCH or NOT_FOUND")
    out.add_argument("--strict", action="store_true",
                     help="Treat warnings (WARN, UNCHECKED) as errors (exit 1)")
    out.add_argument("--json", action="store_true",
                     help="Emit machine-readable JSON to stdout (tables go to stderr)")
    out.add_argument("--gha", action="store_true",
                     help="Emit GitHub Actions annotations and write $GITHUB_STEP_SUMMARY")
    out.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return parser


def main(argv: Optional[list[str]] = None, *, fetch: Fetch = urllib_fetch) -> int:
    args = build_parser().parse_args(argv)

    global console
    console = Console(stderr=args.json, no_color=not IS_TTY, highlight=False)
    setup_logging(args.verbose, output_console=console)

    for name in ("title_threshold", "near_threshold", "abstract_threshold"):
        if not 0.0 < getattr(args, name) <= 1.0:
            log.error(f"--{name.replace('_', '-')} must be between 0 and 1.")
            return 1

    # --- Expand input paths --------------------------------------------------
    files: list[Path] = []
    for p in args.bibfiles:
        if p.is_dir():
            if not args.recursive:
                log.error(f"[cyan]{esc(str(p))}[/] is a directory; use --recursive")
                return 1
            files.extend(sorted(p.rglob("*.bib")))
        else:
            files.append(p)

    if not files:
        log.error("No .bib files found.")
        return 1
    for f in files:
        if f.suffix.lower() != ".bib":
            log.error(f"not a .bib file: [cyan]{esc(str(f))}[/]")
            return 1
        if not f.is_file():
            log.error(f"not a file: [cyan]{esc(str(f))}[/]")
            return 1

    sources = SOURCES if args.source == "both" else (args.source,)

    console.print()
    console.print(Panel(
        f"[bold]Files:[/] [cyan]{len(files)}[/]\n"
        f"[bold]Sources:[/] {', '.join(SOURCE_LABEL[s] for s in sources)}\n"
        f"[bold]Checks:[/] title, authors, abstract, DOI",
        title="[bold blue]verify-bib[/]",
        expand=False,
    ))

    if "crossref" in sources and not args.mailto:
        log.info("No --mailto / $CROSSREF_MAILTO: using Crossref's slower public pool.")
    if "openalex" in sources and not args.openalex_key:
        log.warning("No --openalex-key / $OPENALEX_API_KEY: OpenAlex only allows a handful of "
                    "calls without a key (free key: https://openalex.org/settings/api).")

    cache_path = None if args.no_cache else (args.cache_file or default_cache_path())
    cache = Cache(cache_path, args.cache_days, args.refresh)
    client = ApiClient(mailto=args.mailto, timeout=args.timeout, retries=args.retries,
                       delay=args.delay, fetch=fetch)
    registry = Registry(client, cache, mailto=args.mailto, openalex_key=args.openalex_key)
    verifier = Verifier(registry, Settings(
        sources=sources,
        title_match=args.title_threshold,
        title_near=min(args.near_threshold, args.title_threshold),
        abstract_match=args.abstract_threshold,
        search=not args.no_search,
    ))

    # --- Process each file ---------------------------------------------------
    budget = [args.limit] if args.limit is not None else None
    file_results: list[dict] = []
    interrupted = False
    try:
        for bibfile in files:
            if budget is not None and budget[0] <= 0:
                break
            console.rule(f"[bold]Verifying[/] [cyan]{esc(str(bibfile))}[/]")
            file_results.append(process_file(
                bibfile, verifier,
                only=set(args.only), tags=set(args.tag),
                skip_types={t.lower().lstrip("@") for t in args.skip_types},
                budget=budget, gha=args.gha, problems_only=args.problems_only,
            ))
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted — answers received so far are kept in the cache.")
    finally:
        cache.save()

    # --- Aggregate -----------------------------------------------------------
    per_file = [(r["path"], r["entries"]) for r in file_results]
    everything = [e for _, es in per_file for e in es]
    counts = count_statuses(everything)
    any_fatal = any(r["fatal"] for r in file_results)

    summary = Table(title="Summary", header_style="bold", show_lines=False)
    summary.add_column("Status")
    summary.add_column("Entries", justify="right")
    for s in STATUS_ORDER:
        summary.add_row(Text(s, style=STATUS_STYLE[s]), str(counts[s]))
    console.print()
    console.print(summary)
    by_field = count_field_mismatches(everything)
    if any(by_field.values()):
        log.info("Mismatches by field: "
                 + ", ".join(f"{name} {n}" for name, n in by_field.items()))
    log.info(f"{len(everything)} entries, {client.requests} API request(s), "
             f"{cache.hits} answer(s) from cache.")
    for source, reason in verifier.disabled.items():
        log.warning(f"{SOURCE_LABEL[source]} was switched off part-way: {esc(reason)}. "
                    f"Re-run later; cached answers are reused.")

    # --- Reports -------------------------------------------------------------
    report_md = markdown_report(per_file, sources)
    if args.report:
        try:
            args.report.write_text(report_md, encoding="utf-8")
            log.info(f"Report written to [cyan]{esc(str(args.report))}[/]")
        except OSError as e:
            log.error(f"could not write report: {esc(str(e))}")
            any_fatal = True
    if args.gha:
        write_gha_summary(report_md)

    if args.json:
        payload = {
            "sources": list(sources),
            "counts": counts,
            "field_mismatches": count_field_mismatches(everything),
            "files": [
                {"path": r["path"], "fatal": r["fatal"],
                 "entries": [e.as_dict() for e in r["entries"]]}
                for r in file_results
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    # --- Exit code -----------------------------------------------------------
    if any_fatal or interrupted:
        return 1
    if args.strict and (counts[WARN] or counts[UNCHECKED]):
        log.error(f"--strict: {counts[WARN] + counts[UNCHECKED]} warning(s) → failing.")
        return 1
    if args.check and (counts[MISMATCH] or counts[NOT_FOUND]):
        log.warning(f"--check: {counts[MISMATCH]} mismatch(es), {counts[NOT_FOUND]} not found.")
        return 2
    if args.check:
        log.info("[green]All entries verified.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
