"""`migrate temporal --dryRun`: reports and changes nothing (tier 1)."""
import json
from contextlib import contextmanager

from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind import backfill, data_model, setup
from artmind.cli import cli
from artmind.migrations.temporal_v1 import cli as pack_cli
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import Step


def test_dry_run_sends_no_write_cypher_and_writes_no_file(monkeypatch, tmp_path):
    recorder = WritableRecorder(lambda c, p: [])

    @contextmanager
    def graph():
        yield recorder

    applied = []
    monkeypatch.setattr(steps_mod, "STEPS", [Step("alpha", lambda ctx: {"items": 3},
                                                  lambda ctx, dry: applied.append(1) or {})])
    monkeypatch.setattr(pack_cli, "_open_graph", graph)
    monkeypatch.setattr(pack_cli, "_vault_root", lambda: tmp_path)
    monkeypatch.setattr(setup, "_uncommitted", lambda root, path: False)
    monkeypatch.setattr(backfill, "_worker_running", lambda: False)
    monkeypatch.setattr(data_model, "MODEL_FILE", tmp_path / "data_model.yaml")
    before = sorted(tmp_path.rglob("*"))
    result = CliRunner().invoke(cli, ["migrate", "temporal", "--dryRun", "--compact"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["dry_run"] is True and out["steps"][0]["pending"] == {"items": 3}
    assert_effect_shape(out["effect"])
    assert_no_write_cypher(recorder)
    assert applied == [] and sorted(tmp_path.rglob("*")) == before
