"""The `:TopicPage` graph copy is gone (spec 2026-11-10 §5.2): agents read topic plans,
never pages, so nothing creates, reads or indexes page nodes; only the one-off migration
that drops them names the label."""
from pathlib import Path

import artmind

ROOT = Path(artmind.__file__).parent
MIGRATION = ROOT / "migrations" / "drop_topic_page_graph_copy.py"


def test_only_the_migration_names_the_label():
    hits = [p for p in ROOT.rglob("*.py") if p != MIGRATION and "TopicPage" in p.read_text(encoding="utf-8")]
    assert hits == []


def test_setup_creates_no_topic_page_constraint_or_index():
    from _neo4j_recorder import Recorder

    from artmind import setup

    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    for name in ("TopicPage", "topic_page_slug", "topic_embedding", "topic_text_ft"):
        assert name not in text


def test_the_topic_index_module_and_command_are_gone():
    from click.testing import CliRunner

    from artmind.cli import cli

    assert not (ROOT / "topics" / "index.py").exists()
    result = CliRunner().invoke(cli, ["topic", "index", "--help"])
    assert result.exit_code != 0
