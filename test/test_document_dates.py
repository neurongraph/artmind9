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
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")  # the backfill's answer cache and stash
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "journal_schema.yaml").write_text(JOURNAL_SCHEMA, encoding="utf-8")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    return doc


def _folder(stem):
    """The staging folder of `journal/<stem>.md`: named by its document key."""
    from artmind.document_identity import document_key

    return ing._staging_dir("journal", document_key(f"journal/{stem}.md"))


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
    folder = _folder("musings")

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


def test_a_month_header_gives_the_document_and_its_undated_facts_month_precision(tmp_path, monkeypatch):
    """Spec 2026-11-15 §2: a partial document date is stored as its first day with its precision."""
    doc = _vault(tmp_path, monkeypatch, "plans.md", "| Date | 2026-03 |\n\nPlans for the spring.\n")
    _stub_llm(monkeypatch)

    document, (obs,) = _extract(doc)

    assert (document["_valid_from"], document["_valid_precision"], document["_valid_time_source"]) == (
        "2026-03-01", "month", "header")
    assert (obs["_valid_from"], obs["_valid_precision"]) == ("2026-03-01", "month")


def test_a_default_dated_document_has_precision_none(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "musings.md", "No date anywhere.\n")
    _stub_llm(monkeypatch)

    document, (obs,) = _extract(doc)

    assert (document["_valid_time_source"], document["_valid_precision"]) == ("default_ingestion", "none")
    assert obs["_valid_precision"] == "none"


def _context_prompts(monkeypatch):
    """Entities prompts as the model sees them (the passage carries the context block)."""
    prompts: list = []
    inner = ing._llm_extract

    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            prompts.append(prompt)
        return inner(step_name, model, prompt, debug_dir)

    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(ing, "_llm_extract", llm)
    return prompts


def test_the_context_shows_a_date_line_only_for_a_date_the_note_carries(tmp_path, monkeypatch):
    dated = _vault(tmp_path, monkeypatch, "2026-01-07.md", "2026-01-07\n\nGovernance call today.\n")
    _stub_llm(monkeypatch)
    prompts = _context_prompts(monkeypatch)
    _extract(dated)
    assert prompts and "Date: 2026-01-07" in prompts[0]

    undated = dated.parent / "musings.md"
    undated.write_text("No date anywhere.\n", encoding="utf-8")
    prompts.clear()
    _extract(undated)
    assert prompts and all("Title:" in p for p in prompts), "the context block is there, just undated"
    assert not any(line.startswith("Date:") for p in prompts for line in p.splitlines())


def test_a_failed_summary_call_is_logged_as_a_warning_and_does_not_raise(tmp_path, monkeypatch):
    from loguru import logger

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ing, "_llm_extract", boom)
    seen = []
    handle = logger.add(lambda m: seen.append((m.record["level"].name, m.record["message"])), level="DEBUG")
    try:
        result = ing._summarise_document(
            doc_kg_dir=tmp_path, key="k1", schema={"subject": "a journal", "entity_types": {}},
            sample=lambda: "SAMPLE", focus="", model="m", debug_dir=tmp_path,
        )
    finally:
        logger.remove(handle)

    assert result is None
    assert [level for level, message in seen if "Document summary unavailable" in message] == ["WARNING"]
    assert not (tmp_path / "document_context.json").exists()


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
    return _folder("2026-01-07"), anchors


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
    # Chunks move only their date fields (spec 2026-11-15 §5.1): the document's window, and
    # the chunk's own day from its first line.
    date_keys = set(ing.CHUNK_DATE_KEYS)
    before, after = json.loads(chunks_before), json.loads((folder / "chunks.json").read_text())
    assert [{k: v for k, v in c.items() if k not in date_keys} for c in after] == [
        {k: v for k, v in c.items() if k not in date_keys} for c in before]
    assert {(c["_valid_from"], c["_valid_precision"], c["_doc_valid_from"]) for c in after} == {
        ("2026-01-07", "day", "2026-01-07")}
    # Idempotent.
    assert retime_folder(folder, _schema())["changed"] is False


def test_retime_folder_rederives_the_documents_precision(tmp_path, monkeypatch):
    from artmind.retime import retime_folder

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    assert json.loads((folder / "document.json").read_text())["_valid_precision"] == "none"

    retime_folder(folder, _schema())

    document = json.loads((folder / "document.json").read_text())
    (obs,) = json.loads((folder / "observations.json").read_text())
    assert (document["_valid_precision"], obs["_valid_precision"]) == ("day", "day")


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


