"""artmind.sameas — the same-as proposal review queue (no Neo4j, no LLM)."""
from unittest.mock import MagicMock, patch

import pytest

from artmind.sameas import approve, approve_many, proposal_id, propose, reject


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


def test_approve_scopes_the_rebuild_to_the_groups_full_domain_strings():
    """Regression for neurongraph/artmind9#12: truncating to the top-level
    domain family (`"banking"`) made every approval as expensive as a full
    corpus rebuild, since every real domain nests one level under it and
    `full_rebuild`'s own scoping rolls a family name back up to all its
    descendants. The full domain string must reach `full_rebuild` untouched.
    """
    proposal = _fake_proposal(
        canonical="fca|REGULATOR|banking.reference",
        members=["fca|REGULATOR|banking.reference", "financial conduct authority|REGULATOR|banking.risk_governance"],
    )
    captured_domains = []

    def fake_full_rebuild(tx, domains, **kwargs):
        captured_domains.append(domains)
        return {"rebuilt": 2}

    with patch("artmind.sameas.get_proposal", return_value=proposal), \
         patch("artmind.same_as.load_groups", return_value=[]), \
         patch("artmind.same_as.save_groups"), \
         patch("artmind.projection.full_rebuild", side_effect=fake_full_rebuild), \
         patch("artmind.sameas.neo4j_session") as mock_ctx:
        session = MagicMock()
        session.execute_write.side_effect = lambda fn: fn(session)
        mock_ctx.return_value.__enter__.return_value = session

        approve("pid1")

    assert captured_domains == [["banking.reference", "banking.risk_governance"]]


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


# ── approve_many ──────────────────────────────────────────────────────────────


def _prop(pid, canonical, *others, status="open"):
    return {"id": pid, "status": status, "canonical": canonical, "members": [canonical, *others]}


class _Batch:
    """Runs `approve_many` against fake proposals and records the writes."""

    def __init__(self, proposals, existing_groups=()):
        self.by_id = {p["id"]: p for p in proposals}
        self.existing = [list(g) for g in existing_groups]
        self.saved = []
        self.rebuilds = []
        self.session = MagicMock()

    def run(self, ids, **kwargs):
        def fake_rebuild(tx, domains, **kw):
            self.rebuilds.append(domains)
            return {"rebuilt": 1}

        with patch("artmind.sameas.get_proposal", side_effect=self.by_id.get), \
             patch("artmind.same_as.load_groups", return_value=self.existing), \
             patch("artmind.same_as.save_groups", side_effect=lambda g, path=None: self.saved.append(list(g))), \
             patch("artmind.projection.full_rebuild", side_effect=fake_rebuild), \
             patch("artmind.sameas.neo4j_session") as ctx:
            self.session.execute_write.side_effect = lambda fn: fn(self.session)
            ctx.return_value.__enter__.return_value = self.session
            return approve_many(ids, **kwargs)

    def marked(self):
        calls = [c for c in self.session.run.call_args_list if "SET p.status = 'approved'" in c.args[0]]
        return [[i["id"] for i in c.kwargs["items"]] for c in calls]


def test_approve_many_writes_the_file_once_and_rebuilds_once_over_the_union_of_domains():
    b = _Batch([
        _prop("p1", "a|C|d1", "a2|C|d1"),
        _prop("p2", "b|C|d2", "b2|C|d2"),
        _prop("p3", "c|C|d1", "c2|C|d3"),
    ])

    result = b.run(["p1", "p2", "p3"])

    assert [x["id"] for x in result["approved"]] == ["p1", "p2", "p3"]
    assert result["skipped"] == []
    assert len(b.saved) == 1 and len(b.saved[0]) == 3
    assert b.rebuilds == [["d1", "d2", "d3"]]
    assert b.marked() == [["p1", "p2", "p3"]]
    assert result["projection"] == {"rebuilt": 1}


def test_approve_many_skips_a_claimed_canonical_and_leaves_the_proposal_open():
    existing = [[("a", "C", "d"), ("a2", "C", "d")]]
    b = _Batch([_prop("p1", "a|C|d", "a3|C|d"), _prop("p2", "b|C|d", "b2|C|d")], existing)

    result = b.run(["p1", "p2"])

    assert [x["id"] for x in result["approved"]] == ["p2"]
    assert [x["id"] for x in result["skipped"]] == ["p1"]
    assert "already claimed" in result["skipped"][0]["reason"]
    assert b.marked() == [["p2"]]


def test_approve_many_checks_against_earlier_proposals_in_the_same_batch():
    b = _Batch([_prop("p1", "x|C|d", "y|C|d"), _prop("p2", "x|C|d", "z|C|d")])

    result = b.run(["p1", "p2"])

    assert [x["id"] for x in result["approved"]] == ["p1"]
    assert [x["id"] for x in result["skipped"]] == ["p2"]
    assert len(b.saved[0]) == 1


