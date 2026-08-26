"""projection synthesize — unit tests (no Neo4j, no LLM).

Per CLAUDE.md: assert on parameters actually sent and which query ran, never
on counts; every fake session gets `execute_write`/`execute_read` that
actually call their argument (a bare MagicMock's `execute_write` returns a
MagicMock WITHOUT calling it).
"""
from unittest.mock import MagicMock, patch

from artmind.synthesize import build_synthesis_prompt, classify_key, synthesize_key


def _row(**overrides):
    row = {
        "id": "eid1", "key": "widget rate|RATE_ENTRY|banking.reference",
        "name": "Widget Rate", "entity_class": "RATE_ENTRY",
        "observation_count": 3, "current_hash": "abc", "synth_hash": None,
        "has_open_conflict": False,
    }
    row.update(overrides)
    return row


# ── classify_key ─────────────────────────────────────────────────────────────

def test_classify_skips_open_conflict():
    assert classify_key(_row(has_open_conflict=True), 2, False) == "skipped_open_conflict"


def test_classify_skips_too_few_observations():
    assert classify_key(_row(observation_count=1), 2, False) == "skipped_too_few_observations"


def test_classify_synthesizes_at_the_threshold():
    assert classify_key(_row(observation_count=2), 2, False) == "synthesize"


def test_classify_skips_unchanged():
    row = _row(synth_hash="abc", current_hash="abc")
    assert classify_key(row, 2, False) == "skipped_unchanged"


def test_classify_synthesizes_when_hash_changed():
    row = _row(synth_hash="old", current_hash="new")
    assert classify_key(row, 2, False) == "synthesize"


def test_classify_synthesizes_when_never_synthesized():
    row = _row(synth_hash=None, current_hash="new")
    assert classify_key(row, 2, False) == "synthesize"


def test_classify_force_overrides_unchanged():
    row = _row(synth_hash="abc", current_hash="abc")
    assert classify_key(row, 2, True) == "synthesize"


# ── build_synthesis_prompt ───────────────────────────────────────────────────

def test_prompt_has_no_historical_marking():
    # synthesize reads only :Observation (latest) -- history is structurally
    # excluded, so there is nothing to mark HISTORICAL, unlike the old
    # chunk-based consolidation this module replaces.
    observations = [{"id": "o1", "description": "A widget rate.", "rate_value": 4.5}]
    prompt = build_synthesis_prompt("Widget Rate", "RATE_ENTRY", observations)
    assert "HISTORICAL" not in prompt
    assert "rate_value: 4.5" in prompt
    assert "Widget Rate" in prompt


def test_prompt_drops_system_and_structural_keys():
    observations = [{
        "id": "o1", "key": "x|Y|z", "doc_id": "d1", "_valid_from": "2026-01-01",
        "canonical_name": "widget rate", "description": "text", "rate_value": 4.5,
    }]
    prompt = build_synthesis_prompt("Widget Rate", "RATE_ENTRY", observations)
    assert "doc_id" not in prompt
    assert "_valid_from" not in prompt
    assert "canonical_name" not in prompt
    assert "rate_value: 4.5" in prompt


def test_prompt_asks_for_disagreement_kept_side_by_side():
    prompt = build_synthesis_prompt("E", "C", [{"id": "o1"}])
    assert "side by side" in prompt


# ── synthesize_key: embedding safety (never null an embedding) ─────────────

def _patch_common(monkeypatch, embed_fn):
    monkeypatch.setattr(
        "artmind.synthesize.projection.read_latest_observations",
        lambda tx, key: [{"id": "o1", "description": "A widget.", "rate_value": 4.5}],
    )
    monkeypatch.setattr("artmind.synthesize.call_llm", lambda model, prompt: "{}")
    monkeypatch.setattr(
        "artmind.synthesize.parse_json_response",
        lambda raw: {"description": "A clean description."},
    )
    monkeypatch.setattr("artmind.synthesize.embed_text", embed_fn)


