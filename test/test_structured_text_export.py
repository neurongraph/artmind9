import csv

import pytest

pytest.importorskip("duckdb")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", tmp_path / "structured_text")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_export_then_restore_text_round_trips_replace_mode_table(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name", "price"], [1, "Widget", 9.99], [2, "Gadget", ""]])
    ingest_structured_file(csv_path, "banking")

    # Pipeline auto-exports on ingest -- confirm the text is already there.
    manifest_path = paths.STRUCTURED_TEXT_DIR / "manifest.json"
    assert manifest_path.exists()
    table_csv = paths.STRUCTURED_TEXT_DIR / "banking" / "products.csv"
    assert table_csv.exists()

    # Re-export explicitly too, exercising the CLI-equivalent path directly.
    result = export_structured_text()
    assert result["tables"] == 1

    # Simulate a fresh clone: the registry db and the parquet/duckdb cache are
    # both gone, only the text export directory survives.
    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db

    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()
    assert registry.get_table("products", domain="banking") is None

    summary = import_structured_text()
    assert summary["tables_loaded"] == 1
    assert summary["skipped_no_csv"] == []

    restored = registry.get_table("products", domain="banking")
    assert restored is not None
    assert restored["row_count"] == 2
    # The parquet_path recorded is the CURRENT machine's, not whatever the
    # manifest happened to carry from export time.
    from artmind.structured.duckdb_adapter import parquet_path_for

    assert restored["parquet_path"] == str(parquet_path_for("banking", "products"))

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    rows = ds.run_sql("SELECT * FROM products ORDER BY id")
    assert rows == [
        {"id": 1, "name": "Widget", "price": 9.99},
        {"id": 2, "name": "Gadget", "price": None},
    ]


def test_null_and_empty_string_survive_the_round_trip(tmp_path, monkeypatch):
    """A real empty string and a real NULL in the same column must come back
    as what they were -- not both collapsed to one or the other.

    Built directly from a DuckDB table + hand-registered registry rows,
    bypassing `ingest_structured_file`'s `read_csv_auto`: that path already
    collapses a quoted `""` to NULL at the *original* ingest (a pre-existing
    property of DuckDB's CSV auto-sniffing, unrelated to this module), so
    routing this scenario through it would test that quirk instead of what
    `export_structured_text`/`import_structured_text` themselves guarantee.
    """
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource, parquet_path_for
    from artmind.structured.text_export import export_structured_text, import_structured_text

    ds = DuckDBDatasource()
    ds.con.execute(
        "CREATE TABLE t AS SELECT * FROM (VALUES (1, ''), (2, NULL)) AS v(id, note)"
    )
    parquet_path = parquet_path_for("general", "notes")
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    ds.con.execute(f"COPY t TO '{parquet_path}' (FORMAT PARQUET)")

    registry.register_datasource("default", "duckdb", "test")
    table_id = registry.register_table(
        "default", "notes", "general", parquet_path=str(parquet_path), row_count=2,
    )
    registry.replace_columns(table_id, [
        {"name": "id", "dtype": "BIGINT", "profile_json": None},
        {"name": "note", "dtype": "VARCHAR", "profile_json": None},
    ])

    export_structured_text()

    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db

    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()

    import_structured_text()

    ds2 = DuckDBDatasource()
    ds2.ensure_views(registry.list_tables())
    rows = ds2.run_sql("SELECT * FROM notes ORDER BY id")
    assert rows[0]["note"] == ""
    assert rows[1]["note"] is None


