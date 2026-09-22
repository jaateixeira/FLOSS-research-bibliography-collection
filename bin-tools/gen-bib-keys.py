#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen-bib-keys.py — Standardize BibTeX keys in one or more .bib files.

Inspired by https://github.com/jaateixeira/nameit/ :
  * nameparser.HumanName for robust author parsing
  * loguru + RichHandler for logging
  * rich Table / Panel / Prompt for output
  * argparse (not typer)
  * unidecode added for proper Unicode folding

Key style (matching floss.bib):
  1 author : LastnameYear
  2 authors: Lastname1Lastname2Year
  3+       : Lastname1_et_alYear

Requires:
  pip install 'bibtexparser>=1.4,<2.0' nameparser loguru rich unidecode

Exit codes:
  0  success (or --check and nothing to change)
  1  error (parse failure, missing file, write failure, --strict warnings)
  2  --check and changes are needed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import bibtexparser
from loguru import logger
from nameparser import HumanName
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape as esc
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from unidecode import unidecode


import logging
logging.basicConfig(level=logging.DEBUG)


# Also enable pyparsing's own debug tracing
import pyparsing
pyparsing.ParserElement.DEFAULT_WHITE_CHARS = "\n\t "
pyparsing.ParserElement.enable_packrat()  # optional, faster




# =============================================================================
# Constants
# =============================================================================

YEAR_RE = re.compile(r"(\d{4})")
KEY_CHARS_RE = re.compile(r"[^A-Za-z0-9\-]")
AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+", re.IGNORECASE)
COMMENT_START_RE = re.compile(r"@comment\s*\{", re.IGNORECASE)

IS_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))
IS_TTY = sys.stdin.isatty() and sys.stdout.isatty()

# Module-level console, replaced in main() once we know about --json.
console: Console = Console()


# =============================================================================
# Logging
# =============================================================================

def setup_logging(verbose: bool, output_console: Optional[Console] = None) -> None:
    """Configure loguru with a RichHandler, same pattern as nameit."""
    global console
    if output_console is not None:
        console = output_console
    logger.remove()
    logger.add(
        RichHandler(
            console=console,
            show_time=False,
            show_path=False,
            rich_tracebacks=True,
            markup=True,
        ),
        format="{message}",
        level="DEBUG" if verbose else "INFO",
    )


# =============================================================================
# Unicode + sanitization
# =============================================================================

def strip_braces(s: str) -> str:
    """Remove BibTeX brace protection: '{Gonzalez-Barahona}' -> 'Gonzalez-Barahona'."""
    return s.replace("{", "").replace("}", "")


def sanitize_piece(s: str) -> str:
    """Return a key-safe fragment: ASCII, [A-Za-z0-9-] only."""
    s = strip_braces(s)
    s = unidecode(s)
    s = KEY_CHARS_RE.sub("", s)
    return s


# =============================================================================
# Data model
# =============================================================================

@dataclass
class AuthorChunk:
    raw: str
    last_name: Optional[str] = None
    is_others: bool = False
    malformed: bool = False
    reason: str = ""


@dataclass
class PlanItem:
    entry: dict
    old_key: str
    new_key: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False


# =============================================================================
# Year parsing
# =============================================================================

def parse_year(year_field: str) -> tuple[Optional[str], Optional[str]]:
    """Return (clean_year, warning). clean_year is None if unparsable."""
    year_field = (year_field or "").strip()
    if not year_field:
        return None, "empty year field"
    m = YEAR_RE.search(year_field)
    if not m:
        return None, f"cannot find a 4-digit year in {year_field!r}"
    clean = m.group(1)
    if year_field != clean:
        return clean, (
            f"year field has extra content: {year_field!r} "
            f"(using {clean}); consider a separate 'month' field"
        )
    return clean, None


# =============================================================================
# Author parsing
# =============================================================================

def extract_last_name(chunk: str) -> Optional[str]:
    """Use nameparser.HumanName to get the family name."""
    chunk = strip_braces(chunk).strip()
    if not chunk:
        return None
    hn = HumanName(chunk)
    last = hn.last or ""
    clean = sanitize_piece(last)
    return clean or None


