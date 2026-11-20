import json

from click.testing import CliRunner

import paths
from _dry_run import assert_effect_shape
from artmind import curation_records
from artmind.cli import cli
from artmind.mart import cli as mart_cli
from artmind.mart import records
from artmind.mart.declaration import MartDeclaration, PersonDim, PlaceDim, TypeDim


def _setup(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path)
    monkeypatch.setattr(curation_records, "curation_dir", lambda: tmp_path)
    decl = MartDeclaration("j", 1, None, [], [], False, PersonDim(), PlaceDim(), TypeDim(), {}, {})
    monkeypatch.setattr(mart_cli, "_declaration", lambda d: decl)
    return records.put_dimension("j", "place", "x|LOCATION|j", {"city": "A"}, source="llm", confidence=0.5,
                                 status="proposed")


def test_reject_dry_run_writes_nothing(monkeypatch, tmp_path):
    rid = _setup(monkeypatch, tmp_path)
    before = sorted(p.read_bytes() for p in tmp_path.rglob("*.json"))
    r = CliRunner().invoke(cli, ["mart", "review", "reject", "--domain", "j", "--id", rid, "--dryRun", "--compact"])
    out = json.loads(r.output)
    assert out["dry_run"] is True and out["planned"][0]["action"] == "reject"
    assert_effect_shape(out["effect"])
    assert sorted(p.read_bytes() for p in tmp_path.rglob("*.json")) == before


def test_reject_leaves_queue_and_mart(monkeypatch, tmp_path):
    rid = _setup(monkeypatch, tmp_path)
    r = CliRunner().invoke(cli, ["mart", "review", "reject", "--domain", "j", "--id", rid, "--compact"])
    assert r.exit_code == 0, r.output
    assert records.proposed("j") == []
    assert records.load_dimensions("j") == {}
    # enrich still sees it as decided, so it does not ask the LLM again
    assert ("place", "x|LOCATION|j") in records.load_dimensions("j", include_proposed=True)


def test_reject_unknown_id_fails(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    r = CliRunner().invoke(cli, ["mart", "review", "reject", "--domain", "j", "--id", "nope", "--compact"])
    assert r.exit_code == 1 and "no open proposal" in r.output
