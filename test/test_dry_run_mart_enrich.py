import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.mart import cli as mart_cli


def test_enrich_dry_run(monkeypatch):
    calls = []
    monkeypatch.setattr(mart_cli, "_declaration", lambda d: object())
    monkeypatch.setattr(mart_cli.enrich_mod, "enrich",
                        lambda decl, domains, **k: calls.append(k) or {"domain": "j", "decided": {"person": {"rule": 2}},
                                                                     "undecided": {"place": 3}, "llm_calls": 0})
    r = CliRunner().invoke(cli, ["mart", "enrich", "--domain", "j", "--dryRun", "--compact"])
    out = json.loads(r.output)
    assert out["dry_run"] is True and calls[0]["dry_run"] is True
    assert_effect_shape(out["effect"])
    assert out["effect"]["changes"] == [{"kind": "mart_record", "count": 2, "sample": ["person"]}]
