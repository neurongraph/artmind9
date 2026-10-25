"""The document's own entity, folded in code (`artmind.document_entity`).

Synthetic names only: shaped like what extraction gave one amendment across
its chunks (title-derived vs text-derived names, differing dates appended).
"""
import pytest

from artmind import document_entity as de

TITLE = "CN12 - Agreement - Harbour Data Platform Programme - 01.03.2024 31.12.2027"
SCHEMA = {
    "document_entity": {"classes": ["AMENDMENT"]},
    "entity_types": {"AMENDMENT": {"kind": "occurrent"}, "CONTRACT": {"kind": "recurrent"}},
}


def _entity(name, chunk, cls="AMENDMENT"):
    return {"name": name, "entity_class": cls, "chunk_id": f"doc_{chunk:03d}"}


@pytest.mark.parametrize("text, refs", [
    (TITLE, {"CN12"}),
    ("CN5 - Harbour Programme SoW 01.03.2024 - 30.06.2026", {"CN5"}),
    ("CN 48 Order - Vendor - Renewal - 01.02.2019 - 28.02.2031", {"CN48"}),
    ("Buyer_SR4410_Advisory_Services_(6m)_v2.0", {"SR4410"}),
    ("Harbour_CN004_Signed_140324_(003).docx", {"CN4"}),
    ("SoW - Supplier - Data Training - 131025 - 310126", set()),
    ("Contract Change Note 2026-01-01", set()),  # a word and a date, not a reference
    ("Version v1 of P01234", {"P1234"}),  # one letter needs two digits
])
def test_references_come_from_letters_then_digits_normalised(text, refs):
    assert de.references(text) == refs


def test_cn12_cn012_and_cn_12_are_one_reference():
    assert de.references("CN12") == de.references("CN012") == de.references("CN 12") == {"CN12"}


@pytest.mark.parametrize("name, names_it", [
    ("CN012 Harbour Data Platform Programme Sixth Amendment", True),
    ("CN012 Harbour Data Platform Programme Amendment 2026-01-01", True),
    (TITLE, True),  # the bare filename
    ("CN12 Harbour Data Platform Programme Contract Change Note 2026-01-01", True),
    ("CN012 Appendix 2 Deliverables Change 2026-01-01", False),  # a part of the document
    ("CN005 Harbour Amendment", False),  # another document, referenced by this one
    ("Transaction Contract", False),  # no reference at all
])
def test_a_name_names_the_document_only_with_its_reference_and_its_own_words(name, names_it):
    own = de._own_words(TITLE, TITLE + ".md")
    assert de.names_document(name, de.references(TITLE), own) is names_it


def test_the_document_names_are_folded_into_the_variant_most_chunks_used():
    entities = [
        _entity("CN012 Harbour Programme Amendment 2026-01-01", 1),
        _entity("CN012 Harbour Programme Amendment 2026-01-01", 4),
        _entity("CN012 Harbour Programme Amendment 2026-01-01", 5),
        _entity("CN012 Harbour Data Platform Programme Sixth Amendment", 2),
        _entity("CN12 Harbour Data Platform Programme", 7),
        _entity(TITLE, 9),
        _entity("CN012 Appendix 2 Deliverables Change 2026-01-01", 3),
        _entity("CN005 Harbour Amendment", 3),
        _entity("CN012 Harbour Programme Amendment 2026-01-01", 6, cls="CONTRACT"),  # not opted in
    ]
    identity = {e["name"]: e["name"] for e in entities}

    mapping, folds = de.fold(entities, identity, SCHEMA, title=TITLE, filename=TITLE + ".md")

    chosen = "CN012 Harbour Programme Amendment 2026-01-01"
    for raw in ("CN012 Harbour Data Platform Programme Sixth Amendment", "CN12 Harbour Data Platform Programme", TITLE):
        assert mapping[raw] == chosen
    assert mapping["CN012 Appendix 2 Deliverables Change 2026-01-01"] == "CN012 Appendix 2 Deliverables Change 2026-01-01"
    assert mapping["CN005 Harbour Amendment"] == "CN005 Harbour Amendment"
    assert [(f.entity_class, f.name, len(f.folded)) for f in folds] == [("AMENDMENT", chosen, 3)]
    assert identity[TITLE] == TITLE, "the input mapping is not changed"


