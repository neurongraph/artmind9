from unittest.mock import MagicMock

import pytest

from artmind import graph_query


def test_normalize_entity_class_matches_ingest_label_shape():
    assert graph_query.normalize_entity_class("Character") == "CHARACTER"
    assert graph_query.normalize_entity_class("project role") == "PROJECT_ROLE"
    assert graph_query.normalize_entity_class("sales-collateral") == "SALES_COLLATERAL"


def test_normalize_entity_class_rejects_empty_value():
    with pytest.raises(ValueError, match="cannot be empty"):
        graph_query.normalize_entity_class("   ")


@pytest.mark.parametrize(
    ("pattern", "params", "expected_fragment"),
    [
        ("pattern1", {}, "--entityClass"),
        ("pattern2", {}, "--entityNameList"),
        ("pattern3", {}, "--entityNameList"),
        ("pattern4", {"entityClass": "CHARACTER"}, "--entityName"),
        (
            "pattern5",
            {
                "entityClass1": "CHARACTER",
                "entityClass2": "LOCATION",
                "entityName1": "Holmes",
            },
            "--entityName2",
        ),
        ("pattern6", {"entityName1": "Holmes"}, "--entityName2"),
        ("pattern7", {}, "--searchTerm"),
        ("pattern8", {"entityClass": "LOCATION"}, "--entityName"),
        ("pattern9", {}, "--entityClass"),
    ],
)
def test_validate_pattern_parameters_reports_missing_options(
    pattern, params, expected_fragment
):
    with pytest.raises(ValueError, match=expected_fragment):
        graph_query.validate_pattern_parameters(pattern, params)


def test_sanitize_lucene_query_strips_specials_keeps_terms():
    assert (
        graph_query.sanitize_lucene_query("St Bartholomew's Hospital (London)?")
        == "St Bartholomew's Hospital London"
    )
    assert graph_query.sanitize_lucene_query("a+b -c \"d\" e:f g/h") == "a b c d e f g h"
    assert graph_query.sanitize_lucene_query("?? !! ()") == ""


def test_pattern7_sanitizes_search_term():
    cypher, params = graph_query._pattern_query(
        "pattern7",
        {"domains": ["fiction"], "searchTerm": "copper-beeches (estate)?", "limit": 10},
    )
    assert "entity_name_ft" in cypher
    assert params["searchTerm"] == "copper beeches estate"


def test_pattern7_rejects_unsearchable_term():
    with pytest.raises(ValueError, match="searchable"):
        graph_query._pattern_query(
            "pattern7", {"domains": ["fiction"], "searchTerm": "(?)", "limit": 10}
        )


def test_validate_pattern_parameters_rejects_unknown_pattern():
    with pytest.raises(ValueError, match="Unsupported"):
        graph_query.validate_pattern_parameters("pattern99", {})


def test_pattern5_query_dispatches_shortest_and_all_modes():
    shortest_cypher, _ = graph_query._pattern_query(
        "pattern5",
        {
            "domains": ["fiction"],
            "entityClass1": "CHARACTER",
            "entityClass2": "LOCATION",
            "entityName1": "Holmes",
            "entityName2": "London",
            "mode": "shortest",
        },
    )
    all_cypher, _ = graph_query._pattern_query(
        "pattern5",
        {
            "domains": ["fiction"],
            "entityClass1": "CHARACTER",
            "entityClass2": "LOCATION",
            "entityName1": "Holmes",
            "entityName2": "London",
            "mode": "all",
        },
    )

    assert "shortestPath" in shortest_cypher
    assert "[*1..5]" in all_cypher
    # paths must stay in entity space — no co-mention hops through DocChunk hubs
    assert "all(x IN nodes(p) WHERE x:Entity)" in shortest_cypher
    assert "all(x IN nodes(p) WHERE x:Entity)" in all_cypher


