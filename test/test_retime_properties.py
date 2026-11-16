"""`ingest retime` drops undeclared property keys from staging (spec 2026-11-15 §2.4). Synthetic data."""
import json

import yaml

import artmind.ingest as ing
from artmind.retime import retime_folder, retime_observations
from test_document_dates import EXCLUSIVE_SCHEMA, _extract, _folder, _two_class_llm, _vault

WITH_MOOD = EXCLUSIVE_SCHEMA.replace("      role: {}\n", "      role: {}\n      mood: {}\n")


def test_retime_observations_drops_and_reports_undeclared_keys():
    schema = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"outcome": {}}}}}
    obs = [{"id": "o1", "entity_class": "EVENT", "outcome": "won", "mood": "calm", "chunk_id": "c"}]
    new, dropped = retime_observations(obs, {"_valid_from": "2026-01-07"}, schema)
    assert "mood" not in new[0] and new[0]["outcome"] == "won"
    assert dropped == {"o1": {"mood": "calm"}}


def test_table_observations_are_left_alone():
    schema = {"entity_types": {"ROW": {"kind": "recurrent", "properties": {}}}}
    obs = [{"id": "o1", "entity_class": "ROW", "source_kind": "table", "amount": 3}]
    new, dropped = retime_observations(obs, {}, schema)
    assert new[0]["amount"] == 3 and dropped == {}


def _staged_with_mood(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(WITH_MOOD)
    _two_class_llm(monkeypatch)
    _extract(doc)
    folder = _folder("2026-01-07")
    obs = json.loads((folder / "observations.json").read_text())
    (folder / "observations.json").write_text(json.dumps([{**o, "mood": "calm"} for o in obs]))
    schema_file.write_text(EXCLUSIVE_SCHEMA)  # mood is trimmed from the schema
    return folder


def test_retime_folder_moves_dropped_values_to_logs(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    result = retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA))
    assert result["changed"] and result["dropped"] == {"mood": 2}
    assert all("mood" not in o for o in json.loads((folder / "observations.json").read_text()))
    (sidecar,) = (tmp_path / "logs" / "retime-dropped" / "journal").glob("*.json")
    assert {v["mood"] for v in json.loads(sidecar.read_text())["values"].values()} == {"calm"}


def test_a_dry_run_counts_but_moves_nothing(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    before = (folder / "observations.json").read_bytes()
    assert retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA), write=False)["dropped"] == {"mood": 2}
    assert (folder / "observations.json").read_bytes() == before
    assert not (tmp_path / "logs" / "retime-dropped").exists()


def test_missing_markdown_still_prunes_and_keeps_the_dates(tmp_path, monkeypatch):
    folder = _staged_with_mood(tmp_path, monkeypatch)
    (tmp_path / "vault" / "journal" / "2026-01-07.md").unlink()
    before = json.loads((folder / "document.json").read_text())
    result = retime_folder(folder, yaml.safe_load(EXCLUSIVE_SCHEMA))
    assert result["dates"] == "kept (markdown not found)" and result["dropped"] == {"mood": 2}
    assert json.loads((folder / "document.json").read_text()) == before
