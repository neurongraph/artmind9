"""`topic refresh --dryRun`: nothing is rebuilt; the effect sums the pages' plans."""
import json
from unittest.mock import patch

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.topics import store
from artmind.topics.gather import Material, Source

META = {"artmind_topic": 1, "topic": "Notice", "slug": "notice", "domains": ["j"], "anchor": {"kind": "topic"},
        "built": "2026-11-02", "sources": [], "checks": {}}
LINKS = {"c1_p01": {"path": "p/one.md", "title": "One", "doc_id": "D1", "name": "one.md", "version": "v1"}}


def _invoke(tmp_path, *extra):
    calls = []

    def map_llm(prompt):
        calls.append("map")
        return {"relevant": True, "notes": [{"claim": "Notice is 30 days.", "passages": ["c1_p01"]}]}

    def reduce_llm(prompt):
        calls.append("reduce")
        return "## Overview\n\nNotice is 30 days [#c1_p01]."

    mats = [Material("D1", "One", "one.md", "j", None, [Source("c1_p01", "c1", "Notice is 30 days.")])]
    with patch("artmind.topics.build.gather.gather", lambda *a, **k: (mats, LINKS)), \
         patch("artmind.topics.build._default_llms", lambda model: (map_llm, reduce_llm)), \
         patch("artmind.topics.build.anchor_mod.choose_anchor", lambda *a, **k: {"kind": "topic"}), \
         patch("artmind.topics.build._graph_domains", lambda d: []):
        result = CliRunner().invoke(cli, ["topic", "refresh", "--all", "--out", str(tmp_path), *extra, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), calls


def test_dry_run_rebuilds_nothing_and_matches_the_real_effect(tmp_path):
    store.write_page(tmp_path, "notice", META, "Old body")
    store.write_page(tmp_path, "other", {**META, "slug": "other", "topic": "Other"}, "Old body")
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.md")}

    dry, calls = _invoke(tmp_path, "--dryRun")

    assert calls == [] and {p.name: p.read_bytes() for p in tmp_path.glob("*.md")} == before
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"], llm_calls=4)
    assert dry["effect"]["changes"] == [{"kind": "topic_write", "count": 2, "sample": ["notice", "other"]}]
    assert dry["effect"]["records"] == [str(tmp_path / "notice.md"), str(tmp_path / "other.md")]

    real, calls = _invoke(tmp_path)

    assert calls.count("map") == 2
    assert "dry_run" not in real
    assert real["effect"] == dry["effect"]
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.md")} != before
