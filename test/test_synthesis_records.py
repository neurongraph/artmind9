"""A synthesis travels as a curation record (spec 2026-09-26 §15 A10). No
Neo4j: recording sessions stand in (CLAUDE.md §3)."""
import hashlib
import json
from unittest.mock import MagicMock, patch

import neo4j
import pytest

from artmind import curation_records, projection, synthesis_records
from artmind.observations import entity_id


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return MagicMock()


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def data(self):
        return [dict(r) for r in self._rows]

    def single(self):
        return self._rows[0] if self._rows else None


class _Graph:
    """Holds `:Synthesis` nodes: a write stores the properties it was sent, a
    read answers what the real statements return -- `.single()` a
    `neo4j.Record` (a tuple with a mapping interface, not a dict), `.data()`
    plain dicts."""

    def __init__(self, nodes=None):
        self.nodes = dict(nodes or {})
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if cypher == "MERGE (s:Synthesis {id: $id}) SET s = $props":
            self.nodes[params["id"]] = dict(params["props"])
        elif cypher == "MATCH (s:Synthesis {id: $id}) DETACH DELETE s":
            self.nodes.pop(params["id"], None)
        elif cypher == "MATCH (s:Synthesis {id: $id}) RETURN s.key AS key":
            node = self.nodes.get(params["id"])
            return _Result([neo4j.Record({"key": node.get("key")})] if node else [])
        elif "UNWIND $ids AS id MATCH (s:Synthesis" in cypher:
            return _Result([{"id": i, "p": self.nodes[i]} for i in params["ids"] if i in self.nodes])
        return _Result()


_KEY = ("widget rate", "RATE_ENTRY", "banking.reference")
_SYNTHESIS = {"id": entity_id(_KEY), "text": "A clean description.", "observation_set_hash": "h",
              "observation_ids": ["o1", "o2"], "created_at": "t", "model": "m"}


def test_a_synthesis_record_is_the_node_plus_its_key():
    record = synthesis_records.record_from(_KEY, _SYNTHESIS)

    assert record == {**_SYNTHESIS, "key": "widget rate|RATE_ENTRY|banking.reference"}
    assert "embedding" not in record


def test_apply_writes_the_node_and_hands_back_its_key():
    tx = _Tx()
    record = synthesis_records.record_from(_KEY, _SYNTHESIS)

    assert synthesis_records.apply(tx, record, "fp") == {_KEY}
    assert tx.calls == [("MERGE (s:Synthesis {id: $id}) SET s = $props",
                         {"id": entity_id(_KEY), "props": {**record, "record_fingerprint": "fp"}})], (
        "one statement writes the content and the fingerprint together, so the stamp can never exist without "
        "the node -- and the node needs no entity to exist first (the rebuild it joins creates the entity)"
    )


def test_remove_deletes_the_node_and_hands_back_its_key():
    tx = _Tx()

    assert synthesis_records.remove(tx, synthesis_records.record_from(_KEY, _SYNTHESIS)) == {_KEY}
    assert tx.calls == [("MATCH (s:Synthesis {id: $id}) DETACH DELETE s", {"id": entity_id(_KEY)})]


def test_removing_from_a_stub_record_finds_the_key_on_the_node_before_deleting_it():
    """The record's base blob was unreadable, so only `{"id": ...}` is known:
    the entity's key is read off the node (which carries it), then the node
    goes -- otherwise nothing would rebuild the entity and it would keep the
    removed synthesis as its description."""
    graph = _Graph({entity_id(_KEY): {**synthesis_records.record_from(_KEY, _SYNTHESIS), "record_fingerprint": "fp"}})

    assert synthesis_records.remove(graph, {"id": entity_id(_KEY)}) == {_KEY}

    assert graph.calls == [
        ("MATCH (s:Synthesis {id: $id}) RETURN s.key AS key", {"id": entity_id(_KEY)}),
        ("MATCH (s:Synthesis {id: $id}) DETACH DELETE s", {"id": entity_id(_KEY)}),
    ]
    assert graph.nodes == {}


