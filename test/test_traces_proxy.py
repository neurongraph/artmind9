"""Trace id resolution and forwarding through the `serve` proxy (spec §5.2, §5.4)."""

import io
import json

from artmind import _entry


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
