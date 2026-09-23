#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix-bib.py — Validate and repair BibTeX files.

Checks for and attempts to repair:
  * syntax errors (using bibtexparser v2's fault-tolerant parser)
  * missing required fields (author, title, year)
  * duplicate entry keys
  * duplicate field keys within an entry
  * malformed author fields
  * unparsable or missing years

Uses bibtexparser v2 (>=2.0.0b1,<3.0) and Rich for output.

Exit codes:
  0  success (or --check and no repairs needed)
  1  error (fatal failure, --strict warnings)
  2  --check and repairs are needed
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
from nameparser import HumanName
from rich.console import Console
from rich.markup import escape as esc
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from unidecode import unidecode


# =============================================================================
# Constants
# =============================================================================

YEAR_RE = re.compile(r"(\d{4})")
KEY_CHARS_RE = re.compile(r"[^A-Za-z0-9\-]")
AUTHOR_SPLIT_RE = re.compile(r"\s+and\s+", re.IGNORECASE)

# Required fields for a "complete" entry (may be relaxed per type)
REQUIRED_FIELDS = ("author", "title", "year")

# Entry types that do NOT require author
NO_AUTHOR_TYPES = {"misc", "online", "software"}

IS_CI = bool(os.environ.get("GITHUB_ACTIONS") or os.environ.get("CI"))
IS_TTY = sys.stdin.isatty() and sys.stdout.isatty()

console: Console = Console()


# =============================================================================
# Rich-based logging (same shim as gen-bib-keys.py)
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
# v2 field helpers (same as gen-bib-keys.py)
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


def entry_set(entry, field_name: str, value: str) -> None:
    """Set a field on a bibtexparser v2 Entry object."""
    from bibtexparser.model import Field
    fields = getattr(entry, "fields_dict", {}) or {}
    if field_name in fields:
        fields[field_name].value = value
    else:
        new_field = Field(field_name, value)
        entry.fields.append(new_field)


# =============================================================================
# Unicode + sanitization
# =============================================================================

def strip_braces(s: str) -> str:
    return s.replace("{", "").replace("}", "")


def sanitize_piece(s: str) -> str:
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
class RepairItem:
    entry: object
    key: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    skipped: bool = False


# =============================================================================
# Year parsing
# =============================================================================

def parse_year(year_field: str) -> tuple[Optional[str], Optional[str]]:
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
            f"(using {clean})"
        )
    return clean, None


# =============================================================================
# Author parsing
# =============================================================================

def extract_last_name(chunk: str) -> Optional[str]:
    chunk = strip_braces(chunk).strip()
    if not chunk:
        return None
    hn = HumanName(chunk)
    last = hn.last or ""
    clean = sanitize_piece(last)
    return clean or None


def parse_authors(author_field: str) -> list[AuthorChunk]:
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
# Debug display
# =============================================================================

def show_entry_debug(entry, key: str) -> None:
    """Print a Rich table describing a single entry's fields."""
    table = Table(
        title=f"[bold]Entry debug[/] — [cyan]{esc(key)}[/]",
        show_header=True,
        header_style="bold",
        show_lines=False,
        expand=False,
    )
    table.add_column("Field", style="bold", no_wrap=True)
    table.add_column("Value", overflow="fold")

    table.add_row("entry type", esc(str(getattr(entry, "entry_type", ""))))
    table.add_row("key", esc(key))

    fields = getattr(entry, "fields_dict", {}) or {}
    for fname in sorted(fields.keys()):
        val = entry_get(entry, fname)
        table.add_row(fname, esc(val) if val else "[dim]—[/]")

    console.print(table)


# =============================================================================
# Interactive repair
# =============================================================================

def resolve_malformed_interactive(
    entry, author_field: str, chunks: list[AuthorChunk]
) -> Optional[list[AuthorChunk]]:
    key = getattr(entry, "key", "?")
    console.print()
    console.rule(f"[bold yellow]Ambiguous author field[/] — [cyan]{esc(key)}[/]")
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
    console.print("  [bold]k[/]eep    keep the field unchanged")
    console.print("  [bold]s[/]plit   split malformed chunks on commas")
    console.print("  [bold]m[/]anual  enter corrected author field")

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

    raw = Prompt.ask("Enter corrected author field")
    # Re-parse the user's input
    return parse_authors(raw)


# =============================================================================
# Validation & repair logic
# =============================================================================

