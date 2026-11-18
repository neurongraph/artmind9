"""A fact's valid time from its `temporal:` properties (spec 2026-11-15 §3.2). Synthetic."""
from artmind import ingest as ing


def _schema(with_end=False):
    props = {"date_or_time": {"temporal": "valid_from"}}
    if with_end:
        props["ends"] = {"temporal": "valid_to"}
    return {"entity_types": {"EVENT": {"kind": "occurrent", "properties": props}}}


def _build(props, schema, document=None):
    entities = [{"id": "e1", "name": "Lake trip", "entity_class": "EVENT", "chunk_id": "c1", "_domain": "pj"}]
    document = document or {"id": "d1", "_valid_from": "2023-09-01", "_valid_time_source": "filename"}
    observations, _ = ing._build_observations(entities, [{"id": "e1", "properties": props}], {}, schema, document)
    return observations[0]


def test_a_range_sets_valid_to_precision_and_time_of_day():
    obs = _build({"date_or_time": "2023-09-07 to 2023-09-08 (multi-day)"}, _schema())
    assert (obs["_valid_from"], obs["_valid_to"], obs["_valid_precision"], obs["_valid_time_of_day"]) == (
        "2023-09-07", "2023-09-09", "day", "multi-day")
    assert obs["date_or_time"] == "2023-09-07 to 2023-09-08 (multi-day)"   # the raw value is kept


def test_a_tagged_valid_to_property_wins_over_the_range_end():
    obs = _build({"date_or_time": "2023-09-07 to 2023-09-08", "ends": "2023-09-20"}, _schema(with_end=True))
    assert obs["_valid_to"] == "2023-09-20"


def test_a_month_value_no_longer_falls_back_to_the_document_date():
    obs = _build({"date_or_time": "2026-06 (planned)"}, _schema())
    assert (obs["_valid_from"], obs["_valid_precision"], obs["_valid_time_source"]) == ("2026-06-01", "month", "property")


def test_an_unreadable_value_falls_back_to_the_document_date():
    obs = _build({"date_or_time": "Ongoing"}, _schema())
    assert (obs["_valid_from"], obs["_valid_precision"], obs["_valid_time_source"]) == ("2023-09-01", "day", "filename")
    assert "_valid_time_of_day" not in obs


def test_a_default_document_date_has_precision_none():
    schema = {**_schema(), "temporal": {"defaults": {"time_source": "default_ingestion"}}}
    obs = _build({}, schema, {"id": "d1", "_valid_from": "2026-11-15", "_valid_time_source": "default_ingestion"})
    assert (obs["_valid_from"], obs["_valid_precision"]) == ("2026-11-15", "none")


def test_fact_time_counts_unparsed_values():
    fact = ing._fact_valid_time({"EVENT": {"valid_from": "date_or_time"}}, "EVENT", {"date_or_time": "TBD"})
    assert (fact.valid_from, fact.unparsed) == (None, 1)


def test_precision_and_time_of_day_are_system_keys_copied_from_the_winner():
    from artmind.projection import _OBSERVATION_SYSTEM_KEYS, merge_observations

    assert {"_valid_precision", "_valid_time_of_day", "_due", "_review", "_revised_at"} <= _OBSERVATION_SYSTEM_KEYS
    obs = _build({"date_or_time": "2026-01-07 (evening)"}, _schema())
    merged = merge_observations([obs])["props"]
    assert (merged["_valid_precision"], merged["_valid_time_of_day"]) == ("day", "evening")


DOC_WITH_END = {"id": "d1", "_valid_from": "2023-09-01", "_valid_to": "2023-12-31", "_valid_time_source": "filename"}


def test_a_fact_with_its_own_date_keeps_only_its_own_end():
    obs = _build({"date_or_time": "2023-09-07"}, _schema(), DOC_WITH_END)
    assert obs["_valid_from"] == "2023-09-07"
    assert "_valid_to" not in obs   # a single-day fact is open-ended: the document's end is not borrowed
    ranged = _build({"date_or_time": "2023-09-07 to 2023-09-08"}, _schema(), DOC_WITH_END)
    assert ranged["_valid_to"] == "2023-09-09"


def test_a_fact_dated_by_its_document_inherits_the_document_end():
    obs = _build({}, _schema(), DOC_WITH_END)
    assert (obs["_valid_from"], obs["_valid_to"]) == ("2023-09-01", "2023-12-31")
    unreadable = _build({"date_or_time": "Ongoing"}, _schema(), DOC_WITH_END)
    assert unreadable["_valid_to"] == "2023-12-31"


def test_only_a_fact_dated_by_its_document_keeps_a_copy_of_the_document_end():
    assert _build({}, _schema(), DOC_WITH_END)["_doc_valid_to"] == "2023-12-31"
    assert "_doc_valid_to" not in _build({"date_or_time": "2023-09-07"}, _schema(), DOC_WITH_END)
    assert "_doc_valid_to" not in _build({}, _schema())   # the document states no end
