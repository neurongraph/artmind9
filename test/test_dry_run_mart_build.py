import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.mart import build as mart_build
from artmind.mart import cli as mart_cli


def test_build_dry_run_writes_nothing(monkeypatch):
    wrote = []
    monkeypatch.setattr(mart_cli, "_declaration", lambda d: type("D", (), {"domain": "j", "wholes": {}})())
    monkeypatch.setattr(mart_build, "assemble_all", lambda decl, domains: {"fact_happening": [{}] * 3, "dim_place": []})
    monkeypatch.setattr(mart_build.storage, "write_tables", lambda *a: wrote.append(a))
    monkeypatch.setattr(mart_build.metadata, "write", lambda *a: wrote.append(a))
    monkeypatch.setattr(mart_build, "_generation", lambda domains: 7)
    r = CliRunner().invoke(cli, ["mart", "build", "--domain", "j", "--dryRun", "--compact"])
    out = json.loads(r.output)
    assert out["dry_run"] is True and wrote == []
    assert out["rows"] == {"fact_happening": 3, "dim_place": 0} and out["generation"] == 7
    assert_effect_shape(out["effect"])
