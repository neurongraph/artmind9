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


def test_pattern8_brief_rows_and_params(monkeypatch):
    calls = _paged(monkeypatch, 3)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme", "--shape", "brief", "--limit", "2"])
    assert [(c["offset"], c["limit"]) for c in calls] == [(0, 2)]
    assert out["parameters"]["shape"] == "brief"
    assert set(out["rows"][0]) == {"id", "name", "class", "description", "rel_type", "observations", "documents"}
    assert out["rows"][0]["id"] == "e0" and out["rows"][0]["rel_type"] == "R"
    assert out["rows_total"] == 3 and out["truncated"] is True and out["complete"] is False
    assert "--shape brief --all" in out["next"] and "--shape full" in out["next"]


def test_pattern8_all_brief_pages_in_steps_of_limit(monkeypatch):
    calls = _paged(monkeypatch, 120)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme", "--all", "--shape", "brief"])
    assert [(c["offset"], c["limit"]) for c in calls] == [(0, 50), (50, 50), (100, 50)]
    assert len(out["rows"]) == 120 and out["complete"] is True and out["truncated"] is False
    assert all("rel_type" in r and "entityData" not in r for r in out["rows"])


def test_pattern8_full_shape_unchanged(monkeypatch):
    _paged(monkeypatch, 2)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme"])
    assert "entityData" in out["rows"][0] and "shape" not in out["parameters"]


# --- pattern8 --via mentions ----------------------------------------------------------


def _mention_row(i, total, linked):
    return {"entityData": {"_id": f"m{i}", "name": f"M{i}"}, "linked": linked, "shared_chunks": 3 - i,
            "documents": ["n.md"], "_rowsTotal": total}


def test_pattern8_mentions_aggregates_across_anchors_in_cypher():
    cypher, _ = gq._pattern_query(
        "pattern8", {"domains": ["d"], "entityClass": "PERSON", "entityName": "Sam", "via": "mentions"})
    assert "collect(t) AS anchors" in cypher and "UNWIND anchors AS t" in cypher
    assert "NOT e IN anchors" in cypher and "a IN anchors" in cypher
    assert "chunk_ids" in cypher and "documents_total" in cypher and "[0..5]" in cypher
    assert "NOT e IN anchors" in cypher


def test_pattern8_mentions_pages_before_collecting_per_row_lists():
    cypher, _ = gq._pattern_query(
        "pattern8", {"domains": ["d"], "entityClass": "PERSON", "entityName": "Sam", "via": "mentions"})
    assert "[0..5000]" in cypher
    head, tail = cypher.split("LIMIT $limit", 1)[0], cypher
    page_at = cypher.index("SKIP $offset")
    # chunk / document lists are built only after paging, per row
    assert cypher.index("CALL (e, chunkSet)") > page_at
    assert "collect(c.id)" not in cypher[:page_at] and "collect(DISTINCT d.name)" not in cypher[:page_at]


def test_pattern8_mentions_chunk_cap_marks_incomplete(monkeypatch):
    row = {**_mention_row(0, 1, False), "_chunksTotal": 7000}
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [dict(row)])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert out["complete"] is False
    assert "anchor appears in 7000 chunks; listed co-mentions from the first 5000" in out["warnings"]
    assert "_chunksTotal" not in out["rows"][0]
    row["_chunksTotal"] = 5000
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert out["complete"] is True


def test_pattern8_mentions_memory_error_is_clean_failure(monkeypatch):
    from neo4j.exceptions import TransientError

    def boom(c, p):
        raise TransientError("Neo.TransientError.General.MemoryPoolOutOfMemoryError: pool exhausted")

    monkeypatch.setattr(gq, "_run_read_query", boom)
    r = CliRunner().invoke(cli, ["query", "graph", "pattern8", "--domain", "d", "--entityClass", "PERSON",
                                 "--entityId", "x1", "--via", "mentions"])
    assert r.exit_code == 1 and "too many mentions to list in one query" in r.output
    assert "Traceback" not in r.output