@pytest.mark.parametrize("pattern", [f"pattern{i}" for i in range(1, 10)])
def test_each_supported_pattern_has_query_dispatch(pattern):
    params = {
        "domains": ["fiction"],
        "entityClass": "CHARACTER",
        "entityClass1": "CHARACTER",
        "entityClass2": "LOCATION",
        "entityName": "Holmes",
        "entityName1": "Holmes",
        "entityName2": "Watson",
        "entityNameList": ["Holmes"],
        "searchTerm": "hair",
        "mode": "shortest",
        "topN": 5,
        "limit": 10,
    }

    cypher, cypher_params = graph_query._pattern_query(pattern, params)

    assert "MATCH" in cypher or "CALL" in cypher
    assert cypher_params["domains"] == ["fiction"]


def test_entity_listing_passes_name_filter_as_parameter(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured.update(params)
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)

    result = graph_query.entity_listing("fiction", name_filter="holmes")

    assert captured["nameFilter"] == "holmes"
    assert result["name_filter"] == "holmes"
    assert "total_entities" not in result


def test_domains_overview_unions_structured_only_domains(monkeypatch):
    """Bug 2, other half: the aggregation only reports domains holding
    Document/Entity nodes, so a corpus whose tables sit at a coarser root than
    its documents never surfaced the table-bearing domain — and the routing
    workflow that starts here could not discover a structured store that
    plainly exists."""
    monkeypatch.setattr(
        graph_query, "_run_read_query",
        lambda cypher, params: [
            {"domain": "banking.policy",
             "parts": [{"k": "documents", "c": 10, "names": ["p.md"]},
                       {"k": "entities", "c": 500, "names": None}]},
        ],
    )

    import artmind.structured.registry as registry

    monkeypatch.setattr(
        registry, "list_tables",
        lambda *a, **kw: [
            {"domain": "banking", "table_name": "customers"},
            {"domain": "banking", "table_name": "complaints"},
        ],
    )

    out = graph_query.domains_overview()
    by_domain = {r["domain"]: r for r in out["rows"]}

    assert out["domains"] == ["banking", "banking.policy"]
    assert by_domain["banking"]["table_count"] == 2
    assert by_domain["banking"]["stores"] == ["structured"]
    assert by_domain["banking.policy"]["stores"] == ["graph"]


def test_domains_overview_expands_list_valued_domain(monkeypatch):
    """`domain` is written as a scalar string, but a node carrying a list-valued
    domain must not crash the aggregation on an unhashable dict key. Such a row
    is attributed to each concrete domain, accumulating/unioning with any scalar
    row for the same domain."""
    monkeypatch.setattr(
        graph_query, "_run_read_query",
        lambda cypher, params: [
            # a well-formed scalar row for banking.policy
            {"domain": "banking.policy",
             "parts": [{"k": "documents", "c": 3, "names": ["p.md"]},
                       {"k": "entities", "c": 40, "names": None}]},
            # a bad row whose domain is a list spanning two domains
            {"domain": ["banking.policy", "banking.cases"],
             "parts": [{"k": "documents", "c": 2, "names": ["q.md"]},
                       {"k": "top_classes", "c": 0, "names": ["Policy"]}]},
        ],
    )

    import artmind.structured.registry as registry

    monkeypatch.setattr(registry, "list_tables", lambda *a, **kw: [])

    out = graph_query.domains_overview()
    by_domain = {r["domain"]: r for r in out["rows"]}

    # both domains surfaced, none is an unhashable key
    assert out["domains"] == ["banking.cases", "banking.policy"]
    # the list row's docs merged into the scalar policy row (counts accumulate,
    # names union) and also seeded the cases row on its own
    assert by_domain["banking.policy"]["document_count"] == 5
    assert set(by_domain["banking.policy"]["documents"]) == {"p.md", "q.md"}
    assert by_domain["banking.policy"]["entity_count"] == 40
    assert by_domain["banking.cases"]["document_count"] == 2
    assert by_domain["banking.cases"]["top_classes"] == ["Policy"]


def test_domains_overview_survives_missing_registry(monkeypatch):
    """A query-only host has no $ARTMIND_DATA_DIR and therefore no registry DB.
    The graph half of the overview must still work there."""
    monkeypatch.setattr(
        graph_query, "_run_read_query",
        lambda cypher, params: [
            {"domain": "fiction", "parts": [{"k": "documents", "c": 2, "names": ["a.md"]}]},
        ],
    )

    import artmind.structured.registry as registry

    def boom(*a, **kw):
        raise RuntimeError("no registry db on this host")

    monkeypatch.setattr(registry, "list_tables", boom)

    out = graph_query.domains_overview()
    assert out["domains"] == ["fiction"]
    assert out["rows"][0]["stores"] == ["graph"]


