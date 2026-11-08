"""The answer judge (P3 spec §9.2): one LLM verdict per question pair, checked and cached. Fake LLM."""

import json

import pytest

from artmind.traces import judge


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


QUESTION, EXPECTED = "When did the invented project end?", "It ended in March; the later note supersedes the first."
LEFT, RIGHT = "It ended in March, per the later note.", "It ended in February."


def _llm(answer: dict):
    calls = []

    def call(prompt):
        calls.append(prompt)
        return json.dumps(answer)

    call.calls = calls
    return call


def test_the_prompt_holds_the_question_the_note_and_both_answers_unlabelled():
    p = judge.prompt(QUESTION, EXPECTED, LEFT, RIGHT)
    assert QUESTION in p and EXPECTED in p and LEFT in p and RIGHT in p
    assert "classic" not in p.lower() and "bundle" not in p.lower()  # the judge never learns which contract


def test_a_verdict_is_parsed_and_normalised():
    llm = _llm({"verdict": "materially different", "better": "A", "reason": "B gives the wrong month."})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    assert (v.verdict, v.better) == ("material", "left") and "wrong month" in v.reason


@pytest.mark.parametrize("raw", ["not json", json.dumps({"verdict": "odd"}), json.dumps({"better": "A"})])
def test_a_bad_verdict_is_unjudged_not_a_crash(raw):
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=lambda p: raw, model="m")
    assert v.verdict == "unjudged" and v.better is None


def test_order_is_randomised_and_undone():
    """The judge sees the pair in a seeded order; the stored verdict is about left/right, not A/B."""
    llm = _llm({"verdict": "material", "better": "B", "reason": "r"})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m", swap=True)
    assert v.better == "left"  # B in the swapped prompt was LEFT
    assert llm.calls[0].index(RIGHT) < llm.calls[0].index(LEFT)


def test_verdicts_are_cached_by_content():
    llm = _llm({"verdict": "same", "better": "equal", "reason": "r"})
    a = judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    b = judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, RIGHT, llm=llm, model="m")
    assert len(llm.calls) == 1 and a == b
    judge.cached_judge("run1", "run2", 1, QUESTION, EXPECTED, LEFT, "changed answer", llm=llm, model="m")
    assert len(llm.calls) == 2


def test_an_empty_answer_needs_no_llm():
    llm = _llm({"verdict": "same", "better": "equal", "reason": "r"})
    v = judge.judge_pair(QUESTION, EXPECTED, LEFT, "", llm=llm, model="m")
    assert (v.verdict, v.better, llm.calls) == ("material", "left", [])