def _redo_answers(monkeypatch, answer):
    """`backfill._ask` stubbed: records every prompt, answers `answer`."""
    from artmind import backfill

    prompts: list = []
    monkeypatch.setattr(backfill, "_ask", lambda step, model, prompt, debug_dir=None: prompts.append(prompt) or (answer, True))
    return prompts


def _asked_classes(prompt):
    import re

    return sorted(re.findall(r"^For (\w+), extract:", prompt, re.M))


def test_redo_properties_reextracts_only_properties_with_the_anchor(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    folder, anchors = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    assert anchors == [None]
    monkeypatch.setattr(ing, "_llm_extract", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no extract_kg step")))
    commits = []
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: commits.append(d) or True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})

    # The first extraction canonicalized "Governance Call" to "Governance Sync".
    obs_file = folder / "observations.json"
    staged = json.loads(obs_file.read_text())
    obs_file.write_text(json.dumps([{**o, "canonical_name": "Governance Sync"} for o in staged]))
    monkeypatch.setattr(ing, "canonicalize_document",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("identity must not be re-decided")))
    prompts = _redo_answers(monkeypatch, [{"id": "o1", "properties": {"date_or_time": "2026-01-07 (afternoon)"}}])

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert report["committed"] == 1 and commits == [folder], report
    assert len(prompts) == 1 and _asked_classes(prompts[0]) == ["EVENT"]
    assert "document dated 2026-01-07" in prompts[0], "the anchor is the re-dated document's date"
    assert "o1 (EVENT): Governance Sync" in prompts[0], "asked under its canonical name"
    (obs,) = json.loads(obs_file.read_text())
    assert obs["canonical_name"] == "Governance Sync", "a properties redo keeps every entity's identity"
    assert (obs["date_or_time"], obs["_valid_from"], obs["_valid_time_source"]) == (
        "2026-01-07 (afternoon)", "2026-01-07", "property")
    document = json.loads((folder / "document.json").read_text())
    assert document["_valid_from"] == "2026-01-07"


def test_redo_properties_needs_no_chunk_cache(tmp_path, monkeypatch):
    import shutil

    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    shutil.rmtree(folder / "chunks")
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    prompts = _redo_answers(monkeypatch, [{"id": "o1", "properties": {"date_or_time": "2026-01-06"}}])

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert report["committed"] == 1 and report["skipped"] == [] and len(prompts) == 1
    (obs,) = json.loads((folder / "observations.json").read_text())
    assert obs["date_or_time"] == "2026-01-06"


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
    prompts = _redo_answers(monkeypatch, [{"id": "o1", "properties": {"date_or_time": "2026-01-21"}},
                                          {"id": "o2", "properties": {"date_or_time": "2026-01-21"}}])
    retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    redone = json.loads((_folder("2026-01-21") / "observations.json").read_text())
    assert [o["id"] for o in redone] == [o["id"] for o in first]
    assert prompts and "Ami)" not in prompts[0] and ": Ami\n" not in prompts[0], "the folded mention is not asked"


# ── `exclusive: true`: a property only its declaring classes may carry ─────

EXCLUSIVE_SCHEMA = JOURNAL_SCHEMA.replace(
    "      date_or_time:\n        temporal: valid_from\n",
    "      date_or_time:\n        temporal: valid_from\n        exclusive: true\n",
) + """  PERSON:
    kind: recurrent
    description: A person.
    properties:
      role: {}
"""


def _two_class_llm(monkeypatch):
    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            return [{"id": "e1", "entity_class": "EVENT", "name": "Call"},
                    {"id": "e2", "entity_class": "PERSON", "name": "Srinath"}], True
        if step_name.endswith("_properties"):
            return [{"id": "e1", "properties": {"date_or_time": "2026-01-07 (evening)"}},
                    {"id": "e2", "properties": {"date_or_time": "2026-01-07", "role": "chair"}}], True
        return [], True

    _stub_llm(monkeypatch)
    monkeypatch.setattr(ing, "_llm_extract", llm)


def test_an_exclusive_property_is_stripped_from_classes_that_do_not_declare_it(tmp_path, monkeypatch):
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(EXCLUSIVE_SCHEMA, encoding="utf-8")
    _two_class_llm(monkeypatch)

    _, observations = _extract(doc)

    by_class = {o["entity_class"]: o for o in observations}
    assert by_class["EVENT"]["date_or_time"] == "2026-01-07 (evening)"
    assert "date_or_time" not in by_class["PERSON"]
    assert by_class["PERSON"]["role"] == "chair", "only the exclusive property goes"