def test_entity_listing_excludes_the_generic_entity_label(monkeypatch):
    """Every node carries two labels — :Entity plus its class — so a plain
    `UNWIND labels(n)` emitted an extra 'Entity' row holding the whole domain
    (5,400 of 5,426 names on the banking corpus). That catch-all then beat every
    real class in the since-retired deterministic mapping matcher: 8 of 11
    persisted mappings pointed at the non-existent class 'Entity', mostly at
    confidence 1.0. Mapping proposal no longer reads entity_listing at all, but
    the filter still matters for every other consumer (resolve-key, the
    entity-listing query itself). No node carries only :Entity, so filtering it
    out loses nothing."""
    captured = {}

    def fake_run(cypher, params):
        captured["cypher"] = cypher
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    graph_query.entity_listing("banking")

    assert "UNWIND labels(n) AS label" not in captured["cypher"]
    assert "l <> 'Entity'" in captured["cypher"]


def test_entity_listing_count_all_runs_count_query(monkeypatch):
    call_count = [0]

    def fake_run(cypher, params):
        call_count[0] += 1
        if "count" in cypher.lower():
            return [{"total": 37}]
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)

    result = graph_query.entity_listing("fiction", count_all=True)

    assert result["total_entities"] == 37
    assert call_count[0] == 2  # listing query + count query


def test_entity_listing_default_omits_filter_and_count(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured.update(params)
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)

    result = graph_query.entity_listing("fiction")

    assert captured["nameFilter"] is None
    assert "name_filter" not in result
    assert "total_entities" not in result


def test_execute_pattern_shapes_output_and_strips_embeddings(monkeypatch):
    def fake_run(cypher, params):
        return [{"entityData": {"name": "Holmes", "embedding": [0.1]}}]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)

    result = graph_query.execute_pattern(
        "fiction",
        "pattern1",
        "List characters",
        entityClass="Character",
    )

    assert result["parameters"]["entityClass"] == "CHARACTER"
    assert result["rows"] == [{"entityData": {"name": "Holmes"}}]


def test_pattern1_applies_result_limit():
    cypher, params = graph_query._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON"}
    )
    assert "LIMIT $limit" in cypher
    assert params["limit"] == 200


@pytest.mark.parametrize("pattern", ["pattern2", "pattern3", "pattern6"])
def test_name_match_patterns_scope_to_entity_label(pattern):
    params = {
        "domains": ["fiction"],
        "entityNameList": ["Holmes"],
        "entityName1": "Holmes",
        "entityName2": "Watson",
    }
    cypher, _ = graph_query._pattern_query(pattern, params)
    assert ":Entity" in cypher


@pytest.mark.parametrize("pattern", ["pattern3", "pattern4", "pattern8"])
def test_neighborhood_patterns_exclude_structural_nodes(pattern):
    params = {
        "domains": ["fiction"],
        "entityClass": "PERSON",
        "entityName": "Holmes",
        "entityNameList": ["Holmes"],
    }
    cypher, _ = graph_query._pattern_query(pattern, params)
    # connections must traverse entity-entity edges, not MENTIONS into chunks
    assert "(t:Entity)" in cypher


@pytest.mark.parametrize("pattern", ["pattern2", "pattern3", "pattern4"])
def test_doc_source_patterns_return_source_documents_with_paths(pattern):
    """Answers cite their documents by path (artmind-query skill, Sources) --
    patterns 2/3/4 must carry the documents behind `doc_sources`, not just
    chunk ids the agent can't turn into a link."""
    params = {"domains": ["fiction"], "entityClass": "PERSON", "entityName": "Holmes", "entityNameList": ["Holmes"]}
    cypher, _ = graph_query._pattern_query(pattern, params)
    # joined on the observation's doc_id: table2graph observations have no chunk
    assert "(sd:Document {id: o.doc_id})" in cypher
    assert "(o)-[:EXTRACTED_FROM]->(chunk:DocChunk)" in cypher
    assert "sd { .id, .name, .path, .source_path, ._domain }) AS source_documents" in cypher
    assert "doc_sources, source_documents" in cypher