def test_the_llm_mapping_is_respected_and_then_folded():
    """A name the LLM pass already mapped counts under its canonical name."""
    entities = [
        _entity("CN12 Harbour Programme", 1),
        _entity("CN012 Harbour Programme Amendment", 2),
        _entity("CN012 Harbour Programme Amendment", 3),
    ]
    llm = {"CN12 Harbour Programme": "CN012 Harbour Programme Amendment",
           "CN012 Harbour Programme Amendment": "CN012 Harbour Programme Amendment"}

    mapping, folds = de.fold(entities, llm, SCHEMA, title=TITLE, filename=TITLE + ".md")

    assert mapping == llm and folds == [], "one name already: nothing to fold"


def test_ties_go_to_the_earliest_chunk_then_the_shorter_name():
    entities = [_entity("CN012 Harbour Amendment", 2), _entity("CN012 Harbour Programme Amendment", 1)]
    mapping, _ = de.fold(entities, {}, SCHEMA, title=TITLE, filename=TITLE + ".md")
    assert set(mapping.values()) == {"CN012 Harbour Programme Amendment"}


@pytest.mark.parametrize("schema, title", [
    ({"entity_types": SCHEMA["entity_types"]}, TITLE),  # no opt-in
    ({"document_entity": {"classes": ["NOT_A_CLASS"]}, "entity_types": SCHEMA["entity_types"]}, TITLE),
    (SCHEMA, "Notes from the harbour programme"),  # no reference in the title
])
def test_reference_bearing_names_fold_only_with_an_opt_in_class_and_a_reference(schema, title):
    entities = [_entity("CN012 Harbour Amendment", 1), _entity("CN12 Harbour Programme Amendment", 2)]
    mapping, folds = de.fold(entities, {}, schema, title=title, filename=title + ".md")
    assert mapping == {} and folds == []


def test_domains_validate_reports_an_undeclared_document_entity_class():
    from artmind.schema_validate import validate_schema

    meta = {"kinds": {"occurrent": {}, "recurrent": {}}, "reserved_prefix": "_"}
    bad = {**SCHEMA, "document_entity": {"classes": ["AMENDMENT", "AGREMENT"]}}
    assert any("'AGREMENT' is not a class" in e for e in validate_schema(bad, meta, "c"))
    assert any("non-empty 'classes'" in e for e in validate_schema({**SCHEMA, "document_entity": True}, meta, "c"))
    assert not [e for e in validate_schema(SCHEMA, meta, "c") if "document_entity" in e]


def test_the_shipped_contracts_schema_opts_in_a_declared_class():
    from pathlib import Path

    import yaml

    schema = yaml.safe_load((Path(de.__file__).parent / "domains" / "schemas" / "contracts_schema.yaml").read_text())
    assert de.document_classes(schema) == ["AMENDMENT", "CONTRACT"]


# ── wired into extract_kg ────────────────────────────────────────────────────


def test_extract_kg_stages_one_name_for_the_documents_own_entity(ingest_env, monkeypatch):
    import json

    import artmind.ingest as ing

    vault, _ = ingest_env
    schemas = vault / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "contracts_schema.yaml").write_text(
        "name: contracts\nsubject: contracts\ndocument_entity:\n  classes: [AMENDMENT]\n"
        "entity_types:\n  AMENDMENT:\n    kind: occurrent\n    description: A change to a contract.\n"
        "  PARTY:\n    kind: recurrent\n    description: A party.\n"
    )
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    note = vault / "contracts" / "CN12 Harbour Programme Amendment.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# One\n\n" + "The parties agree. " * 30 + "\n\n# Two\n\n" + "The term ends. " * 30 + "\n")
    names = {1: "CN012 Harbour Programme Amendment 2026-01-01", 2: "CN12 Harbour Programme Amendment"}

    def llm(step_name, model, prompt, debug_dir):
        if step_name == "document_summary":
            return {"summary": "A change.", "identifiers": []}, True
        seq = int(step_name.split("_")[1])
        if step_name.endswith("_entities"):
            return [{"id": "a1", "entity_class": "AMENDMENT", "name": names[seq]},
                    {"id": "p1", "entity_class": "PARTY", "name": "Alpha Ltd"}], True
        return [], True

    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "canonicalize_document", lambda entities, **k: {e["name"]: e["name"] for e in entities})

    result = ing.ingest_file(note, "m", "contracts", chunk_size=700)
    assert result["chunk_count"] == 2
    folder = ing.extract_kg(result, "contracts", max_workers=1)

    observations = json.loads((folder / "observations.json").read_text())
    amendment = {o["canonical_name"] for o in observations if o["entity_class"] == "AMENDMENT"}
    assert len(amendment) == 1, amendment
    assert {o["name"] for o in observations if o["entity_class"] == "AMENDMENT"} == set(names.values()), (
        "each observation keeps what its chunk said"
    )


