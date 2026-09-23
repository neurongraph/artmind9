"""Tests for Task 9 (Phase 5.2): temporal refresh mode wired into
``ingest sync --refreshMode/--businessKey/--effectiveDateColumn`` and the new
``db refresh`` CLI command.

Hermetic: in-process DuckDB + SQLite against tmp paths, no Neo4j/network (the
autouse fixture in ``test/conftest.py`` blocks any real Neo4j driver anyway).
"""

import csv
import json

import click
import pytest
from click.testing import CliRunner

pytest.importorskip("openpyxl")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def _db_sql_rows(sql: str) -> list[dict]:
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "sql", sql])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)["rows"]


# ── ingest sync: --refreshMode temporal validation ───────────────────────────


def test_ingest_structured_file_temporal_requires_business_key(tmp_path, monkeypatch):
    """The pipeline function is the source-of-truth guard (per the plan): a
    temporal table with no business_key can never be refreshed again, so this
    must fail loudly rather than silently seed one."""
    _patch_stores(tmp_path, monkeypatch)
    import click
    from artmind.structured.pipeline import ingest_structured_file

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100]])

    with pytest.raises(click.ClickException, match="businessKey"):
        ingest_structured_file(csv_path, "banking", refresh_mode="temporal")

    from artmind.structured import registry

    assert registry.get_table("accounts", domain="banking") is None


def test_ingest_sync_cli_temporal_without_business_key_does_not_register_table(tmp_path, monkeypatch):
    """Same guard exercised through the CLI. ``ingest sync`` swallows per-file
    exceptions (fail_count++, exit_code 0) rather than propagating them, so the
    CLI-level assertion is that the table was never registered/seeded."""
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100]])

    result = CliRunner().invoke(
        cli.cli,
        ["ingest", "sync", str(csv_path), "--domain", "banking", "--refreshMode", "temporal"],
    )
    assert result.exit_code == 0

    from artmind.structured import registry

    assert registry.get_table("accounts", domain="banking") is None


# ── ingest sync: first-time temporal seed ────────────────────────────────────


def test_ingest_sync_temporal_seeds_current_rows_no_valid_to(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100], [2, 200]])

    result = CliRunner().invoke(
        cli.cli,
        [
            "ingest", "sync", str(csv_path),
            "--domain", "banking",
            "--refreshMode", "temporal",
            "--businessKey", "id",
        ],
    )
    assert result.exit_code == 0, result.output

    from artmind.structured import registry

    table_row = registry.get_table("accounts", domain="banking")
    assert table_row is not None
    assert table_row["refresh_mode"] == "temporal"
    assert table_row["business_key"] == "id"
    assert table_row["row_count"] == 2

    rows = _db_sql_rows(
        "SELECT id, balance, _valid_to, _is_current FROM accounts ORDER BY id"
    )
    assert len(rows) == 2
    assert all(r["_is_current"] is True for r in rows)
    assert all(r["_valid_to"] is None for r in rows)


def test_ingest_sync_temporal_effective_date_column_seeds_valid_from(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance", "asof"], [1, 100, "2020-01-01"]])

    result = CliRunner().invoke(
        cli.cli,
        [
            "ingest", "sync", str(csv_path),
            "--domain", "banking",
            "--refreshMode", "temporal",
            "--businessKey", "id",
            "--effectiveDateColumn", "asof",
        ],
    )
    assert result.exit_code == 0, result.output

    rows = _db_sql_rows("SELECT CAST(_valid_from AS VARCHAR) AS vf FROM accounts")
    assert rows == [{"vf": "2020-01-01"}]


# ── db refresh: temporal accumulates SCD-2 history ───────────────────────────


def test_db_refresh_temporal_accumulates_history(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100], [2, 200]])

    seed = CliRunner().invoke(
        cli.cli,
        [
            "ingest", "sync", str(csv_path),
            "--domain", "banking",
            "--refreshMode", "temporal",
            "--businessKey", "id",
        ],
    )
    assert seed.exit_code == 0, seed.output

    # id=1 changes, id=2 disappears, id=3 is new.
    _write_csv(csv_path, [["id", "balance"], [1, 150], [3, 300]])

    result = CliRunner().invoke(cli.cli, ["db", "refresh", "accounts", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["scd2"] == {"inserted": 2, "closed": 2, "unchanged": 0}
    assert payload["row_count"] == 4  # full history: 1(closed)+1(open)+2(closed)+3(open)

    from artmind.structured import registry

    table_row = registry.get_table("accounts", domain="banking")
    assert table_row["version"] == 2

    rows = _db_sql_rows("SELECT id, balance, _is_current FROM accounts ORDER BY id, balance")

    id1_rows = [r for r in rows if r["id"] == 1]
    assert {r["balance"] for r in id1_rows} == {100, 150}
    assert sum(1 for r in id1_rows if r["_is_current"]) == 1
    assert sum(1 for r in id1_rows if not r["_is_current"]) == 1

    id2_rows = [r for r in rows if r["id"] == 2]
    assert len(id2_rows) == 1
    assert id2_rows[0]["_is_current"] is False

    id3_rows = [r for r in rows if r["id"] == 3]
    assert len(id3_rows) == 1
    assert id3_rows[0]["_is_current"] is True


def test_db_refresh_temporal_confirmed_mapping_carries_forward(tmp_path, monkeypatch):
    """A manually-confirmed mapping must survive a temporal `db refresh`.

    Now guarded twice over: classification no longer re-runs at all on a
    temporal refresh (only first registration, or a replace-mode column-set
    change, trigger it), and even when the mapping step does run it refuses to
    overwrite a confirmed `(column, entity_class)` pair."""
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli
    from artmind.structured import registry

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100]])
    CliRunner().invoke(
        cli.cli,
        [
            "ingest", "sync", str(csv_path),
            "--domain", "banking",
            "--refreshMode", "temporal",
            "--businessKey", "id",
        ],
    )

    table_row = registry.get_table("accounts", domain="banking")
    registry.upsert_mapping(table_row["id"], "id", "ACCOUNT", 1.0, confirmed=True)

    _write_csv(csv_path, [["id", "balance"], [1, 100], [2, 200]])
    result = CliRunner().invoke(cli.cli, ["db", "refresh", "accounts", "--domain", "banking"])
    assert result.exit_code == 0, result.output

    mappings = registry.list_mappings(table_row["id"])
    confirmed = [m for m in mappings if m["confirmed"]]
    assert any(m["column"] == "id" and m["entity_class"] == "ACCOUNT" for m in confirmed)


