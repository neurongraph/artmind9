import json

import yaml
from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.mart import validate as mv


def test_validate_all_reports_errors_per_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_MART_DIR", tmp_path)
    (tmp_path / "journal.yaml").write_text(yaml.safe_dump({"format": 1, "domain": "journal", "version": 1,
                                                          "facts": {"happening": ["NOPE"]}}))
    monkeypatch.setattr(mv, "_schema", lambda d: {"entity_types": {"EVENT": {}, "PERSON": {}, "LOCATION": {}}})
    out = mv.validate_all()
    assert "journal" in out and any("NOPE" in e for e in out["journal"]["errors"])


def test_domains_validate_includes_mart(tmp_path, monkeypatch):
    monkeypatch.setattr(mv, "validate_all", lambda: {"journal": {"errors": ["boom"], "warnings": []}})
    r = CliRunner().invoke(cli, ["domains", "validate", "--compact"])
    payload = json.loads(next(l for l in r.output.splitlines() if l.startswith("{")))
    assert payload["mart"] == {"journal": {"errors": ["boom"], "warnings": []}}
    assert r.exit_code != 0
