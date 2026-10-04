"""A table ingested and committed on THIS machine is not "to apply"
(plan 2026-10-03, B6).

Uses the plugin-fixture vault: real git, a real registry and DuckDB, real
`ingest_structured_file` (which exports the table's CSV + .meta.json into the
vault), Neo4j in memory.
"""
from __future__ import annotations

import json

from test_plugin_fixtures import vault  # noqa: F401  (the fixture)

TEAM = "name,employer,level\nAnn Lee,Acme,senior\n"
CSV = ".artmind/data/structured_text/general/team.csv"
META = ".artmind/data/structured_text/general/team.meta.json"


def _stores(vault) -> dict:
    record = vault.run("vault", "status", "--compact")
    assert record["exit_code"] == 0, record
    return record["json"]["sync"]["stores"]


def _ingest_here(vault) -> None:
    from artmind.structured.pipeline import ingest_structured_file

    vault.sync_bootstrap()
    ingest_structured_file(vault.write("Team/team.csv", TEAM), "general")
    vault.commit("ingested team on this machine")


def test_a_table_ingested_and_committed_here_is_current(vault):
    _ingest_here(vault)

    stores = _stores(vault)

    assert (stores["structured"]["state"], stores["structured"]["tables"]) == ("current", [])
    assert (stores["graph"]["state"], stores["graph"]["tables"]) == ("current", [])


def test_rows_changed_on_the_other_machine_are_still_behind(vault):
    _ingest_here(vault)
    csv = vault.root / CSV
    csv.write_text(csv.read_text() + "Bo Chan,Acme,junior\n")
    vault.commit("laptop B added a row")

    stores = _stores(vault)

    assert stores["structured"]["tables"] == [["general", "team"]]
    assert stores["graph"]["tables"] == [["general", "team"]]


def test_a_curation_change_from_the_other_machine_is_still_behind_for_the_structured_store(vault):
    _ingest_here(vault)
    meta_path = vault.root / META
    meta = json.loads(meta_path.read_text())
    meta["table"]["grain"] = "lookup"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    vault.commit("laptop B: db grain --set lookup")

    assert _stores(vault)["structured"]["tables"] == [["general", "team"]]
