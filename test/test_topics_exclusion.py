from pathlib import Path

from artmind import ingest


def test_topics_folders_are_never_walked(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("x")
    (tmp_path / "_Topics").mkdir()
    (tmp_path / "_Topics" / "page.md").write_text("x")
    (tmp_path / "deep" / "_Topics").mkdir(parents=True)
    (tmp_path / "deep" / "_Topics" / "p.md").write_text("x")
    assert [p.name for p in ingest.collect_ingest_files(tmp_path)] == ["a.md"]


def test_a_named_topic_page_is_refused_too(tmp_path):
    page = tmp_path / "_Topics" / "page.md"
    page.parent.mkdir()
    page.write_text("x")
    assert ingest.collect_ingest_files(page) == []
    assert ingest.collect_ingest_files(page.parent) == []


def test_paths_names_the_topics_folder():
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    assert paths.TOPICS_DIR == (Path(vault) / "_Topics" if vault else None)
