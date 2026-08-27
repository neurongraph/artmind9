"""A6 — placement classifier (ADR 0012).

Exercises prompt construction, response normalization, and the propose_placement
orchestration with the LLM mocked out. No live Neo4j and no LLM calls.
"""
from __future__ import annotations

import json

import pytest

import artmind.placement as placement


# ── vocabulary fixture ─────────────────────────────────────────────────────


VOCAB = {
    "project": [
        {"value": "artmind_canvas", "count": 12},
        {"value": "banking-audit", "count": 4},
    ],
    "area": [
        {"value": "engineering", "count": 20},
        {"value": "legal", "count": 3},
    ],
    "tags": [
        {"value": "roadmap", "count": 8},
        {"value": "skeleton", "count": 2},
    ],
    "domain": [
        {"value": "general", "count": 30},
        {"value": "banking.policy", "count": 15},
    ],
}


# ── prompt construction ────────────────────────────────────────────────────


def test_prompt_lists_available_domains_and_vocabulary():
    prompt = placement.build_prompt(
        text="A note about the audit findings.",
        vocabulary=VOCAB,
        available_domains=["general", "banking.policy"],
    )
    assert "general" in prompt
    assert "banking.policy" in prompt
    assert "artmind_canvas" in prompt
    assert "engineering" in prompt
    assert "roadmap" in prompt
    # Counts help the model prefer well-used labels.
    assert "artmind_canvas (12)" in prompt
    assert "engineering (20)" in prompt


def test_prompt_handles_empty_vocabulary_gracefully():
    prompt = placement.build_prompt(
        text="hello world",
        vocabulary={"project": [], "area": [], "tags": [], "domain": []},
        available_domains=["general"],
    )
    assert "(none yet)" in prompt


def test_prompt_includes_optional_context():
    prompt = placement.build_prompt(
        text="body",
        vocabulary=VOCAB,
        available_domains=["general"],
        context="The user was previously discussing regulatory compliance.",
    )
    assert "ADDITIONAL CONTEXT" in prompt
    assert "regulatory compliance" in prompt


def test_prompt_omits_context_block_when_none():
    prompt = placement.build_prompt(
        text="body",
        vocabulary=VOCAB,
        available_domains=["general"],
    )
    assert "ADDITIONAL CONTEXT" not in prompt


# ── response parsing ───────────────────────────────────────────────────────


def test_parse_json_response_strips_markdown_fences():
    text = """```json
{"domain": "general", "domain_confidence": 0.9}
```"""
    parsed = placement._parse_json_response(text)
    assert parsed["domain"] == "general"


def test_parse_json_response_strips_think_blocks():
    text = "<think>weighing options</think>\n{\"domain\": \"general\"}"
    parsed = placement._parse_json_response(text)
    assert parsed["domain"] == "general"


# ── normalization ──────────────────────────────────────────────────────────


def test_normalize_flags_known_vs_new_labels():
    raw = {
        "domain": "banking.policy",
        "domain_confidence": 0.95,
        "title": "Q1 Audit Findings Summary",
        "title_confidence": 0.8,
        "area": "engineering",
        "area_confidence": 0.9,
        "project": "brand-new-project",
        "project_confidence": 0.7,
        "tags": ["roadmap", "new-tag"],
        "tags_confidence": 0.6,
        "target_file_hint": "audit-notes",
        "reason": "policy discussion",
    }
    out = placement._normalize_proposal(raw, VOCAB, ["general", "banking.policy"])
    assert out["domain"]["value"] == "banking.policy"
    assert out["domain"]["known"] is True
    assert out["title"]["value"] == "Q1 Audit Findings Summary"
    assert out["title"]["confidence"] == 0.8
    assert out["area"]["known"] is True
    assert out["project"]["value"] == "brand-new-project"
    assert out["project"]["known"] is False, "the classifier invented this label"
    assert out["tags"]["value"] == ["roadmap", "new-tag"]
    assert out["tags"]["known"] == [True, False]
    assert out["target_file_hint"] == "audit-notes"


def test_normalize_title_defaults_to_none_when_missing():
    out = placement._normalize_proposal({}, VOCAB, ["general"])
    assert out["title"]["value"] is None
    assert out["title"]["confidence"] == 0.0


