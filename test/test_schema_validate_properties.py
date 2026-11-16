"""`domains validate` property checks (spec 2026-11-15 §2.5, §2.6). Synthetic schemas only."""
from types import SimpleNamespace

import yaml

from artmind import schema_validate as sv

META = {"reserved_prefix": "_", "kinds": {"recurrent": "", "occurrent": ""}}


def _schema(**props):
    return {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent", "properties": props}}}


def _no_mart(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: None)


def _mart(person_seed=None, type_property="type", wholes=None):
    return SimpleNamespace(person=SimpleNamespace(entity_class="PERSON", seed_property=person_seed),
                           place=SimpleNamespace(entity_class="LOCATION", seed_property=None),
                           type=SimpleNamespace(classes=["EVENT"], property=type_property),
                           happening_classes=["EVENT"], wholes=wholes or {})


def test_required_must_be_a_boolean():
    errors = sv.validate_schema(_schema(outcome={"required": "yes"}), META)
    assert any("EVENT.properties.outcome.required" in e and "true or false" in e for e in errors)


def test_keep_reason_must_be_a_string():
    assert any("keep_reason" in e for e in sv.validate_schema(_schema(outcome={"keep_reason": 3}), META))


def test_a_valid_required_and_keep_reason_pass():
    assert sv.validate_schema(_schema(outcome={"required": True, "keep_reason": "probe M04"}), META) == []


def test_an_alias_to_an_undeclared_key_warns():
    schema = {**_schema(outcome={}), "property_aliases": {"result": "outcome", "time_of_visit": "time_of_day"}}
    (w,) = sv.property_alias_warnings(schema, "d")
    assert "time_of_visit" in w and "time_of_day" in w and "dropped" in w


def test_guidance_naming_an_undeclared_key_warns():
    schema = _schema(outcome={})
    schema["entity_types"]["EVENT"]["guidance"] = 'Record properties.cause. RIGHT: { "name": "X", "mood": "calm" }'
    warnings = sv.guidance_property_warnings(schema, "d")
    assert any("EVENT" in w and "cause" in w for w in warnings)
    assert any("EVENT" in w and "mood" in w for w in warnings)


def test_relationship_guidance_is_not_scanned():
    schema = {**_schema(outcome={}), "guidance": {"relationships": "always include properties.task"}}
    assert sv.guidance_property_warnings(schema, "d") == []


def test_the_old_meta_template_warns(tmp_path):
    meta = tmp_path / "meta.yaml"
    meta.write_text(yaml.safe_dump({"prompt_templates": {"properties": "There is NO fixed schema for properties"}}))
    (w,) = sv.meta_template_warnings(meta)
    assert "init --force" in w


def test_a_mart_seed_on_an_undeclared_key_is_an_error(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart(person_seed="relation"))
    schema = {"entity_types": {"PERSON": {"properties": {"role": {}}}, "LOCATION": {}, "EVENT": {}}}
    assert any("mart" in e and "PERSON.relation" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_type_is_structural_and_always_declared(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart())
    schema = {"entity_types": {"PERSON": {}, "LOCATION": {}, "EVENT": {}}}
    assert sv.property_reference_report("d", schema)["errors"] == []


def test_a_mart_whole_match_key_must_be_declared_on_a_happening_class(monkeypatch):
    wholes = {"life_event": SimpleNamespace(match=["edge", "name", "participants"])}
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart(wholes=wholes))
    schema = {"entity_types": {"PERSON": {}, "LOCATION": {}, "EVENT": {"properties": {"outcome": {}}}}}
    assert any("participants" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_insights_open_loops_on_an_undeclared_key_is_an_error(monkeypatch):
    _no_mart(monkeypatch)
    cfg = SimpleNamespace(block=lambda n: {"class": "PLAN", "property": "state", "urgency_property": "urgency"}
                          if n == "open_loops" else None)
    monkeypatch.setattr("artmind.insights.config.load_config", lambda d: cfg)
    schema = {"entity_types": {"PLAN": {"properties": {"status": {}, "urgency": {}}}}}
    assert any("insights" in e and "PLAN.state" in e for e in sv.property_reference_report("d", schema)["errors"])


def test_a_view_reading_an_undeclared_key_on_a_bound_class_warns(monkeypatch):
    _no_mart(monkeypatch)
    view = SimpleNamespace(spec=SimpleNamespace(name="v1", domains=("d",), cypher=(
        "MATCH (e:Entity) WHERE e.entity_class IN ['EVENT'] RETURN e.location, e.date_or_time, e._id, e.name")))
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([view], []))
    schema = {"entity_types": {"EVENT": {"properties": {"date_or_time": {}}}}}
    report = sv.property_reference_report("d", schema)
    assert report["errors"] == []
    assert any("view:v1" in w and "EVENT.location" in w for w in report["warnings"])
