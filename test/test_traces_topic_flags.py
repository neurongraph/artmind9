"""`traces compare` flags a benchmark turn that ran a topic plan matching its question
(spec 2026-11-10 §10). Synthetic questions."""
import json
import sqlite3

from artmind.traces import compare as cmp
from artmind.traces import topic_flags

Q = "What do my notes say about garden work and how has it changed over the years?"


def test_matches_is_equality_or_long_containment():
    assert topic_flags.matches(Q, [Q.upper()])
    assert topic_flags.matches(Q, ["garden work and how has it changed over the years"])  # 40+ characters inside
    assert not topic_flags.matches(Q, ["garden work"])  # too short to count as containment
    assert not topic_flags.matches(Q, ["compost heap", ""])


def _conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trace_spans (trace_id TEXT, command TEXT, args TEXT)")
    conn.executemany("INSERT INTO trace_spans VALUES (?, ?, ?)", rows)
    return conn


def test_slugs_come_from_query_topics_run_spans_only():
    conn = _conn([("t1", "query topics run", json.dumps({"slug": "garden"})),
                  ("t1", "query topics run", json.dumps({"slug": "garden"})),
                  ("t1", "query survey", json.dumps({"question": "x"})),
                  ("t2", "query topics run", json.dumps({"slug": "other"}))])
    assert topic_flags.slugs_run(conn, "t1") == ["garden"]


def test_flags_name_run_and_qid(monkeypatch):
    rows = [("t1", "query topics run", json.dumps({"slug": "from-question"})),
            ("t2", "query topics run", json.dumps({"slug": "own-words"}))]
    monkeypatch.setattr("artmind.traces.store._connect", lambda: _conn(rows))
    run = cmp.Run(run_id="r1", name=None, contract="orient", source_path="", status="completed",
                  questions={1: {"qid": "Q01", "question": Q}, 2: {"qid": "Q02", "question": Q}})
    run.answers.update({1: cmp.Answer(1, "a", "t1", None, None, None, 1, ()),
                        2: cmp.Answer(2, "a", "t2", None, None, None, 1, ())})
    texts = {"from-question": [Q], "own-words": ["Garden", "tomato planting"]}
    assert topic_flags.flags([run], texts.get) == [{"run": "r1", "seq": 1, "qid": "Q01", "slug": "from-question"}]