def test_removing_a_stub_whose_node_is_gone_or_has_no_key_hands_back_nothing():
    absent = _Graph()
    keyless = _Graph({"x": {"id": "x", "text": "t"}})

    assert synthesis_records.remove(absent, {"id": "x"}) == set()
    assert synthesis_records.remove(keyless, {"id": "x"}) == set()

    assert [c for c, _ in absent.calls][-1] == "MATCH (s:Synthesis {id: $id}) DETACH DELETE s", "an absent node: the delete still runs, harmlessly"
    assert keyless.nodes == {}


def test_the_syntheses_kind_runs_before_the_rebuild():
    kind = curation_records.kinds()["syntheses"]

    assert kind.phase == "pre"
    assert kind.reapply_for is None
    assert kind.domains(synthesis_records.record_from(_KEY, _SYNTHESIS)) == ["banking.reference"]
    assert list(curation_records.kinds()) == ["lifecycle", "supersessions", "syntheses", "conflicts"]


def test_an_applied_synthesis_is_what_the_rebuild_reads_back_as_the_description():
    """`projection.load_synthesis_batch` is the rebuild's synthesis_loader:
    what `apply` wrote (the record's extra `key` and `record_fingerprint`
    included) must come back through it and win the merge -- with no
    embedding anywhere in the record."""
    graph = _Graph()
    record = synthesis_records.record_from(_KEY, {
        **_SYNTHESIS, "observation_ids": ["o1"],
        "observation_set_hash": hashlib.sha256(b"o1").hexdigest(),
    })

    synthesis_records.apply(graph, record, "fp")
    loaded = projection.load_synthesis_batch(graph, [_KEY])

    assert list(loaded) == [_KEY]
    observation = {"id": "o1", "canonical_name": "widget rate", "entity_class": "RATE_ENTRY",
                   "_domain": "banking.reference", "description": "the observation's own words"}
    merged = projection.merge_observations([observation], synthesis=loaded[_KEY], override_key=_KEY)
    assert merged["props"]["description"] == "A clean description."
    assert merged["props"]["_description_source"] == "synthesis"
    assert "_description_stale" not in merged["props"]
    assert all("embedding" not in node for node in graph.nodes.values())


def test_synthesize_key_writes_the_record_and_applies_it(monkeypatch, curation_dir):
    from artmind.synthesize import synthesize_key

    monkeypatch.setattr("artmind.synthesize.projection.read_latest_observations",
                        lambda tx, key: [{"id": "o1", "description": "x"}, {"id": "o2"}])
    monkeypatch.setattr("artmind.synthesize.call_llm", lambda model, prompt: "{}")
    monkeypatch.setattr("artmind.synthesize.parse_json_response", lambda raw: {"description": "A clean description."})
    monkeypatch.setattr("artmind.synthesize.embed_text", lambda model, text: [0.1])
    seen = {}
    monkeypatch.setattr("artmind.synthesize.projection.rebuild_key",
                        lambda tx, key, *, synthesis=None, **kw: seen.setdefault("synthesis", synthesis) and "rebuilt")
    tx = _Tx()
    session = MagicMock()
    session.execute_read.side_effect = lambda fn: fn(tx)
    session.execute_write.side_effect = lambda fn: fn(tx)

    with patch("artmind.synthesize.neo4j_session") as ctx:
        ctx.return_value.__enter__.return_value = session
        synthesize_key(_KEY, "Widget Rate", "RATE_ENTRY", model="m", embed_model="e")

    path = curation_dir / "syntheses" / f"{entity_id(_KEY)}.json"
    record = json.loads(path.read_text())
    assert (record["text"], record["key"]) == ("A clean description.", "widget rate|RATE_ENTRY|banking.reference")
    assert "embedding" not in record
    writes = [p for c, p in tx.calls if c == "MERGE (s:Synthesis {id: $id}) SET s = $props"]
    assert writes[0]["props"]["record_fingerprint"] == curation_records.fingerprint(path.read_bytes())
    assert seen["synthesis"]["text"] == "A clean description."
