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


def test_db_bridge_returns_routing_surface(ingested):
    """The read side of everything the bridge stores — without this the grain,
    class scope and bridge columns are write-only and no agent can route on them."""
    import json

    import artmind.cli as cli
    from artmind.structured import registry

    table_id = registry.get_table("products", domain="banking")["id"]
    registry.upsert_mapping(table_id, "name", "PRODUCT", 0.9, confirmed=True)
    registry.upsert_column_role(table_id, "name", "term", 0.9, confirmed=True)

    result = CliRunner().invoke(cli.cli, ["db", "bridge", "--domain", "banking", "--compact"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    table = body["tables"][0]
    assert table["table"] == "products"
    assert table["grain"] == "instance"
    assert [c["entity_class"] for c in table["entity_classes"]] == ["PRODUCT"]
    assert table["bridge_columns"][0]["column"] == "name"


def test_db_bridge_entity_class_filter(ingested):
    import json

    import artmind.cli as cli
    from artmind.structured import registry

    table_id = registry.get_table("products", domain="banking")["id"]
    registry.upsert_mapping(table_id, "name", "PRODUCT", 0.9, confirmed=True)

    hit = CliRunner().invoke(cli.cli, ["db", "bridge", "--entityClass", "PRODUCT", "--compact"])
    miss = CliRunner().invoke(cli.cli, ["db", "bridge", "--entityClass", "CUSTOMER", "--compact"])
    assert [t["table"] for t in json.loads(hit.output)["tables"]] == ["products"]
    assert json.loads(miss.output)["tables"] == []


def test_db_list_finds_root_tables_from_a_leaf_domain(ingested):
    """The reported repro: `db list --domain banking.cases` returned {"tables": []}
    while six tables sat at bare `banking`."""
    import json

    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "list", "--domain", "banking.cases", "--compact"])
    assert result.exit_code == 0, result.output
    assert [t["table_name"] for t in json.loads(result.output)["tables"]] == ["products"]


def test_db_grain_shows_and_confirms(ingested):
    import json

    import artmind.cli as cli

    shown = CliRunner().invoke(cli.cli, ["db", "grain", "products", "--compact"])
    assert json.loads(shown.output)["grain"] == "instance"
    assert json.loads(shown.output)["grainConfirmed"] is False

    setres = CliRunner().invoke(
        cli.cli, ["db", "grain", "products", "--set", "lookup", "--compact"]
    )
    assert setres.exit_code == 0, setres.output
    assert json.loads(setres.output)["grain"] == "lookup"
    assert json.loads(setres.output)["grainConfirmed"] is True

    # normative needs SCD-2 history; this table is refresh_mode=replace.
    bad = CliRunner().invoke(
        cli.cli, ["db", "grain", "products", "--set", "normative", "--compact"]
    )
    assert bad.exit_code != 0
    assert "temporal" in bad.output


