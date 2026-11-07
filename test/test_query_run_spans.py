"""Nested spans: a traced `query run` is the parent of one span per executed step (P3 spec §7.1)."""

import json

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _plan(tmp_path):
    plan = {"program": [{"id": "p", "kind": "par", "children": [
        {"id": "a", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True},
        {"id": "b", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True}]}],
        "guards": [], "route": {"id": "inventory"}, "replacements": {}, "suggested_steps": [], "fallbacks": []}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_a_traced_run_has_child_spans(tmp_path, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    out = CliRunner().invoke(cli_mod.cli, ["query", "run", "--plan", str(_plan(tmp_path)), "--compact"])
    assert out.exit_code == 0, out.output
    spans = store.list_spans("t1")
    run = next(s for s in spans if s["command"] == "query run")
    children = [s for s in spans if s["parent_span_id"] == run["span_id"]]
    assert [s["command"] for s in children] == ["query domains-overview"] * 2
    assert run["parent_span_id"] is None and run["run_mode"] == "reasoning"
    assert run["bundle_chars"] and run["escalate_reason"] is None
    assert all(s["rows"] == 1 for s in children)


def test_a_plain_nested_invocation_keeps_todays_behaviour(monkeypatch):
    """Outside capture, a nested invocation still runs inside its parent's span (no child)."""
    from artmind.traces import span as span_mod

    assert span_mod._child_wanted() is False


def test_a_db_step_gets_a_child_span_too(tmp_path, monkeypatch):
    """TracedDbGroup subclasses TracedGroup, so a read-only db step under a traced run is a child span."""
    from artmind import cli as cli_mod
    from artmind.traces import store

    store.insert_turn("t2", source="admin", question="q")
    monkeypatch.setattr(cli_mod.structured_registry, "list_tables", lambda domains=None: [{"table_name": "widgets"}])
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t2")
    plan = {"program": [{"id": "a", "kind": "run", "run": "artmind db list --compact", "filled": True}],
            "guards": [], "route": {"id": "inventory"}, "replacements": {}, "suggested_steps": [], "fallbacks": []}
    path = tmp_path / "dbplan.json"
    path.write_text(json.dumps(plan))
    out = CliRunner().invoke(cli_mod.cli, ["query", "run", "--plan", str(path), "--compact"])
    assert out.exit_code == 0, out.output
    spans = store.list_spans("t2")
    run = next(s for s in spans if s["command"] == "query run")
    assert [s["command"] for s in spans if s["parent_span_id"] == run["span_id"]] == ["db list"]
