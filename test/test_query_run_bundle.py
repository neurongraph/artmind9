"""The bundle: RRF over steps, assembly to a budget, the full bundle under a handle (P3 spec §6)."""

import json

from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_run import bundle, executor
from artmind.query_run.capture import StepResult

PLAN = {"route": {"id": "search"}, "intent": {"id": "retrieve"}, "program_kind": "cross_document",
        "confidence": {"level": "low", "score": 0.6, "threshold": 0.7, "weakest": "slot_quality"},
        "classifier": {"backend": "lexical"}, "scope": {"domains": ["bank"]}, "gaps": []}


def _doc(i):
    return {"id": f"d{i}", "title": f"Doc {i}", "domain": "bank", "date": None, "status": "current"}


def _passages(prefix, n, text="t"):
    return {"command": "query vector-text",
            "rows": [{"passage_id": f"{prefix}{i}", "chunk_id": f"c{prefix}{i}", "text": text, "relevance": 0.5, "doc": _doc(i)}
                     for i in range(n)]}


def _ex(payloads):
    ex = executor.Execution()
    for sid, payload in payloads.items():
        ex.results[sid] = StepResult(sid, ["query", "vector-text"], 0, None, payload, 1.0, 10)
    ex.answer_ids = list(payloads)
    return ex


def test_rrf_merges_duplicates_and_keeps_every_source():
    a = _passages("p", 3)
    b = {"command": "query vector-text", "rows": [a["rows"][2], *_passages("q", 1)["rows"]]}
    printed, full = bundle.build(PLAN, _ex({"needle": a, "wide": b}), budget=24_000)
    ids = [e["passage_id"] for e in full["evidence"]]
    assert ids[0] == "p2"  # rank 3 in one step and rank 1 in the other beats rank 1 in one
    assert full["evidence"][0]["from"] == ["needle", "wide"] and len(ids) == 4
    assert [e["eid"] for e in full["evidence"]] == ["e1", "e2", "e3", "e4"]
    assert printed["truncated"] is False and printed["handle"] is None


def test_the_cap_is_ten_then_one_per_further_document():
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5, "doc": _doc(i % 12)} for i in range(30)]
    printed, full = bundle.build(PLAN, _ex({"needle": {"command": "query vector-text", "rows": rows}}), budget=200_000)
    assert len(full["evidence"]) == 30
    assert len(printed["evidence"]) == 12  # 10 + documents d10, d11 not yet shown
    assert printed["truncated"] is True and printed["rows_total"] == 30 and printed["handle"]
    assert printed["next"] == f"artmind query page {printed['handle']} --list evidence --from 12"


def test_a_small_budget_keeps_the_top_ranked_evidence():
    payload = _passages("p", 10, text="x" * 1000)
    printed, _ = bundle.build(PLAN, _ex({"needle": payload}), budget=5_000)
    assert len(json.dumps(printed, separators=(",", ":"))) <= 5_000
    assert [e["passage_id"] for e in printed["evidence"]] == [f"p{i}" for i in range(len(printed["evidence"]))]
    assert printed["truncated"] is True


def test_the_fixed_parts_and_documents_are_present():
    survey = {"command": "query survey", "documents": [{"doc_id": "d1", "title": "Doc 1", "status": "current"}]}
    printed, _ = bundle.build(PLAN, _ex({"needle": _passages("p", 2), "wide": survey}), budget=24_000)
    for key in ("bundle_version", "mode", "plan", "scope", "path", "evidence", "documents", "facts", "guides",
                "coverage", "gaps", "escalate", "offer", "timing"):
        assert key in printed
    assert printed["mode"] == "reasoning" and printed["plan"]["route"] == "search"
    assert printed["documents"][0]["doc_id"] == "d1" and printed["documents"][0]["rank"] == 1  # a line row (spec 6.5)
    assert printed["documents"][0]["opened"] is False
    assert printed["coverage"]["found"] == 2


def test_the_bundle_budget_setting(monkeypatch):
    monkeypatch.delenv("ARTMIND_BUNDLE_BUDGET", raising=False)
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    assert bundle.bundle_budget() == 28_000
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "9000")
    assert bundle.bundle_budget() == 9000
    monkeypatch.setenv("ARTMIND_BUNDLE_BUDGET", "12000")
    assert bundle.bundle_budget() == 12000