def test_a_key_another_class_declares_is_dropped_from_one_that_does_not(tmp_path, monkeypatch):
    """Spec 2026-11-15 §2.2: an observation keeps only its own class's declared keys, so
    another class's date property goes whatever `exclusive` says."""
    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(
        EXCLUSIVE_SCHEMA.replace("        exclusive: true\n", ""), encoding="utf-8")
    _two_class_llm(monkeypatch)

    _, observations = _extract(doc)

    assert "date_or_time" not in {o["entity_class"]: o for o in observations}["PERSON"]


def test_retime_strips_an_exclusive_property_from_staged_observations(tmp_path, monkeypatch):
    import yaml

    from artmind.retime import retime_folder

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    schema_file.write_text(EXCLUSIVE_SCHEMA.replace("        exclusive: true\n", ""), encoding="utf-8")
    _two_class_llm(monkeypatch)
    _extract(doc)
    # Staging from before the schema-only filter still carries the PERSON date.
    staged = _folder("2026-01-07") / "observations.json"
    staged.write_text(json.dumps([{**o, "date_or_time": "2026-01-07"} if o["entity_class"] == "PERSON" else o
                                  for o in json.loads(staged.read_text())]))
    schema_file.write_text(EXCLUSIVE_SCHEMA, encoding="utf-8")  # the flag lands later

    assert retime_folder(_folder("2026-01-07"), yaml.safe_load(EXCLUSIVE_SCHEMA))["changed"]

    observations = json.loads((_folder("2026-01-07") / "observations.json").read_text())
    by_class = {o["entity_class"]: o for o in observations}
    assert "date_or_time" not in by_class["PERSON"]
    assert by_class["EVENT"]["date_or_time"] == "2026-01-07 (evening)"


def test_redo_properties_reextracts_only_date_tagged_classes(tmp_path, monkeypatch):
    """Found live on the full personal_journal redo: re-extracting EVERY
    class re-rolled 10,217 undated observations and, told not to date them,
    the model moved ~830 dates into invented keys (date_or_time_qualifier,
    time_of_visit). Only classes with a `temporal:` property need a redo; the
    rest must not even be shown to the model."""
    from artmind.retime import retime_domain

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath today.\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(EXCLUSIVE_SCHEMA, encoding="utf-8")
    _two_class_llm(monkeypatch)
    _, first = _extract(doc)
    person_before = {k: v for k, v in next(o for o in first if o["entity_class"] == "PERSON").items()}

    # Even if the model answers for an entity it wasn't asked about, that answer is ignored.
    prompts = _redo_answers(monkeypatch, [
        {"id": "o1", "properties": {"date_or_time": "2026-01-07 (afternoon)"}},
        {"id": "o2", "properties": {"date_or_time_qualifier": "2026-01-07", "role": "guest"}}])
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})

    retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert [_asked_classes(p) for p in prompts] == [["EVENT"]], "PERSON is never sent back to the model"
    assert "Srinath" not in prompts[0].split("__list_of_entities__")[-1].split("__text__")[0]
    redone = json.loads((_folder("2026-01-07") / "observations.json").read_text())
    by_class = {o["entity_class"]: o for o in redone}
    assert by_class["EVENT"]["date_or_time"] == "2026-01-07 (afternoon)"
    assert by_class["PERSON"] == person_before, "an undated class's observation is untouched"


def test_redo_properties_asks_one_call_per_chunk_for_two_dated_classes(tmp_path, monkeypatch):
    from artmind.retime import retime_domain

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath today.\n")
    two_dated = EXCLUSIVE_SCHEMA.replace("        exclusive: true\n", "").replace(
        "      role: {}\n", "      role: {}\n      met_on:\n        temporal: valid_from\n")
    (ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml").write_text(two_dated, encoding="utf-8")
    _two_class_llm(monkeypatch)
    _extract(doc)
    prompts = _redo_answers(monkeypatch, [{"id": "o2", "properties": {"met_on": "2026-01-06", "role": "guest"}}])
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})

    retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert [_asked_classes(p) for p in prompts] == [["EVENT", "PERSON"]]
    person = next(o for o in json.loads((_folder("2026-01-07") / "observations.json").read_text())
                  if o["entity_class"] == "PERSON")
    assert person["met_on"] == "2026-01-06" and person["_valid_from"] == "2026-01-06"
    assert person["role"] == "chair", "only the date keys are asked for; role keeps its value"


