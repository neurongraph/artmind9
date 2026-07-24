import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    db._init_db()


def _fake_entity_listing(domains, name_filter=None, count_all=False, as_of=None):
    return {
        "domain": domains,
        "query_type": "graph",
        "command": "entity_listing",
        "rows": [
            {
                "label": "PRODUCT",
                "typeGroups": [
                    {"type": "PRODUCT", "names": ["SmartSaver", "SmartSaver Plus"]}
                ],
            }
        ],
    }


def _empty_entity_listing(domains, name_filter=None, count_all=False, as_of=None):
    return {"rows": []}


def _categorical_column(name, sample):
    return {
        "name": name,
        "dtype": "VARCHAR",
        "profile_json": json.dumps(
            {
                "kind": "categorical",
                "distinct_sample": sample,
                "cardinality": len(sample),
                "minimum": None,
                "maximum": None,
                "null_rate": 0.0,
            }
        ),
    }


def _numeric_column(name, minimum, maximum):
    return {
        "name": name,
        "dtype": "DOUBLE",
        "profile_json": json.dumps(
            {
                "kind": "numeric",
                "distinct_sample": [],
                "cardinality": None,
                "minimum": minimum,
                "maximum": maximum,
                "null_rate": 0.0,
            }
        ),
    }


def _register_table(tmp_path, columns, table_name="products"):
    from artmind.structured import registry

    table_id = registry.register_table(
        "default",
        table_name,
        "banking",
        parquet_path=str(tmp_path / f"{table_name}.parquet"),
        row_count=2,
    )
    registry.replace_columns(table_id, columns)
    return table_id


def test_resolve_key_matches_column_and_graph_agree(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _fake_entity_listing)
    _register_table(
        tmp_path, [_categorical_column("product_name", ["SmartSaver", "SmartSaver Plus"])]
    )

    result = resolve_key.resolve_key(
        "smartsaver", ["banking"], column="product_name", table="products"
    )

    assert result["phrase"] == "smartsaver"
    assert result["canonical"] == "SmartSaver"
    assert result["entity_class"] == "PRODUCT"
    assert result["source"] == "both"
    assert result["column"] == "product_name"
    assert result["candidates"]
    assert result["candidates"][0]["score"] == pytest.approx(1.0)


def test_resolve_key_graph_only_when_no_column_given(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _fake_entity_listing)

    result = resolve_key.resolve_key("smartsaver", ["banking"])

    assert result["canonical"] == "SmartSaver"
    assert result["entity_class"] == "PRODUCT"
    assert result["source"] == "graph"
    assert result["column"] is None


def test_resolve_key_unknown_phrase_returns_empty_candidates(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _fake_entity_listing)
    _register_table(
        tmp_path, [_categorical_column("product_name", ["SmartSaver", "SmartSaver Plus"])]
    )

    result = resolve_key.resolve_key(
        "totally unrelated phrase", ["banking"], column="product_name", table="products"
    )

    assert result["candidates"] == []
    assert result["canonical"] is None
    assert result["source"] is None
    assert result["entity_class"] is None


def test_resolve_key_no_graph_matches_falls_back_to_column(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _empty_entity_listing)
    _register_table(tmp_path, [_categorical_column("product_name", ["SmartSaver"])])

    result = resolve_key.resolve_key(
        "smartsaver", ["banking"], column="product_name", table="products"
    )

    assert result["canonical"] == "SmartSaver"
    assert result["source"] == "column"
    assert result["entity_class"] is None


def test_resolve_key_fuzzy_match(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _fake_entity_listing)

    # "SmartSavr" is one edit away from "SmartSaver" — ratio comfortably
    # above MATCH_THRESHOLD but not an exact case-folded match.
    result = resolve_key.resolve_key("SmartSavr", ["banking"])

    assert result["canonical"] == "SmartSaver"
    assert result["entity_class"] == "PRODUCT"
    assert result["source"] == "graph"
    assert 0 < result["candidates"][0]["score"] < 1.0


def test_resolve_key_numeric_column_yields_no_column_matches(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _empty_entity_listing)
    _register_table(tmp_path, [_numeric_column("balance", 10.0, 500.0)])

    result = resolve_key.resolve_key("smartsaver", ["banking"], column="balance", table="products")

    assert result["candidates"] == []
    assert result["canonical"] is None


def test_resolve_key_column_without_table_scans_tables(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.resolve_key as resolve_key

    monkeypatch.setattr(resolve_key, "entity_listing", _empty_entity_listing)
    _register_table(
        tmp_path, [_categorical_column("product_name", ["SmartSaver"])], table_name="products"
    )

    result = resolve_key.resolve_key("smartsaver", ["banking"], column="product_name")

    assert result["canonical"] == "SmartSaver"
    assert result["source"] == "column"


def test_query_resolve_key_cli_dispatches_and_outputs_json():
    payload = {
        "phrase": "smartsaver",
        "canonical": "SmartSaver",
        "source": "both",
        "column": "product_name",
        "entity_class": "PRODUCT",
        "candidates": [{"value": "SmartSaver", "entity_class": "PRODUCT", "score": 1.0, "source": "graph"}],
    }
    runner = CliRunner()
    with patch("artmind.cli.resolve_key.resolve_key", return_value=payload) as mocked:
        result = runner.invoke(
            cli,
            [
                "query",
                "resolve-key",
                "--domain",
                "banking",
                "--column",
                "product_name",
                "--table",
                "products",
                "smartsaver",
            ],
        )

    assert result.exit_code == 0, result.output
    mocked.assert_called_once_with(
        "smartsaver", ["banking"], column="product_name", table="products", top_k=5
    )
    assert json.loads(result.output) == payload


def test_query_resolve_key_cli_graph_only_without_column():
    payload = {
        "phrase": "smartsaver",
        "canonical": "SmartSaver",
        "source": "graph",
        "column": None,
        "entity_class": "PRODUCT",
        "candidates": [],
    }
    runner = CliRunner()
    with patch("artmind.cli.resolve_key.resolve_key", return_value=payload) as mocked:
        result = runner.invoke(
            cli, ["query", "resolve-key", "--domain", "banking", "--compact", "smartsaver"]
        )

    assert result.exit_code == 0, result.output
    mocked.assert_called_once_with("smartsaver", ["banking"], column=None, table=None, top_k=5)
    assert json.loads(result.output) == payload
