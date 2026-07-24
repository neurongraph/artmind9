"""Tests for Task 9 (Phase 5.2): temporal refresh mode wired into
``ingest sync --refreshMode/--businessKey/--effectiveDateColumn`` and the new
``db refresh`` CLI command.

Hermetic: in-process DuckDB + SQLite against tmp paths, no Neo4j/network (the
autouse fixture in ``test/conftest.py`` blocks any real Neo4j driver anyway).
"""

import csv
import json

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
    """Step 1's 'confirmed mappings carry forward' -- propose_mappings never
    overwrites a confirmed mapping, and both temporal seed and temporal
    refresh re-call it, so a manually-confirmed mapping must survive a
    temporal `db refresh`."""
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
    retail_dir = tmp_path / "retail_src"
    retail_dir.mkdir()

    banking_csv = banking_dir / "shared.csv"
    _write_csv(banking_csv, [["id", "name"], [1, "Checking"]])
    retail_csv = retail_dir / "shared.csv"
    _write_csv(retail_csv, [["id", "name"], [1, "Widget"]])

    CliRunner().invoke(cli.cli, ["ingest", "sync", str(banking_csv), "--domain", "banking"])
    CliRunner().invoke(cli.cli, ["ingest", "sync", str(retail_csv), "--domain", "retail"])

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
