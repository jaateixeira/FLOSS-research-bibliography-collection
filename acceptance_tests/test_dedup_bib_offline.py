#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline tests for bin-tools/dedup-bib.py (no network needed).

Run from the repository root:
    pytest acceptance_tests/test_dedup_bib_offline.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPT = next(p for p in (HERE / "dedup-bib.py", HERE.parent / "bin-tools" / "dedup-bib.py")
              if p.is_file())
spec = importlib.util.spec_from_file_location("dedup_bib", SCRIPT)
db = importlib.util.module_from_spec(spec)
sys.modules["dedup_bib"] = db
spec.loader.exec_module(db)


# =============================================================================
# Helpers
# =============================================================================

def items_of(text: str):
    items, skipped, err = db.load_items(text, "t.bib")
    assert err is None, err
    return items, skipped


def by_key(items, key):
    return next(i for i in items if i.key == key)


def verdict(text: str, a: str, b: str, **kw):
    items, _ = items_of(text)
    return db.classify(by_key(items, a), by_key(items, b),
                       thr=kw.get("thr", db.TITLE_THRESHOLD), use_key=kw.get("use_key", True))


def run(tmp_path: Path, text: str, *extra: str, name: str = "t.bib", newline: str = ""):
    path = tmp_path / name
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    code = db.main([str(path), *extra])
    with open(path, encoding="utf-8", newline="") as f:
        return code, f.read(), path


SAME_WORK = r"""
% header comment
@inproceedings{a2026lessons,
  author    = {Teixeira, Jose},
  title     = {Lessons Learned from a Governance Transition},
  booktitle = {Proceedings of ICIS},
  year      = {2026},
  url       = {https://example.org/1},
  comment   = {TOP_IS_C}
}

@article{Other2020,
  author  = {Other, Olga},
  title   = {A completely different paper about forks},
  year    = {2020},
  doi     = {10.1000/other}
}

@inproceedings{Teixeira2026,
  title={Lessons Learned from a  Governance Transition},
  author={José A. Teixeira},
  year={2026},
  location ={Lisbon, Portugal},
  comment={FT50},
  note={Accepted}
}
"""


# =============================================================================
# Small helpers
# =============================================================================

@pytest.mark.parametrize("raw, expected", [
    ("{Cobblestone: A {Divide} Approach}", "Cobblestone: A {Divide} Approach"),
    ("{2026}", "2026"),
    ('"quoted"', "quoted"),
    ("2026", "2026"),
    ("jan", "jan"),
    ("{A} and {B}", "{A} and {B}"),            # first group closes early: not one group
    ("{{AssertFlip}: Reproducing}", "{AssertFlip}: Reproducing"),
])
def test_strip_delimiters(raw, expected):
    assert db.strip_delimiters(raw) == expected


@pytest.mark.parametrize("name, expected", [
    ("Teixeira, Jose", "Teixeira"),
    ("José A. Teixeira", "Teixeira"),
    ("Wil van der Aalst", "van der Aalst"),
    ("Martin Luther King Jr.", "King"),
])
def test_surname(name, expected):
    assert db.surname(name) == expected


def test_normalisation_ignores_latex_case_accents_spacing():
    a = db.norm_text(db.delatex(r"{\'E}vidence  from the {Commons}"))
    b = db.norm_text(db.delatex("Évidence from the commons"))
    assert a == b == "evidence from the commons"


# =============================================================================
# Deciding what is a duplicate
# =============================================================================

def test_same_title_year_and_author_is_a_duplicate():
    kind, reasons, _ = verdict(SAME_WORK, "a2026lessons", "Teixeira2026")
    assert (kind, reasons) == ("dup", {"title"})


def test_same_doi_is_a_duplicate_even_with_other_keys_and_years():
    bib = """
    @article{one, author={A, B}, title={On forks in open source}, year={2019}, doi={10.1000/X}}
    @article{two, author={A, B}, title={On forks in open source}, year={2020}, doi={https://doi.org/10.1000/x}}
    """
    assert verdict(bib, "one", "two")[:2] == ("dup", {"id"})


def test_arxiv_id_matches_across_doi_and_eprint():
    bib = """
    @misc{pre, author={A, B}, title={Deep learning for repositories}, year={2021}, eprint={2101.00001v2}, archiveprefix={arXiv}}
    @misc{pre2, author={A, B}, title={Deep learning for repositories}, year={2021}, doi={10.48550/arXiv.2101.00001}}
    """
    assert verdict(bib, "pre", "pre2")[:2] == ("dup", {"id"})


