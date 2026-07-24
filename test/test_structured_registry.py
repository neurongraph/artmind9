def _patch_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    return db


def _seed_pre_domain_unique_schema(db_path):
    """Seed a temp SQLite file with the OLD `tables` schema (pre-58b7d62):
    `domain` column already present, but the UNIQUE key is still
    (datasource, table_name) without `domain` -- the exact shape of any real
    install (Phases 1-4, already on origin/master) that ran `artmind init` /
    ingested structured data before this branch added the migration."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE datasources (
            name        TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            path_or_dsn TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE tables (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            datasource             TEXT NOT NULL REFERENCES datasources(name),
            table_name             TEXT NOT NULL,
            domain                 TEXT NOT NULL,
            source_file            TEXT,
            sheet                  TEXT,
            parquet_path           TEXT NOT NULL,
            version                INTEGER NOT NULL DEFAULT 1,
            row_count              INTEGER,
            refresh_mode           TEXT NOT NULL DEFAULT 'replace',
            business_key           TEXT,
            effective_date_column  TEXT,
            ingested_at            TEXT NOT NULL,
            sha256                 TEXT,
            UNIQUE(datasource, table_name)
        )
    """)
    conn.execute("""
        CREATE TABLE columns (
            table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            name         TEXT NOT NULL,
            dtype        TEXT NOT NULL,
            profile_json TEXT,
            PRIMARY KEY (table_id, name)
        )
    """)
    conn.execute("""
        CREATE TABLE column_mappings (
            table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
            column       TEXT NOT NULL,
            entity_class TEXT NOT NULL,
            confirmed    INTEGER NOT NULL DEFAULT 0,
            confidence   REAL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (table_id, column, entity_class)
        )
    """)
    conn.execute(
        "INSERT INTO datasources (name, type, path_or_dsn, created_at)"
        " VALUES ('default', 'duckdb', '/tmp/artmind.duckdb', '2026-01-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO \"tables\" (id, datasource, table_name, domain, source_file, sheet,"
        " parquet_path, version, row_count, refresh_mode, business_key,"
        " effective_date_column, ingested_at, sha256)"
        " VALUES (1, 'default', 'products', 'banking', NULL, NULL,"
        " '/tmp/structured/banking/products.parquet', 1, 10, 'replace', NULL,"
        " NULL, '2026-01-01T00:00:00', 'pre-migration-sha')"
    )
    conn.commit()
    conn.close()


def test_init_db_migrates_pre_domain_unique_tables_schema(tmp_path, monkeypatch):
    """Bug 1 regression: commit 58b7d62 changed `tables`'s UNIQUE key from
    (datasource, table_name) to (datasource, domain, table_name), but
    `CREATE TABLE IF NOT EXISTS` is a no-op on any registry DB that already
    has a `tables` table -- i.e. every real install that predates this
    branch. Without an explicit migration, register_table's per-domain
    existence check finds no conflict but the INSERT still hits the old
    2-column UNIQUE index and raises sqlite3.IntegrityError. `_init_db()`
    must detect and migrate the old constraint, preserving existing rows."""
    import sqlite3

    import artmind.db as db

    db_path = tmp_path / "reg.db"
    _seed_pre_domain_unique_schema(db_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Fresh CREATE TABLE IF NOT EXISTS is a no-op here; the migration path
    # inside _init_db() is what must actually fix the constraint.
    db._init_db()

    conn = sqlite3.connect(db_path)
    tables_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tables'"
    ).fetchone()[0]
    assert "UNIQUE(datasource, domain, table_name)" in tables_sql
    assert "UNIQUE(datasource, table_name)" not in tables_sql

    # Pre-existing row survived the migration with its data intact.
    row = conn.execute(
        "SELECT id, table_name, domain, row_count, sha256, parquet_path"
        " FROM \"tables\" WHERE id = 1"
    ).fetchone()
    assert row == (1, "products", "banking", 10, "pre-migration-sha", "/tmp/structured/banking/products.parquet")
    conn.close()

    # The scenario the fix targets: a second domain registering the same
    # table_name must now succeed instead of raising IntegrityError.
    from artmind.structured import registry

    retail_id = registry.register_table(
        "default", "products", "retail", parquet_path="/tmp/structured/retail/products.parquet",
    )
    banking_row = registry.get_table("products", domain="banking")
    retail_row = registry.get_table("products", domain="retail")
    assert banking_row["id"] == 1
    assert banking_row["sha256"] == "pre-migration-sha"
    assert retail_row["id"] == retail_id
    assert retail_id != 1


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


def test_register_table_same_name_different_domains_creates_separate_rows(tmp_path, monkeypatch):
    """Two domains registering a table with the same name must not collide:
    each gets its own row, and registering the second must not silently
    reassign/overwrite the first's domain or data (the bug this guards
    against: a UNIQUE(datasource, table_name) key without domain let a later
    same-named ingest in another domain clobber an earlier domain's row)."""
    _patch_db(tmp_path, monkeypatch)
    from artmind.structured import registry

    registry.register_datasource("default", "duckdb", "/tmp/artmind.duckdb")

    banking_id = registry.register_table(
        "default",
        "products",
        "banking",
        parquet_path="/tmp/structured/banking/products.parquet",
        row_count=10,
        sha256="banking-sha",
    )
    retail_id = registry.register_table(
        "default",
        "products",
        "retail",
        parquet_path="/tmp/structured/retail/products.parquet",
        row_count=99,
        sha256="retail-sha",
    )

    assert banking_id != retail_id

    banking_row = registry.get_table("products", domain="banking")
    retail_row = registry.get_table("products", domain="retail")
    assert banking_row["id"] == banking_id
    assert banking_row["row_count"] == 10
    assert banking_row["sha256"] == "banking-sha"
    assert banking_row["parquet_path"] == "/tmp/structured/banking/products.parquet"
    assert retail_row["id"] == retail_id
    assert retail_row["row_count"] == 99
    assert retail_row["sha256"] == "retail-sha"
    assert retail_row["parquet_path"] == "/tmp/structured/retail/products.parquet"

    all_rows = registry.list_tables()
    assert {r["id"] for r in all_rows if r["table_name"] == "products"} == {banking_id, retail_id}

    # re-registering under "banking" again must version-bump the banking row
    # only, still leaving retail's row untouched.
    same_id = registry.register_table(
        "default",
        "products",
        "banking",
        parquet_path="/tmp/structured/banking/products.parquet",
        row_count=11,
        sha256="banking-sha-2",
    )
    assert same_id == banking_id
    assert registry.get_table("products", domain="banking")["version"] == 2
    assert registry.get_table("products", domain="retail")["row_count"] == 99


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
