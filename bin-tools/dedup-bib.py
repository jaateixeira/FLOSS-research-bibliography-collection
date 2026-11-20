#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dedup-bib.py — Find and remove duplicate entries in BibTeX files.

Two entries are the same work when ANY of these holds:

  key     they have the same citation key (case-insensitive) and the same work
          behind it. Entries that share a key but are *different* works are
          never merged: they are reported as a COLLISION to fix by hand.
  id      they have the same DOI (or arXiv id) and similar titles.
  title   the normalised titles match (LaTeX, case, accents, punctuation and
          spacing are ignored) AND the year is the same AND the authors are
          compatible AND the DOIs do not contradict each other. Editorials,
          "Introduction" pieces and book reviews share titles but have their
          own DOIs and years, so they are left alone.

For every group of duplicates one entry is kept (the most complete one, or the
first with --keep first) and the others are removed. Before they go, anything
the kept entry lacks is copied over from them (an abstract, a DOI, a URL ...)
and their `comment` tags are added to the kept entry's `comment` field, so tags
such as TOP_IS_C or FT50 are never lost. Fields that exist in both entries with
different values are not touched: the kept entry wins and the conflict is shown.

Entries that bibtexparser drops as "duplicate key" blocks (the second copy of
a key) are recovered and examined too.

Safe by default:
  * nothing is written unless you pass --apply (edit in place, with a .bak
    backup) or -o FILE (write a cleaned copy and leave the original alone);
  * the file is edited as text: entries that are not duplicates keep their
    exact formatting, so the diff contains only what changed;
  * the result is re-parsed and checked before it replaces anything;
  * --key-map FILE lists every removed key and the key that replaced it, so
    \\cite commands in your LaTeX sources can be updated.

Other files are never touched. With --across-files, duplicates between the
given files are reported (never removed): topical .bib files overlap on purpose.

Uses bibtexparser v2 (>=2.0.0b1,<3.0), unidecode and Rich — the same
dependencies as fix-bib.py and gen-bib-keys.py — plus the standard library.

Exit codes:
  0  success
  1  error (unreadable file, failed safety check, write failure)
  2  --check and at least one duplicate group or key collision was found
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import bibtexparser
from bibtexparser.model import Entry
from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from unidecode import unidecode


# =============================================================================
# Constants
# =============================================================================

VERSION = "1.0.0"

TITLE_THRESHOLD = 0.93     # normalised-title similarity that counts as "same title"
SAME_WORK_FLOOR = 0.80     # looser bar when a key or DOI already ties two entries
MIN_TITLE_WORDS = 3        # shorter titles ("Editorial") never match on title alone
MAX_BUCKET = 400           # skip absurdly large candidate buckets

AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+", re.IGNORECASE)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>{}]+", re.IGNORECASE)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)
YEAR_RE = re.compile(r"(\d{4})")
MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
GENERIC_TYPES = {"misc", "unpublished", "online", "electronic", "techreport"}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}

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
        self.console.print(f"[{style}]{label}[/] {message}")

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
# Text normalisation
# =============================================================================

_LATEX_ACCENT_RE = re.compile(
    r"""\\(?:[`'^"~=.]|[uvHcdbkrt](?=[\s{\\]))\s*\{?\s*\\?([A-Za-z])\s*\}?"""
)
_LATEX_LETTER_RE = re.compile(r"\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L|i|j)(?![A-Za-z])")
_LATEX_LETTERS = {"ss": "ss", "ae": "ae", "AE": "AE", "oe": "oe", "OE": "OE", "aa": "a",
                  "AA": "A", "o": "o", "O": "O", "l": "l", "L": "L", "i": "i", "j": "j"}
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
    s = _LATEX_COMMAND_RE.sub(" ", s)
    s = s.replace("{", "").replace("}", "").replace("$", "")
    return re.sub(r"\s+", " ", s).strip()


def norm_text(s: str) -> str:
    """Lower-case ASCII words separated by single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", unidecode(s or "").lower()).strip()


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", unidecode(s or "").lower())


def surname(name: str) -> str:
    """Family name following BibTeX's rules: 'Last, First' or 'First von Last'."""
    name = (name or "").strip()
    if "," in name:
        return name.split(",", 1)[0].strip()
    parts = name.split()
    while len(parts) > 1 and parts[-1].lower().strip(".") in NAME_SUFFIXES:
        parts.pop()
    if not parts:
        return ""
    start = len(parts) - 1
    while start > 1 and parts[start - 1][:1].islower():     # von, van der, de la ...
        start -= 1
    return " ".join(parts[start:])


def clean_doi(raw: str) -> str:
    m = DOI_RE.search(delatex(raw or "").replace("\\", ""))
    return m.group(0).rstrip(".,;)]").lower() if m else ""


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def shorten(s: str, width: int = 60) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= width else s[: width - 1] + "…"


