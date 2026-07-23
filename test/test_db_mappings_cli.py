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

    from artmind.structured import registry

    table = registry.get_table("products", domain="banking")
    registry.upsert_mapping(table["id"], "name", "PRODUCT", 0.85, confirmed=False)
    return tmp_path


def test_db_mappings_lists_proposed(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "products", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    assert "name" in result.output
    assert "PRODUCT" in result.output
    assert "0.85" in result.output
    assert '"confirmed": 0' in result.output


def test_db_mappings_set_adds_confirmed(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    result = CliRunner().invoke(
        cli.cli,
        [
            "db", "mappings", "products",
            "set", "--column", "id", "--entityClass", "PRODUCT_ID", "--confidence", "1.0",
        ],
    )
    assert result.exit_code == 0, result.output

    table = registry.get_table("products", domain="banking")
    mappings = registry.list_mappings(table["id"])
    match = [m for m in mappings if m["column"] == "id" and m["entity_class"] == "PRODUCT_ID"]
    assert len(match) == 1
    assert match[0]["confirmed"] == 1
    assert match[0]["confidence"] == 1.0


def test_db_mappings_confirm_flips_proposed(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    result = CliRunner().invoke(
        cli.cli,
        ["db", "mappings", "products", "confirm", "--column", "name", "--entityClass", "PRODUCT"],
    )
    assert result.exit_code == 0, result.output

    table = registry.get_table("products", domain="banking")
    mappings = registry.list_mappings(table["id"])
    match = [m for m in mappings if m["column"] == "name" and m["entity_class"] == "PRODUCT"]
    assert len(match) == 1
    assert match[0]["confirmed"] == 1


def test_db_mappings_confirm_unknown_errors(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli,
        ["db", "mappings", "products", "confirm", "--column", "nope", "--entityClass", "NOPE"],
    )
    assert result.exit_code != 0


def test_db_mappings_clear_removes_column(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    result = CliRunner().invoke(
        cli.cli, ["db", "mappings", "products", "clear", "--column", "name"],
    )
    assert result.exit_code == 0, result.output

    table = registry.get_table("products", domain="banking")
    mappings = registry.list_mappings(table["id"])
    assert not [m for m in mappings if m["column"] == "name"]


def test_db_mappings_clear_all(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table = registry.get_table("products", domain="banking")
    registry.upsert_mapping(table["id"], "id", "PRODUCT_ID", 1.0, confirmed=True)

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "products", "clear"])
    assert result.exit_code == 0, result.output

    mappings = registry.list_mappings(table["id"])
    assert mappings == []


def test_db_mappings_accept_proposed_bulk_confirms(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table = registry.get_table("products", domain="banking")
    registry.upsert_mapping(table["id"], "id", "PRODUCT_ID", 0.6, confirmed=False)

    result = CliRunner().invoke(
        cli.cli, ["db", "mappings", "products", "--domain", "banking", "--acceptProposed"],
    )
    assert result.exit_code == 0, result.output

    mappings = registry.list_mappings(table["id"])
    assert len(mappings) == 2
    assert all(m["confirmed"] == 1 for m in mappings)


def test_db_mappings_unknown_table_errors(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "nope"])
    assert result.exit_code != 0


# Note: cross-domain table-name ambiguity (same table_name, two domains) is not
# reachable through the registry as written — "tables" has a global
# UNIQUE(datasource, table_name) constraint (artmind/db.py), so re-registering the
# same table_name just updates the existing row's domain in place rather than
# creating a second row. _resolve_table_id's ambiguity guard is therefore
# defensive-only under the current schema and isn't exercised here.
