"""`ingest refine-graph --documentName`: same-as proposals within one document,
and a count of the proposed merges whose names occur in a common document
(study item 3: one amendment extracted as many entities, each named after the
label its chunk used). No Neo4j: the session answers by the Cypher it is given."""
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

import artmind.refine_graph as rg


class FakeSession:
    """Answers each query by a marker in its text; records every run."""

    def __init__(self, answers):
        self.answers = answers
        self.runs = []

    def run(self, cypher, **params):
        self.runs.append((cypher, params))
        for marker, rows in self.answers.items():
            if marker in cypher:
                return type("R", (), {"data": lambda s: rows, "__iter__": lambda s: iter(rows)})()
        raise AssertionError(f"unexpected query: {cypher}")


@pytest.fixture
def graph(monkeypatch):
    # One amendment, labelled three ways by three chunks; one unrelated entity.
    labels = [
        {"name": "Change Notice CCN001", "entity_class": "AMENDMENT"},
        {"name": "Change Notice CCN-001", "entity_class": "AMENDMENT"},
        {"name": "Change Notice CCN001 (Schedule 2)", "entity_class": "AMENDMENT"},
        {"name": "Alpha Ltd", "entity_class": "PARTY"},
    ]
    session = FakeSession({
        "RETURN DISTINCT o.name AS name": labels,
        "collect(DISTINCT e._domain) AS domains": [],
        "collect(DISTINCT o.doc_id) AS docs": [
            {"name": "Change Notice CCN001", "docs": ["doc-1"]},
            {"name": "Change Notice CCN-001", "docs": ["doc-1"]},
            {"name": "Change Notice CCN001 (Schedule 2)", "docs": ["doc-1", "doc-2"]},
        ],
    })

    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(rg, "neo4j_session", fake_session)
    monkeypatch.setattr(
        rg, "llm_merge_cluster",
        lambda cluster, model, timeout: {n: "Change Notice CCN001" for n in cluster},
    )
    monkeypatch.setattr("utils.functions.load_env", lambda: {})
    return session


def test_a_document_scope_clusters_only_that_documents_observation_names(graph):
    report = rg.refine_graph(
        domain="contracts", name_filter=None, model="m", similarity_threshold=0.7,
        dry_run=True, output_file=None, from_file=None, document_name="Contract Amendment CCN001.pdf",
    )

    cypher, params = graph.runs[0]
    assert "Observation" in cypher and "Document" in cypher
    assert params["documentName"] == "Contract Amendment CCN001.pdf" and params["domain"] == "contracts"
    assert report["proposed_merges"] == {
        "Change Notice CCN-001": "Change Notice CCN001",
        "Change Notice CCN001 (Schedule 2)": "Change Notice CCN001",
    }
    assert "Alpha Ltd" not in report["proposed_merges"]


def test_the_report_counts_merges_whose_names_share_a_document(graph):
    report = rg.refine_graph(
        domain="contracts", name_filter=None, model="m", similarity_threshold=0.7,
        dry_run=True, output_file=None, from_file=None, document_name="Contract Amendment CCN001.pdf",
    )

    assert report["within_document_merges"] == 2  # both aliases share doc-1 with the canonical


def test_a_merge_across_documents_is_not_counted_as_within_one(graph, monkeypatch):
    graph.answers["collect(DISTINCT o.doc_id) AS docs"] = [
        {"name": "Change Notice CCN001", "docs": ["doc-1"]},
        {"name": "Change Notice CCN-001", "docs": ["doc-2"]},
        {"name": "Change Notice CCN001 (Schedule 2)", "docs": ["doc-1"]},
    ]
    report = rg.refine_graph(
        domain="contracts", name_filter=None, model="m", similarity_threshold=0.7,
        dry_run=True, output_file=None, from_file=None, document_name="x.pdf",
    )
    assert report["within_document_merges"] == 1


def test_without_a_document_the_old_graph_wide_query_runs(graph, monkeypatch):
    graph.answers["RETURN DISTINCT e.name AS name"] = [
        {"name": "Alpha Ltd", "entity_class": "PARTY"}, {"name": "Alpha Limited", "entity_class": "PARTY"},
    ]
    monkeypatch.setattr(
        rg, "llm_merge_cluster",
        lambda cluster, model, timeout: {"Alpha Ltd": "Alpha Ltd", "Alpha Limited": "Alpha Ltd"},
    )

    report = rg.refine_graph(
        domain=None, name_filter=None, model="m", similarity_threshold=0.7,
        dry_run=True, output_file=None, from_file=None,
    )

    assert "Observation" not in graph.runs[0][0]
    assert report["proposed_merges"] == {"Alpha Limited": "Alpha Ltd"}
    assert "within_document_merges" in report


def test_the_cli_takes_document_name_and_prints_the_within_document_count(monkeypatch, tmp_path):
    import artmind.cli as cli_module

    seen = {}

    def fake_refine(**kwargs):
        seen.update(kwargs)
        return {"proposed_merges": {"a": "b", "c": "b"}, "stats": {}, "within_document_merges": 2}

    monkeypatch.setattr(cli_module, "refine_graph", fake_refine)
    monkeypatch.setattr(cli_module, "REFINE_DIR", tmp_path)

    result = CliRunner().invoke(
        cli_module.cli,
        ["ingest", "refine-graph", "--dry-run", "--domain", "contracts", "--documentName", "CCN001.pdf"],
    )

    assert result.exit_code == 0, result.output
    assert seen["document_name"] == "CCN001.pdf"
    assert "2 merge(s) proposed" in result.output
    assert "2 within one document" in result.output
