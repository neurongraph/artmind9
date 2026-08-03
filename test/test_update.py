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


def test_find_candidates_ranks_exact_match_before_fulltext_score():
    """Lucene ftScore is unbounded (often > 1.0), so exact matches must be
    ranked via a flag, not a score remapped to 1.0 that fuzzy hits can beat."""
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        captured["kwargs"] = kwargs
        mock_result = MagicMock()
        mock_result.data.return_value = [{"name": "Alice"}]
        return mock_result

    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        find_candidates("Alice", "PERSON", "general", top_n=5)

    cypher = captured["cypher"]
    assert "THEN 1.0 ELSE ftScore" not in cypher
    assert "is_exact" in cypher
    # exact-match flag must be the primary sort key, ahead of match_score
    order_clause = cypher[cypher.index("ORDER BY"):]
    assert order_clause.index("is_exact") < order_clause.index("match_score")


def test_find_candidates_passes_entity_class_to_both_queries():
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        mock_result = MagicMock()
        # empty domain result forces fallback to the global query
        mock_result.data.return_value = []
        return mock_result

    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        find_candidates("Alice", "PERSON", "general", top_n=5)

    assert len(calls) == 2
    for cypher, kwargs in calls:
        assert kwargs["entity_class"] == "PERSON"
        assert "$entity_class" in cypher


from artmind.update import write_user_chat, draft_update, confirm_update


def test_write_user_chat_creates_node_and_returns_summary():
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
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


def test_write_user_chat_processes_supersedes_for_created_entity():
    resolutions = [
        {
            "entity_temp_id": "e0", "action": "create", "node_id": None,
            "supersedes": [{"node_id": "4:abc:older", "effective": "2026-07-18", "reason": "role changed"}],
        }
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Harry Potter", "entity_class": "PERSON", "properties": {}}
    ]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.apply_node_supersession") as mock_supersede:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1",
            raw_text="The manager changed to Harry Potter.",
            domain="banking_organization",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=[],
        )

    assert result["nodes_superseded"] == 1
    mock_supersede.assert_called_once()
    _, kwargs = mock_supersede.call_args
    assert kwargs["older_id"] == "4:abc:older"
    assert kwargs["effective"] == "2026-07-18"
    assert kwargs["reason"] == "role changed"
    assert kwargs["detected_by"] == "user_update"
    # newer_id must be the uuid generated for the newly created entity, not None
    assert kwargs["newer_id"]


def test_write_user_chat_supersedes_defaults_effective_to_today():
    resolutions = [
        {"entity_temp_id": "e0", "action": "create", "node_id": None,
         "supersedes": [{"node_id": "4:abc:older"}]}
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Harry Potter", "entity_class": "PERSON", "properties": {}}
    ]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.apply_node_supersession") as mock_supersede, \
         patch("artmind.update.date") as mock_date:
        mock_date.today.return_value.isoformat.return_value = "2026-07-18"
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        write_user_chat(
            session_id="sess1", raw_text="text", domain="general", user_id="alice@example.com",
            resolutions=resolutions, extracted_entities=extracted_entities, extracted_relationships=[],
        )

    _, kwargs = mock_supersede.call_args
    assert kwargs["effective"] == "2026-07-18"


def test_write_user_chat_skips_reserved_rel_type_and_writes_normal_one():
    """rel_type normalizing to a reserved system-managed type (e.g. SUPERSEDES)
    must never be written by this extraction-driven loop — only the audited
    temporal helpers may create those edges."""
    resolutions = [
        {"entity_temp_id": "e0", "action": "create", "node_id": None},
        {"entity_temp_id": "e1", "action": "create", "node_id": None},
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Rate A", "entity_class": "RATE", "properties": {}},
        {"temp_id": "e1", "name": "Rate B", "entity_class": "RATE", "properties": {}},
    ]
    extracted_relationships = [
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "supersedes"},
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "relates_to"},
    ]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.logger") as mock_logger:
        mock_session = mock_ctx.return_value.__enter__.return_value
        calls = []

        def run_side_effect(cypher, **kwargs):
            calls.append((cypher, kwargs))
            return MagicMock()

        mock_session.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1",
            raw_text="text",
            domain="general",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=extracted_relationships,
        )

    rel_calls = [(c, kw) for c, kw in calls if "apoc.merge.relationship" in c]
    rel_types_written = {kw["type"] for _, kw in rel_calls}

    assert "SUPERSEDES" not in rel_types_written
    assert "RELATES_TO" in rel_types_written
    assert result["relationships_written"] == 1

    assert any(
        "Rate A" in str(call.args) and "Rate B" in str(call.args) and "SUPERSEDES" in str(call.args)
        for call in mock_logger.warning.call_args_list
    )


