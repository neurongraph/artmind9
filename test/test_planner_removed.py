"""The planner is gone (spec 2026-11-08 §5, phase 3): no package, no import, no command; traces stored while it
existed still render."""

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.traces import analysis, path, store

ROOT = Path(__file__).resolve().parent.parent / "artmind"


def test_no_query_plan_package_or_import():
    assert not (ROOT / "query_plan").exists()
    for p in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("artmind.query_plan"), p
                assert not (node.module == "artmind" and any(a.name == "query_plan" for a in node.names)), p
            if isinstance(node, ast.Import):
                assert not any(a.name.startswith("artmind.query_plan") for a in node.names), p


@pytest.mark.parametrize("argv", [["query", "plan", "--help"], ["plan", "--help"]])
def test_no_plan_commands(argv):
    assert CliRunner().invoke(cli, argv).exit_code == 2


@pytest.fixture
def _db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def test_a_stored_query_plan_span_and_shadow_event_still_render(_db):
    tid = store.new_trace_id()
    store.insert_turn(tid, source="admin", question="q")
    store.insert_events(tid, [{"seq": 0, "ts": 1.0, "type": "plan_shadow", "tool_id": None, "parent_tool_id": None,
                               "name": "query plan", "payload": json.dumps({"status": "ok", "plan": {}})}])
    for i, command in enumerate(("query plan", "query survey")):
        store.insert_span({"span_id": f"s{i}", "trace_id": tid, "command": command, "args": "{}", "exit_code": 0,
                           "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                           "evidence": json.dumps({"view": "v"} if i == 0 else {}), "started_at": float(i)}, [])
    shown = analysis.show(tid)
    assert [s["command"] for s in shown["unattributed_spans"]] == ["query plan", "query survey"]
    spans = store.list_spans(tid)
    assert path.span_layer(spans[0]) == "discovery"  # a plan located things; it never answered
    path.derive(spans, "answer")
    path.views_check(spans)
