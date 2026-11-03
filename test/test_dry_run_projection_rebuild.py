"""`projection rebuild --dryRun`: planned from reads alone, counts equal the real run's."""
import json

import pytest
from click.testing import CliRunner

import artmind.graph_query as graph_query
import artmind.projection as projection
import artmind.same_as as same_as
from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher, write_calls
from artmind.cli import cli
from artmind.observations import entity_id, key_string

DOMAIN = "demo.one"
A = ("alpha", "THING", DOMAIN)
B = ("alpha two", "THING", DOMAIN)   # folded into A by a same-as group
C = ("gamma", "THING", DOMAIN)       # only an Entity: its observations are gone
D = ("delta", "THING", DOMAIN)


def _obs(key, oid):
    return {"id": oid, "name": key[0], "canonical_name": key[0], "entity_class": key[1], "_domain": key[2],
            "_kind": "occurrent", "doc_version": 1, "_doc_valid_from": "2026-01-01", "_valid_from": "2026-01-01"}


def _respond(cypher, params):
    cy = " ".join(cypher.split())
    if "RETURN DISTINCT n.key AS key" in cy:
        keys = [A, B, D] if "(n:Observation)" in cy else [C]
        return [{"key": key_string(k)} for k in keys]
    if "MATCH (o:Observation {key: key}) RETURN key, properties(o) AS p" in cy:
        have = {key_string(A): [_obs(A, "o1")], key_string(B): [_obs(B, "o2")], key_string(D): [_obs(D, "o3")]}
        return [{"key": k, "p": o} for k in params["keys"] for o in have.get(k, [])]
    if "MATCH (e:Entity {_id: id}) RETURN id" in cy:
        return [{"id": i} for i in params["ids"] if i == entity_id(C)]
    return []


@pytest.fixture()
def world(monkeypatch):
    recorder = WritableRecorder(_respond)
    monkeypatch.setattr(graph_query, "neo4j_session", lambda *a, **k: recorder)
    monkeypatch.setattr(same_as, "load_groups", lambda: [[A, B]])
    monkeypatch.setattr(same_as, "content_hash", lambda path=None: "same-as-hash")
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "schema-hash")
    return recorder


def _run(*args):
    result = CliRunner().invoke(cli, ["projection", "rebuild", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_sends_no_write_cypher_and_matches_the_real_counts(world):
    dry = _run("--dryRun")
    assert_no_write_cypher(world)
    assert world.queries_containing("MATCH (n:Observation)"), "the plan reads the keys it would rebuild"
    assert not world.queries_containing("ProjectionState")
    reads = len(world.calls)

    real = _run()

    assert write_calls(world), "the real run writes"
    assert len(world.calls) > reads
    assert dry["dry_run"] is True and "dry_run" not in real
    counts = ("rebuilt", "deleted", "absent", "keys", "batches", "domains", "recorded")
    assert {k: dry[k] for k in counts} == {k: real[k] for k in counts}
    assert (dry["rebuilt"], dry["deleted"], dry["absent"], dry["keys"]) == (2, 2, 0, 4)
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert [c["kind"] for c in dry["effect"]["changes"]] == ["projection_rebuild", "entity_delete"]
    assert dry["effect"]["changes"][0]["count"] == 4


def test_dry_run_with_a_domain_runs_no_embed_sweep(world, monkeypatch):
    import artmind.ingest as ing

    def boom(*a, **k):
        raise AssertionError("a dry run must not sweep")

    monkeypatch.setattr(ing, "_sweep_domain", boom)
    monkeypatch.setattr(ing, "_sweep_embeddings", boom)
    monkeypatch.setattr(ing, "_sweep_passages", boom)

    payload = _run("--domain", DOMAIN, "--dryRun")

    assert payload["dry_run"] is True and payload["recorded"] is False
    assert_no_write_cypher(world)
    assert world.queries_containing("n._domain IN $domains")[0][1]["domains"] == [DOMAIN]
