"""`topic index --dryRun`: reports the sweep's counts, sends no write; the effect matches the real run."""
import json

from click.testing import CliRunner

from _dry_run import WritableRecorder, assert_effect_shape, assert_no_write_cypher
from artmind import graph_query
from artmind.cli import cli
from artmind.topics import index, store

META = {"artmind_topic": 1, "topic": "Notice", "slug": "notice", "domains": ["j"], "anchor": {"kind": "topic"},
        "built": "2026-11-02", "sources": [{"doc_id": "d1", "name": "a.md", "passages": ["p1"]}], "checks": {}}


def _run(tmp_path, *args):
    return CliRunner().invoke(cli, ["topic", "index", "--out", str(tmp_path), *args, "--compact"])


def _graph(monkeypatch, nodes):
    recorder = WritableRecorder(lambda c, p: nodes if "RETURN p.slug AS slug" in c else [])
    monkeypatch.setattr(graph_query, "neo4j_session", lambda: recorder)
    monkeypatch.setattr(graph_query, "read_session", lambda: recorder)
    monkeypatch.setattr(index, "_embedding_setup", lambda embed=None, key=None, cache=True: (lambda t: [1.0], "k", None))
    return recorder


def test_dry_run_writes_nothing_and_reports_counts(tmp_path, monkeypatch):
    store.write_page(tmp_path, "notice", META, "## Overview\nText.")
    recorder = _graph(monkeypatch, [{"slug": "stale", "domains": ["j"], "has_embedding": True, "embed_key": "k"}])
    result = _run(tmp_path, "--dryRun")
    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["dry_run"] is True
    assert (out["indexed"], out["updated"], out["deleted"], out["embedded"]) == (1, 1, 1, 1)
    assert_no_write_cypher(recorder)
    assert_effect_shape(out["effect"])
    assert out["effect"]["changes"] == [{"kind": "topic_index", "count": 2, "sample": []}]
    assert out["effect"]["llm"]["calls"] == 0


def test_a_real_run_writes_and_reports_the_same_effect(tmp_path, monkeypatch):
    store.write_page(tmp_path, "notice", META, "## Overview\nText.")
    # the fake graph does not apply writes, so the new page's node is listed up front, unembedded
    nodes = [{"slug": "stale", "domains": ["j"], "has_embedding": True, "embed_key": "k"},
             {"slug": "notice", "domains": ["j"], "has_embedding": False, "embed_key": None}]
    _graph(monkeypatch, nodes)
    dry = json.loads(_run(tmp_path, "--dryRun").stdout)
    recorder = _graph(monkeypatch, nodes)
    real = json.loads(_run(tmp_path).stdout)
    assert "dry_run" not in real
    assert dry["effect"] == real["effect"]
    assert recorder.queries_containing("DETACH DELETE")
    assert recorder.queries_containing("MERGE (p:TopicPage {slug: $slug})")
