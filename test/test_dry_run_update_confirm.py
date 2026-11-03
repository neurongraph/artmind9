"""`update confirm --dryRun`: resolved from reads alone, effect equal to the real run's."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import artmind.update as upd
from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli

ENTITIES = [
    {"temp_id": "e0", "name": "Alice Example", "entity_class": "PERSON", "properties": {}},
    {"temp_id": "e1", "name": "Acme Widgets", "entity_class": "ORG", "properties": {}},
    {"temp_id": "e2", "name": "Bob Sample", "entity_class": "PERSON", "properties": {}},
]
RELATIONSHIPS = [{"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "WORKS_FOR"}]
RESOLUTIONS = [
    {"entity_temp_id": "e0", "action": "link", "node_id": "node-alice"},
    {"entity_temp_id": "e1", "action": "create", "node_id": None},
    {"entity_temp_id": "e2", "action": "create", "node_id": None, "retracts": ["obs-old-1"]},
]


def _respond(cypher, params):
    cy = " ".join(cypher.split())
    if "WHERE elementId(e) = $ref" in cy:
        return [{"id": "eid-alice", "name": "Alice Example", "entity_class": "PERSON", "domain": "general",
                 "key": None}]
    return []


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    recorder = WritableRecorder(_respond)
    monkeypatch.setattr(upd, "neo4j_session", lambda *a, **k: recorder)
    monkeypatch.setattr(upd, "read_session", lambda *a, **k: recorder)
    monkeypatch.setattr(upd, "embed_missing_entity_embeddings", lambda *a, **k: 0)
    monkeypatch.setattr(upd, "_get_latest_pending_draft", lambda sid: {
        "id": 4, "raw_text": "Alice works for Acme. Bob left.", "domain": "general",
        "extraction_json": json.dumps({"entities": ENTITIES, "relationships": RELATIONSHIPS}),
    })
    status = []
    monkeypatch.setattr(upd, "_update_draft_status", lambda i, s: status.append(("draft", i, s)))
    monkeypatch.setattr(upd, "_update_session_status", lambda i, s: status.append(("session", i, s)))
    embeds = []
    monkeypatch.setattr(upd, "embed_document", lambda m, t: embeds.append(t) or [0.1] * 768)
    recorder.status, recorder.embeds = status, embeds
    return recorder


def _run(*args):
    with patch("artmind.projection.rebuild", return_value={}):
        result = CliRunner().invoke(
            cli, ["update", "confirm", "--session", "sess-0001", "--resolutions", json.dumps(RESOLUTIONS),
                  *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_writes_nothing_and_matches_the_real_effect(world, tmp_path):
    dry = _run("--dryRun")

    assert_no_write_cypher(world)
    assert world.queries_containing("WHERE elementId(e) = $ref"), "the chosen node is looked up"
    assert world.queries_containing("MATCH (e:Entity {_id: $id})"), "create checks for an existing entity"
    assert world.embeds == [], "no embedding call"
    assert world.status == [], "the draft stays pending"
    assert not (tmp_path / "kg").exists(), "no staging folder"
    reads = len(world.calls)

    real = _run()

    assert write_calls(world) and len(world.calls) > reads
    assert world.status == [("draft", 4, "confirmed"), ("session", "sess-0001", "confirmed")]
    assert (tmp_path / "kg" / "general").is_dir()
    assert dry["dry_run"] is True and "dry_run" not in real
    counts = ("nodes_created", "nodes_updated", "nodes_retracted", "observations_written",
              "relationships_written", "user_chat_id", "staging_dir")
    assert (dry["nodes_created"], dry["nodes_updated"], dry["nodes_retracted"]) == (2, 1, 1)
    assert dry["observations_written"] == 4 and dry["relationships_written"] == 1
    for k in counts[:5]:
        assert dry[k] == real[k], k
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    kinds = {c["kind"]: c["count"] for c in dry["effect"]["changes"]}
    assert kinds == {"fact_write": 3, "fact_retract": 1, "entity_create": 2, "entity_change": 1}
    assert dry["effect"]["undo"] == "artmind update retract --session sess-0001 --draft 4"


def test_dry_run_with_no_pending_draft_fails_like_a_real_run(world, monkeypatch):
    monkeypatch.setattr(upd, "_get_latest_pending_draft", lambda sid: None)
    result = CliRunner().invoke(cli, ["update", "confirm", "--session", "x", "--resolutions", "[]", "--dryRun"])
    assert result.exit_code != 0 and "No pending draft" in result.output
