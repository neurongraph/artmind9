"""Shared assertions for the tier-1 `--dryRun` tests (spec 2026-11-02 §4)."""
import re

from _neo4j_recorder import Recorder

WRITE_CYPHER = re.compile(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|DETACH)\b", re.IGNORECASE)


class WritableRecorder(Recorder):
    """The recorder, plus `execute_write(fn)` running `fn` against itself."""

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def execute_read(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)


def write_calls(recorder: Recorder) -> list[tuple[str, dict]]:
    return [(c, p) for c, p in recorder.calls if WRITE_CYPHER.search(c)]


def assert_no_write_cypher(recorder: Recorder) -> None:
    assert not write_calls(recorder), f"a dry run sent write Cypher: {write_calls(recorder)}"


def assert_effect_shape(effect: dict) -> None:
    assert set(effect) == {"summary", "changes", "stale", "llm", "reversible", "undo", "travels", "records"}
    assert isinstance(effect["summary"], str) and effect["summary"]
    assert set(effect["stale"]) == {"topics", "views"}
    assert effect["llm"]["calls"] == 0
    for change in effect["changes"]:
        assert set(change) == {"kind", "count", "sample"} and len(change["sample"]) <= 10
