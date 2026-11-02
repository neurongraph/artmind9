"""`ingest table2graph --dryRun`: builds and counts from reads, stages and commits nothing."""
import json

import pytest
from click.testing import CliRunner

import artmind.ingest as ingest
import artmind.table2graph as t2g
from _dry_run import assert_effect_shape
from artmind.cli import cli
from test_table2graph import cli_env  # noqa: F401  (the fixture: mapping, table row, rows, schema)


def _run(*args):
    result = CliRunner().invoke(cli, ["ingest", "table2graph", "hr_export_20260921", *args, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output.strip().splitlines()[-1])


def test_dry_run_commits_nothing_and_matches_the_real_effect(cli_env, monkeypatch):
    committed = []

    def fake_write(directory, domain, defer_rebuild=False):
        committed.append(directory)
        return {"chunks": 6, "observations": 13, "relationships": 12, "deferred_keys": [],
                "unembedded_chunk_ids": []}

    monkeypatch.setattr(ingest, "_write_to_neo4j", fake_write)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys: {"keys": len(keys)})

    dry = _run("--dryRun")

    assert committed == [] and not (cli_env / "kg").exists()
    assert dry["dry_run"] is True
    effect = dry["effect"]
    assert_effect_shape(effect)
    report = dry["tables"][0]
    kinds = {c["kind"]: c for c in effect["changes"]}
    assert kinds["table_change"] == {"kind": "table_change", "count": 1, "sample": ["hr_export_20260921"]}
    assert kinds["fact_write"]["count"] == report["observations"] + report["relationship_observations"]
    assert effect["records"] == [str(cli_env / "kg" / "perf" / "table__hr_export_20260921")]
    assert effect["llm"] == {"calls": 0, "model": None, "est_cost_usd": None}

    real = _run("--noEmbed")

    assert committed == [cli_env / "kg" / "perf" / "table__hr_export_20260921"]
    assert "dry_run" not in real
    assert real["effect"] == effect