def test_document_projections_carry_source_path():
    """A binary's `path` is its internal converted copy; `source_path` is the
    vault original a Sources link must open."""
    assert ".path, .source_path" in graph_query._chunks_query(0, None)
    context = graph_query._entity_context_query()
    assert "document: d { .id, .name, .path, .source_path" in context
    assert "(sd:Document {id: so.doc_id})" in context and "source_documents" in context
    cypher, _ = graph_query._pattern_query("pattern10", {"domains": ["fiction"], "documentName": "a.md"})
    assert ".path, .source_path" in cypher


def test_pattern9_degree_modes():
    base = {"domains": ["fiction"], "entityClass": "PERSON", "topN": 5}

    relations_cypher, _ = graph_query._pattern_query("pattern9", base)
    # relations-mode neighbor is domain-scoped like every other entity match;
    # Entity carries `_domain` (Phase 4's `_`-prefix), not `domain`.
    assert "(e)-[r:RELATES_TO]-(t:Entity) WHERE (t._domain IN $domains" in relations_cypher

    mentions_cypher, _ = graph_query._pattern_query(
        "pattern9", {**base, "degreeMode": "mentions"}
    )
    # Mentions are reached via the projection's own provenance edge now
    # (AGGREGATES->Observation->EXTRACTED_FROM), not a direct Entity->DocChunk
    # EXTRACTED_FROM (Entity has never carried one since Phase 3) or the dead
    # UserChat MENTIONS edge (never written).
    assert "-[r1:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)" in mentions_cypher

    all_cypher, _ = graph_query._pattern_query("pattern9", {**base, "degreeMode": "all"})
    assert "(e)-[r]-()" in all_cypher


def test_validate_rejects_bad_degree_mode():
    with pytest.raises(ValueError, match="degreeMode"):
        graph_query.validate_pattern_parameters(
            "pattern9", {"entityClass": "PERSON", "degreeMode": "bogus"}
        )


def test_entity_id_takes_precedence_over_name():
    cypher, params = graph_query._pattern_query(
        "pattern4",
        {
            "domains": ["fiction"],
            "entityClass": "PERSON",
            "entityName": "Holmes",
            "entityId": "ent-42",
        },
    )
    assert "e._id = $entityId" in cypher
    assert params["entityId"] == "ent-42"
    assert "entityName" not in params


def test_pattern2_accepts_entity_id_list():
    cypher, params = graph_query._pattern_query(
        "pattern2", {"domains": ["fiction"], "entityIdList": ["ent-1", "ent-2"]}
    )
    assert "e._id IN $entityIdList" in cypher
    assert params["entityIdList"] == ["ent-1", "ent-2"]


def test_pattern6_accepts_entity_ids():
    cypher, params = graph_query._pattern_query(
        "pattern6",
        {"domains": ["fiction"], "entityId1": "ent-1", "entityId2": "ent-2"},
    )
    assert "e1._id = $entityId1" in cypher
    assert "e2._id = $entityId2" in cypher


def test_validate_accepts_id_alternative_for_name():
    # entityId satisfies the entityName requirement
    graph_query.validate_pattern_parameters(
        "pattern4", {"entityClass": "PERSON", "entityId": "ent-42"}
    )
    graph_query.validate_pattern_parameters(
        "pattern2", {"entityIdList": ["ent-1"]}
    )


def test_validate_missing_name_mentions_id_alternative():
    with pytest.raises(ValueError, match=r"--entityName \(or --entityId\)"):
        graph_query.validate_pattern_parameters("pattern4", {"entityClass": "PERSON"})


def test_execute_pattern_with_id_list_normalizes_tuple(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured["cypher"] = cypher
        captured.update(params)
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)

    result = graph_query.execute_pattern(
        "fiction", "pattern2", None, entityIdList=("ent-1",), entityNameList=()
    )

    assert captured["entityIdList"] == ["ent-1"]
    assert result["parameters"]["entityIdList"] == ["ent-1"]


