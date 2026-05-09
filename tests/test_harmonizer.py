"""Tests for artmind.harmonizer block extraction and patching logic."""
from artmind.harmonizer import (
    _split_entity_blocks,
    _split_property_blocks,
    _split_relationship_blocks,
    _inject_entity_blocks,
    _inject_property_blocks,
    _inject_relationship_blocks,
)

ENTITIES_PROMPT = """\
You are an extractor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENTITY TYPES YOU MUST EXTRACT:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Use ONLY these entity_classes.

  PERSON
    A named individual.
    example type values: author | subject

  LOCATION
    A place.
    example type values: country | city

  EXTRACTION RULES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Be complete.
"""

PROPERTIES_PROMPT = """\
You are an extractor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROPERTIES MAP GUIDANCE:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  For PERSON, consider:
    - role
    - affiliation

  For LOCATION, consider:
    - country
    - city

  KEY RULES FOR PROPERTIES:
    1. Be clear.
"""

RELATIONSHIPS_PROMPT = """\
You are an extractor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMMON rel_type VALUES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  PERSON ↔ LOCATION:
    visited, lives_in, works_at

  PERSON ↔ PERSON:
    knows, works_with

  EXTRACTION RULES:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Be explicit.
"""


def test_split_entity_blocks_finds_person():
    blocks = _split_entity_blocks(ENTITIES_PROMPT)
    assert 'PERSON' in blocks
    assert 'A named individual.' in blocks['PERSON']


def test_split_entity_blocks_finds_location():
    blocks = _split_entity_blocks(ENTITIES_PROMPT)
    assert 'LOCATION' in blocks
    assert 'A place.' in blocks['LOCATION']


def test_split_entity_blocks_does_not_find_missing_type():
    blocks = _split_entity_blocks(ENTITIES_PROMPT)
    assert 'EVENT' not in blocks


def test_split_property_blocks_finds_person():
    blocks = _split_property_blocks(PROPERTIES_PROMPT)
    assert 'PERSON' in blocks
    assert '- role' in blocks['PERSON']


def test_split_property_blocks_finds_location():
    blocks = _split_property_blocks(PROPERTIES_PROMPT)
    assert 'LOCATION' in blocks
    assert '- country' in blocks['LOCATION']


def test_split_relationship_blocks_person_appears_in_both_blocks():
    blocks = _split_relationship_blocks(RELATIONSHIPS_PROMPT)
    assert 'PERSON' in blocks
    assert len(blocks['PERSON']) == 2  # PERSON ↔ LOCATION and PERSON ↔ PERSON


def test_split_relationship_blocks_location_one_block():
    blocks = _split_relationship_blocks(RELATIONSHIPS_PROMPT)
    assert 'LOCATION' in blocks
    assert len(blocks['LOCATION']) == 1  # Only PERSON ↔ LOCATION


def test_inject_entity_blocks_appears_before_extraction_rules():
    new_block = "  EVENT\n    A happening.\n    example type values: meeting"
    result = _inject_entity_blocks(ENTITIES_PROMPT, [new_block])
    assert 'EVENT' in result
    assert result.index('EVENT') < result.index('EXTRACTION RULES')


def test_inject_property_blocks_appears_before_key_rules():
    new_block = "  For EVENT, consider:\n    - date\n    - participants"
    result = _inject_property_blocks(PROPERTIES_PROMPT, [new_block])
    assert 'For EVENT' in result
    assert result.index('For EVENT') < result.index('KEY RULES FOR PROPERTIES')


def test_inject_relationship_blocks_appears_before_extraction_rules():
    new_block = "  EVENT ↔ PERSON:\n    involves, triggers"
    result = _inject_relationship_blocks(RELATIONSHIPS_PROMPT, [new_block])
    assert 'EVENT ↔ PERSON' in result
    assert result.index('EVENT ↔ PERSON') < result.index('EXTRACTION RULES')