def validate_entry(
    entry,
    interactive: bool,
) -> RepairItem:
    """Validate a single entry and collect warnings/errors/repairs."""
    key = (getattr(entry, "key", "") or "").strip()
    item = RepairItem(entry=entry, key=key)

    entry_type = (getattr(entry, "entry_type", "") or "").lower()

    # ---- Check required fields ---------------------------------------------
    for fname in REQUIRED_FIELDS:
        if fname == "author" and entry_type in NO_AUTHOR_TYPES:
            continue
        val = entry_get(entry, fname).strip()
        if not val:
            item.errors.append(f"missing required field: {fname}")

    # ---- Check year --------------------------------------------------------
    year_val = entry_get(entry, "year").strip()
    if year_val:
        clean_year, year_warn = parse_year(year_val)
        if year_warn:
            item.warnings.append(year_warn)
        if not clean_year:
            item.errors.append(f"unparsable year: {year_val!r}")
        elif clean_year != year_val:
            # Repair: replace year with just the 4-digit year
            if interactive:
                console.print()
                console.print(
                    f"[yellow]Entry[/] [cyan]{esc(key)}[/] "
                    f"[yellow]has year field[/] {esc(year_val)!r}"
                )
                if Confirm.ask(
                    f"Replace year with {esc(clean_year)!r}?", default=True
                ):
                    entry_set(entry, "year", clean_year)
                    item.repairs.append(f"year: {year_val!r} → {clean_year!r}")
            else:
                entry_set(entry, "year", clean_year)
                item.repairs.append(f"year: {year_val!r} → {clean_year!r}")

    # ---- Check author ------------------------------------------------------
    author_val = entry_get(entry, "author").strip()
    if author_val:
        chunks = parse_authors(author_val)
        malformed = [c for c in chunks if c.malformed]

        if malformed:
            if interactive:
                resolved = resolve_malformed_interactive(entry, author_val, chunks)
                if resolved is None:
                    item.warnings.append("user chose to keep author field unchanged")
                else:
                    # Rebuild the author field from resolved chunks
                    new_author = " and ".join(c.raw for c in resolved)
                    entry_set(entry, "author", new_author)
                    item.repairs.append(
                        f"author: {author_val!r} → {new_author!r}"
                    )
            else:
                for c in malformed:
                    item.warnings.append(
                        f"malformed author chunk: {c.raw!r} ({c.reason})"
                    )

    # ---- Check for duplicate field keys (already caught by v2 as failed blocks)
    # v2 handles this in library.failed_blocks, not per-entry.

    # ---- Check for empty key -----------------------------------------------
    if not key:
        item.errors.append("empty entry key")
        item.skipped = True

    return item


def validate_library(
    library,
    interactive: bool,
) -> tuple[list[RepairItem], list]:
    """Validate all entries in a library. Returns (items, failed_blocks)."""
    items: list[RepairItem] = []
    entries = list(getattr(library, "entries", []) or [])

    for entry in entries:
        item = validate_entry(entry, interactive=interactive)
        items.append(item)

    failed = list(getattr(library, "failed_blocks", []) or [])
    return items, failed


# =============================================================================
# Display
# =============================================================================

def show_report(
    items: list[RepairItem],
    failed_blocks: list,
    title: str,
) -> None:
    table = Table(title=title, show_lines=False, header_style="bold")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Status", width=10)
    table.add_column("Key", style="cyan", overflow="fold")
    table.add_column("Notes", style="yellow", overflow="fold")

    for i, item in enumerate(items, 1):
        if item.errors:
            status = Text("ERROR", style="bold red")
        elif item.repairs:
            status = Text("REPAIRED", style="bold green")
        elif item.warnings:
            status = Text("WARN", style="yellow")
        else:
            status = Text("OK", style="green")

        notes_parts = []
        if item.repairs:
            notes_parts.append("repairs: " + "; ".join(item.repairs))
        if item.warnings:
            notes_parts.append("warnings: " + "; ".join(item.warnings))
        if item.errors:
            notes_parts.append("errors: " + "; ".join(item.errors))

        table.add_row(
            str(i),
            status,
            Text(item.key or "[empty]"),
            Text("\n".join(notes_parts) or "—"),
        )

    console.print()
    console.print(table)

    if failed_blocks:
        fb_table = Table(
            title="[bold red]Failed blocks (syntax errors)[/]",
            show_lines=False,
            header_style="bold",
        )
        fb_table.add_column("#", style="dim", width=4, justify="right")
        fb_table.add_column("Type", style="red")
        fb_table.add_column("Line", style="dim")
        fb_table.add_column("Details", overflow="fold")

        for i, fb in enumerate(failed_blocks, 1):
            fb_type = type(fb).__name__
            start_line = getattr(fb, "start_line", "?")
            details = ""
            if hasattr(fb, "duplicate_keys"):
                details = f"duplicate field keys: {fb.duplicate_keys}"
            elif hasattr(fb, "key"):
                details = f"key: {fb.key}"
            fb_table.add_row(str(i), fb_type, str(start_line), details)

        console.print()
        console.print(fb_table)


# =============================================================================
# GitHub Actions helpers
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

