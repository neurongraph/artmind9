"""A document's date, end to end through `extract_kg` (no Neo4j, no LLM).

The `personal_journal` defect: a daily note (`2026-01-07.md`) carries its
date only in its filename and as a bare first line, so every document fell
through to the ingestion-date default -- `datetime.now()` at each extraction,
which gave one batch four different "ingestion dates". And the extractor
never saw any date at all, so `date_or_time` came back as "today".
"""
import json
import subprocess

import artmind.db as db
import artmind.ingest as ing

JOURNAL_SCHEMA = """\
name: journal
subject: a personal journal
temporal:
  document:
    valid_from: [Date, date]
  document_patterns:
    valid_from:
    - filename: '^(\\d{4}-\\d{2}-\\d{2})$'
  relative_anchor: document.valid_from
  defaults:
    valid_from: ingestion_date
    time_source: default_ingestion
entity_types:
  EVENT:
    kind: occurrent
    description: Something that happened.
    properties:
      date_or_time:
        temporal: valid_from
"""


def _vault(tmp_path, monkeypatch, filename, body):
    v = tmp_path / "vault"
    (v / "journal").mkdir(parents=True)
    doc = v / "journal" / filename
    doc.write_text(body, encoding="utf-8")
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "T"],
                ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        subprocess.run(["git", *cmd], cwd=v, check=True)
    import artmind.document_identity as di

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", v)
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", v)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "journal_schema.yaml").write_text(JOURNAL_SCHEMA, encoding="utf-8")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    return doc


def _stub_llm(monkeypatch, date_or_time="today"):
    """One EVENT per chunk; records every properties prompt's anchor."""
    anchors: list = []

    def properties_prompt(text, ents, schema, vocabulary=None, anchor_date=None):
        anchors.append(anchor_date)
        return "properties"

    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EVENT", "name": "Governance Call"}], True
        if step_name.endswith("_properties"):
            return [{"id": "e1", "properties": {"date_or_time": date_or_time}}], True
        return [], True

    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda *a, **k: "entities")
    monkeypatch.setattr(ing, "build_properties_prompt", properties_prompt)
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda *a, **k: "relationships")
    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "canonicalize_document", lambda entities, **k: {})
    return anchors


def _extract(doc):
    result = ing.ingest_file(doc, "gemma4:e4b", "journal", chunk_size=6000)
    assert result["status"] == "ok"
    folder = ing.extract_kg(result, "journal", max_workers=1)
    return (
        json.loads((folder / "document.json").read_text()),
        json.loads((folder / "observations.json").read_text()),
    )


def test_a_daily_note_is_dated_by_its_filename(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "2026-01-07\n\nGovernance call today.\n")
    anchors = _stub_llm(monkeypatch)

    document, observations = _extract(doc)

    assert (document["_valid_from"], document["_valid_time_source"]) == ("2026-01-07", "filename")
    # The properties prompt was told the entry's date.
    assert anchors == ["2026-01-07"]
    # "today" doesn't parse, so the fact inherits the entry's date -- not the
    # day it was ingested.
    (obs,) = observations
    assert (obs["_valid_from"], obs["_doc_valid_from"]) == ("2026-01-07", "2026-01-07")


def test_a_resolved_date_property_dates_the_fact(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Yesterday's call ran long.\n")
    _stub_llm(monkeypatch, date_or_time="2026-01-06 (yesterday)")

    _document, (obs,) = _extract(doc)

    assert (obs["_valid_from"], obs["_doc_valid_from"]) == ("2026-01-06", "2026-01-07")
    assert obs["_valid_time_source"] == "property"


def test_an_undated_note_keeps_its_first_ingestion_date_and_gets_no_anchor(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "musings.md", "No date anywhere.\n")
    anchors = _stub_llm(monkeypatch)
    folder = ing.KG_DIR / "journal" / "musings"

    document, _ = _extract(doc)
    assert document["_valid_time_source"] == "default_ingestion"
    assert anchors == [None], "an ingestion date anchors nothing"

    # A later extraction (here: a pretend earlier first-ingest date) keeps the
    # date first recorded instead of re-stamping today's.
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "_valid_from": "2026-10-02"}))
    doc.write_text("No date anywhere. Still.\n", encoding="utf-8")
    document, (obs,) = _extract(doc)
    assert document["_valid_from"] == "2026-10-02"
    assert obs["_valid_from"] == "2026-10-02"


