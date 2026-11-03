"""`catalogue refresh --dryRun`: names the stale parts, rebuilds and writes nothing."""
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind import graph_query
from artmind.catalogue import cache, parts
from artmind.cli import cli


@pytest.fixture
def env(monkeypatch, tmp_path):
    import paths

    recorder = WritableRecorder(lambda c, p: [{"domain": d, "generation": 4, "entities": 1, "documents": 1, "history": 0}
                                              for d in p["domains"]] if "DomainState" in c else [])
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: recorder.run(c, **p).data())
    built = []

    def fake(part):
        def build(domain):
            built.append((domain, part))
            return {"classes": {"classes": (("PERSON", 2, ""),)}, "relations": {"relations": (("KNOWS", 1),)}}.get(part, {})
        return build

    monkeypatch.setattr(parts, "BUILDERS", {"classes": fake("classes"), "relations": fake("relations")})
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_PLAN_CATALOG", "1")
    cache.reset()
    yield recorder, built, tmp_path / "logs"
    cache.reset()


def _run(*args):
    result = CliRunner().invoke(cli, ["catalogue", "refresh", "--domain", "journal", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_dry_run_reports_what_is_stale_and_writes_nothing(env):
    recorder, built, logs = env
    out = _run("--dryRun")
    assert out["dry_run"] is True and out["refreshed"] == []
    assert [s["part"] for s in out["domains"][0]["stale"]] == ["classes", "relations"]
    assert out["domains"][0]["stale"][0]["reason"] == "not built"
    assert_effect_shape(out["effect"])
    assert out["effect"]["changes"] == [{"kind": "catalogue_refresh", "count": 2, "sample": ["journal/classes", "journal/relations"]}]
    assert built == [] and not logs.exists()
    assert_no_write_cypher(recorder)


def test_the_real_run_builds_what_the_dry_run_named_and_then_nothing_is_stale(env):
    _, built, logs = env
    dry = _run("--dryRun")
    real = _run()
    assert real["refreshed"] == ["journal/classes", "journal/relations"] and "dry_run" not in real
    assert real["effect"] == dry["effect"]
    assert sorted(built) == [("journal", "classes"), ("journal", "relations")]
    assert (Path(logs) / "plan-catalog" / "journal.json").is_file()
    again = _run("--dryRun")
    assert again["domains"][0]["stale"] == [] and again["effect"]["changes"] == []


def test_a_generation_change_makes_only_graph_parts_stale(env, monkeypatch):
    recorder, built, _ = env
    _run()
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: [
        {"domain": d, "generation": 5, "entities": 1, "documents": 1, "history": 0} for d in p["domains"]])
    out = _run("--dryRun", "--part", "relations")
    assert out["domains"][0]["stale"] == [{"part": "relations", "reason": "inputs changed"}]


def test_an_unknown_part_is_refused(env):
    result = CliRunner().invoke(cli, ["catalogue", "refresh", "--domain", "journal", "--part", "nope"])
    assert result.exit_code != 0 and "unknown catalogue part" in result.output