def test_write_user_chat_no_supersedes_leaves_count_zero():
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.apply_node_supersession") as mock_supersede:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO.", domain="general", user_id="alice@example.com",
            resolutions=resolutions, extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_superseded"] == 0
    mock_supersede.assert_not_called()


from artmind.update import find_supersession_candidates, _detect_supersession_candidates


def test_find_supersession_candidates_queries_by_element_id():
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        captured["kwargs"] = kwargs
        mock_result = MagicMock()
        mock_result.data.return_value = [
            {"node_id": "4:abc:older", "name": "Branch Manager - James Chen",
             "entity_class": "PERSON", "rel_type": "headed_by"}
        ]
        return mock_result

    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect

        result = find_supersession_candidates(
            "4:abc:branch", "headed_by", "Harry Potter",
        )

    assert len(result) == 1
    assert result[0]["name"] == "Branch Manager - James Chen"
    assert captured["kwargs"]["sourceNodeId"] == "4:abc:branch"
    assert captured["kwargs"]["targetName"] == "Harry Potter"


def test_detect_supersession_candidates_flags_same_source_rel_type_different_target():
    entities = [
        {"temp_id": "e0", "name": "London Canary Wharf Branch"},
        {"temp_id": "e2", "name": "Harry Potter"},
    ]
    relationships = [
        {"source_temp_id": "e0", "target_temp_id": "e2", "rel_type": "headed_by"}
    ]
    candidates_per_entity = [
        {"temp_id": "e0", "top_n": [{"node_id": "4:abc:branch", "name": "London Canary Wharf Branch", "is_exact": True}]},
        {"temp_id": "e2", "top_n": []},
    ]

    with patch(
        "artmind.update.find_supersession_candidates",
        return_value=[{"node_id": "4:abc:older", "name": "Branch Manager - James Chen", "entity_class": "PERSON"}],
    ) as mock_find:
        result = _detect_supersession_candidates(entities, relationships, candidates_per_entity)

    assert len(result) == 1
    assert result[0]["source_name"] == "London Canary Wharf Branch"
    assert result[0]["new_target_name"] == "Harry Potter"
    assert result[0]["replaces"][0]["name"] == "Branch Manager - James Chen"
    mock_find.assert_called_once_with("4:abc:branch", "headed_by", "Harry Potter")


def test_detect_supersession_candidates_skips_fuzzy_source_match():
    entities = [{"temp_id": "e0", "name": "Canary Wharf"}, {"temp_id": "e2", "name": "Harry Potter"}]
    relationships = [{"source_temp_id": "e0", "target_temp_id": "e2", "rel_type": "headed_by"}]
    candidates_per_entity = [
        {"temp_id": "e0", "top_n": [{"node_id": "4:abc:branch", "name": "London Canary Wharf Branch", "is_exact": False}]},
        {"temp_id": "e2", "top_n": []},
    ]

    with patch("artmind.update.find_supersession_candidates") as mock_find:
        result = _detect_supersession_candidates(entities, relationships, candidates_per_entity)

    assert result == []
    mock_find.assert_not_called()


from artmind.update import _link_entity_in_session


def _recording_session(run_side_effect):
    session = MagicMock()
    session.run.side_effect = run_side_effect
    return session