def test_extraction_and_retime_normalize_property_keys(tmp_path, monkeypatch):
    from artmind.retime import retime_folder
    import yaml

    doc = _vault(tmp_path, monkeypatch, "2026-01-07.md", "Call with Srinath.\n")
    schema_file = ing.DOMAIN_SCHEMAS_DIR / "journal_schema.yaml"
    # PERSON declares `role` and, for now, `narrative_function`.
    declared_now = EXCLUSIVE_SCHEMA.replace("      role: {}\n", "      role: {}\n      narrative_function: {}\n")
    schema_file.write_text(declared_now, encoding="utf-8")
    _two_class_llm(monkeypatch)

    def llm(step_name, model, prompt, debug_dir):
        if step_name.endswith("_entities"):
            return [{"id": "e2", "entity_class": "PERSON", "name": "Srinath"}], True
        if step_name.endswith("_properties"):
            return [{"id": "e2", "properties": {"roles": "chair", "narrative_function": "host"}}], True
        return [], True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    _, (obs,) = _extract(doc)
    assert obs["role"] == "chair" and "roles" not in obs, "number variant folded at extraction"
    assert obs["narrative_function"] == "host", "declared, no alias yet"

    # The key is renamed in the schema: `narrative_role` declared, the old name an alias.
    with_alias = (EXCLUSIVE_SCHEMA.replace("      role: {}\n", "      role: {}\n      narrative_role: {}\n")
                  + "property_aliases:\n  narrative_function: narrative_role\n")
    schema_file.write_text(with_alias, encoding="utf-8")
    assert retime_folder(_folder("2026-01-07"), yaml.safe_load(with_alias))["changed"]
    (obs,) = json.loads((_folder("2026-01-07") / "observations.json").read_text())
    assert obs["narrative_role"] == "host" and "narrative_function" not in obs, "retime applies a new alias"


def test_a_failing_redo_still_commits_the_redated_documents(tmp_path, monkeypatch):
    from artmind import backfill
    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    commits, rebuilds = [], []
    monkeypatch.setattr(ing, "commit_to_graph", lambda d, dom, defer_rebuild=False: commits.append(d) or True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: rebuilds.append(domain) or {})
    monkeypatch.setattr(backfill, "backfill_domain", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic")))

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert commits == [folder] and rebuilds == ["journal"] and report["committed"] == 1
    assert any("properties redo failed" in w for w in report["warnings"])


def test_redo_preconditions_refuse_before_retime_writes(tmp_path, monkeypatch):
    import pytest

    from artmind import backfill
    from artmind.retime import retime_domain

    folder, _ = _ingested_under_the_old_schema(tmp_path, monkeypatch)
    before = (folder / "document.json").read_bytes()
    with pytest.raises(ValueError, match="needs a model"):
        retime_domain("journal", redo_properties=True, text_model=None)
    monkeypatch.setattr(backfill, "worker_is_running", lambda: True)
    with pytest.raises(ValueError, match="worker is running"):
        retime_domain("journal", redo_properties=True, text_model="m")
    assert (folder / "document.json").read_bytes() == before, "nothing re-dated before the refusal"


def test_redo_report_names_failed_chunks(tmp_path, monkeypatch):
    from artmind import backfill
    from artmind.retime import retime_domain

    _ingested_under_the_old_schema(tmp_path, monkeypatch)
    monkeypatch.setattr(ing, "commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr(ing, "rebuild_projection", lambda domain: {})
    monkeypatch.setattr(backfill, "_ask", lambda *a, **k: ([], False))

    report = retime_domain("journal", redo_properties=True, text_model="m", embed_model="e")

    assert len(report["failed_chunks"]) == 1 and any("re-run to retry" in w for w in report["warnings"])
    assert report["committed"] == 1, "the re-dating still commits"


def test_unreadable_frontmatter_valid_from_falls_through(tmp_path):
    md = tmp_path / "n.md"
    md.write_text("---\n_valid_from: sometime soon\n---\nBody\n", encoding="utf-8")
    out = ing._document_valid_time(md, {"_valid_from": "sometime soon"}, {"temporal": {}}, filename="n.md")
    assert "_valid_from" not in out
    assert "_valid_time_source" not in out
