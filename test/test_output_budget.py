"""ARTMIND_OUTPUT_BUDGET: cut, result handles, `query page`, proxy forwarding, span fields."""
import json
import os
import time

import pytest
from click.testing import CliRunner

from artmind import result_store
from artmind.cli import _echo_json, cli


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    import artmind.cli as cli_mod

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    return tmp_path / "logs" / "results"


def _payload(n=50, size=400):
    return {"documents": [{"id": i, "text": "x" * size} for i in range(n)], "note": "ok"}


def _dumps(p):
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"))


def test_within_budget_unchanged(tmp_path):
    p = _payload(2, 10)
    out, n, shown, handle = result_store.fit(p, 100000, _dumps, "documents", tmp_path)
    assert out is p and handle is None and n == shown


def test_cut_and_round_trip(tmp_path):
    p = _payload()
    out, n, shown, handle = result_store.fit(p, 8000, _dumps, "documents", tmp_path)
    assert result_store.is_handle(handle)
    assert shown <= 8000 < n
    assert out["truncated"] is True and out["handle"] == handle
    assert out["rows_total"] == 50 and out["rows_shown"] == len(out["documents"]) < 50
    assert out["next"] == f"artmind query page {handle} --from {out['rows_shown']}"
    assert f"showing {out['rows_shown']} of 50 documents" in out["warnings"][-1]
    assert out["note"] == "ok"
    assert result_store.load(tmp_path, handle) == p


def test_fallback_key_and_long_string_shortening(tmp_path):
    p = {"rows": [], "blob": "y" * 5000}
    out, *_ = result_store.fit(p, 3000, _dumps, None, tmp_path)
    assert len(out["blob"]) == 2001 and out["blob"].endswith("…")
    assert any("blob" in w for w in out["warnings"])
    q = {"other": "z" * 9000}
    out, *_ = result_store.fit(q, 3000, _dumps, None, tmp_path)
    assert out["truncated"] and out["next"].startswith("artmind query page ")


def test_bad_budget():
    for bad in ("abc", "-3", "1.5"):
        with pytest.raises(result_store.BudgetError, match="ARTMIND_OUTPUT_BUDGET"):
            result_store.parse_budget(bad)
    assert result_store.parse_budget(None) == 0 and result_store.parse_budget("0") == 0


def test_bad_env_is_click_exception(monkeypatch):
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "lots")
    result = CliRunner().invoke(cli, ["query", "page", "0123456789ab"])
    assert result.exit_code == 1


def test_page_command_pages_and_rejects_bad_handle(_home):
    handle = result_store.store(_home, {"command": "query survey", "documents": list(range(10)), "warnings": [],
                                        "next": None, "truncated": True, "handle": "x", "rows_shown": 3}, _dumps)
    r = CliRunner().invoke(cli, ["query", "page", handle, "--from", "3", "--rows", "4", "--compact"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)
    assert out["documents"] == [3, 4, 5, 6] and out["rows_total"] == 10 and out["truncated"] is True
    assert out["next"] == f"artmind query page {handle} --from 7"
    r = CliRunner().invoke(cli, ["query", "page", handle, "--from", "8", "--compact"])
    out = json.loads(r.stdout)
    assert out["documents"] == [8, 9] and out["next"] is None and out["truncated"] is False
    for bad in ("../../etc/passwd", "ABCDEF123456", "abc", "0123456789abc"):
        r = CliRunner().invoke(cli, ["query", "page", bad])
        assert r.exit_code == 1
    r = CliRunner().invoke(cli, ["query", "page", "0" * 12])
    assert r.exit_code == 1 and "no stored result" in r.output


def test_echo_json_applies_budget(monkeypatch, _home, capsys):
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "5000")
    _echo_json(_payload(), compact=True)
    out = json.loads(capsys.readouterr().out)
    assert out["truncated"]
    assert (_home / f"{out['handle']}.json").exists()


def test_old_results_pruned_on_write(_home):
    h = result_store.store(_home, {"a": 1}, _dumps)
    old = time.time() - 25 * 3600
    os.utime(_home / f"{h}.json", (old, old))
    result_store.store(_home, {"b": 2}, _dumps)
    assert not (_home / f"{h}.json").exists()
    assert result_store.prune_files(_home, None, dry_run=True) == 1
    assert len(list(_home.glob("*.json"))) == 1


def test_traces_prune_removes_results(tmp_path, _home, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    result_store.store(_home, {"a": 1}, _dumps)
    r = CliRunner().invoke(cli, ["traces", "prune", "--dryRun", "--compact"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["results_pruned"] == 1 and len(list(_home.glob("*.json"))) == 1
    r = CliRunner().invoke(cli, ["traces", "prune", "--compact"])
    assert r.exit_code == 0 and not list(_home.glob("*.json"))


def test_proxy_forwards_budget_and_server_applies_it_per_request(monkeypatch):
    from artmind import _entry, server

    sent = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"output": "", "exit_code": 0}).encode()

    def fake_urlopen(req, timeout=None):
        sent.update(json.loads(req.data))
        return Resp()

    monkeypatch.setattr(_entry.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "1234")
    _entry._proxy("http://x", ["query", "domains-overview"])
    assert sent["output_budget"] == "1234"

    # the daemon's own env is ignored; the request's value is what _echo_json sees
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "99")
    with result_store.budget_override("1234"):
        assert result_store.current_budget() == 1234
    with result_store.budget_override(None):
        assert result_store.current_budget() == 0
    assert result_store.current_budget() == 99
    assert server.QueryRequest(args=["query"]).output_budget is None


def test_span_fields_recorded(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind.traces import store

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    store.insert_turn("t1", source="admin", question="q", started_at=time.time())
    store.insert_span({
        "span_id": "s1", "trace_id": "t1", "command": "query survey", "args": "[]", "exit_code": 0,
        "duration_ms": 1, "rows": 3, "rows_total": 50, "truncated": 1, "evidence": "{}",
        "started_at": time.time(), "payload_chars": 20000, "shown_chars": 15000, "handle": "0123456789ab",
    }, [])
    conn = store._connect()
    span = dict(conn.execute("SELECT * FROM trace_spans WHERE span_id='s1'").fetchone())
    conn.close()
    assert (span["payload_chars"], span["shown_chars"], span["handle"]) == (20000, 15000, "0123456789ab")
