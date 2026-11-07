"""traces stats: the run_shadow block (P3 spec §7.2)."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _turn(tid, run_shadow):
    store.insert_turn(tid, source="benchmark", question="q", started_at=1_700_000_000.0)
    store.update_turn(tid, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    store.insert_events(tid, [{"seq": 0, "ts": 1.0, "type": "run_shadow", "payload": json.dumps(run_shadow)}])


def test_the_run_shadow_block():
    _turn("t1", {"status": "ok", "route": "search", "level": "low", "program_kind": "cross_document",
                 "timing": {"exec_ms": 800}, "escalate": None, "covers_agent": 1.0})
    _turn("t2", {"status": "ok", "route": "search", "level": "high", "program_kind": "cross_document",
                 "timing": {"exec_ms": 1200}, "escalate": "no_evidence", "covers_agent": 0.5})
    _turn("t3", {"status": "timeout"})
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    block = row["run_shadow"]
    assert block["count"] == 3 and block["statuses"] == {"ok": 2, "timeout": 1}
    assert block["exec_ms_p90"] == {"cross_document": 1200}
    assert block["escalation_rate"] == {"search": 0.5}
    assert block["covers_agent"] == {"search": {"low": 1.0, "high": 0.5}}


def test_no_run_shadow_keeps_the_same_keys():
    store.insert_turn("t9", source="admin", question="q", started_at=1_700_000_000.0)
    store.update_turn("t9", status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["run_shadow"] == {"count": 0, "statuses": {}, "exec_ms_p90": {}, "escalation_rate": {}, "covers_agent": {}}
