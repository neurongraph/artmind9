"""The one-time OBJECT -> ACTIVITY move (plan Task 35, question 14). Invented names."""
from artmind.migrations.temporal_v1.reclassify import reclassify

SCHEMA = {"entity_types": {"OBJECT": {"kind": "recurrent", "identity": "name"},
                           "ACTIVITY": {"kind": "occurrent", "identity": "occasion"}}}


def _obj(name, **extra):
    return {"id": name, "entity_class": "OBJECT", "canonical_name": name, "type": "vehicle", "_kind": "recurrent",
            **extra}


def test_a_whole_travel_word_moves_and_drops_type():
    out, moved = reclassify([_obj("Flight to Lisbon"), _obj("Flightless bird figurine")], SCHEMA)
    assert [o["entity_class"] for o in out] == ["ACTIVITY", "OBJECT"]
    assert "type" not in out[0] and out[0]["_kind"] == "occurrent"
    assert out[1]["type"] == "vehicle"
    assert moved == {"OBJECT->ACTIVITY": 1}


def test_the_match_ignores_case_and_keeps_ids():
    out, _ = reclassify([_obj("Night TRAIN home"), _obj("Taxi"), _obj("Ridealong pen")], SCHEMA)
    assert [o["entity_class"] for o in out] == ["ACTIVITY", "ACTIVITY", "OBJECT"]
    assert [o["id"] for o in out] == ["Night TRAIN home", "Taxi", "Ridealong pen"]


def test_a_schema_without_activity_moves_nothing():
    schema = {"entity_types": {"OBJECT": {"kind": "recurrent"}}}
    out, moved = reclassify([_obj("Flight to Lisbon")], schema)
    assert out[0]["entity_class"] == "OBJECT" and not moved


def test_a_table_row_keeps_its_class():
    out, moved = reclassify([_obj("Cab fare", source_kind="table")], SCHEMA)
    assert out[0]["entity_class"] == "OBJECT" and not moved


def test_plurals_move_too():
    out, moved = reclassify([_obj("Flights to Goa"), _obj("Rides"), _obj("Trips abroad"), _obj("Cabinet")], SCHEMA)
    assert [o["entity_class"] for o in out] == ["ACTIVITY", "ACTIVITY", "ACTIVITY", "OBJECT"]
    assert moved == {"OBJECT->ACTIVITY": 3}
