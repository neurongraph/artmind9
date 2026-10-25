"""A truncated or malformed LLM answer must cost one chunk step at most, never
the file (extraction-failure study, items 1 and 5).

Seen on real contracts: the entities answer for a table-heavy chunk hit the
output-token limit, `json_repair` closed the JSON silently, the last entity had
no `entity_class`, and `entities_list_text` raised `KeyError` while building the
properties prompt, which aborted the whole file. No Neo4j, no LLM: the provider
client and the extraction seams are mocked. Synthetic text only."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from _fake_credentials import FAKE_CREDENTIAL
from test_ingest_concurrency import _setup_extract_env

import artmind.extraction as extraction
import artmind.ingest as ing
from artmind.extraction import TruncatedAnswer, entities_list_text
from artmind.llm_providers import TruncatedText, _reset_clients, call_llm_ollama, call_llm_openrouter
from conftest import strip_document_context


@pytest.fixture(autouse=True)
def _clear_clients():
    _reset_clients()
    yield
    _reset_clients()


# ── the prompt builders no longer trust every entity to be complete ──────────


def test_entities_list_text_tolerates_an_incomplete_entity():
    text = entities_list_text([
        {"id": "e1", "entity_class": "Contract", "name": "Alpha"},
        {"id": "e2", "name": "Half"},
    ])
    assert "e1 (Contract): Alpha" in text
    assert "e2" in text and "Half" in text


# ── providers say when the answer was cut off ────────────────────────────────


def _openai_response(content, finish_reason):
    choice = MagicMock(message=MagicMock(content=content), finish_reason=finish_reason)
    return MagicMock(choices=[choice])


def test_openrouter_marks_a_length_stop_as_truncated():
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as openai_cls:
        openai_cls.return_value.chat.completions.create.return_value = _openai_response('[{"id": "a"', "length")
        result = call_llm_openrouter("m", "p", 30, env)
    assert isinstance(result, TruncatedText)
    assert str(result) == '[{"id": "a"'


def test_openrouter_leaves_a_normal_stop_a_plain_string():
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as openai_cls:
        openai_cls.return_value.chat.completions.create.return_value = _openai_response("[]", "stop")
        result = call_llm_openrouter("m", "p", 30, env)
    assert result == "[]" and not isinstance(result, TruncatedText)


def test_openrouter_sends_max_tokens_only_when_asked():
    env = {"ARTMIND_OPENROUTER_API_KEY": FAKE_CREDENTIAL}
    with patch("artmind.llm_providers.openai.OpenAI") as openai_cls:
        create = openai_cls.return_value.chat.completions.create
        create.return_value = _openai_response("[]", "stop")
        call_llm_openrouter("m", "p", 30, env)
        call_llm_openrouter("m", "p", 30, env, max_tokens=32000)
    first, second = (c.kwargs for c in create.call_args_list)
    assert "max_tokens" not in first
    assert second["max_tokens"] == 32000


def test_ollama_marks_a_length_stop_as_truncated_and_caps_with_num_predict():
    response = MagicMock(message=MagicMock(content='[{"id": "a"'), done_reason="length")
    with patch("artmind.llm_providers._ollama_client") as client:
        client.return_value.chat.return_value = response
        result = call_llm_ollama("m", "p", 30, max_tokens=9000)
    assert isinstance(result, TruncatedText)
    assert client.return_value.chat.call_args.kwargs["options"]["num_predict"] == 9000


# ── extract_with_retry: a cut-off answer is never repaired ───────────────────


def _scripted_llm(monkeypatch, answers):
    """Each call returns the next of `answers`; the max_tokens it was given is recorded."""
    seen = []

    def fake(model, prompt, call_type="chat", timeout=None, max_retries=None, max_tokens=None):
        seen.append(max_tokens)
        return answers[len(seen) - 1]

    monkeypatch.setattr(extraction, "call_llm", fake)
    return seen


COMPLETE = '[{"id": "a", "name": "A", "entity_class": "X", "type": "t"}]'


def test_an_answer_stopped_at_the_budget_is_not_asked_again(monkeypatch):
    """Every call already asks for the full output budget, so the same request
    would stop at the same place: no second call."""
    seen = _scripted_llm(monkeypatch, [TruncatedText('[{"id": "a", "name": "A"'), COMPLETE])
    assert extraction.extract_with_retry("s", "m", "p") == ([], False)
    assert len(seen) == 1


def test_an_answer_stopped_at_the_budget_raises_for_the_chunk_to_split(monkeypatch):
    seen = _scripted_llm(monkeypatch, [TruncatedText('[{"id": "a"')])
    with pytest.raises(TruncatedAnswer):
        extraction.extract_with_retry("s", "m", "p", raise_on_truncation=True)
    assert len(seen) == 1


def test_an_empty_answer_stopped_at_the_budget_is_a_truncation(monkeypatch):
    """A model that thinks can spend the whole budget before answering: the
    provider says "length" and the content is empty."""
    _scripted_llm(monkeypatch, [TruncatedText("")])
    with pytest.raises(TruncatedAnswer):
        extraction.extract_with_retry("s", "m", "p", raise_on_truncation=True)


def test_an_unclosed_json_array_counts_as_truncation_without_a_finish_reason(monkeypatch):
    seen = _scripted_llm(monkeypatch, ['[{"id": "a", "name": "A"}, {"id": "b", "na'] * 2)
    assert extraction.extract_with_retry("s", "m", "p") == ([], False)
    assert len(seen) == 2


def test_a_prose_answer_is_not_mistaken_for_truncation(monkeypatch):
    seen = _scripted_llm(monkeypatch, ["There are no entities in this text."])
    extraction.extract_with_retry("s", "m", "p")
    assert seen == [None]  # one attempt, no retry with more output room


def test_repairable_but_complete_json_is_still_accepted(monkeypatch):
    # a trailing comma is sloppy, not cut off: the array closes
    _scripted_llm(monkeypatch, ['[{"id": "a", "name": "A"},]'])
    items, ok = extraction.extract_with_retry("s", "m", "p")
    assert ok and items == [{"id": "a", "name": "A"}]


# ── splitting a chunk ────────────────────────────────────────────────────────


def test_split_repeats_the_table_header_at_the_row_boundary():
    rows = [f"| item {i} | {i * 10} |" for i in range(40)]
    text = "Rate card\n\n| Item | Rate |\n| --- | --- |\n" + "\n".join(rows)
    first, second = ing._split_chunk_text(text)
    assert "| Item | Rate |" in first and "| Item | Rate |" in second
    assert second.splitlines()[1].startswith("| ---")
    body = [line for piece in (first, second) for line in piece.splitlines() if line.startswith("| item")]
    assert body == rows  # nothing lost, nothing doubled


def test_split_prefers_a_paragraph_boundary():
    paras = [f"Paragraph {i} " + "word " * 40 for i in range(6)]
    first, second = ing._split_chunk_text("\n\n".join(paras))
    assert first.split("\n\n") == paras[:3] and second.split("\n\n") == paras[3:]


def test_a_chunk_too_small_to_split_returns_no_pieces():
    assert ing._split_chunk_text("short") == []


# ── extract_kg: bad items dropped, cut-off steps split, failures isolated ────


def _real_entity_list_builders(monkeypatch):
    """The prompt builders as far as they crashed: through entities_list_text."""
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: text)
    monkeypatch.setattr(
        ing, "build_properties_prompt",
        lambda text, ents, schema, vocabulary=None, anchor_date=None: "PROPS\n" + entities_list_text(ents),
    )
    monkeypatch.setattr(
        ing, "build_relationships_prompt",
        lambda text, ents, schema: "RELS\n" + entities_list_text(ents),
    )


def _entity(i, **extra):
    return {"id": f"t{i}", "entity_class": "Thing", "type": "t", "name": f"Thing {i}", **extra}


def test_an_entity_missing_id_name_or_class_is_dropped_and_the_file_completes(monkeypatch, tmp_path):
    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    _real_entity_list_builders(monkeypatch)
    prompts = []

    def llm(step_name, model, prompt, debug_dir):
        prompts.append(prompt)
        if step_name.endswith("_entities"):
            return ([
                _entity(1),
                {"id": "t2", "name": "No class", "type": "t"},  # no entity_class
                {"id": "t3", "entity_class": "Thing", "type": "t", "name": "  "},  # blank name
                {"id": "t4", "entity_class": "Thing", "name": "No type"},  # type is optional: kept
            ], True)
        return ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)

    doc_kg_dir = ing.extract_kg(file_result, "testdom", max_workers=1)

    assert doc_kg_dir is not None
    observations = json.loads((doc_kg_dir / "observations.json").read_text())
    assert sorted(o["name"] for o in observations) == ["No type", "Thing 1"]
    assert not any("No class" in p for p in prompts)


def test_a_cut_off_entities_answer_is_split_and_the_pieces_merged(monkeypatch, tmp_path):
    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    paras = [f"Clause{i} " + "text " * 30 for i in range(6)]
    chunk = Path(file_result["chunks_dir"]) / "chunk_001.md"
    chunk.write_text("\n\n".join(paras), encoding="utf-8")
    _real_entity_list_builders(monkeypatch)
    answered = []

    def llm(step_name, model, prompt, debug_dir):
        if "_entities" not in step_name:  # a split piece is "chunk_001_entities_p1"
            return ([], True)
        answered.append(prompt)
        if len(prompt) > 800:  # the whole chunk is too long to answer, each half is not
            raise TruncatedAnswer("cut off")
        clause = strip_document_context(prompt).split()[0]  # the piece's own first clause
        return ([_entity(1, name=f"Entity of {clause}")], True)  # local id "t1" in every piece

    monkeypatch.setattr(ing, "_llm_extract", llm)

    doc_kg_dir = ing.extract_kg(file_result, "testdom", max_workers=1)

    assert doc_kg_dir is not None
    assert len(answered) == 3  # the whole chunk, then its two halves
    names = sorted(o["name"] for o in json.loads((doc_kg_dir / "observations.json").read_text()))
    assert names == ["Entity of Clause0", "Entity of Clause3"]  # the shared local id did not collide
    assert ing._get_chunk_statuses(file_result["sha256"])[1]["entities_status"] == "ok"


def test_a_cut_off_answer_that_cannot_split_fails_only_its_chunk(monkeypatch, tmp_path):
    file_result = _setup_extract_env(monkeypatch, tmp_path, 2)  # "chunk body 1" is too small to split
    _real_entity_list_builders(monkeypatch)

    def llm(step_name, model, prompt, debug_dir):
        if step_name == "chunk_001_entities":
            raise TruncatedAnswer("cut off")
        return ([_entity(2)], True) if step_name.endswith("_entities") else ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)

    assert ing.extract_kg(file_result, "testdom", max_workers=1) is None
    assert file_result["failed_chunks"] == [1]
    rows = ing._get_chunk_statuses(file_result["sha256"])
    assert rows[1]["entities_status"] == "failed" and rows[2]["entities_status"] == "ok"


@pytest.mark.parametrize("workers", [1, 3])
def test_one_chunk_raising_leaves_the_others_cached_and_the_file_resumable(monkeypatch, tmp_path, workers):
    file_result = _setup_extract_env(monkeypatch, tmp_path, 4)
    _real_entity_list_builders(monkeypatch)
    asked = []
    broken = {"on": True}

    def llm(step_name, model, prompt, debug_dir):
        asked.append(step_name)
        if step_name == "chunk_003_entities" and broken["on"]:
            raise RuntimeError("provider exploded")
        return ([_entity(1)], True) if step_name.endswith("_entities") else ([], True)

    monkeypatch.setattr(ing, "_llm_extract", llm)

    # first run: the other three chunks finish and stay cached; the file is not staged
    assert ing.extract_kg(file_result, "testdom", max_workers=workers) is None
    assert file_result["failed_chunks"] == [3]
    assert "chunk(s) [3]" in file_result["kg_error"]
    rows = ing._get_chunk_statuses(file_result["sha256"])
    assert {seq: r["entities_status"] for seq, r in rows.items()} == {
        1: "ok", 2: "ok", 3: "failed", 4: "ok",
    }
    cache = next((ing.KG_DIR / "testdom").glob("*/chunks/*"))
    assert sorted(p.name for p in cache.glob("chunk_*.json")) == [
        "chunk_001.json", "chunk_002.json", "chunk_003.json", "chunk_004.json",
    ]
    assert not list((ing.KG_DIR / "testdom").glob("*/observations.json"))

    # resume: only the failed chunk's entities are asked again, then everything is staged
    broken["on"] = False
    asked.clear()
    doc_kg_dir = ing.extract_kg(file_result, "testdom", max_workers=workers)
    assert doc_kg_dir is not None
    assert [c for c in asked if c.endswith("_entities")] == ["chunk_003_entities"]
    assert len(json.loads((doc_kg_dir / "observations.json").read_text())) == 4


def test_a_step_is_marked_ok_only_once_the_chunk_json_is_on_disk(monkeypatch, tmp_path):
    """Entities used to be marked ok before the later steps ran and before the
    chunk JSON was written, so a crash in between left `ok` rows and no
    chunk_NNN.json: the resume treated the chunk as done and the merge skipped it."""
    file_result = _setup_extract_env(monkeypatch, tmp_path, 1)
    _real_entity_list_builders(monkeypatch)
    real_update = ing._update_chunk_step
    marks = []

    def spy_update(doc_sha256, seq, step, status):
        on_disk = bool(list((ing.KG_DIR / "testdom").glob("*/chunks/*/chunk_001.json")))
        marks.append((step, status, on_disk))
        return real_update(doc_sha256, seq, step, status)

    monkeypatch.setattr(ing, "_update_chunk_step", spy_update)
    monkeypatch.setattr(
        ing, "_llm_extract",
        lambda step_name, model, prompt, debug_dir: (
            ([_entity(1)], True) if step_name.endswith("_entities") else ([], True)
        ),
    )

    ing.extract_kg(file_result, "testdom", max_workers=1)

    ok_marks = [on_disk for _, status, on_disk in marks if status == "ok"]
    assert ok_marks and all(ok_marks)


# ── cut-off detection: brackets in prose, single quotes (review A3) ─────────


@pytest.mark.parametrize("answer, cut", [
    ('Here are the entities [see below]:\n[{"id": "a", "name": "x"}, {"id": "b", "na', True),
    ('Here are the entities [see below]:\n[{"id": "a", "name": "x"}]', False),
    ("[{'id': 'a', 'name': 'say \"hi'}]", False),
    ("[{'id': 'a', 'name': 'it", True),
    ('[{"id": "a", "name": "it\'s fine"}]', False),
    ('- item [x]\n[{"id": "a"}', True),
])
def test_cut_off_detection_reads_the_json_not_the_prose_around_it(answer, cut):
    assert extraction.is_cut_off(answer) is cut


# ── splitting halves near the middle (review A5) ─────────────────────────────


def test_a_split_prefers_a_line_near_the_middle_over_a_far_paragraph_break():
    lines = "\n".join(f"line {i} " + "word " * 8 for i in range(30))
    text = lines + "\n\nTail paragraph."
    first, second = ing._split_chunk_text(text)
    assert 0.35 < len(first) / len(text) < 0.65
    assert not second.startswith("\n") and not first.endswith("\n")


# ── evidence comes from the passage, not the context block ───────────────────


def test_a_context_quote_copied_from_the_document_context_is_dropped():
    block = "DOCUMENT CONTEXT ...\nSummary: This document is a First Amendment Agreement between A and B.\nPASSAGE TO EXTRACT FROM:"
    passage = "## 9 Term\nThe term runs until 28 February 2026."
    items = [
        {"source_id": "a", "target_id": "b", "context": "This document is a First Amendment Agreement between A and B."},
        {"source_id": "a", "target_id": "c", "context": "The term runs until 28 February 2026."},
        {"id": "e1", "properties": {"end_date": "2026-02-28", "context": "This  document is a first amendment agreement"}},
    ]

    assert ing._drop_context_echo(items, block, passage, 1, "relationship") == 2

    assert "context" not in items[0]
    assert items[1]["context"] == "The term runs until 28 February 2026."
    assert items[2]["properties"] == {"end_date": "2026-02-28"}


def test_without_a_context_block_nothing_is_dropped():
    items = [{"context": "anything"}]
    assert ing._drop_context_echo(items, "", "passage", 1, "entity") == 0
    assert items == [{"context": "anything"}]
