"""The two anti-drift steps: retrieved name vocabulary, and the per-document
canonicalization pass.
"""
from unittest.mock import MagicMock

import pytest

from artmind.canonicalize import (
    build_canonicalization_prompt,
    canonicalize_document,
    collect_names,
    recurrent_classes,
    retrieve_vocabulary,
)

SCHEMA = {
    "entity_types": {
        "RATE_ENTRY": {"kind": "recurrent", "description": "A published rate."},
        "PRODUCT": {"kind": "recurrent", "description": "A product."},
        "INCIDENT": {"kind": "occurrent", "description": "An incident."},
    }
}


def test_recurrent_classes_excludes_occurrent_ones():
    assert recurrent_classes(SCHEMA) == {"RATE_ENTRY", "PRODUCT"}


# ── vocabulary retrieval ────────────────────────────────────────────────────


def test_vocabulary_query_is_restricted_to_recurrent_classes():
    """Asserted on the PARAMETERS ACTUALLY SENT. A MagicMock session answers
    any Cypher truthily, so the only real check is what we handed it."""
    session = MagicMock()
    session.run.return_value.data.return_value = [
        {"name": "SmartSaver Account Tier 2 Rate", "entity_class": "RATE_ENTRY", "score": 0.9}
    ]
    monkey = {}

    import artmind.extraction as extraction
    original = extraction.embed_text
    extraction.embed_text = lambda model, text: monkey.setdefault("vec", [0.1] * 8)
    try:
        result = retrieve_vocabulary(
            session, domain="banking.reference", schema=SCHEMA,
            seed_text="a rate schedule", embed_model="nomic",
        )
    finally:
        extraction.embed_text = original

    assert result == [{"name": "SmartSaver Account Tier 2 Rate", "entity_class": "RATE_ENTRY"}]
    _cypher, kwargs = session.run.call_args[0][0], session.run.call_args[1]
    assert kwargs["classes"] == ["PRODUCT", "RATE_ENTRY"], "occurrent classes must not be offered"
    assert kwargs["domain"] == "banking.reference"
    assert kwargs["limit"] == 25
    assert "entity_embedding" in _cypher


def test_a_down_embedding_service_degrades_to_no_vocabulary_and_never_raises():
    session = MagicMock()
    import artmind.extraction as extraction
    original = extraction.embed_text

    def boom(model, text):
        raise RuntimeError("ollama is down")

    extraction.embed_text = boom
    try:
        assert retrieve_vocabulary(
            session, domain="d", schema=SCHEMA, seed_text="x", embed_model="nomic"
        ) == []
    finally:
        extraction.embed_text = original
    session.run.assert_not_called()


def test_a_schema_with_no_recurrent_classes_skips_the_query_entirely():
    session = MagicMock()
    occurrent_only = {"entity_types": {"INCIDENT": {"kind": "occurrent"}}}
    assert retrieve_vocabulary(
        session, domain="d", schema=occurrent_only, seed_text="x", embed_model="nomic"
    ) == []
    session.run.assert_not_called()


def test_vocabulary_reaches_the_entities_prompt():
    from artmind.extraction import build_entities_prompt

    # A name that appears nowhere in the meta-schema's own worked examples,
    # so its presence can only come from the vocabulary block.
    vocabulary = [{"name": "Kestrel Bond Ladder Rate", "entity_class": "RATE_ENTRY"}]
    with_vocab = build_entities_prompt("passage", SCHEMA, vocabulary=vocabulary)
    without = build_entities_prompt("passage", SCHEMA)

    assert "Kestrel Bond Ladder Rate" in with_vocab
    assert "NAMES ALREADY IN USE" in with_vocab
    assert "Kestrel Bond Ladder Rate" not in without
    assert "NAMES ALREADY IN USE" not in without
    assert "{{NAME_VOCABULARY}}" not in without, "the token must be substituted, not left in place"


def test_the_recurrent_naming_rule_reaches_the_entities_prompt():
    """It existed only as prose for schema authors before — never in the
    prompt — which is why extracted names carried rates and dates."""
    from artmind.extraction import build_entities_prompt

    prompt = build_entities_prompt("passage", SCHEMA)
    assert "NEVER embed a measurement" in prompt
    assert "completed event" in prompt


# ── the per-document canonicalization pass ──────────────────────────────────