# ── deictic names ("This Amendment") ─────────────────────────────────────────


@pytest.mark.parametrize("name, deictic", [
    ("This Amendment", True), ("this transaction contract", True), ("This Contract Change Note", True),
    ("The Amendment", False), ("This Harbour Amendment", False), ("Amendment", False), ("This", False),
])
def test_a_deictic_name_is_this_plus_document_type_words(name, deictic):
    assert de.is_deictic(name) is deictic


def test_a_deictic_name_folds_into_the_reference_bearing_name():
    """Left alone, every document's "This Amendment" would be one entity."""
    entities = [
        _entity("CN012 Harbour Programme Amendment", 1),
        _entity("CN012 Harbour Programme Amendment", 2),
        _entity("This Amendment", 3),
    ]
    mapping, folds = de.fold(entities, {}, SCHEMA, title=TITLE, filename=TITLE + ".md")
    assert mapping["This Amendment"] == "CN012 Harbour Programme Amendment"
    assert folds[0].folded == ("This Amendment",)


def test_a_deictic_name_with_nothing_to_fold_into_is_scoped_to_its_document():
    """No reference-bearing name in the class (or none in the filename at all, as
    for a framework agreement): the name is made the document's own, so two
    documents' "This Amendment" never merge into one entity."""
    entities = [_entity("This Amendment", 1), _entity("this  amendment", 2), _entity("Clause 5", 3, cls="CONTRACT")]
    for title in (TITLE, "Outsourcing Framework Agreement final signed"):
        mapping, folds = de.fold(entities, {}, SCHEMA, title=title, filename=title + ".md")
        assert mapping["This Amendment"] == mapping["this  amendment"] == f"This Amendment ({title})"
        assert "Clause 5" not in mapping
        assert [(f.entity_class, f.scoped) for f in folds] == [("AMENDMENT", True)]


# ── the document's own entity's date ─────────────────────────────────────────

DATED_SCHEMA = {
    "document_entity": {"classes": ["AMENDMENT"], "date_labels": ["Amendment Date", "Effective Date"]},
    "entity_types": {
        "AMENDMENT": {"kind": "occurrent", "properties": {"effective_date": {"temporal": "valid_from"}}},
        "CONTRACT": {"kind": "recurrent", "properties": {"contract_date": {"temporal": "valid_from"}}},
    },
}


def test_the_date_property_and_labels_come_from_the_schema():
    assert de.date_property(DATED_SCHEMA, "AMENDMENT") == "effective_date"
    assert de.date_property(DATED_SCHEMA, "PARTY") is None
    assert de.date_labels(DATED_SCHEMA) == ["Amendment Date", "Effective Date"]
    assert de.date_labels(SCHEMA) == []


def test_own_entities_is_the_reference_bearing_name_most_chunks_used():
    entities = [
        _entity("CN012 Harbour Amendment", 1), _entity("CN012 Harbour Amendment", 2),
        _entity("CN005 Harbour Amendment", 3), _entity("Alpha Ltd", 1, cls="PARTY"),
    ]
    assert de.own_entities(entities, {}, DATED_SCHEMA, title=TITLE, filename=TITLE + ".md") == {
        "AMENDMENT": "CN012 Harbour Amendment",
    }
    assert de.own_entities(entities, {}, DATED_SCHEMA, title="Framework agreement", filename="fa.md") == {}


