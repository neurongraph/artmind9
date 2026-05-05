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

    assert "MATCH" in cypher
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


def test_pattern_cypher_includes_user_chat_source_match():
    cypher, _ = graph_query._pattern_query("pattern2", {
        "domain": "general",
        "entityNameList": ["Alice"],
    })
    assert "UserChat" in cypher or "user_chat" in cypher.lower()
