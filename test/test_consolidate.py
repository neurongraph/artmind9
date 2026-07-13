"""Description consolidation unit tests (no Neo4j, no LLM)."""
from artmind.consolidate import (
    _WRITE_CYPHER,
    build_consolidation_prompt,
    classify_entity,
    fragment_count,
    select_excerpts,
)

TODAY = "2026-07-12"


def _chunk(cid, text="some text", valid_to=None, doc="doc_a.md"):
    return {"id": cid, "text": text, "valid_to": valid_to, "doc_name": doc, "doc_domain": "d"}


def _row(**overrides):
    row = {
        "id": "e1",
        "name": "Complaint Handling Policy",
        "entity_class": "POLICY",
        "description": "frag one | frag two | frag three",
        "prev_sources": None,
        "has_open_conflict": False,
        "chunks": [_chunk("doc_001"), _chunk("doc_002")],
    }
    row.update(overrides)
    return row


# ── fragment_count / classify_entity ──────────────────────────────────────────

def test_fragment_count():
    assert fragment_count("a | b | c") == 3
    assert fragment_count("single") == 1
    assert fragment_count("") == 0


def test_classify_consolidates_multi_fragment_entity():
    assert classify_entity(_row(), min_fragments=2, force=False) == "consolidate"


def test_classify_skips_open_conflict():
    # blending is exactly what conflict adjudication must prevent
    assert (
        classify_entity(_row(has_open_conflict=True), 2, False)
        == "skipped_open_conflict"
    )


def test_classify_skips_no_chunks():
    assert classify_entity(_row(chunks=[]), 2, False) == "skipped_no_chunks"
    assert classify_entity(_row(chunks=[{"id": "x", "text": None}]), 2, False) == "skipped_no_chunks"


def test_classify_skips_below_fragment_threshold():
    assert classify_entity(_row(description="just one"), 2, False) == "skipped_below_threshold"


def test_classify_unchanged_chunk_set_is_idempotent():
    row = _row(prev_sources=["doc_001", "doc_002"])
    assert classify_entity(row, 2, False) == "skipped_unchanged"


def test_classify_reconsolidates_when_chunk_set_changed():
    # a new ingest added doc_003 since the last consolidation
    row = _row(
        prev_sources=["doc_001", "doc_002"],
        chunks=[_chunk("doc_001"), _chunk("doc_002"), _chunk("doc_003")],
    )
    assert classify_entity(row, 2, False) == "consolidate"


def test_classify_force_overrides_unchanged():
    row = _row(prev_sources=["doc_001", "doc_002"])
    assert classify_entity(row, 2, True) == "consolidate"


# ── select_excerpts ────────────────────────────────────────────────────────────

def test_select_excerpts_current_first_then_capped():
    chunks = [
        _chunk("c_003", valid_to="2026-01-01"),  # superseded
        _chunk("c_002"),
        _chunk("c_001"),
        _chunk("c_004", valid_to="2099-01-01"),  # future valid_to = current
    ]
    picked = select_excerpts(chunks, max_chunks=3, today=TODAY)
    assert [c["id"] for c in picked] == ["c_001", "c_002", "c_004"]


def test_select_excerpts_includes_historical_when_room():
    chunks = [_chunk("c_002", valid_to="2026-01-01"), _chunk("c_001")]
    picked = select_excerpts(chunks, max_chunks=6, today=TODAY)
    assert [c["id"] for c in picked] == ["c_001", "c_002"]


# ── prompt safety rules ────────────────────────────────────────────────────────

def test_prompt_marks_historical_and_keeps_conflicts():
    excerpts = [
        _chunk("c_001", text="Acknowledge within 3 days.", doc="policy_v3.md"),
        _chunk("c_002", text="Acknowledge within 5 days.", valid_to="2026-01-01", doc="policy_v2.md"),
    ]
    prompt = build_consolidation_prompt(
        "Complaint Handling Policy", "POLICY", "old | frags", excerpts, TODAY
    )
    assert "keep BOTH values side by side" in prompt
    assert "HISTORICAL — superseded" in prompt
    assert "policy_v3.md" in prompt and "policy_v2.md" in prompt
    assert prompt.index("policy_v3.md") < prompt.index("policy_v2.md")


def test_prompt_caps_excerpt_length():
    excerpts = [_chunk("c_001", text="x" * 5000)]
    prompt = build_consolidation_prompt("E", "POLICY", "d", excerpts, TODAY, max_excerpt_chars=100)
    assert "x" * 100 in prompt and "x" * 101 not in prompt


# ── write semantics ────────────────────────────────────────────────────────────

def test_write_preserves_original_raw_once():
    # coalesce keeps the FIRST original blob across re-consolidations
    assert "e.description_raw = coalesce(e.description_raw, e.description)" in _WRITE_CYPHER
    assert "e.description_source_chunks = $chunkIds" in _WRITE_CYPHER
    assert "e.description_source_docs = $docNames" in _WRITE_CYPHER
    assert "e.embedding = null" in _WRITE_CYPHER