def test_pattern_cypher_reaches_source_chunks_via_the_projection():
    # UserChat/:MENTIONS was dead (never written since Phase 3's write path
    # moved to Observation-based provenance) and is gone from this query; doc
    # sources are reached via AGGREGATES->Observation->EXTRACTED_FROM instead.
    cypher, _ = graph_query._pattern_query("pattern2", {
        "domains": ["general"],
        "entityNameList": ["Alice"],
    })
    assert "AGGREGATES" in cypher and "EXTRACTED_FROM" in cypher
    assert "UserChat" not in cypher


def test_pattern10_query_uses_part_of_relationship():
    cypher, params = graph_query._pattern_query("pattern10", {
        "domains": ["fiction"],
        "documentName": "The Copper Beeches",
    })
    assert "PART_OF" in cypher
    assert "DocChunk" in cypher
    assert "Document" in cypher
    assert params["documentName"] == "The Copper Beeches"
    assert params["domains"] == ["fiction"]


def test_pattern10_validates_document_name_required():
    with pytest.raises(ValueError, match="--documentName"):
        graph_query.validate_pattern_parameters("pattern10", {})


def test_structural_metadata_returns_expected_shape(monkeypatch):
    fake_rows = [
        {"label": "Document", "count": 3, "names": ["doc1", "doc2", "doc3"],
         "relationship": None, "from_label": None, "to_label": None},
        {"label": None, "count": 10, "names": None,
         "relationship": "PART_OF", "from_label": "DocChunk", "to_label": "Document"},
    ]
    monkeypatch.setattr(graph_query, "_run_read_query", lambda cypher, params: fake_rows)

    result = graph_query.structural_metadata("fiction")

    assert result["command"] == "structural_metadata"
    assert result["domain"] == "fiction"
    assert len(result["rows"]) == 2
    assert result["rows"][0]["label"] == "Document"
    assert result["rows"][1]["relationship"] == "PART_OF"


# ── hierarchy ────────────────────────────────────────────────────────────────


def _root_row(entity_id="root1", name="Root", entity_class="ROLE_PERSON"):
    return {"entity": {"_id": entity_id, "name": name, "entity_class": entity_class}}


def _node(entity_id, name, entity_class="ROLE_PERSON"):
    return {"_id": entity_id, "name": name, "entity_class": entity_class}


def _scripted_session(call_results: list[list[dict]]):
    """A fake `read_session()` whose `session.run()` returns `call_results[i]`
    on the i-th call (one call == one round trip), recording each call's
    cypher + parameters -- the `run_side_effect` pattern from test_update.py,
    adapted for a sequence of distinct per-level responses."""
    calls: list[dict] = []
    session = MagicMock()

    def run_side_effect(cypher, **kwargs):
        idx = len(calls)
        calls.append({"cypher": cypher, "params": kwargs})
        return list(call_results[idx]) if idx < len(call_results) else []

    session.run.side_effect = run_side_effect
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx, calls


def _patch_session(monkeypatch, call_results: list[list[dict]]):
    ctx, calls = _scripted_session(call_results)
    monkeypatch.setattr(graph_query, "read_session", lambda: ctx)
    return calls


def test_hierarchy_requires_an_entity_id_or_name():
    with pytest.raises(ValueError, match="--entityId"):
        graph_query.hierarchy("fiction")


def test_hierarchy_rejects_rel_type_in_both_child_and_parent_of():
    with pytest.raises(ValueError, match="REPORTS_TO"):
        graph_query.hierarchy(
            "fiction", entity_id="root1", child_of="REPORTS_TO", parent_of="reports_to"
        )