def parse_authors(author_field: str) -> list[AuthorChunk]:
    """Split an author field on ' and ', then parse each chunk."""
    chunks: list[AuthorChunk] = []
    parts = AUTHOR_SPLIT_RE.split(author_field.strip())
    for raw in parts:
        raw = raw.strip()
        if not raw:
            continue
        if raw.lower() == "others":
            chunks.append(AuthorChunk(raw=raw, is_others=True))
            continue
        n_commas = raw.count(",")
        if n_commas > 1:
            chunks.append(AuthorChunk(
                raw=raw,
                malformed=True,
                reason=f"{n_commas} commas — expected at most 1",
            ))
            continue
        ln = extract_last_name(raw)
        if not ln:
            chunks.append(AuthorChunk(
                raw=raw,
                malformed=True,
                reason="could not extract a usable last name",
            ))
            continue
        chunks.append(AuthorChunk(raw=raw, last_name=ln))
    return chunks


# =============================================================================
# Interactive resolution
# =============================================================================

def resolve_malformed_interactive(
    entry: dict, author_field: str, chunks: list[AuthorChunk]
) -> Optional[list[AuthorChunk]]:
    """Ask the user how to handle a malformed author field."""
    old_key = entry.get("ID", "?")
    console.print()
    console.rule(f"[bold yellow]Ambiguous author field[/] — [cyan]{esc(old_key)}[/]")
    console.print(f"  author = {{{esc(author_field)}}}")
    console.print()
    for c in chunks:
        if c.malformed:
            console.print(
                f"  [yellow]• malformed:[/] {esc(c.raw)!r}  "
                f"[dim]({esc(c.reason)})[/]"
            )
        elif c.is_others:
            console.print("  [dim]• others[/]")
        else:
            console.print(
                f"  [green]• parsed:[/] {esc(c.raw)!r} "
                f"→ last name [bold]{esc(c.last_name or '')}[/]"
            )
    console.print()
    console.print("  [bold]k[/]eep    keep the old key, do not rename this entry")
    console.print("  [bold]s[/]plit   split malformed chunks on commas")
    console.print("  [bold]m[/]anual  enter last names manually (comma-separated)")

    choice = Prompt.ask("Choice", choices=["k", "s", "m"], default="k").lower()

    if choice == "k":
        return None

    if choice == "s":
        out: list[AuthorChunk] = []
        for c in chunks:
            if c.malformed:
                for piece in c.raw.split(","):
                    piece = piece.strip()
                    if not piece:
                        continue
                    ln = extract_last_name(piece)
                    if ln:
                        out.append(AuthorChunk(raw=piece, last_name=ln))
            else:
                out.append(c)
        return out

    raw = Prompt.ask("Enter last names, comma-separated")
    out = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        ln = sanitize_piece(piece)
        if ln:
            out.append(AuthorChunk(raw=piece, last_name=ln))
    return out


# =============================================================================
# Key generation
# =============================================================================

def build_base_key(
    entry: dict, interactive: bool
) -> tuple[Optional[str], list[str], list[str]]:
    """Return (base_key, warnings, errors). base_key is None on failure."""
    warnings: list[str] = []
    errors: list[str] = []

    author_field = (entry.get("author") or "").strip()
    year_field = (entry.get("year") or "").strip()

    if not author_field:
        errors.append("missing author field")
        return None, warnings, errors
    if not year_field:
        errors.append("missing year field")
        return None, warnings, errors

    clean_year, year_warn = parse_year(year_field)
    if year_warn:
        warnings.append(year_warn)
    if not clean_year:
        errors.append(f"cannot parse year: {year_field!r}")
        return None, warnings, errors

    chunks = parse_authors(author_field)
    if not chunks:
        errors.append("no authors parsed")
        return None, warnings, errors

    malformed = [c for c in chunks if c.malformed]
    if malformed:
        if interactive:
            resolved = resolve_malformed_interactive(entry, author_field, chunks)
            if resolved is None:
                warnings.append("user chose to keep the old key")
                return None, warnings, errors
            chunks = resolved
        else:
            for c in malformed:
                warnings.append(
                    f"malformed author chunk: {c.raw!r} ({c.reason}); "
                    f"run with --interactive to resolve"
                )
            return None, warnings, errors

    has_others = any(c.is_others for c in chunks)
    real = [c for c in chunks if not c.is_others and c.last_name]
    last_names = [c.last_name for c in real]  # type: ignore[list-item]

    if not last_names:
        errors.append("no usable last names")
        return None, warnings, errors

    if has_others or len(last_names) >= 3:
        base = f"{last_names[0]}_et_al{clean_year}"
    elif len(last_names) == 2:
        base = f"{last_names[0]}{last_names[1]}{clean_year}"
    else:
        base = f"{last_names[0]}{clean_year}"

    return base, warnings, errors


# =============================================================================
# Disambiguation
# =============================================================================