def process_file(
    bibfile: Path,
    *,
    interactive: bool,
    dry_run: bool,
    check: bool,
    apply: bool,
    no_backup: bool,
    gha: bool,
) -> dict:
    """Validate and optionally repair one .bib file."""
    result: dict = {
        "path": str(bibfile),
        "entries": 0,
        "failed_blocks": 0,
        "repairs": [],
        "warnings": 0,
        "errors": 0,
        "applied": False,
        "fatal": None,
    }

    log.debug(f"Reading [cyan]{esc(str(bibfile))}[/]")

    try:
        raw_text = bibfile.read_text(encoding="utf-8")
    except OSError as e:
        result["fatal"] = f"cannot read: {e}"
        log.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")
        return result

    log.debug(f"Parsing [cyan]{esc(str(bibfile))}[/] "
              f"({len(raw_text)} bytes) with bibtexparser v2")

    try:
        library = bibtexparser.parse_string(raw_text)
    except Exception as e:
        result["fatal"] = f"parse failure: {e}"
        log.error(f"[cyan]{esc(str(bibfile))}[/] parse failure: {esc(str(e))}")
        return result

    # ---- v2 fault tolerance: inspect failed_blocks ------------------------
    failed_blocks = list(getattr(library, "failed_blocks", []) or [])
    result["failed_blocks"] = len(failed_blocks)

    if failed_blocks:
        for fb in failed_blocks:
            line = getattr(fb, "start_line", "?")
            fb_type = type(fb).__name__
            log.warning(
                f"[cyan]{esc(str(bibfile))}[/] failed block "
                f"({fb_type}) at line {line}"
            )
            if gha:
                gha_annotate(
                    "warning",
                    f"failed block ({fb_type})",
                    file=str(bibfile),
                    line=line if isinstance(line, int) else None,
                )

    entries = list(getattr(library, "entries", []) or [])
    result["entries"] = len(entries)

    log.debug(f"Found [bold]{len(entries)}[/] entries in "
              f"[cyan]{esc(str(bibfile))}[/]")

    # ---- Validate & repair ------------------------------------------------
    items, _ = validate_library(library, interactive=interactive)

    # ---- Debug tables (only with --verbose) --------------------------------
    if log.verbose:
        for item in items:
            show_entry_debug(item.entry, item.key)

    # ---- Per-entry logging -------------------------------------------------
    for item in items:
        for r in item.repairs:
            log.info(f"[cyan]{esc(item.key)}[/] repaired: {esc(r)}")
            if gha:
                gha_annotate("notice", f"{item.key}: {r}", file=str(bibfile))
        for w in item.warnings:
            log.warning(f"[cyan]{esc(item.key)}[/] {esc(w)}")
            if gha:
                gha_annotate("warning", f"{item.key}: {w}", file=str(bibfile))
        for e in item.errors:
            log.error(f"[cyan]{esc(item.key)}[/] {esc(e)}")
            if gha:
                gha_annotate("error", f"{item.key}: {e}", file=str(bibfile))

    # ---- Report ------------------------------------------------------------
    show_report(items, failed_blocks, title=f"Validation report — {bibfile.name}")

    result["repairs"] = [
        {"key": it.key, "repairs": list(it.repairs)}
        for it in items if it.repairs
    ]
    result["warnings"] = sum(1 for it in items if it.warnings)
    result["errors"] = sum(1 for it in items if it.errors)

    has_repairs = any(it.repairs for it in items)
    has_errors = any(it.errors for it in items)
    has_failed = len(failed_blocks) > 0

    if not has_repairs and not has_errors and not has_failed:
        log.info(f"[green]{esc(str(bibfile))}: no issues found.[/]")
        return result

    if dry_run or check or not apply:
        if dry_run:
            log.info("[yellow]Dry run — no file written.[/]")
        return result

    if not has_repairs:
        # Nothing to write, but we may still want to flag errors
        if has_errors or has_failed:
            log.warning(
                f"[cyan]{esc(str(bibfile))}[/] has errors but no automatic repairs; "
                f"file not modified."
            )
        return result

    # ---- Backup -----------------------------------------------------------
    if not no_backup:
        backup = bibfile.with_name(bibfile.name + ".bak")
        try:
            shutil.copy2(bibfile, backup)
            log.info(f"Backup: [cyan]{esc(str(backup))}[/]")
        except OSError as e:
            result["fatal"] = f"backup failed: {e}"
            log.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")
            return result

    # ---- Apply repairs: write the library back ----------------------------
    try:
        with open(bibfile, "w", encoding="utf-8") as f:
            bibtexparser.write_file(f, library)
        log.info(f"[green]File written with {len(result['repairs'])} repair(s).[/]")
        result["applied"] = True
    except Exception as e:
        result["fatal"] = f"write failed: {e}"
        log.error(f"[cyan]{esc(str(bibfile))}[/] {esc(str(e))}")

    return result


