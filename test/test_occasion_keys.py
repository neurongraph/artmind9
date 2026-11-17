"""Day keys for occasion classes (spec 2026-11-15 §5.2, plan questions 1, 2, 21). Invented names."""
from unittest.mock import MagicMock, patch

import pytest

from artmind import ingest as ing
from artmind.observations import aggregate_key, key_string, occasion_day, split_occasion
from artmind.retime import retime_observations

SCHEMA = {"entity_types": {
    "ACTIVITY": {"kind": "occurrent", "identity": "occasion", "properties": {"date_or_time": {"temporal": "valid_from"}}},
    "PERSON": {"kind": "recurrent", "identity": "name", "properties": {}}},
    "temporal": {"defaults": {"time_source": "default_ingestion"}}}


def test_the_day_goes_in_the_name_part():
    key = aggregate_key("Tennis", "ACTIVITY", "pj", occurs_on="2024-03-30")
    assert key == ("tennis~2024-03-30", "ACTIVITY", "pj")
    assert key_string(key).count("|") == 2
    assert split_occasion(key[0]) == ("tennis", "2024-03-30")
    assert split_occasion("tennis") == ("tennis", None)
    assert split_occasion("approx ~ten") == ("approx ~ten", None)


def test_a_declared_identity_ignores_the_day():
    assert aggregate_key("x", "ACTIVITY", "pj", identity="Row 7", occurs_on="2024-03-30")[0] == "row 7"


def test_occasion_day():
    assert occasion_day("2024-03-30", "day") == "2024-03-30"
    assert occasion_day("2024-03-30", "none") is None
    assert occasion_day(None, "day") is None
    assert occasion_day("2024-03", "month") is None


def _document(doc_date="2024-03-30", source="filename"):
    return {"id": "d1", "_valid_from": doc_date, "_valid_time_source": source}


def _obs(name, cls, day_prop, doc_date="2024-03-30", source="filename"):
    entities = [{"id": "e1", "name": name, "entity_class": cls, "chunk_id": "c1", "_domain": "pj"}]
    props = [{"id": "e1", "properties": {"date_or_time": day_prop} if day_prop else {}}]
    observations, _ = ing._build_observations(entities, props, {}, SCHEMA, _document(doc_date, source))
    return observations[0]


def test_same_name_two_days_two_keys():
    a, b = _obs("Tennis", "ACTIVITY", "2024-03-30"), _obs("Tennis", "ACTIVITY", "2024-04-02")
    assert a["key"] != b["key"] and a["key"].startswith("tennis~2024-03-30|")


def test_an_undated_mention_keys_on_its_documents_day():
    assert _obs("Tennis", "ACTIVITY", None, doc_date="2024-05-01")["key"] == "tennis~2024-05-01|ACTIVITY|pj"


def test_a_default_document_date_adds_no_day():
    obs = _obs("Tennis", "ACTIVITY", None, doc_date="2026-11-15", source="default_ingestion")
    assert obs["_valid_precision"] == "none"
    assert obs["key"] == "tennis|ACTIVITY|pj"


def test_a_range_keys_on_its_start_day():
    assert _obs("Lake trip", "ACTIVITY", "2023-09-07 to 2023-09-08")["key"] == "lake trip~2023-09-07|ACTIVITY|pj"


def test_name_classes_are_unchanged():
    assert _obs("Alex", "PERSON", None)["key"] == "alex|PERSON|pj"


def test_an_undeclared_class_is_an_occasion():
    assert _obs("Fall", "HEALTH_INCIDENT", None)["key"] == "fall~2024-03-30|HEALTH_INCIDENT|pj"


def test_the_observation_id_carries_no_day():
    """One chunk is one day: the id stays chunk-scoped, so a re-key moves the node in place."""
    from artmind.observations import observation_id

    assert _obs("Tennis", "ACTIVITY", "2024-03-30")["id"] == observation_id("c1", "Tennis", "ACTIVITY", "pj")


