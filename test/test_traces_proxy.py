"""Trace id resolution and forwarding through the `serve` proxy (spec §5.2, §5.4)."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from artmind import _entry


@pytest.fixture
def _db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")


def test_trace_id_prefers_explicit_env(monkeypatch, tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("from-file")
    monkeypatch.setenv("ARTMIND_TRACE_ID", "explicit")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() == "explicit"


def test_trace_id_reads_context_file(monkeypatch, tmp_path):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("abc123\n")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() == "abc123"


def test_empty_or_missing_context_file_means_no_trace(monkeypatch, tmp_path):
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    ctx = tmp_path / "ctx.id"
    ctx.write_text("")
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    assert _entry._trace_id() is None
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(tmp_path / "missing.id"))
    assert _entry._trace_id() is None
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT")
    assert _entry._trace_id() is None


def test_proxy_payload_carries_trace_id(monkeypatch, tmp_path, capsys):
    ctx = tmp_path / "ctx.id"
    ctx.write_text("t-42")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.setenv("ARTMIND_TRACE_CONTEXT", str(ctx))
    sent = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        sent["body"] = json.loads(request.data)
        return _Resp(json.dumps({"exit_code": 0, "output": "{}\n", "stderr": ""}).encode())

    monkeypatch.setattr(_entry.urllib.request, "urlopen", fake_urlopen)
    assert _entry._proxy("http://127.0.0.1:1", ["query", "chunks"]) == 0
    assert sent["body"] == {"args": ["query", "chunks"], "trace_id": "t-42"}


def test_serve_records_span_under_forwarded_trace_id(_db, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.server import create_app
    from artmind.traces import store
    from artmind.traces.span import current_trace_id

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": [{"domain": "b"}]})
    client = TestClient(create_app())
    resp = client.post("/query", json={"args": ["query", "domains-overview"], "trace_id": "t-serve"})
    assert resp.status_code == 200
    assert resp.json()["exit_code"] == 0
    spans = store.list_spans("t-serve")
    assert [(s["command"], s["rows"]) for s in spans] == [("query domains-overview", 1)]
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)
    assert current_trace_id() is None  # override cleared after the request


def test_serve_ignores_its_own_environment(_db, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.server import create_app
    from artmind.traces import store

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda: {"rows": []})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "daemon-env")
    TestClient(create_app()).post("/query", json={"args": ["query", "domains-overview"]})
    assert store.list_spans("daemon-env") == []
