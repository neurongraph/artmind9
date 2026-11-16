"""`ingest backfill-properties --dryRun`: reads staging only; no LLM, no write (spec 2026-11-15 §4)."""
import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind import backfill
from artmind.cli import cli
from test_backfill_properties import staged  # noqa: F401  (fixture)


def _run(*args):
    r = CliRunner().invoke(cli, ["ingest", "backfill-properties", "--domain", "journal", "--entityClass", "EMOTION",
                                 "--property", "trigger", *args, "--compact"])
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def test_dry_run_asks_nothing_writes_nothing_and_matches_the_real_effect(staged, monkeypatch):
    before = {p.name: p.read_bytes() for p in staged.iterdir() if p.is_file()}
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM")))

    dry = _run("--dryRun")

    assert {p.name: p.read_bytes() for p in staged.iterdir() if p.is_file()} == before
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"], llm_calls=1)
    assert dry["effect"]["changes"] == [{"kind": "property_backfill", "count": 1, "sample": ["2026-01-07.md"]}]
    assert dry["effect"]["reversible"] is True and "backfill-stash" in dry["effect"]["undo"]
    assert "sent to the configured provider" in dry["effect"]["summary"]

    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([{"id": "o1", "properties": {"trigger": "x"}}], True))
    real = _run()
    assert real["dry_run"] is False and real["effect"] == dry["effect"]


def test_nothing_to_fill_is_an_empty_effect(staged, monkeypatch):
    out = _run("--match", "nothing-*", "--dryRun")
    assert out["effect"]["changes"] == [] and out["effect"]["llm"]["calls"] == 0
