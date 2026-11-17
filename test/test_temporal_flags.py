"""`temporal: due|review` and document `revised` (spec 2026-11-15 §4). Synthetic."""
from artmind import ingest as ing
from artmind import schema_validate as sv
from artmind.retime import retime_observations
from artmind.temporal import lift_document_dates

SCHEMA = {"entity_types": {"ACTION": {"kind": "recurrent", "properties": {
    "due_date": {"temporal": "due"}, "next_check": {"temporal": "review"}}}}}
META = {"kinds": {"recurrent": "", "occurrent": ""}}


def test_due_and_review_never_set_valid_from():
    entities = [{"id": "e1", "name": "Fix the gate", "entity_class": "ACTION", "chunk_id": "c1", "_domain": "pg"}]
    props = [{"id": "e1", "properties": {"due_date": "2026-Q2", "next_check": "2026-05-01"}}]
    observations, _ = ing._build_observations(entities, props, {}, SCHEMA, {
        "id": "d", "_valid_from": "2026-01-10", "_valid_time_source": "header"})
    obs = observations[0]
    assert (obs["_valid_from"], obs["_due"], obs["_review"]) == ("2026-01-10", "2026-04-01", "2026-05-01")
    assert obs["_valid_time_source"] == "header"


def test_retime_writes_due_and_drops_a_stale_one():
    staged = [{"id": "o1", "key": "fix the gate|ACTION|pg", "entity_class": "ACTION", "_domain": "pg",
               "due_date": "2026-06-30", "_valid_from": "2026-06-30", "_review": "2020-01-01"}]
    new, _ = retime_observations(staged, {"_valid_from": "2026-01-10", "_valid_time_source": "header"}, SCHEMA)
    assert (new[0]["_valid_from"], new[0]["_due"]) == ("2026-01-10", "2026-06-30")
    assert "_review" not in new[0]


def test_revised_label_does_not_become_the_effective_date():
    md = "| Effective Date | 2026-01-01 |\n| Last Updated | 2026-03-04 |\n"
    out = lift_document_dates(md, {}, {"revised": ["Last Updated"], "valid_from": ["Effective Date"]})
    assert (out["valid_from"], out["revised"], out["time_source"]) == ("2026-01-01", "2026-03-04", "header")


def test_revised_alone_leaves_valid_from_to_the_default():
    out = lift_document_dates("| Last Updated | 2026-03-04 |\n", {},
                              {"valid_from": ["Effective Date"], "revised": ["Last Updated"]},
                              {"valid_from": "ingestion_date", "time_source": "default_ingestion"})
    assert out["revised"] == "2026-03-04"
    assert (out["valid_precision"], out["time_source"]) == ("none", "default_ingestion")


def test_revised_reaches_the_document_as_revised_at(tmp_path):
    md = tmp_path / "policy.md"
    md.write_text("| Effective Date | 2026-01-01 |\n| Last Updated | 2026-03-04 |\n", encoding="utf-8")
    schema = {"temporal": {"document": {"valid_from": ["Effective Date"], "revised": ["Last Updated"]}}}
    out = ing._document_valid_time(md, {}, schema)
    assert (out["_valid_from"], out["_revised_at"]) == ("2026-01-01", "2026-03-04")


def test_unknown_temporal_value_is_an_error():
    bad = {"entity_types": {"X": {"kind": "recurrent", "properties": {"d": {"temporal": "deadline"}}}}}
    assert any("temporal" in e for e in sv.validate_schema(bad, META))
    good = {"entity_types": {"X": {"kind": "recurrent", "properties": {"d": {"temporal": "review"}}}}}
    assert sv.validate_schema(good, META) == []


def test_unknown_document_temporal_key_is_an_error():
    bad = {"temporal": {"document": {"valid_from": ["Date"], "updated": ["Last Updated"]}}, "entity_types": {}}
    (err,) = sv.validate_schema(bad, META, "b")
    assert "temporal.document.updated" in err
    ok = {"temporal": {"document": {"valid_from": ["Date"], "revised": ["Last Updated"], "version": ["V"]}},
          "entity_types": {}}
    assert sv.validate_schema(ok, META, "b") == []


def test_occasion_without_a_date_property_warns():
    schema = {"entity_types": {"SAR": {"kind": "occurrent", "properties": {"filing_obligation": {}}},
                               "BANK": {"kind": "recurrent", "properties": {}}}}
    (w,) = sv.temporal_warnings(schema, "b")
    assert "SAR" in w and "valid_from" in w
