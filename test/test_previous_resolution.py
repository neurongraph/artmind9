"""Resolving the previous turn: --previous, the previous-context override/file, then the trace store.
Synthetic names only."""

import json
import time

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_plan import intents, previous
from artmind.traces import store
from artmind.traces.span import trace_override

T1, T2 = "a" * 16, "b" * 16


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.delenv(previous.PREVIOUS_CONTEXT_ENV, raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _file(tmp_path, name, question):
    p = tmp_path / name
    p.write_text(json.dumps({"question": question}))
    return str(p)


def _sessions():
    now = time.time()
    store.insert_turn(T1, source="admin", question="from trace", session_id="s1", started_at=now)
    store.insert_turn(T2, source="admin", question="now", session_id="s1", started_at=now + 5)


def _span(span_id, docs, parent=None, command="query vector-text", started=1.0):
    return {"span_id": span_id, "trace_id": T1, "command": command, "args": "{}", "exit_code": 0,
            "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
            "evidence": json.dumps({"documents": [{"doc_id": d} for d in docs]}),
            "started_at": started, "parent_span_id": parent}


def test_file_wins_over_everything(tmp_path, monkeypatch):
    _sessions()
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, _file(tmp_path, "env.json", "from env"))
    with previous.previous_override(json.dumps({"question": "from override"})):
        got = previous.resolve_previous(_file(tmp_path, "f.json", "from file"), trace_id=T2)
    assert got.question == "from file"


def test_file_errors_still_raise(tmp_path):
    with pytest.raises(intents.IntentError):
        previous.resolve_previous(str(tmp_path / "missing.json"))


def test_override_wins_over_env_and_trace(tmp_path, monkeypatch):
    _sessions()
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, _file(tmp_path, "env.json", "from env"))
    with previous.previous_override(json.dumps({"question": "from override"})):
        assert previous.resolve_previous(None, trace_id=T2).question == "from override"


def test_override_none_outside_serve_reads_env_file(tmp_path, monkeypatch):
    _sessions()
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, _file(tmp_path, "env.json", "from env"))
    assert previous.resolve_previous(None, trace_id=T2).question == "from env"


def test_override_empty_text_means_no_context_source_even_with_env(tmp_path, monkeypatch):
    # serve never reads its own env; an empty override falls through to the trace
    _sessions()
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, _file(tmp_path, "env.json", "from env"))
    with previous.previous_override(""):
        assert previous.resolve_previous(None, trace_id=T2).question == "from trace"


@pytest.mark.parametrize("content", ["", "   \n", "not json", "[1]", '{"intent": "nonsense"}'])
def test_bad_context_falls_through_to_trace(tmp_path, monkeypatch, content):
    _sessions()
    p = tmp_path / "ctx.json"
    p.write_text(content)
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, str(p))
    assert previous.resolve_previous(None, trace_id=T2).question == "from trace"


def test_missing_context_file_falls_through(tmp_path, monkeypatch):
    _sessions()
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, str(tmp_path / "gone.json"))
    assert previous.resolve_previous(None, trace_id=T2).question == "from trace"


def test_trace_is_last_and_store_errors_are_none(monkeypatch):
    _sessions()
    assert previous.resolve_previous(None, trace_id=T2).question == "from trace"
    assert previous.resolve_previous(None, trace_id=None) is None
    monkeypatch.setattr(previous, "previous_from_trace", lambda t: (_ for _ in ()).throw(RuntimeError("db")))
    assert previous.resolve_previous(None, trace_id=T2) is None


def test_documents_from_top_level_spans_run_first_and_children_ignored():
    _sessions()
    store.insert_span(_span("a1", ["d1", "d2"], started=1.0), [])
    store.insert_span(_span("r1", ["d9", "d1"], command="query run", started=2.0), [])
    store.insert_span(_span("c1", ["child"], parent="r1", started=3.0), [])
    got = previous.previous_from_trace(T2)
    assert got.documents == ("d9", "d1", "d2")


def test_documents_capped_at_twenty():
    _sessions()
    store.insert_span(_span("a1", [f"d{i}" for i in range(30)]), [])
    assert len(previous.previous_from_trace(T2).documents) == 20


@pytest.mark.parametrize("args", [["plan"], ["run"]])
def test_plan_and_run_pass_the_resolved_previous(monkeypatch, tmp_path, args):
    from artmind.query_plan import planner

    seen = {}

    def fake_plan(question, domains=None, **kw):
        seen.update(kw)
        raise ValueError("stop here")

    monkeypatch.setattr(planner, "plan", fake_plan)
    monkeypatch.setenv(previous.PREVIOUS_CONTEXT_ENV, _file(tmp_path, "env.json", "from env"))
    CliRunner().invoke(cli, ["query", *args, "hello"])
    assert seen["previous"].question == "from env"
    seen.clear()
    CliRunner().invoke(cli, ["query", *args, "--previous", _file(tmp_path, "f.json", "explicit"), "hello"])
    assert seen["previous"].question == "explicit"


def test_plan_uses_the_trace_id_in_force(monkeypatch):
    from artmind.query_plan import planner

    _sessions()
    seen = {}
    monkeypatch.setattr(planner, "plan", lambda q, d=None, **kw: seen.update(kw) or (_ for _ in ()).throw(ValueError("x")))
    with trace_override(T2):
        CliRunner().invoke(cli, ["query", "plan", "hello"])
    assert seen["previous"].question == "from trace"


def test_bad_previous_file_is_a_click_error(tmp_path):
    result = CliRunner().invoke(cli, ["query", "plan", "--previous", str(tmp_path / "nope.json"), "hello"])
    assert result.exit_code == 1


def test_context_json_carries_plan_offer_and_documents():
    _sessions()
    store.insert_events(T1, [{"seq": 1, "ts": 1.0, "type": "plan_shadow", "payload": json.dumps({"status": "ok", "plan": {
        "intent": {"id": "retrieve"}, "route": {"id": "enumeration"},
        "entities": [{"candidates": [{"_id": "E1", "name": "Ada"}]}]}})}])
    store.insert_span(_span("a1", ["d1"]), [])
    turns = [{"status": "done", "user_text": "List trips", "answer_text": "Four. Shall I group them?", "trace_id": T1},
             {"status": "error", "user_text": "boom", "answer_text": None, "trace_id": None}]
    got = intents.parse_previous(previous.context_json(turns))
    assert (got.question, got.intent, got.route, got.offer) == ("List trips", "retrieve", "enumeration",
                                                                 "Shall I group them?")
    assert got.entities[0].id == "E1" and got.documents == ("d1",)
    assert previous.context_json([]) == ""


def test_create_backend_passes_the_previous_context_env(monkeypatch, tmp_path):
    import paths

    from artmind.webui import backends

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    path = backends.new_previous_context()
    assert path.parent == tmp_path / "previous-context" and path.read_text() == ""
    seen = {}

    class Fake:
        def __init__(self, *a, **k):
            seen.update(k)

    monkeypatch.setattr("artmind.webui.backends.claude_sdk.ClaudeSDKBackend", Fake)
    backends.create_backend("claude-sdk", previous_context=path)
    assert seen["env"]["ARTMIND_PREVIOUS_CONTEXT"] == str(path) and seen["previous_context"] == path
