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


def _register_table(tmp_path, columns, table_name="accounts"):
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


def _register_table_with_columns(tmp_path):
    return _register_table(
        tmp_path,
        [
            _categorical_column("product_name", ["SmartSaver", "SmartSaver Plus"]),
            _numeric_column("balance", 10.0, 500.0),
        ],
    )


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


def test_propose_mappings_skips_entity_listing_when_no_categorical_columns(tmp_path, monkeypatch):
    """Important #1 regression: a purely-numeric table must never pay for the
    entity_listing graph round trip."""
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    def _fail_if_called(domains, name_filter=None, count_all=False, as_of=None):
        pytest.fail("entity_listing should not be called for a table with no categorical columns")

    monkeypatch.setattr(mappings, "entity_listing", _fail_if_called)

    table_id = _register_table(
        tmp_path,
        [_numeric_column("balance", 10.0, 500.0)],
        table_name="numeric_only",
    )

    proposals = mappings.propose_mappings(table_id, ["banking"])
    assert proposals == []
    assert registry.list_mappings(table_id) == []


def test_propose_mappings_fuzzy_match(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    monkeypatch.setattr(mappings, "entity_listing", _fake_entity_listing)

    # "SmartSavr" is one character away from the KG entity "SmartSaver" —
    # ratio ~0.947, comfortably above the default 0.82 fuzzy_threshold, but
    # not an exact case-folded match.
    table_id = _register_table(tmp_path, [_categorical_column("product_name", ["SmartSavr"])])

    proposals = mappings.propose_mappings(table_id, ["banking"])

    assert len(proposals) == 1
    assert proposals[0]["column"] == "product_name"
    assert proposals[0]["entity_class"] == "PRODUCT"
    assert proposals[0]["confidence"] == pytest.approx(1.0)

    persisted = registry.list_mappings(table_id)
    assert len(persisted) == 1
    assert persisted[0]["entity_class"] == "PRODUCT"


def test_propose_mappings_partial_confidence(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    monkeypatch.setattr(mappings, "entity_listing", _fake_entity_listing)

    # Only "SmartSaver" matches a KG entity; "Unrelated Thing" matches nothing.
    table_id = _register_table(
        tmp_path, [_categorical_column("product_name", ["SmartSaver", "Unrelated Thing"])]
    )

    proposals = mappings.propose_mappings(table_id, ["banking"])

    assert len(proposals) == 1
    assert proposals[0]["confidence"] == pytest.approx(0.5)

    persisted = registry.list_mappings(table_id)
    assert persisted[0]["confidence"] == pytest.approx(0.5)


def test_propose_mappings_multiple_categorical_columns(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.structured.mappings as mappings
    from artmind.structured import registry

    def _two_class_listing(domains, name_filter=None, count_all=False, as_of=None):
        return {
            "rows": [
                {
                    "label": "PRODUCT",
                    "typeGroups": [
                        {"type": "PRODUCT", "names": ["SmartSaver", "SmartSaver Plus"]}
                    ],
                },
                {
                    "label": "BRANCH",
                    "typeGroups": [{"type": "BRANCH", "names": ["Downtown", "Uptown"]}],
                },
            ]
        }

    monkeypatch.setattr(mappings, "entity_listing", _two_class_listing)

    table_id = _register_table(
        tmp_path,
        [
            _categorical_column("product_name", ["SmartSaver", "SmartSaver Plus"]),
            _categorical_column("branch_name", ["Downtown", "Uptown"]),
            _numeric_column("balance", 10.0, 500.0),
        ],
    )

    proposals = mappings.propose_mappings(table_id, ["banking"])

    by_column = {p["column"]: p for p in proposals}
    assert set(by_column) == {"product_name", "branch_name"}
    assert by_column["product_name"]["entity_class"] == "PRODUCT"
    assert by_column["product_name"]["confidence"] == pytest.approx(1.0)
    assert by_column["branch_name"]["entity_class"] == "BRANCH"
    assert by_column["branch_name"]["confidence"] == pytest.approx(1.0)

    persisted_columns = {p["column"] for p in registry.list_mappings(table_id)}
    assert persisted_columns == {"product_name", "branch_name"}