# ── `ingest retime`: the backfill ───────────────────────────────────────────

OLD_JOURNAL_SCHEMA = JOURNAL_SCHEMA.replace(
    "  document_patterns:\n    valid_from:\n    - filename: '^(\\d{4}-\\d{2}-\\d{2})$'\n", ""
)


def _ingested_under_the_old_schema(tmp_path, monkeypatch, date_or_time="today"):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "2026-01-07\n\nGovernance call today.\n")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(OLD_JOURNAL_SCHEMA, encoding="utf-8")
    anchors = _stub_llm(monkeypatch, date_or_time)
    document, _ = _extract(doc)
    assert document["_valid_time_source"] == "default_ingestion"
    schema_file.write_text(JOURNAL_SCHEMA, encoding="utf-8")  # the fix lands
    return ing.KG_DIR / "journal" / "2026-01-07", anchors


def _schema():
    import yaml

    return yaml.safe_load(JOURNAL_SCHEMA)


def test_retime_folder_redates_the_document_and_its_observations(tmp_path, monkeypatch):
    from artmind.retime import retime_folder

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    chunks_before = (folder / "chunks.json").read_bytes()

    result = retime_folder(folder, _schema())

    assert (result["after"], result["source"], result["changed"]) == ("2026-01-07", "filename", True)
    document = json.loads((folder / "document.json").read_text())
    (obs,) = json.loads((folder / "observations.json").read_text())
    assert (document["_valid_from"], document["_valid_time_source"]) == ("2026-01-07", "filename")
    assert (obs["_valid_from"], obs["_doc_valid_from"], obs["_valid_time_source"]) == (
        "2026-01-07", "2026-01-07", "filename")
    assert (folder / "chunks.json").read_bytes() == chunks_before, "only the time fields move"
    # Idempotent.
    assert retime_folder(folder, _schema())["changed"] is False


def test_retime_folder_keeps_a_facts_own_date(tmp_path, monkeypatch):
    from artmind.retime import retime_folder

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch, date_or_time="2026-01-05")
    retime_folder(folder, _schema())
    (obs,) = json.loads((folder / "observations.json").read_text())
    assert (obs["_valid_from"], obs["_doc_valid_from"], obs["_valid_time_source"]) == (
        "2026-01-05", "2026-01-07", "property")


def test_retime_dry_run_writes_nothing(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    before = (folder / "document.json").read_bytes()
    commits = []
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: commits.append(a) or True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {"rebuilt": domain})

    report = retime_domain("journal", dry_run=True)

    assert (report["scanned"], report["changed"], report["committed"]) == (1, 1, 0)
    assert report["sample"][0]["after"] == "2026-01-07"
    assert (folder / "document.json").read_bytes() == before
    assert commits == [] and report["rebuild"] is None


def test_retime_domain_commits_changed_folders_then_rebuilds_once(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    commits, rebuilds = [], []
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: commits.append((d, dom, defer_rebuild)) or True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: rebuilds.append(domain) or {})

    report = retime_domain("journal")

    assert commits == [(folder, "journal", True)]
    assert rebuilds == ["journal"]
    assert report["by_source"] == {"filename": 1}
    # Nothing left to change: no commit, no rebuild.
    commits.clear(), rebuilds.clear()
    assert retime_domain("journal")["changed"] == 0
    assert commits == [] and rebuilds == []


