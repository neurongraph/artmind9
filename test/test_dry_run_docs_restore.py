"""`docs restore --dryRun`: the record stays, no label swap, same effect as the real run."""
from click.testing import CliRunner  # noqa: F401

from _dry_run import assert_no_write_cypher
from test_dry_run_docs_retire import _run, world  # noqa: F401


def test_restore_real_run_has_the_same_effect_and_deletes_the_record(world):
    from artmind import curation_records, lifecycle_records

    recorder, curation, swept = world
    curation_records.write_record(lifecycle_records.NAME, {"id": lifecycle_records.record_id("doc-1"),
                                                           "doc_id": "doc-1", "domain": "demo.one", "status": "retired"})

    dry = _run("restore", "--dryRun")
    assert len(list((curation / "lifecycle").glob("*.json"))) == 1, "a dry run keeps the record"
    real = _run("restore")

    assert real["effect"] == dry["effect"]
    assert list((curation / "lifecycle").glob("*.json")) == []


def test_restore_dry_run_sends_no_write_cypher(world):
    recorder, curation, swept = world

    payload = _run("restore", "--dryRun")

    assert_no_write_cypher(recorder)
    assert swept == [] and payload["dry_run"] is True
    assert payload["effect"]["changes"][0]["kind"] == "document_restore"