def test_page_list_reads_another_list_of_a_stored_bundle():
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5, "doc": _doc(i % 12)} for i in range(30)]
    survey = {"command": "query survey", "documents": [{"doc_id": f"x{i}", "title": "X"} for i in range(5)]}
    printed, _ = bundle.build(PLAN, _ex({"needle": {"command": "query vector-text", "rows": rows}, "wide": survey}),
                              budget=200_000)
    out = CliRunner().invoke(cli, ["query", "page", printed["handle"], "--list", "documents", "--from", "2", "--compact"])
    assert out.exit_code == 0, out.output
    page = json.loads(out.stdout)
    assert page["rows_from"] == 2 and page["rows_shown"] == len(page["documents"]) > 0
    default = json.loads(CliRunner().invoke(cli, ["query", "page", printed["handle"], "--compact"]).stdout)
    assert len(default["evidence"]) == 30  # LIST_KEY["query run"] = evidence


def _plan_file(tmp_path):
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query vector-text --domain bank q", "filled": True}],
            "guards": [], "route": {"id": "search"}, "suggested_steps": [], "fallbacks": [], "replacements": {}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_the_printed_bundle_is_not_cut_a_second_time(tmp_path, monkeypatch):
    import artmind.cli as cli_mod
    from artmind.query_run import capture

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("paths.LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "8000")
    monkeypatch.delenv("ARTMIND_BUNDLE_BUDGET", raising=False)
    payload = _passages("p", 40, text="y" * 200)

    def fake(argv, step_id=""):
        return StepResult(step_id, list(argv), 0, None, payload, 1.0, 10)

    monkeypatch.setattr(capture, "invoke_step", fake)
    out = CliRunner().invoke(cli, ["query", "run", "--plan", str(_plan_file(tmp_path))])  # no --compact
    assert out.exit_code == 0, out.output
    printed = json.loads(out.stdout)
    assert printed["handle"] and "--list evidence" in printed["next"]
    assert len(list((tmp_path / "logs" / "results").glob("*.json"))) == 1
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET")  # the page itself is budgeted; ask for the lot
    page = CliRunner().invoke(cli, ["query", "page", printed["handle"], "--list", "evidence", "--from", "0", "--compact"])
    assert page.exit_code == 0, page.output
    assert len(json.loads(page.stdout)["evidence"]) == 40


def test_paging_a_bundle_cuts_the_paged_list_not_documents(tmp_path, monkeypatch):
    import artmind.cli as cli_mod
    from artmind import result_store

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "6000")
    stored = {"command": "query run", "bundle_version": 1, "warnings": [], "next": None,
              "evidence": [{"passage_id": f"p{i}", "text": "z" * 400} for i in range(30)],
              "documents": [{"doc_id": f"d{i}", "title": "T"} for i in range(5)]}
    handle = result_store.store(tmp_path / "logs" / "results", stored, lambda p: json.dumps(p, separators=(",", ":")))
    out = CliRunner().invoke(cli, ["query", "page", handle, "--list", "evidence", "--from", "10", "--compact"])
    assert out.exit_code == 0, out.output
    assert len(out.stdout) <= 6000
    page = json.loads(out.stdout)
    assert page["truncated"] and 0 < len(page["evidence"]) < 20
    assert len(page["documents"]) == 5


def test_the_fixed_part_is_bounded_by_the_budget(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.LOGS_DIR", tmp_path / "logs")
    ex = _ex({"needle": _passages("p", 5)})
    ex.path = [{"id": f"s{i}", "cmd": f"artmind query vector-text --domain bank q{i}", "exit_code": 0, "error": None, "rows": 3,
                "facts": {"rows": 3, "rows_total": 3, "truncated": False, "extra": "f" * 200}} for i in range(40)]
    ex.escalate = {"reason": "low_confidence", "suggested": [f"artmind query survey --domain bank question number {i}" for i in range(30)]}
    printed, full = bundle.build(PLAN, ex, budget=6000)
    assert len(json.dumps(printed, separators=(",", ":"))) <= 6000
    assert len(printed["evidence"]) >= 1
    assert any("left out" in w for w in printed["warnings"])
    assert len(printed["escalate"]["suggested"]) <= 3
