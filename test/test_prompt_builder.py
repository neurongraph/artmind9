"""Tests for artmind.prompt_builder: runtime prompt assembly from meta.yaml +
a schema's structured entity_types map (Phase 1)."""

from artmind.prompt_builder import (
    assemble_entities_prompt,
    assemble_properties_prompt,
    assemble_relationships_prompt,
    load_meta,
    relationship_pairs,
)

SCHEMA = {
    "name": "fixture",
    "description": "a fixture domain",
    "subject": "fixture domain analysis",
    "persona": "a fixture analyst",
    "guidance": {
        "entities": "ENTITY GUIDANCE LINE",
        "properties": "PROPERTY GUIDANCE LINE",
        "relationships": "RELATIONSHIP GUIDANCE LINE",
    },
    "entity_types": {
        "PERSON": {
            "kind": "recurrent",
            "description": "A named individual.",
            "type_examples": ["author", "subject"],
            "guidance": "PERSON-SPECIFIC NOTE",
            "properties": {
                "role": {"hint": "their role in the document"},
                "affiliation": {},
            },
            "relates_to": {"LOCATION": ["visited", "lives_in"]},
        },
        "LOCATION": {
            "kind": "recurrent",
            "description": "A place.",
            "type_examples": ["country", "city"],
        },
    },
}


def test_assemble_entities_prompt_includes_all_classes_and_tokens_filled():
    result = assemble_entities_prompt(SCHEMA)
    assert "PERSON" in result and "LOCATION" in result
    assert "A named individual." in result
    assert "example type values: author | subject" in result
    assert "fixture domain analysis" in result  # {{SUBJECT}}
    assert "a fixture analyst" in result  # {{PERSONA}}
    assert "PERSON | LOCATION" in result  # {{CLASS_ENUM}}
    assert "PERSON-SPECIFIC NOTE" in result  # per-class guidance
    assert "ENTITY GUIDANCE LINE" in result  # schema-level guidance
    # no unfilled double-brace tokens left behind
    assert "{{" not in result
    # single-brace per-chunk placeholders survive for extraction.py to fill
    assert "{text}" in result


def test_assemble_entities_prompt_falls_back_to_default_persona():
    schema = {k: v for k, v in SCHEMA.items() if k != "persona"}
    result = assemble_entities_prompt(schema)
    assert load_meta()["default_persona"] in result


def test_assemble_properties_prompt_lists_properties_and_hints():
    result = assemble_properties_prompt(SCHEMA)
    assert "For PERSON, consider:" in result
    assert "- role (their role in the document)" in result
    assert "- affiliation" in result
    # LOCATION has no properties -- no empty "For LOCATION, consider:" block
    assert "For LOCATION, consider:" not in result
    assert "PROPERTY GUIDANCE LINE" in result
    assert "PERSON-SPECIFIC NOTE" in result  # per-class guidance also rendered here
    assert "{entities_list}" in result and "{text}" in result


def test_assemble_relationships_prompt_renders_one_line_per_pair():
    result = assemble_relationships_prompt(SCHEMA)
    assert "- PERSON → LOCATION: visited, lives_in" in result
    assert "RELATIONSHIP GUIDANCE LINE" in result
    assert "never an entity class name" in result.lower() or "never a class name" in result.lower()


def test_relationship_pairs_merges_both_sides_of_a_duplicate_declaration():
    entity_types = {
        "A": {"relates_to": {"B": ["knows"]}},
        "B": {"relates_to": {"A": ["likes"]}},
    }
    pairs = relationship_pairs(entity_types)
    assert len(pairs) == 1
    a, b, types = pairs[0]
    assert {a, b} == {"A", "B"}
    assert set(types) == {"knows", "likes"}


def test_relationship_pairs_empty_when_no_relates_to():
    assert relationship_pairs({"A": {}, "B": {}}) == []