def test_db_propose_runs_all_steps_by_default(ingested, monkeypatch):
    import json

    import artmind.cli as cli
    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append((table_id, domain, kw)) or {
            "table": "products", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    result = CliRunner().invoke(cli.cli, ["db", "propose", "products", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    _, domain, kwargs = calls[0]
    assert domain == "banking"
    assert kwargs["steps"] is None
    assert kwargs["redo"] is False
    payload = json.loads(result.output)
    assert payload["mapping_status"] == "ok"


def test_db_propose_step_flag_repeatable(ingested, monkeypatch):
    import artmind.cli as cli
    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(kw) or {
            "table": "products", "domain": domain,
            "grain_status": "pending", "bridge_status": "pending", "mapping_status": "ok",
        },
    )

    result = CliRunner().invoke(
        cli.cli,
        ["db", "propose", "products", "--domain", "banking", "--step", "mapping", "--redo"],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["steps"] == ["mapping"]
    assert calls[0]["redo"] is True


def test_db_propose_skip_semantics_flag_removed(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli, ["db", "propose", "products", "--domain", "banking", "--skipSemantics"]
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


def test_db_propose_rejects_unknown_step(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli, ["db", "propose", "products", "--domain", "banking", "--step", "nonsense"]
    )
    assert result.exit_code != 0


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


def test_db_sql_serializes_date_columns(tmp_path, monkeypatch):
    """DATE/TIMESTAMP/DECIMAL values come off the DuckDB result set as Python
    objects `json.dumps` can't encode. Before `_echo_json` passed `default=str`,
    any query touching a date column died with "Object of type date is not JSON
    serializable" and the only workaround was CAST(... AS VARCHAR)."""
    import artmind.cli as cli
    from artmind.structured.pipeline import ingest_structured_file

    _patch_stores(tmp_path, monkeypatch)
    csv_path = tmp_path / "complaints.csv"
    _write_csv(
        csv_path,
        [["complaint_id", "date_received"], [1, "2026-07-01"], [2, "2026-07-02"]],
    )
    ingest_structured_file(csv_path, "banking")

    result = CliRunner().invoke(
        cli.cli,
        ["db", "sql", "SELECT complaint_id, date_received FROM complaints", "--compact"],
    )
    assert result.exit_code == 0, result.output
    assert "2026-07-01" in result.output


def test_db_sql_is_unscoped_across_domains(tmp_path, monkeypatch):
    """`db sql` is deliberately domain-unscoped (operator-facing raw SQL) —
    unlike text2sql's execution, it must still see tables from every domain
    in the same invocation."""
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file

    banking_csv = tmp_path / "banking_table.csv"
    _write_csv(banking_csv, [["id", "name"], [1, "Checking"]])
    ingest_structured_file(banking_csv, "banking", table="banking_table")

    retail_csv = tmp_path / "retail_table.csv"
    _write_csv(retail_csv, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(retail_csv, "retail", table="retail_table")

    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli, ["db", "sql", "SELECT count(*) AS n FROM retail_table"]
    )
    assert result.exit_code == 0, result.output
    assert '"n": 1' in result.output


def test_db_catalogue_invokes_projection_and_returns_json(ingested, monkeypatch):
    import artmind.cli as cli
    import artmind.structured.catalogue as catalogue

    spy = {"calls": []}

    def fake_project_catalogue(domain):
        spy["calls"].append(domain)
        return {"tables": 1, "columns": 2, "mappings": 0}

    monkeypatch.setattr(catalogue, "project_catalogue", fake_project_catalogue)

    result = CliRunner().invoke(cli.cli, ["db", "catalogue", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    assert spy["calls"] == ["banking"]
    assert '"domain": "banking"' in result.output
    assert '"tables": 1' in result.output


def test_db_catalogue_requires_domain(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "catalogue"])
    assert result.exit_code != 0


def test_db_catalogue_surfaces_failure_as_click_exception(ingested, monkeypatch):
    import artmind.cli as cli
    import artmind.structured.catalogue as catalogue

    def failing_project_catalogue(domain):
        raise RuntimeError("neo4j is down")

    monkeypatch.setattr(catalogue, "project_catalogue", failing_project_catalogue)

    result = CliRunner().invoke(cli.cli, ["db", "catalogue", "--domain", "banking"])
    assert result.exit_code != 0
    assert "catalogue projection failed" in result.output


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


# ── db bridge: confirm / clear a bridge column ───────────────────────────────


def _seed_roles_and_mappings(table="products", domain="banking"):
    """Give the fixture table one proposed bridge role and one proposed mapping."""
    from artmind.structured import registry

    row = registry.get_table(table, domain=domain)
    registry.upsert_column_role(row["id"], "name", "term", 0.9, confirmed=False)
    registry.upsert_mapping(row["id"], "name", "PRODUCT", 0.9, confirmed=False)
    return row["id"]


def test_db_bridge_without_subcommand_still_returns_routing_surface(ingested):
    """Regression: turning `db bridge` into a group must not change its default."""
    import json

    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "bridge", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "db bridge"
    assert any(t["table"] == "products" for t in payload["tables"])


def test_db_bridge_confirm_flips_the_role(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table_id = _seed_roles_and_mappings()
    result = CliRunner().invoke(cli.cli, [
        "db", "bridge", "confirm", "--table", "products", "--domain", "banking", "--column", "name",
    ])
    assert result.exit_code == 0, result.output
    role = registry.list_column_roles(table_id)[0]
    assert role["column"] == "name"
    assert role["confirmed"] == 1


def test_db_bridge_confirm_unknown_column_errors(ingested):
    """Mirrors `db mappings confirm`: asserting a transition on a row that isn't
    there must fail loudly, not silently no-op."""
    import artmind.cli as cli

    _seed_roles_and_mappings()
    result = CliRunner().invoke(cli.cli, [
        "db", "bridge", "confirm", "--table", "products", "--domain", "banking", "--column", "nope",
    ])
    assert result.exit_code != 0
    assert "no bridge column" in result.output.lower()


def test_db_bridge_clear_removes_the_role(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table_id = _seed_roles_and_mappings()
    result = CliRunner().invoke(cli.cli, [
        "db", "bridge", "clear", "--table", "products", "--domain", "banking", "--column", "name",
    ])
    assert result.exit_code == 0, result.output
    assert registry.list_column_roles(table_id) == []


# ── db review: what is awaiting human adjudication ───────────────────────────


def test_db_review_lists_unconfirmed_items(ingested):
    import json

    import artmind.cli as cli

    _seed_roles_and_mappings()
    result = CliRunner().invoke(cli.cli, ["db", "review", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    entry = next(t for t in payload["tables"] if t["table"] == "products")
    assert entry["mappings"] == [
        {"column": "name", "entity_class": "PRODUCT", "confidence": 0.9}
    ]
    assert entry["bridge_columns"] == [{"column": "name", "confidence": 0.9}]
    assert entry["grain_confirmed"] is False
    # 'failed', not 'pending': the fixture's ingest triggers first-registration
    # classification, whose LLM call conftest blocks to keep the suite hermetic.
    # The step records the failure and the load still succeeds — which is the
    # best-effort ingest hook behaving correctly, and is exactly the state
    # `db review` has to stay readable in.
    assert entry["grain_status"] == "failed"
    assert payload["pending_count"] == 1


def test_db_review_omits_fully_confirmed_tables(ingested):
    """A table with nothing left to adjudicate must drop out of the list —
    that is the whole point of the command."""
    import json

    import artmind.cli as cli
    from artmind.structured import registry

    table_id = _seed_roles_and_mappings()
    registry.set_mapping_confirmed(table_id, "name", "PRODUCT", True)
    registry.set_column_role_confirmed(table_id, "name", True)
    registry.set_grain(table_id, "instance", confirmed=True)

    result = CliRunner().invoke(cli.cli, ["db", "review", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["tables"] == []
    assert payload["pending_count"] == 0
