# test/test_retrieval_cli.py
import json

import yaml
from click.testing import CliRunner

from artmind.cli import cli
from artmind.retrieval_eval import labels as rl
from artmind.retrieval_eval import runner


def _labels_file(tmp_path):
    path = tmp_path / "retrieval_labels.yaml"
    path.write_text(yaml.safe_dump({"labels": [
        {"id": "r001", "question": "q", "domains": ["p"], "kind": "needle", "relevant": ["c1"]}]}))
    return path


def test_validate_ok(tmp_path):
    res = CliRunner().invoke(cli, ["retrieval", "labels", "validate", "--file", str(_labels_file(tmp_path)), "--compact"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["ok"] is True


def test_validate_reports_errors_and_exits_1(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("labels: [{id: r1}]")
    res = CliRunner().invoke(cli, ["retrieval", "labels", "validate", "--file", str(path), "--compact"])
    assert res.exit_code == 1
    assert json.loads(res.output)["errors"]


def test_eval_writes_a_run_and_compares(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_default_search",
                        lambda d, q, k, div: {"rows": [{"chunk": {"id": "c1", "doc_id": "d1"}}]})
    monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path / "runs")
    before = tmp_path / "before.json"
    before.write_text(json.dumps({"summary": {"needle": {"questions": 1, "recall@5": 0.0, "mrr": 0.0}}}))
    res = CliRunner().invoke(cli, ["retrieval", "eval", "--file", str(_labels_file(tmp_path)),
                                   "--topK", "5", "--compare", str(before), "--compact"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["summary"]["needle"]["recall@5"] == 1.0
    assert out["compare"] == {"needle": {"recall@5": 1.0, "mrr": 1.0}}
    assert list((tmp_path / "runs").glob("*.json"))


def test_generate_appends(tmp_path, monkeypatch):
    from artmind.retrieval_eval import generate
    monkeypatch.setattr(generate, "generate_labels", lambda s, d, c, seed: (
        [rl.RetrievalLabel(id="", question="q?", domains=(d,), kind="needle", relevant=("c9",), source="synthetic")],
        ["c8: empty question"]))
    import artmind.graph_query as gq
    from _neo4j_recorder import Recorder
    monkeypatch.setattr(gq, "read_session", lambda: Recorder())
    path = tmp_path / "l.yaml"
    res = CliRunner().invoke(cli, ["retrieval", "labels", "generate", "--domain", "p", "--count", "1",
                                   "--file", str(path), "--compact"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["added"] == ["r001"] and out["warnings"] == ["c8: empty question"]