def test_link_entity_in_session_matches_by_element_id_or_app_id():
    """`node_id` is whichever handle the caller had — find_candidates returns an
    elementId, entity-context/pattern2 return the app-managed `id`. Same
    dual-format contract apply_node_supersession accepts."""
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.single.return_value = {"id": "alice-uuid", "name": "Alice Smith"}
        return result

    node = _link_entity_in_session(
        _recording_session(run_side_effect), "4:abc:alice",
        "Alice", "PERSON", "general", {"role": "CEO"}, "u@example.com", "2026-08-02T00:00:00",
    )

    assert node == {"id": "alice-uuid", "name": "Alice Smith"}
    assert "elementId(e) = $ref OR e.id = $ref" in captured["cypher"]
    assert captured["kwargs"]["ref"] == "4:abc:alice"
    assert captured["kwargs"]["props"]["role"] == "CEO"
    # a node written before Entity.id existed still gets a usable handle back
    assert "coalesce(e.id" in captured["cypher"]


def test_link_entity_in_session_falls_back_to_triple_without_node_ref():
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        captured["kwargs"] = kwargs
        result = MagicMock()
        result.single.return_value = {"id": "alice-uuid", "name": "Alice"}
        return result

    _link_entity_in_session(
        _recording_session(run_side_effect), None,
        "Alice", "PERSON", "general", {}, "u@example.com", "2026-08-02T00:00:00",
    )

    assert "{name: $name, entity_class: $ec, domain: $domain}" in captured["cypher"]
    assert "elementId" not in captured["cypher"]


def test_link_entity_in_session_returns_none_when_nothing_matches():
    def run_side_effect(cypher, **kwargs):
        result = MagicMock()
        result.single.return_value = None
        return result

    node = _link_entity_in_session(
        _recording_session(run_side_effect), "4:abc:gone",
        "Alice", "PERSON", "general", {}, "u@example.com", "2026-08-02T00:00:00",
    )
    assert node is None


def test_write_user_chat_link_resolves_to_chosen_node_not_extracted_name():
    """The case the candidate step exists for: the user picks "Alice Smith" for
    an extracted "Alice". Matching on the extracted (name, entity_class, domain)
    finds nothing, so the whole resolution — node update, MENTIONS edge,
    relationship — silently no-ops while still reporting success."""
    resolutions = [
        {"entity_temp_id": "e0", "action": "link", "node_id": "4:abc:alice"},
        {"entity_temp_id": "e1", "action": "create", "node_id": None},
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {"role": "CEO"}},
        {"temp_id": "e1", "name": "Acme", "entity_class": "ORG", "properties": {}},
    ]
    extracted_relationships = [
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "works_at"}
    ]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = {"id": "alice-uuid", "name": "Alice Smith"}
        return result

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO of Acme.", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=extracted_relationships,
        )

    assert result["nodes_updated"] == 1
    assert result["nodes_created"] == 1

    link_calls = [kw for c, kw in calls if "elementId(e) = $ref" in c]
    assert len(link_calls) == 1
    assert link_calls[0]["ref"] == "4:abc:alice"

    # MENTIONS and the relationship key off the canonical node, not "Alice"
    mention_ids = {kw["entityId"] for c, kw in calls if "MERGE (c)-[:MENTIONS]->(e)" in c}
    assert "alice-uuid" in mention_ids
    rel_calls = [kw for c, kw in calls if "apoc.merge.relationship" in c]
    assert len(rel_calls) == 1
    assert rel_calls[0]["srcId"] == "alice-uuid"
    assert result["relationships_written"] == 1


def test_write_user_chat_unresolved_link_is_not_counted():
    """A node_id pointing at a deleted node must not be reported as an update —
    skip it, warn, and let the rest of the confirm land."""
    resolutions = [{"entity_temp_id": "e0", "action": "link", "node_id": "4:abc:gone"}]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
    ]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = None
        return result

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.logger") as mock_logger:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO.", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_updated"] == 0
    assert result["nodes_created"] == 0
    assert not [c for c, _ in calls if "MENTIONS" in c]
    assert any("link resolution" in str(c.args) for c in mock_logger.warning.call_args_list)


