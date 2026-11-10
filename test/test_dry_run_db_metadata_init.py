"""`db metadata init --dryRun`: reports the file and tables, writes nothing, calls nothing."""
import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
import paths
from artmind import sql_browser
from artmind.cli import cli


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    monkeypatch.setattr("artmind.cli.structured_registry.list_tables",
                        lambda domains=None, **k: [{"table_name": "complaints", "domain": "bank"}])
    called = []
    monkeypatch.setattr(sql_browser, "metadata_command", lambda *a, **k: called.append(a) or {})
    r = CliRunner().invoke(cli, ["db", "metadata", "init", "--domain", "bank", "--dryRun", "--compact"])
    out = json.loads(r.output)
    assert out["dry_run"] is True and out["tables"] == ["complaints"] and called == []
    assert not (tmp_path / "bank.meta.yaml").exists()
    assert_effect_shape(out["effect"])


def test_real_run_refuses_to_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    (tmp_path / "bank.meta.yaml").write_text("tables: {}\n")
    monkeypatch.setattr("artmind.cli.structured_registry.list_tables",
                        lambda domains=None, **k: [{"table_name": "complaints", "domain": "bank"}])
    r = CliRunner().invoke(cli, ["db", "metadata", "init", "--domain", "bank", "--compact"])
    assert r.exit_code != 0 and "exists" in r.output