def test_collect_names_dedupes_within_a_class():
    entities = [
        {"name": "Tier 2 Rate", "entity_class": "RATE_ENTRY"},
        {"name": "Tier 2 Rate", "entity_class": "RATE_ENTRY"},
        {"name": "SmartSaver", "entity_class": "PRODUCT"},
        {"name": "", "entity_class": "PRODUCT"},
    ]
    assert collect_names(entities) == {
        "RATE_ENTRY": ["Tier 2 Rate"],
        "PRODUCT": ["SmartSaver"],
    }


def test_the_prompt_names_each_class_kind_and_lists_existing_names():
    prompt = build_canonicalization_prompt(
        {"RATE_ENTRY": ["Tier 2 Rate — 4.60% AER"]},
        [{"name": "SmartSaver Account Tier 2 Rate", "entity_class": "RATE_ENTRY"}],
        SCHEMA,
    )
    assert "kind: recurrent" in prompt
    assert "Tier 2 Rate — 4.60% AER" in prompt
    assert "SmartSaver Account Tier 2 Rate" in prompt
    assert "{{" not in prompt, "every token must be substituted"


def test_it_is_exactly_ONE_llm_call_per_document_not_one_per_chunk(monkeypatch):
    """Trap 10, asserted by call count. Chunks extract in parallel and cannot
    see each other — a per-chunk pass would share their blind spot."""
    calls = []

    def fake(step_name, model, prompt, debug_dir=None):
        calls.append(step_name)
        return [
            {"name": "Tier 2 Rate — 4.60% AER", "canonical_name": "SmartSaver Account Tier 2 Rate"},
            {"name": "£10,001–£50,000", "canonical_name": "SmartSaver Account Tier 2 Rate"},
        ], True

    monkeypatch.setattr("artmind.extraction.extract_with_retry", fake)

    # entities from THREE different chunks
    entities = [
        {"name": "Tier 2 Rate — 4.60% AER", "entity_class": "RATE_ENTRY", "chunk_id": "d_001"},
        {"name": "£10,001–£50,000", "entity_class": "RATE_ENTRY", "chunk_id": "d_002"},
        {"name": "Tier 2 Rate — 4.60% AER", "entity_class": "RATE_ENTRY", "chunk_id": "d_003"},
    ]
    mapping = canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m")

    assert len(calls) == 1, f"expected exactly one call, got {len(calls)}"
    assert mapping["Tier 2 Rate — 4.60% AER"] == "SmartSaver Account Tier 2 Rate"
    assert mapping["£10,001–£50,000"] == "SmartSaver Account Tier 2 Rate"


def test_nine_names_for_one_thing_collapse_to_one():
    """The live-corpus shape: 9 of 11 spurious 'Tier 2 rate' entities came
    from a single document."""
    entities = [{"name": f"Tier 2 variant {i}", "entity_class": "RATE_ENTRY"} for i in range(9)]

    import artmind.extraction as extraction
    original = extraction.extract_with_retry
    extraction.extract_with_retry = lambda *a, **k: (
        [{"name": f"Tier 2 variant {i}", "canonical_name": "SmartSaver Account Tier 2 Rate"} for i in range(9)],
        True,
    )
    try:
        mapping = canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m")
    finally:
        extraction.extract_with_retry = original

    assert set(mapping.values()) == {"SmartSaver Account Tier 2 Rate"}


def test_no_entities_makes_no_llm_call_at_all(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "artmind.extraction.extract_with_retry",
        lambda *a, **k: (calls.append(1), ([], True))[1],
    )
    assert canonicalize_document([], schema=SCHEMA, vocabulary=[], model="m") == {}
    assert calls == []


def test_a_failed_call_maps_every_name_to_itself_and_never_raises(monkeypatch):
    """Canonicalization is a quality step, not a correctness one: degrading
    beats failing the ingest. (Unlike the projection rebuild, which fails its
    commit on purpose.)"""
    monkeypatch.setattr("artmind.extraction.extract_with_retry", lambda *a, **k: ([], False))
    entities = [{"name": "Tier 2 Rate", "entity_class": "RATE_ENTRY"}]
    assert canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m") == {
        "Tier 2 Rate": "Tier 2 Rate"
    }


