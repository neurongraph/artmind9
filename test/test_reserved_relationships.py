"""LLM-extracted relationships must never mint system-managed rel_type values,
and every extracted relationship is written as an immutable, chunk-scoped
`ASSERTS_RELATION` edge between the two `:Observation` nodes it connects —
never a direct `:Entity`-`:Entity` write (Phase 4: the projection rebuild is
what aggregates these into `RELATES_TO`; see `test_projection_merge.py` /
`projection.py` for that half).

SUPERSEDES is created ONLY by the audited helpers in artmind.temporal, which
always stamp provenance. RELATES_TO, ASSERTS_RELATION, and AGGREGATES are the
system's own collapsed-relationship machinery (Phase 4) — an LLM claiming one
of those as its own rel_type would be indistinguishable from the system's own
edges. PART_OF is deliberately NOT reserved: several shipped schemas list it
as a legitimate Entity->Entity relationship ("Branch X part_of Region Y"), and
the only structural PART_OF is the DocChunk->Document edge, a different code
path.

EXTRACTED_FROM changed meaning in Phase 3. It used to be an Entity->DocChunk
provenance edge written by this same loop; provenance now lives on the
immutable :Observation, which carries its own EXTRACTED_FROM to its DocChunk.
So the relationship writer skips the type entirely rather than reserving it —
see `test_extracted_from_is_written_by_observations_now`.

Endpoints are matched by resolving the extracted name against **this
document's own observations** (by chunk_id first, then doc-wide as a
fallback) — never by a live Entity lookup: the Entity's name is projection
output (the longest canonical name across its observations) and is frequently
not what any single chunk said, so a name match against Entity would silently
miss, and a live lookup couldn't see an entity this very commit just created.
"""
import json

import pytest

import artmind.ingest as ing
from artmind.observations import aggregate_key, relation_observation_id

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
        return [(c, kw) for c, kw in self.calls if "ASSERTS_RELATION" in c and "MERGE" in c]


def _observation(name, entity_class, **extra):
    obs = {
        "id": f"obs-{name}",
        "name": name,
        "canonical_name": name,
        "entity_class": entity_class,
        "domain": DOMAIN,
        "chunk_id": "c1",
        "doc_id": "d1",
        "_kind": "recurrent",
    }
    obs.update(extra)
    # `key` is derived from canonical_name last, like the real
    # build_observation does — so a fixture that overrides canonical_name
    # (e.g. to simulate two raw names folding onto one canonical entity)
    # gets a key consistent with that override, not with the raw `name`.
    obs["key"] = "|".join(aggregate_key(obs["canonical_name"], obs["entity_class"], obs["domain"]))
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
        {"source_name": "Rate A", "target_name": "Rate B", "rel_type": "supersedes", "chunk_id": "c1"},
        {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "higher_than", "chunk_id": "c1"},
    ])
    assert ing._write_to_neo4j(doc_kg_dir, DOMAIN) is not None

    written = {kw["rel_type"] for _, kw in session.rel_calls()}
    assert "SUPERSEDES" not in written
    assert written == {"HIGHER_THAN"}
    assert any("SUPERSEDES" in str(a) and "Rate A" in str(a) for a in warnings)


def test_a_system_owned_rel_type_is_also_reserved(session, tmp_path):
    """RELATES_TO/ASSERTS_RELATION/AGGREGATES are the system's own collapsed-
    relationship machinery (Phase 4) — an extractor must never claim one."""
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "relates_to", "chunk_id": "c1"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)
    assert session.rel_calls() == []


def test_endpoints_are_resolved_against_this_documents_own_observations(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "higher_than", "chunk_id": "c1"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    cypher, kwargs = session.rel_calls()[0]
    assert kwargs["src"] == "obs-Branch X"
    assert kwargs["tgt"] == "obs-Branch Y"
    assert "{id: $src}" in cypher and "{id: $tgt}" in cypher
    assert "name: $src" not in cypher, "matching by name would silently miss"
    assert kwargs["id"] == relation_observation_id("c1", "obs-Branch X", "HIGHER_THAN", "obs-Branch Y")
    assert kwargs["doc_id"] == "d1"
    assert kwargs["chunk_id"] == "c1"


def test_a_canonicalized_name_still_resolves_its_endpoint(session, tmp_path):
    """The chunk said one thing, canonicalization decided another; the edge the
    chunk asserted must still land on the right Observation."""
    observations = [
        _observation("Rate A", "RATE"),
        _observation("Rate B — 4.60% AER", "RATE", canonical_name="Rate B"),
    ]
    doc_kg_dir = _stage(
        tmp_path,
        [{"source_name": "Rate A", "target_name": "Rate B — 4.60% AER", "rel_type": "higher_tier_than", "chunk_id": "c1"}],
        observations=observations,
    )
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    _, kwargs = session.rel_calls()[0]
    assert kwargs["tgt"] == "obs-Rate B — 4.60% AER"


def test_extracted_from_is_written_by_observations_now(session, tmp_path):
    """Provenance moved to the immutable record. The relationship loop must not
    write an Entity->DocChunk EXTRACTED_FROM at all."""
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Rate A", "target_id": "c1", "rel_type": "extracted_from", "chunk_id": "c1"},
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
        _observation("Rate A alias", "RATE", canonical_name="Rate A"),
    ]
    doc_kg_dir = _stage(
        tmp_path,
        [{"source_name": "Rate A", "target_name": "Rate A alias", "rel_type": "higher_than", "chunk_id": "c1"}],
        observations=observations,
    )
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)
    assert session.rel_calls() == []


def test_an_edge_to_an_unknown_endpoint_is_skipped(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Rate A", "target_name": "Never Extracted", "rel_type": "higher_than", "chunk_id": "c1"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)
    assert session.rel_calls() == []


def test_a_bidirectional_edge_writes_both_directions(session, tmp_path):
    doc_kg_dir = _stage(tmp_path, [
        {"source_name": "Branch X", "target_name": "Branch Y",
         "rel_type": "higher_than", "bidirectional": True, "chunk_id": "c1"},
    ])
    ing._write_to_neo4j(doc_kg_dir, DOMAIN)

    pairs = {(kw["src"], kw["tgt"]) for _, kw in session.rel_calls()}
    assert pairs == {("obs-Branch X", "obs-Branch Y"), ("obs-Branch Y", "obs-Branch X")}
