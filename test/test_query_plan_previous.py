"""The previous turn rebuilt from traces, `follows:` threads in replay, the decision signature and shadow.
Synthetic names and questions only."""

import json
import time
from pathlib import Path

import pytest

from artmind.query_plan import labels as labels_mod
from artmind.query_plan import planner, previous, replay, shadow
from artmind.query_plan.intents import Previous
from artmind.query_plan.labels import Label
from artmind.traces import store

T1, T2, T3, T4 = "a" * 16, "b" * 16, "c" * 16, "d" * 16


@pytest.fixture(autouse=True)
def _registry(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.delenv("ARTMIND_PLAN_SHADOW", raising=False)


def _turn(trace_id, session, question, started, answer=None, plan=None):
    store.insert_turn(trace_id, source="admin", question=question, session_id=session, started_at=started)
    if answer is not None:
        store.update_turn(trace_id, answer=answer)
    if plan is not None:
        store.insert_events(trace_id, [{"seq": 1, "ts": started, "type": "plan_shadow",
                                        "payload": json.dumps({"status": "ok", "plan": plan})}])


def _plan_of(intent="retrieve", route="enumeration", ents=(("E1", "Ada"),)):
    return {"intent": {"id": intent}, "route": {"id": route}, "entities": [
        {"mention": n, "candidates": [{"_id": i, "name": n}, {"_id": i + "x", "name": n + "2"}]} for i, n in ents]}


def _sessions():
    now = time.time()
    _turn(T1, "s1", "List the trips Ada took", now, "Here are four trips. Shall I group them by year?",
          _plan_of())
    _turn(T2, "s1", "yes please", now + 10)
    _turn(T3, "s2", "Who is Bo?", now + 5, "Bo is a neighbour.")
    _turn(T4, "s2", "and the sister?", now + 20)


# ── offers ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("answer,offer", [
    ("Four trips found. Shall I group them by year?", "Shall I group them by year?"),
    ("Done.\n\nWould you like a table of this? ", "Would you like a table of this?"),
    ("Three found. Do you want me to open the first one? Otherwise that is all.",
     "Do you want me to open the first one?"),
    ("Did you also visit the lake? It was a wet year.", None),
    ("Result.\nWant me to save it as a view?", "Want me to save it as a view?"),
    ("Done. Should we re-run it? Shall I start with 2025?", "Shall I start with 2025?"),
    ("Shall I begin?" + " x" * 400, None),
    ("", None), (None, None),
])
def test_offer_extraction(answer, offer):
    assert previous.extract_offer(answer) == offer


# ── previous_from_trace ─────────────────────────────────────────────────────


def test_previous_from_trace_uses_the_latest_earlier_turn_of_the_same_session():
    _sessions()
    got = previous.previous_from_trace(T2)
    assert got == Previous(intent="retrieve", route="enumeration", question="List the trips Ada took",
                           offer="Shall I group them by year?", entities=got.entities)
    assert [(e.id, e.name) for e in got.entities] == [("E1", "Ada")]
    assert got.documents == () and got.open_job is None
    other = previous.previous_from_trace(T4)  # the other session: no plan_shadow event, no offer
    assert (other.question, other.intent, other.route, other.offer, other.entities) == ("Who is Bo?", None, None, None, ())


def test_the_first_turn_of_a_session_and_an_unknown_trace_have_none():
    _sessions()
    assert previous.previous_from_trace(T1) is None
    assert previous.previous_from_trace(T3) is None
    assert previous.previous_from_trace("f" * 16) is None


def test_a_turn_without_a_session_has_no_previous():
    _turn(T1, None, "q", time.time())
    assert previous.previous_from_trace(T1) is None


def test_previous_from_plan():
    got = previous.previous_from_plan("  q  ", _plan_of("meta", "search", (("E1", "Ada"), ("E2", "Bo"))),
                                      "Done. Shall I go on?")
    assert (got.question, got.intent, got.route, got.offer) == ("q", "meta", "search", "Shall I go on?")
    assert [e.id for e in got.entities] == ["E1", "E2"]
    assert previous.previous_from_plan("q", None) == Previous(question="q")


# ── labels: follows ─────────────────────────────────────────────────────────


def _doc(*specs):
    return {"version": 1, "labels": [
        {"id": i, "question": f"question {i}", "source": src, "domains": ["journal"], "route": "search", **extra}
        for i, src, extra in specs]}


def test_follows_parses_and_is_validated():
    out = labels_mod.parse_labels(_doc(("L001", "written", {}), ("L002", "written", {"follows": "L001"}),
                                       ("L003", "written", {"follows": "none"})), Path("."), view_params={})
    assert [lb.follows for lb in out] == [None, "L001", "none"]
    with pytest.raises(labels_mod.LabelError, match="follows 'L009' is not a label id"):
        labels_mod.parse_labels(_doc(("L001", "written", {"follows": "L009"})), Path("."), view_params={})
    with pytest.raises(labels_mod.LabelError, match="follows must be a label id or none"):
        labels_mod.parse_labels(_doc(("L001", "written", {"follows": "yes"})), Path("."), view_params={})