def test_pattern8_mentions_multi_anchor_warns(monkeypatch):
    row = {**_mention_row(0, 1, True), "_anchors": 2}
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [dict(row)])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityName", "Sam", "--via", "mentions"])
    assert "--entityName matched 2 entities; using all of them \u2014 pass --entityId for one" in out["warnings"]
    assert "_anchors" not in out["rows"][0]
    row["_anchors"] = 1
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityName", "Sam", "--via", "mentions"])
    assert not [w for w in out["warnings"] if "matched" in w]
    row["_anchors"] = 2
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert not [w for w in out["warnings"] if "matched" in w]


def test_pattern8_mentions_rows_carry_chunk_ids_and_documents_total(monkeypatch):
    row = {**_mention_row(0, 1, False), "chunk_ids": ["c1", "c2"], "documents_total": 7}
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [dict(row)])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert out["rows"][0]["chunk_ids"] == ["c1", "c2"] and out["rows"][0]["documents_total"] == 7
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions", "--shape", "brief"])
    b = out["rows"][0]
    assert b["chunk_ids"] == ["c1", "c2"] and b["documents_total"] == 7 and isinstance(b["documents"], list)


def test_pattern8_invalid_via_is_usage_error():
    r = CliRunner().invoke(cli, ["query", "graph", "pattern8", "--domain", "d", "--entityClass", "ORG",
                                 "--entityName", "A", "--via", "bogus"])
    assert r.exit_code == 2


def test_pattern8_mentions_cypher_shape():
    cypher, params = gq._pattern_query(
        "pattern8", {"domains": ["d"], "entityClass": "PERSON", "entityId": "x1", "via": "mentions"})
    assert "-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)" in cypher
    assert "(c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(e:PERSON)" in cypher
    assert "NOT e IN anchors" in cypher
    assert "EXISTS { MATCH (e)-[:RELATES_TO]-(a) WHERE a IN anchors } AS linked" in cypher
    assert "count(DISTINCT c) AS shared" in cypher
    assert "ORDER BY linked DESC, shared_chunks DESC, e.name, e._id" in cypher
    assert "SKIP $offset" in cypher and "LIMIT $limit" in cypher and "_rowsTotal" in cypher
    assert params["entityId"] == "x1" and params["offset"] == 0


def test_pattern8_links_cypher_unchanged_by_default():
    base = {"domains": ["d"], "entityClass": "ORG", "entityName": "A"}
    default, _ = gq._pattern_query("pattern8", base)
    explicit, _ = gq._pattern_query("pattern8", {**base, "via": "links"})
    assert default == explicit
    assert "MATCH (e:ORG)-[r:RELATES_TO]-(t:Entity)" in default and "AGGREGATES" not in default


def test_pattern8_mentions_cli_rows(monkeypatch):
    rows = [_mention_row(0, 2, True), _mention_row(1, 2, False)]
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [dict(r) for r in rows])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert out["parameters"]["via"] == "mentions"
    assert [(r["linked"], r["shared_chunks"], r["documents"]) for r in out["rows"]] == [
        (True, 3, ["n.md"]), (False, 2, ["n.md"])]
    assert out["complete"] is True


def test_pattern8_default_payload_has_no_via(monkeypatch):
    _paged(monkeypatch, 1)
    out = _run(["pattern8", "--entityClass", "ORG", "--entityName", "Acme"])
    assert "via" not in out["parameters"]


def test_pattern8_mentions_all_pages_and_next(monkeypatch):
    calls = []

    def fake(cypher, params):
        calls.append(dict(params))
        lo = params["offset"]
        return [_mention_row(i % 3, 120, i % 2 == 0) for i in range(lo, min(120, lo + params["limit"]))]

    monkeypatch.setattr(gq, "_run_read_query", fake)
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions", "--all"])
    assert [(c["offset"], c["limit"]) for c in calls] == [(0, 50), (50, 50), (100, 50)]
    assert len(out["rows"]) == 120 and out["complete"] is True

    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [_mention_row(0, 120, True)])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions"])
    assert out["complete"] is False
    assert "--via mentions" in out["next"] and "--all" in out["next"]


def test_pattern8_mentions_brief_rows(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda c, p: [_mention_row(0, 1, False)])
    out = _run(["pattern8", "--entityClass", "PERSON", "--entityId", "x1", "--via", "mentions",
                "--shape", "brief"])
    row = out["rows"][0]
    assert row["linked"] is False and row["shared_chunks"] == 3 and row["documents"] == ["n.md"]
    assert "rel_type" not in row