@pytest.mark.parametrize("max_depth", [0, 11, -1])
def test_hierarchy_rejects_max_depth_out_of_range(max_depth):
    with pytest.raises(ValueError, match="maxDepth"):
        graph_query.hierarchy("fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=max_depth)


@pytest.mark.parametrize("limit", [0, 2001, -5])
def test_hierarchy_rejects_limit_out_of_range(limit):
    with pytest.raises(ValueError, match="limit"):
        graph_query.hierarchy("fiction", entity_id="root1", child_of="REPORTS_TO", limit=limit)


def test_hierarchy_name_fan_out_raises_with_candidates(monkeypatch):
    _patch_session(monkeypatch, [
        [_root_row("e1", "Alex Smith"), _root_row("e2", "Alex Smythe")],
    ])

    with pytest.raises(ValueError, match="Alex Smith"):
        graph_query.hierarchy("fiction", entity_name="Alex")


def test_hierarchy_discovery_mode_returns_edge_candidates(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [
            {"rel_type": "REPORTS_TO", "direction": "in", "count": 3, "neighbor_classes": ["ROLE_PERSON"]},
            {"rel_type": "MANAGES", "direction": "out", "count": 2, "neighbor_classes": ["ROLE_PERSON"]},
        ],
    ])

    result = graph_query.hierarchy("fiction", entity_id="root1")

    assert result["command"] == "hierarchy"
    assert result["root"]["_id"] == "root1"
    assert result["edge_candidates"] == [
        {"rel_type": "REPORTS_TO", "direction": "in", "count": 3, "neighbor_classes": ["ROLE_PERSON"]},
        {"rel_type": "MANAGES", "direction": "out", "count": 2, "neighbor_classes": ["ROLE_PERSON"]},
    ]
    assert "rows" not in result
    assert calls[1]["params"]["rootId"] == "root1"


def test_hierarchy_single_level_sends_frontier_and_rel_type_params(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [{"parent_id": "root1", "node": _node("c1", "Child One"), "via": "REPORTS_TO"}],
        [],
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=5, limit=1500,
    )

    level_call = calls[1]
    assert level_call["params"]["frontier"] == ["root1"]
    assert level_call["params"]["childOf"] == ["REPORTS_TO"]
    assert level_call["params"]["parentOf"] == []
    assert level_call["params"]["levelCap"] == 1501

    assert result["direction"] == "down"
    assert result["rows"] == [
        {"_id": "c1", "name": "Child One", "entity_class": "ROLE_PERSON",
         "depth": 1, "parents": [{"_id": "root1", "rel_type": "REPORTS_TO"}]}
    ]
    assert result["summary"]["total"] == 1
    assert result["summary"]["by_depth"] == {"1": 1}
    assert result["summary"]["truncated"] is False
    assert result["summary"]["back_edges"] == 0
    assert result["summary"]["multi_parent"] == 0


def test_hierarchy_direction_up_swaps_the_cypher_startnode_clause(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [],
    ])

    graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", direction="up",
    )

    level_cypher = calls[1]["cypher"]
    assert "startNode(r) = p" in level_cypher
    # The clause attached to childOf must be the one that is swapped for 'up'.
    child_clause_idx = level_cypher.index("$childOf")
    parent_clause_idx = level_cypher.index("$parentOf")
    assert "startNode(r) = p" in level_cypher[child_clause_idx:child_clause_idx + 40]
    assert "startNode(r) = c" in level_cypher[parent_clause_idx:parent_clause_idx + 40]


def test_hierarchy_direction_down_default_cypher(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [],
    ])

    graph_query.hierarchy("fiction", entity_id="root1", child_of="REPORTS_TO")

    level_cypher = calls[1]["cypher"]
    child_clause_idx = level_cypher.index("$childOf")
    parent_clause_idx = level_cypher.index("$parentOf")
    assert "startNode(r) = c" in level_cypher[child_clause_idx:child_clause_idx + 40]
    assert "startNode(r) = p" in level_cypher[parent_clause_idx:parent_clause_idx + 40]


def test_hierarchy_cycle_produces_back_edge_without_reexpansion(monkeypatch):
    """A -> B -> A: B's level-2 edge back to the already-visited root (at a
    different depth) must become a back_edge, and root must never be
    re-expanded as if it were a new frontier member."""
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [{"parent_id": "root1", "node": _node("b1", "B"), "via": "REPORTS_TO"}],
        [{"parent_id": "b1", "node": _node("root1", "Root"), "via": "REPORTS_TO"}],
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=5,
    )

    assert result["back_edges"] == [{"from": "b1", "to": "root1", "rel_type": "REPORTS_TO"}]
    assert result["summary"]["back_edges"] == 1
    assert len(calls) == 3, "root must not be re-expanded as a new frontier member"
    assert [r["_id"] for r in result["rows"]] == ["b1"]