def disambiguate(plan: list[PlanItem]) -> None:
    """Ensure unique final keys across the whole plan."""
    kept: set[str] = set()
    to_rename: list[PlanItem] = []
    for item in plan:
        if not item.new_key or item.new_key == item.old_key:
            kept.add(item.old_key)
        else:
            to_rename.append(item)

    used = set(kept)
    for item in to_rename:
        base = item.new_key  # type: ignore[assignment]
        candidate = base
        if candidate in used:
            for letter in "abcdefghijklmnopqrstuvwxyz":
                if f"{base}{letter}" not in used:
                    candidate = f"{base}{letter}"
                    break
            else:
                i = 1
                while f"{base}{i}" in used:
                    i += 1
                candidate = f"{base}{i}"
            item.warnings.append(f"key collision: {base} → {candidate}")
        item.new_key = candidate
        used.add(candidate)


# =============================================================================
# Display
# =============================================================================

def show_plan(plan: list[PlanItem], title: str) -> None:
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Status", width=8)
    table.add_column("Old key", style="cyan", overflow="fold")
    table.add_column("New key", style="green", overflow="fold")
    table.add_column("Notes", style="yellow", overflow="fold")

    for i, item in enumerate(plan, 1):
        if item.errors:
            status = Text("ERROR", style="bold red")
        elif item.skipped:
            status = Text("KEEP", style="yellow")
        elif item.new_key == item.old_key:
            status = Text("OK", style="green")
        else:
            status = Text("RENAME", style="bold green")

        notes = "; ".join(item.warnings + item.errors)
        table.add_row(
            str(i),
            status,
            Text(item.old_key),
            Text(item.new_key or "—"),
            Text(notes),
        )
    console.print()
    console.print(table)


# =============================================================================
# Text-preserving key replacement
# =============================================================================

def rewrite_keys_in_text(
    text: str, mapping: dict[str, str]
) -> tuple[str, set[str]]:
    """Replace only the keys we know about, only in '@type{KEY,' context."""
    found: set[str] = set()
    for old_key, new_key in mapping.items():
        if old_key == new_key:
            continue
        pattern = re.compile(
            r"(?P<prefix>@\w+[ \t]*\{[ \t]*)"
            + re.escape(old_key)
            + r"(?P<suffix>[ \t]*,)"
        )
        new_text, n = pattern.subn(
            lambda m: f"{m.group('prefix')}{new_key}{m.group('suffix')}",
            text,
        )
        if n:
            found.add(old_key)
            text = new_text
    return text, found


def mask_comment_blocks(text: str) -> tuple[str, list[str]]:
    """Replace @comment{...} blocks with placeholders (balanced braces)."""
    masked: list[str] = []
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        m = COMMENT_START_RE.match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue
        start = i
        depth = 0
        j = m.end()
        while j < n:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    j += 1
                    break
                depth -= 1
            j += 1
        masked.append(text[start:j])
        out.append(f"@@COMMENT_{len(masked) - 1}@@")
        i = j
    return "".join(out), masked


def unmask_comment_blocks(text: str, masked: list[str]) -> str:
    for i, block in enumerate(masked):
        text = text.replace(f"@@COMMENT_{i}@@", block)
    return text


# =============================================================================
# GitHub Actions helpers
# =============================================================================

def gha_annotate(level: str, message: str,
                 *, file: Optional[str] = None,
                 line: Optional[int] = None) -> None:
    """Emit a GitHub Actions workflow command annotation."""
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
        logger.warning(f"could not write GITHUB_STEP_SUMMARY: {e}")


# =============================================================================
# Per-file processing
# =============================================================================

