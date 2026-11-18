"""`ingest retime` re-reads raw temporal values with parse_valid (spec 2026-11-15 §8). Synthetic."""
from artmind.retime import retime_observations


def _obs(result):
    """retime_observations returns (observations, dropped)."""
    return result[0] if isinstance(result, tuple) else result


SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent", "properties": {"date_or_time": {"temporal": "valid_from"}}}},
          "temporal": {"defaults": {"time_source": "default_ingestion"}}}


def test_retime_fills_end_precision_and_time_of_day_from_the_stored_raw_value():
    staged = [{"id": "o1", "key": "lake trip|EVENT|pj", "entity_class": "EVENT", "_domain": "pj",
               "canonical_name": "Lake trip", "date_or_time": "2023-09-07 to 2023-09-08 (multi-day)",
               "_valid_from": "2023-09-07"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2023-09-01", "_valid_time_source": "filename"}, SCHEMA))
    assert (new[0]["_valid_to"], new[0]["_valid_precision"], new[0]["_valid_time_of_day"]) == (
        "2023-09-09", "day", "multi-day")


def test_retime_marks_a_default_document_date_as_none():
    staged = [{"id": "o1", "key": "k|EVENT|pj", "entity_class": "EVENT", "_domain": "pj"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2026-11-15", "_valid_time_source": "default_ingestion"}, SCHEMA))
    assert new[0]["_valid_precision"] == "none"


def test_retime_drops_a_stale_time_of_day():
    staged = [{"id": "o1", "key": "k|EVENT|pj", "entity_class": "EVENT", "_domain": "pj",
               "date_or_time": "2023-09-07", "_valid_time_of_day": "evening"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2023-09-01", "_valid_time_source": "filename"}, SCHEMA))
    assert "_valid_time_of_day" not in new[0]


def test_retime_rekeys_an_occasion_and_rederives_kind():
    schema = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion",
                                            "properties": {"date_or_time": {"temporal": "valid_from"}}}}}
    staged = [{"id": "o1", "key": "tennis|ACTIVITY|pj", "entity_class": "ACTIVITY", "_domain": "pj", "_kind": "recurrent",
               "canonical_name": "Tennis", "name": "tennis", "date_or_time": "2024-03-30"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2024-03-30", "_valid_time_source": "filename"}, schema))
    assert (new[0]["key"], new[0]["_kind"]) == ("tennis~2024-03-30|ACTIVITY|pj", "occurrent")
    assert new[0]["id"] == "o1"      # observation ids never change


def test_retime_leaves_table_observations_keys_alone():
    schema = {"entity_types": {"ACTIVITY": {"kind": "occurrent", "identity": "occasion"}}}
    staged = [{"id": "o1", "key": "row 1|ACTIVITY|pj", "entity_class": "ACTIVITY", "_domain": "pj", "source_kind": "table"}]
    new = _obs(retime_observations(staged, {"_valid_from": "2024-03-30", "_valid_time_source": "filename"}, schema))
    assert new[0]["key"] == "row 1|ACTIVITY|pj"


DOC_WITH_END = {"_valid_from": "2023-09-01", "_valid_to": "2023-12-31", "_valid_time_source": "filename"}


def test_retime_gives_an_own_dated_fact_only_its_own_end():
    staged = [{"id": "o1", "key": "k|EVENT|pj", "entity_class": "EVENT", "_domain": "pj",
               "date_or_time": "2023-09-07", "_valid_to": "2023-12-31"}]
    new = _obs(retime_observations(staged, DOC_WITH_END, SCHEMA))
    assert new[0]["_valid_from"] == "2023-09-07"
    assert "_valid_to" not in new[0]


def test_retime_gives_a_document_dated_fact_the_document_end():
    staged = [{"id": "o1", "key": "k|EVENT|pj", "entity_class": "EVENT", "_domain": "pj"}]
    new = _obs(retime_observations(staged, DOC_WITH_END, SCHEMA))
    assert (new[0]["_valid_from"], new[0]["_valid_to"]) == ("2023-09-01", "2023-12-31")