def test_hierarchy_multi_parent_at_same_depth(monkeypatch):
    """Two parents in the frontier both reach the same child at the same
    depth: a matrix-org fan-in, not a cycle -- the child gets two parents
    recorded, not two rows."""
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [
            {"parent_id": "root1", "node": _node("a1", "A"), "via": "REPORTS_TO"},
            {"parent_id": "root1", "node": _node("b1", "B"), "via": "REPORTS_TO"},
        ],
        [
            {"parent_id": "a1", "node": _node("c1", "C"), "via": "REPORTS_TO"},
            {"parent_id": "b1", "node": _node("c1", "C"), "via": "REPORTS_TO"},
        ],
        [],
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=5,
    )

    by_id = {r["_id"]: r for r in result["rows"]}
    assert sorted(p["_id"] for p in by_id["c1"]["parents"]) == ["a1", "b1"]
    assert result["summary"]["multi_parent"] == 1
    assert result["summary"]["back_edges"] == 0
    assert len(calls) == 4, "c1 must be expanded exactly once despite two parents"


def test_hierarchy_limit_truncation(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [
            {"parent_id": "root1", "node": _node("c1", "C1"), "via": "REPORTS_TO"},
            {"parent_id": "root1", "node": _node("c2", "C2"), "via": "REPORTS_TO"},
        ],
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", limit=1,
    )

    assert calls[1]["params"]["levelCap"] == 2
    assert result["summary"]["truncated"] is True
    assert result["summary"]["truncated_reason"] == "limit"
    assert len(result["rows"]) == 1


def test_hierarchy_max_depth_probe_marks_truncated(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [{"parent_id": "root1", "node": _node("c1", "C1"), "via": "REPORTS_TO"}],
        [{"parent_id": "c1", "node": _node("c2", "C2"), "via": "REPORTS_TO"}],  # the probe
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=1,
    )

    assert result["summary"]["truncated"] is True
    assert result["summary"]["truncated_reason"] == "max_depth"
    assert result["summary"]["max_depth_reached"] == 1
    assert calls[2]["params"]["levelCap"] == 1
    assert calls[2]["params"]["frontier"] == ["c1"]


def test_hierarchy_max_depth_reached_naturally_is_not_truncated(monkeypatch):
    """The probe at max_depth finds nothing further -- the walk ended exactly
    at max_depth because there was nothing more, not because it was cut off."""
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [{"parent_id": "root1", "node": _node("c1", "C1"), "via": "REPORTS_TO"}],
        [],  # the probe finds nothing beyond c1
    ])

    result = graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO", max_depth=1,
    )

    assert result["summary"]["truncated"] is False
    assert result["summary"]["truncated_reason"] is None
    assert len(calls) == 3


def test_hierarchy_empty_result(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [],
    ])

    result = graph_query.hierarchy("fiction", entity_id="root1", child_of="REPORTS_TO")

    assert result["rows"] == []
    assert result["summary"]["total"] == 0
    assert result["summary"]["truncated"] is False
    assert len(calls) == 2, "an empty level must not trigger a max_depth probe"


def test_hierarchy_node_class_filters_the_walk(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [],
    ])

    graph_query.hierarchy(
        "fiction", entity_id="root1", child_of="REPORTS_TO",
        node_class=["ROLE_PERSON", "ORGANIZATIONAL_UNIT"],
    )

    assert calls[1]["params"]["nodeLabels"] == ["ROLE_PERSON", "ORGANIZATIONAL_UNIT"]


def test_hierarchy_rel_types_are_normalized_like_ingest(monkeypatch):
    calls = _patch_session(monkeypatch, [
        [_root_row("root1", "Root")],
        [],
    ])

    graph_query.hierarchy("fiction", entity_id="root1", child_of="reports to")

    assert calls[1]["params"]["childOf"] == ["REPORTS_TO"]