def test_write_user_chat_link_supersession_uses_linked_node_id():
    """Supersession keyed off the linked node's id. It resolved to None before,
    so `supersedes` on a link action was dropped without a trace."""
    resolutions = [
        {"entity_temp_id": "e0", "action": "link", "node_id": "4:abc:alice",
         "supersedes": [{"node_id": "4:abc:older"}]}
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
    ]

    def run_side_effect(cypher, **kwargs):
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = {"id": "alice-uuid", "name": "Alice Smith"}
        return result

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.apply_node_supersession") as mock_supersede:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1", raw_text="text", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_superseded"] == 1
    _, kwargs = mock_supersede.call_args
    assert kwargs["newer_id"] == "alice-uuid"


def test_write_user_chat_create_on_existing_triple_updates_instead_of_duplicating():
    """(name, entity_class, domain) is the identity every other write path
    matches on, but only Entity.id is constrained in Neo4j — a bare CREATE would
    leave those matches choosing arbitrarily between two nodes."""
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None}]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
    ]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = {"id": "alice-uuid", "name": "Alice"}
        return result

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity",
               return_value={"id": "alice-uuid", "name": "Alice"}), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.logger") as mock_logger:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO.", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_created"] == 0
    assert result["nodes_updated"] == 1
    assert not [c for c, _ in calls if "CREATE (e:" in c]
    assert any("create resolution" in str(c.args) for c in mock_logger.warning.call_args_list)


def test_write_user_chat_relationship_not_counted_when_endpoints_dont_match():
    """An empty MATCH raises nothing, so counting the call rather than the
    returned row over-reported relationships_written."""
    resolutions = [
        {"entity_temp_id": "e0", "action": "create", "node_id": None},
        {"entity_temp_id": "e1", "action": "create", "node_id": None},
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}},
        {"temp_id": "e1", "name": "Acme", "entity_class": "ORG", "properties": {}},
    ]
    extracted_relationships = [
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "works_at"}
    ]

    def run_side_effect(cypher, **kwargs):
        result = MagicMock()
        if "apoc.merge.relationship" in cypher:
            result.single.return_value = None
        return result

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect

        result = write_user_chat(
            session_id="sess1", raw_text="text", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=extracted_relationships,
        )

    assert result["relationships_written"] == 0


def test_draft_update_rejects_unknown_session():
    with patch("artmind.update._get_update_session", return_value=None):
        with pytest.raises(ValueError, match="No such update session"):
            draft_update(
                domain="general", text="Alice is CEO.",
                session_id="nope", user_id="alice@example.com",
            )


def test_draft_update_rejects_domain_mismatch_on_resume():
    """confirm writes with the session's domain, so a resumed turn given a
    different --domain would extract from one domain and write to another."""
    with patch(
        "artmind.update._get_update_session",
        return_value={"session_id": "s1", "domain": "banking", "status": "draft"},
    ):
        with pytest.raises(ValueError, match="belongs to domain 'banking'"):
            draft_update(
                domain="general", text="Alice is CEO.",
                session_id="s1", user_id="alice@example.com",
            )


def test_draft_update_resumes_session_with_matching_domain():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    with patch("artmind.update.extract_facts", return_value={"entities": [], "relationships": []}), \
         patch("artmind.update._load_schema", return_value=schema), \
         patch("artmind.update._get_update_session",
               return_value={"session_id": "s1", "domain": "general", "status": "draft"}), \
         patch("artmind.update._create_update_session") as mock_create, \
         patch("artmind.update._create_update_draft", return_value=1):

        result = draft_update(
            domain="general", text="Alice is CEO.",
            session_id="s1", user_id="alice@example.com",
        )

    assert result["session_id"] == "s1"
    mock_create.assert_not_called()


@pytest.mark.parametrize("fmt", ["sequential", "by-entity"])
def test_export_chats_domain_filter_rolls_up_descendants(tmp_path, fmt):
    """A parent-domain filter includes descendants, the convention every other
    domain-scoped path follows (graph_query.domain_predicate)."""
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        result = MagicMock()
        result.data.return_value = []
        return result

    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect
        export_chats(domain="banking", format=fmt, output_dir=tmp_path)

    assert "c.domain STARTS WITH ($domain + '.')" in captured["cypher"]


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
