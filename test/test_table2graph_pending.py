"""`artmind ingest table2graph --pending` (spec 2026-09-30 §6 X4): the
registered tables a mapping matches whose projection is missing, or older
than the table's last refresh or the mapping's last change.

Real structured store and registry, real mappings on disk, the real
`table2graph` build and staging. Only Neo4j is stood in for: the document
commit and the batched rebuild are recorded, never sent (CLAUDE.md §3)."""
import json

import pytest
from click.testing import CliRunner

from artmind import table2graph as t2g

SCHEMA = {
    "name": "banking",
    "entity_types": {"ACCOUNT": {"kind": "recurrent", "properties": {"account_id": {}, "holder": {}}}},
}


def _mapping_yaml(table: str, *, extra: str = "") -> str:
    return (
        f'table: "{table}"\n'
        "entities:\n"
        "  acct:\n"
        "    class: ACCOUNT\n"
        '    name: "{account_id}"\n'
        "    properties:\n"
        "      account_id: id\n"
        f"{extra}"
    )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A registry, a structured store, a mappings dir and a KG staging dir
    of the test's own; Neo4j recorded."""
    import artmind.db as db
    import artmind.ingest as ing
    import artmind.temporal as temporal
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    mappings = tmp_path / "table_mappings"
    mappings.mkdir()
    monkeypatch.setattr(paths, "TABLE_MAPPINGS_DIR", mappings)
    monkeypatch.setattr(temporal, "load_schema", lambda domain: SCHEMA)
    commits = []

    def _write(directory, domain, defer_rebuild=False):
        commits.append((directory.name, domain))
        return {"deferred_keys": [], "unembedded_chunk_ids": []}

    monkeypatch.setattr(ing, "_write_to_neo4j", _write)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys, groups=None: {"keys": len(keys)})
    return tmp_path, mappings, commits


def _load(tmp_path, table: str, rows: str) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    source = tmp_path / f"{table}.csv"
    source.write_text(rows)
    assert ingest_structured_file(source, "banking")["status"] == "ok"


def _project(table: str) -> None:
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", table, "--noEmbed", "--compact"])
    assert result.exit_code == 0, result.output


def _pending() -> list[dict]:
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _build(tmp_path, mappings) -> None:
    """One table per reason, plus a current one and an unmapped one."""
    for table in ("never_projected", "current", "refreshed", "remapped", "recommented"):
        _load(tmp_path, table, "id,holder\n1,Ann\n")
        (mappings / f"{table}.yaml").write_text(_mapping_yaml(table))
    _load(tmp_path, "unmapped", "id\n1\n")
    _load(tmp_path, "twice", "id\n1\n")
    (mappings / "twice_a.yaml").write_text(_mapping_yaml("twice"))
    (mappings / "twice_b.yaml").write_text(_mapping_yaml("tw*"))
    for table in ("current", "refreshed", "remapped", "recommented"):
        _project(table)
    _load(tmp_path, "refreshed", "id,holder\n1,Ann\n2,Bo\n")
    (mappings / "remapped.yaml").write_text(_mapping_yaml("remapped", extra="      holder: holder\n"))
    (mappings / "recommented.yaml").write_text("# only a comment changed\n" + _mapping_yaml("recommented"))


def test_lists_each_out_of_date_projection_with_its_reason(env):
    tmp_path, mappings, _ = env
    _build(tmp_path, mappings)

    assert _pending() == [
        {"table": "never_projected", "domain": "banking", "mapping": "never_projected.yaml", "reason": "missing"},
        {"table": "refreshed", "domain": "banking", "mapping": "refreshed.yaml", "reason": "table_refreshed"},
        {"table": "remapped", "domain": "banking", "mapping": "remapped.yaml", "reason": "mapping_changed"},
        {"table": "twice", "domain": "banking", "mapping": "twice_a.yaml, twice_b.yaml", "reason": "ambiguous"},
    ]


