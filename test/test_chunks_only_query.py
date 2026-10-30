"""Query side of chunks-only domains (spec 2026-10-30)."""
import json

import pytest

import artmind.schema_validate as sv
from artmind import graph_query
from artmind.query_plan import planner
from artmind.staging_audit import empty_extractions


@pytest.fixture()
def flagged(tmp_path, monkeypatch):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ngraph_extraction: false\n")
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", schemas)


def test_audit_skips_chunks_only_domains(tmp_path, flagged):
    for domain in ("notes", "journal"):
        folder = tmp_path / "kg" / domain / "a"
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"path": f"{domain}.md", "_domain": domain}))
        (folder / "chunks.json").write_text(json.dumps([{"id": "c", "text": "x" * 300}]))
        (folder / "observations.json").write_text("[]")

    found = empty_extractions(tmp_path / "kg")

    assert [n["domain"] for n in found["notes"]] == ["journal"]


def test_overview_reports_the_flag_and_a_chunks_store(monkeypatch, flagged):
    monkeypatch.setattr(graph_query, "_run_read_query", lambda cypher, params: [
        {"domain": "notes", "parts": [{"k": "documents", "c": 4, "names": ["a.md"]}]},
        {"domain": "journal", "parts": [{"k": "documents", "c": 2, "names": ["b.md"]},
                                        {"k": "entities", "c": 9, "names": None}]},
    ])
    import artmind.structured.registry as registry

    monkeypatch.setattr(registry, "list_tables", lambda *a, **k: [])

    by_domain = {r["domain"]: r for r in graph_query.domains_overview()["rows"]}

    assert by_domain["notes"]["graph_extraction"] is False
    assert by_domain["notes"]["stores"] == ["chunks"]
    assert "graph_extraction" not in by_domain["journal"]
    assert by_domain["journal"]["stores"] == ["graph"]


def test_chunks_only_scope_helper():
    rows = [{"domain": "notes", "graph_extraction": False},
            {"domain": "notes.sub", "graph_extraction": False},
            {"domain": "journal"}]
    assert planner._chunks_only(["notes"], rows) is True
    assert planner._chunks_only(["notes", "journal"], rows) is False
    assert planner._chunks_only(["unknown"], rows) is False
    assert planner._chunks_only([], rows) is False