def strip_delimiters(value: str) -> str:
    """Remove one pair of outer {} or "" from a value, if it is a single group.

    bibtexparser keeps the delimiters on the fields of a duplicate-key block
    (but not on normal entries), so those values must be unwrapped once."""
    v = value.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"' and '"' not in v[1:-1]:
        return v[1:-1]
    if len(v) >= 2 and v[0] == "{" and v[-1] == "}":
        depth = 0
        for i, c in enumerate(v):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and i < len(v) - 1:
                    return v        # the first group closes early: not one group
        return v[1:-1]
    return v


# =============================================================================
# Data model
# =============================================================================

@dataclass(eq=False)
class Item:
    """One entry found in a file (including second copies of a key)."""
    file: str
    order: int                      # position in the file, 0-based
    key: str
    entry_type: str
    fields: dict[str, str]          # lower-case name -> clean value, in file order
    names: dict[str, str]           # lower-case name -> name as written
    raw: str
    start_line: int                 # 0-based line of the '@'
    from_failed: bool = False       # a duplicate-key block recovered from failed_blocks
    start: int = -1                 # character span of `raw` in the file text
    end: int = -1
    cut_start: int = -1             # start of its line, when only blanks precede it

    # derived (see finish())
    title: str = ""
    title_key: str = ""
    doi: str = ""
    ids: frozenset = frozenset()
    year: str = ""
    surnames: tuple = ()
    tags: tuple = ()

    @property
    def line(self) -> int:
        return self.start_line + 1

    @property
    def label(self) -> str:
        return f"{Path(self.file).name}:{self.key}"

    def finish(self) -> "Item":
        f = self.fields
        self.title = delatex(f.get("title", ""))
        self.title_key = norm_text(self.title)
        doi = clean_doi(f.get("doi", ""))
        if not doi and "doi.org/" in f.get("url", "").lower():
            doi = clean_doi(f["url"])
        self.doi = doi
        ids = set()
        if doi:
            m = ARXIV_DOI_RE.match(doi)
            ids.add(f"arxiv:{m.group(1).lower()}" if m else f"doi:{doi}")
        prefix = f.get("archiveprefix", f.get("eprinttype", "")).strip().lower()
        eprint = f.get("eprint", "").strip().lower()
        if eprint and (prefix == "arxiv" or re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", eprint)):
            ids.add("arxiv:" + re.sub(r"v\d+$", "", eprint))
        self.ids = frozenset(ids)
        m = YEAR_RE.search(f.get("year", ""))
        self.year = m.group(1) if m else ""
        self.surnames = tuple(author_surnames(f.get("author") or f.get("editor") or ""))
        self.tags = tuple(split_tags(f.get("comment", "")))
        return self


def author_surnames(value: str) -> list[str]:
    out = []
    for raw in AUTHOR_SPLIT_RE.split((value or "").strip()):
        chunk = delatex(raw)
        if not chunk or chunk.lower() in ("others", "et al", "et al."):
            continue
        n = norm_name(surname(chunk) or chunk)
        if n:
            out.append(n)
    return out


def split_tags(value: str) -> list[str]:
    seen, out = set(), []
    for t in (value or "").split(","):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


@dataclass
class Match:
    a: Item
    b: Item
    reasons: set[str]


@dataclass
class Review:
    """Something that needs a human look; never changed by the tool."""
    items: list[Item]
    why: str
    collision: bool = False         # same key, different works: breaks LaTeX


@dataclass
class Conflict:
    field: str
    kept: str
    dropped: str
    dropped_key: str


@dataclass
class Cluster:
    primary: Item
    removed: list[Item]
    reasons: list[str]
    filled: dict[str, str] = field(default_factory=dict)       # field -> donor key
    tags_added: list[str] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    final_fields: dict[str, str] = field(default_factory=dict)
    final_names: dict[str, str] = field(default_factory=dict)
    # edits to the kept entry's text
    replace: dict[str, str] = field(default_factory=dict)       # existing field -> new value
    add: list[tuple[str, str]] = field(default_factory=list)    # (name, value) to append

    @property
    def changes_primary(self) -> bool:
        return bool(self.replace or self.add)

    def as_dict(self) -> dict:
        return {
            "kept": self.primary.key,
            "kept_line": self.primary.line,
            "removed": [{"key": r.key, "line": r.line} for r in self.removed],
            "match": self.reasons,
            "filled_fields": dict(self.filled),
            "tags_added": list(self.tags_added),
            "conflicts": [c.__dict__ for c in self.conflicts],
        }


@dataclass
class FileResult:
    path: str
    entries: int = 0
    clusters: list[Cluster] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    survivors: list[Item] = field(default_factory=list)
    skipped_blocks: int = 0
    written: str = ""               # path written, if any
    fatal: Optional[str] = None

    @property
    def removed(self) -> int:
        return sum(len(c.removed) for c in self.clusters)

    @property
    def collisions(self) -> int:
        return sum(1 for r in self.reviews if r.collision)


# =============================================================================
# Reading a file
# =============================================================================

def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:      # keep CRLF as is
        return f.read()


def line_offsets(text: str) -> list[int]:
    offsets, pos = [], 0
    for line in text.splitlines(keepends=True):
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)
    return offsets