def test_equals_what_a_following_table2graph_changes(env):
    """The contract: projecting every mapped table rewrites the staged
    projection of exactly the tables `--pending` listed (the other runs
    rebuild byte-identical files), and afterwards only the ambiguous table
    -- which `table2graph` refuses -- is left."""
    from artmind import sync_state
    from artmind.structured import registry

    tmp_path, mappings, _ = env
    _build(tmp_path, mappings)
    listed = {e["table"] for e in _pending() if e["reason"] != "ambiguous"}

    changed = set()
    for row in registry.list_tables():
        if len(t2g.find_mappings(row["table_name"], row["domain"])) != 1:
            continue
        folder = t2g.stage_dir(row["domain"], row["table_name"])
        before = sync_state.folder_fingerprint(folder) if folder.is_dir() else None
        _project(row["table_name"])
        if sync_state.folder_fingerprint(folder) != before:
            changed.add(row["table_name"])

    assert changed == listed
    assert [e["reason"] for e in _pending()] == ["ambiguous"]


def test_a_projection_from_before_the_digest_is_judged_on_its_mapping_name(env):
    """A projection staged before `table_mapping_sha256` was recorded is not
    reported as `mapping_changed` merely for lacking it."""
    tmp_path, mappings, _ = env
    _load(tmp_path, "legacy", "id\n1\n")
    (mappings / "legacy.yaml").write_text(_mapping_yaml("legacy"))
    _project("legacy")
    document_path = t2g.stage_dir("banking", "legacy") / "document.json"
    document = json.loads(document_path.read_text())
    del document["table_mapping_sha256"]
    document_path.write_text(json.dumps(document))

    assert _pending() == []

    document["table_mapping"] = "some_other.yaml"
    document_path.write_text(json.dumps(document))
    assert [e["reason"] for e in _pending()] == ["mapping_changed"]


def test_every_projection_records_its_mapping_digest(env):
    tmp_path, mappings, commits = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    _project("accounts")

    document = json.loads((t2g.stage_dir("banking", "accounts") / "document.json").read_text())
    assert document["table_mapping"] == "accounts.yaml"
    assert document["table_mapping_sha256"] == t2g.load_mapping(mappings / "accounts.yaml").digest
    assert commits == [("table__accounts", "banking")]


def test_the_digest_ignores_comments_and_key_order_but_not_content():
    base = t2g.parse_mapping({"table": "t", "entities": {"a": {"class": "C", "name": "{x}"}}})
    reordered = t2g.parse_mapping({"entities": {"a": {"name": "{x}", "class": "C"}}, "table": "t"})
    edited = t2g.parse_mapping({"table": "t", "entities": {"a": {"class": "C", "name": "{y}"}}})

    assert base.digest == reordered.digest
    assert base.digest != edited.digest


def test_pending_writes_nothing(env):
    tmp_path, mappings, commits = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    assert [e["reason"] for e in _pending()] == ["missing"]
    assert commits == []
    assert not (tmp_path / "kg").exists()


def test_domain_scopes_the_listing(env):
    from artmind.cli import cli

    tmp_path, mappings, _ = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "accounts.yaml").write_text(_mapping_yaml("accounts"))

    other = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--domain", "fiction", "--compact"])
    mine = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending", "--domain", "banking", "--compact"])

    assert json.loads(other.stdout) == []
    assert [e["table"] for e in json.loads(mine.stdout)] == ["accounts"]


def test_pending_takes_no_table(env):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "accounts", "--pending"])

    assert result.exit_code != 0
    assert "--pending takes no TABLE" in result.output


def test_table2graph_still_needs_a_table_without_pending(env):
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["ingest", "table2graph"])

    assert result.exit_code != 0
    assert "Give one or more TABLEs to project, or --pending" in result.output


def test_a_mapping_that_does_not_parse_fails_the_listing(env):
    """As it fails `table2graph` itself: silently skipping it would report
    "nothing waiting" about tables it plainly names."""
    from artmind.cli import cli

    tmp_path, mappings, _ = env
    _load(tmp_path, "accounts", "id\n1\n")
    (mappings / "broken.yaml").write_text("table: [unclosed\n")

    result = CliRunner().invoke(cli, ["ingest", "table2graph", "--pending"])

    assert result.exit_code != 0
    assert "invalid YAML" in result.output
