"""`ingest date-audit` (spec 2026-11-15 §3.4): counts per class from staging; no values."""
import json

from click.testing import CliRunner

from artmind import date_audit
from artmind.cli import cli

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}},
                           "PERSON": {"kind": "recurrent", "properties": {}}}}


def _stage(root, name="d1"):
    folder = root / name
    folder.mkdir(parents=True)
    obs = [
        {"entity_class": "EVENT", "date_or_time": "2026-01-07", "_valid_time_source": "property", "_valid_precision": "day"},
        {"entity_class": "EVENT", "date_or_time": "2026-01 to 2026-03", "_valid_time_source": "property",
         "_valid_precision": "month", "_valid_to": "2026-04-01"},
        {"entity_class": "EVENT", "date_or_time": "someday", "_valid_time_source": "filename", "_valid_precision": "day"},
        {"entity_class": "PERSON", "_valid_time_source": "filename"},
    ]
    (folder / "observations.json").write_text(json.dumps(obs))
    return folder


def test_rows_per_dated_class(tmp_path):
    rows = date_audit.audit_folders([_stage(tmp_path)], SCHEMA)
    assert rows == [{"class": "EVENT", "observations": 3, "dated_by_property": 2, "share": 0.667,
                     "precision": {"day": 1, "month": 1}, "ranges": 1, "unparsed": 1}]


def test_counts_add_up_across_folders_and_skip_unreadable_ones(tmp_path):
    broken = tmp_path / "d3"
    broken.mkdir()
    (broken / "observations.json").write_text("{not json")
    rows = date_audit.audit_folders([_stage(tmp_path), _stage(tmp_path, "d2"), broken, tmp_path / "gone"], SCHEMA)
    assert rows[0]["observations"] == 6 and rows[0]["dated_by_property"] == 4 and rows[0]["unparsed"] == 2


def test_no_value_reaches_the_payload(tmp_path):
    dumped = json.dumps(date_audit.audit_folders([_stage(tmp_path)], SCHEMA))
    assert "someday" not in dumped and "2026" not in dumped


def test_cli_envelope(tmp_path, monkeypatch):
    folder = _stage(tmp_path)
    monkeypatch.setattr(date_audit, "staging_folders", lambda domain: [folder])
    monkeypatch.setattr(date_audit, "load_schema", lambda domain: SCHEMA)
    result = CliRunner().invoke(cli, ["ingest", "date-audit", "--domain", "pj", "--compact"])
    payload = json.loads(result.stdout)
    assert payload["command"] == "ingest date-audit" and payload["rows_total"] == 1
    assert payload["next"] == "artmind ingest retime --domain pj --dryRun"
    assert payload["documents"] == 1 and payload["truncated"] is False


def test_no_next_when_every_value_parses(tmp_path, monkeypatch):
    monkeypatch.setattr(date_audit, "staging_folders", lambda domain: [])
    monkeypatch.setattr(date_audit, "load_schema", lambda domain: SCHEMA)
    payload = date_audit.audit_domain("pj")
    assert payload["next"] is None and payload["rows"] == [] and payload["documents"] == 0