def _mock_session(run_recorder=None):
    session = MagicMock()
    session.execute_read.side_effect = lambda fn: fn(session)
    session.execute_write.side_effect = lambda fn: fn(session)
    if run_recorder is not None:
        session.run.side_effect = lambda cypher, **kw: run_recorder.append((cypher, kw)) or MagicMock()
    return session


def test_synthesize_key_writes_embedding_and_clears_stale_in_same_transaction(monkeypatch):
    order: list[str] = []

    def fake_embed(model, text):
        order.append("embed")
        return [0.1, 0.2, 0.3]

    _patch_common(monkeypatch, fake_embed)

    def fake_rebuild_key(tx, key, *, synthesis=None, **kw):
        order.append("rebuild_key")
        assert synthesis["text"] == "A clean description."
        return "rebuilt"

    monkeypatch.setattr("artmind.synthesize.projection.rebuild_key", fake_rebuild_key)

    run_calls: list[tuple] = []
    session = _mock_session(run_calls)

    with patch("artmind.synthesize.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = synthesize_key(
            ("widget rate", "RATE_ENTRY", "banking.reference"), "Widget Rate", "RATE_ENTRY",
            model="test-model", embed_model="test-embed",
        )

    assert result["status"] == "synthesized"
    # embedding computed BEFORE rebuild_key ran (before any write at all)
    assert order[0] == "embed"
    assert "rebuild_key" in order

    synthesis_writes = [c for c in run_calls if "MERGE (s:Synthesis" in c[0]]
    assert len(synthesis_writes) == 1
    assert synthesis_writes[0][1]["props"]["text"] == "A clean description."

    embedding_writes = [c for c in run_calls if "e.embedding = $embedding" in c[0]]
    assert len(embedding_writes) == 1
    assert embedding_writes[0][1]["embedding"] == [0.1, 0.2, 0.3]
    assert "embedding_stale = false" in embedding_writes[0][0]


def test_synthesize_key_skips_whole_entity_on_embed_failure_never_writes(monkeypatch):
    def failing_embed(model, text):
        raise RuntimeError("embed service down")

    _patch_common(monkeypatch, failing_embed)

    rebuild_calls: list = []
    monkeypatch.setattr(
        "artmind.synthesize.projection.rebuild_key",
        lambda *a, **kw: rebuild_calls.append(1) or "rebuilt",
    )

    run_calls: list[tuple] = []
    session = _mock_session(run_calls)

    with patch("artmind.synthesize.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = synthesize_key(
            ("widget rate", "RATE_ENTRY", "banking.reference"), "Widget Rate", "RATE_ENTRY",
            model="test-model", embed_model="test-embed",
        )

    assert result["status"] == "failed_embedding"
    # Nothing written at all -- no Synthesis node, no rebuild_key call, and
    # definitely never a null/None embedding written anywhere.
    assert rebuild_calls == []
    assert run_calls == []


def test_synthesize_key_skips_on_llm_failure(monkeypatch):
    monkeypatch.setattr(
        "artmind.synthesize.projection.read_latest_observations",
        lambda tx, key: [{"id": "o1", "description": "A widget."}],
    )

    def failing_llm(model, prompt):
        raise RuntimeError("llm down")

    monkeypatch.setattr("artmind.synthesize.call_llm", failing_llm)
    session = _mock_session()

    with patch("artmind.synthesize.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = synthesize_key(
            ("widget rate", "RATE_ENTRY", "banking.reference"), "Widget Rate", "RATE_ENTRY",
            model="test-model", embed_model="test-embed",
        )

    assert result["status"] == "failed_llm"


def test_synthesize_key_skips_when_no_observations(monkeypatch):
    monkeypatch.setattr(
        "artmind.synthesize.projection.read_latest_observations", lambda tx, key: []
    )
    session = _mock_session()

    with patch("artmind.synthesize.neo4j_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = synthesize_key(
            ("widget rate", "RATE_ENTRY", "banking.reference"), "Widget Rate", "RATE_ENTRY",
            model="test-model", embed_model="test-embed",
        )

    assert result["status"] == "skipped_no_observations"
