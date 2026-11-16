"""`domains property-audit` rules (spec 2026-11-15 §3). Synthetic staging only."""
import json
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
    assert deps[("EVENT", "outcome")] == ["skill"] and deps[("EVENT", "mood")] == ["skill"]
    assert deps[("EVENT", "cause")] == ["skill"]
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