def test_editorials_with_different_dois_are_not_duplicates():
    bib = """
    @article{e1, author={Avison, D}, title={Editorial: a note on things}, year={2011}, doi={10.1000/a}}
    @article{e2, author={Avison, D}, title={Editorial: a note on things}, year={2011}, doi={10.1000/b}}
    """
    assert verdict(bib, "e1", "e2") is None


def test_short_generic_titles_never_match_on_title_alone():
    bib = """
    @article{i1, author={Bosch, J}, title={Introduction}, year={2018}}
    @article{i2, author={Bosch, J}, title={Introduction}, year={2018}}
    """
    assert verdict(bib, "i1", "i2") is None


def test_same_title_different_year_is_for_review_not_removal():
    bib = """
    @misc{pre, author={A, B}, title={Open source governance under stress}, year={2020}}
    @article{pub, author={A, B}, title={Open source governance under stress}, year={2021}}
    """
    kind, _, note = verdict(bib, "pre", "pub")
    assert kind == "review" and "year" in note


def test_same_title_different_authors_is_not_a_duplicate():
    bib = """
    @article{x, author={Smith, A}, title={Open source governance under stress}, year={2020}}
    @article{y, author={Jones, B}, title={Open source governance under stress}, year={2020}}
    """
    assert verdict(bib, "x", "y") is None


def test_same_key_different_work_is_a_collision():
    bib = """
    @article{k, author={A, B}, title={First paper about forks}, year={2019}, doi={10.1000/a}}
    @article{k, author={C, D}, title={Something else entirely about money}, year={2020}, doi={10.1000/b}}
    """
    items, skipped = items_of(bib)
    assert skipped == 0 and len(items) == 2                      # the second copy is recovered
    kind, _, _ = db.classify(items[0], items[1], thr=0.93, use_key=True)
    assert kind == "collision"


# =============================================================================
# Duplicate-key blocks
# =============================================================================

TWINS = """
@inproceedings{kasi2026cobble,
  author    = {Kasibatla, Saketh and Lerner, Sorin},
  title     = {Cobblestone: A {Divide-and-Conquer} Approach},
  year      = {2026},
  doi       = {10.1145/3744916.3773178},
  comment   = {TOP_SE_C}
}

@inproceedings{kasi2026cobble,
  author    = {Kasibatla, Saketh and Lerner, Sorin},
  title     = {Cobblestone: A {Divide-and-Conquer} Approach},
  year      = {2026},
  doi       = {10.1145/3744916.3773178},
  comment   = {TOP_SE_C},
  abstract  = {[Paraphrase] A tool.}
}
"""


def test_second_copy_of_a_key_is_recovered_with_clean_values():
    items, skipped = items_of(TWINS)
    assert len(items) == 2 and skipped == 0
    dup = items[1]
    assert dup.from_failed and dup.fields["year"] == "2026"
    assert dup.fields["title"] == items[0].fields["title"]
    assert dup.doi == "10.1145/3744916.3773178"


def test_twins_are_removed_and_the_more_complete_copy_survives(tmp_path):
    code, out, _ = run(tmp_path, TWINS, "--apply", "--no-backup")
    assert code == 0
    items, _ = items_of(out)
    assert len(items) == 1 and items[0].fields["abstract"].startswith("[Paraphrase]")
    assert "failed" not in out and out.count("@inproceedings") == 1


# =============================================================================
# Merging
# =============================================================================

def merged(text: str, keep: str = "best"):
    items, _ = items_of(text)
    matches, _ = db.find_matches(items)
    (group,) = db.group(items, matches)
    return db.merge_group(group, matches, keep)


def test_most_complete_entry_is_kept_and_gaps_filled():
    c = merged(SAME_WORK)
    assert c.primary.key == "a2026lessons" and [r.key for r in c.removed] == ["Teixeira2026"]
    assert c.filled == {"location": "Teixeira2026", "note": "Teixeira2026"}
    assert c.final_fields["location"] == "Lisbon, Portugal"


def test_comment_tags_are_unioned_not_overwritten():
    c = merged(SAME_WORK)
    assert c.tags_added == ["FT50"]
    assert c.final_fields["comment"] == "TOP_IS_C, FT50"
    assert c.replace == {"comment": "TOP_IS_C, FT50"}


