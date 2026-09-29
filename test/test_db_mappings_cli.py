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


# _TableFirstGroup edge cases — see its docstring in artmind/cli.py for the Click
# limitation this class works around (a Group's own required Argument can't have
# trailing Options when no subcommand follows). These lock in that the shim fails
# safely (a clear, non-zero-exit "Missing argument" error) rather than silently
# misrouting, so a future Click upgrade or refactor can't quietly break it.


def test_db_mappings_no_table_errors_clearly(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output
    assert "TABLE" in result.output


def test_db_mappings_flag_before_table_errors_clearly(ingested):
    """--acceptProposed with no table first isn't silently misread as TABLE."""
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "--acceptProposed"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output


def test_db_mappings_help_documents_table(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "--help"])
    assert result.exit_code == 0, result.output
    assert "TABLE" in result.output


def test_db_mappings_declares_table_as_required(ingested):
    """TABLE is mandatory, and must be *declared* that way, not just enforced.

    _TableFirstGroup excludes the argument from Click's parser, which used to
    force `required=False` — so `--help` (and the admin-ui CLI guide, which
    reads the same Click metadata) advertised an optional `[TABLE]` for an
    argument that errors when omitted. `_PeeledArgument` lets it be declared
    honestly; this guards the declaration, since the runtime behaviour it must
    agree with is already covered above.
    """
    import artmind.cli as cli

    table_param = next(p for p in cli.db_mappings.params if p.name == "table")
    assert table_param.required is True


@pytest.mark.parametrize("subcommand", ["set", "confirm", "clear"])
def test_db_mappings_subcommand_help_works_with_unknown_table(ingested, subcommand):
    """`--help` must not require a table that exists.

    Click runs a group's callback before parsing the subcommand, so resolving
    TABLE eagerly in db_mappings made `db mappings BAD clear --help` fail with
    "table 'BAD' not found" instead of printing help. Resolution is deferred to
    _ctx_table_id for exactly this reason.
    """
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "no_such_table", subcommand, "--help"])
    assert result.exit_code == 0, result.output
    assert "not found" not in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["clear"],
        ["set", "--column", "c", "--entityClass", "E"],
        ["confirm", "--column", "c", "--entityClass", "E"],
    ],
)
def test_db_mappings_subcommands_still_reject_unknown_table(ingested, argv):
    """Deferring resolution must not let a bad table slip through to a real run."""
    import artmind.cli as cli

    result = CliRunner().invoke(cli.cli, ["db", "mappings", "no_such_table", *argv])
    assert result.exit_code != 0
    assert "not found" in result.output


# Note: cross-domain table-name ambiguity (same table_name, two domains) is
# reachable through the registry as written — "tables" has a
# UNIQUE(datasource, domain, table_name) constraint (artmind/db.py), so
# registering the same table_name under a different domain creates a second
# row rather than overwriting the first (see register_table in
# artmind/structured/registry.py). _resolve_table_id's ambiguity guard exists
# to handle exactly that case, but isn't exercised here.


# ── a registry-only curation change travels as the table's .meta.json ────────


import json


def _meta():
    import paths

    return json.loads((paths.STRUCTURED_TEXT_DIR / "banking" / "products.meta.json").read_text())


def _csv_mtime():
    import paths

    return (paths.STRUCTURED_TEXT_DIR / "banking" / "products.csv").stat().st_mtime_ns


def test_confirming_a_mapping_re_exports_only_the_tables_meta(ingested):
    import artmind.cli as cli

    before = _csv_mtime()
    result = CliRunner().invoke(
        cli.cli, ["db", "mappings", "products", "confirm", "--column", "name", "--entityClass", "PRODUCT"],
    )

    assert result.exit_code == 0, result.output
    assert [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]] == [
        ("name", "PRODUCT", 1)
    ]
    assert _csv_mtime() == before, "a curation change never re-dumps the rows"


def test_set_clear_and_accept_proposed_re_export_the_meta(ingested):
    import artmind.cli as cli

    runner = CliRunner()
    runner.invoke(cli.cli, ["db", "mappings", "products", "set", "--column", "id", "--entityClass", "PRODUCT_ID"])
    assert ("id", "PRODUCT_ID", 1) in [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]]

    runner.invoke(cli.cli, ["db", "mappings", "products", "--acceptProposed"])
    assert all(m["confirmed"] == 1 for m in _meta()["column_mappings"])

    runner.invoke(cli.cli, ["db", "mappings", "products", "clear"])
    assert _meta()["column_mappings"] == []


def test_confirming_grain_and_bridge_columns_re_exports_the_meta(ingested):
    import artmind.cli as cli
    from artmind.structured import registry

    table = registry.get_table("products", domain="banking")
    registry.upsert_column_role(table["id"], "name", "term", 0.9, confirmed=False)
    runner = CliRunner()

    runner.invoke(cli.cli, ["db", "grain", "products", "--set", "lookup"])
    assert (_meta()["table"]["grain"], _meta()["table"]["grain_confirmed"]) == ("lookup", 1)

    runner.invoke(cli.cli, ["db", "bridge", "confirm", "--table", "products", "--column", "name"])
    assert [(r["column"], r["confirmed"]) for r in _meta()["column_roles"]] == [("name", 1)]

    runner.invoke(cli.cli, ["db", "bridge", "clear", "--table", "products"])
    assert _meta()["column_roles"] == []


