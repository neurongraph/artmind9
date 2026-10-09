"""Pins what `kg_views` relies on: entity_resolve survives a dead embedding service.

Spec 2026-10-09 section 7 asks for resolution to degrade to the fulltext leg
when Ollama is down. vector_query.entity_resolve already catches the vector
leg's failure; this test keeps it that way, because a view run with an
entity param would otherwise die whenever the embedder is unreachable.
"""

from unittest.mock import MagicMock, patch

from artmind import vector_query


def _session_returning(ft_rows):
    session = MagicMock()
    session.run.return_value = ft_rows
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    return ctx, session


def test_entity_resolve_falls_back_to_fulltext_when_embedding_fails():
    ft = [{"score": 2.0, "entity": {"_id": "e1", "name": "Retail Mortgage", "entity_class": "Product"}}]
    ctx, session = _session_returning(ft)
    with patch.object(vector_query, "read_session", return_value=ctx), patch.object(
        vector_query, "embed_question", side_effect=ConnectionError("ollama down")
    ):
        result = vector_query.entity_resolve(["finance"], "mortgage", 5)

    assert [r["entity"]["_id"] for r in result["rows"]] == ["e1"]
    assert result["rows"][0]["matched_by"] == ["fulltext"]
    assert session.run.call_count == 1  # only the fulltext query ran
