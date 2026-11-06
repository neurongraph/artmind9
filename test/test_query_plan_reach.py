"""`plan replay --reach`: document reach of a plan's own steps. Stub plans, canned payloads, invented data."""

from artmind.query_plan import reach
from artmind.query_plan.labels import Label

reach_orig = reach.reach

NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 fees --compact"
WIDE = "artmind query survey --shape brief --domain bank 'fees?' --compact"
GRAPH = "artmind query graph pattern1 --domain bank --entityName Acme --compact"
DB = "artmind db sql 'select 1'"

PROGRAM = [
    {"id": "p", "kind": "par", "children": [{"id": "needle", "kind": "run", "run": NEEDLE},
                                              {"id": "wide", "kind": "run", "run": WIDE}]},
    {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []},
     "when": "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"},
]


def _label(lid="L1", expected=("policies/fees.md", "Rates.MD"), **over):
    base = dict(id=lid, question="invented question", source="written", domains=("bank",), route="search",
                view=None, params={}, expected_documents=tuple(expected))
    return Label(**{**base, **over})


def _plan(steps=(NEEDLE, WIDE), suggested=(), program=PROGRAM):
    return {"steps": [{"cmd": c} for c in steps], "suggested_steps": [{"cmd": c} for c in suggested],
            "program": program, "route": {"id": "search"}, "domains": {"selected": ["bank"]},
            "confidence": {"level": "high"}, "classifier": {"latency_ms": 1.0, "fell_back": False}}


def _row(doc_id, path, relevance):
    return {"rank": 1, "relevance": relevance, "doc": {"id": doc_id, "title": path}, "document": {"id": doc_id, "path": path}}


def _needle_payload(drop_pair, docs=3):
    """Ten rows over `docs` documents; ranks 1 and 2 carry the relevance pair."""
    rows = [_row(f"d{i % docs}", f"policies/doc{i % docs}.md", 0.5) for i in range(10)]
    rows[0] = _row("d0", "policies/fees.md", drop_pair[0])
    rows[1]["relevance"] = drop_pair[1]
    return {"rows": rows}


SURVEY = {"documents": [{"doc_id": "s1", "title": "Rates", "name": "Rates.md", "domain": "bank"},
                        {"doc_id": "s2", "title": "Other", "name": "other.md"}]}


def _steps(payloads):
    def run(args):
        cmd = " ".join(args)
        for key, value in payloads.items():
            if key.split(" ", 1)[1].startswith(cmd.split(" --")[0]) and key.split()[2] == args[1]:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected step {args}")
    return run


# -- matching ---------------------------------------------------------------

def test_match_by_path_then_basename_case_insensitive():
    assert reach.match_kind("policies/fees.md", {"path": "/vault/policies/Fees.md"}) == "path"
    assert reach.match_kind("policies/fees.md", {"path": "policies/fees.md"}) == "path"
    assert reach.match_kind("fees.md", {"source_path": "policies/FEES.md"}) == "path"
    assert reach.match_kind("archive/fees.md", {"path": "policies/fees.md"}) == "basename"
    assert reach.match_kind("Rates.MD", {"name": "rates.md"}) == "path"
    assert reach.match_kind("policies/fees.md", {"name": "fees-2.md"}) is None
    assert reach.match_kind("policies/fees.md", {"title": "fees"}) == "title"  # brief rows carry only a title
    assert reach.match_kind("policies/fees.md", {}) is None
    assert reach.match_kind("", {"path": "a.md"}) is None


def test_step_documents_reads_the_known_fields_only():
    payload = {"rows": [_row("d1", "a/b.md", 0.4),
                        {"source_documents": [{"id": "d2", "name": "c.md"}]},
                        {"entityData": {"name": "not a document"}}],
               "documents": [{"doc_id": "d3", "title": "T"}]}
    names = [(d["id"], d["path"], d["name"], d["title"]) for d in reach.step_documents(payload)]
    assert ("d1", "a/b.md", None, "a/b.md") in names or ("d1", None, None, "a/b.md") in names
    assert ("d2", None, "c.md", None) in names and ("d3", None, None, "T") in names
    assert all(d[0] for d in names)  # the entity dict is not a document


# -- the needle-or-merge rule -----------------------------------------------

def test_needle_answers_alone_when_clear_winner_and_few_documents():
    payloads = {NEEDLE: _needle_payload((0.9, 0.5), docs=3), WIDE: SURVEY}
    row = reach.reach_label(_label(), _plan(), _steps(payloads))
    assert row["needle_alone"] is True
    assert (row["expected"], row["reached"], row["reached_all"], row["missed"]) == (2, 1, 2, 1)
    assert row["detail"]["matched"] == ["policies/fees.md"] and row["detail"]["missed"] == ["Rates.MD"]


