"""CLI-side capture: one span per `artmind query` call (spec §5.2, §5.3, §5.5)."""

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)


VECTOR_PAYLOAD = {
    "query_type": "vector_text", "question": "who", "rows": [
        {"score": 0.9, "chunk": {"id": "c1", "name": "c1", "doc_id": "d1", "text": "t"},
         "document": {"id": "d1", "name": "P", "path": "b/P.md", "source_path": "b/P.md"}}],
}


def _spans(trace_id="t1"):
    from artmind.traces import store

    return store.list_spans(trace_id)


def test_traced_query_records_command_params_and_evidence(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")

    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])

    assert result.exit_code == 0, result.output
    spans = _spans()
    assert len(spans) == 1
    span = spans[0]
    assert span["command"] == "query vector-text"
    assert span["args"]["domain"] == ["b"]
    assert span["args"]["question"] == "who"
    assert span["exit_code"] == 0
    assert span["rows"] == 1
    assert span["evidence"]["chunks"] == [{"chunk_id": "c1", "doc_id": "d1"}]
    assert span["evidence"]["documents"] == [{"doc_id": "d1", "path": "b/P.md"}]


def test_nested_group_command_path(monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": []})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "domains-overview"])
    assert [s["command"] for s in _spans()] == ["query domains-overview"]


def test_failed_command_still_records_a_span(monkeypatch):
    from artmind import cli as cli_mod

    def boom(*a, **k):
        raise ValueError("bad question")

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", boom)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    assert result.exit_code == 1
    span = _spans()[0]
    assert span["exit_code"] == 1
    assert span["rows"] == 0
    assert span["command"] == "query vector-text"
    assert span["args"] == ["vector-text", "--domain", "b", "who"]


def test_no_trace_id_writes_nothing_and_opens_no_db(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    opened = []
    monkeypatch.setattr(store, "_connect", lambda: opened.append(1))
    result = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    assert result.exit_code == 0
    assert opened == []


def test_help_is_not_spanned(monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--help"])
    assert _spans() == []


def test_capture_failure_leaves_output_and_exit_code_unchanged(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", lambda *a, **k: VECTOR_PAYLOAD)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "insert_span", broken)
    traced = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "--compact", "who"])
    monkeypatch.delenv("ARTMIND_TRACE_ID")
    plain = CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "--compact", "who"])
    assert traced.exit_code == plain.exit_code == 0
    assert traced.stdout == plain.stdout


def test_llm_calls_inside_a_span_are_recorded(monkeypatch):
    from artmind import cli as cli_mod
    from utils.functions import log_llm_call

    def fake_search(*a, **k):
        log_llm_call("chat", "m1", "PROMPT", "RESPONSE")
        return VECTOR_PAYLOAD

    monkeypatch.setattr(cli_mod.vector_query, "vector_text_search", fake_search)
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    CliRunner().invoke(cli_mod.cli, ["query", "vector-text", "--domain", "b", "who"])
    from artmind.traces import store

    span = _spans()[0]
    calls = store.list_llm_calls([span["span_id"]])[span["span_id"]]
    assert [(c["call_type"], c["model"], c["prompt"], c["response"]) for c in calls] == [
        ("chat", "m1", "PROMPT", "RESPONSE")]


def test_override_beats_environment(monkeypatch):
    from artmind.traces.span import current_trace_id, trace_override

    monkeypatch.setenv("ARTMIND_TRACE_ID", "from-env")
    with trace_override(None):
        assert current_trace_id() is None
    with trace_override("from-serve"):
        assert current_trace_id() == "from-serve"
    assert current_trace_id() == "from-env"
