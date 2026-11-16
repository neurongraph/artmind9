"""Schema-only property keys (spec 2026-11-15 §2.2). Synthetic data only."""
from artmind import ingest as ing
from artmind.observations import declared_property_keys, declared_property_order, filter_declared

SCHEMA = {
    "property_aliases": {"outcome_or_result": "outcome"},
    "entity_types": {
        "EVENT": {"kind": "occurrent", "properties": {
            "date_or_time": {"temporal": "valid_from"}, "outcome": {"required": True}, "cause": {}}},
        "PERSON": {"kind": "recurrent", "properties": {"role": {}}},
        "PLACE": {"kind": "recurrent"},
    },
}


def test_declared_property_keys_per_class():
    assert declared_property_keys(SCHEMA) == {
        "EVENT": {"date_or_time", "outcome", "cause"}, "PERSON": {"role"}, "PLACE": set()}


def test_declared_order_puts_required_first_and_can_drop_temporal():
    assert declared_property_order(SCHEMA)["EVENT"] == ["outcome", "date_or_time", "cause"]
    assert declared_property_order(SCHEMA, include_temporal=False)["EVENT"] == ["outcome", "cause"]


def test_filter_keeps_declared_structural_and_reserved_keys_and_returns_the_rest():
    obs = {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07",
           "outcome": "won", "mood": "calm", "role": "x"}
    kept, dropped = filter_declared(obs, "EVENT", declared_property_keys(SCHEMA))
    assert kept == {"id": "o1", "name": "N", "entity_class": "EVENT", "_valid_from": "2026-01-07", "outcome": "won"}
    assert dropped == {"mood": "calm", "role": "x"}


def test_build_observations_drops_undeclared_keys_after_alias_folding():
    entities = [{"id": "e1", "name": "Call", "entity_class": "EVENT", "chunk_id": "d_001", "_domain": "j"}]
    props = [{"id": "e1", "properties": {"outcome_or_result": "agreed", "sentiment": "warm"}}]
    document = {"id": "d", "version": 1, "_valid_from": "2026-01-07"}
    obs, dropped = ing._build_observations(entities, props, {}, SCHEMA, document)
    assert obs[0]["outcome"] == "agreed"
    assert "sentiment" not in obs[0] and "outcome_or_result" not in obs[0]
    assert dropped == {"EVENT": {"sentiment": 1}}


def test_build_observations_drops_model_emitted_provenance_keys():
    """A raw LLM bag's `source_kind` / `created_by` are not the observation's own fields: a stray
    `source_kind: table` would make retime and the audit skip the observation."""
    entities = [{"id": "e1", "name": "Call", "entity_class": "EVENT", "chunk_id": "d_001", "_domain": "j"}]
    props = [{"id": "e1", "properties": {"source_kind": "table", "created_by": "X", "foo": "x", "outcome": "ok"}}]
    document = {"id": "d", "version": 1, "_valid_from": "2026-01-07"}
    obs, dropped = ing._build_observations(entities, props, {}, SCHEMA, document)
    assert "source_kind" not in obs[0] and "created_by" not in obs[0] and "foo" not in obs[0]
    assert obs[0]["outcome"] == "ok"
    assert dropped == {"EVENT": {"source_kind": 1, "created_by": 1, "foo": 1}}


def test_filter_declared_own_parameter():
    from artmind.observations import LLM_BAG_OWN_KEYS

    declared = declared_property_keys(SCHEMA)
    staged = {"id": "o", "source_kind": "update_session", "created_by": "u", "outcome": "x"}
    assert filter_declared(staged, "EVENT", declared)[0] == staged, "a staged observation keeps its own keys"
    kept, gone = filter_declared(staged, "EVENT", declared, own=LLM_BAG_OWN_KEYS)
    assert kept == {"id": "o", "outcome": "x"} and gone == {"source_kind": "update_session", "created_by": "u"}


def test_declared_order_can_leave_out_own_keys():
    schema = {"entity_types": {"EVENT": {"properties": {
        "context": {}, "outcome": {}, "description": {"required": True}, "domain": {}}}}}
    assert declared_property_order(schema)["EVENT"] == ["description", "context", "outcome", "domain"]
    assert declared_property_order(schema, include_own=False)["EVENT"] == ["outcome"]
    assert declared_property_keys(schema)["EVENT"] == {"context", "outcome", "description", "domain"}


NO_PROPS_SCHEMA = """\
name: journal
subject: a journal
entity_types:
  EVENT:
    kind: occurrent
    description: Something that happened.
    properties:
      outcome: {}
  PLACE:
    kind: recurrent
    description: A place.
"""


def _run_extract(tmp_path, monkeypatch, entities):
    from test_document_dates import _stub_llm, _vault

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "At the park.\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(NO_PROPS_SCHEMA, encoding="utf-8")
    _stub_llm(monkeypatch)
    steps, shown = [], []

    def llm(step, model, prompt, debug_dir):
        steps.append(step)
        return (entities, True) if step.endswith("_entities") else ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "build_properties_prompt",
                        lambda text, ents, schema, anchor_date=None, **k: shown.append(ents) or "p")
    result = ing.ingest_file(doc, "gemma4:e4b", "journal", chunk_size=6000)
    ing.extract_kg(result, "journal", max_workers=1)
    return steps, shown, ing._get_chunk_statuses(result["sha256"])[1]


def test_a_chunk_with_only_property_less_classes_makes_no_call(tmp_path, monkeypatch):
    steps, _, status = _run_extract(tmp_path, monkeypatch, [{"id": "e1", "entity_class": "PLACE", "name": "Park"}])
    assert not any(s.endswith("_properties") for s in steps)
    assert status["properties_status"] == "skipped"


def test_only_entities_of_classes_with_properties_are_sent(tmp_path, monkeypatch):
    _, shown, status = _run_extract(tmp_path, monkeypatch, [
        {"id": "e1", "entity_class": "PLACE", "name": "Park"}, {"id": "e2", "entity_class": "EVENT", "name": "Walk"}])
    assert [[e["entity_class"] for e in ents] for ents in shown] == [["EVENT"]]
    assert status["properties_status"] == "ok"