def test_merge_runs_when_no_clear_winner_or_many_documents():
    close = {NEEDLE: _needle_payload((0.51, 0.5), docs=3), WIDE: SURVEY}
    row = reach.reach_label(_label(), _plan(), _steps(close))
    assert (row["needle_alone"], row["reached"], row["reached_all"]) == (False, 2, 2)
    spread = {NEEDLE: _needle_payload((0.9, 0.5), docs=8), WIDE: SURVEY}  # 8 >= NEEDLE_SPREAD
    assert reach.reach_label(_label(), _plan(), _steps(spread))["needle_alone"] is False
    keyword_only = {NEEDLE: _needle_payload((None, 0.5), docs=3), WIDE: SURVEY}  # no relevance: no winner
    assert reach.reach_label(_label(), _plan(), _steps(keyword_only))["needle_alone"] is False


def test_no_pick_when_means_every_step_counts():
    plan = _plan(program=[{"id": "p", "kind": "par", "children": [{"id": "needle", "kind": "run", "run": NEEDLE}]}])
    payloads = {NEEDLE: _needle_payload((0.9, 0.5)), WIDE: SURVEY}
    row = reach.reach_label(_label(), plan, _steps(payloads))
    assert (row["needle_alone"], row["reached"]) == (False, 2)


# -- step handling ----------------------------------------------------------

def test_errored_and_skipped_steps_are_recorded_not_fatal():
    plan = _plan(steps=(NEEDLE, DB), suggested=(WIDE, GRAPH))
    payloads = {NEEDLE: _needle_payload((0.51, 0.5)), WIDE: RuntimeError("boom"),
                GRAPH: {"rows": [{"source_documents": [{"id": "g1", "name": "rates.md"}]}]}}
    row = reach.reach_label(_label(), plan, _steps(payloads))
    assert (row["steps_run"], row["steps_skipped"], row["steps_errored"]) == (2, 1, 1)
    assert row["reached"] == 2  # fees.md from the needle, rates.md from the graph row
    assert "boom" not in str(reach.public_report({"classifier": "x", "totals": {}, "labels": [row]}))
    assert row["detail"]["steps"][2]["status"] == "error"


def test_runnable_refuses_everything_but_artmind_query():
    assert reach.runnable(NEEDLE)[:2] == ["query", "vector-text"]
    assert reach.runnable(DB) is None
    assert reach.runnable("rm -rf /") is None
    assert reach.runnable("artmind ingest sync x") is None
    assert reach.runnable("artmind query 'unterminated") is None


# -- the whole pass ---------------------------------------------------------

def test_reach_skips_unlabelled_plans_once_and_totals():
    labels = [_label("L1"), _label("L2", expected=("policies/fees.md",)), _label("L3", expected=())]
    calls = []

    def plan_fn(question, domains, classifier, today, **kw):
        calls.append(classifier)
        return _plan()

    payloads = {NEEDLE: _needle_payload((0.9, 0.5), docs=3), WIDE: SURVEY}
    report = reach.reach(labels, "lexical", plan_fn=plan_fn, step_fn=_steps(payloads))
    assert calls == ["lexical", "lexical"]  # L3 has no expected documents; each plan once
    assert [r["id"] for r in report["labels"]] == ["L1", "L2"]
    assert report["totals"] == {"labels": 2, "expected": 3, "reached": 2, "reached_all": 3,
                                "needle_alone": 2, "steps_errored": 0, "steps_skipped": 0}
    printed = reach.public_report(report)
    assert all("detail" not in r for r in printed["labels"])
    assert "invented question" not in str(printed)


def test_a_plan_error_is_a_zero_reach_row():
    def plan_fn(*a, **k):
        raise ValueError("planner down")

    report = reach.reach([_label()], "lexical", plan_fn=plan_fn, step_fn=_steps({}))
    row = report["labels"][0]
    assert (row["reached"], row["missed"], row["plan_error"]) == (0, 2, "ValueError")


def test_cli_reach_prints_counts_and_writes_detail(tmp_path, monkeypatch):
    import json

    from click.testing import CliRunner

    from artmind import graph_query
    from artmind.cli import cli

    labels = tmp_path / "labels.yaml"
    labels.write_text("version: 1\nlabels:\n- id: L001\n  question: invented question\n  source: written\n"
                      "  domains: [bank]\n  route: search\n  expected_documents: [policies/fees.md]\n")
    monkeypatch.setattr(graph_query, "domains_overview", lambda *a, **k: {})
    monkeypatch.setattr("paths.LOGS_DIR", tmp_path / "logs")
    payloads = {NEEDLE: _needle_payload((0.9, 0.5)), WIDE: SURVEY}
    monkeypatch.setattr(reach, "reach", lambda labels_, clf, **kw: reach_orig(
        labels_, clf, plan_fn=lambda *a, **k: _plan(), step_fn=_steps(payloads)))
    result = CliRunner().invoke(cli, ["plan", "replay", "--file", str(labels), "--reach", "--compact"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    rep = out["reports"]["lexical"]
    assert rep["totals"]["expected"] == 1 and rep["totals"]["reached"] == 1
    assert "invented question" not in result.output and "policies/fees.md" not in result.output
    assert list((tmp_path / "logs").glob("plan-reach-*.json"))