def test_a_cycle_in_follows_is_an_error():
    data = _doc(("L001", "written", {"follows": "L002"}), ("L002", "written", {"follows": "L003"}),
                ("L003", "written", {"follows": "L001"}), ("L004", "written", {"follows": "L004"}))
    with pytest.raises(labels_mod.LabelError) as exc:
        labels_mod.parse_labels(data, Path("."), view_params={})
    text = str(exc.value)
    assert "follows forms a cycle" in text and "L004 -> L004" in text and "L001" in text


# ── replay threads ──────────────────────────────────────────────────────────


def _label(lid, source="written", follows=None, question=None):
    return Label(id=lid, question=question or f"question {lid}", source=source, domains=("journal",),
                 route="search", view=None, params={}, follows=follows)


def _plan(route="search", intent="retrieve", level="high", **over):
    return {"domains": {"selected": ["journal"]}, "route": {"id": route, "confidence": 0.9},
            "intent": {"id": intent, "confidence": 0.9}, "confidence": {"level": level, "score": 0.8},
            "classifier": {"latency_ms": 1.0, "fell_back": False}, "entities": [], **over}


def _recorder():
    calls = []

    def plan_fn(question, domains, classifier="lexical", today=None, **kw):
        calls.append((question, kw.get("previous")))
        return _plan(entities=[{"candidates": [{"_id": "E-" + question.split()[-1], "name": "N"}]}])

    return calls, plan_fn


def test_a_follower_is_planned_after_the_label_it_follows_and_gets_that_plan():
    calls, plan_fn = _recorder()
    labels = [_label("L001", follows="L002"), _label("L002")]
    report = replay.replay(labels, "lexical", plan_fn=plan_fn, threads=None)
    assert [q for q, _ in calls] == ["question L002", "question L001"]
    assert calls[0][1] is None
    prev = calls[1][1]
    assert (prev.question, prev.route, prev.intent) == ("question L002", "search", "retrieve")
    assert [(e.id, e.name) for e in prev.entities] == [("E-L002", "N")]
    assert report["threaded"] == 1
    assert [r["id"] for r in report["results"]] == ["L001", "L002"]  # reported in file order


def test_chains_pass_the_planners_own_output_down_each_link():
    calls, plan_fn = _recorder()
    labels = [_label("L003", follows="L002"), _label("L002", follows="L001"), _label("L001")]
    report = replay.replay(labels, "lexical", plan_fn=plan_fn)
    assert [q for q, _ in calls] == ["question L001", "question L002", "question L003"]
    assert [p.question if p else None for _, p in calls] == [None, "question L001", "question L002"]
    assert report["threaded"] == 2


def test_follows_none_plans_alone_even_with_a_trace_session():
    _sessions()
    calls, plan_fn = _recorder()
    labels = [_label("L001", source=f"trace:{T1}"), _label("L002", source=f"trace:{T2}", follows="none")]
    report = replay.replay(labels, "lexical", plan_fn=plan_fn)
    assert [p for _, p in calls] == [None, None] and report["threaded"] == 0


def test_a_trace_label_follows_the_label_of_the_previous_turn():
    _sessions()
    calls, plan_fn = _recorder()
    labels = [_label("L001", source=f"trace:{T2}", question="yes please"),
              _label("L002", source=f"trace:{T1}", question="List the trips")]
    threads = replay.resolve_threads(labels)
    assert threads == {"L001": ("label", "L002"), "L002": None}
    replay.replay(labels, "lexical", plan_fn=plan_fn)
    assert [q for q, _ in calls] == ["List the trips", "yes please"]
    prev = calls[1][1]
    assert prev.question == "List the trips" and prev.offer == "Shall I group them by year?"  # answer from the trace
    assert prev.entities and prev.entities[0].id == "E-trips"  # the plan's output, not the recorded one


def test_a_trace_label_without_a_matching_label_uses_the_recorded_turn_directly():
    _sessions()
    calls, plan_fn = _recorder()
    report = replay.replay([_label("L001", source=f"trace:{T2[:10]}", question="yes please")], "lexical",
                           plan_fn=plan_fn)
    prev = calls[0][1]
    assert prev.question == "List the trips Ada took" and prev.intent == "retrieve"
    assert [(e.id, e.name) for e in prev.entities] == [("E1", "Ada")]
    assert report["threaded"] == 1


