"""Views-first signal: did a turn check saved views before any other query?

artmind-query says `views list` comes right after Route, once per domain per
conversation. These tests drive `path.views_check` on synthetic spans and the
`triage` / `stats` wiring on a synthetic registry DB.
"""

import json
import time

import pytest

from artmind.traces import analysis, store
from artmind.traces.path import span_domains, views_check


def s(command, domains=None, *, rows=1, exit_code=0, at=0.0):
    args = {"domain": list(domains)} if domains is not None else {}
    return {"command": command, "args": args, "rows": rows, "exit_code": exit_code,
            "started_at": at, "evidence": {}}


# --- path.views_check ------------------------------------------------------


def test_route_then_views_then_retrieval_is_first():
    check = views_check([
        s("query domains-overview", at=0),
        s("query views list", ["b"], rows=3, at=1),
        s("query entity-context", ["b"], at=2),
    ])
    assert check["status"] == "first"
    assert check["views_available"] == 3 and check["view_ran"] is False


def test_retrieval_without_any_views_call_is_skipped():
    check = views_check([s("query domains-overview", at=0), s("query entity-resolve", ["b"], at=1)])
    assert check["status"] == "skipped" and check["views_available"] is None


def test_views_listed_after_another_query_is_late():
    check = views_check([s("query insights list", ["b"], at=0), s("query views list", ["b"], at=1)])
    assert check["status"] == "late"


def test_only_route_and_views_is_not_applicable():
    assert views_check([s("query domains-overview"), s("query views list", ["b"], at=1)])["status"] == "n/a"
    assert views_check([])["status"] == "n/a"


def test_a_second_unlisted_domain_is_skipped():
    check = views_check([s("query views list", ["b"], at=0), s("query vector-text", ["b", "hr"], at=1)])
    assert check["status"] == "skipped"


def test_listing_matches_the_domain_family_both_ways():
    assert views_check([s("query views list", ["banking"], at=0),
                        s("query chunks", ["banking.policy"], at=1)])["status"] == "first"
    assert views_check([s("query views list", ["banking.policy"], at=0),
                        s("query chunks", ["banking"], at=1)])["status"] == "first"
    assert views_check([s("query views list", ["bank"], at=0),
                        s("query chunks", ["banking"], at=1)])["status"] == "skipped"


def test_listing_without_domain_covers_every_domain():
    assert views_check([s("query views list", at=0), s("query chunks", ["b"], at=1)])["status"] == "first"


def test_views_run_counts_as_the_check_and_as_a_run():
    check = views_check([s("query views run", ["b"], at=0), s("query chunks", ["b"], at=1)])
    assert check["status"] == "first" and check["view_ran"] is True
    failed = views_check([s("query views run", ["b"], exit_code=1, at=0)])
    assert failed["view_ran"] is False


def test_listing_carried_from_an_earlier_turn():
    assert views_check([s("query chunks", ["b"])], {"b"})["status"] == "first"
    assert views_check([s("query chunks", ["b"])], listed_all_before=True)["status"] == "first"


def test_span_domains_reads_params_and_argv_and_comma_splits():
    assert span_domains({"args": {"domain": ["a,b", "c"]}}) == {"a", "b", "c"}
    assert span_domains({"args": ["views", "list", "--domain", "a", "--domain=b"]}) == {"a", "b"}
    assert span_domains({"args": {"domain": []}}) is None
    assert span_domains({"args": None}) is None


# --- triage / stats over a synthetic registry DB ---------------------------


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _turn(tid, *, started_at, session=None, domains=("b",)):
    store.insert_turn(tid, source="admin", question=f"q-{tid}", session_id=session, started_at=started_at)
    store.update_turn(tid, answer="ok", turns=1, fell_through=0, answer_basis="graph", graph_attempts=1,
                      graph_productive=1, domains=json.dumps(list(domains)), retrieval_path="[]", status="done")


def _span(tid, command, domains, *, rows=1, at):
    store.insert_span({"span_id": f"{tid}-{command}-{at}", "trace_id": tid, "command": command,
                       "args": json.dumps({"domain": list(domains), "compact": True}), "exit_code": 0,
                       "duration_ms": 1, "rows": rows, "rows_total": None, "truncated": 0, "evidence": "{}",
                       "started_at": at}, [])


def test_triage_surfaces_a_skipped_views_check():
    now = time.time()
    _turn("skip", started_at=now)
    _span("skip", "query entity-context", ["b"], at=now + 1)
    _turn("ok", started_at=now + 10)
    _span("ok", "query views list", ["b"], rows=0, at=now + 11)
    _span("ok", "query entity-context", ["b"], at=now + 12)

    items = {i["trace_id"]: i for i in analysis.triage()}
    assert items["skip"]["signals"] == {"views_skipped": 1}
    assert "ok" not in items  # unrated with no signals


def test_listing_reused_within_a_conversation_even_outside_the_since_window():
    now = time.time()
    _turn("t1", started_at=now - 7200, session="c1")
    _span("t1", "query views list", ["b"], at=now - 7199)
    _span("t1", "query entity-context", ["b"], at=now - 7198)
    _turn("t2", started_at=now, session="c1")
    _span("t2", "query entity-context", ["b"], at=now + 1)
    _turn("other", started_at=now, session="c2")
    _span("other", "query entity-context", ["b"], at=now + 1)

    flagged = {i["trace_id"] for i in analysis.triage(since=now - 60)}
    assert flagged == {"other"}


def test_stats_reports_views_first_rates():
    now = time.time()
    _turn("first", started_at=now)
    _span("first", "query views list", ["b"], rows=2, at=now + 1)
    _span("first", "query entity-context", ["b"], at=now + 2)
    _turn("ran", started_at=now)
    _span("ran", "query views list", ["b"], rows=2, at=now + 1)
    _span("ran", "query views run", ["b"], at=now + 2)
    _turn("late", started_at=now)
    _span("late", "query vector-text", ["b"], at=now + 1)
    _span("late", "query views list", ["b"], rows=0, at=now + 2)
    _turn("skip", started_at=now)
    _span("skip", "query graph pattern2", ["b"], at=now + 1)
    _turn("none", started_at=now)  # ran no query: not counted in the rates

    row = analysis.stats(by="all")[0]
    assert row["turns"] == 5
    assert row["views_first_rate"] == pytest.approx(2 / 4)
    assert row["views_late_rate"] == pytest.approx(1 / 4)
    assert row["views_skipped_rate"] == pytest.approx(1 / 4)
    assert row["view_run_rate"] == pytest.approx(1 / 5)
    assert row["views_listed_unused"] == 1


def test_an_empty_views_list_is_not_an_empty_rows_signal():
    turn = {"fell_through": 0, "answer_basis": "graph", "turns": 1, "answer": "ok"}
    spans = [s("query views list", ["b"], rows=0, at=0), s("query entity-context", ["b"], at=1)]
    assert analysis.signals(turn, spans, turns_p90=None) == {}


def test_a_plan_counts_as_the_views_check():
    from artmind.traces.path import views_check

    spans = [
        {"command": "query plan", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 0, "started_at": 1.0},
        {"command": "query vector-text", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 3, "started_at": 2.0},
    ]
    assert views_check(spans)["status"] == "first"
    no_domain = [{**spans[0], "args": {"domain": []}}, spans[1]]
    assert views_check(no_domain)["listed_all"] is True
