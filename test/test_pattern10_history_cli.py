"""H4b/c (spec 2026-10-22 §5.4.2-3): --includeHistory, the --asOf alias, retired-only hints."""

import json

from click.testing import CliRunner

from artmind import graph_query
from artmind.cli import cli


def _run(args):
    result = CliRunner().invoke(cli, ["query", "graph", "pattern10", "--domain", "f", "--compact", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_include_history_is_passed_through(monkeypatch):
    seen = {}

    def fake_execute(**kw):
        seen.update(kw)
        return {"rows": [{"document": {}, "chunk": {}}]}

    monkeypatch.setattr(graph_query, "execute_pattern", fake_execute)
    _run(["--documentName", "a.md", "--includeHistory"])
    assert seen["includeHistory"] is True and seen["as_of"] is None


def test_as_of_is_an_alias_with_a_warning(monkeypatch):
    seen = {}

    def fake_execute(**kw):
        seen.update(kw)
        return {"rows": [{"document": {}, "chunk": {}}]}

    monkeypatch.setattr(graph_query, "execute_pattern", fake_execute)
    payload = _run(["--documentName", "a.md", "--asOf", "2026-01-01"])
    assert seen["includeHistory"] is True
    assert any("--includeHistory" in w for w in payload["warnings"])


def test_retired_only_match_says_add_include_history(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", lambda domains, name: ["policy_alpha.md"])
    payload = _run(["--documentName", "alpha"])
    assert payload["history_matches"] == ["policy_alpha.md"]
    assert payload["next"] == "add --includeHistory: 1 retired document(s) match"


def test_no_match_anywhere_has_no_hint(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", lambda domains, name: [])
    payload = _run(["--documentName", "nothing"])
    assert "next" not in payload and "history_matches" not in payload


def _must_not_be_called(domains, name):
    raise AssertionError("pattern10_history_matches must not be called")


def test_rows_present_without_flag_asks_no_history_question(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": [{"document": {}, "chunk": {}}]})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", _must_not_be_called)
    payload = _run(["--documentName", "a.md"])
    assert "next" not in payload and "history_matches" not in payload


def test_empty_page_past_the_start_gets_no_hint(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", _must_not_be_called)
    payload = _run(["--documentName", "a.md", "--offset", "300"])
    assert "next" not in payload and "history_matches" not in payload


def test_more_than_five_retired_matches_say_so(monkeypatch):
    names = [f"policy_{i}.md" for i in range(6)]
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", lambda domains, name: names)
    payload = _run(["--documentName", "policy"])
    assert payload["history_matches"] == names[:5]
    assert payload["next"] == "add --includeHistory: more than 5 retired documents match"