# ── db refresh: replace mode regression ──────────────────────────────────────


def test_db_refresh_replace_mode_still_works(tmp_path, monkeypatch):
    """Regression: a plain replace-mode table's `db refresh` behaves exactly
    as before Task 9's temporal branch was added."""
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    seed = CliRunner().invoke(cli.cli, ["ingest", "sync", str(csv_path), "--domain", "banking"])
    assert seed.exit_code == 0, seed.output

    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    result = CliRunner().invoke(cli.cli, ["db", "refresh", "products", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["row_count"] == 2
    assert payload["version"] == 2
    assert "scd2" not in payload


# ── db refresh: table resolution errors ──────────────────────────────────────


def test_db_refresh_unknown_table_errors(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "refresh", "nope"])
    assert result.exit_code != 0


def test_db_refresh_ambiguous_table_requires_domain(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    banking_dir = tmp_path / "banking_src"
    banking_dir.mkdir()
    contracts_dir = tmp_path / "contracts_src"
    contracts_dir.mkdir()

    banking_csv = banking_dir / "shared.csv"
    _write_csv(banking_csv, [["id", "name"], [1, "Checking"]])
    contracts_csv = contracts_dir / "shared.csv"
    _write_csv(contracts_csv, [["id", "name"], [1, "Widget"]])

    CliRunner().invoke(cli.cli, ["ingest", "sync", str(banking_csv), "--domain", "banking"])
    CliRunner().invoke(cli.cli, ["ingest", "sync", str(contracts_csv), "--domain", "contracts"])

    ambiguous = CliRunner().invoke(cli.cli, ["db", "refresh", "shared"])
    assert ambiguous.exit_code != 0
    assert "ambiguous" in ambiguous.output

    scoped = CliRunner().invoke(cli.cli, ["db", "refresh", "shared", "--domain", "banking"])
    assert scoped.exit_code == 0, scoped.output


# ── db refresh: temporal schema-drift is a clean error, not a raw traceback ──


def test_db_refresh_temporal_schema_drift_raises_clean_error(tmp_path, monkeypatch):
    """Code-review followup: a dropped/added non-key column on refresh used to
    surface as a raw duckdb.BinderException from deep inside
    scd2.apply_scd2_refresh's hash(ROW(...)) construction. pipeline.py now
    validates the incoming columns against the existing history up front and
    raises a clean ValueError, which `db refresh` turns into a ClickException."""
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100]])
    seed = CliRunner().invoke(
        cli.cli,
        [
            "ingest", "sync", str(csv_path),
            "--domain", "banking",
            "--refreshMode", "temporal",
            "--businessKey", "id",
        ],
    )
    assert seed.exit_code == 0, seed.output

    # Drop the "balance" column and add an unrelated "region" column instead.
    _write_csv(csv_path, [["id", "region"], [1, "west"]])

    result = CliRunner().invoke(cli.cli, ["db", "refresh", "accounts", "--domain", "banking"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "columns don't match" in result.output
    assert "balance" in result.output
    assert "region" in result.output


# ── ingest sync --tableName: one table across dated exports ──────────────────


def _sync(*args):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["ingest", "sync", *map(str, args)])
    assert result.exit_code == 0, result.output
    return result


def test_table_name_loads_a_file_into_the_named_table(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    export = tmp_path / "Hercules-output_20260921.csv"
    _write_csv(export, [["id", "segment"], [1, "Core"]])

    _sync(export, "--domain", "banking", "--tableName", "Hercules Output")

    from artmind.structured import registry

    assert registry.get_table("hercules_output", domain="banking") is not None   # sanitized
    assert registry.get_table("hercules_output_20260921", domain="banking") is None


def test_a_new_export_into_a_temporal_table_adds_a_version_not_a_rebuild(tmp_path, monkeypatch):
    """The point of --tableName: the next export lands in the SAME temporal
    table and is diffed against its history. Before, a sync into an existing
    temporal table rebuilt it from the one file, re-seeding history."""
    _patch_stores(tmp_path, monkeypatch)
    first = tmp_path / "export_20260921.csv"
    second = tmp_path / "export_20261021.csv"
    _write_csv(first, [["id", "segment"], [1, "Core"], [2, "Low"]])
    _write_csv(second, [["id", "segment"], [1, "Top"], [3, "Core"]])

    _sync(first, "--domain", "banking", "--tableName", "hercules", "--refreshMode", "temporal", "--businessKey", "id")
    # No --refreshMode / --businessKey: the table's recorded ones apply.
    _sync(second, "--domain", "banking", "--tableName", "hercules")

    from artmind.structured import registry

    row = registry.get_table("hercules", domain="banking")
    assert row["refresh_mode"] == "temporal" and row["business_key"] == "id"
    assert row["source_file"] == str(second)
    rows = _db_sql_rows("SELECT id, segment, _is_current FROM hercules ORDER BY id, segment")
    assert rows == [
        {"id": 1, "segment": "Core", "_is_current": False},
        {"id": 1, "segment": "Top", "_is_current": True},
        {"id": 2, "segment": "Low", "_is_current": False},
        {"id": 3, "segment": "Core", "_is_current": True},
    ]


def test_forced_resync_of_a_temporal_table_keeps_its_history(tmp_path, monkeypatch):
    """Same file, --force: previously re-seeded (history lost); now an
    idempotent SCD-2 diff."""
    _patch_stores(tmp_path, monkeypatch)
    csv_path = tmp_path / "accounts.csv"
    _write_csv(csv_path, [["id", "balance"], [1, 100]])
    _sync(csv_path, "--domain", "banking", "--refreshMode", "temporal", "--businessKey", "id")
    _write_csv(csv_path, [["id", "balance"], [1, 150]])
    _sync(csv_path, "--domain", "banking")
    _sync(csv_path, "--domain", "banking", "--force")

    rows = _db_sql_rows("SELECT balance, _is_current FROM accounts ORDER BY balance")
    assert rows == [{"balance": 100, "_is_current": False}, {"balance": 150, "_is_current": True}]


@pytest.mark.parametrize("extra, message", [
    (["--refreshMode", "replace"], "would discard that history"),
    (["--businessKey", "segment"], "would break its history"),
    (["--effectiveDateColumn", "segment"], "takes its effective date from the ingest date"),
])
def test_requests_that_would_break_a_temporal_tables_history_are_refused(tmp_path, monkeypatch, extra, message):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli
    import artmind.structured.pipeline as pipeline

    first = tmp_path / "export_1.csv"
    second = tmp_path / "export_2.csv"
    _write_csv(first, [["id", "segment"], [1, "Core"]])
    _write_csv(second, [["id", "segment"], [1, "Top"]])
    _sync(first, "--domain", "banking", "--tableName", "hercules", "--refreshMode", "temporal", "--businessKey", "id")

    with pytest.raises(click.ClickException, match=message):
        pipeline.ingest_structured_file(second, "banking", table="hercules", **{
            "--refreshMode": {"refresh_mode": extra[1]},
            "--businessKey": {"business_key": extra[1]},
            "--effectiveDateColumn": {"effective_date_column": extra[1]},
        }[extra[0]])

    # Through the CLI the refusal is logged per file (ingest sync's convention),
    # and the table is untouched either way.
    CliRunner().invoke(cli.cli, ["ingest", "sync", str(second), "--domain", "banking", "--tableName", "hercules", *extra])
    rows = _db_sql_rows("SELECT id, segment, _is_current FROM hercules")
    assert rows == [{"id": 1, "segment": "Core", "_is_current": True}]


def test_table_name_is_refused_for_a_directory(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import artmind.cli as cli

    folder = tmp_path / "exports"
    folder.mkdir()
    _write_csv(folder / "a.csv", [["id"], [1]])

    result = CliRunner().invoke(cli.cli, ["ingest", "sync", str(folder), "--domain", "banking", "--tableName", "hercules"])
    assert result.exit_code != 0
    assert "--tableName applies to a single csv/xlsx file" in result.output


def test_table_name_prefixes_each_sheet_of_a_multi_sheet_workbook(tmp_path, monkeypatch):
    from openpyxl import Workbook

    from artmind.structured.pipeline import _enumerate_source_tables

    path = tmp_path / "export_20260921.xlsx"
    wb = Workbook()
    wb.active.title = "People"
    wb.active.append(["id"])
    wb.active.append([1])
    other = wb.create_sheet("PIPs")
    other.append(["id"])
    other.append([1])
    wb.save(path)

    specs = _enumerate_source_tables(path, table="hercules", sheet=None, header_row=0)
    assert [s["table_name"] for s in specs] == ["hercules__people", "hercules__pips"]