def test_restore_text_preserves_grain_and_bridge_confirmations(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    csv_path = tmp_path / "complaints.csv"
    _write_csv(csv_path, [["id", "category"], [1, "Fee Dispute"], [2, "Fraud"]])
    ingest_structured_file(csv_path, "banking")

    table = registry.get_table("complaints", domain="banking")
    registry.set_grain(table["id"], "lookup", confirmed=True)
    registry.upsert_column_role(table["id"], "category", "term", 0.9, confirmed=True)

    export_structured_text()

    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db

    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()

    import_structured_text()

    restored = registry.get_table("complaints", domain="banking")
    assert restored["grain"] == "lookup"
    assert bool(restored["grain_confirmed"]) is True

    roles = registry.list_column_roles(restored["id"])
    assert roles[0]["bridge_role"] == "term"
    assert bool(roles[0]["confirmed"]) is True


def test_temporal_table_history_survives_the_round_trip(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file, refresh_table
    from artmind.structured.text_export import export_structured_text, import_structured_text

    v1 = tmp_path / "accounts_v1.csv"
    _write_csv(v1, [["acct_id", "status"], [1, "open"], [2, "open"]])
    ingest_structured_file(v1, "banking", table="accounts", refresh_mode="temporal", business_key="acct_id")

    v2 = tmp_path / "accounts_v2.csv"
    _write_csv(v2, [["acct_id", "status"], [1, "closed"], [2, "open"], [3, "open"]])
    ingest_structured_file(v2, "banking", table="accounts", refresh_mode="temporal", business_key="acct_id")

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    before = ds.run_sql("SELECT count(*) AS n FROM accounts")[0]["n"]
    assert before == 4  # acct 1 has two versions now (open -> closed)

    export_structured_text()

    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db

    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()

    import_structured_text()

    restored = registry.get_table("accounts", domain="banking")
    assert restored["refresh_mode"] == "temporal"

    ds2 = DuckDBDatasource()
    ds2.ensure_views(registry.list_tables())
    after = ds2.run_sql("SELECT count(*) AS n FROM accounts")[0]["n"]
    assert after == before  # every historical row version, not just the latest


def test_restore_text_raises_when_no_export_exists(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.text_export import import_structured_text

    with pytest.raises(FileNotFoundError):
        import_structured_text()


def test_import_structured_text_scoped_to_tables_leaves_others_untouched(tmp_path, monkeypatch):
    """`tables=[(domain, table_name), ...]` rebuilds only the requested
    tables' parquet and registry rows -- never `shutil.rmtree`s the whole
    STRUCTURED_DIR (spec 2026-09-25-vault-sync-design.md §7)."""
    _patch_stores(tmp_path, monkeypatch)
    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    products_csv = tmp_path / "products.csv"
    _write_csv(products_csv, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(products_csv, "banking")

    customers_csv = tmp_path / "customers.csv"
    _write_csv(customers_csv, [["id", "name"], [1, "Acme"]])
    ingest_structured_file(customers_csv, "banking")

    export_structured_text()

    customers_parquet = paths.STRUCTURED_DIR / "banking" / "customers.parquet"
    customers_mtime_before = customers_parquet.stat().st_mtime_ns

    summary = import_structured_text(tables=[("banking", "products")])

    assert summary["tables_loaded"] == 1
    assert registry.get_table("products", domain="banking") is not None
    assert registry.get_table("customers", domain="banking") is not None, (
        "a scoped restore must never wipe an untouched table's registry row"
    )
    assert customers_parquet.stat().st_mtime_ns == customers_mtime_before, (
        "a scoped restore must never rewrite an untouched table's parquet"
    )

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    rows = ds.run_sql("SELECT * FROM products ORDER BY id")
    assert rows == [{"id": 1, "name": "Widget"}, {"id": 2, "name": "Gadget"}]


def test_import_structured_text_unscoped_behaviour_is_unchanged(tmp_path, monkeypatch):
    """Regression pin: `tables=None` (the default) is byte-for-byte today's
    existing wholesale behavior."""
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")
    export_structured_text()

    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db
    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()

    summary = import_structured_text()

    assert summary["tables_loaded"] == 1
    assert registry.get_table("products", domain="banking") is not None


def test_db_restore_text_cli_table_option_scopes_the_restore(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    products_csv = tmp_path / "products.csv"
    _write_csv(products_csv, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(products_csv, "banking")
    customers_csv = tmp_path / "customers.csv"
    _write_csv(customers_csv, [["id", "name"], [1, "Acme"]])
    ingest_structured_file(customers_csv, "banking")
    export_structured_text()

    products_id_before = registry.get_table("products", domain="banking")["id"]
    registry.set_grain(products_id_before, "lookup", confirmed=False)

    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "products", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert registry.get_table("products", domain="banking")["grain"] == "instance"
    assert registry.get_table("customers", domain="banking") is not None


def test_db_restore_text_cli_table_option_is_comma_splittable(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    for name in ("products", "customers"):
        csv_path = tmp_path / f"{name}.csv"
        _write_csv(csv_path, [["id"], [1]])
        ingest_structured_file(csv_path, "banking")
    export_structured_text()

    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "products,customers", "--compact"]
    )

    assert result.exit_code == 0, result.output
    import json as jsonlib
    assert jsonlib.loads(result.output)["tables_loaded"] == 2


def test_db_restore_text_cli_table_nonexistent_raises_error(tmp_path, monkeypatch):
    """Requesting a nonexistent table should error and leave the store untouched."""
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    # Create and export a known table so we have something to verify wasn't touched.
    products_csv = tmp_path / "products.csv"
    _write_csv(products_csv, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(products_csv, "banking")
    export_structured_text()

    # Get the products table ID before the failed restore attempt.
    products_id_before = registry.get_table("products", domain="banking")["id"]

    # Try to restore a nonexistent table.
    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "nonexistent_table"]
    )

    # Should error with "not found" message.
    assert result.exit_code != 0, result.output
    assert "not found" in result.output.lower()

    # Products table should be unchanged (verify by comparing table IDs).
    products_id_after = registry.get_table("products", domain="banking")["id"]
    assert products_id_after == products_id_before


def test_db_restore_text_cli_table_domain_disambiguation(tmp_path, monkeypatch):
    """--table X --domain Y disambiguates a same-named table across domains."""
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    # Create the same table name in two different domains.
    for domain in ("banking", "legal"):
        accounts_csv = tmp_path / f"accounts_{domain}.csv"
        _write_csv(accounts_csv, [["id", "name"], [1, f"{domain}_account"]])
        ingest_structured_file(accounts_csv, domain, table="accounts")

    export_structured_text()

    # Mark the banking.accounts table with a special grain so we can verify it changed.
    banking_accounts_id = registry.get_table("accounts", domain="banking")["id"]
    registry.set_grain(banking_accounts_id, "lookup", confirmed=False)

    legal_accounts_id = registry.get_table("accounts", domain="legal")["id"]
    legal_grain_before = registry.get_table("accounts", domain="legal")["grain"]

    # Restore only banking.accounts using --domain to disambiguate.
    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "accounts", "--domain", "banking", "--compact"]
    )

    assert result.exit_code == 0, result.output

    # Banking.accounts should have been restored (grain reset to "instance").
    banking_grain_after = registry.get_table("accounts", domain="banking")["grain"]
    assert banking_grain_after == "instance"

    # Legal.accounts should be completely untouched.
    legal_accounts_id_after = registry.get_table("accounts", domain="legal")["id"]
    legal_grain_after = registry.get_table("accounts", domain="legal")["grain"]
    assert legal_accounts_id_after == legal_accounts_id
    assert legal_grain_after == legal_grain_before
