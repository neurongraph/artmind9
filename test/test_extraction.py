from unittest.mock import MagicMock, patch

from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    entities_list_text,
    parse_json_response,
    extract_with_retry,
)


def test_build_entities_prompt_substitutes_text():
    schema = {"entities_prompt": "Extract from: {text}"}
    assert build_entities_prompt("my text", schema) == "Extract from: my text"


def test_build_properties_prompt_substitutes_entities_and_text():
    schema = {"properties_prompt": "Entities: {entities_list}\nText: {text}"}
    entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    result = build_properties_prompt("my text", entities, schema)
    assert "e0 (PERSON): Alice" in result
    assert "my text" in result


def test_build_relationships_prompt_substitutes_entities_and_text():
    schema = {"relationships_prompt": "Rels: {entities_list}\nText: {text}"}
    entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    result = build_relationships_prompt("my text", entities, schema)
    assert "e0 (PERSON): Alice" in result
    assert "my text" in result


def test_entities_list_text_formats_correctly():
    entities = [
        {"id": "e1", "entity_class": "PERSON", "name": "Alice"},
        {"id": "e2", "entity_class": "LOCATION", "name": "London"},
    ]
    assert entities_list_text(entities) == "e1 (PERSON): Alice\ne2 (LOCATION): London"


def test_parse_json_response_strips_think_blocks():
    raw = '<think>reasoning here</think>\n[{"name": "Alice"}]'
    assert parse_json_response(raw) == [{"name": "Alice"}]


def test_parse_json_response_strips_code_fences():
    raw = '```json\n[{"name": "Alice"}]\n```'
    assert parse_json_response(raw) == [{"name": "Alice"}]


def test_parse_json_response_handles_plain_json():
    raw = '[{"name": "Bob"}]'
    assert parse_json_response(raw) == [{"name": "Bob"}]


def test_extract_with_retry_returns_result_on_success():
    with patch("artmind.extraction.call_llm", return_value='[{"name": "Alice"}]'):
        result, ok = extract_with_retry("test_step", "test-model", "some prompt")
    assert ok is True
    assert result == [{"name": "Alice"}]


def test_extract_with_retry_returns_empty_list_on_failure():
    with patch("artmind.extraction.call_llm", side_effect=Exception("LLM error")):
        result, ok = extract_with_retry("test_step", "test-model", "some prompt")
    assert ok is False
    assert result == []
