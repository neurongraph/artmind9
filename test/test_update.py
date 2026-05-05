# test/test_update.py
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from artmind.update import _classify_input, extract_facts


def test_classify_input_atomic_fact():
    assert _classify_input("Alice is the CEO") == "atomic_fact"


def test_classify_input_todo():
    assert _classify_input("TODO: call Bob tomorrow") == "todo"


def test_classify_input_need_to():
    assert _classify_input("Need to review the proposal") == "todo"


def test_classify_input_passage():
    text = "Alice works at Acme. Bob is her manager. They met in 2020."
    assert _classify_input(text) == "passage"


def test_classify_input_bulk():
    long_text = "a " * 300  # > 500 chars
    assert _classify_input(long_text) == "bulk"


def test_extract_facts_returns_entities_with_temp_ids():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    mock_props = [{"name": "Alice", "properties": {"role": "CEO"}}]
    mock_rels = [{"source_name": "Alice", "target_name": "Alice", "rel_type": "KNOWS"}]

    with patch("artmind.update.extract_with_retry") as mock:
        mock.side_effect = [
            (mock_entities, True),
            (mock_props, True),
            (mock_rels, True),
        ]
        result = extract_facts("Alice is CEO.", "general", schema, text_model="test-model")

    assert len(result["entities"]) == 1
    assert result["entities"][0]["name"] == "Alice"
    assert result["entities"][0]["temp_id"] == "e0"
    assert result["entities"][0]["properties"]["role"] == "CEO"


def test_extract_facts_returns_empty_on_entity_failure():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    with patch("artmind.update.extract_with_retry") as mock:
        mock.return_value = ([], False)
        result = extract_facts("some text", "general", schema, text_model="test-model")

    assert result["entities"] == []
    assert result["relationships"] == []
    assert mock.call_count == 1


def test_extract_facts_maps_relationship_source_target_to_temp_ids():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_entities = [
        {"id": "e0", "entity_class": "PERSON", "name": "Alice"},
        {"id": "e1", "entity_class": "ORG", "name": "Acme"},
    ]
    mock_rels = [{"source_name": "Alice", "target_name": "Acme", "rel_type": "WORKS_AT"}]

    with patch("artmind.update.extract_with_retry") as mock:
        mock.side_effect = [
            (mock_entities, True),
            ([], True),
            (mock_rels, True),
        ]
        result = extract_facts("Alice works at Acme.", "general", schema, text_model="test-model")

    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["source_temp_id"] == "e0"
    assert result["relationships"][0]["target_temp_id"] == "e1"
    assert result["relationships"][0]["rel_type"] == "WORKS_AT"


from artmind.update import find_candidates


def test_find_candidates_returns_domain_matches_first():
    mock_rows = [
        {"node_id": "n1", "name": "Alice Smith", "entity_class": "PERSON",
         "context_snippet": "CEO of Acme", "match_score": 1.0}
    ]
    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows
        result = find_candidates("Alice", "PERSON", "general", top_n=5)

    assert len(result) == 1
    assert result[0]["name"] == "Alice Smith"


def test_find_candidates_falls_back_to_global_when_domain_empty():
    global_rows = [
        {"node_id": "n2", "name": "Alice Jones", "entity_class": "PERSON",
         "context_snippet": None, "match_score": 0.5}
    ]

    def run_side_effect(cypher, **kwargs):
        mock_result = MagicMock()
        mock_result.data.return_value = [] if "e.domain = $domain" in cypher else global_rows
        return mock_result

    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        result = find_candidates("Alice", "PERSON", "general", top_n=5)

    assert len(result) == 1
    assert result[0]["name"] == "Alice Jones"


from artmind.update import write_user_chat, draft_update, confirm_update


def test_write_user_chat_creates_node_and_returns_summary():
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1",
            raw_text="Alice is CEO.",
            domain="general",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=[],
        )

    assert "user_chat_id" in result
    assert result["nodes_created"] == 1
    assert result["nodes_updated"] == 0
    assert result["relationships_written"] == 0


def test_write_user_chat_skipped_entity_not_written():
    resolutions = [{"entity_temp_id": "e0", "action": "skip", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1",
            raw_text="Alice is CEO.",
            domain="general",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=[],
        )

    assert result["nodes_created"] == 0
    assert result["nodes_updated"] == 0


def test_draft_update_stores_draft_and_returns_session_id():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_facts = {"entities": [], "relationships": []}

    with patch("artmind.update.extract_facts", return_value=mock_facts), \
         patch("artmind.update.find_candidates", return_value=[]), \
         patch("artmind.update._load_schema", return_value=schema), \
         patch("artmind.update._create_update_session"), \
         patch("artmind.update._create_update_draft", return_value=1):

        result = draft_update(
            domain="general",
            text="Alice is CEO.",
            session_id=None,
            user_id="alice@example.com",
        )

    assert "session_id" in result
    assert "extracted_entities" in result
    assert "candidates_per_entity" in result


from artmind.update import export_chats


def test_export_chats_sequential_writes_markdown(tmp_path):
    mock_rows = [
        {
            "session_id": "s1", "id": "c1", "raw_text": "Alice is CEO.",
            "domain": "general", "created_by": "alice@example.com",
            "created_at": "2026-05-05T10:00:00", "input_hint": "atomic_fact",
            "mentions": ["Alice"],
        }
    ]
    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows

        written = export_chats(domain=None, format="sequential", output_dir=tmp_path)

    assert len(written) == 1
    content = written[0].read_text()
    assert "Alice is CEO." in content
    assert "alice@example.com" in content


def test_export_chats_by_entity_writes_one_file_per_entity(tmp_path):
    mock_rows = [
        {
            "entity_name": "Alice",
            "chats": [
                {"id": "c1", "raw_text": "Alice is CEO.", "created_by": "alice@example.com",
                 "created_at": "2026-05-05T10:00:00", "domain": "general"},
            ],
        }
    ]
    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows

        written = export_chats(domain=None, format="by-entity", output_dir=tmp_path)

    assert len(written) == 1
    content = written[0].read_text()
    assert "Alice" in content
    assert "Alice is CEO." in content