def test_re_proposing_classifications_re_exports_the_meta(ingested, monkeypatch):
    import artmind.cli as cli
    from artmind.structured import registry

    def _propose(table_id, domain, steps=None, redo=False, model=None):
        registry.upsert_mapping(table_id, "id", "PRODUCT_ID", 0.7, confirmed=False)
        return {"steps": ["mapping"]}

    monkeypatch.setattr("artmind.structured.semantics.propose_table_semantics", _propose)

    result = CliRunner().invoke(cli.cli, ["db", "propose", "products"])

    assert result.exit_code == 0, result.output
    assert ("id", "PRODUCT_ID", 0) in [(m["column"], m["entity_class"], m["confirmed"]) for m in _meta()["column_mappings"]]


def test_a_meta_only_export_still_writes_a_missing_csv(ingested):
    import paths
    from artmind.structured import registry, text_export

    csv = paths.STRUCTURED_TEXT_DIR / "banking" / "products.csv"
    csv.unlink()

    text_export.export_structured_text(tables=[registry.get_table("products", domain="banking")], meta_only=True)

    assert csv.is_file()


# A failed or no-op write must not re-export: nothing changed, and a failed
# command must leave the working tree alone.


def _record_reexports(monkeypatch):
    import artmind.cli as cli

    calls = []
    monkeypatch.setattr(cli, "_reexport_table_meta", lambda table_id: calls.append(table_id), raising=False)
    return calls


def test_a_failed_curation_write_does_not_re_export(ingested, monkeypatch):
    import artmind.cli as cli

    calls = _record_reexports(monkeypatch)
    runner = CliRunner()

    confirm = runner.invoke(
        cli.cli, ["db", "mappings", "products", "confirm", "--column", "nope", "--entityClass", "NOPE"]
    )
    bridge = runner.invoke(cli.cli, ["db", "bridge", "confirm", "--table", "products", "--column", "nope"])
    grain = runner.invoke(cli.cli, ["db", "grain", "products", "--set", "normative"])
    unknown = runner.invoke(cli.cli, ["db", "mappings", "no_such_table", "set", "--column", "a", "--entityClass", "B"])

    assert [r.exit_code != 0 for r in (confirm, bridge, grain, unknown)] == [True, True, True, True]
    assert calls == []


def test_read_only_invocations_do_not_re_export(ingested, monkeypatch):
    import artmind.cli as cli

    calls = _record_reexports(monkeypatch)
    runner = CliRunner()

    runner.invoke(cli.cli, ["db", "mappings", "products"])
    runner.invoke(cli.cli, ["db", "grain", "products"])
    runner.invoke(cli.cli, ["db", "bridge"])

    assert calls == []


def test_each_curation_command_re_exports_its_own_table_once(ingested, monkeypatch):
    import artmind.cli as cli
    from artmind.structured import registry

    table_id = registry.get_table("products", domain="banking")["id"]
    registry.upsert_column_role(table_id, "name", "term", 0.9, confirmed=False)
    calls = _record_reexports(monkeypatch)
    runner = CliRunner()

    runner.invoke(cli.cli, ["db", "mappings", "products", "set", "--column", "id", "--entityClass", "PRODUCT_ID"])
    runner.invoke(cli.cli, ["db", "mappings", "products", "clear"])
    runner.invoke(cli.cli, ["db", "grain", "products", "--set", "lookup"])
    runner.invoke(cli.cli, ["db", "bridge", "clear", "--table", "products"])

    assert calls == [table_id] * 4


def test_a_failing_meta_export_warns_but_never_fails_the_command(ingested, monkeypatch):
    import artmind.cli as cli
    from artmind.structured import registry, text_export
    from loguru import logger

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(text_export, "export_structured_text", _boom)
    warnings = []
    sink = logger.add(lambda m: warnings.append(str(m)), level="WARNING")
    try:
        result = CliRunner().invoke(
            cli.cli, ["db", "mappings", "products", "confirm", "--column", "name", "--entityClass", "PRODUCT"],
        )
    finally:
        logger.remove(sink)

    assert result.exit_code == 0, result.output
    table = registry.get_table("products", domain="banking")
    assert [(m["column"], m["confirmed"]) for m in registry.list_mappings(table["id"])] == [("name", 1)]
    assert any("text export failed" in w and "disk full" in w for w in warnings)


def test_a_meta_only_export_is_byte_stable_and_leaves_an_existing_csv_alone(ingested):
    import paths
    from artmind.structured import registry, text_export

    banking = paths.STRUCTURED_TEXT_DIR / "banking"
    table = registry.get_table("products", domain="banking")
    csv_before = (banking / "products.csv").read_bytes()
    (banking / "products.csv").write_text("sentinel\n")  # would be overwritten by a full export

    text_export.export_structured_text(tables=[table], meta_only=True)
    first = (banking / "products.meta.json").read_bytes()
    text_export.export_structured_text(tables=[table], meta_only=True)

    assert (banking / "products.meta.json").read_bytes() == first
    assert (banking / "products.csv").read_text() == "sentinel\n"
    assert csv_before != b"sentinel\n"


def test_a_meta_only_export_never_opens_duckdb_when_every_csv_exists(ingested, monkeypatch):
    import paths
    from artmind.structured import registry, text_export

    banking = paths.STRUCTURED_TEXT_DIR / "banking"
    assert (banking / "products.csv").is_file()
    (banking / "products.meta.json").unlink()

    def _boom(*args, **kwargs):
        raise AssertionError("DuckDB must stay unopened: there is no CSV to write")

    monkeypatch.setattr(text_export, "DuckDBDatasource", _boom)

    text_export.export_structured_text(tables=[registry.get_table("products", domain="banking")], meta_only=True)

    assert (banking / "products.meta.json").is_file()
