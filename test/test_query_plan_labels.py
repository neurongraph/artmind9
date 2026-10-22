"""The labelled set (spec 2026-10-22 §4.6). Synthetic labels only."""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_plan import labels

VIEWS = {"visits_by_year": {"person", "place", "year"}}


def _data(**over):
    label = {"id": "L001", "question": "How many visits did Ada make to Lakeport in 2025?", "source": "written",
             "domains": ["journal"], "route": "view", "view": "visits_by_year",
             "params": {"person": "Ada", "place": ["Lakeport", "Lakeport"], "year": 2025}}
    return {"version": 1, "labels": [{**label, **over}]}


def test_a_valid_label_parses():
    (label,) = labels.parse_labels(_data(), Path("."), view_params=VIEWS)
    assert (label.id, label.route, label.view, label.expect) == ("L001", "view", "visits_by_year", "high")
    assert label.domains == ("journal",)


@pytest.mark.parametrize("over, message", [
    ({"id": "7"}, "id must be L"),
    ({"route": "nowhere"}, "unknown route"),
    ({"view": "missing_view"}, "no saved view"),
    ({"params": {"colour": "red"}}, "has no param"),
    ({"source": "chat"}, "source must be"),
    ({"expect": "maybe"}, "expect must be"),
    ({"domains": []}, "domains must be"),
    ({"benchmark": "Q07"}, "exactly one of question / benchmark"),
    ({"route": "view", "view": None, "params": {}}, "route view needs a view"),
])
def test_each_rule_is_reported(over, message):
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(_data(**over), Path("."), view_params=VIEWS)
    assert any(message in e for e in caught.value.errors), caught.value.errors


def test_all_errors_are_collected_not_just_the_first():
    data = _data(route="nowhere", source="chat")
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(data, Path("."), view_params=VIEWS)
    assert len(caught.value.errors) == 2


def test_a_benchmark_reference_takes_its_question_text(tmp_path):
    (tmp_path / "benchmarking").mkdir()
    (tmp_path / "benchmarking" / "questions.md").write_text(
        "## Q07 — Rates\n\n**Question:** Which rate applied in January?\n\n**Evaluation comment:** x\n"
    )
    data = {"version": 1, "benchmark_file": "benchmarking/questions.md", "labels": [
        {"id": "L002", "benchmark": "Q07", "source": "benchmark", "domains": ["bank"], "route": "time_history",
         "view": None},
    ]}
    (label,) = labels.parse_labels(data, tmp_path, view_params={})
    assert label.question == "Which rate applied in January?" and label.benchmark == "Q07"


def test_the_cli_validates_a_file(tmp_path):
    path = tmp_path / "plan_labels.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "labels": [
        {"id": "L001", "question": "What did the team decide?", "source": "written", "domains": ["journal"],
         "route": "search", "view": None},
    ]}))
    result = CliRunner().invoke(cli, ["plan", "labels", "validate", "--file", str(path), "--compact"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"command": "plan labels validate", "file": str(path), "ok": True,
                                         "labels": 1, "errors": []}


def test_the_cli_fails_on_an_invalid_file(tmp_path):
    path = tmp_path / "plan_labels.yaml"
    path.write_text("version: 1\nlabels: [{id: bad}]\n")
    result = CliRunner().invoke(cli, ["plan", "labels", "validate", "--file", str(path), "--compact"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_plan_config_check_reports_each_file(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    (tmp_path / "journal.yaml").write_text("confidence_threshold: 0.8\n")
    (tmp_path / "bank.yaml").write_text("colour: blue\n")
    result = CliRunner().invoke(cli, ["plan", "config", "check", "--compact"])
    assert result.exit_code == 1
    by_domain = {r["domain"]: r for r in json.loads(result.output)["results"]}
    assert by_domain["journal"]["ok"] is True and by_domain["bank"]["ok"] is False
