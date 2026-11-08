"""Compact document rows, truthful cut/next, open_next and documents behind facts (spec 6.5)."""

import json

import pytest

from artmind.query_run import adapters, bundle, executor, opening
from artmind.query_run.capture import StepResult

PLAN = {"program": [{"id": "s1", "kind": "run", "run": "artmind query survey --shape brief --domain bank q --compact", "filled": True}],
        "guards": [], "route": {"id": "survey"}, "replacements": {}, "question": "q", "effective_question": "what rate?",
        "scope": {"domains": ["bank"]}, "confidence": {"level": "low"}, "classifier": {"backend": "lexical"},
        "intent": {"id": "retrieve"}, "program_kind": "cross_document"}


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)


def _listing(n, gist="g" * 160):
    return {"command": "query survey", "documents": [
        {"doc_id": f"d{i}", "title": f"Document number {i}", "domain": "bank", "date": "2026-01-01", "status": "current",
         "gist": gist, "gist_source": "summary"} for i in range(n)]}


def _opened(ids, size=1100):
    return {"command": "query survey", "documents": [], "passages": [
        {"passage_id": f"op{i}", "chunk_id": f"oc{i}", "text": "x" * size, "relevance": 0.5,
         "doc": {"id": i, "title": f"Document number {i[1:]}", "domain": "bank", "date": None, "status": "current"}} for i in ids]}


def _run(listing, open_size=1100, plan=PLAN, extra=None):
    def step(argv, sid):
        if sid == "open":
            ids = [argv[i + 1] for i, a in enumerate(argv) if a == "--expand"]
            return StepResult(sid, list(argv), 0, None, _opened(ids, open_size), 1.0, 1)
        return StepResult(sid, list(argv), 0, None, listing, 1.0, 1)
    return executor.execute(plan, step_fn=step)


def _sizes(printed):
    d = lambda v: len(json.dumps(v, separators=(",", ":")))
    opened = [e for e in printed["evidence"] if e["from"] == ["open"]]
    return {"opened": d(opened), "evidence": d(printed["evidence"]) - d(opened), "document lines":
            d([{k: v for k, v in r.items() if k not in ("gist", "gist_source")} for r in printed["documents"]]),
            "gists": d([r for r in printed["documents"] if "gist" in r]) , "facts": d(printed["facts"]), "total": d(printed)}


def test_all_fifty_documents_are_listed_even_when_opened_text_is_large():
    printed, full = bundle.build(PLAN, _run(_listing(50), open_size=1190), budget=24_000)
    assert len(printed["documents"]) == 50 and len(printed["opened"]) == 5
    assert set(printed["documents"][0]) >= {"doc_id", "title", "date", "opened", "rank"}
    assert "evidence" not in printed["documents"][0]
    lines = [{k: v for k, v in d.items() if k not in ("gist", "gist_source")} for d in printed["documents"]]
    assert len(json.dumps(lines, separators=(",", ":"))) < 5_000
    assert printed["documents"][0]["opened"] is True and printed["documents"][5]["opened"] is False
    assert all("gist" in d for d in full["documents"])  # the stored bundle keeps full rows
    assert len(json.dumps(printed, separators=(",", ":"))) <= 24_000


def test_gists_are_added_to_unopened_rows_in_rank_order_while_budget_remains():
    printed, _ = bundle.build(PLAN, _run(_listing(50)), budget=14_000)
    with_gist = [d["rank"] for d in printed["documents"] if "gist" in d]
    assert with_gist and with_gist == list(range(with_gist[0], with_gist[0] + len(with_gist))) and with_gist[0] == 6
    assert len(with_gist) < 45 and printed["cut"]["gists"] == 45 - len(with_gist) and printed["cut"]["documents"] == 0
    assert all("gist" not in d for d in printed["documents"] if d["opened"])
    assert len(json.dumps(printed, separators=(",", ":"))) <= 14_000
    assert printed["truncated"] is True and printed["handle"] and printed["next"] is None  # only gists lost: paging adds nothing
    assert "document gists" in printed["warnings"][-1]


def test_nothing_cut():
    printed, _ = bundle.build(PLAN, _run(_listing(8, gist="g")), budget=24_000)
    assert printed["truncated"] is False and printed["next"] is None and printed["handle"] is None
    assert printed["cut"] == {"evidence": 0, "documents": 0, "facts": 0, "guides": 0, "gists": 0}


def _ex_with(payloads):
    ex = executor.Execution()
    for sid, p in payloads.items():
        ex.results[sid] = StepResult(sid, ["query", "x"], 0, None, p, 1.0, 1)
    ex.answer_ids = list(payloads)
    return ex


def test_only_documents_cut_next_names_documents():
    printed, _ = bundle.build(PLAN, _ex_with({"s1": _listing(50, gist="")}), budget=2_000)
    assert 0 < len(printed["documents"]) < 50 and printed["cut"]["evidence"] == 0
    assert printed["cut"]["documents"] == 50 - len(printed["documents"]) and printed["cut"]["facts"] == 0
    assert printed["truncated"] is True
    assert printed["next"] == f"artmind query page {printed['handle']} --list documents --from {len(printed['documents'])}"
    assert f"{printed['cut']['documents']} of 50 documents" in printed["warnings"][-1]
    assert "evidence" not in printed["warnings"][-1]


def test_only_facts_cut_next_names_facts():
    rows = [{"observation": {"key": f"k{i}", "value": "v" * 60, "doc_id": "d0"}} for i in range(60)]
    payload = {"command": "query entity-history", "rows": rows}
    printed, _ = bundle.build(PLAN, _ex_with({"s1": payload}), budget=3_000)
    assert printed["cut"]["facts"] > 0 and printed["cut"]["evidence"] == 0 and printed["cut"]["documents"] == 0
    assert printed["next"] == f"artmind query page {printed['handle']} --list facts --from {len(printed['facts'])}"


