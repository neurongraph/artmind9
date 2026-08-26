"""artmind.lifecycle — resolve_document_id must reach a document in history too.

Found live in Phase 5: restore-from-archive deliberately leaves a document in
`history`, and `resolve_document_id`'s Cypher matched `(d:Document)` only, so
`docs restore --documentName <anything, even the exact id>` could not find it.
Fixed in Phase 6 (see docs/redesign-phase5-implementation-notes.md, open
question 4).
"""
from unittest.mock import MagicMock, patch

from artmind.lifecycle import resolve_document_id


def test_resolve_document_id_matches_both_labels():
    session = MagicMock()
    session.run.return_value.data.return_value = [{"id": "doc-1", "name": "Some Doc"}]

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = resolve_document_id("Some Doc")

    assert result == "doc-1"
    cypher = session.run.call_args[0][0]
    assert "MATCH (d) WHERE (d:Document OR d:DocumentHistory)" in cypher


def test_resolve_document_id_finds_a_document_in_history():
    """The exact Phase 5 gap: a document restore-from-archive just retired."""
    session = MagicMock()
    session.run.return_value.data.return_value = [{"id": "hist-doc-1", "name": "Restored Doc"}]

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = resolve_document_id("hist-doc-1")

    assert result == "hist-doc-1"


def test_resolve_document_id_returns_none_when_nothing_matches():
    session = MagicMock()
    session.run.return_value.data.return_value = []

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        assert resolve_document_id("nope") is None
