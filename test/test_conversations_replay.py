"""Replay transcript builder (spec 2026-10-16 §4.1). Pure: no DB."""

from artmind.conversations import replay


def _turn(seq, q, a, status="done"):
    return {"seq": seq, "user_text": q, "answer_text": a, "status": status}


def test_no_replayable_turns_returns_none():
    assert replay.build_context([]) is None
    assert replay.build_context([_turn(0, "q", None), _turn(1, "q", "a", status="error")]) is None
    assert replay.build_context([_turn(0, "q", "a", status="interrupted")]) is None


def test_pairs_are_emitted_oldest_first_inside_the_markers():
    result = replay.build_context([_turn(0, "q1", "a1"), _turn(1, "q2", "a2")], budget=10_000)
    assert (result.included, result.omitted) == (2, 0)
    lines = result.text.splitlines()
    assert lines[0].startswith("[Restored conversation.")
    assert "omitted" not in lines[0]
    assert lines[1:3] == ["User: q1", "Assistant: a1"]
    assert lines[3:5] == ["User: q2", "Assistant: a2"]
    assert lines[-1] == "[End of restored conversation. The new message follows.]"


def test_budget_drops_the_oldest_turns_and_says_so():
    turns = [_turn(i, f"question {i}", "x" * 100) for i in range(5)]
    one_pair = len("User: question 0\nAssistant: ") + 100 + 2
    result = replay.build_context(turns, budget=one_pair * 2 + 5)
    assert (result.included, result.omitted) == (2, 3)
    assert "User: question 3" in result.text and "User: question 4" in result.text
    assert "User: question 2" not in result.text
    assert "3 earlier turns omitted" in result.text.splitlines()[0]


def test_single_omitted_turn_uses_singular_wording():
    turns = [_turn(0, "old", "x" * 50), _turn(1, "new", "y" * 50)]
    result = replay.build_context(turns, budget=len("User: new\nAssistant: ") + 50 + 5)
    assert result.omitted == 1
    assert "1 earlier turn omitted" in result.text.splitlines()[0]


def test_an_oversized_newest_turn_is_truncated_not_skipped():
    result = replay.build_context([_turn(0, "q", "z" * 5_000)], budget=200)
    assert result.included == 1
    body = result.text.split("\n", 1)[1]
    assert "User: q" in body
    assert "…" in body
    assert len(result.text) < 500


def test_non_replayable_turns_are_ignored_and_not_counted_as_omitted():
    turns = [_turn(0, "q0", "a0"), _turn(1, "q1", None, status="error"), _turn(2, "q2", "a2")]
    result = replay.build_context(turns, budget=10_000)
    assert (result.included, result.omitted) == (2, 0)
    assert "q1" not in result.text


def test_budget_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("ARTMIND_REPLAY_BUDGET_CHARS", raising=False)
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "500")
    assert replay.budget_chars() == 500
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "not-a-number")
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
    monkeypatch.setenv("ARTMIND_REPLAY_BUDGET_CHARS", "-5")
    assert replay.budget_chars() == replay.DEFAULT_BUDGET_CHARS
