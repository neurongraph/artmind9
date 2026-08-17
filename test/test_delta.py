"""A4 — three-tier delta classifier (ADR 0006 (f)).

Verifies the classifier's tier decisions and the metadata-only fast path.
Graph reads/writes are exercised through a recording session; no live Neo4j.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import artmind.delta as delta
import artmind.ingest as ing


# ── recording session (mirrors test_chunk_offsets.py / test_filing_metadata.py) ──


class _Rec:
    def __init__(self, single=None, data=None):
        self._single = single
        self._data = data or []

    def single(self):
        return self._single

    def data(self):
        return self._data


class _RecordingSession:
    def __init__(self, single_seq=None, data_seq=None, runs=None):
        self.runs = runs if runs is not None else []
        self._single_seq = list(single_seq or [])
        self._data_seq = list(data_seq or [])

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        if "MATCH (d:Document" in cypher and "RETURN" in cypher and "d.id" in cypher:
            single = self._single_seq.pop(0) if self._single_seq else None
            return _Rec(single=single)
        if "MATCH (c:DocChunk" in cypher and "RETURN c.block_hash" in cypher:
            data = self._data_seq.pop(0) if self._data_seq else []
            return _Rec(data=data)
        return _Rec()


class _SessionCtx:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        "artmind.graph_query.neo4j_session",
        lambda *a, **k: _SessionCtx(session),
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _write_md(path: Path, frontmatter: dict | None, body: str) -> None:
    parts = []
    if frontmatter is not None:
        import yaml
        parts.append("---")
        parts.append(yaml.safe_dump(frontmatter, sort_keys=False).strip())
        parts.append("---")
        parts.append("")
    parts.append(body)
    path.write_text("\n".join(parts), encoding="utf-8")


def _hashes_for(body: str) -> list[str]:
    return [c["block_hash"] for c in ing._split_markdown(body, chunk_size=6000)]


# ── classifier: tier decisions ─────────────────────────────────────────────


def test_initial_when_no_prior_document(tmp_path, monkeypatch):
    """No node in the graph for this logical_id → tier is 'initial'."""
    md = tmp_path / "note.md"
    _write_md(md, {"title": "New"}, "# First\n\nBody.")
    session = _RecordingSession(single_seq=[None])
    _patch_session(monkeypatch, session)

    result = delta.classify_reingest(md, domain="general", logical_id="lid-1")

    assert result.tier == "initial"
    assert result.doc_id is None
    assert result.version is None
    assert result.metadata["title"] == "New"


def test_metadata_only_when_body_block_hashes_match(tmp_path, monkeypatch):
    """Same body block-for-block, only frontmatter differs → 'metadata_only'."""
    body = "# Heading\n\nUnchanged prose that hashes the same both times.\n"
    md = tmp_path / "note.md"
    _write_md(md, {"title": "Renamed", "project": "artmind_canvas"}, body)
    prior_hashes = _hashes_for(body)

    session = _RecordingSession(
        single_seq=[{"id": "doc-abc", "version": 3, "title": "Old"}],
        data_seq=[[{"h": h} for h in prior_hashes]],
    )
    _patch_session(monkeypatch, session)

    result = delta.classify_reingest(md, domain="general", logical_id="lid-1")

    assert result.tier == "metadata_only"
    assert result.doc_id == "doc-abc"
    assert result.version == 3
    assert result.metadata["title"] == "Renamed"
    assert result.metadata["project"] == "artmind_canvas"


def test_content_when_body_differs(tmp_path, monkeypatch):
    """Any block_hash mismatch (edited content) → 'content'."""
    old_body = "# Heading\n\nOriginal prose.\n"
    new_body = "# Heading\n\nEdited prose.\n"
    md = tmp_path / "note.md"
    _write_md(md, {"title": "Same"}, new_body)
    prior_hashes = _hashes_for(old_body)

    session = _RecordingSession(
        single_seq=[{"id": "doc-abc", "version": 1}],
        data_seq=[[{"h": h} for h in prior_hashes]],
    )
    _patch_session(monkeypatch, session)

    result = delta.classify_reingest(md, domain="general", logical_id="lid-1")

    assert result.tier == "content"
    assert result.doc_id == "doc-abc"


def test_domain_change_short_circuits_to_domain_tier(tmp_path, monkeypatch):
    """A caller-declared domain change bypasses body comparison entirely."""
    body = "# Same\n\nBody.\n"
    md = tmp_path / "note.md"
    _write_md(md, {"title": "T"}, body)
    session = _RecordingSession(
        single_seq=[{"id": "doc-abc", "version": 2}],
    )
    _patch_session(monkeypatch, session)

    result = delta.classify_reingest(
        md, domain="banking", logical_id="lid-1", prior_domain="general"
    )

    assert result.tier == "domain"
    assert result.doc_id == "doc-abc"
    assert "banking" in result.reason


def test_content_when_prior_hash_lookup_fails(tmp_path, monkeypatch):
    """A prior-hash lookup that returns None degrades to content (safe default)."""
    md = tmp_path / "note.md"
    _write_md(md, {"title": "T"}, "# body\n\ntext\n")
    session = _RecordingSession(
        single_seq=[{"id": "doc-abc", "version": 1}],
        data_seq=[None],  # will not be popped
    )
    # Monkeypatch the block-hash lookup to simulate failure.
    monkeypatch.setattr(delta, "_read_prior_block_hashes", lambda doc_id: None)
    _patch_session(monkeypatch, session)

    result = delta.classify_reingest(md, domain="general", logical_id="lid-1")
    assert result.tier == "content"
    assert "lookup failed" in result.reason


# ── metadata extraction ────────────────────────────────────────────────────


def test_extract_metadata_handles_list_and_string_tags(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("body", encoding="utf-8")
    out = delta._extract_metadata(
        {"title": "T", "tags": ["a", "b"]}, src
    )
    assert out["title"] == "T"
    assert out["tags"] == ["a", "b"]

    out2 = delta._extract_metadata({"tags": "x, y ,z"}, src)
    assert out2["tags"] == ["x", "y", "z"]


def test_extract_metadata_defaults_temporal_from_filesystem(tmp_path):
    """When frontmatter omits created_on/modified_on, use filesystem times."""
    src = tmp_path / "note.md"
    src.write_text("body", encoding="utf-8")
    out = delta._extract_metadata({"title": "T"}, src)
    assert "modified_on" in out
    assert "created_on" in out


# ── metadata-only fast path ────────────────────────────────────────────────


def test_apply_metadata_only_sets_document_props_and_denorms_chunks(monkeypatch):
    """The fast path writes filing metadata to Document and denorms to DocChunks."""
    runs = []
    session = _RecordingSession(runs=runs)
    _patch_session(monkeypatch, session)

    result = delta.apply_metadata_only(
        doc_id="doc-abc",
        domain="general",
        metadata={
            "title": "New Title",
            "project": "canvas",
            "area": "eng",
            "tags": ["a", "b"],
            "modified_on": "2026-08-16T10:00:00+00:00",
        },
    )

    assert result["tier"] == "metadata_only"
    assert result["applied"]["title"] == "New Title"
    assert result["applied"]["project"] == "canvas"

    doc_runs = [(c, k) for c, k in runs if "MATCH (d:Document" in c and "SET d" in c]
    assert doc_runs, "expected a Document SET"
    _, kwargs = doc_runs[0]
    assert kwargs["props"]["title"] == "New Title"
    assert kwargs["props"]["project"] == "canvas"

    chunk_set_runs = [(c, k) for c, k in runs if "MATCH (c:DocChunk" in c and "SET c" in c]
    assert chunk_set_runs, "expected a DocChunk denormalization SET"
    _, kwargs = chunk_set_runs[0]
    assert kwargs["props"]["project"] == "canvas"
    assert kwargs["props"]["area"] == "eng"
    assert kwargs["props"]["tags"] == ["a", "b"]
    # `title`/`created_on`/`modified_on` are Document-only — never denormalized.
    assert "title" not in kwargs["props"]
    assert "modified_on" not in kwargs["props"]


def test_apply_metadata_only_removes_fields_absent_from_new_frontmatter(monkeypatch):
    """A field the user deleted from frontmatter comes off the node too."""
    runs = []
    session = _RecordingSession(runs=runs)
    _patch_session(monkeypatch, session)

    # Only `title` — everything else absent should be REMOVEd.
    delta.apply_metadata_only(
        doc_id="doc-abc",
        domain="general",
        metadata={"title": "Only title"},
    )

    doc_runs = [(c, k) for c, k in runs if "MATCH (d:Document" in c]
    assert doc_runs
    doc_cypher, _ = doc_runs[0]
    # Every non-title METADATA_FIELDS entry should show up as `d.x = null`.
    for field in ("project", "area", "tags", "created_on", "modified_on"):
        assert f"d.{field} = null" in doc_cypher

    chunk_rm_runs = [(c, k) for c, k in runs if "MATCH (c:DocChunk" in c and "REMOVE" in c]
    assert chunk_rm_runs
    rm_cypher, _ = chunk_rm_runs[0]
    for field in ("project", "area", "tags"):
        assert f"c.{field}" in rm_cypher


# ── integration: ingest_to_kg takes the fast path on metadata_only ─────────


def test_ingest_to_kg_takes_metadata_only_fast_path(tmp_path, monkeypatch):
    """A --replace re-ingest that classifies as metadata_only skips extract_kg
    and commit_to_graph entirely, going straight to apply_metadata_only."""
    body = "# Heading\n\nUnchanged prose.\n"
    md_dir = tmp_path / "markdowns"
    md_dir.mkdir()
    md_file = md_dir / "note.md"
    _write_md(md_file, {"title": "New title"}, body)

    # Point MARKDOWNS_DIR at our tmp dir so classify_reingest reads the fixture.
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", md_dir)

    # Stub the classifier to a metadata_only verdict and record the apply call.
    apply_calls = []

    def fake_apply(doc_id, domain, metadata):
        apply_calls.append((doc_id, domain, metadata))
        return {"tier": "metadata_only", "doc_id": doc_id, "applied": metadata, "removed": []}

    monkeypatch.setattr(
        "artmind.delta.classify_reingest",
        lambda *a, **k: delta.DeltaResult(
            tier="metadata_only",
            doc_id="doc-abc",
            version=2,
            metadata={"title": "New title"},
            reason="body unchanged",
        ),
    )
    monkeypatch.setattr("artmind.delta.apply_metadata_only", fake_apply)

    # extract_kg must NOT be called on the fast path.
    called_extract = {"n": 0}
    def fake_extract(*a, **k):
        called_extract["n"] += 1
        return tmp_path
    monkeypatch.setattr(ing, "extract_kg", fake_extract)

    file_result = {
        "registered_path": str(md_file.with_suffix(".md")),
        "logical_id": "lid-1",
    }

    ok = ing.ingest_to_kg(file_result, domain="general", replace=True)

    assert ok is True
    assert called_extract["n"] == 0, "extract_kg must not run on the metadata-only fast path"
    assert apply_calls == [("doc-abc", "general", {"title": "New title"})]


def test_ingest_to_kg_falls_through_when_classifier_returns_content(tmp_path, monkeypatch):
    """A 'content' verdict must fall through to the normal extract/commit pipeline."""
    body = "# Heading\n\nEdited prose.\n"
    md_dir = tmp_path / "markdowns"
    md_dir.mkdir()
    md_file = md_dir / "note.md"
    _write_md(md_file, {"title": "T"}, body)

    monkeypatch.setattr(ing, "MARKDOWNS_DIR", md_dir)
    monkeypatch.setattr(
        "artmind.delta.classify_reingest",
        lambda *a, **k: delta.DeltaResult(
            tier="content",
            doc_id="doc-abc",
            version=2,
            metadata={"title": "T"},
            reason="body changed",
        ),
    )

    # Stub the downstream calls so the test doesn't need Neo4j/LLM.
    called = {"extract": 0, "commit": 0, "apply": 0}
    monkeypatch.setattr(ing, "extract_kg", lambda *a, **k: (called.__setitem__("extract", called["extract"] + 1) or tmp_path))
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: (called.__setitem__("commit", called["commit"] + 1) or True))
    monkeypatch.setattr(
        "artmind.delta.apply_metadata_only",
        lambda *a, **k: called.__setitem__("apply", called["apply"] + 1),
    )

    file_result = {
        "registered_path": str(md_file.with_suffix(".md")),
        "logical_id": "lid-1",
        "chunks_dir": str(tmp_path / "chunks"),
    }
    (tmp_path / "chunks").mkdir()

    ok = ing.ingest_to_kg(file_result, domain="general", replace=True)

    assert ok is True
    assert called["extract"] == 1, "content verdict must run extract_kg"
    assert called["commit"] == 1, "content verdict must run commit_to_graph"
    assert called["apply"] == 0, "content verdict must NOT run apply_metadata_only"
