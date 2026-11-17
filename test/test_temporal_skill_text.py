"""The query skill describes entity-history --asOf as a point in valid time and --knownAsOf
as record time (spec 2026-11-15 §1.5, §6.2)."""
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"


def test_temporal_reference_does_not_call_entity_history_a_point_in_time_filter():
    text = (SKILL / "references" / "temporal.md").read_text(encoding="utf-8")
    assert "genuine point-in-time" not in text
    assert "entity-history --asOf" in text and "point in valid time" in text
    assert "--knownAsOf" in text and "_record_time_approx" in text


def test_entity_history_help_says_point():
    from artmind.cli import cli

    cmd = cli.commands["query"].commands["entity-history"]
    help_text = next(p.help for p in cmd.params if p.name == "as_of")
    assert help_text.startswith("A point in valid time: facts whose own date window contains this date")
    known = next(p.help for p in cmd.params if p.name == "known_as_of")
    assert "record time" in known
