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


# ── review fixes ─────────────────────────────────────────────────────────────

def test_properties_as_a_list_is_an_error_not_a_crash():
    schema = {"name": "d", "entity_types": {"EVENT": {"kind": "occurrent", "properties": ["a", "b"]}}}
    errors = sv.validate_schema(schema, META)
    assert "d.entity_types.EVENT.properties: must be a map" in errors
    assert sv._declared(schema["entity_types"]) == {"EVENT": set()}
    assert sv.guidance_property_warnings(schema, "d") == []


def test_an_alias_to_a_common_key_does_not_warn():
    schema = {**_schema(outcome={}), "property_aliases": {"summary": "description"}}
    assert sv.property_alias_warnings(schema, "d") == []


def test_alias_warnings_tolerate_a_non_map_entity_types():
    assert sv.property_alias_warnings({"entity_types": ["EVENT"], "property_aliases": {"a": "b"}}, "d") != []


def test_right_example_wrapper_key_is_not_a_property():
    schema = _schema(outcome={})
    schema["entity_types"]["EVENT"]["guidance"] = 'RIGHT: { "name": "X", "properties": { "outcome": "won" } }'
    assert sv.guidance_property_warnings(schema, "d") == []


def _write(directory, name, data):
    (directory / f"{name}_schema.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_family_schema_unions_children_properties_per_class(tmp_path):
    _write(tmp_path, "fam", {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"role": {}}}}})
    _write(tmp_path, "fam.a", {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"relation": {}}}}})
    _write(tmp_path, "fam.b", {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"nickname": {}}},
                                                "EVENT": {"kind": "occurrent", "properties": {"outcome": {}}}}})
    merged = sv.family_schema("fam", schemas_dir=tmp_path)
    assert set(merged["entity_types"]["PERSON"]["properties"]) == {"role", "relation", "nickname"}
    assert set(merged["entity_types"]["EVENT"]["properties"]) == {"outcome"}


def test_a_root_mart_seed_declared_only_on_a_child_passes(tmp_path, monkeypatch):
    _write(tmp_path, "fam", {"entity_types": {"PERSON": {"kind": "recurrent"}}})
    _write(tmp_path, "fam.a", {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"relation": {}}},
                                                "LOCATION": {"kind": "recurrent"}, "EVENT": {"kind": "occurrent"}}})
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: _mart(person_seed="relation"))
    root_only = {"entity_types": {"PERSON": {"kind": "recurrent"}}}
    assert sv.property_reference_report("fam", root_only)["errors"] != []
    assert sv.property_reference_report("fam", sv.family_schema("fam", schemas_dir=tmp_path))["errors"] == []


def test_insights_outcome_on_an_undeclared_key_is_an_error(monkeypatch):
    _no_mart(monkeypatch)
    cfg = SimpleNamespace(block=lambda n: {"class": "EVENT", "property": "result"} if n == "outcome" else None)
    monkeypatch.setattr("artmind.insights.config.load_config", lambda d: cfg)
    report = sv.property_reference_report("d", _schema(outcome={}))
    assert any("insights/d outcome.property: EVENT.result" in e for e in report["errors"])


DECL = {"EVENT": {"outcome"}, "PERSON": {"role"}}


def test_view_refs_skip_a_key_inside_a_string_literal():
    refs = sv.view_property_refs("MATCH (e:EVENT) WHERE e.name = 'e.foo' RETURN e.outcome", DECL)
    assert refs == [(frozenset({"EVENT"}), "outcome")]  # `foo` sits inside the literal


def test_view_refs_bind_a_label_pattern_and_a_label_chain():
    assert sv.view_property_refs("MATCH (e:EVENT) RETURN e.mood", DECL) == [(frozenset({"EVENT"}), "mood")]
    assert sv.view_property_refs("MATCH (e:Entity:EVENT) RETURN e.mood", DECL) == [(frozenset({"EVENT"}), "mood")]


def test_view_refs_skip_underscore_common_and_node_keys():
    cypher = "MATCH (e:EVENT) RETURN e._valid_from, e.description, e.name_embedding, e.embedding_stale, e.key"
    assert sv.view_property_refs(cypher, DECL) == []


def test_view_refs_match_in_case_insensitively():
    assert sv.view_property_refs("MATCH (e) WHERE e.entity_class in ['PERSON'] RETURN e.age", DECL) == [
        (frozenset({"PERSON"}), "age")]


def test_view_refs_ignore_unbound_variables():
    assert sv.view_property_refs("MATCH (e:Entity) RETURN e.mood", DECL) == []


def test_a_family_view_is_checked_only_under_its_own_domain(monkeypatch):
    _no_mart(monkeypatch)
    view = SimpleNamespace(spec=SimpleNamespace(name="v1", domains=("fam",), cypher="MATCH (e:EVENT) RETURN e.mood"))
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([view], []))
    schema = _schema(outcome={})
    assert sv.property_reference_report("fam.a", schema)["warnings"] == []
    assert len(sv.property_reference_report("fam", schema)["warnings"]) == 1


def test_a_declared_property_named_like_an_observation_field_warns():
    schema = _schema(context={}, outcome={})
    (w,) = sv.own_key_property_warnings(schema, "d")
    assert "EVENT.properties.context" in w and "observation field" in w
    assert sv.validate_schema(schema, META) == []  # a WARN, never an error


def test_own_key_warnings_tolerate_a_non_map_entity_types():
    assert sv.own_key_property_warnings({"entity_types": ["EVENT"]}, "d") == []


def test_warn_all_carries_own_key_warnings(tmp_path):
    (tmp_path / "d_schema.yaml").write_text(yaml.safe_dump(_schema(domain={})), encoding="utf-8")
    assert any("properties.domain" in w for w in sv.warn_all(tmp_path)["d"])


def test_view_bound_classes():
    assert sv.view_bound_classes("MATCH (t:TASK), (e) WHERE e.entity_class = 'EVENT' RETURN t",
                                 {"TASK": set(), "EVENT": set(), "PLACE": set()}) == {"TASK", "EVENT"}
    assert sv.view_bound_classes("", {"TASK": set()}) == set()
