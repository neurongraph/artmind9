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


def test_assemble_entities_prompt_instructs_singular_naming():
    """Deliberate scope decision after Finding A's re-analysis: the vast
    majority of same-class/domain near-duplicates are legitimately distinct
    entities (rate tiers, severities, regions) that a name-similarity metric
    over-flags -- only the true singular/plural split ("Mortgage" /
    "Mortgages", "Standing Order" / "Standing Orders") is a genuine defect
    worth an extraction-time fix. Mirrors the properties prompt's singular-key
    rule (Finding B) for entity names."""
    result = assemble_entities_prompt(SCHEMA)
    assert "SINGULAR" in result
    assert "singular sense" in result


def test_assemble_properties_prompt_lists_properties_and_hints():
    result = assemble_properties_prompt(SCHEMA)
    assert "For PERSON, extract:" in result
    assert "- role (their role in the document)" in result
    assert "- affiliation" in result
    # LOCATION has no properties -- no empty "For LOCATION, extract:" block
    assert "For LOCATION, extract:" not in result
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


# ── the document-date anchor (temporal.relative_anchor) ────────────────────

_DATED_SCHEMA = {
    **SCHEMA,
    "entity_types": {
        **SCHEMA["entity_types"],
        "EVENT": {
            "kind": "occurrent",
            "description": "Something that happened.",
            "properties": {"date_or_time": {"temporal": "valid_from"}, "outcome": {}},
        },
    },
}


def test_assemble_properties_prompt_without_anchor_lists_the_forms_only():
    prompt = assemble_properties_prompt(_DATED_SCHEMA)
    assert prompt == assemble_properties_prompt(_DATED_SCHEMA, anchor_date=None)
    assert "DATES" in prompt and "YYYY-Qn" in prompt
    assert "document dated" not in prompt


def test_assemble_properties_prompt_anchors_relative_dates_to_the_document_date():
    prompt = assemble_properties_prompt(_DATED_SCHEMA, anchor_date="2026-01-07")
    assert "DATES" in prompt
    assert "2026-01-07" in prompt
    # Names the exact date properties, so the instruction can't bleed into
    # free-text properties.
    assert "EVENT.date_or_time" in prompt
    assert "PERSON.role" not in prompt.split("DATES", 1)[1]
    # Leads with the ISO date -- what parse_iso reads -- keeping the wording.
    assert "YYYY-MM-DD" in prompt
    # Found live: without this, the model stamped `date_or_time` onto every
    # PERSON/LOCATION/OBJECT in the passage.
    assert "Do not add a date property to any other entity" in prompt


def test_assemble_properties_prompt_anchor_ignored_when_no_property_is_temporal():
    # Nothing to resolve: SCHEMA declares no `temporal:` property.
    assert "DATES" not in assemble_properties_prompt(SCHEMA, anchor_date="2026-01-07")


# ── the naming rule no longer teaches every domain banking (study item 6) ────


def _recurrent_schema(**decl):
    return {
        "name": "contracts", "subject": "contracts",
        "entity_types": {"AGREEMENT": {"kind": "recurrent", "description": "A contract.", **decl}},
    }


def test_the_recurrent_naming_rule_carries_no_banking_example():
    prompt = assemble_entities_prompt(_recurrent_schema())

    assert "naming:" in prompt and "NEVER embed" in prompt
    assert "SmartSaver" not in prompt and "AER" not in prompt


def test_a_class_can_opt_in_to_its_own_naming_example():
    prompt = assemble_entities_prompt(_recurrent_schema(
        naming_example={
            "right": "Master Services Agreement",
            "wrong": "Master Services Agreement, amended 2026-03-01",
        },
    ))

    assert 'RIGHT: "Master Services Agreement"' in prompt
    assert 'WRONG: "Master Services Agreement, amended 2026-03-01"' in prompt
    assert prompt.index("NEVER embed") < prompt.index("RIGHT:")  # after the rule it illustrates


def test_an_occurrent_class_gets_no_recurrent_example():
    schema = _recurrent_schema(naming_example={"right": "X", "wrong": "Y"})
    schema["entity_types"]["AGREEMENT"]["kind"] = "occurrent"
    assert 'RIGHT: "X"' not in assemble_entities_prompt(schema)


def test_the_example_is_shown_under_the_class_that_declares_it_only():
    schema = _recurrent_schema(naming_example={"right": "Master Services Agreement", "wrong": "MSA v2"})
    schema["entity_types"]["PARTY"] = {"kind": "recurrent", "description": "A party."}
    prompt = assemble_entities_prompt(schema)

    assert prompt.count('RIGHT: "Master Services Agreement"') == 1


