"""traces misses: loader and CLI over an invented registry (spec 2026-11-08 §4.1)."""

import json

import pytest
from click.testing import CliRunner

from artmind.traces import misses, store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import artmind.cli as cli_mod

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path / "logs")


def _turn(tid, question, answer, *, session=None, run_id=None, seq=None, spans=(), started=1_700_000_000.0):
    store.insert_turn(tid, source="benchmark" if run_id else "admin", question=question, session_id=session,
                      benchmark_run_id=run_id, benchmark_seq=seq, started_at=started)
    store.update_turn(tid, status="done", answer=answer)
    for n, s in enumerate(spans):
        store.insert_span({"span_id": f"{tid}-s{n}", "trace_id": tid, "command": s["command"],
                           "args": json.dumps(s.get("args", {})), "exit_code": 0, "duration_ms": 1,
                           "rows": s.get("rows", 1), "rows_total": None, "truncated": 0,
                           "evidence": json.dumps(s.get("evidence", {})), "started_at": started + n + 0.5}, [])


ANSWER = "Ten.\n\n### Sources\n- [f](notes/fees.md)\n"


def test_load_and_derive_reads_spans_feedback_and_the_follow_up():
    _turn("t1", "What fee applies?", ANSWER, session="c1",
          spans=[{"command": "query vector-text", "args": {"question": "returned debit charge"},
                  "evidence": {"documents": [{"doc_id": "d1", "path": "notes/fees.md"}]}}])
    _turn("t2", "You missed the weekend surcharge", "ok", session="c1", started=1_700_000_100.0)
    store.add_feedback("t1", "partial", "the cap", None)
    records, turns = misses.load_and_derive()
    kinds = sorted(m.kind for m in records if m.trace_id == "t1")
    assert kinds == ["user-stated", "user-stated", "vocabulary"]
    summary = misses.summarise(records, turns)
    assert summary["turns"] == 2 and summary["by_kind"]["vocabulary"] == 1
    assert set(summary["by_kind"]) == set(misses.KINDS)


def test_answer_misses_come_from_the_latest_grade_of_a_benchmark_turn():
    from artmind import benchmark

    qs = [{"seq": 1, "qid": "Q07", "title": "t", "question": "invented?", "eval_comment": "Must: a; b"}]
    rid = benchmark._create_benchmark_run("/tmp/q.md", qs, contract="classic")
    _turn("b1", "invented?", "a only", run_id=rid, seq=1)
    conn = store._connect()
    conn.execute("INSERT INTO trace_grades (key, run_id, seq, model, grade, detail, created_at) VALUES"
                 " ('k1', ?, 1, 'm', 'partial', ?, 1)",
                 (rid, json.dumps({"grade": "partial", "must_found": ["a"], "must_missed": ["b"],
                                   "must_not_hit": [], "reason": "r"})))
    conn.commit()
    conn.close()
    records, _ = misses.load_and_derive(run_refs=[rid[:8]])
    ans = [m for m in records if m.kind == "answer"]
    assert [(m.qid, m.run_id, m.detail["item"]) for m in ans] == [("Q07", rid, "b")]


def test_cli_writes_records_and_prints_counts_only(tmp_path):
    from artmind.cli import cli

    _turn("t1", "A private question about Person Z?", ANSWER,
          spans=[{"command": "query vector-text", "args": {"question": "secret phrase words"},
                  "evidence": {"documents": [{"doc_id": "d1", "path": "notes/fees.md"}]}}])
    result = CliRunner().invoke(cli, ["traces", "misses", "--compact"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["command"] == "traces misses" and out["by_kind"]["vocabulary"] == 1
    for private in ("Person Z", "secret", "notes/fees.md"):
        assert private not in result.output
    written = list((tmp_path / "logs" / "misses").glob("misses-*.jsonl"))
    assert len(written) == 1 and out["records_file"] == str(written[0])
    lines = [json.loads(line) for line in written[0].read_text().splitlines()]
    assert lines[0]["kind"] == "vocabulary" and "secret" in lines[0]["detail"]["phrase"]


def test_cli_rejects_an_unknown_run():
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["traces", "misses", "--run", "nope"])
    assert result.exit_code == 1 and "no benchmark run" in result.output
