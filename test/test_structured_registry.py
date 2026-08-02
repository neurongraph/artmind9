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


def test_list_tables_matches_ancestor_domains(tmp_path, monkeypatch):
    """Bug 2: documents live at genre domains (banking.cases) while tables live
    at the corpus root (banking), because a table has no genre. Matching only
    downwards made `db list --domain banking.cases` return [] and an agent
    conclude no structured store existed, with six populated tables sitting at
    banking."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    registry.register_table("default", "customers", "banking", parquet_path="/tmp/c.parquet")
    registry.register_table("default", "loans", "banking.retail", parquet_path="/tmp/l.parquet")

    # Ancestor: a leaf domain reaches the root's tables.
    assert [t["table_name"] for t in registry.list_tables(["banking.cases"])] == ["customers"]
    # Descendant: unchanged behaviour.
    assert sorted(t["table_name"] for t in registry.list_tables(["banking"])) == [
        "customers", "loans",
    ]
    # Unrelated domain matches neither.
    assert registry.list_tables(["fiction"]) == []
    # The opt-out restores descendant-only scope for project_catalogue.
    assert registry.list_tables(["banking.cases"], include_ancestors=False) == []


def test_routing_surface_shape_and_class_filter(tmp_path, monkeypatch):
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    vc_id = registry.register_table(
        "default", "vulnerable_customers", "banking", parquet_path="/tmp/v.parquet", row_count=7
    )
    other_id = registry.register_table(
        "default", "products", "banking", parquet_path="/tmp/p.parquet"
    )
    registry.upsert_mapping(vc_id, "reviewed_by", "ROLE_PERSON", 1.0, confirmed=True)
    registry.upsert_mapping(vc_id, "customer_id", "CUSTOMER", 0.6)
    registry.upsert_mapping(other_id, "name", "PRODUCT", 0.9)
    registry.upsert_column_role(vc_id, "vulnerability_driver", "term", 0.9)

    surface = registry.routing_surface(["banking.cases"])  # ancestor match
    by_table = {t["table"]: t for t in surface}
    vc = by_table["vulnerable_customers"]
    assert vc["grain"] == "instance"
    assert [c["entity_class"] for c in vc["entity_classes"]] == ["CUSTOMER", "ROLE_PERSON"]
    assert [c["confirmed"] for c in vc["entity_classes"]] == [False, True]
    assert vc["bridge_columns"] == [
        {"column": "vulnerability_driver", "bridge_role": "term", "confirmed": False}
    ]

    # Class-first discovery: many-to-many, which a single dotted domain can't express.
    filtered = registry.routing_surface(None, entity_class="ROLE_PERSON")
    assert [t["table"] for t in filtered] == ["vulnerable_customers"]


def test_init_db_adds_grain_to_pre_grain_schema(tmp_path, monkeypatch):
    """A registry seeded before `grain` existed must gain the column without
    losing rows. The seed here also has the old UNIQUE key, so this exercises
    the ordering that matters: the rename/recreate block rebuilds `tables` from
    the pre-grain definition, and the ADD COLUMN migration has to run after it."""
    import sqlite3

    import artmind.db as db

    db_path = tmp_path / "reg.db"
    _seed_pre_domain_unique_schema(db_path)
    monkeypatch.setattr(db, "DB_PATH", db_path)

    db._init_db()

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute('PRAGMA table_info("tables")')}
    assert "grain" in cols
    row = conn.execute('SELECT grain, table_name, sha256 FROM "tables" WHERE id = 1').fetchone()
    conn.close()
    assert row == ("instance", "products", "pre-migration-sha")


def test_register_table_grain_defaults_and_validates(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")

    registry.register_table("default", "customers", "banking", parquet_path="/tmp/c.parquet")
    assert registry.get_table("customers", domain="banking")["grain"] == "instance"

    registry.register_table(
        "default", "rate_card", "banking", parquet_path="/tmp/r.parquet", grain="normative"
    )
    assert registry.get_table("rate_card", domain="banking")["grain"] == "normative"

    with pytest.raises(ValueError, match="grain must be one of"):
        registry.register_table(
            "default", "bogus", "banking", parquet_path="/tmp/b.parquet", grain="nonsense"
        )


def test_register_table_update_preserves_grain(tmp_path, monkeypatch):
    """Re-ingesting a table must not silently reset an operator's confirmed
    grain back to the 'instance' default — the same durability guarantee
    column_mappings confirmations have."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    # Temporal because confirming 'normative' requires it — normative rows get
    # superseded, so they need SCD-2 history.
    registry.register_table(
        "default", "rate_card", "banking", parquet_path="/tmp/r.parquet",
        refresh_mode="temporal", business_key="product,tier",
    )
    registry.set_grain(
        registry.get_table("rate_card", domain="banking")["id"], "normative"
    )

    # Re-ingest: same datasource/domain/table_name, so this takes the UPDATE path.
    registry.register_table(
        "default", "rate_card", "banking", parquet_path="/tmp/r2.parquet", row_count=99,
        refresh_mode="temporal", business_key="product,tier",
    )

    row = registry.get_table("rate_card", domain="banking")
    assert row["grain"] == "normative"
    assert row["grain_confirmed"] == 1
    assert row["row_count"] == 99
    assert row["version"] == 2


