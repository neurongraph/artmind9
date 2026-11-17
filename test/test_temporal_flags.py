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


def test_a_known_temporal_value_is_valid():
    good = {"entity_types": {"X": {"kind": "recurrent", "properties": {"d": {"temporal": "review"}}}}}
    assert sv.validate_schema(good, META) == []


def test_unknown_document_temporal_key_is_an_error():
    bad = {"temporal": {"document": {"valid_from": ["Date"], "updated": ["Last Updated"]}}, "entity_types": {}}
    (err,) = sv.validate_schema(bad, META, "b")
    assert "temporal.document.updated: unknown key" in err
    ok = {"temporal": {"document": {"valid_from": ["Date"], "revised": ["Last Updated"], "version": ["V"]}},
          "entity_types": {}}
    assert sv.validate_schema(ok, META, "b") == []


def test_occasion_without_a_date_property_warns():
    schema = {"entity_types": {"SAR": {"kind": "occurrent", "properties": {"filing_obligation": {}}},
                               "BANK": {"kind": "recurrent", "properties": {}}}}
    (w,) = sv.temporal_warnings(schema, "b")
    assert "SAR" in w and "valid_from" in w


def test_unknown_temporal_value_is_an_error():
    bad = {"entity_types": {"X": {"kind": "recurrent", "properties": {"d": {"temporal": "deadline"}}}}}
    (err,) = sv.validate_schema(bad, META, "b")
    assert err.startswith("b.entity_types.X.properties.d:") and "'deadline'" in err


def test_document_labels_must_be_a_list_and_pattern_keys_are_checked():
    schema = {"temporal": {"document": {"valid_from": "Effective Date"},
                           "document_patterns": {"effective": [{"filename": "^(.*)$"}]}}, "entity_types": {}}
    errors = sv.validate_schema(schema, META, "b")
    assert any("temporal.document.valid_from" in e and "list of label strings" in e for e in errors)
    assert any("temporal.document_patterns.effective" in e and "unknown key" in e for e in errors)


BANKING = {"temporal": {"document": {"valid_from": ["Effective Date"], "revised": ["Last Updated"],
                                     "version": ["Version"]},
                        "defaults": {"valid_from": "ingestion_date", "time_source": "default_ingestion"}}}


def test_version_and_revised_without_an_effective_date_take_the_default(tmp_path):
    md = tmp_path / "sop.md"
    md.write_text("| Version | 1.0 |\n| Last Updated | 2026-03-04 |\n", encoding="utf-8")
    out = ing._document_valid_time(md, {}, BANKING)
    assert out["_valid_from"] and out["_valid_precision"] == "none"
    assert out["_valid_time_source"] == "default_ingestion"
    assert (out["_revised_at"], out["declared_version"]) == ("2026-03-04", "1.0")


def test_frontmatter_valid_from_keeps_the_revision_date(tmp_path):
    md = tmp_path / "sop.md"
    md.write_text("| Last Updated | 2026-03-04 |\n", encoding="utf-8")
    out = ing._document_valid_time(md, {"_valid_from": "2026-01-01"}, BANKING)
    assert (out["_valid_from"], out["_revised_at"]) == ("2026-01-01", "2026-03-04")


def test_a_revised_label_from_frontmatter():
    out = lift_document_dates("Body.\n", {"last_updated": "2026-03-04"},
                              {"valid_from": ["Effective Date"], "revised": ["Last Updated"]})
    assert out["revised"] == "2026-03-04" and "time_source" not in out


def test_retime_removes_a_stale_revised_at(tmp_path):
    import json

    from artmind.retime import retime_folder

    md = tmp_path / "sop.md"
    md.write_text("| Effective Date | 2026-01-01 |\n", encoding="utf-8")
    folder = tmp_path / "kg" / "b" / "sop"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({
        "id": "d1", "name": "sop.md", "path": str(md), "_valid_from": "2026-01-01",
        "_valid_precision": "day", "_valid_time_source": "header", "_revised_at": "2025-12-01"}))
    (folder / "observations.json").write_text("[]")
    assert retime_folder(folder, BANKING)["changed"] is True
    document = json.loads((folder / "document.json").read_text())
    assert "_revised_at" not in document and document["_valid_from"] == "2026-01-01"