def make_item(entry: Entry, *, file: str, raw: str, start_line: int, from_failed: bool) -> Item:
    fields: dict[str, str] = {}
    names: dict[str, str] = {}
    for fld in entry.fields:
        low = fld.key.lower()
        value = str(fld.value)
        if from_failed:
            value = strip_delimiters(value)
        fields[low] = value
        names.setdefault(low, fld.key)
    return Item(file=file, order=0, key=(entry.key or "").strip(),
                entry_type=(entry.entry_type or "").lower(), fields=fields, names=names,
                raw=raw, start_line=start_line, from_failed=from_failed)


def load_items(text: str, file: str) -> tuple[list[Item], int, Optional[str]]:
    """Return (items in file order, number of blocks left unexamined, error)."""
    try:
        library = bibtexparser.parse_string(text)
    except Exception as e:                                  # pragma: no cover - defensive
        return [], 0, f"parse failure: {e}"

    items: list[Item] = []
    for entry in library.entries:
        items.append(make_item(entry, file=file, raw=entry.raw,
                               start_line=entry.start_line, from_failed=False))
    skipped = 0
    for block in library.failed_blocks:
        inner = getattr(block, "ignore_error_block", None)
        if type(block).__name__ == "DuplicateBlockKeyBlock" and isinstance(inner, Entry):
            items.append(make_item(inner, file=file, raw=block.raw,
                                   start_line=block.start_line, from_failed=True))
        else:
            skipped += 1
            log.warning(f"[cyan]{esc(Path(file).name)}[/] line {getattr(block, 'start_line', 0) + 1}: "
                        f"block not examined ({type(block).__name__})")

    items.sort(key=lambda i: i.start_line)
    offsets = line_offsets(text)
    for n, it in enumerate(items):
        it.order = n
        line_start = offsets[it.start_line] if it.start_line < len(offsets) else -1
        pos = text.find(it.raw, line_start) if line_start >= 0 else -1
        if pos >= 0 and text[line_start:pos].strip() == "":
            it.start, it.end, it.cut_start = pos, pos + len(it.raw), line_start
        else:
            it.start = it.end = -1
        if it.start < 0:
            return [], 0, (f"cannot locate entry '{it.key}' (line {it.line}) in the file text; "
                           f"run fix-bib.py first")
        it.finish()
    return items, skipped, None


# =============================================================================
# Finding duplicates
# =============================================================================

def title_similarity(a: Item, b: Item) -> float:
    return similarity(a.title_key, b.title_key)


def authors_compatible(a: Item, b: Item) -> bool:
    sa, sb = a.surnames, b.surnames
    if not sa or not sb:
        return True                                  # nothing to contradict
    if sa[0] == sb[0]:
        return True
    union = set(sa) | set(sb)
    return len(set(sa) & set(sb)) / len(union) >= 0.5


def classify(a: Item, b: Item, *, thr: float, use_key: bool) -> Optional[tuple[str, set[str], str]]:
    """Judge a pair. Returns (kind, reasons, note) with kind 'dup', 'review' or
    'collision'; None when the two are different works."""
    same_key = use_key and a.key.lower() == b.key.lower()
    same_id = bool(a.ids & b.ids)
    tsim = title_similarity(a, b)
    doi_conflict = bool(a.doi and b.doi and a.doi != b.doi and not (a.ids & b.ids))

    if same_id:
        if tsim >= SAME_WORK_FLOOR or not a.title_key or not b.title_key:
            reasons = {"id"} | ({"key"} if same_key else set())
            return "dup", reasons, ""
        return "review", set(), "same DOI/arXiv id but different titles"

    if same_key:
        if tsim >= SAME_WORK_FLOOR and not doi_conflict:
            return "dup", {"key"}, ""
        return "collision", set(), "same key, different works"

    if tsim >= thr and len(a.title_key.split()) >= MIN_TITLE_WORDS and not doi_conflict:
        if authors_compatible(a, b):
            if a.year and a.year == b.year:
                return "dup", {"title"}, ""
            return "review", set(), "same title and authors, different or missing year (preprint?)"
    return None