def test_a_half_declared_example_is_ignored_rather_than_rendered_broken():
    prompt = assemble_entities_prompt(_recurrent_schema(naming_example={"right": "Only the right half"}))
    assert "Only the right half" not in prompt


def test_properties_prompt_is_schema_only():
    result = assemble_properties_prompt(SCHEMA)
    assert "Extract ONLY the properties listed" in result and "Never add a key" in result
    assert "NO fixed schema" not in result
    assert "For PERSON, extract:" in result and "consider:" not in result


def test_a_required_key_is_marked():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {
        "role": {"hint": "their role", "required": True}, "affiliation": {}}}}}
    result = assemble_properties_prompt(schema)
    assert "- role (their role) (required when stated)" in result
    assert "- affiliation (required" not in result


def test_only_the_given_classes_are_rendered():
    schema = {"entity_types": {"PERSON": {"kind": "recurrent", "properties": {"role": {}}},
                               "ORG": {"kind": "recurrent", "properties": {"sector": {}}}}}
    result = assemble_properties_prompt(schema, classes={"ORG"})
    assert "For ORG, extract:" in result and "For PERSON" not in result


def test_an_old_template_token_is_blanked():
    meta = load_meta()
    old = {**meta, "prompt_templates": {**meta["prompt_templates"],
                                        "properties": "{{PROPERTY_BLOCKS}}\n{{PROPERTY_VOCABULARY}}\n{entities_list}{text}"}}
    assert "{{PROPERTY_VOCABULARY}}" not in assemble_properties_prompt(SCHEMA, meta=old)


# ── the DATES section states the accepted forms (spec 2026-11-15 §3.3) ────────

_TYPES = {"EVENT": {"properties": {"date_or_time": {"temporal": "valid_from"}}}}


def test_dates_section_lists_ranges_months_and_quarters():
    from artmind.prompt_builder import _render_anchor_date_section

    text = _render_anchor_date_section(_TYPES, "2026-01-07")
    for form in ("YYYY-MM-DD to YYYY-MM-DD", "YYYY-MM", "YYYY-Qn"):
        assert form in text
    assert "whenever the text gives one" in text and "2026-01-07" in text


def test_dates_section_without_an_anchor_still_lists_the_forms():
    from artmind.prompt_builder import _render_anchor_date_section

    text = _render_anchor_date_section(_TYPES, None)
    assert "YYYY-Qn" in text and "document dated" not in text


def test_every_form_the_section_names_is_one_parse_valid_reads():
    import re

    from artmind.prompt_builder import _render_anchor_date_section
    from artmind.temporal import parse_valid

    text = _render_anchor_date_section(_TYPES, None)
    forms = re.findall(r"^  - [a-z ]+: (.+?)(?: -- .*)?$", text, re.MULTILINE)
    assert len(forms) == 5
    fill = {"YYYY": "2026", "MM": "03", "DD": "14", "Qn": "Q2"}
    for form in forms:
        value = re.sub(r"YYYY|MM|DD|Qn", lambda m: fill[m.group(0)], form)
        assert parse_valid(value) is not None, form
        assert parse_valid(value + " (evening)") is not None, form
    example = re.search(r'e\.g\. "([^"]+)"', text).group(1)
    parsed = parse_valid(example)
    assert (parsed.start, parsed.time_of_day) == ("2026-01-07", "evening")


def test_occasion_classes_get_the_no_date_naming_rule():
    from artmind.prompt_builder import _render_class_block

    meta = {"kind_naming_rules": {"occurrent": "a date ... belongs IN the name."},
            "identity_naming_rules": {"occasion": "NEVER add a date to the name."}}
    block = _render_class_block("ACTIVITY", {"kind": "occurrent", "identity": "occasion"}, meta)
    assert "NEVER add a date" in block and "belongs IN the name" not in block
    other = _render_class_block("AMENDMENT", {"kind": "occurrent", "identity": "name"}, meta)
    assert "belongs IN the name" in other and "NEVER add a date" not in other


def test_a_meta_without_the_occasion_rule_names_the_migration():
    import pytest
    from artmind.prompt_builder import MetaIncomplete, _render_class_block

    with pytest.raises(MetaIncomplete, match="migrate status"):
        _render_class_block("ACTIVITY", {"kind": "occurrent", "identity": "occasion"}, {"kind_naming_rules": {}})


def test_packaged_meta_carries_the_occasion_rule():
    from pathlib import Path
    import yaml

    meta = yaml.safe_load((Path(__file__).resolve().parent.parent / "artmind" / "domains" / "meta.yaml").read_text())
    assert "NEVER add a date" in meta["identity_naming_rules"]["occasion"]
