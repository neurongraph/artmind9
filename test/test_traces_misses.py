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


def test_identity_records_no_match_and_disambiguation():
    spans = [span("s1", "query entity-resolve", args={"name": "Person A"}, rows=0, status="no_match"),
             span("s2", "query entity-resolve", args={"name": "Person B"}, status="needs_disambiguation")]
    out = misses.identity(spans)
    assert [(m.span_id, m.detail["reason"]) for m in out] == [("s1", "no_match"), ("s2", "ambiguous")]
    assert out[0].detail["name"] == "Person A"


def test_identity_records_twins_before_a_low_score():
    resolved = [{"id": "e1", "name": "Org X", "entity_class": "ORG", "score": 0.5},
                {"id": "e2", "name": "org x", "entity_class": "ORG", "score": 0.2}]
    out = misses.identity([span("s1", "query entity-resolve", args={"name": "Org X"}, resolved=resolved)])
    assert out[0].detail == {"reason": "twins", "name": "Org X", "ids": ["e1", "e2"]}


def test_identity_records_a_low_top_score():
    resolved = [{"id": "e1", "name": "Place C", "score": 0.4}]
    out = misses.identity([span("s1", "query entity-resolve", args={"name": "Plac"}, resolved=resolved)])
    assert out[0].detail["reason"] == "low_score" and out[0].detail["score"] == 0.4


def test_a_confident_resolution_is_not_a_miss():
    resolved = [{"id": "e1", "name": "Place C", "score": 0.95}, {"id": "e2", "name": "Place D", "score": 0.3}]
    assert misses.identity([span("s1", "query entity-resolve", resolved=resolved)]) == []


def test_scope_records_an_unknown_domain_warning():
    out = misses.scope(None, [span("s1", "query vector-text", args={"domain": ["nope"]},
                                   warnings=["unknown domain 'nope': no schema"])])
    assert out[0].detail["reason"] == "unknown_domain"


def test_scope_records_a_widened_rerun():
    spans = [span("s1", "query vector-text", args={"domain": ["fam.a"], "question": "q"}, t=1),
             span("s2", "query vector-text", args={"domain": ["fam.a", "fam.b"], "question": "q"}, t=2)]
    out = misses.scope(None, spans)
    assert [m.detail for m in out] == [{"reason": "widened", "command": "query vector-text",
                                        "from": ["fam.a"], "to": ["fam.a", "fam.b"]}]


def test_scope_records_a_cited_source_outside_the_first_search_scope():
    spans = [span("s1", "query vector-text", args={"domain": ["fam.a"], "question": "q"}, docs=["x.md"], t=1),
             span("s2", "query graph pattern1", args={"domain": ["fam.b"]}, docs=["notes/fees.md"], t=2)]
    out = [m for m in misses.scope(ANSWER, spans) if m.detail["reason"] == "outside_first_scope"]
    assert out[0].detail["source"] == "notes/fees.md" and out[0].span_id == "s2"


def test_structure_flags_a_text_answer_when_an_entity_resolved():
    spans = [span("s1", "query entity-resolve", resolved=[{"id": "e1", "name": "Person A", "score": 0.97}], t=1),
             span("s2", "query vector-text", args={"question": "q"}, docs=["notes/fees.md"], t=2)]
    out = misses.structure(ANSWER, spans)
    assert out[0].detail == {"reason": "text_despite_entity", "entities": ["Person A"]}


def test_structure_is_quiet_when_the_graph_was_asked():
    spans = [span("s1", "query entity-resolve", resolved=[{"id": "e1", "name": "Person A", "score": 0.97}], t=1),
             span("s2", "query graph pattern3", docs=["notes/fees.md"], t=2)]
    assert misses.structure(ANSWER, spans) == []


def test_structure_flags_an_incomplete_count():
    s = span("s1", "query graph pattern1")
    s["evidence"]["complete"] = False
    assert misses.structure(None, [s])[0].detail["reason"] == "incomplete_count"


def test_answer_misses_come_from_the_grade():
    grade = {"grade": "partial", "must_missed": ["item one", "item two"], "must_not_hit": ["bad thing"]}
    out = misses.answer(grade)
    assert [m.detail for m in out] == [{"item": "item one", "grade": "partial"},
                                      {"item": "item two", "grade": "partial"},
                                      {"must_not": "bad thing", "grade": "partial"}]


def test_user_stated_from_feedback_and_a_follow_up():
    fb = {"rating": "partial", "expected": "should list the third option", "note": None}
    out = misses.user_stated(fb, "You missed: the weekend charge, and the cap.")
    assert out[0].detail == {"source": "feedback", "rating": "partial", "item": "should list the third option"}
    assert out[1].detail == {"source": "follow_up", "item": "the weekend charge, and the cap."}


def test_good_feedback_and_an_ordinary_follow_up_are_not_misses():
    assert misses.user_stated({"rating": "good", "expected": "x", "note": None}, "Thanks. And for 2025?") == []


# inline citations and brief-shape documents

INLINE = "The `fee_schedule` note lists ten."


def test_inline_citation_by_file_stem_links_to_the_first_span():
    spans = [span("s1", "query vector-text", docs=["other.md"], t=1),
             span("s2", "query vector-text", docs=["notes/fee_schedule.md"], t=2)]
    assert misses.cited_spans(INLINE, spans) == {"notes/fee_schedule.md": "s2"}


def test_brief_span_with_only_a_doc_id_is_linked_through_a_later_span():
    brief = span("s1", "query survey", t=1)
    brief["evidence"]["provenance"] = {"documents": [{"doc_id": "d9", "path": None, "source_path": None,
                                                      "title": None}]}
    later = span("s2", "query graph pattern10", t=2)
    later["evidence"]["documents"] = [{"doc_id": "d9", "path": "notes/fee_schedule.md"}]
    assert misses.cited_spans(INLINE, [later, brief]) == {"notes/fee_schedule.md": "s1"}


def test_journal_stem_is_cited_by_its_date():
    spans = [span("s1", "query vector-text", docs=["journal/2025-10-04.md"])]
    assert misses.cited_spans("On 2025-10-04 it rained.", spans) == {"journal/2025-10-04.md": "s1"}


def test_a_short_stem_is_never_matched_inline():
    spans = [span("s1", "query vector-text", docs=["a.md", "notes/fee.md"])]
    assert misses.cited_spans("a fee applies; see a and fee", spans) == {}


def test_vocabulary_works_with_an_inline_citation():
    spans = [span("s1", "query vector-text", args={"question": "returned direct debit charge"},
                  docs=["notes/fee_schedule.md"])]
    out = misses.vocabulary(Q, INLINE, spans)
    assert len(out) == 1 and out[0].detail["sources"] == ["notes/fee_schedule.md"]


def test_a_brief_row_with_only_a_title_is_cited_inline_by_its_title():
    s = span("s1", "query vector-text", args={"question": "outing"})
    s["evidence"]["provenance"] = {"documents": [{"doc_id": "d1", "path": None, "source_path": None,
                                                  "title": "2025-03-07"}]}
    links = misses.cited_spans("On 2025-03-07 the family went out.", [s])
    assert links == {"2025-03-07": "s1"}