def test_every_observation_of_the_document_entity_gets_the_document_date_and_nothing_else_does():
    entities = [
        {**_entity("CN012 Harbour Amendment", 1), "id": "a1"},
        {**_entity("CN012 Harbour Amendment", 4), "id": "a4"},
        {**_entity("CN005 Harbour Amendment", 4), "id": "b4"},
    ]
    properties = [
        {"id": "a4", "properties": {"effective_date": "2024-06-17", "reason": "kept"}},
        {"id": "b4", "properties": {"effective_date": "2025-08-01"}},
    ]
    owners = {"AMENDMENT": "CN012 Harbour Amendment"}

    out, stamped = de.stamp_own_dates(entities, properties, {}, owners, DATED_SCHEMA, "2025-12-12")

    by_id = {p["id"]: p["properties"] for p in out}
    assert by_id["a1"] == {"effective_date": "2025-12-12"}, "an entity no chunk dated gets the date too"
    assert by_id["a4"] == {"effective_date": "2025-12-12", "reason": "kept"}
    assert by_id["b4"] == {"effective_date": "2025-08-01"}, "another document's entity keeps its own"
    assert stamped == [("AMENDMENT", "CN012 Harbour Amendment", "effective_date")]
    assert properties[0]["properties"]["effective_date"] == "2024-06-17", "the input is not changed"


@pytest.mark.parametrize("definition, expected", [
    ("'Amendment Date' means the date of this Amendment.", "2025-12-12"),  # no date: the document's own
    ("'Amendment Date' means 1 August 2025.", "2025-08-01"),  # the defined term wins
])
def test_extract_kg_dates_the_documents_own_entity_from_the_document(ingest_env, monkeypatch, definition, expected):
    import json

    import artmind.ingest as ing

    vault, _ = ingest_env
    schemas = vault / "schemas"
    schemas.mkdir(exist_ok=True)
    (schemas / "contracts_schema.yaml").write_text(
        "name: contracts\nsubject: contracts\n"
        "temporal:\n  document:\n    valid_from: [Dated]\n"
        "document_entity:\n  classes: [AMENDMENT]\n  date_labels: [Amendment Date]\n"
        "entity_types:\n  AMENDMENT:\n    kind: occurrent\n    description: A change to a contract.\n"
        "    properties:\n      effective_date:\n        temporal: valid_from\n"
    )
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    note = vault / "contracts" / "CN12 Harbour Programme Amendment.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "Dated 12/12/2025\n\n# Definitions\n\n" + definition + " " + "The parties agree. " * 30
        + "\n\n# Term\n\n" + "The contract term commences on 17/06/2024. " * 14 + "\n"
    )

    def llm(step_name, model, prompt, debug_dir):
        if step_name == "document_summary":
            return {"summary": "A change.", "identifiers": []}, True
        seq = int(step_name.split("_")[1])
        if step_name.endswith("_entities"):
            return [{"id": "a1", "entity_class": "AMENDMENT", "name": "CN012 Harbour Programme Amendment"}], True
        if step_name.endswith("_properties"):
            return ([{"id": "a1", "properties": {"effective_date": "2024-06-17"}}] if seq == 2 else []), True
        return [], True

    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "_llm_extract", llm)
    monkeypatch.setattr(ing, "canonicalize_document", lambda entities, **k: {e["name"]: e["name"] for e in entities})

    result = ing.ingest_file(note, "m", "contracts", chunk_size=700)
    assert result["chunk_count"] == 2
    folder = ing.extract_kg(result, "contracts", max_workers=1)

    amendments = [o for o in json.loads((folder / "observations.json").read_text()) if o["entity_class"] == "AMENDMENT"]
    assert amendments and {(o.get("effective_date"), o["_valid_from"]) for o in amendments} == {(expected, expected)}


def test_domains_validate_reports_malformed_date_labels():
    from artmind.schema_validate import validate_schema

    meta = {"kinds": {"occurrent": {}, "recurrent": {}}, "reserved_prefix": "_"}
    bad = {**DATED_SCHEMA, "document_entity": {"classes": ["AMENDMENT"], "date_labels": "Amendment Date"}}
    assert any("date_labels" in e for e in validate_schema(bad, meta, "c"))
    assert not [e for e in validate_schema(DATED_SCHEMA, meta, "c") if "date_labels" in e]
