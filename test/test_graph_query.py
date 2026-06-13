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
        {"domain": "fiction", "searchTerm": "copper-beeches (estate)?", "limit": 10},
    )
    assert "entity_name_ft" in cypher
    assert params["searchTerm"] == "copper beeches estate"


def test_pattern7_rejects_unsearchable_term():
    with pytest.raises(ValueError, match="searchable"):
        graph_query._pattern_query(
            "pattern7", {"domain": "fiction", "searchTerm": "(?)", "limit": 10}
        )


def test_validate_pattern_parameters_rejects_unknown_pattern():
    with pytest.raises(ValueError, match="Unsupported"):
        graph_query.validate_pattern_parameters("pattern99", {})


def test_pattern5_query_dispatches_shortest_and_all_modes():
    shortest_cypher, _ = graph_query._pattern_query(
        "pattern5",
        {
            "domain": "fiction",
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
            "domain": "fiction",
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
        "domain": "fiction",
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
    assert cypher_params["domain"] == "fiction"


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
        "pattern1", {"domain": "fiction", "entityClass": "PERSON"}
    )
    assert "LIMIT $limit" in cypher
    assert params["limit"] == 200


@pytest.mark.parametrize("pattern", ["pattern2", "pattern3", "pattern6"])
def test_name_match_patterns_scope_to_entity_label(pattern):
    params = {
        "domain": "fiction",
        "entityNameList": ["Holmes"],
        "entityName1": "Holmes",
        "entityName2": "Watson",
    }
    cypher, _ = graph_query._pattern_query(pattern, params)
    assert ":Entity" in cypher


@pytest.mark.parametrize("pattern", ["pattern3", "pattern4", "pattern8"])
def test_neighborhood_patterns_exclude_structural_nodes(pattern):
    params = {
        "domain": "fiction",
        "entityClass": "PERSON",
        "entityName": "Holmes",
        "entityNameList": ["Holmes"],
    }
    cypher, _ = graph_query._pattern_query(pattern, params)
    # connections must traverse entity-entity edges, not MENTIONS into chunks
    assert "(t:Entity)" in cypher


def test_pattern9_degree_modes():
    base = {"domain": "fiction", "entityClass": "PERSON", "topN": 5}

    relations_cypher, _ = graph_query._pattern_query("pattern9", base)
    assert "(e)-[r]-(:Entity)" in relations_cypher

    mentions_cypher, _ = graph_query._pattern_query(
        "pattern9", {**base, "degreeMode": "mentions"}
    )
    assert "<-[r:MENTIONS]-" in mentions_cypher

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
            "domain": "fiction",
            "entityClass": "PERSON",
            "entityName": "Holmes",
            "entityId": "ent-42",
        },
    )
    assert "e.id = $entityId" in cypher
    assert params["entityId"] == "ent-42"
    assert "entityName" not in params


def test_pattern2_accepts_entity_id_list():
    cypher, params = graph_query._pattern_query(
        "pattern2", {"domain": "fiction", "entityIdList": ["ent-1", "ent-2"]}
    )
    assert "e.id IN $entityIdList" in cypher
    assert params["entityIdList"] == ["ent-1", "ent-2"]


def test_pattern6_accepts_entity_ids():
    cypher, params = graph_query._pattern_query(
        "pattern6",
        {"domain": "fiction", "entityId1": "ent-1", "entityId2": "ent-2"},
    )
    assert "e1.id = $entityId1" in cypher
    assert "e2.id = $entityId2" in cypher


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


def test_pattern_cypher_includes_user_chat_source_match():
    cypher, _ = graph_query._pattern_query("pattern2", {
        "domain": "general",
        "entityNameList": ["Alice"],
    })
    assert "UserChat" in cypher or "user_chat" in cypher.lower()


def test_pattern10_query_uses_part_of_relationship():
    cypher, params = graph_query._pattern_query("pattern10", {
        "domain": "fiction",
        "documentName": "The Copper Beeches",
    })
    assert "PART_OF" in cypher
    assert "DocChunk" in cypher
    assert "Document" in cypher
    assert params["documentName"] == "The Copper Beeches"
    assert params["domain"] == "fiction"


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
