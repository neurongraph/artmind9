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