def find_matches(items: list[Item], *, thr: float = TITLE_THRESHOLD,
                 use_key: bool = True) -> tuple[list[Match], list[Review]]:
    """Compare only entries that share a key, an id, a title or (year, first author)."""
    buckets: dict[tuple, list[int]] = {}

    def add(kind: str, value, idx: int) -> None:
        buckets.setdefault((kind, value), []).append(idx)

    for i, it in enumerate(items):
        if use_key and it.key:
            add("key", it.key.lower(), i)
        for ident in it.ids:
            add("id", ident, i)
        if it.title_key:
            add("title", it.title_key, i)
            if it.year and it.surnames:
                add("ya", (it.year, it.surnames[0]), i)

    pairs: set[tuple[int, int]] = set()
    for (kind, _), members in buckets.items():
        if len(members) < 2:
            continue
        if len(members) > MAX_BUCKET:
            log.debug(f"skipping a {kind} bucket of {len(members)} entries")
            continue
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                pairs.add((members[x], members[y]))

    matches: list[Match] = []
    reviews: list[Review] = []
    for i, j in sorted(pairs):
        a, b = items[i], items[j]
        verdict = classify(a, b, thr=thr, use_key=use_key)
        if verdict is None:
            continue
        kind, reasons, note = verdict
        if kind == "dup":
            matches.append(Match(a, b, reasons))
        else:
            reviews.append(Review([a, b], note, collision=(kind == "collision")))
    return matches, reviews


def group(items: list[Item], matches: list[Match]) -> list[list[Item]]:
    """Union-find over the matches; returns groups of 2+ items in file order."""
    root: dict[int, int] = {id(i): id(i) for i in items}

    def find(x: int) -> int:
        while root[x] != x:
            root[x] = root[root[x]]
            x = root[x]
        return x

    for m in matches:
        ra, rb = find(id(m.a)), find(id(m.b))
        if ra != rb:
            root[rb] = ra
    groups: dict[int, list[Item]] = {}
    for it in items:
        groups.setdefault(find(id(it)), []).append(it)
    return [sorted(g, key=lambda i: (i.file, i.order)) for g in groups.values() if len(g) > 1]


# =============================================================================
# Merging a group
# =============================================================================

def completeness(it: Item) -> float:
    score = float(sum(1 for v in it.fields.values() if v.strip()))
    if it.doi:
        score += 5
    if it.fields.get("abstract", "").strip():
        score += 3
    if it.fields.get("url", "").strip():
        score += 1
    if it.entry_type in GENERIC_TYPES:
        score -= 3
    return score


def same_value(name: str, a: str, b: str) -> bool:
    if name in ("author", "editor"):
        return author_surnames(a) == author_surnames(b)
    if name == "doi":
        return clean_doi(a) == clean_doi(b)
    if name == "url":
        return a.strip().lower().rstrip("/") == b.strip().lower().rstrip("/")
    return norm_text(delatex(a)) == norm_text(delatex(b))


def merge_group(members: list[Item], matches: list[Match], keep: str) -> Cluster:
    ordered = sorted(members, key=lambda i: i.order)
    if keep == "first":
        primary = ordered[0]
    else:
        primary = max(ordered, key=lambda i: (completeness(i), -i.order))
    others = [m for m in ordered if m is not primary]
    donors = sorted(others, key=lambda i: (-completeness(i), i.order))

    ids = {id(m) for m in members}
    reasons = sorted({r for m in matches if id(m.a) in ids and id(m.b) in ids for r in m.reasons},
                     key={"id": 0, "key": 1, "title": 2}.get)

    cluster = Cluster(primary=primary, removed=others, reasons=reasons)
    current = dict(primary.fields)
    names = dict(primary.names)
    seen_conflicts: set[tuple[str, str]] = set()

    for d in donors:
        for name, value in d.fields.items():
            if name == "comment" or not value.strip():
                continue
            names.setdefault(name, d.names.get(name, name))
            if name in current and current[name].strip():
                if not same_value(name, current[name], value):
                    sig = (name, norm_text(value))
                    if sig not in seen_conflicts:
                        seen_conflicts.add(sig)
                        cluster.conflicts.append(Conflict(name, current[name], value, d.key))
            elif name in primary.fields:                      # present but empty
                cluster.replace[name] = value
                cluster.filled[name] = d.key
                current[name] = value
            else:
                cluster.add.append((names[name], value))
                cluster.filled[name] = d.key
                current[name] = value

    tags = list(primary.tags)
    for d in donors:
        for t in d.tags:
            if t not in tags:
                tags.append(t)
                cluster.tags_added.append(t)
    if cluster.tags_added:
        joined = ", ".join(tags)
        if "comment" in primary.fields:
            cluster.replace["comment"] = joined
        else:
            cluster.add.append(("comment", joined))
        current["comment"] = joined
        names.setdefault("comment", "comment")

    cluster.final_fields = current
    cluster.final_names = names
    return cluster


# =============================================================================
# Editing the text
# =============================================================================

def fmt_value(name: str, value: str) -> str:
    if name.lower() == "month" and value.strip().lower() in MONTHS:
        return value.strip().lower()                           # BibTeX month macro
    return "{" + value + "}"