def test_normalize_clamps_confidence_and_floors_weak_values():
    raw = {
        "domain": "general",
        "domain_confidence": 0.05,   # below floor
        "area": "engineering",
        "area_confidence": 1.5,      # over 1
        "project": None,
        "project_confidence": -0.4,  # below 0
        "tags": [],
        "tags_confidence": 0.7,
    }
    out = placement._normalize_proposal(raw, VOCAB, ["general"])
    assert out["domain"]["confidence"] == 0.0, "sub-floor confidence must floor to 0"
    assert out["area"]["confidence"] == 1.0, "confidence must be clamped to <= 1"
    assert out["project"]["confidence"] == 0.0


def test_normalize_caps_tags_at_five():
    raw = {"tags": ["a", "b", "c", "d", "e", "f", "g"]}
    out = placement._normalize_proposal(raw, VOCAB, ["general"])
    assert len(out["tags"]["value"]) == 5


def test_normalize_accepts_comma_separated_tag_string():
    """A model that returns tags as a CSV string instead of a list still normalizes."""
    raw = {"tags": "roadmap, skeleton, adhoc"}
    out = placement._normalize_proposal(raw, VOCAB, ["general"])
    assert out["tags"]["value"] == ["roadmap", "skeleton", "adhoc"]


def test_normalize_defaults_domain_to_general_when_missing():
    raw = {}
    out = placement._normalize_proposal(raw, VOCAB, ["general"])
    assert out["domain"]["value"] == "general"


# ── orchestration ──────────────────────────────────────────────────────────


def _fake_vocab(*a, **k):
    return {"vocabulary": VOCAB, "command": "filing_vocabulary"}


def test_propose_placement_returns_normalized_proposal(monkeypatch):
    """propose_placement wires vocabulary + LLM + normalization together."""
    monkeypatch.setattr(placement, "filing_vocabulary", _fake_vocab, raising=False)

    # Monkeypatch the LLM call to return a canned JSON response.
    def fake_llm(model, prompt):
        return json.dumps({
            "domain": "banking.policy",
            "domain_confidence": 0.9,
            "title": "Canvas A6 Roadmap Notes",
            "title_confidence": 0.75,
            "area": "engineering",
            "area_confidence": 0.8,
            "project": "artmind_canvas",
            "project_confidence": 0.85,
            "tags": ["roadmap"],
            "tags_confidence": 0.7,
            "target_file_hint": "canvas-a6-notes",
            "reason": "roadmap update for the canvas project",
        })

    import artmind.extraction as extraction
    monkeypatch.setattr(extraction, "call_llm", fake_llm)
    # graph_query.filing_vocabulary is imported inside propose_placement; patch that too
    import artmind.graph_query as gq
    monkeypatch.setattr(gq, "filing_vocabulary", _fake_vocab)

    result = placement.propose_placement(
        text="Adding A6 placement classifier: propose→review→confirm with LLM grounding.",
        domains=["general", "banking.policy"],
    )

    assert result["text_length"] > 0
    assert result["proposals"]["domain"]["value"] == "banking.policy"
    assert result["proposals"]["domain"]["known"] is True
    assert result["proposals"]["title"]["value"] == "Canvas A6 Roadmap Notes"
    assert result["proposals"]["project"]["value"] == "artmind_canvas"
    assert result["proposals"]["project"]["known"] is True
    assert result["proposals"]["tags"]["value"] == ["roadmap"]
    assert result["proposals"]["target_file_hint"] == "canvas-a6-notes"
    assert result["proposals"]["reason"].startswith("roadmap update")


def test_propose_placement_survives_graph_lookup_failure(monkeypatch):
    """When the vocabulary lookup fails the classifier still returns a proposal
    (just without grounding). No exception should escape."""
    import artmind.graph_query as gq

    def raising_vocab(*a, **k):
        raise RuntimeError("neo4j is down")

    monkeypatch.setattr(gq, "filing_vocabulary", raising_vocab)

    import artmind.extraction as extraction
    monkeypatch.setattr(
        extraction,
        "call_llm",
        lambda model, prompt: json.dumps({"domain": "general", "domain_confidence": 0.5}),
    )

    result = placement.propose_placement(text="a note", domains=["general"])
    assert result["proposals"]["domain"]["value"] == "general"
    assert result["vocabulary"] == {"project": [], "area": [], "tags": [], "domain": []}


def test_propose_placement_rejects_empty_text():
    with pytest.raises(ValueError, match="text is required"):
        placement.propose_placement(text="")
    with pytest.raises(ValueError, match="text is required"):
        placement.propose_placement(text="   \n\t ")