def test_evidence_cut_comes_first_in_next():
    payload = {"command": "query vector-text", "rows": [
        {"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t" * 900, "relevance": 0.5,
         "doc": {"id": f"d{i}", "title": f"D{i}", "domain": "bank", "date": None, "status": "current"}} for i in range(30)]}
    printed, _ = bundle.build(PLAN, _ex_with({"s1": payload}), budget=6_000)
    assert printed["cut"]["evidence"] > 0 and printed["cut"]["documents"] > 0
    assert printed["next"] == f"artmind query page {printed['handle']} --list evidence --from {len(printed['evidence'])}"
    assert "evidence rows" in printed["warnings"][-1] or "evidence" in printed["warnings"][-1]


def test_assembly_order_evidence_then_document_lines_then_facts():
    payload = {"command": "query entity-history", "rows": [{"observation": {"key": f"k{i}", "value": "v" * 100, "doc_id": "d1"}} for i in range(40)]}
    ex = _ex_with({"s1": payload, "s2": _listing(10, gist="")})
    ex.results["s2"].payload["documents"][0]["passage"] = {"passage_id": "p", "chunk_id": "c", "text": "t" * 200}
    printed, _ = bundle.build(PLAN, ex, budget=3_400)
    assert printed["evidence"] and len(printed["documents"]) == 10  # all line rows placed before facts
    assert 0 < len(printed["facts"]) < 40


def test_open_next_is_present_never_cut_and_built_like_the_open_command():
    plan = {**PLAN, "effective_question": "-what rate?"}
    printed, _ = bundle.build(plan, _run(_listing(50), plan=plan), budget=1_500)
    on = printed["open_next"]
    assert on["doc_ids"] == ["d5", "d6", "d7", "d8", "d9"]
    assert on["command"] == ("artmind query survey --shape brief --domain bank --passages 8 --expand d5 --expand d6 --expand d7 "
                             "--expand d8 --expand d9 --compact -- '-what rate?'")
    assert printed["truncated"] is True


def test_open_next_is_null_when_everything_is_opened_or_no_domain():
    printed, _ = bundle.build(PLAN, _run(_listing(3)), budget=24_000)
    assert printed["open_next"] is None
    printed, _ = bundle.build({**PLAN, "scope": {}}, _run(_listing(9)), budget=24_000)
    assert printed["open_next"] is None


# ── documents behind facts ───────────────────────────────────────────────
HISTORY = {"command": "query entity-history", "rows": [
    {"observation": {"key": f"acme|rate|{i}", "value": str(i), "doc_id": f"h{i}"}} for i in range(8)]}


def test_adapters_keep_a_facts_document_ids():
    (fact,) = adapters.adapt({"command": "query entity-history", "rows": [{"observation": {"key": "k", "chunk_id": "c1", "doc_id": "h1"}}]}).facts
    assert fact["doc_ids"] == ["h1"] and fact["chunk_ids"] == ["c1"]
    ctx = adapters.adapt({"command": "query entity-context", "rows": [{"entity": {"id": "e"}, "chunks": [
        {"chunk_id": "c9", "text": "t", "doc": {"id": "d3", "title": "T"}}], "source_documents": [{"id": "d4"}]}]}).facts[0]
    assert ctx["doc_ids"] == ["d3", "d4"]
    ent = adapters.adapt({"command": "query graph pattern7", "rows": [{"name": "x", "documents": [{"doc_id": "d7"}]}]}).facts[0]
    assert ent["doc_ids"] == ["d7"]


def test_fact_documents_use_chunk_ids_mapped_through_evidence():
    facts = [{"doc_ids": [], "chunk_ids": ["c1"]}, {"doc_ids": ["x"], "chunk_ids": []}]
    ev = [{"chunk_id": "c1", "doc": {"id": "m"}}]
    assert opening.fact_documents(facts, ev) == ["m", "x"]


def test_selection_interleaves_survey_and_fact_documents():
    docs = [{"doc_id": f"s{i}", "title": "t"} for i in range(6)]
    chosen = opening.select_documents(docs, [], "q", fact_docs=["f0", "s1", "f2", "f3"])
    assert chosen == [("s0", "rank"), ("f0", "rank"), ("s1", "rank"), ("s2", "rank"), ("f2", "rank")]


def test_selection_without_fact_documents_is_unchanged():
    docs = [{"doc_id": f"s{i}", "title": "t"} for i in range(7)]
    assert [d for d, _ in opening.select_documents(docs, [], "q")] == ["s0", "s1", "s2", "s3", "s4"]


def test_an_entity_history_run_opens_the_documents_behind_its_facts():
    plan = {**PLAN, "program": [{"id": "s1", "kind": "run", "run": "artmind query entity-history --domain bank --name Acme", "filled": True}]}
    calls = []

    def step(argv, sid):
        calls.append((sid, list(argv)))
        if sid == "open":
            return StepResult(sid, list(argv), 0, None, _opened([a for a in argv if a.startswith("h")]), 1.0, 1)
        return StepResult(sid, list(argv), 0, None, HISTORY, 1.0, 1)
    ex = executor.execute(plan, step_fn=step)
    open_argv = dict(calls)["open"]
    assert [open_argv[i + 1] for i, a in enumerate(open_argv) if a == "--expand"] == ["h0", "h1", "h2", "h3", "h4"]
    assert [o["doc_id"] for o in ex.opened] == ["h0", "h1", "h2", "h3", "h4"]