def process_file(
    bibfile: Path,
    *,
    interactive: bool,
    rewrite: bool,
    dry_run: bool,
    check: bool,
    apply: bool,
    no_backup: bool,
    gha: bool,
) -> dict:
    """Return a result dict describing what happened to one file."""
    result: dict = {
        "path": str(bibfile),
        "entries": 0,
        "changes": [],
        "unchanged": 0,
        "warning_count": 0,
        "error_count": 0,
        "applied": False,
        "skipped": False,
        "fatal": None,
    }

    try:
        raw_text = bibfile.read_text(encoding="utf-8")
    except OSError as e:
        result["fatal"] = f"cannot read: {e}"
        logger.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")
        return result

    try:
        bibdb = bibtexparser.loads(raw_text)
    except Exception as e:
        # Extract line number and the offending line text
        result["fatal"] = f"parse failure: {e}"

        
        logger.error(f"[cyan]{esc(str(bibfile))}[/] parse failure: {esc(str(e))}")
        return result

    entries = [
        e for e in bibdb.entries
        if (e.get("ENTRYTYPE", "") or "").lower() != "comment"
    ]
    result["entries"] = len(entries)

    if not entries:
        logger.warning(f"[cyan]{esc(str(bibfile))}[/] no entries found")
        result["skipped"] = True
        return result

    has_comments = bool(COMMENT_START_RE.search(raw_text))
    if has_comments and rewrite and apply:
        result["fatal"] = "@comment blocks would be LOST with --rewrite"
        logger.error(f"[cyan]{esc(str(bibfile))}[/] {esc(result['fatal'])}")
        return result
    if has_comments:
        logger.warning(
            f"[cyan]{esc(str(bibfile))}[/] contains @comment blocks; "
            f"they will be preserved as raw text"
        )

    # Build plan
    plan: list[PlanItem] = []
    for entry in entries:
        old_key = (entry.get("ID") or "").strip()
        item = PlanItem(entry=entry, old_key=old_key)
        base, warns, errs = build_base_key(entry, interactive=interactive)
        item.warnings.extend(warns)
        item.errors.extend(errs)
        if base:
            item.new_key = base
        else:
            item.new_key = old_key
            item.skipped = True

        for w in item.warnings:
            logger.warning(f"[cyan]{esc(old_key)}[/] {esc(w)}")
            if gha:
                gha_annotate("warning", f"{old_key}: {w}", file=str(bibfile))
        for e in item.errors:
            logger.error(f"[cyan]{esc(old_key)}[/] {esc(e)}")
            if gha:
                gha_annotate("error", f"{old_key}: {e}", file=str(bibfile))

        plan.append(item)

    disambiguate(plan)
    show_plan(plan, title=f"Key plan — {bibfile.name}")

    changes = [it for it in plan if it.new_key and it.new_key != it.old_key]
    result["changes"] = [
        {
            "old": it.old_key,
            "new": it.new_key,
            "warnings": list(it.warnings),
            "errors": list(it.errors),
        }
        for it in changes
    ]
    result["unchanged"] = len(plan) - len(changes)
    result["warning_count"] = sum(1 for it in plan if it.warnings)
    result["error_count"] = sum(1 for it in plan if it.errors)

    if not changes:
        logger.info(f"[green]{esc(str(bibfile))}: no key changes needed.[/]")
        return result

    logger.info(
        f"[bold]{len(changes)}[/] key(s) will be renamed in "
        f"[cyan]{esc(str(bibfile))}[/]"
    )

    if dry_run or check or not apply:
        if dry_run:
            logger.info("[yellow]Dry run — no file written.[/]")
        return result

    # Backup
    if not no_backup:
        backup = bibfile.with_name(bibfile.name + ".bak")
        try:
            shutil.copy2(bibfile, backup)
            logger.info(f"Backup: [cyan]{esc(str(backup))}[/]")
        except OSError as e:
            result["fatal"] = f"backup failed: {e}"
            logger.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")
            return result

    # Apply
    mapping = {it.old_key: it.new_key for it in changes if it.new_key}
    try:
        if rewrite:
            for item in plan:
                if item.new_key:
                    item.entry["ID"] = item.new_key
            with bibfile.open("w", encoding="utf-8") as f:
                bibtexparser.dump(bibdb, f)
            logger.info("[green]File rewritten via bibtexparser.[/]")
        else:
            masked_text, masked_blocks = mask_comment_blocks(raw_text)
            new_text, found = rewrite_keys_in_text(masked_text, mapping)
            new_text = unmask_comment_blocks(new_text, masked_blocks)
            for k in sorted(set(mapping) - found):
                logger.warning(
                    f"old key not found in raw text: [cyan]{esc(k)}[/] (kept)"
                )
            bibfile.write_text(new_text, encoding="utf-8")
            logger.info(f"[green]Text-substituted {len(found)} key(s).[/]")
        result["applied"] = True
    except OSError as e:
        result["fatal"] = f"write failed: {e}"
        logger.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")

    return result


