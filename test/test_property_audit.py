"""`domains property-audit` rules (spec 2026-11-15 §3). Synthetic staging only."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from artmind import property_audit as pa

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {
    "date_or_time": {"temporal": "valid_from"}, "outcome": {}, "cause": {}, "mood": {},
    "rare": {}, "kept": {"keep_reason": "probe M04"}, "must": {"required": True}}}}}


def _stage(root, observations, chunk_text):
    folder = root / "kg" / "d" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "doc1", "name": "doc1.md"}))
    (folder / "chunks.json").write_text(json.dumps([{"id": "c1", "text": chunk_text}]))
    (folder / "observations.json").write_text(json.dumps(observations))
    return root / "kg"


def _obs(i, **props):
    return {"id": f"o{i}", "entity_class": "EVENT", "chunk_id": "c1",
            "description": "a team call about the budget", **props}


def _rows(kg, depended_on=None):
    return {(r["class"], r["key"]): r for r in pa.audit("d", SCHEMA, kg_dir=kg, depended_on=depended_on or {})["rows"]}


@pytest.fixture()
def rows(tmp_path):
    observations = [_obs(i, date_or_time="2026-01-07", outcome="budget agreed", must="x",
                         mood="quietly hopeful and reflective", cause="the budget call") for i in range(20)]
    observations[0]["rare"] = "once"
    observations[1]["invented"] = "y"
    return _rows(_stage(tmp_path, observations, "The budget call went well; budget agreed."))


def test_undeclared_key_is_drop(rows):
    assert rows[("EVENT", "invented")]["proposal"] == "drop" and rows[("EVENT", "invented")]["declared"] is False


def test_temporal_key_is_keep(rows):
    assert rows[("EVENT", "date_or_time")]["proposal"] == "keep"
    assert rows[("EVENT", "date_or_time")]["flags"]["temporal"] == "valid_from"


def test_declared_required_reports_required(rows):
    assert rows[("EVENT", "must")]["proposal"] == "required"


def test_keep_reason_wins_over_the_drop_rules(rows):
    assert rows[("EVENT", "kept")]["proposal"] == "keep" and "probe M04" in rows[("EVENT", "kept")]["reason"]


def test_full_fill_is_required(rows):
    assert rows[("EVENT", "outcome")]["fill"] == 1.0 and rows[("EVENT", "outcome")]["proposal"] == "required"


def test_interpretive_values_are_drop(tmp_path):
    observations = [_obs(i, mood="quietly hopeful and reflective") for i in range(10)] + [_obs(10 + i) for i in range(2)]
    row = _rows(_stage(tmp_path, observations, "The call happened."))[("EVENT", "mood")]
    assert row["inferred"] >= 0.6 and row["text_only"] < 0.1 and row["proposal"] == "drop"


def test_rarely_stated_is_drop(rows):
    assert rows[("EVENT", "rare")]["fill"] == 0.05 and rows[("EVENT", "rare")]["proposal"] == "drop"


def test_depended_on_is_keep(tmp_path):
    kg = _stage(tmp_path, [_obs(1, rare="x")] + [_obs(i) for i in range(2, 40)], "text")
    row = _rows(kg, {("EVENT", "rare"): ["mart"]})[("EVENT", "rare")]
    assert row["proposal"] == "keep" and row["flags"]["depended_on"] == ["mart"]


def test_payload_lists_no_values_and_carries_the_rules(tmp_path):
    out = pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, [_obs(1, mood="secret words")], "text"), depended_on={})
    assert "secret" not in json.dumps(out)
    assert out["rules"]["required_fill"] == pa.REQUIRED_FILL


def test_table_observations_are_not_counted(tmp_path):
    kg = _stage(tmp_path, [{**_obs(1, amount=3), "source_kind": "table"}], "text")
    assert ("EVENT", "amount") not in _rows(kg)


def test_depended_on_keys_reads_the_mart_declaration(monkeypatch):
    decl = SimpleNamespace(person=SimpleNamespace(entity_class="PERSON", seed_property="relation"),
                           place=SimpleNamespace(entity_class="LOCATION", seed_property=None),
                           type=SimpleNamespace(classes=["EVENT"], property="type"),
                           happening_classes=["EVENT"], wholes={})
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: decl)
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([], []))
    deps = pa.depended_on_keys("d", {"entity_types": {"PERSON": {"properties": {"relation": {}}}, "EVENT": {}}})
    assert deps[("PERSON", "relation")] == ["mart"]


def test_graph_conflicts_is_one_read_query_scoped_to_the_domain(monkeypatch):
    from _neo4j_recorder import Recorder

    rec = Recorder(respond=lambda cypher, params: [{"cls": "EVENT", "key": "mood", "n": 4}])
    monkeypatch.setattr("artmind.graph_query.read_session", lambda: rec)
    assert pa.graph_conflicts("d") == {("EVENT", "mood"): 4}
    ((cypher, params),) = rec.calls
    assert "Conflict" in cypher and "MERGE" not in cypher and params["domains"] == ["d"]
    assert "status: 'open'" in cypher and "_source: 'projection'" in cypher
    from artmind.graph_query import domain_predicate
    assert domain_predicate("e") in cypher


def test_skill_md_backticked_keys_count_as_depended_on(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: None)
    view = SimpleNamespace(spec=SimpleNamespace(name="v1", domains=("d",), cypher="MATCH (e:Entity) RETURN e.name"),
                           skill_md="Read `EVENT.outcome` and `mood`; `undeclared` and plain cause are ignored.")
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([view], []))
    plans = tmp_path / "plans"
    for slug, domains, text in (("p-in", ["d.child"], "Uses `cause`."), ("p-out", ["other"], "Uses `rare`.")):
        (plans / slug).mkdir(parents=True)
        (plans / slug / "plan.yaml").write_text(json.dumps({"domains": domains}))
        (plans / slug / "SKILL.md").write_text(text)
    monkeypatch.setattr("artmind.topics.plan_store.plans_dir", lambda: plans)
    deps = pa.depended_on_keys("d", SCHEMA)
    assert deps[("EVENT", "outcome")] == ["skill:view:v1"] and deps[("EVENT", "mood")] == ["skill:view:v1"]
    assert deps[("EVENT", "cause")] == ["skill:plan:p-in"]
    assert ("EVENT", "rare") not in deps and ("EVENT", "undeclared") not in deps


def test_view_cypher_reference_names_the_view(monkeypatch):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: None)
    view = SimpleNamespace(spec=SimpleNamespace(name="v2", domains=("d",), cypher="MATCH (e:EVENT) RETURN e.outcome"),
                           skill_md="")
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: ([view], []))
    assert pa.depended_on_keys("d", SCHEMA)[("EVENT", "outcome")] == ["view:v2"]


def test_cli_prints_the_envelope_and_next(tmp_path, monkeypatch):
    import yaml
    from click.testing import CliRunner

    import artmind.ingest as ing
    from artmind.cli import cli

    monkeypatch.setattr(ing, "KG_DIR", _stage(tmp_path, [_obs(1, outcome="x")], "text"))
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "d_schema.yaml").write_text(yaml.safe_dump({"name": "d", **SCHEMA}))
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr("artmind.cli.DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(pa, "depended_on_keys", lambda d, s: {})
    monkeypatch.setattr(pa, "graph_conflicts", lambda d: (_ for _ in ()).throw(AssertionError("no Neo4j")))

    r = CliRunner().invoke(cli, ["domains", "property-audit", "--domain", "d", "--compact"])

    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["command"] == "domains property-audit" and out["next"] == "artmind domains validate"
    assert "conflicts" not in out["rows"][0]


# ── review fixes ─────────────────────────────────────────────────────────────

TWO = {"entity_types": {"EVENT": {"properties": {"status": {}, "outcome": {}}},
                        "TASK": {"properties": {"status": {}}}}}


def _no_deps(monkeypatch, views=()):
    monkeypatch.setattr("artmind.mart.declaration.load_declaration", lambda d: None)
    monkeypatch.setattr("artmind.kg_views.store.list_views", lambda domains=None: (list(views), []))
    monkeypatch.setattr("artmind.topics.plan_store.plans_dir", lambda: Path("/nonexistent-plans"))


def _view(name, cypher, skill_md):
    return SimpleNamespace(spec=SimpleNamespace(name=name, domains=("d",), cypher=cypher), skill_md=skill_md)


def test_zero_observation_class_and_empty_domain_warning(tmp_path):
    schema = {"entity_types": {"EVENT": {"properties": {"outcome": {}}}, "PLACE": {"properties": {"region": {}}}}}
    kg = _stage(tmp_path, [_obs(1, outcome="x")], "x")
    out = pa.audit("d", schema, kg_dir=kg, depended_on={})
    (row,) = [r for r in out["rows"] if r["class"] == "PLACE"]
    assert row["proposal"] == "review" and row["reason"] == "no document observations of this class"
    assert out["warnings"] == []
    empty = pa.audit("nothing", schema, kg_dir=kg, depended_on={})
    assert {r["proposal"] for r in empty["rows"]} == {"review"}
    assert any("no staged document observations" in w for w in empty["warnings"])


def test_a_chunks_only_domain_warns(tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.schema_validate.domain_graph_extraction", lambda d: False)
    out = pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, [_obs(1)], "x"), depended_on={})
    assert any("chunks-only" in w for w in out["warnings"])


def test_a_family_root_scans_its_children_folders(tmp_path):
    kg = _stage(tmp_path, [_obs(1, outcome="x")], "x")
    child = kg / "d.child" / "doc2"
    child.mkdir(parents=True)
    (child / "observations.json").write_text(json.dumps([_obs(2, outcome="y"), _obs(3)]))
    (kg / "dx" / "doc3").mkdir(parents=True)  # not a child: no dot
    (kg / "dx" / "doc3" / "observations.json").write_text(json.dumps([_obs(4)] * 5))
    out = pa.audit("d", SCHEMA, kg_dir=kg, depended_on={})
    assert out["observations"] == {"EVENT": 3}
    assert {r["key"]: r for r in out["rows"]}["outcome"]["fill"] == round(2 / 3, 3)


def test_a_family_root_judges_declared_against_each_folders_leaf_schema(tmp_path):
    """A key only the root declares, observed in a child folder, is what retime of the child drops:
    `declared: false` there. Rows stay one per (class, key); `undeclared_in` names the domains."""
    kg = _stage(tmp_path, [_obs(1, outcome="x", cause="c")], "x")  # kg/d: the root's own folder
    child = kg / "d.child" / "doc2"
    child.mkdir(parents=True)
    (child / "observations.json").write_text(json.dumps([_obs(2, outcome="y"), _obs(3, rare="r")]))
    child_schema = {"entity_types": {"EVENT": {"properties": {"outcome": {}}}}}
    leaves = {"d": SCHEMA, "d.child": child_schema}
    rows = {r["key"]: r for r in pa.audit("d", SCHEMA, kg_dir=kg, depended_on={},
                                          leaf_schema=lambda name: leaves[name])["rows"]}
    assert rows["outcome"]["declared"] is True and "undeclared_in" not in rows["outcome"]
    assert rows["cause"]["declared"] is True, "seen only under the root, which declares it"
    assert rows["rare"]["declared"] is False and rows["rare"]["undeclared_in"] == ["d.child"]
    assert rows["rare"]["proposal"] == "drop"
    # without leaf schemas the family schema decides, as before
    plain = {r["key"]: r for r in pa.audit("d", SCHEMA, kg_dir=kg, depended_on={})["rows"]}
    assert plain["rare"]["declared"] is True


def test_a_key_declared_in_one_observing_domain_only_is_review(tmp_path):
    kg = _stage(tmp_path, [_obs(1, rare="a")], "x")
    child = kg / "d.child" / "doc2"
    child.mkdir(parents=True)
    (child / "observations.json").write_text(json.dumps([_obs(2, rare="b")]))
    leaves = {"d": SCHEMA, "d.child": {"entity_types": {"EVENT": {"properties": {}}}}}
    row = {r["key"]: r for r in pa.audit("d", SCHEMA, kg_dir=kg, depended_on={},
                                         leaf_schema=lambda name: leaves[name])["rows"]}["rare"]
    assert row["declared"] is False and row["undeclared_in"] == ["d.child"] and row["proposal"] == "review"


def test_the_cli_uses_the_family_schema(tmp_path, monkeypatch):
    import yaml
    from click.testing import CliRunner

    import artmind.ingest as ing
    from artmind.cli import cli

    kg = _stage(tmp_path, [_obs(1, outcome="x")], "x")
    (kg / "d.child" / "doc2").mkdir(parents=True)
    (kg / "d.child" / "doc2" / "observations.json").write_text(json.dumps(
        [{"id": "p", "entity_class": "PLACE", "chunk_id": "c9", "region": "north"},
         _obs(5, outcome="seen in the child")]))
    monkeypatch.setattr(ing, "KG_DIR", kg)
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "d_schema.yaml").write_text(yaml.safe_dump({"name": "d", **SCHEMA}))
    (schemas / "d.child_schema.yaml").write_text(yaml.safe_dump(
        {"name": "d.child", "entity_types": {"PLACE": {"kind": "recurrent", "properties": {"region": {}}}}}))
    monkeypatch.setattr("artmind.cli.DOMAIN_SCHEMAS_DIR", schemas)
    seen = {}
    monkeypatch.setattr(pa, "depended_on_keys", lambda d, s: seen.setdefault("schema", s) and {})

    r = CliRunner().invoke(cli, ["domains", "property-audit", "--domain", "d", "--compact"])

    assert r.exit_code == 0, r.output
    rows = {(x["class"], x["key"]): x for x in json.loads(r.stdout)["rows"]}
    assert rows[("PLACE", "region")]["declared"] is True
    assert "PLACE" in seen["schema"]["entity_types"]
    # EVENT.outcome is declared by the root only; d.child's own schema (which retime and
    # extraction of d.child read) does not, so its observation there makes it undeclared.
    assert rows[("EVENT", "outcome")]["declared"] is False
    assert rows[("EVENT", "outcome")]["undeclared_in"] == ["d.child"]


def test_a_malformed_schema_is_a_click_error(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from artmind.cli import cli

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "d_schema.yaml").write_text("name: d\nentity_types: [\n")
    monkeypatch.setattr("artmind.cli.DOMAIN_SCHEMAS_DIR", schemas)
    r = CliRunner().invoke(cli, ["domains", "property-audit", "--domain", "d"])
    assert r.exit_code == 1 and "d_schema.yaml" in r.output


def test_a_declared_own_key_is_reserved_and_review(tmp_path):
    schema = {"entity_types": {"EVENT": {"properties": {"context": {}, "outcome": {}}}}}
    row = {r["key"]: r for r in pa.audit("d", schema, kg_dir=_stage(tmp_path, [_obs(1, context=["a"])], "x"),
                                         depended_on={})["rows"]}["context"]
    assert row["flags"]["reserved"] is True and row["proposal"] == "review"
    assert row["reason"] == "collides with an observation field"


def test_numeric_and_short_values_are_evidence(tmp_path):
    observations = [{**_obs(i, count=3, code="7"), "description": "seen"} for i in range(5)]
    schema = {"entity_types": {"EVENT": {"properties": {"count": {}, "code": {}}}}}
    rows = {r["key"]: r for r in pa.audit("d", schema, kg_dir=_stage(tmp_path, observations, "3 items, room 7"),
                                          depended_on={})["rows"]}
    assert rows["count"]["text_only"] == 1.0 and rows["count"]["inferred"] == 0.0
    assert rows["code"]["text_only"] == 1.0


def test_distinct_values_use_the_whole_normalised_value(tmp_path):
    observations = [_obs(1, tags=["A", "b"]), _obs(2, tags=["a", "b"]), _obs(3, tags=["b", "a"]),
                    _obs(4, spec={"x": 1}), _obs(5, spec={"x": 1}), _obs(6, label=" Big Room "), _obs(7, label="big room"),
                    _obs(8, label="big-room")]
    rows = {r["key"]: r for r in pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, observations, "x"),
                                          depended_on={})["rows"]}
    assert rows["tags"]["distinct_values"] == 2  # ["a","b"] twice after lower-casing, ["b","a"] once
    assert rows["spec"]["distinct_values"] == 1
    assert rows["label"]["distinct_values"] == 2
    assert "distinct_capped" not in rows["label"]


def test_distinct_values_report_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "DISTINCT_CAP", 3)
    row = {r["key"]: r for r in pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, [_obs(i, label=f"v{i}") for i in range(5)],
                                                                      "x"), depended_on={})["rows"]}["label"]
    assert row["distinct_values"] == 3 and row["distinct_capped"] is True


def test_observations_without_chunk_or_class(tmp_path):
    observations = [{**_obs(1, outcome="zebra"), "chunk_id": None}, {"id": "o9", "outcome": "x"}]
    out = pa.audit("d", SCHEMA, kg_dir=_stage(tmp_path, observations, "x"), depended_on={})
    row = {r["key"]: r for r in out["rows"]}["outcome"]
    assert out["observations"] == {"EVENT": 1} and row["fill"] == 1.0
    assert row["inferred"] == 0.0 and row["in_description"] == 0.0


def test_an_unqualified_skill_key_needs_one_owner_or_a_bound_class(monkeypatch):
    _no_deps(monkeypatch, [_view("v1", "MATCH (e:Entity) RETURN e.name", "Reads `status` and `outcome`.")])
    deps = pa.depended_on_keys("d", TWO)
    assert deps == {("EVENT", "outcome"): ["skill:view:v1"]}  # `status` is ambiguous: two classes declare it
    _no_deps(monkeypatch, [_view("v2", "MATCH (t:TASK) RETURN t.name", "Reads `status`.")])
    assert pa.depended_on_keys("d", TWO) == {("TASK", "status"): ["skill:view:v2"]}


def test_a_qualified_skill_key_counts_for_that_class_only(monkeypatch):
    _no_deps(monkeypatch, [_view("v1", "", "Reads `TASK.status`, `TASK.outcome` and `PLACE.status`.")])
    assert pa.depended_on_keys("d", TWO) == {("TASK", "status"): ["skill:view:v1"]}


def test_no_row_field_carries_a_staged_value(tmp_path):
    values = {"outcome": "zanzibar accord", "mood": "melancholy", "rare": 98765, "tags": ["quokka", "wombat"],
              "invented": {"inner": "platypus"}}
    kg = _stage(tmp_path, [_obs(i, **values) for i in range(3)], "zanzibar accord melancholy 98765 quokka platypus")
    dumped = json.dumps(pa.audit("d", SCHEMA, kg_dir=kg, depended_on={}))
    for word in ("zanzibar", "accord", "melancholy", "98765", "quokka", "wombat", "platypus", "inner"):
        assert word not in dumped
