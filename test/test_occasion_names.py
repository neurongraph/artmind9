"""No dates in happening names (spec 2026-11-15 §5.2, plan question 5). Invented names."""
import pytest

from artmind.observations import normalize_name, strip_occasion_date


@pytest.mark.parametrize("raw, expected", [
    ("Tennis - 2024-03-30", "Tennis"),
    ("Tennis on 2024-03-30", "Tennis"),
    ("Tennis 2024-03-30", "Tennis"),
    ("Tennis (2024-03-30)", "Tennis"),
    ("Tennis: 2024-03-30", "Tennis"),
    ("2024-03-30 Tennis", "Tennis"),
    ("2024-03-30: Tennis", "Tennis"),
    ("30 March 2024 - Tennis", "Tennis"),
    ("Tennis on Saturday", "Tennis"),
    ("Tennis (Saturday)", "Tennis"),
    ("Tennis today", "Tennis"),
    ("Dinner last night", "Dinner"),
    ("Tennis, 30 March 2024", "Tennis"),
    ("Tennis on March 30, 2024", "Tennis"),
    ("Tennis — Saturday, 30 March 2024", "Tennis"),
    ("Tennis on Saturday 30 March 2024", "Tennis"),
    ("Tennis on the 30th", "Tennis"),
    ("Dinner, on the 3rd of May", "Dinner"),
    ("Tennis 30/03/2024", "Tennis"),
    ("Flight AI 302 on 2024-03-30", "Flight AI 302"),
    # Not dates, or a weekday that is part of the name: kept.
    ("Lantern festival lights", "Lantern festival lights"),
    ("Q3 review", "Q3 review"),
    ("Flight to Lisbon", "Flight to Lisbon"),
    ("Flight AI 302", "Flight AI 302"),
    ("Room 101", "Room 101"),
    ("Apollo 11", "Apollo 11"),
    ("Olympics 2024", "Olympics 2024"),
    ("2024 Olympics", "2024 Olympics"),
    ("Q3 2024 review", "Q3 2024 review"),
    ("Meeting in Room 12 2024", "Meeting in Room 12 2024"),
    ("Easter Sunday", "Easter Sunday"),
    ("Black Friday sale", "Black Friday sale"),
    ("Sunday roast", "Sunday roast"),
    ("Mayday drill", "Mayday drill"),
    ("Release 2024-03-30 hotfix", "Release 2024-03-30 hotfix"),
    ("Trip 28-30 March 2024", "Trip 28-30 March 2024"),
])
def test_strip(raw, expected):
    assert strip_occasion_date(raw) == expected
    assert strip_occasion_date(expected) == expected  # idempotent


@pytest.mark.parametrize("raw", ["2024-03-30", "today", "Saturday", "2024-03-30 - today", "", None])
def test_a_name_that_is_only_a_date_is_never_emptied(raw):
    assert strip_occasion_date(raw) == (raw or "")


def test_stripped_variants_share_one_normalised_name():
    names = ["Tennis - 2024-03-30", "Tennis on 2024-04-02", "tennis today", "Tennis on Saturday"]
    assert {normalize_name(strip_occasion_date(n)) for n in names} == {"tennis"}


# ── where keys are built: ingest, retime, update ────────────────────────────

SCHEMA = {"entity_types": {
    "ACTIVITY": {"kind": "occurrent", "identity": "occasion", "properties": {"date_or_time": {"temporal": "valid_from"}}},
    "AMENDMENT": {"kind": "occurrent", "identity": "name", "properties": {}},
    "PERSON": {"kind": "recurrent", "identity": "name", "properties": {}}},
    "temporal": {"defaults": {"time_source": "default_ingestion"}}}
DOCUMENT = {"id": "d1", "_valid_from": "2024-03-30", "_valid_time_source": "filename"}


def _ingest(name, cls, canonical=None):
    from artmind import ingest as ing

    entities = [{"id": "e1", "name": name, "entity_class": cls, "chunk_id": "c1", "_domain": "pj"}]
    observations, _ = ing._build_observations(
        entities, [{"id": "e1", "properties": {}}], {name: canonical} if canonical else {}, SCHEMA, DOCUMENT)
    return observations[0]


def test_ingest_strips_the_canonical_name_of_an_occasion_and_keeps_the_raw_name():
    from artmind.observations import observation_id

    obs = _ingest("Tennis on 2024-03-30", "ACTIVITY")
    assert (obs["name"], obs["canonical_name"], obs["key"]) == (
        "Tennis on 2024-03-30", "Tennis", "tennis~2024-03-30|ACTIVITY|pj")
    assert obs["id"] == observation_id("c1", "Tennis", "ACTIVITY", "pj")
    # The canonicalisation pass's name is the one stripped.
    assert _ingest("tennis", "ACTIVITY", canonical="Tennis - Saturday")["canonical_name"] == "Tennis"


@pytest.mark.parametrize("cls", ["AMENDMENT", "PERSON"])
def test_ingest_leaves_other_identities_alone(cls):
    assert _ingest("Lease change on 2024-03-30", cls)["canonical_name"] == "Lease change on 2024-03-30"


def test_retime_strips_like_ingest_and_is_idempotent():
    from artmind.retime import retime_observations

    built = _ingest("Tennis on 2024-03-30", "ACTIVITY")
    staged = {**built, "canonical_name": "Tennis on 2024-03-30", "key": "tennis on 2024-03-30|ACTIVITY|pj"}
    (once,), _ = retime_observations([staged], DOCUMENT, SCHEMA)
    (twice,), _ = retime_observations([once], DOCUMENT, SCHEMA)
    assert (once["key"], once["canonical_name"], once["name"]) == (built["key"], "Tennis", "Tennis on 2024-03-30")
    assert twice == once
    name_class = {**staged, "entity_class": "AMENDMENT", "key": "x|AMENDMENT|pj"}
    (kept,), _ = retime_observations([name_class], DOCUMENT, SCHEMA)
    assert kept["canonical_name"] == "Tennis on 2024-03-30"


def test_update_confirm_names_an_occasion_without_its_date(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch

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
        upd.write_user_chat(
            session_id="s", raw_text="Hill walk on 3 April.", domain="testchat", user_id="u",
            resolutions=[{"entity_temp_id": "e0", "action": "create", "node_id": None}],
            extracted_entities=[{"temp_id": "e0", "name": "Hill walk on 2025-04-03", "entity_class": "OUTING",
                                 "properties": {"date_or_time": "2025-04-03"}}],
            extracted_relationships=[])

    assert find.call_args.args[1:] == ("Hill walk", "OUTING", "testchat", "2025-04-03")
    (props,) = [kw["props"] for c, kw in calls if "CREATE (n:Observation {id: $id})" in c and "props" in kw]
    assert (props["key"], props["canonical_name"], props["name"]) == (
        "hill walk~2025-04-03|OUTING|testchat", "Hill walk", "Hill walk on 2025-04-03")
