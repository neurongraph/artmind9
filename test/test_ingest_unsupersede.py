"""`ingest unsupersede`: the real run reverses `apply_supersession`."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder
from artmind import curation_records, supersession_records
from artmind.cli import cli


def _responder(retired=True, others=0, edge=1, domains=("demo.one",)):
    def respond(cypher, params):
        cy = " ".join(cypher.split())
        if "d.name IN [$n,$o]" in cy:
            return [{"name": "v2", "id": "new-1"}, {"name": "v1", "id": "old-1"}]
        if "NOT (a.id" in cy:
            return [{"n": others}]
        if "RETURN count(s) AS n" in cy:
            return [{"n": edge}]
        if "d:DocumentHistory AS retired" in cy:
            return [{"id": "new-1", "name": "v2", "retired": False, "domain": domains[0]},
                    {"id": "old-1", "name": "v1", "retired": retired, "domain": domains[-1]}]
        if "RETURN DISTINCT d._domain AS domain" in cy:
            return [{"domain": "demo.one"}]
        if "RETURN DISTINCT o.key AS key" in cy:
            return []
        if "count(o) AS n" in cy:
            return [{"n": 0}]
        return []
    return respond


@pytest.fixture()
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path / "_Topics")
    rid = supersession_records.record_id("new-1", "old-1", "document")
    curation_records.write_record(supersession_records.NAME, {"id": rid})
    restored = []
    holder = {}

    def make(**kw):
        recorder = WritableRecorder(_responder(**kw))
        holder["r"] = recorder
        return recorder

    def run(**kw):
        recorder = make(**kw)
        with patch("artmind.graph_query.read_session", return_value=recorder), \
             patch("artmind.graph_query.neo4j_session", return_value=recorder), \
             patch("artmind.temporal.neo4j_session", return_value=recorder), \
             patch("artmind.lifecycle.restore_document", side_effect=lambda *a, **k: restored.append((a, k))):
            result = CliRunner().invoke(cli, ["ingest", "unsupersede", "--domain", "demo.one", "--newer", "v2",
                                              "--older", "v1", "--compact"])
        assert result.exit_code == 0, result.output
        return json.loads(result.stdout), recorder

    return run, restored, tmp_path / "curation" / "supersessions" / f"{rid}.json"


def test_real_run_removes_edge_recomputes_bumps_deletes_record_and_restores(world):
    run, restored, record_file = world
    assert record_file.exists()

    payload, recorder = run()

    assert payload["removed"] is True and payload["restored"] is True
    assert not record_file.exists()
    delete = [(c, p) for c, p in recorder.calls if "DELETE s" in c]
    assert delete and delete[0][1]["newer"] == "new-1" and delete[0][1]["older"] == "old-1" and delete[0][1]["scope"] == "document"
    recompute = [(c, p) for c, p in recorder.calls if "SET older.superseded_by" in c]
    assert recompute and recompute[0][1] == {"older": "old-1"}
    bumps = [p for c, p in recorder.calls if "MERGE (s:DomainState" in c]
    assert [b["domains"] for b in bumps] == [["demo.one"]]
    assert restored == [(("old-1", "demo.one"), {})]


def test_older_document_stays_retired_when_another_supersession_points_at_it(world):
    run, restored, _ = world

    payload, _ = run(others=1)

    assert payload["removed"] is True and payload["restored"] is False
    assert restored == []


def test_live_older_document_is_not_restored(world):
    run, restored, _ = world

    payload, _ = run(retired=False)

    assert payload["restored"] is False and restored == []


def test_rerun_after_a_failed_restore_still_restores(world):
    run, restored, record_file = world

    payload, recorder = run(edge=0)

    assert payload["removed"] is False and payload["restored"] is True
    assert restored == [(("old-1", "demo.one"), {})]
    assert not [1 for c, _ in recorder.calls if "DELETE s" in c]


def test_no_edge_and_not_retired_does_nothing(world):
    run, restored, _ = world

    payload, _ = run(edge=0, retired=False)

    assert payload["restored"] is False and restored == []


def test_multi_domain_effect_warns_and_undo_names_the_domain():
    from artmind.temporal import unsupersede_effect

    state = {"edge": True, "others": 0, "retired": True, "names": {"n": "v2", "o": "v1"}, "domains": ["a.one", "b.two"]}
    with patch("artmind.lifecycle.plan_transition", return_value={"keys": [], "observations": 0}):
        effect = unsupersede_effect("n", "o", "document", state)
    assert "embedding sweep" in effect["summary"]
    assert "--domain a.one" in effect["undo"]
