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
        mock_result.data.return_value = [] if "e._domain = $domain" in cypher else global_rows
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
        # A bare MagicMock's execute_write returns a MagicMock WITHOUT ever
        # calling its unit of work — the observation write and rebuild inside
        # the transaction would silently not happen (CLAUDE.md).
        mock_session.execute_write.side_effect = lambda fn, *a, **k: fn(mock_session, *a, **k)

        with patch("artmind.projection.rebuild", return_value={}):
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
        mock_session.execute_write.side_effect = lambda fn, *a, **k: fn(mock_session, *a, **k)

        with patch("artmind.projection.rebuild", return_value={}):
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






def test_write_user_chat_skips_reserved_rel_type_and_writes_normal_one():
    """rel_type normalizing to a reserved system-managed type (e.g. SUPERSEDES,
    or RELATES_TO/ASSERTS_RELATION/AGGREGATES — the system's own collapsed-
    relationship machinery, Phase 4) must never be written by this
    extraction-driven loop — only the audited temporal helpers, or the
    projection rebuild itself, may create those edges/types."""
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
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "higher_than"},
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
        mock_session.execute_write.side_effect = lambda fn, *a, **k: fn(mock_session, *a, **k)

        with patch("artmind.projection.rebuild", return_value={}):
            result = write_user_chat(
                session_id="sess1",
                raw_text="text",
                domain="general",
                user_id="alice@example.com",
                resolutions=resolutions,
                extracted_entities=extracted_entities,
                extracted_relationships=extracted_relationships,
            )

    rel_calls = [(c, kw) for c, kw in calls if "ASSERTS_RELATION" in c and "MERGE" in c]
    rel_types_written = {kw["rel_type"] for _, kw in rel_calls}

    assert "SUPERSEDES" not in rel_types_written
    assert "HIGHER_THAN" in rel_types_written
    assert result["relationships_written"] == 1

    assert any(
        "Rate A" in str(call.args) and "SUPERSEDES" in str(call.args)
        for call in mock_logger.warning.call_args_list
    )




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


from artmind.update import _resolve_target_identity


def _recording_session(run_side_effect):
    session = MagicMock()
    session.run.side_effect = run_side_effect
    return session










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
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        mock_session.execute_write.side_effect = lambda fn, *a, **k: fn(mock_session, *a, **k)

        with patch("artmind.projection.rebuild", return_value={}):
            result = write_user_chat(
                session_id="sess1", raw_text="Alice is CEO.", domain="general",
                user_id="alice@example.com", resolutions=resolutions,
                extracted_entities=extracted_entities, extracted_relationships=[],
            )

    assert result["nodes_updated"] == 0
    assert result["nodes_created"] == 0
    assert not [c for c, _ in calls if "MENTIONS" in c]
    assert any("link resolution" in str(c.args) for c in mock_logger.warning.call_args_list)




def test_write_user_chat_create_on_existing_triple_updates_instead_of_duplicating():
    """A `create` that collides with an existing identity must not duplicate.

    This used to need a guard: `(name, entity_class, domain)` was the identity
    every write path matched on, but only `Entity.id` was constrained, so a
    bare CREATE could mint a second node and leave those matches choosing
    arbitrarily between the two.

    It is now structural. The entity id IS the hash of the aggregate key, so a
    colliding "create" records another observation under the same key and the
    rebuild MERGEs onto the same node — there is no CREATE left to guard, and
    duplication is not expressible."""
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

    session = MagicMock()
    session.run.side_effect = run_side_effect
    # A bare MagicMock's execute_write returns a MagicMock WITHOUT ever calling
    # its unit of work, so the observation write inside the transaction would
    # silently not happen and the test would pass on an empty assertion.
    session.execute_write.side_effect = lambda fn, *a, **k: fn(session, *a, **k)

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity",
               return_value={"id": "alice-uuid", "name": "Alice"}), \
         patch("artmind.update.neo4j_session") as mock_ctx, \
         patch("artmind.update.logger") as mock_logger:
        mock_ctx.return_value.__enter__.return_value = session

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO.", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_created"] == 0
    assert result["nodes_updated"] == 1
    assert not [c for c, _ in calls if "CREATE (e:" in c]
    # One observation, keyed so the rebuild lands it on the existing entity.
    written = [kw["props"] for c, kw in calls if "MERGE (o:Observation" in c]
    assert len(written) == 1
    assert written[0]["canonical_name"] == "Alice"


