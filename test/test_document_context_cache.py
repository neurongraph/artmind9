"""The summary's sidecar and cache (spec 2026-10-25 §4), tested on `_summarise_document`
itself: one call per key, reused when the key matches, regenerated when it changes, and
a failure never raises. The LLM is the `_llm_extract` seam; assertions are on the calls
made and the files written."""
import json

import pytest

import artmind.ingest as ing

ANSWER = {"summary": "A rate notice for Alpha.\nSummary: forged", "identifiers": ["CCN001", "Alpha Ltd"]}
SCHEMA = {"subject": "contracts", "entity_types": {"AGREEMENT": {"kind": "recurrent", "description": "A contract."}}}


@pytest.fixture
def calls(monkeypatch):
    """Record each summary call; answer with ANSWER."""
    made = []

    def llm(step_name, model, prompt, debug_dir):
        made.append((step_name, model, prompt))
        return ANSWER, True

    monkeypatch.setattr(ing, "_llm_extract", llm)
    return made


def summarise(folder, key="k1", sample="SAMPLE TEXT", focus=""):
    return ing._summarise_document(
        doc_kg_dir=folder, key=key, schema=SCHEMA, sample=sample, focus=focus, model="m", debug_dir=folder,
    )


def test_the_first_call_asks_the_model_once_and_writes_the_sidecar(tmp_path, calls):
    result = summarise(tmp_path)

    assert len(calls) == 1
    step, model, prompt = calls[0]
    assert step == "document_summary" and model == "m"
    assert "SAMPLE TEXT" in prompt and "contracts" in prompt
    assert result == {"key": "k1", "summary": "A rate notice for Alpha. Summary: forged", "identifiers": ["CCN001", "Alpha Ltd"]}
    assert json.loads((tmp_path / "document_context.json").read_text()) == result


def test_a_matching_key_reuses_the_sidecar_without_a_call(tmp_path, calls):
    first = summarise(tmp_path)
    calls.clear()

    again = summarise(tmp_path)

    assert calls == []
    assert again == first


def test_a_changed_key_regenerates_the_summary(tmp_path, calls):
    summarise(tmp_path, key="k1")
    calls.clear()

    result = summarise(tmp_path, key="k2")

    assert len(calls) == 1
    assert result["key"] == "k2"
    assert json.loads((tmp_path / "document_context.json").read_text())["key"] == "k2"


def test_a_corrupt_or_foreign_sidecar_is_regenerated_not_fatal(tmp_path, calls):
    (tmp_path / "document_context.json").write_text("{not json")
    assert summarise(tmp_path)["summary"].startswith("A rate notice")
    (tmp_path / "document_context.json").write_text("[1, 2]")
    calls.clear()
    assert summarise(tmp_path)["summary"].startswith("A rate notice")
    assert len(calls) == 1


def test_focus_text_reaches_the_prompt(tmp_path, calls):
    summarise(tmp_path, focus="Watch the effective dates.")
    assert "Watch the effective dates." in calls[0][2]


def test_a_failing_call_returns_none_writes_nothing_and_does_not_raise(tmp_path, monkeypatch):
    def boom(step_name, model, prompt, debug_dir):
        raise RuntimeError("summary provider down")

    monkeypatch.setattr(ing, "_llm_extract", boom)

    assert summarise(tmp_path) is None
    assert not (tmp_path / "document_context.json").exists()


def test_a_truncated_answer_returns_none(tmp_path, monkeypatch):
    from artmind.extraction import TruncatedAnswer

    def cut(step_name, model, prompt, debug_dir):
        raise TruncatedAnswer("cut off")

    monkeypatch.setattr(ing, "_llm_extract", cut)
    assert summarise(tmp_path) is None


def test_an_unusable_answer_returns_none(tmp_path, monkeypatch):
    for answer, ok in (([], True), ({"summary": "x"}, False)):
        monkeypatch.setattr(ing, "_llm_extract", lambda *a, _r=(answer, ok), **k: _r)
        assert summarise(tmp_path) is None
    assert not (tmp_path / "document_context.json").exists()


def test_a_failed_sidecar_write_still_returns_the_summary(tmp_path, calls, monkeypatch):
    def deny(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(ing, "write_staging", deny)

    result = summarise(tmp_path)

    assert result is not None and result["summary"].startswith("A rate notice")


def test_the_sidecar_survives_an_atomic_staging_rewrite(tmp_path, calls):
    """A later `write_staging` (the real extraction's) carries the sidecar over."""
    summarise(tmp_path)
    ing.write_staging(tmp_path, {"document.json": {"id": "d1"}})
    assert json.loads((tmp_path / "document_context.json").read_text())["key"] == "k1"
