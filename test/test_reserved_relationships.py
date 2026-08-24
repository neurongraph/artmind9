"""LLM-extracted relationships must never mint system-managed edge types.

SUPERSEDES is created ONLY by the audited helpers in artmind.temporal, which
always stamp provenance. PRIOR_STATE links a live Entity to an :EntityVersion
snapshot and is written only by artmind.entity_history, so an LLM-minted one
would imply history no snapshot node backs. PART_OF is deliberately NOT
reserved: several shipped schemas list it as a legitimate Entity->Entity
relationship ("Branch X part_of Region Y"), and the only structural PART_OF is
the DocChunk->Document edge, a different code path.

EXTRACTED_FROM changed meaning in Phase 3. It used to be an Entity->DocChunk
provenance edge written by this same loop; provenance now lives on the
immutable :Observation, which carries its own EXTRACTED_FROM to its DocChunk.
So the relationship writer skips the type entirely rather than reserving it —
see `test_extracted_from_is_written_by_observations_now`.

Endpoints are matched by the deterministic ENTITY ID computed from the
extracted name's aggregate key, never by `(name, domain)`: the Entity's name is
projection output (the longest canonical name across its observations) and is
frequently not what any single chunk said, so a name match would silently miss.
"""
import json

import pytest

import artmind.ingest as ing
from artmind.observations import aggregate_key, entity_id

DOMAIN = "banking.test"


class FakeResult:
    def single(self):
        return {"n": 0, "c": 0}

    def data(self):
        return []

    def consume(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult()

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def rel_calls(self):
        return [(c, kw) for c, kw in self.calls if "apoc.merge.relationship" in c]


def _observation(name, entity_class, **extra):
    key = aggregate_key(name, entity_class, DOMAIN)
    obs = {
        "id": f"obs-{name}",
        "name": name,
        "canonical_name": name,
        "key": "|".join(key),
        "entity_class": entity_class,
        "domain": DOMAIN,
        "chunk_id": "c1",
        "doc_id": "d1",
        "_status": "latest",
        "_kind": "recurrent",
    }
    obs.update(extra)
    return obs


def _stage(tmp_path, relationships, observations=None):
    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "rates.md", "domain": DOMAIN}), encoding="utf-8"
    )
    (tmp_path / "chunks.json").write_text("[]", encoding="utf-8")
    (tmp_path / "observations.json").write_text(
        json.dumps(observations if observations is not None else [
            _observation("Rate A", "RATE"),
            _observation("Rate B", "RATE"),
            _observation("Branch X", "BRANCH"),
            _observation("Branch Y", "BRANCH"),
        ]),
        encoding="utf-8",
    )
    (tmp_path / "relationships.json").write_text(json.dumps(relationships), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def session(monkeypatch):
    s = FakeSession()
    monkeypatch.setattr("artmind.graph_query.neo4j_session", lambda *a, **k: s)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda *a, **k: 0)
    return s


def test_a_reserved_rel_type_is_skipped_and_the_normal_one_is_written(session, monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(ing.logger, "warning", lambda *a, **k: warnings.append(a))

    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Rate A", "target_name": "Rate B", "rel_type": "supersedes"},
        {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "relates_to"},
    ])
    assert ing._write_to_neo4j(doc_kg_dir, DOMAIN) is not None

    written = {kw["type"] for _, kw in session.rel_calls()}
    assert "SUPERSEDES" not in written
    assert written == {"RELATES_TO"}
    assert any("SUPERSEDES" in str(a) and "Rate A" in str(a) for a in warnings)


def test_endpoints_are_matched_by_deterministic_entity_id_not_by_name(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "relates_to"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    cypher, kwargs = session.rel_calls()[0]
    assert kwargs["src"] == entity_id(aggregate_key("Branch X", "BRANCH", DOMAIN))
    assert kwargs["tgt"] == entity_id(aggregate_key("Branch Y", "BRANCH", DOMAIN))
    assert "{id: $src}" in cypher and "{id: $tgt}" in cypher
    assert "name: $src" not in cypher, "matching by name would silently miss"


def test_a_canonicalized_name_still_resolves_its_endpoint(session, tmp_path):
    """The chunk said one thing, canonicalization decided another; the edge the
    chunk asserted must still land on the right Entity."""
    observations = [
        _observation("Rate A", "RATE"),
        {**_observation("Rate B", "RATE"),
         "name": "Rate B — 4.60% AER", "canonical_name": "Rate B"},
    ]
    doc_kg_dir = _stage(
        tmp_path,
        [{"source_name": "Rate A", "target_name": "Rate B — 4.60% AER", "rel_type": "higher_tier_than"}],
        observations=observations,
    )
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    _, kwargs = session.rel_calls()[0]
    assert kwargs["tgt"] == entity_id(aggregate_key("Rate B", "RATE", DOMAIN))


def test_extracted_from_is_written_by_observations_now(session, tmp_path):
    """Provenance moved to the immutable record. The relationship loop must not
    write an Entity->DocChunk EXTRACTED_FROM at all."""
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Rate A", "target_id": "c1", "rel_type": "extracted_from"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    assert session.rel_calls() == []
    # ...and the observation write is what carries it
    provenance = [
        c for c, _ in session.calls
        if "MERGE (o)-[:EXTRACTED_FROM]->(c)" in c
    ]
    assert provenance, "observations must carry their own EXTRACTED_FROM to the chunk"


def test_a_self_edge_is_skipped(session, tmp_path):
    """Two names that canonicalize to one thing must not produce a self-loop."""
    observations = [
        _observation("Rate A", "RATE"),
        {**_observation("Rate A alias", "RATE"), "canonical_name": "Rate A"},
    ]
    doc_kg_dir = _stage(
        tmp_path,
        [{"source_name": "Rate A", "target_name": "Rate A alias", "rel_type": "relates_to"}],
        observations=observations,
    )
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)
    assert session.rel_calls() == []


def test_an_edge_to_an_unknown_endpoint_is_skipped(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Rate A", "target_name": "Never Extracted", "rel_type": "relates_to"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)
    assert session.rel_calls() == []


def test_a_bidirectional_edge_writes_both_directions(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Branch X", "target_name": "Branch Y",
         "rel_type": "relates_to", "bidirectional": True},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    pairs = {(kw["src"], kw["tgt"]) for _, kw in session.rel_calls()}
    x = entity_id(aggregate_key("Branch X", "BRANCH", DOMAIN))
    y = entity_id(aggregate_key("Branch Y", "BRANCH", DOMAIN))
    assert pairs == {(x, y), (y, x)}
