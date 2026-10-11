"""The prompt builders accept an alternative meta.yaml dict (for a dry-run
re-extraction under a proposed prompt template)."""

import copy

from artmind import extraction, prompt_builder

SCHEMA = {"name": "d", "description": "A test domain.", "entity_types": {
    "PERSON": {"kind": "recurrent", "description": "A person.", "type_examples": ["friend"]}}}


def _meta_with_marker(step):
    meta = copy.deepcopy(prompt_builder.load_meta())
    meta["prompt_templates"][step] = "MARKER-" + step + "\n" + meta["prompt_templates"][step]
    return meta


def test_entities_prompt_uses_given_meta():
    meta = _meta_with_marker("entities")
    assert extraction.build_entities_prompt("Some text.", SCHEMA, meta=meta).startswith("MARKER-entities")
    assert "MARKER-entities" not in extraction.build_entities_prompt("Some text.", SCHEMA)


def test_properties_and_relationships_prompts_use_given_meta():
    ents = [{"id": "p1", "entity_class": "PERSON", "name": "Mira"}]
    props = extraction.build_properties_prompt("t", ents, SCHEMA, meta=_meta_with_marker("properties"))
    rels = extraction.build_relationships_prompt("t", ents, SCHEMA, meta=_meta_with_marker("relationships"))
    assert props.startswith("MARKER-properties") and "p1 (PERSON): Mira" in props
    assert rels.startswith("MARKER-relationships") and "p1 (PERSON): Mira" in rels


def test_default_meta_output_unchanged():
    ents = [{"id": "p1", "entity_class": "PERSON", "name": "Mira"}]
    assert extraction.build_relationships_prompt("t", ents, SCHEMA) == \
        extraction.build_relationships_prompt("t", ents, SCHEMA, meta=None)
