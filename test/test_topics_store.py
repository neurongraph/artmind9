import pytest

from artmind.topics import store

META = {"artmind_topic": 1, "topic": "Termination rights", "slug": "termination-rights",
        "domains": ["contracts"], "anchor": {"kind": "topic"}, "built": "2026-11-02T10:00:00Z",
        "sources": [{"doc_id": "d1", "name": "a.md", "content_sha": "v1", "passages": ["c1_p01"]},
                    {"doc_id": "d2", "name": "b.md", "content_sha": "v2", "passages": ["c2_p01"]}],
        "checks": {"uncited_paragraphs": 0, "unsupported_numbers": 0, "removed_citations": 0}}


@pytest.mark.parametrize("topic, slug", [("Termination rights", "termination-rights"),
                                         ("  Q3 / Q4: risks!  ", "q3-q4-risks"), ("***", "topic")])
def test_slugify(topic, slug):
    assert store.slugify(topic) == slug


def test_write_then_read_round_trips(tmp_path):
    path = store.write_page(tmp_path, "termination-rights", META, "## Overview\nText [#c1_p01]")
    page = store.read_page(tmp_path, "termination-rights")
    assert path == tmp_path / "termination-rights.md"
    assert page.meta == META and page.body == "## Overview\nText [#c1_p01]" and page.notes == ""
    text = path.read_text()
    assert text.startswith("---\n") and store.BANNER in text and text.rstrip().endswith(store.NOTES_HEADING)


def test_my_notes_survive_a_rebuild(tmp_path):
    path = store.write_page(tmp_path, "t", META, "old body")
    path.write_text(path.read_text() + "Keep this.\n- and this\n")
    store.write_page(tmp_path, "t", META, "new body")
    page = store.read_page(tmp_path, "t")
    assert page.body == "new body" and page.notes == "Keep this.\n- and this\n"


def test_non_pages_are_ignored_by_list(tmp_path):
    store.write_page(tmp_path, "b", {**META, "slug": "b"}, "x")
    store.write_page(tmp_path, "a", {**META, "slug": "a"}, "x")
    (tmp_path / "mine.md").write_text("# my own note\n")
    (tmp_path / "broken.md").write_text("---\n: [\n---\n")
    assert [p.slug for p in store.list_pages(tmp_path)] == ["a", "b"]
    assert store.list_pages(tmp_path / "missing") == []


def test_delete(tmp_path):
    store.write_page(tmp_path, "t", META, "x")
    assert store.delete_page(tmp_path, "t") is True
    assert store.delete_page(tmp_path, "t") is False


def test_matching_needs_two_cited_documents_and_an_overlapping_domain(tmp_path):
    store.write_page(tmp_path, "t", META, "x")
    pages = store.list_pages(tmp_path)
    assert [(p.slug, n) for p, n in store.matching(pages, ["d1", "d2", "d9"], ["contracts"])] == [("t", 2)]
    assert store.matching(pages, ["d1", "d9"], ["contracts"]) == []
    assert store.matching(pages, ["d1", "d2"], ["journal"]) == []
    assert [p.slug for p, _ in store.matching(pages, ["d1", "d2"], ["contracts.msa"])] == ["t"]


def test_topics_dir_needs_out_outside_a_vault(monkeypatch, tmp_path):
    import paths

    monkeypatch.setattr(paths, "TOPICS_DIR", None)
    with pytest.raises(ValueError, match="--out"):
        store.topics_dir(None)
    assert store.topics_dir(tmp_path) == tmp_path
