"""Miss records (spec 2026-11-08 §4.1). Invented spans and text only."""

from artmind.traces import misses


def span(sid, command, *, args=None, rows=1, docs=(), prov=(), resolved=None, status=None, warnings=None, t=0.0):
    ev = {}
    if docs:
        ev["documents"] = [{"doc_id": f"id-{d}", "path": d} for d in docs]
    if prov:
        ev["provenance"] = {"documents": [{"doc_id": f"id-{p}", "path": None, "source_path": p, "title": None}
                                          for p in prov]}
    if resolved is not None:
        ev["resolved"] = resolved
    if status:
        ev["status"] = status
    if warnings:
        ev["warnings"] = warnings
    return {"span_id": sid, "command": command, "args": args or {}, "exit_code": 0, "rows": rows,
            "evidence": ev, "started_at": t}


ANSWER = "The fee is ten.\n\n### Sources\n- [fees](notes/fees.md)\n- [rates](<notes/rate sheet.md>)\n"


def test_cited_sources_link_to_the_first_span_that_returned_them():
    spans = [span("s1", "query vector-text", docs=["other.md"], t=1),
             span("s2", "query vector-text", docs=["notes/fees.md"], t=2),
             span("s3", "query survey", prov=["notes/rate sheet.md", "notes/fees.md"], t=3)]
    links = misses.cited_spans(ANSWER, spans)
    assert links == {"notes/fees.md": "s2", "notes/rate sheet.md": "s3"}


def test_a_citation_no_span_returned_maps_to_none():
    links = misses.cited_spans(ANSWER, [span("s1", "query vector-text", docs=["notes/fees.md"])])
    assert links["notes/rate sheet.md"] is None


def test_no_sources_section_means_no_links():
    assert misses.cited_spans("just text", [span("s1", "query vector-text", docs=["a.md"])]) == {}


def test_a_miss_serialises_with_its_kind_first():
    m = misses.Miss(kind="vocabulary", trace_id="t1", detail={"added_terms": ["fee"]}, span_id="s2")
    d = m.to_dict()
    assert list(d)[0] == "kind" and d["trace_id"] == "t1" and d["detail"] == {"added_terms": ["fee"]}


Q = "What fee applies when a payment bounces?"


def test_vocabulary_lists_terms_the_question_lacked_from_a_cited_span():
    spans = [span("s1", "query vector-text", args={"question": "returned direct debit charge"},
                  docs=["notes/fees.md"])]
    out = misses.vocabulary(Q, ANSWER, spans)
    assert len(out) == 1 and out[0].kind == "vocabulary" and out[0].span_id == "s1"
    assert out[0].detail["added_terms"] == ["returned", "direct", "debit", "charge"]
    assert out[0].detail["sources"] == ["notes/fees.md"]


def test_vocabulary_ignores_stems_and_stopwords_of_question_words():
    spans = [span("s1", "query vector-text", args={"question": "the fees for bounced payments"},
                  docs=["notes/fees.md"])]
    assert misses.vocabulary(Q, ANSWER, spans) == []


def test_vocabulary_ignores_spans_whose_results_were_not_cited():
    spans = [span("s1", "query vector-text", args={"question": "unrelated words entirely"}, docs=["x.md"])]
    assert misses.vocabulary(Q, ANSWER, spans) == []


def test_reformulation_counts_attempts_before_the_winning_phrase():
    spans = [span("s1", "query vector-text", args={"question": "bounce fee"}, docs=["x.md"], t=1),
             span("s2", "query survey", args={"question": "payment failure charge"}, rows=0, t=2),
             span("s3", "query vector-text", args={"question": "returned payment fee"}, docs=["notes/fees.md"], t=3),
             span("s4", "query vector-text", args={"question": "later"}, docs=["notes/fees.md"], t=4)]
    out = misses.reformulation(Q, ANSWER, spans)
    assert len(out) == 1 and out[0].span_id == "s3"
    assert out[0].detail == {"attempts": 3, "phrases": ["bounce fee", "payment failure charge"],
                             "winning_phrase": "returned payment fee"}


def test_no_reformulation_when_the_first_search_won():
    spans = [span("s1", "query vector-text", args={"question": "fee"}, docs=["notes/fees.md"])]
    assert misses.reformulation(Q, ANSWER, spans) == []