def test_conflicting_values_keep_the_kept_entry_and_are_reported():
    bib = """
    @article{a, author={X, Y}, title={A study of forking in open source}, year={2020}, doi={10.1000/z}, publisher={IEEE}, abstract={Long abstract text here.}}
    @article{b, author={X, Y}, title={A study of forking in open source}, year={2020}, doi={10.1000/z}, publisher={ACM}}
    """
    c = merged(bib)
    assert c.primary.key == "a"
    assert [(x.field, x.kept, x.dropped) for x in c.conflicts] == [("publisher", "IEEE", "ACM")]


def test_author_spelling_differences_are_not_conflicts():
    assert merged(SAME_WORK).conflicts == []


def test_keep_first():
    bib = """
    @article{first, author={X, Y}, title={A study of forking in open source}, year={2020}}
    @article{second, author={X, Y}, title={A study of forking in open source}, year={2020}, doi={10.1000/z}, abstract={Something.}}
    """
    assert merged(bib, "first").primary.key == "first"
    assert merged(bib, "best").primary.key == "second"


def test_a_group_of_three_is_one_cluster():
    bib = """
    @article{a, author={X, Y}, title={A study of forking in open source}, year={2020}, doi={10.1000/z}}
    @article{b, author={X, Y}, title={A study of forking in open source}, year={2020}}
    @article{c, author={X, Y}, title={A study of forking in open source!}, year={2020}, comment={FT50}}
    """
    c = merged(bib)
    assert len(c.removed) == 2 and c.tags_added == ["FT50"]


# =============================================================================
# Editing text
# =============================================================================

def test_added_fields_follow_the_style_of_the_entry():
    aligned = "@article{k,\n  author  = {A},\n  title   = {T}\n}"
    out = db.patch_entry_text(aligned, {}, [("note", "N"), ("location", "L")])
    assert out == ("@article{k,\n  author  = {A},\n  title   = {T},\n"
                   "  note     = {N},\n  location = {L}\n}")
    tight = "@article{k,\n  author={A},\n  title={T},\n}"
    out = db.patch_entry_text(tight, {}, [("note", "N")])
    assert out == "@article{k,\n  author={A},\n  title={T},\n  note = {N},\n}"


def test_replace_existing_field_value_with_nested_braces():
    raw = "@article{k,\n  comment = {A, {B}},\n  year = {2020}\n}"
    out = db.patch_entry_text(raw, {"comment": "A, {B}, C"}, [])
    assert out == "@article{k,\n  comment = {A, {B}, C},\n  year = {2020}\n}"


def test_patch_gives_up_on_quoted_values_and_the_entry_is_rewritten(tmp_path):
    bib = ('@article{a, author="X, Y", title="A study of forking in open source", year="2020", comment="T1"}\n\n'
           '@article{b, author="X, Y", title="A study of forking in open source", year="2020", comment="T2"}\n')
    assert db.patch_entry_text(bib.splitlines()[0], {"comment": "T1, T2"}, []) is None
    code, out, _ = run(tmp_path, bib, "--apply", "--no-backup")
    assert code == 0
    items, _ = items_of(out)
    assert len(items) == 1 and items[0].fields["comment"] == "T1, T2"


def test_untouched_entries_stay_byte_identical(tmp_path):
    code, out, _ = run(tmp_path, SAME_WORK, "--apply", "--no-backup")
    assert code == 0
    other = SAME_WORK[SAME_WORK.index("@article{Other2020"):SAME_WORK.index("@inproceedings{Teixeira2026")].rstrip()
    assert other in out and out.startswith("\n% header comment\n")
    assert "Teixeira2026" not in out
    assert "comment   = {TOP_IS_C, FT50}," in out and "location  = {Lisbon, Portugal}," in out


def test_entries_at_the_end_of_the_file_leave_one_final_newline(tmp_path):
    bib = ("@article{a, author={X, Y}, title={A study of forking in open source}, year={2020}, doi={10.1000/z}}\n\n"
           "% trailing comment\n\n"
           "@article{b, author={X, Y}, title={A study of forking in open source}, year={2020}}\n\n"
           "@article{c, author={X, Y}, title={A study of forking in open source}, year={2020}}\n\n\n")
    code, out, _ = run(tmp_path, bib, "--apply", "--no-backup")
    assert code == 0 and out.endswith("% trailing comment\n")


def test_crlf_line_endings_are_preserved(tmp_path):
    bib = SAME_WORK.strip().replace("\n", "\r\n") + "\r\n"
    code, out, _ = run(tmp_path, bib, "--apply", "--no-backup")
    assert code == 0 and "Teixeira2026" not in out
    assert out.count("\r\n") > 10 and "\n" not in out.replace("\r\n", "")


