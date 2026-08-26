"""artmind.sameas — the same-as proposal review queue (no Neo4j, no LLM)."""
from unittest.mock import MagicMock, patch

import pytest

from artmind.sameas import approve, proposal_id, propose, reject


def test_proposal_id_is_order_independent_in_members():
    a = proposal_id("c|C|d", ["c|C|d", "m1|C|d", "m2|C|d"])
    b = proposal_id("c|C|d", ["m2|C|d", "c|C|d", "m1|C|d"])
    assert a == b


def test_proposal_id_differs_by_canonical():
    members = ["a|C|d", "b|C|d"]
    assert proposal_id("a|C|d", members) != proposal_id("b|C|d", members)


def test_propose_writes_a_merge_style_query_with_canonical_first():
    session = MagicMock()
    canonical = ("fca", "REGULATOR", "banking.reference")
    other = ("financial conduct authority", "REGULATOR", "banking.risk_governance")

    pid = propose(session, canonical, [other], source="adjudicator", reason="cross-domain match", model="m")

    assert pid == proposal_id("fca|REGULATOR|banking.reference",
                               ["fca|REGULATOR|banking.reference",
                                "financial conduct authority|REGULATOR|banking.risk_governance"])
    cypher, kwargs = session.run.call_args
    assert "MERGE (p:SameAsProposal {id: $id})" in cypher[0]
    assert kwargs["canonical"] == "fca|REGULATOR|banking.reference"
    assert set(kwargs["members"]) == {
        "fca|REGULATOR|banking.reference",
        "financial conduct authority|REGULATOR|banking.risk_governance",
    }
    assert kwargs["source"] == "adjudicator"


def test_propose_adds_canonical_to_members_if_missing():
    session = MagicMock()
    canonical = ("a", "C", "d")
    propose(session, canonical, [("b", "C", "d")], source="refine_graph")
    _, kwargs = session.run.call_args
    assert "a|C|d" in kwargs["members"]


# ── approve ───────────────────────────────────────────────────────────────────


def _fake_proposal(**overrides):
    p = {
        "id": "pid1", "status": "open",
        "canonical": "fca|REGULATOR|banking.reference",
        "members": ["fca|REGULATOR|banking.reference", "financial conduct authority|REGULATOR|banking.reference"],
    }
    p.update(overrides)
    return p


def test_approve_appends_group_and_marks_approved():
    saved_groups = []

    with patch("artmind.sameas.get_proposal", return_value=_fake_proposal()), \
         patch("artmind.same_as.load_groups", return_value=[]), \
         patch("artmind.same_as.save_groups", side_effect=lambda groups, path=None: saved_groups.extend(groups)), \
         patch("artmind.projection.full_rebuild", return_value={"rebuilt": 2}), \
         patch("artmind.sameas.neo4j_session") as mock_ctx:
        session = MagicMock()
        session.execute_write.side_effect = lambda fn: fn(session)
        mock_ctx.return_value.__enter__.return_value = session

        result = approve("pid1")

    assert result["status"] == "approved"
    assert result["canonical"] == "fca|REGULATOR|banking.reference"
    assert len(saved_groups) == 1
    assert saved_groups[0][0] == ("fca", "REGULATOR", "banking.reference")

    # the proposal's own status update ran
    status_calls = [c for c in session.run.call_args_list if "SET p.status = 'approved'" in c.args[0]]
    assert len(status_calls) == 1


def test_approve_rejects_a_canonical_not_in_the_proposals_members():
    with patch("artmind.sameas.get_proposal", return_value=_fake_proposal()):
        with pytest.raises(ValueError, match="not among"):
            approve("pid1", canonical="not-a-member|C|d")


def test_approve_rejects_an_already_resolved_proposal():
    with patch("artmind.sameas.get_proposal", return_value=_fake_proposal(status="approved")):
        with pytest.raises(ValueError, match="already"):
            approve("pid1")


def test_approve_raises_on_unknown_proposal():
    with patch("artmind.sameas.get_proposal", return_value=None):
        with pytest.raises(ValueError, match="No SameAsProposal"):
            approve("nope")


# ── reject ────────────────────────────────────────────────────────────────────


def test_reject_sets_status_and_reason():
    session = MagicMock()
    session.run.return_value.single.return_value = {"id": "pid1", "status": "rejected"}

    with patch("artmind.sameas.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = reject("pid1", "not the same thing")

    assert result == {"id": "pid1", "status": "rejected", "reason": "not the same thing"}
    _, kwargs = session.run.call_args
    assert kwargs["reason"] == "not the same thing"


def test_reject_raises_on_unknown_proposal():
    session = MagicMock()
    session.run.return_value.single.return_value = None
    with patch("artmind.sameas.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        with pytest.raises(ValueError, match="No SameAsProposal"):
            reject("nope")
