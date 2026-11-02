"""`update retract --dryRun`: counted from reads alone, effect equal to the real run's."""
import json

import pytest
from click.testing import CliRunner

import artmind.ingest as ing
import artmind.projection as projection
import artmind.same_as as same_as
import artmind.table2graph as t2g
import artmind.update as upd
from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind.cli import cli
from artmind.observations import entity_id, key_string

A = ("alice", "PERSON", "general")
B = ("a. smith", "PERSON", "general")   # folded into A by a same-as group


def _respond(cypher, params):
    cy = " ".join(cypher.split())
    if "RETURN DISTINCT o.key AS key" in cy:
        return [{"key": key_string(A)}]
    if "RETURN count(o) AS n" in cy:
        return [{"n": 3}]
    if "MATCH (o:Observation {key: key}) RETURN key, properties(o) AS p" in cy:
        return [{"key": key_string(A), "p": {"id": "o1", "name": "alice", "canonical_name": "alice",
                                              "entity_class": "PERSON", "_domain": "general", "_kind": "occurrent",
                                              "doc_version": 1, "_doc_valid_from": "2026-01-01",
                                              "_valid_from": "2026-01-01"}}]
    if "MATCH (e:Entity {_id: id}) RETURN id" in cy:
        return [{"id": i} for i in params["ids"] if i == entity_id(B)]
    return []


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    from artmind.ingest import write_staging

    folder = tmp_path / "kg" / "general" / "update__s1__7"
    write_staging(folder, {"document.json": {"id": "update:s1:7", "source_kind": "user_chat"},
                           "observations.json": []})
    recorder = WritableRecorder(_respond)
    monkeypatch.setattr(upd, "read_session", lambda *a, **k: recorder)
    monkeypatch.setattr(upd, "_get_update_session", lambda sid: {"session_id": sid, "domain": "general"})
    monkeypatch.setattr(same_as, "load_groups", lambda: [[A, B]])
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "h")
    seen = {"retract": [], "rebuild": [], "sweep": [], "status": []}
    monkeypatch.setattr(ing, "retract_document", lambda doc_id, domain: seen["retract"].append(doc_id) or {
        "affected_keys": [list(A)], "observations_demoted": 3})
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: seen["rebuild"].append(keys) or {
        "rebuilt": 1, "deleted": 1, "absent": 0, "keys": 2, "batches": 1})
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda d, k: seen["sweep"].append(d) or 0)
    monkeypatch.setattr(upd, "_update_draft_status", lambda i, s: seen["status"].append((i, s)))
    monkeypatch.setattr(upd, "_update_session_status", lambda i, s: seen["status"].append((i, s)))
    recorder.seen, recorder.folder = seen, folder
    return recorder


def _run(*args):
    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s1", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_changes_nothing_and_matches_the_real_effect(world):
    dry = _run("--dryRun")

    assert_no_write_cypher(world)
    assert world.queries_containing("o.doc_id = $doc_id") or world.queries_containing("{doc_id: $doc_id}")
    assert (world.queries_containing("count(o)")[0][1]) == {"doc_id": "update:s1:7"}
    assert world.seen == {"retract": [], "rebuild": [], "sweep": [], "status": []}
    assert world.folder.is_dir(), "the staging folder stays"

    real = _run()

    assert world.seen["retract"] == ["update:s1:7"] and world.seen["rebuild"] and world.seen["status"]
    assert not world.folder.exists()
    assert dry["dry_run"] is True and "dry_run" not in real
    assert dry["retracted"] == real["retracted"] and dry["folders_removed"] == real["folders_removed"]
    assert dry["projection"]["keys"] == 2 and dry["projection"]["deleted"] == 1 and dry["projection"]["rebuilt"] == 1
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"][0] == {"kind": "fact_retract", "count": 3, "sample": ["update:s1:7"]}
    assert dry["effect"]["travels"] is True and dry["effect"]["records"] == real["folders_removed"]


def test_dry_run_with_no_confirmed_update_fails_like_a_real_run(world):
    result = CliRunner().invoke(cli, ["update", "retract", "--session", "s1", "--draft", "99", "--dryRun"])
    assert result.exit_code != 0 and "No confirmed update to retract" in result.output
