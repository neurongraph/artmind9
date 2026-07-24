def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    return db


def test_init_db_creates_structured_tables(tmp_path, monkeypatch):
    import sqlite3

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    conn = sqlite3.connect(db.DB_PATH)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"datasources", "tables", "columns", "column_mappings"} <= names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tables)")}
    assert {
        "id",
        "datasource",
        "table_name",
        "domain",
        "parquet_path",
        "version",
        "row_count",
        "refresh_mode",
        "business_key",
        "effective_date_column",
        "ingested_at",
        "sha256",
        "source_file",
        "sheet",
    } <= cols


def test_register_table_domain_scoping_and_version_bump(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")

    table_id = registry.register_table(
        "default",
        "products",
        "banking.retail",
        parquet_path="/tmp/structured/banking/products.parquet",
        row_count=10,
        sha256="abc123",
    )
    assert isinstance(table_id, int)

    row = registry.get_table("products", domain="banking.retail")
    assert row["version"] == 1
    assert row["row_count"] == 10

    same_id = registry.register_table(
        "default",
        "products",
        "banking.retail",
        parquet_path="/tmp/structured/banking/products.parquet",
        row_count=20,
        sha256="def456",
    )
    assert same_id == table_id
    row = registry.get_table("products", domain="banking.retail")
    assert row["version"] == 2
    assert row["row_count"] == 20
    assert row["sha256"] == "def456"

    # domain scoping: parent domain "banking" rolls up sub-domain "banking.retail"
    rows = registry.list_tables(["banking"])
    assert {r["table_name"] for r in rows} == {"products"}

    rows = registry.list_tables(["other"])
    assert rows == []

    rows = registry.list_tables()
    assert {r["table_name"] for r in rows} == {"products"}


def test_replace_and_get_columns_round_trip(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")
    table_id = registry.register_table(
        "default", "accounts", "banking", parquet_path="/tmp/accounts.parquet"
    )

    registry.replace_columns(
        table_id,
        [
            {"name": "id", "dtype": "BIGINT", "profile_json": None},
            {"name": "balance", "dtype": "DOUBLE", "profile_json": '{"kind":"numeric"}'},
        ],
    )
    cols = registry.get_columns(table_id)
    assert [c["name"] for c in cols] == ["balance", "id"]
    assert cols[0]["profile_json"] == '{"kind":"numeric"}'

    # replace_columns fully replaces the prior set
    registry.replace_columns(table_id, [{"name": "id", "dtype": "BIGINT", "profile_json": None}])
    cols = registry.get_columns(table_id)
    assert [c["name"] for c in cols] == ["id"]


def test_mapping_upsert_confirm_list_clear(tmp_path, monkeypatch):
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")
    table_id = registry.register_table(
        "default", "products", "banking", parquet_path="/tmp/products.parquet"
    )

    registry.upsert_mapping(table_id, "product_name", "PRODUCT", 0.95, confirmed=False)
    mappings = registry.list_mappings(table_id)
    assert len(mappings) == 1
    assert mappings[0]["column"] == "product_name"
    assert mappings[0]["confirmed"] == 0
    assert mappings[0]["confidence"] == 0.95

    updated = registry.set_mapping_confirmed(table_id, "product_name", "PRODUCT", True)
    assert updated == 1
    mappings = registry.list_mappings(table_id)
    assert mappings[0]["confirmed"] == 1

    # upsert again overwrites confidence/confirmed for the same (table, column, class)
    registry.upsert_mapping(table_id, "product_name", "PRODUCT", 0.5, confirmed=False)
    mappings = registry.list_mappings(table_id)
    assert len(mappings) == 1
    assert mappings[0]["confidence"] == 0.5
    assert mappings[0]["confirmed"] == 0

    cleared = registry.clear_mappings(table_id, column="product_name")
    assert cleared == 1
    assert registry.list_mappings(table_id) == []


def test_dump_all_and_restore_all_round_trip(tmp_path, monkeypatch):
    import sqlite3

    from artmind.structured import registry

    db = _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "products", "banking",
        source_file="/tmp/products.csv", sheet=None,
        parquet_path="/tmp/products.parquet", row_count=2, sha256="abc",
    )
    registry.replace_columns(table_id, [
        {"name": "id", "dtype": "BIGINT", "profile_json": None},
        {"name": "name", "dtype": "VARCHAR", "profile_json": None},
    ])
    registry.upsert_mapping(table_id, "name", "PRODUCT", 0.9, confirmed=True)

    dump = registry.dump_all()
    assert len(dump["tables"]) == 1
    assert dump["tables"][0]["id"] == table_id
    assert len(dump["columns"]) == 2
    assert len(dump["column_mappings"]) == 1

    # simulate total loss
    registry.delete_table(table_id)
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("DELETE FROM datasources")
    conn.commit()
    conn.close()
    assert registry.list_tables() == []

    registry.restore_all(dump)

    restored = registry.get_table("products", domain="banking")
    assert restored is not None
    assert restored["id"] == table_id  # id preserved so columns/mappings FKs still resolve
    assert restored["row_count"] == 2
    assert registry.get_columns(table_id) == dump["columns"]
    mappings = registry.list_mappings(table_id)
    assert len(mappings) == 1
    assert mappings[0]["entity_class"] == "PRODUCT"
    assert mappings[0]["confirmed"] == 1


def test_restore_all_wipes_before_reinserting(tmp_path, monkeypatch):
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    stale_id = registry.register_table(
        "default", "stale_table", "general",
        parquet_path="/tmp/stale.parquet", row_count=1,
    )

    registry.restore_all({"datasources": [], "tables": [], "columns": [], "column_mappings": []})

    assert registry.get_table("stale_table", domain="general") is None