def test_redo_properties_reextracts_only_properties_with_the_anchor(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    folder, anchors = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    assert anchors == [None]
    steps = []
    real_llm = ing._llm_extract
    monkeypatch.setattr(ing, "_llm_extract", lambda step, *a: steps.append(step) or real_llm(step, *a))
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})

    # The first extraction canonicalized "Governance Call" to "Governance Sync".
    obs_file = folder / "observations.json"
    staged = json.loads(obs_file.read_text())
    obs_file.write_text(json.dumps([{**o, "canonical_name": "Governance Sync"} for o in staged]))
    monkeypatch.setattr(ing, "canonicalize_document",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("identity must not be re-decided")))

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert report["committed"] == 1, report
    assert steps == ["chunk_001_properties"], "entities and relationships are not re-run"
    (obs,) = json.loads(obs_file.read_text())
    assert obs["canonical_name"] == "Governance Sync", "a properties redo keeps every entity's identity"
    assert anchors == [None, "2026-01-07"]
    document = json.loads((folder / "document.json").read_text())
    assert document["_valid_from"] == "2026-01-07"


def test_redo_properties_skips_a_document_without_its_chunk_cache(tmp_path, monkeypatch):
    import shutil

    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    shutil.rmtree(folder / "chunks")
    monkeypatch.setattr(ing, "_llm_extract", lambda *a: (_ for _ in ()).throw(AssertionError("no LLM")))
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert report["committed"] == 0
    assert "chunk extraction cache" in report["skipped"][0]["skipped"]


def test_a_child_domain_inherits_its_parents_temporal_block(tmp_path, monkeypatch):
    """`journal.work` declares no `temporal:` of its own; `journal` does. The
    schema skill promises the child inherits it at load time -- extraction
    used to read the child's raw YAML and date nothing."""

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Governance call today.\n")
    child = JOURNAL_SCHEMA.split("temporal:")[0] + "entity_types:" + JOURNAL_SCHEMA.split("entity_types:")[1]
    (ing.DOMAIN_SCHEMAS_DIR / "journal.work_schema.yaml").write_text(child, encoding="utf-8")
    anchors = _stub_llm(monkeypatch)

    result = ing.ingest_file(doc, "gemma4:e4b", "journal.work", chunk_size=6000)
    folder = ing.extract_kg(result, "journal.work", max_workers=1)
    document = json.loads((folder / "document.json").read_text())

    assert (document["_valid_from"], document["_valid_time_source"]) == ("2026-01-07", "filename")
    assert anchors == ["2026-01-07"], "the inherited relative_anchor applies too"


def test_retime_domain_document_filter(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    _ingested_under_the_old_schema(tmp_path, monkeypatch)
    assert retime_domain("journal", dry_run=True, documents=["2026-01-07.md"])["scanned"] == 1
    assert retime_domain("journal", dry_run=True, documents=["2026-01-07"])["scanned"] == 1
    assert retime_domain("journal", dry_run=True, documents=["2026-01-08.md"])["scanned"] == 0


def test_redo_properties_drops_a_mention_that_was_folded_away(tmp_path, monkeypatch):
    """Found live (2026-01-21): canonicalization folded a chunk's "Ami" into
    another person in the same chunk, so "Ami" left no observation and no
    record of its canonical name. A redo that let it fall back to its raw
    name minted a new "Ami" entity; it must produce exactly the observations
    the first extraction did."""
    from artmind.retime import retime_domain

    doc = _vault(tmp_path, monkeypatch, "2026-01-21.md", "Met Amitesh. Ami joined later.\n")
    anchors = _stub_llm(monkeypatch)

    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EVENT", "name": "Amitesh"},
                    {"id": "e2", "entity_class": "EVENT", "name": "Ami"}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "canonicalize_document",
                        lambda entities, **k: {"Amitesh": "Amitesh Mishra", "Ami": "Amitesh Mishra"})
    _, first = _extract(doc)
    assert [(o["name"], o["canonical_name"]) for o in first] == [("Amitesh", "Amitesh Mishra")]

    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    monkeypatch.setattr(ing, "canonicalize_document",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("identity must not be re-decided")))
    retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    redone = json.loads((ing.KG_DIR / "journal" / "2026-01-21" / "observations.json").read_text())
    assert [o["id"] for o in redone] == [o["id"] for o in first]
