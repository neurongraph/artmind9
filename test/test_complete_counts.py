"""`--all` and `complete` on pattern7, pattern8 and entity-listing (P2-4). Synthetic data only."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from artmind import graph_query as gq
from artmind.cli import cli


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)


def _run(args):
    r = CliRunner().invoke(cli, ["query", "graph", *args, "--domain", "d", "--compact"])
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def _p8_row(i, total):
    return {"entityData": {"_id": f"e{i}", "name": f"E{i}"}, "relType": "R", "relProps": {}, "_rowsTotal": total}


def _paged(monkeypatch, total):
    calls = []

    def fake(cypher, params):
        calls.append(dict(params))
        lo = params["offset"]
        return [_p8_row(i, total) for i in range(lo, min(total, lo + params["limit"]))]

    monkeypatch.setattr(gq, "_run_read_query", fake)
    return calls


def test_pattern8_all_pages_in_steps_of_limit(monkeypatch):
    calls = _paged(monkeypatch, 120)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme", "--all"])
    assert [(c["offset"], c["limit"]) for c in calls] == [(0, 50), (50, 50), (100, 50)]
    assert len(out["rows"]) == 120 and out["rows_total"] == 120
    assert out["complete"] is True and out["truncated"] is False


def test_pattern8_all_caps_at_all_cap(monkeypatch):
    calls = _paged(monkeypatch, 5000)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme", "--all", "--limit", "500"])
    assert [c["offset"] for c in calls] == [0, 500, 1000, 1500]
    assert len(out["rows"]) == gq.ALL_CAP == 2000
    assert out["rows_total"] == 5000 and out["complete"] is False and out["truncated"] is True


def test_pattern8_all_rejects_offset(monkeypatch):
    _paged(monkeypatch, 10)
    r = CliRunner().invoke(cli, ["query", "graph", "pattern8", "--domain", "d", "--entityClass", "ORG",
                                 "--entityName", "A", "--all", "--offset", "5"])
    assert r.exit_code == 1


def test_pattern8_incomplete_says_all(monkeypatch):
    _paged(monkeypatch, 120)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "O'Neil"])
    assert out["complete"] is False and len(out["rows"]) == 50
    assert out["next"] == ("artmind query graph pattern8 --domain d --entityClass ORG "
                           "--entityName 'O'\"'\"'Neil' --all --compact")


def test_pattern8_complete_has_no_next(monkeypatch):
    _paged(monkeypatch, 3)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme"])
    assert out["complete"] is True and out["next"] is None


def _p7_rows(n, total, raw):
    return [{"entityData": {"_id": f"e{i}", "name": f"E{i}"}, "documents": [], "document_count": 0,
             "_rowsTotal": total, "_rawHits": raw} for i in range(n)]


def test_pattern7_capped_is_incomplete(monkeypatch):
    sent = {}

    def fake(cypher, params):
        sent.update(params)
        return _p7_rows(params["limit"], 1000, 1000)

    monkeypatch.setattr(gq, "_run_read_query", fake)
    out = _run(["pattern7", "--searchTerm", "harbour", "--all"])
    assert sent["limit"] == 500
    assert out["rows_total_capped"] is True and out["complete"] is False


def test_pattern7_all_whole_is_complete(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: _p7_rows(min(p["limit"], 44), 44, 44))
    out = _run(["pattern7", "--searchTerm", "harbour", "--all"])
    assert out["complete"] is True and out["next"] is None


def test_pattern7_truncated_next_is_all_command(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: _p7_rows(p["limit"], 44, 44))
    out = _run(["pattern7", "--searchTerm", "harbour"])
    assert out["complete"] is False
    assert out["next"] == "artmind query graph pattern7 --domain d --searchTerm harbour --all --compact"


def test_entity_listing_groups_and_all(monkeypatch):
    rows = [{"label": "ORG", "typeGroups": [
        {"type": "a", "names": [f"n{i:03d}" for i in range(80)]},
        {"type": "b", "names": ["x", "y"]}]}]
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: json.loads(json.dumps(rows)))
    out = _run(["entity-listing"])
    groups = out["rows"][0]["typeGroups"]
    assert [g["complete"] for g in groups] == [False, True]
    assert groups[0]["names_total"] == 80 and len(groups[0]["names"]) == 50
    assert out["complete"] is False
    assert out["next"] == "artmind query graph entity-listing --domain d --all --compact"
    full = _run(["entity-listing", "--all"])
    assert len(full["rows"][0]["typeGroups"][0]["names"]) == 80
    assert full["complete"] is True and full["next"] is None


def test_budget_cut_sets_complete_false(monkeypatch, tmp_path):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p:
                        [{"entityData": {"_id": f"e{i}", "name": "N" * 200}, "documents": [], "document_count": 0,
                          "_rowsTotal": 8, "_rawHits": 8} for i in range(8)])
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "1500")
    out = _run(["pattern7", "--searchTerm", "n", "--limit", "8"])
    assert out["truncated"] is True and out["complete"] is False and out["handle"]


# --- review fixes ---------------------------------------------------------------------


def test_pattern8_order_by_is_a_total_order():
    cypher, _ = gq._pattern_query("pattern8", {"domains": ["d"], "entityClass": "ORG", "entityName": "A"})
    order = cypher.split("ORDER BY", 1)[1].split("SKIP", 1)[0]
    assert "e._id" in order and "elementId(r)" in order


def test_pattern7_at_max_limit_has_no_all_next(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: _p7_rows(p["limit"], 600, 600))
    out = _run(["pattern7", "--searchTerm", "h", "--limit", "500"])
    assert out["complete"] is False and out["next"] is None


def test_pattern7_all_capped_warns_and_has_no_next(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: _p7_rows(p["limit"], 1000, 1000))
    out = _run(["pattern7", "--searchTerm", "h", "--all"])
    assert out["next"] is None
    assert "pattern7 matched more than 1000 entities; narrow --searchTerm or --domain" in out["warnings"]


def test_pattern8_all_at_cap_warns(monkeypatch):
    _paged(monkeypatch, 5000)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "A", "--all", "--limit", "500"])
    assert ("showing 2000 of 5000; narrow with --entityName/--entityId or another --entityClass"
            in out["warnings"])


def test_pattern8_all_total_change_is_incomplete(monkeypatch):
    def fake(cypher, params):
        total = 120 if params["offset"] == 0 else 110
        lo = params["offset"]
        return [_p8_row(i, total) for i in range(lo, min(total, lo + params["limit"]))]

    monkeypatch.setattr(gq, "_run_read_query", fake)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "A", "--all"])
    assert out["rows_total"] == 120 and out["complete"] is False
    assert "the graph changed while paging" in out["warnings"]


@pytest.mark.parametrize("limit", ["0", "-5"])
def test_pattern8_all_rejects_non_positive_limit(monkeypatch, limit):
    _paged(monkeypatch, 10)
    r = CliRunner().invoke(cli, ["query", "graph", "pattern8", "--domain", "d", "--entityClass", "ORG",
                                 "--entityName", "A", "--all", "--limit", limit])
    assert r.exit_code == 1
