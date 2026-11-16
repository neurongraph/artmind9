"""`ingest backfill-properties` (spec 2026-11-15 §4). Synthetic text only; the LLM is a stub."""
import json

import pytest

import artmind.ingest as ing
from artmind import backfill
from test_document_dates import JOURNAL_SCHEMA, _extract, _folder, _stub_llm, _vault

HEADER = JOURNAL_SCHEMA.split("entity_types:")[0]
SCHEMA_V1 = HEADER + """entity_types:
  EMOTION:
    kind: occurrent
    description: A feeling.
    properties:
      intensity: {}
  PERSON:
    kind: recurrent
    description: A person.
    properties:
      role: {}
"""
SCHEMA_V2 = SCHEMA_V1.replace("      intensity: {}\n", "      intensity: {}\n      trigger: {}\n")
SCHEMA_V3 = SCHEMA_V2.replace("      trigger: {}\n", "      trigger: {}\n      felt_on:\n        temporal: valid_from\n")


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Felt uneasy before the review with Alex.\n")
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(SCHEMA_V1)
    _stub_llm(monkeypatch)

    def llm(step, model, prompt, debug_dir):
        if step.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EMOTION", "name": "Unease"},
                    {"id": "e2", "entity_class": "PERSON", "name": "Alex"}], True
        if step.endswith("_properties"):
            return [{"id": "e1", "properties": {"intensity": "mild"}}, {"id": "e2", "properties": {"role": "lead"}}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    _extract(doc)
    schema_file.write_text(SCHEMA_V2)  # trigger is declared later
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    return _folder("2026-01-07")


def _answer(monkeypatch, answer, seen=None):
    def ask(step, model, prompt, debug_dir=None):
        if seen is not None:
            seen.append(prompt)
        return answer, True
    monkeypatch.setattr(backfill, "_ask", ask)


def _by_class(folder):
    return {o["entity_class"]: o for o in json.loads((folder / "observations.json").read_text())}


def test_fills_only_the_named_key_and_keeps_everything_else(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review", "intensity": "IGNORED"}}], seen)
    before = _by_class(staged)

    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")

    after = _by_class(staged)
    assert after["EMOTION"]["trigger"] == "the review"
    assert after["EMOTION"]["intensity"] == before["EMOTION"]["intensity"], "an unnamed key is never touched"
    assert after["PERSON"] == before["PERSON"]
    assert after["EMOTION"]["canonical_name"] == before["EMOTION"]["canonical_name"]
    blocks = seen[0].split("__list_of_entities__")[0]
    assert "For EMOTION, extract:" in blocks and "- trigger" in blocks and "intensity" not in blocks
    assert "PERSON" not in seen[0]
    assert report["committed"] == 1 and report["filled"] == 1


def test_an_undeclared_property_is_refused(staged):
    with pytest.raises(ValueError, match="mood not declared on EMOTION"):
        backfill.backfill_domain("journal", targets={"EMOTION": ["mood"]}, model="m")


def test_a_rerun_uses_the_cache(staged, monkeypatch):
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    calls = []
    _answer(monkeypatch, [], calls)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert calls == []


def test_the_folder_is_stashed_before_it_changes(staged, monkeypatch, tmp_path):
    original = (staged / "observations.json").read_bytes()
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (stashed,) = (tmp_path / "logs" / "backfill-stash").rglob("observations.json")
    assert stashed.read_bytes() == original and report["run_id"] in str(stashed)


def test_a_temporal_key_gets_the_anchor_and_redates(staged, monkeypatch):
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(SCHEMA_V3)
    seen = []
    _answer(monkeypatch, [{"id": "o1", "properties": {"felt_on": "2026-01-06"}}], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["felt_on"]}, model="m")
    emotion = _by_class(staged)["EMOTION"]
    assert "document dated 2026-01-07" in seen[0]
    assert (emotion["_valid_from"], emotion["_valid_time_source"]) == ("2026-01-06", "property")


def test_two_classes_share_one_call_per_chunk(staged, monkeypatch):
    seen = []
    _answer(monkeypatch, [], seen)
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"], "PERSON": ["role"]}, model="m")
    assert len(seen) == 1 and "For EMOTION" in seen[0] and "For PERSON" in seen[0]


def test_the_chunk_cache_gets_the_filled_value_too(staged, monkeypatch):
    _answer(monkeypatch, [{"id": "o1", "properties": {"trigger": "the review"}}])
    backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    (chunk_file,) = (staged / "chunks").rglob("chunk_001.json")
    data = json.loads(chunk_file.read_text())
    ent = next(e for e in data["entities"] if e["entity_class"] == "EMOTION")
    entry = next(p for p in data["properties"] if p["id"] == ent["id"])
    assert entry["properties"]["trigger"] == "the review"


def test_a_table_folder_is_never_planned(staged, monkeypatch):
    doc = json.loads((staged / "document.json").read_text())
    (staged / "document.json").write_text(json.dumps({**doc, "source_kind": "table"}))
    calls = []
    _answer(monkeypatch, [], calls)
    report = backfill.backfill_domain("journal", targets={"EMOTION": ["trigger"]}, model="m")
    assert calls == [] and report["planned"] == [] and report["effect"]["changes"] == []