@pytest.mark.parametrize("day_prop, doc_date, source", [
    ("2024-03-30", "2024-03-29", "filename"),
    (None, "2024-05-01", "filename"),
    (None, "2026-11-15", "default_ingestion"),
    ("2023-09-07 to 2023-09-08", "2023-09-01", "filename"),
])
@pytest.mark.parametrize("name, cls", [("Tennis", "ACTIVITY"), ("Alex", "PERSON")])
def test_ingest_and_retime_build_the_same_key(day_prop, doc_date, source, name, cls):
    built = _obs(name, cls, day_prop, doc_date, source)
    staged = {**built, "key": f"{name.lower()}|{cls}|pj"}  # as staged before day keys
    (again,), _ = retime_observations([staged], _document(doc_date, source), SCHEMA)
    assert (again["key"], again["id"], again["_kind"]) == (built["key"], built["id"], built["_kind"])


def test_table_rows_keep_their_declared_key():
    staged = {"id": "o1", "key": "row 1|ACTIVITY|pj", "entity_class": "ACTIVITY", "_domain": "pj",
              "canonical_name": "Row 1", "source_kind": "table", "date_or_time": "2024-03-30"}
    (again,), _ = retime_observations([staged], _document(), SCHEMA)
    assert again["key"] == "row 1|ACTIVITY|pj"


def test_every_key_parser_accepts_a_day_key():
    from artmind import projection, refine_graph, same_as, sameas
    from artmind.update import _stored_identity

    key = "tennis~2024-03-30|ACTIVITY|pj"
    expected = ("tennis~2024-03-30", "ACTIVITY", "pj")
    assert projection._parse_key(key) == expected
    assert same_as._parse_key(key) == expected
    assert sameas._parse_key(key) == expected
    assert refine_graph._parse_key(key) == expected
    assert _stored_identity(key) == "tennis~2024-03-30"
    # update's link path re-keys from the stored name part: the same key, day included.
    assert key_string(aggregate_key("ignored", "ACTIVITY", "pj", identity=_stored_identity(key))) == key


def test_update_confirm_keys_an_occasion_on_its_facts_day(tmp_path, monkeypatch):
    import yaml

    import artmind.temporal as temporal
    import artmind.update as upd

    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "testchat_schema.yaml").write_text(yaml.safe_dump({
        "name": "testchat",
        "entity_types": {"OUTING": {"kind": "occurrent", "identity": "occasion",
                                    "properties": {"date_or_time": {"temporal": "valid_from"}}}},
    }))
    monkeypatch.setattr(temporal, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(upd, "DOMAIN_SCHEMAS_DIR", schemas)
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None, "retracts": ["old-obs"]}]
    extracted = [{"temp_id": "e0", "name": "Hill walk", "entity_class": "OUTING",
                  "properties": {"date_or_time": "2025-04-03"}}]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        result.single.return_value = None
        result.data.return_value = []
        return result

    session = MagicMock()
    session.run.side_effect = run_side_effect
    session.execute_write.side_effect = lambda fn, *a, **k: fn(session, *a, **k)
    find = MagicMock(return_value=None)
    with patch("artmind.update.embed_document", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", find), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.projection.rebuild", return_value={}):
        mock_ctx.return_value.__enter__.return_value = session
        upd.write_user_chat(session_id="s", raw_text="Hill walk on 3 April.", domain="testchat", user_id="u",
                            resolutions=resolutions, extracted_entities=extracted, extracted_relationships=[])

    assert find.call_args.args[1:] == ("Hill walk", "OUTING", "testchat", "2025-04-03")
    written = [kw["props"] for c, kw in calls if "CREATE (n:Observation {id: $id})" in c and "props" in kw]
    assert {p["key"] for p in written} == {"hill walk~2025-04-03|OUTING|testchat"}
    assert len(written) == 2  # the fact and its retraction, under one key


def test_find_existing_entity_looks_up_the_day_key():
    from artmind.observations import entity_id
    from artmind.update import _find_existing_entity

    session = MagicMock()
    session.run.return_value.single.return_value = None
    _find_existing_entity(session, "Tennis", "ACTIVITY", "pj", "2024-03-30")
    assert session.run.call_args.kwargs["id"] == entity_id(("tennis~2024-03-30", "ACTIVITY", "pj"))


def test_entity_history_by_name_matches_day_keys(monkeypatch):
    from artmind import graph_query

    calls = []
    monkeypatch.setattr(graph_query, "_run_read_query", lambda c, p: calls.append((c, p)) or [])
    graph_query.entity_history(["pj"], name="Tennis")
    cypher, params = calls[0]
    assert "split(o.key, '|')[0] STARTS WITH $nameKey + '~'" in cypher and params["nameKey"] == "tennis"