def patch_entry_text(raw: str, replace: dict[str, str], add: list[tuple[str, str]]) -> Optional[str]:
    """Change/add fields in an entry's text, leaving the rest of it as written.
    Returns None when the entry has a shape this cannot handle."""
    body = raw
    for name, value in replace.items():
        m = re.search(rf"(?im)^[ \t]*{re.escape(name)}[ \t]*=[ \t]*", body)
        if not m or m.end() >= len(body) or body[m.end()] != "{":
            return None
        i, depth, j = m.end(), 0, m.end()
        while j < len(body):
            if body[j] == "{":
                depth += 1
            elif body[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        else:
            return None
        body = body[: i + 1] + value + body[j:]

    if add:
        m = re.search(r"\}\s*\Z", body)
        if not m:
            return None
        head, tail = body[: m.start()].rstrip(), body[m.start():]
        had_comma = head.endswith(",")
        existing = re.findall(r"(?m)^([ \t]+)([A-Za-z][\w-]*)([ \t]*)=", head)
        indent = existing[0][0] if existing else "  "
        aligned = any(len(sp) >= 2 for _, _, sp in existing)
        width = max([len(n) + len(sp) for _, n, sp in existing] + [len(n) + 1 for n, _ in add])
        lines = [f"{indent}{n.ljust(width)}= {fmt_value(n, v)}" if aligned
                 else f"{indent}{n} = {fmt_value(n, v)}" for n, v in add]
        body = (head if had_comma else head + ",") + "\n" + ",\n".join(lines) \
            + ("," if had_comma else "") + "\n" + tail
    return body


def render_entry(cluster: Cluster) -> str:
    """Fallback: write the kept entry out in full from its merged fields."""
    p = cluster.primary
    lines = [f"@{p.entry_type}{{{p.key},"]
    for name, value in cluster.final_fields.items():
        shown = cluster.final_names.get(name, name)
        lines.append(f"  {shown} = {fmt_value(name, value)},")
    return "\n".join(lines) + "\n}"


def new_entry_text(cluster: Cluster) -> str:
    p = cluster.primary
    patched = patch_entry_text(p.raw, cluster.replace, cluster.add)
    if patched is not None:
        try:
            lib = bibtexparser.parse_string(patched)
            ok = (len(lib.entries) == 1 and not lib.failed_blocks) or \
                 (not lib.entries and len(lib.failed_blocks) == 1)
            if ok:
                return patched
        except Exception:
            pass
    log.debug(f"{p.key}: rewriting the whole entry (its text could not be patched)")
    return render_entry(cluster)


def removal_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    """The characters to cut for a removed entry: the entry and the blank lines
    after it, so no stack of empty lines is left behind."""
    j = end
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    return start, j, ""


def apply_clusters(text: str, clusters: list[Cluster]) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"                # keep the file's line endings

    def to_nl(s: str) -> str:
        return s.replace("\r\n", "\n").replace("\n", nl)

    cuts: list[tuple[int, int, str]] = []
    replacements: list[tuple[int, int, str]] = []
    for c in clusters:
        for r in c.removed:
            cuts.append(removal_span(text, r.cut_start, r.end))
        if c.changes_primary:
            replacements.append((c.primary.start, c.primary.end, to_nl(new_entry_text(c))))

    merged: list[tuple[int, int, str]] = []          # neighbouring cuts become one
    for start, end, repl in sorted(cuts):
        if merged and start <= merged[-1][1]:
            ps, pe, pr = merged[-1]
            merged[-1] = (ps, max(pe, end), repl if end >= pe else pr)
        else:
            merged.append((start, end, repl))

    if merged and merged[-1][1] >= len(text):        # cut reaches the end of the file:
        start, end, _ = merged[-1]                   # leave exactly one final newline
        while start > 0 and text[start - 1] in " \t\r\n":
            start -= 1
        merged[-1] = (start, end, nl if start > 0 else "")

    edits = sorted(merged + replacements, key=lambda e: e[0], reverse=True)
    for a, b in zip(edits, edits[1:]):
        if b[1] > a[0]:
            raise ValueError("overlapping edits")
    for start, end, repl in edits:
        text = text[:start] + repl + text[end:]
    return text


def check_result(new_text: str, before: list[Item], clusters: list[Cluster],
                 skipped: int) -> list[str]:
    """Re-parse the cleaned text and make sure it is what we meant to write."""
    problems: list[str] = []
    items, skipped_after, err = load_items(new_text, before[0].file if before else "")
    if err:
        return [err]
    removed = sum(len(c.removed) for c in clusters)
    if len(items) != len(before) - removed:
        problems.append(f"expected {len(before) - removed} entries, found {len(items)}")
    if skipped_after != skipped:
        problems.append(f"{skipped} unexamined block(s) before, {skipped_after} after")
    keys = {i.key.lower() for i in items}
    for c in clusters:
        if c.primary.key.lower() not in keys:
            problems.append(f"kept entry '{c.primary.key}' is missing from the result")
    counts: dict[str, int] = {}
    for i in items:
        counts[i.key.lower()] = counts.get(i.key.lower(), 0) + 1
    removed_keys = {r.key.lower() for c in clusters for r in c.removed}
    for k in removed_keys:
        if counts.get(k, 0) > 1:
            problems.append(f"key '{k}' still appears {counts[k]} times")
    after = {i.key.lower(): i for i in items}
    for c in clusters:
        got = after.get(c.primary.key.lower())
        if got is None:
            continue
        for name, value in c.final_fields.items():
            if name not in got.fields:
                problems.append(f"'{c.primary.key}': field {name} was lost")
            elif name in ("comment",) and got.fields[name].strip() != value.strip():
                problems.append(f"'{c.primary.key}': comment tags not saved as intended")
    return problems


# =============================================================================
# Processing files
# =============================================================================

def process_file(path: Path, args: argparse.Namespace) -> FileResult:
    res = FileResult(path=str(path))
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError) as e:
        res.fatal = f"cannot read: {e}"
        log.error(f"[cyan]{esc(str(path))}[/] {esc(str(e))}")
        return res

    items, res.skipped_blocks, err = load_items(text, str(path))
    if err:
        res.fatal = err
        log.error(f"[cyan]{esc(str(path))}[/] {esc(err)}")
        return res
    res.entries = len(items)

    matches, reviews = find_matches(items, thr=args.title_threshold)
    groups = group(items, matches)
    res.clusters = [merge_group(g, matches, args.keep) for g in groups]

    gid = {id(i): n for n, g in enumerate(groups) for i in g}
    res.reviews = [r for r in reviews                       # a doubt inside one group is settled
                   if len({gid.get(id(i), -1 - n) for n, i in enumerate(r.items)}) > 1]
    removed_ids = {id(r) for c in res.clusters for r in c.removed}
    res.survivors = [i for i in items if id(i) not in removed_ids]

    if not res.clusters or not (args.apply or args.output):
        return res

    try:
        new_text = apply_clusters(text, res.clusters)
    except ValueError as e:
        res.fatal = f"cannot edit safely: {e}"
        log.error(f"[cyan]{esc(str(path))}[/] {esc(str(e))}")
        return res
    problems = check_result(new_text, items, res.clusters, res.skipped_blocks)
    if problems:
        res.fatal = "safety check failed: " + "; ".join(problems)
        log.error(f"[cyan]{esc(str(path))}[/] safety check failed — file not written:")
        for p in problems:
            log.error(f"  {esc(p)}")
        return res

    target = Path(args.output) if args.output else path
    if not args.output and not args.no_backup:
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copy2(path, backup)
            log.info(f"Backup: [cyan]{esc(str(backup))}[/]")
        except OSError as e:
            res.fatal = f"backup failed: {e}"
            log.error(f"[cyan]{esc(str(path))}[/] {esc(str(e))}")
            return res
    try:
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    except OSError as e:
        res.fatal = f"write failed: {e}"
        log.error(f"[cyan]{esc(str(target))}[/] {esc(str(e))}")
        return res
    res.written = str(target)
    return res


