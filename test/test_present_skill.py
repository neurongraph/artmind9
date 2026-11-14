"""artmind-present: when to draw what (spec 2026-11-14 §6)."""

import json
import re
from pathlib import Path

from artmind.webui.graph_routes import prepare_cypher
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "artmind" / "skills" / "artmind-present" / "SKILL.md"
QUERY_SKILL = ROOT / "artmind" / "skills" / "artmind-query" / "SKILL.md"
ROUTING_LINE = "Asked to show, draw or graph the answer: after retrieval, load `artmind-present`."


def test_frontmatter_names_the_directory():
    assert re.match(r"---\nname: artmind-present\ndescription: .+\n---\n", SKILL.read_text())


def test_stays_short():
    assert len(SKILL.read_text().splitlines()) <= 100


def test_both_chat_profiles_but_not_benchmarks():
    assert "artmind-present" in QA_PROFILE.skills and "artmind-present" in ADMIN_PROFILE.skills
    assert "artmind-present" not in BENCHMARK_PROFILE.skills


def test_artmind_query_routes_to_it_in_one_line():
    text = QUERY_SKILL.read_text()
    assert text.count("artmind-present") == 1 and ROUTING_LINE in text


def test_the_example_block_is_one_the_endpoint_accepts():
    text = SKILL.read_text()
    block = text.split("```artmind-graph\n", 1)[1].split("\n```", 1)[0]
    spec = json.loads(block)
    assert set(spec) == {"domain", "title", "cypher"}
    prepare_cypher(spec["cypher"], require_domains=True)


def test_every_cli_command_it_names_exists():
    from artmind.cli import cli

    paths = set(re.findall(r"`artmind ((?:query )?(?:graph )?[a-z][a-z0-9-]+)", SKILL.read_text()))
    assert "query graph text2cypher" in paths
    for path in paths:
        node = cli
        for part in path.split():
            node = node.commands[part]


def test_personas_name_it():
    for persona in ("artmind.md", "artmind-admin.md"):
        assert "artmind-present" in (ROOT / "artmind" / "opencode" / "agent" / persona).read_text()
