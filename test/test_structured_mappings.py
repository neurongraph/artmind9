import json

import pytest


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


def _register_table_with_columns(tmp_path):
    from artmind.structured import registry

    table_id = registry.register_table(
        "default",
        "accounts",
        "banking",
        parquet_path=str(tmp_path / "accounts.parquet"),
        row_count=2,
    )
    columns = [
        {
            "name": "product_name",
            "dtype": "VARCHAR",
            "profile_json": json.dumps(
                {
                    "kind": "categorical",
                    "distinct_sample": ["SmartSaver", "SmartSaver Plus"],
                    "cardinality": 2,
                    "minimum": None,
                    "maximum": None,
                    "null_rate": 0.0,
                }
            ),
        },
        {
            "name": "balance",
            "dtype": "DOUBLE",
            "profile_json": json.dumps(
                {
                    "kind": "numeric",
                    "distinct_sample": [],
                    "cardinality": None,
                    "minimum": 10.0,
                    "maximum": 500.0,
                    "null_rate": 0.0,
                }
            ),
        },
    ]
    registry.replace_columns(table_id, columns)
    return table_id


def test_propose_mappings_matches_categorical_column(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    monkeypatch.setattr(mappings, "entity_listing", _fake_entity_listing)

    table_id = _register_table_with_columns(tmp_path)

    proposals = mappings.propose_mappings(table_id, ["banking"])

    assert len(proposals) == 1
    assert proposals[0]["column"] == "product_name"
    assert proposals[0]["entity_class"] == "PRODUCT"
    assert proposals[0]["confidence"] == pytest.approx(1.0)

    persisted = registry.list_mappings(table_id)
    assert len(persisted) == 1
    assert persisted[0]["column"] == "product_name"
    assert persisted[0]["entity_class"] == "PRODUCT"
    assert persisted[0]["confirmed"] == 0
    assert persisted[0]["confidence"] == pytest.approx(1.0)


def test_propose_mappings_numeric_column_no_proposal(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    monkeypatch.setattr(mappings, "entity_listing", _fake_entity_listing)

    table_id = _register_table_with_columns(tmp_path)

    proposals = mappings.propose_mappings(table_id, ["banking"])
    columns_proposed = {p["column"] for p in proposals}
    assert "balance" not in columns_proposed

    persisted = registry.list_mappings(table_id)
    assert all(p["column"] != "balance" for p in persisted)


def test_propose_mappings_below_floor_unmapped(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    def _no_match_listing(domains, name_filter=None, count_all=False, as_of=None):
        return {
            "rows": [
                {
                    "label": "PRODUCT",
                    "typeGroups": [{"type": "PRODUCT", "names": ["Zorblatt", "Quexil"]}],
                }
            ]
        }

    monkeypatch.setattr(mappings, "entity_listing", _no_match_listing)

    table_id = _register_table_with_columns(tmp_path)

    proposals = mappings.propose_mappings(table_id, ["banking"])
    assert proposals == []
    assert registry.list_mappings(table_id) == []