# =============================================================================
# Display
# =============================================================================

def merged_summary(c: Cluster) -> str:
    bits = [f"+{n}" for n in c.filled]
    if c.tags_added:
        bits.append("comment +" + ",".join(c.tags_added))
    return ", ".join(bits) or "—"


def conflict_names(c: Cluster) -> str:
    return ", ".join(dict.fromkeys(x.field for x in c.conflicts)) or "—"


def conflict_summary(c: Cluster) -> str:
    return "; ".join(f"{x.field}: kept “{shorten(x.kept, 28)}”, dropped “{shorten(x.dropped, 28)}”"
                     for x in c.conflicts) or "—"


def show_file(res: FileResult, name: str) -> None:
    console.print()
    if res.clusters:
        t = Table(title=f"Duplicates — {name}", header_style="bold", show_lines=False)
        t.add_column("#", style="dim", justify="right", width=3)
        t.add_column("Kept", style="green", overflow="fold")
        t.add_column("Removed", style="red", overflow="fold")
        t.add_column("Match", width=9)
        t.add_column("Added to kept entry", style="cyan", overflow="fold")
        t.add_column("Differs (kept wins)", style="yellow", overflow="fold")
        for n, c in enumerate(res.clusters, 1):
            t.add_row(str(n), f"{c.primary.key}\n[dim]line {c.primary.line}[/]",
                      "\n".join(f"{r.key} [dim](line {r.line})[/]" for r in c.removed),
                      "+".join(c.reasons), merged_summary(c), conflict_names(c))
        console.print(t)
        if log.verbose:
            for c in res.clusters:
                for x in c.conflicts:
                    console.print(f"  [dim]{esc(c.primary.key)}[/] {x.field}: kept “{esc(shorten(x.kept, 70))}” "
                                  f"· dropped (from {esc(x.dropped_key)}) “{esc(shorten(x.dropped, 70))}”")
    if res.reviews:
        t = Table(title=f"Needs a human look — {name}", header_style="bold")
        t.add_column("Entries", style="cyan", overflow="fold")
        t.add_column("Why", style="yellow", overflow="fold")
        for r in res.reviews:
            tag = "[bold red]COLLISION[/] " if r.collision else ""
            t.add_row("\n".join(f"{i.key} [dim](line {i.line})[/]" for i in r.items), tag + r.why)
        console.print(t)
    if not res.clusters and not res.reviews:
        console.print(f"[green]{esc(name)}: no duplicates ({res.entries} entries).[/]")