def test_set_grain_validates(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )
    with pytest.raises(ValueError, match="grain must be one of"):
        registry.set_grain(table_id, "nonsense")
    assert registry.get_table("customers", domain="banking")["grain"] == "instance"


def test_column_roles_survive_replace_columns(tmp_path, monkeypatch):
    """The reason bridge_role lives in its own table rather than on `columns`:
    replace_columns() DELETEs and re-INSERTs every row on each ingest/refresh,
    so a role stored there would be wiped whenever the table is refreshed."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "vulnerable_customers", "banking", parquet_path="/tmp/v.parquet"
    )
    registry.replace_columns(table_id, [{"name": "vulnerability_driver", "dtype": "VARCHAR"}])
    registry.upsert_column_role(table_id, "vulnerability_driver", "term", 0.9, confirmed=True)

    # Simulate a refresh re-profiling the same table.
    registry.replace_columns(table_id, [{"name": "vulnerability_driver", "dtype": "VARCHAR"}])

    roles = registry.list_column_roles(table_id)
    assert len(roles) == 1
    assert roles[0]["column"] == "vulnerability_driver"
    assert roles[0]["bridge_role"] == "term"
    assert roles[0]["confirmed"] == 1


def test_column_role_confirm_filter_and_clear(tmp_path, monkeypatch):
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "vulnerable_customers", "banking", parquet_path="/tmp/v.parquet"
    )
    registry.upsert_column_role(table_id, "vulnerability_driver", "term", 0.9)
    registry.upsert_column_role(table_id, "support_needed", "term", 0.8)

    assert len(registry.list_column_roles(table_id)) == 2
    assert registry.list_column_roles(table_id, confirmed_only=True) == []

    registry.set_column_role_confirmed(table_id, "vulnerability_driver", True)
    confirmed = registry.list_column_roles(table_id, confirmed_only=True)
    assert [r["column"] for r in confirmed] == ["vulnerability_driver"]

    registry.clear_column_roles(table_id, "support_needed")
    assert [r["column"] for r in registry.list_column_roles(table_id)] == ["vulnerability_driver"]


def test_dump_restore_round_trips_grain_and_column_roles(tmp_path, monkeypatch):
    """restore_all rebuilds `tables` from an explicit column list, so a new
    column has to be added there too or a snapshot restore silently resets it."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "rate_card", "banking", parquet_path="/tmp/r.parquet", grain="normative"
    )
    registry.upsert_column_role(table_id, "tier", "term", 0.7, confirmed=True)

    dump = registry.dump_all()
    assert dump["tables"][0]["grain"] == "normative"
    assert dump["column_roles"][0]["bridge_role"] == "term"

    registry.restore_all(dump)

    restored = registry.get_table("rate_card", domain="banking")
    assert restored["grain"] == "normative"
    roles = registry.list_column_roles(restored["id"])
    assert [(r["column"], r["bridge_role"], r["confirmed"]) for r in roles] == [("tier", "term", 1)]


