import json
from unittest.mock import patch

from click.testing import CliRunner

from artmind.cli import cli
from artmind.traces import evidence, path


def test_query_survey_passes_options_through():
    payload = {"query_type": "survey", "documents": [{"doc_id": "A"}], "topics": [], "warnings": []}
    with patch("artmind.survey.survey_search", return_value=payload) as search:
        result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--topDocs", "5",
                                          "--passages", "2", "--compact", "what do the notes say"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
    args, kwargs = search.call_args
    assert args == (["notes"], "what do the notes say")
    assert kwargs["top_docs"] == 5 and kwargs["passages"] == 2


def test_query_survey_limits_passages_to_three():
    result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--passages", "4", "q"])
    assert result.exit_code != 0


def test_a_value_error_is_a_click_error():
    with patch("artmind.survey.survey_search", side_effect=ValueError("the summary index is missing")):
        result = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "q"])
    assert result.exit_code == 1 and "summary index is missing" in result.output


def test_survey_spans_have_their_own_layer_and_count_documents():
    assert path.layer_of("query survey") == "survey"
    assert evidence.summarize({"documents": [{"doc_id": "A"}, {"doc_id": "B"}]})["rows"] == 2