def test_an_exception_inside_the_call_is_contained(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr("artmind.extraction.extract_with_retry", boom)
    entities = [{"name": "Tier 2 Rate", "entity_class": "RATE_ENTRY"}]
    assert canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m") == {
        "Tier 2 Rate": "Tier 2 Rate"
    }


def test_a_name_the_model_omitted_defaults_to_itself(monkeypatch):
    monkeypatch.setattr(
        "artmind.extraction.extract_with_retry",
        lambda *a, **k: ([{"name": "A", "canonical_name": "Canonical A"}], True),
    )
    entities = [
        {"name": "A", "entity_class": "RATE_ENTRY"},
        {"name": "B", "entity_class": "RATE_ENTRY"},
    ]
    mapping = canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m")
    assert mapping == {"A": "Canonical A", "B": "B"}


def test_the_model_cannot_invent_a_mapping_for_a_name_the_document_never_had(monkeypatch):
    monkeypatch.setattr(
        "artmind.extraction.extract_with_retry",
        lambda *a, **k: ([{"name": "Never Extracted", "canonical_name": "Something"}], True),
    )
    entities = [{"name": "A", "entity_class": "RATE_ENTRY"}]
    mapping = canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m")
    assert mapping == {"A": "A"}
    assert "Never Extracted" not in mapping


def test_a_dict_shaped_response_is_accepted_too(monkeypatch):
    monkeypatch.setattr(
        "artmind.extraction.extract_with_retry",
        lambda *a, **k: ({"A": "Canonical A"}, True),
    )
    entities = [{"name": "A", "entity_class": "RATE_ENTRY"}]
    assert canonicalize_document(entities, schema=SCHEMA, vocabulary=[], model="m") == {"A": "Canonical A"}


# ── the model's echo is never byte-identical ────────────────────────────────


EXTRACTED = "SmartSaver Account Tier 2 Rate — 4.70% AER (£10,001–£50,000), effective 2026-01-15"


@pytest.mark.parametrize(
    "label,echoed",
    [
        ("exact", EXTRACTED),
        ("hyphen for em-dash", EXTRACTED.replace("—", "-")),
        ("en-dash normalized", EXTRACTED.replace("–", "-")),
        ("trailing period", EXTRACTED + "."),
        ("collapsed double space", EXTRACTED.replace(" Tier", "  Tier")),
        ("all lowercase", EXTRACTED.lower()),
    ],
)
def test_a_rewrite_survives_the_model_reformatting_the_name_it_echoes(label, echoed):
    """Exact-string matching silently discarded the rewrite whenever the model
    echoed a name back with a hyphen for an em-dash, a collapsed double space
    or a trailing period — which is most of the time on names carrying `—`,
    `–` and `£`. The symptom was a canonicalization pass that appeared to run
    and changed nothing at all."""
    from artmind.canonicalize import _apply_mapping

    out = _apply_mapping(
        {"RATE_ENTRY": [EXTRACTED]},
        [{"name": echoed, "canonical_name": "SmartSaver Account Tier 2 Rate"}],
    )
    assert out[EXTRACTED] == "SmartSaver Account Tier 2 Rate", label


def test_tolerant_matching_does_not_conflate_two_different_measurements():
    """The match fold must NOT be the key function: that strips measurement
    tails, so a rewrite aimed at the 4.70% entry would land on the 5.25% one."""
    from artmind.canonicalize import _apply_mapping

    a = "SmartSaver Tier 2 Rate — 4.70% AER"
    b = "SmartSaver Tier 2 Rate — 5.25% AER"
    out = _apply_mapping(
        {"RATE_ENTRY": [a, b]}, [{"name": a, "canonical_name": "SmartSaver Tier 2 Rate"}]
    )
    assert out[a] == "SmartSaver Tier 2 Rate"
    assert out[b] == b, "the untouched entry must keep its own name"


def test_a_returned_name_matching_nothing_is_reported(monkeypatch):
    from artmind.canonicalize import _apply_mapping

    warnings = []
    monkeypatch.setattr("artmind.canonicalize.logger.warning",
                        lambda *a, **k: warnings.append(a))
    out = _apply_mapping(
        {"RATE_ENTRY": ["Real Name"]},
        [{"name": "Hallucinated Name", "canonical_name": "Something"}],
    )
    assert out == {"Real Name": "Real Name"}
    assert any("matched nothing" in str(w) for w in warnings)