def test_restore_all_tolerates_pre_grain_dump(tmp_path, monkeypatch):
    """Snapshots taken before this change carry neither grain nor column_roles."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.restore_all({
        "datasources": [
            {"name": "default", "type": "duckdb", "path_or_dsn": "/tmp/x.duckdb",
             "created_at": "2026-01-01T00:00:00"},
        ],
        "tables": [
            {"id": 1, "datasource": "default", "table_name": "products", "domain": "banking",
             "source_file": None, "sheet": None, "parquet_path": "/tmp/p.parquet", "version": 1,
             "row_count": 2, "refresh_mode": "replace", "business_key": None,
             "effective_date_column": None, "ingested_at": "2026-01-01T00:00:00", "sha256": "x"},
        ],
        "columns": [],
        "column_mappings": [],
    })

    assert registry.get_table("products", domain="banking")["grain"] == "instance"


def test_init_db_adds_step_status_columns_to_pre_existing_schema(tmp_path, monkeypatch):
    """A registry seeded before this design shipped must gain the three status
    columns, defaulted to 'pending', without disturbing existing rows."""
    import sqlite3

    import artmind.db as db

    db_path = tmp_path / "reg.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Simulate a pre-existing DB that already has grain/grain_confirmed but not
    # the new status columns, by creating the schema then dropping them.
    db._init_db()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO datasources (name, type, path_or_dsn, created_at)"
        " VALUES ('default', 'duckdb', '/tmp/x', 'now')"
    )
    conn.execute(
        'INSERT INTO "tables" (datasource, table_name, domain, parquet_path,'
        " version, refresh_mode, grain, ingested_at)"
        " VALUES ('default', 'legacy_table', 'banking', '/tmp/l.parquet', 1,"
        " 'replace', 'instance', 'now')"
    )
    conn.commit()
    conn.close()

    # Re-run _init_db (idempotent) -- this is the real-world path: an existing
    # DB file, code upgraded, next command run re-triggers _init_db via _get_db.
    db._init_db()

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute('PRAGMA table_info("tables")')}
    assert {"grain_status", "bridge_status", "mapping_status"} <= cols
    row = conn.execute(
        'SELECT grain_status, bridge_status, mapping_status, table_name'
        ' FROM "tables" WHERE table_name = ?', ("legacy_table",)
    ).fetchone()
    conn.close()
    assert row == ("pending", "pending", "pending", "legacy_table")


def test_register_table_defaults_step_statuses_to_pending(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()

    registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )
    row = registry.get_table("customers", domain="banking")
    assert row["grain_status"] == "pending"
    assert row["bridge_status"] == "pending"
    assert row["mapping_status"] == "pending"


def test_set_step_status_validates_and_persists(tmp_path, monkeypatch):
    import pytest

    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    table_id = registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )

    registry.set_step_status(table_id, "mapping", "ok")
    assert registry.get_table_by_id(table_id)["mapping_status"] == "ok"

    with pytest.raises(ValueError, match="step must be one of"):
        registry.set_step_status(table_id, "not_a_step", "ok")
    with pytest.raises(ValueError, match="status must be one of"):
        registry.set_step_status(table_id, "grain", "not_a_status")


def test_routing_surface_includes_step_statuses(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    table_id = registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )
    registry.set_step_status(table_id, "grain", "ok")

    surface = registry.routing_surface(["banking"])
    assert surface[0]["grain_status"] == "ok"
    assert surface[0]["bridge_status"] == "pending"
    assert surface[0]["mapping_status"] == "pending"
