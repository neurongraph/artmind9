"""leakcheck: added checkout lines that carry a dossier's private evidence."""

import subprocess

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews.leakcheck import LeakcheckError, leakcheck


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "checkout"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "app.py").write_text("print('hello')\n")
    _git(r, "add", "app.py")
    _git(r, "commit", "-q", "-m", "init")
    return r


def _dossier():
    sections = {t: "" for t in dz.SECTIONS}
    sections["Symptom"] = "**Question**\n\n> How often did Person Q travel to Kestrel Bay last spring?\n"
    sections["Shape audit"] = ("| element | check | source sentence | why |\n|---|---|---|---|\n"
                               "| d/Trip | coverage | took the slow ferry across the northern sound | count |")
    sections["Diagnosis"] = "The model looked at the wrong pattern for this question entirely."  # prose, not quoted
    return dz.Dossier(meta={"review_id": "rv-2026-01-02-abcdef", "private_terms": ["Person Q", "Kestrel Bay", "ab"]},
                      sections=sections)


def test_clean_diff_passes(repo):
    (repo / "app.py").write_text("print('hello')\nprint('Person X travels')\n")
    result = leakcheck(_dossier(), repo)
    assert result["ok"] is True and result["hits"] == []


def test_term_in_modified_file_is_a_hit(repo):
    (repo / "app.py").write_text("print('hello')\nCITY = 'kestrel bay'\n")
    result = leakcheck(_dossier(), repo)
    assert not result["ok"]
    assert result["hits"] == [{"file": "app.py", "line": 2, "kind": "term", "match": "Kestrel Bay"}]


def test_quoted_five_gram_in_untracked_file_is_a_hit(repo):
    (repo / "test_new.py").write_text("# fixture: Took the slow ferry across the northern sound\n")
    result = leakcheck(_dossier(), repo)
    assert [(h["file"], h["kind"]) for h in result["hits"]] == [("test_new.py", "quote")]


def test_unquoted_prose_and_short_terms_are_ignored(repo):
    (repo / "app.py").write_text("print('hello')\n# looked at the wrong pattern for this question\nab = 1\n")
    assert leakcheck(_dossier(), repo)["ok"] is True


def test_staged_changes_count_and_review_folder_is_skipped(repo):
    (repo / "staged.py").write_text("WHO = 'Person Q'\n")
    _git(repo, "add", "staged.py")
    (repo / ".artmind" / "reviews").mkdir(parents=True)
    (repo / ".artmind" / "reviews" / "rv.md").write_text("Person Q\n")
    hits = leakcheck(_dossier(), repo)["hits"]
    assert [h["file"] for h in hits] == ["staged.py"]


def test_committed_changes_against_a_base(repo):
    (repo / "app.py").write_text("print('hello')\nWHERE = 'Kestrel Bay'\n")
    _git(repo, "commit", "-q", "-am", "leak")
    assert leakcheck(_dossier(), repo)["ok"] is True          # nothing uncommitted vs HEAD
    assert not leakcheck(_dossier(), repo, base="HEAD~1")["ok"]


def test_bad_base_raises(repo):
    with pytest.raises(LeakcheckError):
        leakcheck(_dossier(), repo, base="no-such-ref")


def test_non_ascii_path_is_detected(repo):
    """git quotes non-ASCII paths by default; without core.quotePath=false the
    file is silently skipped and a real private-term hit is missed."""
    fname = "résumé.py"
    (repo / fname).write_text("CITY = 'Kestrel Bay'\n")
    _git(repo, "add", fname)
    _git(repo, "commit", "-q", "-m", "add resume")
    result = leakcheck(_dossier(), repo, base="HEAD~1")
    assert result["hits"], "expected at least one hit for non-ASCII path"
    assert result["hits"][0]["file"] == fname


def test_shape_table_structure_is_not_quoted_text_only_its_source_sentences(repo):
    d = _dossier()
    d.sections["Shape audit"] = ("| element | check | source sentence | why the question needs it |\n|---|---|---|---|\n"
                                 "| d/PERSON-WENT_TO-> | coverage | I went over the old bridge at dawn. | count |")
    # same element/check cells and table header in a test fixture: structure, not evidence
    (repo / "t.py").write_text('AUDIT = "| element | check | source sentence | why |"\n'
                               'ROW = "| j/PERSON-WENT_TO-> | coverage | I went to a fake town. |"\n')
    assert leakcheck(d, repo)["ok"] is True
    (repo / "t.py").write_text('ROW = "I went over the old bridge at dawn"\n')
    assert [h["kind"] for h in leakcheck(d, repo)["hits"]] == ["quote"]


def test_five_grams_do_not_span_two_quoted_lines(repo):
    d = _dossier()
    d.sections["Symptom"] = "> We crossed the long valley in the morning.\n>\n> I went back to fetch the old map."
    (repo / "t.py").write_text('S = "in the morning I went"\n')       # end of one quote + start of the next
    assert leakcheck(d, repo)["ok"] is True
    (repo / "t.py").write_text('S = "crossed the long valley in"\n')  # inside one quote
    assert [h["kind"] for h in leakcheck(d, repo)["hits"]] == ["quote"]
