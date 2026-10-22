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


# ── parser: no crash on malformed values ─────────────────────────────────────


@pytest.mark.parametrize("over", [
    {"route": ["view"]}, {"route": {"a": 1}}, {"route": 3}, {"view": ["x"]}, {"view": 5}, {"view": {"a": 1}},
    {"question": None, "benchmark": ["Q1"]}, {"id": ["L001"]}, {"source": ["written"]}, {"expect": ["high"]},
    {"params": "red"}, {"domains": [["a"]]}, {"expected_documents": "a.md"},
])
def test_malformed_values_are_reported_not_raised(over):
    over = dict(over)
    data = _data()
    if over.get("question", 0) is None:
        del data["labels"][0]["question"]
        over.pop("question")
    data["labels"][0].update(over)
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(data, Path("."), view_params=VIEWS)
    assert caught.value.errors


@pytest.mark.parametrize("data", [
    {"version": True, "labels": []}, {"version": 1.0, "labels": []}, {"version": "1", "labels": []},
    {"version": 1, "labels": "x"}, [], None, "text",
])
def test_the_version_must_be_the_integer_one(data):
    with pytest.raises(labels.LabelError):
        labels.parse_labels(data, Path("."), view_params={})


@pytest.mark.parametrize("bench", [["a"], 5, {"a": 1}, ""])
def test_benchmark_file_must_be_a_string(bench):
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels({"version": 1, "benchmark_file": bench, "labels": []}, Path("."), view_params={})
    assert any("benchmark_file must be" in e for e in caught.value.errors)


def test_an_undecodable_benchmark_file_is_reported_with_its_base(tmp_path):
    (tmp_path / "q.md").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels({"version": 1, "benchmark_file": "q.md", "labels": []}, tmp_path, view_params={})
    assert any(e.startswith("benchmark_file:") and str(tmp_path.resolve()) in e for e in caught.value.errors)


def test_a_missing_benchmark_file_names_the_base(tmp_path):
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels({"version": 1, "benchmark_file": "nope.md", "labels": []}, tmp_path, view_params={})
    assert any(str(tmp_path.resolve()) in e for e in caught.value.errors)


def test_a_view_needs_route_view():
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(_data(route="search"), Path("."), view_params=VIEWS)
    assert any("must have route view" in e for e in caught.value.errors)


@pytest.mark.parametrize("source, ok", [("trace:" + "a" * 8, True), ("trace:" + "a" * 32, True),
                                        ("trace:" + "a" * 33, False), ("trace:abc", False), ("trace:ABCDEF12", False)])
def test_trace_source_length_is_bounded(source, ok):
    if ok:
        labels.parse_labels(_data(source=source), Path("."), view_params=VIEWS)
    else:
        with pytest.raises(labels.LabelError):
            labels.parse_labels(_data(source=source), Path("."), view_params=VIEWS)


def test_a_yaml_syntax_error_shows_no_source_text(tmp_path):
    path = tmp_path / "plan_labels.yaml"
    path.write_text("version: 1\nlabels:\n  - question: Secret question text?\n    id: [unclosed\n")
    with pytest.raises(labels.LabelError) as caught:
        labels.load_labels(path)
    (message,) = caught.value.errors
    assert "line" in message and "column" in message and "Secret" not in message


def test_benchmark_file_is_read_from_the_current_vault_when_set(tmp_path, monkeypatch):
    import paths

    vault = tmp_path / "vault"
    (vault / "benchmarking").mkdir(parents=True)
    (vault / "q.md").write_text("## Q01 — A\n\n**Question:** Which rate applied?\n")
    elsewhere = tmp_path / "elsewhere" / "deep"
    elsewhere.mkdir(parents=True)
    path = elsewhere / "plan_labels.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "benchmark_file": "q.md", "labels": [
        {"id": "L001", "benchmark": "Q01", "source": "benchmark", "domains": ["bank"], "route": "search"}]}))
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", vault)
    (label,) = labels.load_labels(path)
    assert label.question == "Which rate applied?"


# ── check_entities resolves like the planner ─────────────────────────────────


def _entity_view(monkeypatch, entity_class="Person"):
    from artmind.kg_views import store
    from artmind.kg_views.model import ParamSpec

    class _Spec:
        params = {"person": ParamSpec(name="person", type="entity", entity_class=entity_class)}

    loads = []

    def load_view(name):
        loads.append(name)
        return type("V", (), {"spec": _Spec})()

    monkeypatch.setattr(store, "load_view", load_view)
    return loads


def _entity_label(value="Ada", **kw):
    return labels.Label("L001", "q?", "written", ("journal",), "view", "people", {"person": value}, **kw)


