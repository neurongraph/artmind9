"""The query skill describes entity-history --asOf as a valid-time floor (spec 2026-11-15 §1.5)."""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"


def test_temporal_reference_does_not_call_entity_history_a_point_in_time_filter():
    text = (SKILL / "references" / "temporal.md").read_text(encoding="utf-8")
    assert "genuine point-in-time" not in text
    assert "entity-history --asOf" in text and "floor" in text


def test_entity_history_help_says_floor():
    from artmind.cli import cli

    cmd = cli.commands["query"].commands["entity-history"]
    help_text = next(p.help for p in cmd.params if p.name == "as_of")
    assert "floor" in help_text
