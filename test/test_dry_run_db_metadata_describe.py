"""`db metadata describe --dryRun`: reports the change, writes nothing."""
import json

import yaml
from click.testing import CliRunner

from _dry_run import assert_effect_shape
import paths
from artmind.cli import cli


def test_dry_run_then_write(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    meta_dir = tmp_path
    _file = lambda d: yaml.safe_load((d / "bank.meta.yaml").read_text())
    VIEW = "bank__complaints"
    monkeypatch.setattr("artmind.cli._resolve_table_row", lambda *a, **k: {"id": 1})
    args = ["db", "metadata", "describe", "--domain", "bank", "--table", "complaints", "--column", "HRS",
            "--text", "weekly hours", "--compact"]
    out = json.loads(CliRunner().invoke(cli, [*args, "--dryRun"]).output)
    assert out["dry_run"] is True and out["changed"] is True
    assert_effect_shape(out["effect"])
    assert not (meta_dir / "bank.meta.yaml").exists()
    r = CliRunner().invoke(cli, args)
    assert r.exit_code == 0 and _file(meta_dir)["tables"][VIEW]["columns"]["HRS"]["description"] == "weekly hours"
