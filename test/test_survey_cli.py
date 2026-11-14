import json
from unittest.mock import patch

from click.testing import CliRunner

from _envelope import plain
from artmind.cli import cli
from artmind.traces import evidence, path


def test_query_survey_passes_options_through():
    payload = {"query_type": "survey", "documents": [{"doc_id": "A"}], "topics": [], "warnings": []}
    with patch("artmind.survey.survey_search", return_value=payload) as search:
        result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--topDocs", "5",
                                          "--passages", "2", "--compact", "what do the notes say"])
    assert result.exit_code == 0, result.output
    assert plain(json.loads(result.output)) == plain(payload)
    args, kwargs = search.call_args
    assert args == (["notes"], "what do the notes say")
    assert kwargs["top_docs"] == 5 and kwargs["passages"] == 2


def test_query_survey_limits_passages_to_eight():
    result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--passages", "9", "q"])
    assert result.exit_code != 0


def test_a_value_error_is_a_click_error():
    with patch("artmind.survey.survey_search", side_effect=ValueError("the summary index is missing")):
        result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "q"])
    assert result.exit_code == 1 and "summary index is missing" in result.output


def test_survey_spans_have_their_own_layer_and_count_documents():
    assert path.layer_of("query survey") == "survey"
    assert evidence.summarize({"documents": [{"doc_id": "A"}, {"doc_id": "B"}]})["rows"] == 2


def test_passages_accepts_up_to_eight():
    from artmind.cli import cli

    opt = next(p for p in cli.commands["query"].commands["survey"].params if p.name == "passages")
    assert opt.type.max == 8


def _brief_payload():
    return {"query_type": "survey", "question": "q", "domains": ["notes"], "warnings": [],
            "documents": [{"doc_id": "A"}, {"doc_id": "B"}]}


def test_brief_survey_stores_its_payload_under_a_handle(monkeypatch, tmp_path):
    """Spec 2026-11-12 find §2: every brief survey carries a handle `query find` can read."""
    from artmind import cli as cli_mod, result_store

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path)
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    with patch("artmind.survey.survey_search", return_value=_brief_payload()):
        result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--shape", "brief", "--compact", "q"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    stored = result_store.load(tmp_path / "results", out["handle"])
    assert stored["command"] == "query survey" and [d["doc_id"] for d in stored["documents"]] == ["A", "B"]


def test_full_and_expand_surveys_carry_no_handle(monkeypatch, tmp_path):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path)
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    for extra in (["--shape", "full"], ["--shape", "brief", "--expand", "A"]):
        with patch("artmind.survey.survey_search", return_value=_brief_payload()):
            result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", *extra, "--compact", "q"])
        assert result.exit_code == 0, result.output
        assert "handle" not in json.loads(result.output)