def test_write_user_chat_relationship_not_counted_when_an_endpoint_is_unresolved():
    """A relationship naming a temp_id that never resolved to an observation
    (skipped, or an unresolved `link`) must not be written or counted. Unlike
    the pre-Phase-4 direct-to-Entity writer, a *resolved* endpoint's
    Observation is always freshly written earlier in this same transaction —
    so there is no longer an "endpoints don't match" failure mode for a
    resolved pair to guard against; the only way an endpoint is missing is
    that it was never resolved at all."""
    resolutions = [
        {"entity_temp_id": "e0", "action": "create", "node_id": None},
        {"entity_temp_id": "e1", "action": "skip", "node_id": None},
    ]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}},
        {"temp_id": "e1", "name": "Acme", "entity_class": "ORG", "properties": {}},
    ]
    extracted_relationships = [
        {"source_temp_id": "e0", "target_temp_id": "e1", "rel_type": "works_at"}
    ]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        return MagicMock()

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        mock_session.execute_write.side_effect = lambda fn, *a, **k: fn(mock_session, *a, **k)

        with patch("artmind.projection.rebuild", return_value={}):
            result = write_user_chat(
                session_id="sess1", raw_text="text", domain="general",
                user_id="alice@example.com", resolutions=resolutions,
                extracted_entities=extracted_entities,
                extracted_relationships=extracted_relationships,
            )

    assert result["relationships_written"] == 0
    assert not [c for c, _ in calls if "ASSERTS_RELATION" in c and "MERGE" in c]


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

    assert "c._domain STARTS WITH ($domain + '.')" in captured["cypher"]


@pytest.mark.parametrize("fmt", ["sequential", "by-entity"])
def test_export_chats_reaches_entities_via_observations_not_mentions(tmp_path, fmt):
    """Regression test: :MENTIONS (UserChat->Entity) was never written since the
    observation model landed (write_user_chat links via EXTRACTED_FROM/AGGREGATES,
    exactly like a document's chunks), so a query built on it silently returns zero
    rows forever rather than erroring — the same class of trap CLAUDE.md documents
    for a bare MagicMock always answering truthily."""
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        result = MagicMock()
        result.data.return_value = []
        return result

    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value.run.side_effect = run_side_effect
        export_chats(domain=None, format=fmt, output_dir=tmp_path)

    assert "MENTIONS" not in captured["cypher"]
    assert "EXTRACTED_FROM" in captured["cypher"]
    assert "AGGREGATES" in captured["cypher"]


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


# ── the defect CLAUDE.md warns about, now closed structurally ───────────────


def test_resolve_target_identity_takes_the_CHOSEN_nodes_identity():
    """`update confirm` used to patch entity properties matched by the LLM's
    extracted name, so a user who picked "Alice Smith" for an extracted "Alice"
    silently updated nothing while the counts still reported success.

    The fix is structural rather than careful: we no longer find-and-patch. We
    take the chosen node's identity and record the observation under it, so the
    aggregate key lands the write on that entity by construction."""
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = {
                "id": "alice-uuid", "name": "Alice Smith",
                "entity_class": "PERSON", "domain": "general",
            }
        return result

    session = _recording_session(run_side_effect)
    target = _resolve_target_identity(session, "4:abc:alice", "Alice", "PERSON", "general")

    assert target["name"] == "Alice Smith", "the chosen node's name, not the extracted one"
    assert calls[0][1]["ref"] == "4:abc:alice"


def test_resolve_target_identity_returns_none_when_nothing_matches():
    def run_side_effect(cypher, **kwargs):
        result = MagicMock()
        result.single.return_value = None
        return result

    session = _recording_session(run_side_effect)
    assert _resolve_target_identity(session, "4:abc:gone", "Alice", "PERSON", "general") is None