def test_unexaminable_blocks_are_left_alone(tmp_path):
    bib = SAME_WORK + '\n@article{broken, title = {no closing brace, year = {2020}\n'
    code, out, _ = run(tmp_path, bib, "--apply", "--no-backup")
    assert code == 0 and "@article{broken" in out and "Teixeira2026" not in out


# =============================================================================
# Command line
# =============================================================================

def test_dry_run_writes_nothing(tmp_path, capsys):
    code, out, path = run(tmp_path, SAME_WORK)
    assert code == 0 and out == SAME_WORK
    assert not list(tmp_path.glob("*.bak"))
    assert "Dry run" in capsys.readouterr().out


def test_apply_makes_a_backup_and_second_run_is_clean(tmp_path):
    code, out, path = run(tmp_path, SAME_WORK, "--apply")
    assert code == 0 and (tmp_path / "t.bib.bak").read_text(encoding="utf-8") == SAME_WORK
    assert db.main([str(path), "--check"]) == 0


def test_output_option_leaves_the_original_alone(tmp_path):
    target = tmp_path / "clean.bib"
    code, out, _ = run(tmp_path, SAME_WORK, "-o", str(target))
    assert code == 0 and out == SAME_WORK
    assert "Teixeira2026" not in target.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.bak"))


def test_check_exits_2_and_never_writes(tmp_path):
    code, out, _ = run(tmp_path, SAME_WORK, "--check")
    assert code == 2 and out == SAME_WORK
    code, _, _ = run(tmp_path, "@article{x, author={A, B}, title={Only entry here in file}, year={2020}}\n",
                     "--check", name="clean.bib")
    assert code == 0


def test_check_rejects_write_options(tmp_path):
    assert run(tmp_path, SAME_WORK, "--check", "--apply")[0] == 1
    assert run(tmp_path, SAME_WORK, "--apply", "-o", str(tmp_path / "x.bib"))[0] == 1


def test_collisions_are_reported_not_touched(tmp_path):
    bib = """
    @article{k, author={A, B}, title={First paper about forks}, year={2019}, doi={10.1000/a}}

    @article{k, author={C, D}, title={Something else entirely about money}, year={2020}, doi={10.1000/b}}
    """
    code, out, _ = run(tmp_path, bib, "--apply", "--no-backup")
    assert code == 0 and out == bib
    assert run(tmp_path, bib, "--check")[0] == 2


def test_json_and_key_map(tmp_path, capsys):
    km = tmp_path / "keys.tsv"
    code, _, _ = run(tmp_path, SAME_WORK, "--json", "--key-map", str(km))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["duplicate_groups"] == 1 and payload["entries_removed"] == 0
    group = payload["files"][0]["groups"][0]
    assert group["kept"] == "a2026lessons" and group["removed"][0]["key"] == "Teixeira2026"
    assert group["tags_added"] == ["FT50"]
    assert km.read_text(encoding="utf-8").splitlines()[1].startswith("Teixeira2026\ta2026lessons\t")


def test_markdown_report(tmp_path):
    rep = tmp_path / "r.md"
    run(tmp_path, SAME_WORK, "--report", str(rep))
    text = rep.read_text(encoding="utf-8")
    assert "`a2026lessons`" in text and "`Teixeira2026`" in text and "comment +FT50" in text


def test_across_files_reports_but_never_removes(tmp_path, capsys):
    one = tmp_path / "one.bib"
    two = tmp_path / "two.bib"
    entry = "@article{%s, author={X, Y}, title={A study of forking in open source}, year={2020}}\n"
    one.write_text(entry % "k1", encoding="utf-8")
    two.write_text(entry % "k2", encoding="utf-8")
    code = db.main([str(one), str(two), "--across-files", "--apply", "--no-backup", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["duplicate_groups"] == 0
    assert len(payload["across_files"]) == 1 and len(payload["across_files"][0]) == 2
    assert one.read_text(encoding="utf-8") == entry % "k1" and two.read_text(encoding="utf-8") == entry % "k2"


def test_bad_input(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("x")
    assert db.main([str(notes)]) == 1
    assert db.main([str(tmp_path)]) == 1                           # directory without --recursive


def test_gha_annotations(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    run(tmp_path, SAME_WORK, "--gha")
    assert "::warning file=" in capsys.readouterr().out
    assert "## dedup-bib" in (tmp_path / "s.md").read_text(encoding="utf-8")
