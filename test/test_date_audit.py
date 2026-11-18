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


def test_an_end_taken_from_the_document_is_not_a_range(tmp_path):
    folder = tmp_path / "d1"
    folder.mkdir()
    obs = [{"entity_class": "EVENT", "date_or_time": "2026-01-07", "_valid_time_source": "property",
            "_valid_precision": "day", "_valid_to": "2026-06-30"}]
    (folder / "observations.json").write_text(json.dumps(obs))
    assert date_audit.audit_folders([folder], SCHEMA)[0]["ranges"] == 0


def test_a_family_root_scans_its_children_with_their_own_schemas(tmp_path, monkeypatch):
    from artmind import ingest

    monkeypatch.setattr(ingest, "KG_DIR", tmp_path)
    _stage(tmp_path / "fam")
    _stage(tmp_path / "fam.child")
    _stage(tmp_path / "other")
    child_only = {"entity_types": {"PERSON": {"properties": {"born": {"temporal": "valid_from"}}}}}
    monkeypatch.setattr(date_audit, "load_schema",
                        lambda domain: child_only if domain == "fam.child" else SCHEMA)
    payload = date_audit.audit_domain("fam")
    assert payload["documents"] == 2
    by_class = {r["class"]: r for r in payload["rows"]}
    assert by_class["EVENT"]["observations"] == 3          # only fam's folder declares EVENT dates
    assert by_class["PERSON"] == {"class": "PERSON", "observations": 1, "dated_by_property": 0, "share": 0.0,
                                  "precision": {}, "ranges": 0, "unparsed": 0}


def test_an_unknown_domain_is_named_in_warnings(tmp_path, monkeypatch):
    from artmind import ingest

    monkeypatch.setattr(ingest, "KG_DIR", tmp_path)
    monkeypatch.setattr(date_audit, "load_schema", lambda domain: {})
    monkeypatch.setattr("artmind.schema_validate.family_schema", lambda domain: {"entity_types": {}})
    result = CliRunner().invoke(cli, ["ingest", "date-audit", "--domain", "nosuch", "--compact"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0 and payload["rows"] == []
    assert len(payload["warnings"]) == 1 and "nosuch" in payload["warnings"][0]