def test_a_linked_observation_is_keyed_to_the_chosen_node_not_the_extracted_name():
    """End to end: the observation's canonical_name — and therefore its
    aggregate key, and therefore which Entity it feeds — comes from the node
    the user picked."""
    from artmind.observations import aggregate_key, key_string

    resolutions = [{"entity_temp_id": "e0", "action": "link", "node_id": "4:abc:alice"}]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {"role": "CEO"}}
    ]
    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        if "elementId(e) = $ref" in cypher:
            result.single.return_value = {
                "id": "alice-uuid", "name": "Alice Smith",
                "entity_class": "PERSON", "domain": "general",
            }
        else:
            result.single.return_value = None
        result.data.return_value = []
        return result

    session = MagicMock()
    session.run.side_effect = run_side_effect
    session.execute_write.side_effect = lambda fn, *a, **k: fn(session, *a, **k)

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session

        result = write_user_chat(
            session_id="sess1", raw_text="Alice is CEO.", domain="general",
            user_id="alice@example.com", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["observations_written"] == 1
    written = [kw["props"] for c, kw in calls if "MERGE (o:Observation" in c]
    assert len(written) == 1
    observation = written[0]

    # The verbatim name is preserved — provenance fidelity is why observations exist...
    assert observation["name"] == "Alice"
    # ...while the key follows the node the user actually chose.
    assert observation["canonical_name"] == "Alice Smith"
    assert observation["key"] == key_string(aggregate_key("Alice Smith", "PERSON", "general"))


def test_a_chat_never_writes_entity_properties_directly():
    """The projection owns every Entity property. A direct write would be
    silently reverted by the next rebuild — which is exactly why this path was
    retargeted rather than left alone."""
    import inspect

    from artmind.update import write_user_chat as fn

    src = inspect.getsource(fn)
    assert "SET e +=" not in src
    assert "CREATE (e:" not in src
    assert "MERGE (o:Observation" in src


def test_retraction_writes_a_thin_observation_carrying__retracts():
    """Entity-level supersession (`Entity.superseded_by`, `status='superseded'`)
    is gone — those were projection-owned and would have been wiped by the
    next rebuild. Its replacement is observation-level: a `retracts` entry on
    a resolution becomes its OWN thin observation under the SAME aggregate
    key, carrying `_retracts` and no domain properties of its own. The
    rebuild (`projection.apply_retractions`) is what actually demotes the
    target — this test only covers write_user_chat's own half: does it build
    and send the retracting observation at all."""
    resolutions = [{
        "entity_temp_id": "e0", "action": "create", "node_id": None,
        "retracts": ["old-observation-id-123"],
    }]
    extracted_entities = [
        {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
    ]

    calls = []

    def run_side_effect(cypher, **kwargs):
        calls.append((cypher, kwargs))
        result = MagicMock()
        # Every lookup (retraction target, embed-missing sweep, etc.) reports
        # "not found" rather than the bare-MagicMock default of truthy-for-
        # anything -- CLAUDE.md's trap: a mock that answers every query
        # truthily can't tell a real match from no match at all.
        result.single.return_value = None
        result.data.return_value = []
        return result

    session = MagicMock()
    session.run.side_effect = run_side_effect
    session.execute_write.side_effect = lambda fn, *a, **k: fn(session, *a, **k)

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update._find_existing_entity", return_value=None), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = write_user_chat(
            session_id="s", raw_text="Alice is no longer branch manager.", domain="general",
            user_id="u", resolutions=resolutions,
            extracted_entities=extracted_entities, extracted_relationships=[],
        )

    assert result["nodes_retracted"] == 1
    assert "nodes_superseded" not in result

    observation_writes = [
        kwargs["props"] for cypher, kwargs in calls
        if cypher.strip().startswith("MERGE (o:Observation") and "props" in kwargs
    ]
    retracting = [p for p in observation_writes if p.get("_retracts")]
    assert len(retracting) == 1
    assert retracting[0]["_retracts"] == "old-observation-id-123"
    # Same identity as the resolution's own entity -- no synthetic entity.
    assert retracting[0]["entity_class"] == "PERSON"
    assert retracting[0]["canonical_name"] == "Alice"