# =============================================================================
# Main
# =============================================================================

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen-bib-keys",
        description="Standardize BibTeX keys in one or more .bib files.",
    )
    parser.add_argument("bibfiles", nargs="+", type=Path,
                        help="One or more .bib files (or directories with --recursive)")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="Recurse into directories looking for *.bib")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the plan without writing anything")
    parser.add_argument("--check", action="store_true",
                        help="Exit 2 if any key would change, 0 if clean. Implies --dry-run.")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Prompt the user when an entry is ambiguous (disabled in CI)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the final confirmation prompt")
    parser.add_argument("--no-backup", action="store_true",
                        help="Do not create a .bak file before writing")
    parser.add_argument("--rewrite", action="store_true",
                        help="Rewrite whole file via bibtexparser (loses formatting and comments)")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors (exit 1)")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON to stdout (table goes to stderr)")
    parser.add_argument("--gha", action="store_true",
                        help="Emit GitHub Actions annotations and write $GITHUB_STEP_SUMMARY")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")
    args = parser.parse_args(argv)

    # --check implies dry-run
    if args.check:
        args.dry_run = True

    # Set up console: route table output to stderr if we need stdout for JSON.
    global console
    console = Console(
        stderr=args.json,
        no_color=not IS_TTY,
        highlight=False,
    )
    setup_logging(args.verbose, output_console=console)

    # --- CI / TTY guard ------------------------------------------------------
    if IS_CI or not IS_TTY:
        if args.interactive:
            logger.error("--interactive cannot be used in CI / non-TTY mode.")
            return 1
        if not args.yes:
            args.yes = True
        if not args.no_backup:
            args.no_backup = True
        logger.info("[yellow]CI/non-TTY detected: --yes --no-backup auto-enabled.[/]")

    # --- Expand input paths --------------------------------------------------
    files: list[Path] = []
    for p in args.bibfiles:
        if p.is_dir():
            if not args.recursive:
                logger.error(f"[cyan]{esc(str(p))}[/] is a directory; use --recursive")
                return 1
            files.extend(sorted(p.rglob("*.bib")))
        else:
            files.append(p)

    if not files:
        logger.error("No .bib files found.")
        return 1

    bad_ext = [f for f in files if f.suffix.lower() != ".bib"]
    if bad_ext:
        for f in bad_ext:
            logger.error(f"not a .bib file: [cyan]{esc(str(f))}[/]")
        return 1

    missing = [f for f in files if not f.is_file()]
    if missing:
        for f in missing:
            logger.error(f"not a file: [cyan]{esc(str(f))}[/]")
        return 1

    console.print()
    console.print(Panel(
        f"[bold]Files:[/] [cyan]{len(files)}[/]\n"
        f"[bold]Key style:[/] LastnameYear | Lastname1Lastname2Year | Lastname1_et_alYear",
        title="[bold blue]gen-bib-keys[/]",
        expand=False,
    ))

    # --- Process each file ---------------------------------------------------
    apply_now = (
        not args.dry_run
        and not args.check
        and (args.yes or Confirm.ask(
            f"Apply changes to [cyan]{len(files)}[/] file(s)?", default=False
        ))
    )

    if not apply_now and not args.dry_run and not args.check:
        logger.info("Aborted by user.")
        return 0

    results: list[dict] = []
    for bibfile in files:
        console.rule(f"[bold]Processing[/] [cyan]{esc(str(bibfile))}[/]")
        res = process_file(
            bibfile,
            interactive=args.interactive,
            rewrite=args.rewrite,
            dry_run=args.dry_run,
            check=args.check,
            apply=apply_now,
            no_backup=args.no_backup,
            gha=args.gha,
        )
        results.append(res)

    # --- Aggregate -----------------------------------------------------------
    total_changes = sum(len(r["changes"]) for r in results)
    total_warnings = sum(r["warning_count"] for r in results)
    total_errors = sum(r["error_count"] for r in results)
    any_fatal = any(r["fatal"] for r in results)

    # --- GHA step summary ----------------------------------------------------
    if args.gha:
        lines = ["## gen-bib-keys\n\n"]
        lines.append(f"- Files: {len(results)}\n")
        lines.append(f"- Renames: {total_changes}\n")
        lines.append(f"- Warnings: {total_warnings}\n")
        lines.append(f"- Errors: {total_errors}\n\n")
        for r in results:
            if r["changes"]:
                lines.append(f"### `{r['path']}`\n\n")
                lines.append("| Old | New |\n|---|---|\n")
                for c in r["changes"]:
                    lines.append(f"| `{c['old']}` | `{c['new']}` |\n")
                lines.append("\n")
        write_gha_summary("".join(lines))

    # --- JSON output ---------------------------------------------------------
    if args.json:
        payload = {
            "files": results,
            "total_changes": total_changes,
            "total_warnings": total_warnings,
            "total_errors": total_errors,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    # --- Exit code -----------------------------------------------------------
    if any_fatal:
        return 1
    if args.strict and total_warnings:
        logger.error(f"--strict: {total_warnings} warning(s) → failing.")
        return 1
    if args.check and total_changes:
        logger.warning(f"--check: {total_changes} key(s) need standardization.")
        return 2
    if args.check:
        logger.info("[green]All keys are standardized.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