def test_an_unreadable_registry_leaves_a_label_alone(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")

    monkeypatch.setattr(previous, "resolve_trace_id", boom)
    calls, plan_fn = _recorder()
    report = replay.replay([_label("L001", source=f"trace:{T2}")], "lexical", plan_fn=plan_fn)
    assert calls[0][1] is None and report["threaded"] == 0


def test_a_failed_followed_label_gives_its_follower_no_previous():
    seen = []

    def plan_fn(question, domains, classifier="lexical", today=None, **kw):
        seen.append(kw.get("previous"))
        if question == "question L001":
            raise RuntimeError("x")
        return _plan()

    report = replay.replay([_label("L001"), _label("L002", follows="L001")], "lexical", plan_fn=plan_fn)
    assert seen == [None, None] and report["outcomes"]["error"] == 1


# ── decision signature ──────────────────────────────────────────────────────


def test_probability_jitter_is_stable_but_a_route_flip_is_not():
    base = _plan(steps=[{"n": 1, "cmd": "artmind query survey", "why": "a"}])
    jitter = _plan(steps=[{"n": 1, "cmd": "artmind query survey", "why": "other words"}])
    jitter["route"]["confidence"], jitter["intent"]["confidence"] = 0.88, 0.91
    jitter["confidence"]["score"] = 0.79
    jitter["classifier"]["latency_ms"] = 99.0
    jitter["route"]["alternatives"] = [{"id": "x", "p": 0.1}]
    assert replay._stable(base) == replay._stable(jitter)
    flipped = _plan(route="enumeration", steps=[{"n": 1, "cmd": "artmind query survey", "why": "a"}])
    assert replay._stable(base) != replay._stable(flipped)

    seq = iter([base, jitter, base])
    report = replay.replay([_label("L001")], "lexical", repeat=3, plan_fn=lambda *a, **k: next(seq))
    assert report["unstable"] == [] and report["flips"] == []
    seq = iter([base, flipped, _plan(level="low", steps=base["steps"])])
    report = replay.replay([_label("L001")], "lexical", repeat=3, plan_fn=lambda *a, **k: next(seq))
    assert report["unstable"] == ["L001"]
    assert report["flips"] == [{"id": "L001", "fields": ["route", "level"]}]


def test_signature_catches_entity_ids_and_commands():
    def with_param(eid):
        return _plan(route="view", view_match={"name": "v", "params": {
            "who": {"type": "entity", "chosen": {"_id": eid, "name": "Ada"}, "twins": False, "candidates": []}}})

    assert replay.flipped_fields([with_param("E1"), with_param("E2")]) == ["params"]
    assert replay.flipped_fields([_plan(steps=[{"cmd": "a"}]), _plan(steps=[{"cmd": "b"}])]) == ["steps"]
    assert replay.flipped_fields([_plan(suggested_steps=[{"cmd": "a"}]), _plan()]) == ["suggested_steps"]


# ── shadow ──────────────────────────────────────────────────────────────────


def _run_shadow(monkeypatch, trace_id, builder=None):
    calls = []

    def fake_plan(question, domains=None, **kw):
        calls.append((question, kw))
        return {"confidence": {"level": "high"}}

    monkeypatch.setattr(planner, "plan", fake_plan)
    if builder is not None:
        monkeypatch.setattr(previous, "previous_from_trace", builder)
    result = shadow.start("yes please", None, True, trace_id).result(timeout=5)
    return calls, result


def test_shadow_passes_the_previous_turn_to_the_planner(monkeypatch):
    _sessions()
    calls, result = _run_shadow(monkeypatch, T2)
    ((question, kw),) = calls
    assert question == "yes please"
    assert kw["previous"].question == "List the trips Ada took" and kw["previous"].offer.startswith("Shall I")
    assert "decision_timeout" in kw
    assert result["status"] == "ok" and result["previous_given"] is True


def test_shadow_plans_alone_for_a_first_turn_or_an_untraced_turn(monkeypatch):
    _sessions()
    calls, result = _run_shadow(monkeypatch, T1)
    assert "previous" not in calls[0][1] and result["previous_given"] is False
    calls, _ = _run_shadow(monkeypatch, None)
    assert "previous" not in calls[0][1]


def test_shadow_survives_a_failing_previous_builder(monkeypatch):
    def boom(trace_id):
        raise RuntimeError("registry locked")

    calls, result = _run_shadow(monkeypatch, T2, boom)
    assert "previous" not in calls[0][1]
    assert result["status"] == "ok" and result["previous_given"] is False


def test_the_recorder_hands_the_shadow_its_trace_id(monkeypatch):
    from artmind.traces.recorder import TraceRecorder

    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {})
    seen = {}
    monkeypatch.setattr(shadow, "start", lambda q, d=None, followup=False, trace_id=None:
                        seen.update(trace_id=trace_id))
    rec = TraceRecorder.start(source="admin", question="q", session_id="s9", shadow_question="q")
    assert seen["trace_id"] == rec.trace_id