# =============================================================================
# Main
# =============================================================================

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fix-bib",
        description="Validate and repair BibTeX files.",
    )
    parser.add_argument("bibfiles", nargs="+", type=Path,
                        help="One or more .bib files (or directories with --recursive)")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="Recurse into directories looking for *.bib")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be repaired without writing")
    parser.add_argument("--check", action="store_true",
                        help="Exit 2 if repairs are needed, 0 if clean. Implies --dry-run.")
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Prompt when a repair needs a decision (disabled in CI)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip the final confirmation prompt")
    parser.add_argument("--no-backup", action="store_true",
                        help="Do not create a .bak file before writing")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors (exit 1)")
    parser.add_argument("--json", action="store_true",
                        help="Emit machine-readable JSON to stdout (table goes to stderr)")
    parser.add_argument("--gha", action="store_true",
                        help="Emit GitHub Actions annotations and write $GITHUB_STEP_SUMMARY")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging + per-entry debug tables")
    args = parser.parse_args(argv)

    if args.check:
        args.dry_run = True

    global console
    console = Console(
        stderr=args.json,
        no_color=not IS_TTY,
        highlight=False,
    )
    setup_logging(args.verbose, output_console=console)

    if args.verbose:
        console.print("[dim]IN verbose mode[/]")

    # --- CI / TTY guard ------------------------------------------------------
    if IS_CI or not IS_TTY:
        if args.interactive:
            log.error("--interactive cannot be used in CI / non-TTY mode.")
            return 1
        if not args.yes:
            args.yes = True
        if not args.no_backup:
            args.no_backup = True
        log.info("[yellow]CI/non-TTY detected: --yes --no-backup auto-enabled.[/]")

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

    bad_ext = [f for f in files if f.suffix.lower() != ".bib"]
    if bad_ext:
        for f in bad_ext:
            log.error(f"not a .bib file: [cyan]{esc(str(f))}[/]")
        return 1

    missing = [f for f in files if not f.is_file()]
    if missing:
        for f in missing:
            log.error(f"not a file: [cyan]{esc(str(f))}[/]")
        return 1

    console.print()
    console.print(Panel(
        f"[bold]Files:[/] [cyan]{len(files)}[/]\n"
        f"[bold]Checks:[/] required fields, year format, author parsing, "
        f"duplicate keys, syntax errors",
        title="[bold blue]fix-bib[/]",
        expand=False,
    ))

    # --- Process each file ---------------------------------------------------
    apply_now = (
        not args.dry_run
        and not args.check
        and (args.yes or Confirm.ask(
            f"Apply repairs to [cyan]{len(files)}[/] file(s)?", default=False
        ))
    )

    if not apply_now and not args.dry_run and not args.check:
        log.info("Aborted by user.")
        return 0

    results: list[dict] = []
    for bibfile in files:
        console.rule(f"[bold]Processing[/] [cyan]{esc(str(bibfile))}[/]")
        res = process_file(
            bibfile,
            interactive=args.interactive,
            dry_run=args.dry_run,
            check=args.check,
            apply=apply_now,
            no_backup=args.no_backup,
            gha=args.gha,
        )
        results.append(res)

    # --- Aggregate -----------------------------------------------------------
    total_repairs = sum(len(r["repairs"]) for r in results)
    total_warnings = sum(r["warnings"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    total_failed = sum(r["failed_blocks"] for r in results)
    any_fatal = any(r["fatal"] for r in results)

    # --- GHA step summary ----------------------------------------------------
    if args.gha:
        lines = ["## fix-bib\n\n"]
        lines.append(f"- Files: {len(results)}\n")
        lines.append(f"- Repairs: {total_repairs}\n")
        lines.append(f"- Warnings: {total_warnings}\n")
        lines.append(f"- Errors: {total_errors}\n")
        lines.append(f"- Failed blocks: {total_failed}\n\n")
        for r in results:
            if r["repairs"]:
                lines.append(f"### `{r['path']}`\n\n")
                for rep in r["repairs"]:
                    for detail in rep["repairs"]:
                        lines.append(f"- `{rep['key']}`: {detail}\n")
                lines.append("\n")
        write_gha_summary("".join(lines))

    # --- JSON output ---------------------------------------------------------
    if args.json:
        payload = {
            "files": results,
            "total_repairs": total_repairs,
            "total_warnings": total_warnings,
            "total_errors": total_errors,
            "total_failed_blocks": total_failed,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))

    # --- Exit code -----------------------------------------------------------
    if any_fatal:
        return 1
    if args.strict and total_warnings:
        log.error(f"--strict: {total_warnings} warning(s) → failing.")
        return 1
    if args.check and total_repairs:
        log.warning(f"--check: {total_repairs} repair(s) needed.")
        return 2
    if args.check:
        log.info("[green]All files are clean.[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())