def test_check_entities_sends_a_capitalised_mention_to_resolve_mention(monkeypatch):
    from artmind.query_plan import candidates

    _entity_view(monkeypatch)
    calls = []
    monkeypatch.setattr(candidates, "resolve_mention",
                        lambda m, d, n: calls.append((m, d, n)) or [{"name": "Ada", "entity_class": "person"}])
    assert labels.check_entities([_entity_label()]) == []
    ((mention, domains, narrator),) = calls
    assert mention == candidates.Mention(text="Ada", start=0, end=3, via="capitalised")
    assert domains == ["journal"] and narrator is None


def test_check_entities_reports_a_class_mismatch(monkeypatch):
    from artmind.query_plan import candidates

    _entity_view(monkeypatch, "Person")
    monkeypatch.setattr(candidates, "resolve_mention", lambda m, d, n: [{"name": "Ada", "entity_class": "Place"}])
    (error,) = labels.check_entities([_entity_label()])
    assert "L001" in error and "does not resolve" in error and "Person" in error


def test_check_entities_reports_no_candidates(monkeypatch):
    from artmind.query_plan import candidates

    _entity_view(monkeypatch)
    monkeypatch.setattr(candidates, "resolve_mention", lambda m, d, n: [])
    assert len(labels.check_entities([_entity_label()])) == 1


def test_check_entities_caches_views_and_resolutions(monkeypatch):
    from artmind.query_plan import candidates

    loads = _entity_view(monkeypatch)
    calls = []
    monkeypatch.setattr(candidates, "resolve_mention",
                        lambda m, d, n: calls.append(m.text) or [{"entity_class": "Person"}])
    two = [_entity_label(["Ada", "Ada", "Bo"]), _entity_label("Ada")]
    assert labels.check_entities(two) == []
    assert loads == ["people"] and calls == ["Ada", "Bo"]


def test_the_cli_wraps_neo4j_errors_when_checking_entities(tmp_path, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    path = tmp_path / "plan_labels.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "labels": [
        {"id": "L001", "question": "What did the team decide?", "source": "written", "domains": ["journal"],
         "route": "search"}]}))

    def boom(_labels):
        raise ServiceUnavailable("down")

    monkeypatch.setattr(labels, "check_entities", boom)
    result = CliRunner().invoke(cli, ["plan", "labels", "validate", "--file", str(path), "--checkEntities"])
    assert result.exit_code == 1 and "entities not checked: down" in result.output


# ── plan config check ────────────────────────────────────────────────────────

_NARRATOR = "narrator:\n  name: Ada\n  entity_class: Person\n"


def _check(tmp_path, monkeypatch, files, *, found=None, error=None, args=()):
    import paths
    from artmind.query_plan import candidates

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    for name, text in files.items():
        (tmp_path / name).write_text(text)

    def narrator_candidates(narrator, domains):
        if error:
            raise error
        return [{"name": narrator.name}] if found else []

    monkeypatch.setattr(candidates, "narrator_candidates", narrator_candidates)
    result = CliRunner().invoke(cli, ["plan", "config", "check", "--compact", *args])
    return result, {r["domain"]: r for r in json.loads(result.output)["results"]}


def test_a_found_narrator_passes(tmp_path, monkeypatch):
    result, by = _check(tmp_path, monkeypatch, {"journal.yaml": _NARRATOR}, found=True)
    assert result.exit_code == 0 and by["journal"]["narrator_found"] is True and by["journal"]["warnings"] == []


def test_a_missing_narrator_is_an_error(tmp_path, monkeypatch):
    result, by = _check(tmp_path, monkeypatch, {"journal.yaml": _NARRATOR}, found=False)
    assert result.exit_code == 1
    assert by["journal"]["ok"] is False and by["journal"]["narrator_found"] is False
    assert any("is not in journal" in e for e in by["journal"]["errors"])


def test_neo4j_down_is_a_warning_not_an_error(tmp_path, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    result, by = _check(tmp_path, monkeypatch, {"journal.yaml": _NARRATOR}, error=ServiceUnavailable("down"))
    assert result.exit_code == 0
    assert by["journal"]["ok"] is True and by["journal"]["errors"] == []
    assert any("narrator not checked" in w for w in by["journal"]["warnings"])


def test_an_invalid_file_name_is_reported(tmp_path, monkeypatch):
    result, by = _check(tmp_path, monkeypatch, {"bad name.yaml": "{}\n", "journal.yaml": "{}\n"})
    assert result.exit_code == 1
    assert by["bad name"]["ok"] is False
    assert by["bad name"]["errors"] == ["file name is not a valid domain name"]
    assert by["journal"]["ok"] is True


def test_hidden_files_and_directories_are_ignored(tmp_path, monkeypatch):
    (tmp_path / "folder.yaml").mkdir()
    result, by = _check(tmp_path, monkeypatch, {".hidden.yaml": "colour: blue\n", "journal.yaml": "{}\n"})
    assert result.exit_code == 0 and set(by) == {"journal"}
