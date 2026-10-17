"""The packaged journal schema tells extraction who "I" is, and that a
companion on a trip went there too.

Journal entries are first-person. Without a narrator rule the extractor
invented placeholder names for the author ("Self", "Journal Author", ...) or
attributed the author's actions to another person named in the same entry,
and "I went to X with Y" produced no went_to edge for Y.
"""

from pathlib import Path

import pytest
import yaml

from artmind import extraction

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "artmind" / "domains" / "schemas" / "personal_journal_schema.yaml"

# Invented entry: same shape as the failure (a first-person trip to a named
# person's event, with a companion, and a later return).
ENTRY = ("2031-03-04\n\nI worked from home in the morning. I went to Lakeport for Arlo Venn's wedding "
         "with Mira. We stayed at the Harbour Inn. Later Mira returned from Lakeport by the evening ferry.")
ENTITIES = [
    {"id": "p1", "name": "Diarist", "entity_class": "PERSON"},
    {"id": "p2", "name": "Mira", "entity_class": "PERSON"},
    {"id": "p3", "name": "Arlo Venn", "entity_class": "PERSON"},
    {"id": "l1", "name": "Lakeport", "entity_class": "LOCATION"},
    {"id": "a1", "name": "Work from Home", "entity_class": "ACTIVITY"},
]


@pytest.fixture(scope="module")
def schema():
    return yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_guidance_names_one_narrator_and_forbids_the_placeholders(schema):
    rule = schema["guidance"]["entities"]
    assert 'named exactly "Diarist"' in rule
    for placeholder in ('"Self"', '"Journal Author"', '"Narrator"'):
        assert placeholder in rule
    assert "NEVER as another person named in the" in rule


def test_entities_prompt_carries_the_narrator_rule(schema):
    prompt = extraction.build_entities_prompt(ENTRY, schema)
    assert "DOMAIN-SPECIFIC GUIDANCE:" in prompt
    assert 'named exactly "Diarist"' in prompt
    assert ENTRY in prompt


def test_relationships_prompt_carries_companion_and_return_rules(schema):
    prompt = extraction.build_relationships_prompt(ENTRY, ENTITIES, schema)
    assert 'Attribute first-person actions ("I drove", "I went", "I stayed",' in prompt
    assert "for EVERY\ntraveller" in prompt or "for EVERY traveller" in prompt
    assert "RETURNS." in prompt
    assert "p2 (PERSON): Mira" in prompt


def test_extraction_keeps_both_travellers_and_one_narrator(schema, monkeypatch):
    """With the LLM answering as the guidance asks, the extracted shape has one
    Diarist, a went_to edge for each traveller, and no narrator action on the
    groom -- and the guidance reached both prompts on the way."""
    from artmind.reviews import reextract

    prompts = []

    def fake(step, model, prompt, debug_dir=None):
        prompts.append((step, prompt))
        if step.endswith("entities"):
            return [dict(e) for e in ENTITIES], True
        if step.endswith("properties"):
            return [], True
        return [{"source_id": "p1", "target_id": "l1", "rel_type": "went_to"},
                {"source_id": "p2", "target_id": "l1", "rel_type": "went_to"},
                {"source_id": "p1", "target_id": "a1", "rel_type": "performs"},
                {"source_id": "p3", "target_id": "p2", "rel_type": "spends_time_with"}], True

    monkeypatch.setattr(extraction, "extract_with_retry", fake)
    out = reextract.extract_chunk(ENTRY, schema, model="test-model")
    shape = reextract.proposed_side(out["entities"], out["relationships"])

    assert sum(1 for name, cls in shape["nodes"] if name == "diarist") == 1
    assert {("diarist", "went_to", "lakeport"), ("mira", "went_to", "lakeport")} <= shape["edges"]
    assert not any(src == "arlo venn" and rel == "performs" for src, rel, _ in shape["edges"])
    entities_prompt = next(p for s, p in prompts if s.endswith("entities"))
    relationships_prompt = next(p for s, p in prompts if s.endswith("relationships"))
    assert 'named exactly "Diarist"' in entities_prompt
    assert "COMPANIONS." in relationships_prompt


# Invented entries for the beneficiary and independent-travel rules.
TRIP_ENTRY = "2025-03-15\n\nSarah is travelling to Chicago this morning. I walked her to Union Station."
TICKET_ENTRY = "2025-06-10\n\nI booked return tickets for Sarah for Friday."
TRIP_ENTITIES = [
    {"id": "p1", "name": "Diarist", "entity_class": "PERSON"},
    {"id": "p2", "name": "Sarah", "entity_class": "PERSON"},
    {"id": "l1", "name": "Chicago", "entity_class": "LOCATION"},
]


def test_entities_prompt_says_for_a_name_is_a_beneficiary_not_a_destination(schema):
    prompt = extraction.build_entities_prompt(TICKET_ENTRY, schema)
    assert "BENEFICIARY vs DESTINATION." in prompt
    assert "Never classify a person as a\nLOCATION" in prompt
    assert 'in "I booked tickets\nfor Sarah", Sarah is a PERSON' in prompt
    assert TICKET_ENTRY in prompt


def test_relationships_prompt_carries_independent_travel_and_drop_off_rules(schema):
    prompt = extraction.build_relationships_prompt(TRIP_ENTRY, TRIP_ENTITIES, schema)
    assert "INDEPENDENT TRAVEL." in prompt
    assert '"Y is going to X"' in prompt
    assert "dropped Y at the station for their trip to X" in prompt
    assert "p2 (PERSON): Sarah" in prompt


def test_independent_travel_keeps_the_companion_a_person_with_a_went_to_edge(schema, monkeypatch):
    from artmind.reviews import reextract

    def fake(step, model, prompt, debug_dir=None):
        if step.endswith("entities"):
            return [dict(e) for e in TRIP_ENTITIES], True
        if step.endswith("properties"):
            return [], True
        return [{"source_id": "p2", "target_id": "l1", "rel_type": "went_to"}], True

    monkeypatch.setattr(extraction, "extract_with_retry", fake)
    out = reextract.extract_chunk(TRIP_ENTRY, schema, model="test-model")
    shape = reextract.proposed_side(out["entities"], out["relationships"])

    assert any(name == "sarah" and cls.upper() == "PERSON" for name, cls in shape["nodes"])
    assert not any(name == "sarah" and cls.upper() == "LOCATION" for name, cls in shape["nodes"])
    assert ("sarah", "went_to", "chicago") in shape["edges"]
