"""Benchmark labels are read without the planner: question + expected_documents only. Invented data."""

import pytest

from artmind import benchmark_labels as bl


def _write(tmp_path, text):
    p = tmp_path / "plan_labels.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_reads_question_and_expected_documents_and_ignores_planner_keys(tmp_path):
    p = _write(tmp_path, """
version: 2
labels:
  - id: L001
    question: Which invented policy applies?
    route: episode
    follows: none
    intent: retrieve
    expected_documents: [policies/alpha.md, beta.md]
  - id: L002
    question: No documents here?
""")
    labels = bl.load_labels(p)
    assert [(x.id, x.question, x.expected_documents) for x in labels] == [
        ("L001", "Which invented policy applies?", ("policies/alpha.md", "beta.md")),
        ("L002", "No documents here?", ())]


@pytest.mark.parametrize("text, msg", [
    ("labels: 3", "labels"),
    ("labels:\n  - id: L1\n", "no question"),
    ("labels:\n  - question: q\n    expected_documents: [1]\n", "expected_documents"),
    ("labels: [", "plan_labels.yaml"),
])
def test_bad_files_raise(tmp_path, text, msg):
    with pytest.raises(bl.LabelError, match=msg):
        bl.load_labels(_write(tmp_path, text))


def test_document_matching():
    assert bl.match_kind("policies/alpha.md", {"path": "vault/policies/alpha.md"}) == "path"
    assert bl.match_kind("x/alpha.md", {"path": "y/alpha.md"}) == "basename"
    assert bl.match_kind("x/alpha.md", {"title": "alpha"}) == "title"
    assert bl.match_kind("alpha.md", {"path": "beta.md"}) is None
    assert bl.reached_documents(["a.md", "b.md"], [{"path": "a.md"}]) == (["a.md"], ["b.md"])


def test_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("paths.ARTMIND_VAULT_DIR", str(tmp_path), raising=False)
    assert bl.vault_dir() == tmp_path
    assert bl.default_path() == tmp_path / "benchmarking" / "plan_labels.yaml"
