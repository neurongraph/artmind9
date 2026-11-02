"""`projection synthesize --dryRun`: reads the candidates, makes no LLM call, writes nothing."""
import json

import pytest
from click.testing import CliRunner

import artmind.synthesize as syn
from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind.cli import cli


def _row(name, key, **kw):
    row = {"id": f"id-{name}", "key": key, "name": name, "entity_class": "WIDGET", "observation_count": 3,
           "current_hash": "new", "synth_hash": None, "has_open_conflict": False}
    row.update(kw)
    return row


ROWS = [
    _row("Alpha", "alpha|WIDGET|d"),
    _row("Beta", "beta|WIDGET|d"),
    _row("Gamma", "gamma|WIDGET|d", observation_count=1),
    _row("Delta", "delta|WIDGET|d", synth_hash="same", current_hash="same"),
]


@pytest.fixture()
def world(monkeypatch):
    rec = WritableRecorder(lambda cypher, params: ROWS if "MATCH (e:Entity)" in cypher else [])
    monkeypatch.setattr(syn, "neo4j_session", lambda *a, **k: rec)
    monkeypatch.setattr(syn, "read_session", lambda *a, **k: rec)
    monkeypatch.setattr(syn, "load_env", lambda: {})
    monkeypatch.setattr(syn, "resolve_llm_model", lambda env, model=None: "test-model")
    llm = []

    def fake_key(key, name, entity_class, **kw):
        llm.append(name)
        return {"key": "|".join(key), "name": name, "status": "synthesized"}

    monkeypatch.setattr(syn, "synthesize_key", fake_key)
    monkeypatch.setattr("artmind.same_as.load_groups", lambda: [])
    return rec, llm


def _run(*args):
    result = CliRunner().invoke(cli, ["projection", "synthesize", "--domain", "d", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_makes_no_call_and_matches_the_real_effect(world):
    rec, llm = world

    dry = _run("--dryRun")

    assert llm == [] and dry["dry_run"] is True
    assert_no_write_cypher(rec)
    select = rec.queries_containing("MATCH (e:Entity)")
    assert select and select[0][1] == {"domain": "d", "nameFilter": None}
    assert [c["name"] for c in dry["candidates"]] == ["Alpha", "Beta"]
    assert_effect_shape(dry["effect"], llm_calls=2)
    assert dry["effect"]["llm"]["model"] == "test-model"
    assert dry["effect"]["changes"] == [{"kind": "description_write", "count": 2, "sample": ["Alpha", "Beta"]}]

    real = _run()

    assert llm == ["Alpha", "Beta"]
    assert real["dry_run"] is False
    assert real["effect"] == dry["effect"]


def test_limit_caps_the_estimate(world):
    dry = _run("--dryRun", "--limit", "1")

    assert dry["effect"]["llm"]["calls"] == 1
    assert dry["effect"]["changes"][0]["sample"] == ["Alpha"]
    assert dry["counts"]["skipped_over_limit"] == 1