def cross_file_groups(results: list[FileResult], thr: float) -> list[list[Item]]:
    items = [i for r in results for i in r.survivors]
    matches, _ = find_matches(items, thr=thr, use_key=False)
    return [g for g in group(items, matches) if len({i.file for i in g}) > 1]


def markdown_report(results: list[FileResult], across: list[list[Item]], applied: bool) -> str:
    total_groups = sum(len(r.clusters) for r in results)
    total_removed = sum(r.removed for r in results)
    out = ["## dedup-bib\n\n",
           f"{total_groups} duplicate group(s), {total_removed} entr{'y' if total_removed == 1 else 'ies'} "
           f"{'removed' if applied else 'to remove'}, in {len(results)} file(s).\n\n"]

    def cell(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ")

    for r in results:
        if not r.clusters and not r.reviews:
            continue
        out.append(f"### `{r.path}`\n\n")
        if r.clusters:
            out.append("| Kept | Removed | Match | Added to kept entry | Differs (kept value wins) |\n|---|---|---|---|---|\n")
            for c in r.clusters:
                out.append(f"| `{c.primary.key}` | {cell(', '.join(f'`{x.key}`' for x in c.removed))} | "
                           f"{'+'.join(c.reasons)} | {cell(merged_summary(c))} | {cell(conflict_summary(c))} |\n")
            out.append("\n")
        for rv in r.reviews:
            out.append(f"- {'**COLLISION** ' if rv.collision else ''}"
                       f"{', '.join(f'`{i.key}` (line {i.line})' for i in rv.items)} — {rv.why}\n")
        out.append("\n")
    if across:
        out.append("### Also present in more than one file (not modified)\n\n")
        for g in across:
            out.append("- " + ", ".join(f"`{i.label}`" for i in g) + "\n")
        out.append("\n")
    return "".join(out)


def key_map_text(results: list[FileResult]) -> str:
    rows = ["removed_key\tkept_key\tfile"]
    for r in results:
        for c in r.clusters:
            for x in c.removed:
                if x.key != c.primary.key:
                    rows.append(f"{x.key}\t{c.primary.key}\t{r.path}")
    return "\n".join(rows) + "\n"


# =============================================================================
# GitHub Actions helpers (same as fix-bib.py)
# =============================================================================

def gha_annotate(level: str, message: str, *, file: Optional[str] = None,
                 line: Optional[int] = None) -> None:
    loc = ""
    if file:
        loc += f" file={file}"
    if line:
        loc += f",line={line}"
    msg = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
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
# Main
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dedup-bib",
        description="Find and remove duplicate entries in BibTeX files "
                    "(dry run unless --apply or -o is given).",
    )
    p.add_argument("bibfiles", nargs="+", type=Path,
                   help="One or more .bib files (or directories with --recursive)")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="Recurse into directories looking for *.bib")
    w = p.add_argument_group("writing")
    w.add_argument("--apply", "--in-place", dest="apply", action="store_true",
                   help="Remove the duplicates from the files (a .bak backup is made first)")
    w.add_argument("-o", "--output", type=Path, metavar="FILE",
                   help="Write the cleaned file here instead (one input file only); "
                        "the original is not touched")
    w.add_argument("--no-backup", action="store_true", help="With --apply: no .bak file")
    m = p.add_argument_group("matching")
    m.add_argument("--keep", choices=("best", "first"), default="best",
                   help="Which entry of a group survives: the most complete one (default) "
                        "or the first in the file")
    m.add_argument("--title-threshold", type=float, default=TITLE_THRESHOLD,
                   help=f"Title similarity counted as the same title (default {TITLE_THRESHOLD})")
    m.add_argument("--across-files", action="store_true",
                   help="Also report works that appear in more than one of the given files "
                        "(report only, nothing is removed)")
    o = p.add_argument_group("output")
    o.add_argument("--report", type=Path, metavar="FILE.md", help="Write a Markdown report")
    o.add_argument("--key-map", type=Path, metavar="FILE.tsv",
                   help="Write 'removed_key<TAB>kept_key<TAB>file' for every removed key")
    o.add_argument("--check", action="store_true",
                   help="Exit 2 if duplicates or key collisions are found (never writes)")
    o.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON to stdout (tables go to stderr)")
    o.add_argument("--gha", action="store_true",
                   help="Emit GitHub Actions annotations and write $GITHUB_STEP_SUMMARY")
    o.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    global console
    console = Console(stderr=args.json, no_color=not IS_TTY, highlight=False)
    setup_logging(args.verbose, output_console=console)

    if not 0.0 < args.title_threshold <= 1.0:
        log.error("--title-threshold must be between 0 and 1.")
        return 1
    if args.apply and args.output:
        log.error("use either --apply or -o, not both.")
        return 1
    if args.check:
        if args.apply or args.output:
            log.error("--check never writes; drop --apply / -o.")
            return 1

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
        if f.suffix.lower() != ".bib" or not f.is_file():
            log.error(f"not a .bib file: [cyan]{esc(str(f))}[/]")
            return 1
    if args.output and len(files) != 1:
        log.error("-o needs exactly one input file.")
        return 1

    mode = "apply (in place)" if args.apply else (f"write copy to {args.output}" if args.output
                                                  else "dry run — nothing will be written")
    console.print()
    console.print(Panel(f"[bold]Files:[/] [cyan]{len(files)}[/]\n[bold]Mode:[/] {esc(mode)}\n"
                        f"[bold]Keep:[/] {args.keep}", title="[bold blue]dedup-bib[/]", expand=False))

    results: list[FileResult] = []
    for f in files:
        res = process_file(f, args)
        results.append(res)
        if not res.fatal:
            show_file(res, f.name)
        if args.gha:
            for c in res.clusters:
                for r in c.removed:
                    gha_annotate("warning", f"{r.key} duplicates {c.primary.key} ({'+'.join(c.reasons)})",
                                 file=str(f), line=r.line)
            for rv in res.reviews:
                gha_annotate("error" if rv.collision else "warning", f"{rv.items[0].key}: {rv.why}",
                             file=str(f), line=rv.items[0].line)

    across: list[list[Item]] = []
    if args.across_files and len(files) > 1:
        across = cross_file_groups([r for r in results if not r.fatal], args.title_threshold)
        if across:
            t = Table(title="Also present in more than one file (not modified)", header_style="bold")
            t.add_column("#", style="dim", justify="right", width=4)
            t.add_column("Where", style="cyan", overflow="fold")
            for n, g in enumerate(across, 1):
                t.add_row(str(n), "\n".join(i.label for i in g))
            console.print()
            console.print(t)
        else:
            log.info("No work appears in more than one of the given files.")

    n_groups = sum(len(r.clusters) for r in results)
    n_removed = sum(r.removed for r in results)
    n_collisions = sum(r.collisions for r in results)
    n_review = sum(len(r.reviews) for r in results)
    written = [r for r in results if r.written]

    summary = Table(title="Summary", header_style="bold")
    summary.add_column("")
    summary.add_column("Count", justify="right")
    summary.add_row("Entries examined", str(sum(r.entries for r in results)))
    summary.add_row("Duplicate groups", str(n_groups))
    summary.add_row("Entries removed" if written else "Entries to remove", str(n_removed))
    summary.add_row(Text("Key collisions", style="bold red" if n_collisions else ""), str(n_collisions))
    summary.add_row("Other entries to review", str(n_review - n_collisions))
    console.print()
    console.print(summary)

    for r in written:
        log.info(f"[green]Wrote[/] [cyan]{esc(r.written)}[/] ({r.removed} entr"
                 f"{'y' if r.removed == 1 else 'ies'} removed).")
    if n_removed and not written and not args.check:
        log.info("[yellow]Dry run — nothing written.[/] Re-run with --apply (in place) or -o FILE.")
    if n_collisions:
        log.warning(f"{n_collisions} key collision(s): different works share a key. "
                    f"Rename one by hand; they were left untouched.")

    markdown = markdown_report(results, across, applied=bool(written))
    if args.report:
        try:
            args.report.write_text(markdown, encoding="utf-8")
            log.info(f"Report written to [cyan]{esc(str(args.report))}[/]")
        except OSError as e:
            log.error(f"could not write report: {esc(str(e))}")
            return 1
    if args.key_map:
        try:
            args.key_map.write_text(key_map_text(results), encoding="utf-8")
            log.info(f"Key map written to [cyan]{esc(str(args.key_map))}[/]")
        except OSError as e:
            log.error(f"could not write key map: {esc(str(e))}")
            return 1
    if args.gha:
        write_gha_summary(markdown)

    if args.json:
        print(json.dumps({
            "applied": bool(written),
            "duplicate_groups": n_groups,
            "entries_removed": n_removed if written else 0,
            "entries_to_remove": n_removed,
            "key_collisions": n_collisions,
            "files": [{
                "path": r.path, "fatal": r.fatal, "entries": r.entries, "written": r.written,
                "skipped_blocks": r.skipped_blocks,
                "groups": [c.as_dict() for c in r.clusters],
                "review": [{"entries": [{"key": i.key, "line": i.line} for i in rv.items],
                            "why": rv.why, "collision": rv.collision} for rv in r.reviews],
            } for r in results],
            "across_files": [[{"file": i.file, "key": i.key, "line": i.line} for i in g] for g in across],
        }, indent=2, ensure_ascii=False))

    if any(r.fatal for r in results):
        return 1
    if args.check and (n_groups or n_collisions):
        log.warning(f"--check: {n_groups} duplicate group(s), {n_collisions} key collision(s).")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
