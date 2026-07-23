import csv

import pytest
from click.testing import CliRunner

pytest.importorskip("openpyxl")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


@pytest.fixture()
def ingested(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(csv_path, "banking")
    return tmp_path


def test_db_list_shows_table_domain_scoped(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "list", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    assert "products" in result.output

    result_other = CliRunner().invoke(cli.cli, ["db", "list", "--domain", "other"])
    assert result_other.exit_code == 0, result_other.output
    assert "products" not in result_other.output


def test_db_schema_shows_columns(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "schema", "products"])
    assert result.exit_code == 0, result.output
    assert "id" in result.output
    assert "name" in result.output


def test_db_schema_unknown_table_errors(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "schema", "nope"])
    assert result.exit_code != 0


def test_db_sql_returns_rows(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "sql", "SELECT count(*) AS n FROM products"])
    assert result.exit_code == 0, result.output
    assert '"n": 2' in result.output


def test_db_sql_rejects_write_statement(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "sql", "DELETE FROM products"])
    assert result.exit_code != 0


def test_db_connect_stubbed(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "connect", "postgresql://x"])
    assert result.exit_code != 0
    assert "not available in v1" in result.output


def test_db_backup_creates_snapshot(ingested):
    import artmind.cli as cli
    import paths

    result = CliRunner().invoke(cli.cli, ["db", "backup"])
    assert result.exit_code == 0, result.output
    assert list(paths.STRUCTURED_SNAPSHOT_DIR.glob("*.tar.gz"))


def test_db_restore_requires_confirm(ingested):
    import artmind.cli as cli

    CliRunner().invoke(cli.cli, ["db", "backup"])
    result = CliRunner().invoke(cli.cli, ["db", "restore"])
    assert result.exit_code != 0


def test_db_restore_round_trips_data(ingested):
    import shutil

    import artmind.cli as cli
    import paths
    from artmind.structured import registry

    CliRunner().invoke(cli.cli, ["db", "backup"])

    shutil.rmtree(paths.STRUCTURED_DIR)
    table = registry.get_table("products", domain="banking")
    registry.delete_table(table["id"])
    assert registry.get_table("products", domain="banking") is None

    result = CliRunner().invoke(cli.cli, ["db", "restore", "--confirm"])
    assert result.exit_code == 0, result.output

    restored = registry.get_table("products", domain="banking")
    assert restored is not None
    assert restored["row_count"] == 2

    sql_result = CliRunner().invoke(
        cli.cli, ["db", "sql", "SELECT count(*) AS n FROM products"]
    )
    assert '"n": 2' in sql_result.output
