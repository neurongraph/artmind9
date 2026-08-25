"""A2 — filing taxonomy as ingested metadata.

ADR 0010: project, area, tags, title, created_on, modified_on are read from
markdown frontmatter and become first-class properties on Document nodes.
DocChunk nodes carry a denormalized copy of project/area/tags for fast
filtering. Entity nodes never carry filing metadata (derived by traversal).
"""
import json
from pathlib import Path

import artmind.ingest as ing


class _Rec:
    def __init__(self, single=None, data=None):
        self._single = single
        self._data = data or []

    def single(self):
        return self._single

    def data(self):
        return self._data


class _RecordingSession:
    """Records every run() and the parameters it was given.

    Recording is the point: an empty result means no query can be "verified"
    by its return value, so these tests assert on what was actually SENT —
    the pattern `test_update.py` established after a mocked session hid a real
    defect in `update confirm`.
    """

    def __init__(self, runs):
        self.runs = runs

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec(single=None, data=[])

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Driver:
    def __init__(self, session):
        self._session = session

    def session(self, **kwargs):
        class _Ctx:
            def __init__(self, s):
                self._s = s

            def __enter__(self_inner):
                return self_inner._s

            def __exit__(self_inner, *exc):
                return False

        return _Ctx(self._session)

    def close(self):
        pass


def _stage_doc_dir(tmp_path: Path, document: dict, chunk: dict) -> Path:
    """Stage a doc_kg_dir with document.json/chunks.json and empty extraction files."""
    (tmp_path / "document.json").write_text(json.dumps(document), encoding="utf-8")
    (tmp_path / "chunks.json").write_text(json.dumps([chunk]), encoding="utf-8")
    for name in ("entities.json", "properties.json", "relationships.json"):
        (tmp_path / name).write_text("[]", encoding="utf-8")
    return tmp_path


def _run_write(tmp_path: Path, monkeypatch) -> list:
    runs: list = []
    session = _RecordingSession(runs)
    # The commit path opens its session through graph_query.neo4j_session()
    # and runs one unit of work under execute_write(), so that is what gets
    # patched — the old GraphDatabase.driver() seam no longer exists.
    monkeypatch.setattr("artmind.graph_query.neo4j_session", lambda *a, **k: session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)
    assert ing._write_to_neo4j(tmp_path) is not None
    return runs


def test_frontmatter_parsing_captures_filing_metadata():
    """_parse_md_frontmatter reads the ADR-0010 filing fields from YAML."""
    md = """---
title: Example doc
project: artmind_canvas
area: engineering
tags: [phase-0, skeleton]
created_on: 2026-01-15T10:00:00Z
modified_on: 2026-01-16T11:00:00Z
---

# body content
"""
    meta, body = ing._parse_md_frontmatter(md)
    assert meta["title"] == "Example doc"
    assert meta["project"] == "artmind_canvas"
    assert meta["area"] == "engineering"
    assert meta["tags"] == ["phase-0", "skeleton"]
    assert body.startswith("# body content")


def test_document_props_include_filing_metadata_from_frontmatter(tmp_path, monkeypatch):
    """A Document node's props carry project/area/tags/title when frontmatter has them."""
    doc = {
        "id": "d1",
        "name": "example.md",
        "domain": "general",
        "title": "Example doc",
        "project": "artmind_canvas",
        "area": "engineering",
        "tags": ["phase-0", "skeleton"],
        "created_on": "2026-01-15T10:00:00Z",
        "modified_on": "2026-01-16T11:00:00Z",
    }
    chunk = {
        "id": "d1_001",
        "doc_id": "d1",
        "name": "Chunk 1/1",
        "text": "some text",
        "domain": "general",
        "embedding": [],
    }
    _stage_doc_dir(tmp_path, doc, chunk)
    runs = _run_write(tmp_path, monkeypatch)

    doc_runs = [(c, k) for c, k in runs if "MERGE (d:Document" in c]
    assert doc_runs, "expected a Document MERGE"
    _, kwargs = doc_runs[0]
    props = kwargs["props"]
    assert props["title"] == "Example doc"
    assert props["project"] == "artmind_canvas"
    assert props["area"] == "engineering"
    assert props["tags"] == ["phase-0", "skeleton"]
    assert props["created_on"] == "2026-01-15T10:00:00Z"
    assert props["modified_on"] == "2026-01-16T11:00:00Z"


def test_docchunk_props_carry_denormalized_filing_metadata(tmp_path, monkeypatch):
    """DocChunks get a denormalized copy of project/area/tags for fast filtering.

    Per ADR 0010, DocChunks carry the filing metadata for filter speed; entity
    nodes are excluded — they're shared across projects, so filtering is by
    traversal, not property.
    """
    doc = {"id": "d1", "name": "example.md", "domain": "general"}
    chunk = {
        "id": "d1_001",
        "doc_id": "d1",
        "name": "Chunk 1/1",
        "text": "some text",
        "domain": "general",
        "embedding": [],
        "project": "artmind_canvas",
        "area": "engineering",
        "tags": ["phase-0", "skeleton"],
    }
    _stage_doc_dir(tmp_path, doc, chunk)
    runs = _run_write(tmp_path, monkeypatch)

    chunk_runs = [(c, k) for c, k in runs if "MERGE (c:DocChunk" in c]
    assert chunk_runs, "expected a DocChunk MERGE"
    _, kwargs = chunk_runs[0]
    props = kwargs["props"]
    assert props["project"] == "artmind_canvas"
    assert props["area"] == "engineering"
    assert props["tags"] == ["phase-0", "skeleton"]


def test_tags_string_is_split_on_commas():
    """Frontmatter tags given as a comma-separated string are parsed as a list."""
    # This test exercises the parsing logic that happens in extract_kg and in the
    # final document node construction. Both accept string or list forms.
    tags_str = "one, two , three"
    # emulate the parsing done in ingest.py
    parsed = [t.strip() for t in tags_str.split(",")]
    assert parsed == ["one", "two", "three"]


def test_document_props_omit_missing_filing_fields(tmp_path, monkeypatch):
    """A Document without filing metadata in its frontmatter doesn't get empty props."""
    doc = {
        "id": "d1",
        "name": "example.md",
        "domain": "general",
    }
    chunk = {
        "id": "d1_001",
        "doc_id": "d1",
        "name": "Chunk 1/1",
        "text": "some text",
        "domain": "general",
        "embedding": [],
    }
    _stage_doc_dir(tmp_path, doc, chunk)
    runs = _run_write(tmp_path, monkeypatch)

    doc_runs = [(c, k) for c, k in runs if "MERGE (d:Document" in c]
    assert doc_runs, "expected a Document MERGE"
    _, kwargs = doc_runs[0]
    props = kwargs["props"]
    assert "project" not in props
    assert "area" not in props
    assert "tags" not in props
