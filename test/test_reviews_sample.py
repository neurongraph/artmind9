"""Proactive shape sampling: random chunks of a domain, mechanical signals."""

import json
from contextlib import contextmanager

import pytest

from artmind.reviews import sample


@pytest.fixture(autouse=True)
def _staging(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    for i in range(1, 4):
        folder = tmp_path / "kg" / "j" / f"2031-01-0{i}"
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"id": f"doc-{i}", "path": f"j/2031-01-0{i}.md"}))
        (folder / "chunks.json").write_text(json.dumps([{"id": f"doc-{i}_001", "text": "Mira met Arlo. They sailed."}]))
        (folder / "observations.json").write_text(json.dumps([
            {"name": "Mira", "canonical_name": "Mira Venn", "entity_class": "PERSON", "chunk_id": f"doc-{i}_001"},
            {"name": "Arlo", "entity_class": "PERSON", "chunk_id": f"doc-{i}_001"},
            {"name": "Harbour", "entity_class": "LOCATION", "chunk_id": f"doc-{i}_001"}]))
        (folder / "relationships.json").write_text(json.dumps([
            {"source_name": "Mira", "target_name": "Arlo", "rel_type": "meets", "chunk_id": f"doc-{i}_001"}]))


@pytest.fixture
def fake_graph(monkeypatch):
    from artmind import graph_query

    class _Result:
        def data(self):
            return [{"entity": {"_id": "e1", "name": "Mira Venn", "entity_class": "PERSON"}, "rels": []},
                    {"entity": {"_id": "e2", "name": "Harbour", "entity_class": "LOCATION"}, "rels": []}]

    class _Session:
        def run(self, cypher, **params):
            return _Result()

    @contextmanager
    def fake_read_session():
        yield _Session()

    monkeypatch.setattr(graph_query, "read_session", fake_read_session)


def test_all_chunks():
    assert [cid for cid, _ in sample.all_chunks("j")] == ["doc-1_001", "doc-2_001", "doc-3_001"]
    assert sample.all_chunks("missing") == []


def test_signals():
    staging = {"observations": [{"name": "Mira", "canonical_name": "Mira Venn", "entity_class": "PERSON"},
                                {"name": "Arlo", "entity_class": "PERSON"},
                                {"name": "Harbour", "entity_class": "LOCATION"}],
               "relationships": [{"source_name": "Mira", "target_name": "Arlo", "rel_type": "meets"}]}
    graph = {"nodes": [{"name": "Mira Venn", "extracted_here": True}, {"name": "Harbour", "extracted_here": True},
                       {"name": "Arlo", "extracted_here": False}]}
    assert sample.signals(staging, graph) == {"lost_at_commit": ["Arlo"], "isolated": ["Harbour"]}
    assert sample.signals(staging, None) == {"isolated": ["Harbour"]}


def test_sample_is_seeded_and_bounded(fake_graph):
    a = sample.sample("j", 2, seed=7)
    b = sample.sample("j", 2, seed=7)
    assert [s["chunk_id"] for s in a["sampled"]] == [s["chunk_id"] for s in b["sampled"]]
    assert a["population"] == 3 and len(a["sampled"]) == 2
    first = a["sampled"][0]
    assert first["observations"] == 3 and first["relationships"] == 1 and first["graph_entities"] == 2
    assert first["signals"] == {"lost_at_commit": ["Arlo"], "isolated": ["Harbour"]}
    assert first["next"].startswith("artmind review shape --domain j --chunk doc-")
    assert len(sample.sample("j", 10, seed=1)["sampled"]) == 3


def test_sample_without_graph():
    out = sample.sample("j", 1, seed=1, with_graph=False)
    assert out["sampled"][0]["graph_entities"] is None
    assert out["sampled"][0]["signals"] == {"isolated": ["Harbour"]}