def test_approve_many_drops_a_claimed_member_but_keeps_the_rest_of_the_group():
    existing = [[("m", "C", "d"), ("m2", "C", "d")]]
    b = _Batch([_prop("p1", "a|C|d", "m|C|d", "n|C|d")], existing)

    result = b.run(["p1"])

    done = result["approved"][0]
    assert done["status"] == "partial"
    assert done["dropped_members"] == ["m|C|d"]
    assert done["group"] == ["a|C|d", "n|C|d"]


def test_approve_many_skips_a_group_left_with_no_other_member():
    existing = [[("m", "C", "d"), ("m2", "C", "d")]]
    b = _Batch([_prop("p1", "a|C|d", "m|C|d")], existing)

    result = b.run(["p1"])

    assert result["approved"] == []
    assert "every other member" in result["skipped"][0]["reason"]


def test_approve_many_reports_unknown_and_already_resolved_without_stopping():
    b = _Batch([_prop("p1", "a|C|d", "a2|C|d", status="approved"), _prop("p2", "b|C|d", "b2|C|d")])

    result = b.run(["nope", "p1", "p2"])

    assert [x["id"] for x in result["approved"]] == ["p2"]
    reasons = {x["id"]: x["reason"] for x in result["skipped"]}
    assert "No SameAsProposal" in reasons["nope"]
    assert "already" in reasons["p1"]


def test_approve_many_with_nothing_approvable_writes_and_rebuilds_nothing():
    b = _Batch([_prop("p1", "a|C|d", "a2|C|d", status="rejected")])

    result = b.run(["p1"])

    assert result == {"approved": [], "skipped": result["skipped"], "projection": None}
    assert b.saved == [] and b.rebuilds == [] and b.session.run.call_args_list == []


def test_approve_many_ignores_a_repeated_id():
    b = _Batch([_prop("p1", "a|C|d", "a2|C|d")])

    result = b.run(["p1", "p1"])

    assert [x["id"] for x in result["approved"]] == ["p1"]
    assert result["skipped"] == []


def test_approve_refuses_a_claimed_canonical_instead_of_reporting_approved():
    """The bug this guards: the proposal was marked approved and the command
    printed success, but `validate_groups` silently dropped the group on the
    next read, so nothing merged."""
    proposal = _fake_proposal()
    with patch("artmind.sameas.get_proposal", return_value=proposal), \
         patch("artmind.same_as.load_groups", return_value=[[("fca", "REGULATOR", "banking.reference"), ("x", "C", "d")]]), \
         patch("artmind.same_as.save_groups") as save, \
         patch("artmind.sameas.neo4j_session") as ctx:
        with pytest.raises(ValueError, match="already claimed"):
            approve("pid1")

    save.assert_not_called()
    ctx.assert_not_called()


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


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(args):
    from click.testing import CliRunner

    from artmind.cli import cli

    return CliRunner().invoke(cli, ["sameas", "approve", *args, "--compact"])


def test_cli_approve_with_one_id_keeps_the_single_approval_shape():
    one = {"id": "p1", "status": "approved", "canonical": "a|C|d", "group": ["a|C|d", "b|C|d"], "projection": {}}
    with patch("artmind.sameas.approve", return_value=one) as single, \
         patch("artmind.sameas.approve_many") as many:
        result = _cli(["p1", "--canonical", "a|C|d"])

    assert result.exit_code == 0
    single.assert_called_once_with("p1", canonical="a|C|d")
    many.assert_not_called()
    assert '"status":"approved"' in result.output.replace(" ", "")


def test_cli_approve_with_several_ids_makes_one_batch_call():
    batch = {"approved": [], "skipped": [{"id": "p2", "reason": "x"}], "projection": None}
    with patch("artmind.sameas.approve_many", return_value=batch) as many, \
         patch("artmind.sameas.approve") as single:
        result = _cli(["p1", "p2", "p3"])

    assert result.exit_code == 0
    many.assert_called_once_with(["p1", "p2", "p3"])
    single.assert_not_called()
    assert '"skipped"' in result.output


def test_cli_approve_rejects_canonical_with_several_ids():
    with patch("artmind.sameas.approve_many") as many:
        result = _cli(["p1", "p2", "--canonical", "a|C|d"])

    assert result.exit_code != 0
    assert "one proposal" in result.output
    many.assert_not_called()


def test_cli_approve_turns_a_single_refusal_into_an_error():
    with patch("artmind.sameas.approve", side_effect=ValueError("already claimed")):
        result = _cli(["p1"])

    assert result.exit_code != 0
    assert "already claimed" in result.output


def test_cli_approve_requires_an_id():
    assert _cli([]).exit_code != 0
