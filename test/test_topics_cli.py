import json
from unittest.mock import patch

from click.testing import CliRunner

from artmind.cli import cli
from artmind.topics import store

META = {"artmind_topic": 1, "topic": "Notice", "slug": "notice", "domains": ["j"], "anchor": {"kind": "topic"},
        "built": "2026-11-02", "sources": [{"doc_id": "d1", "name": "a.md", "content_sha": "v1", "passages": ["p"]}],
        "checks": {}}


def test_topic_build_passes_options(tmp_path):
    with patch("artmind.topics.build.build_page", return_value={"slug": "notice", "path": "x"}) as b:
        r = CliRunner().invoke(cli, ["topic", "build", "Notice periods", "--domain", "j", "--maxDocs", "5",
                                     "--out", str(tmp_path), "--dry-run", "--compact"])
    assert r.exit_code == 0, r.output
    args, kwargs = b.call_args
    assert args == ("Notice periods", ["j"])
    assert kwargs["max_docs"] == 5 and kwargs["out"] == tmp_path and kwargs["dry_run"] is True


def test_query_topics_list_and_show(tmp_path, monkeypatch):
    import paths

    store.write_page(tmp_path, "notice", META, "## Overview\n\nBody [^1].")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path)
    with patch("artmind.topics.stale.staleness", return_value={"notice": ["source changed: a.md"]}), \
         patch("artmind.cli.read_session_for_topics", return_value=_null()):
        r = CliRunner().invoke(cli, ["query", "topics", "list", "--compact"])
        assert r.exit_code == 0, r.output
        pages = json.loads(r.output)["pages"]
        assert pages[0]["slug"] == "notice" and pages[0]["stale"] is True
        r = CliRunner().invoke(cli, ["query", "topics", "show", "notice", "--compact"])
        page = json.loads(r.output)["page"]
        assert page["body"].startswith("## Overview") and page["stale_reasons"] == ["source changed: a.md"]
        assert page["sources"] == META["sources"]
        r = CliRunner().invoke(cli, ["query", "topics", "show", "missing"])
        assert r.exit_code == 1 and "no topic page" in r.output


def test_query_topics_list_filters_by_domain_and_staleness(tmp_path, monkeypatch):
    import paths

    store.write_page(tmp_path, "notice", META, "x")
    store.write_page(tmp_path, "other", {**META, "slug": "other", "domains": ["k"]}, "x")
    monkeypatch.setattr(paths, "TOPICS_DIR", tmp_path)
    with patch("artmind.topics.stale.staleness", return_value={"notice": [], "other": []}), \
         patch("artmind.cli.read_session_for_topics", return_value=_null()):
        r = CliRunner().invoke(cli, ["query", "topics", "list", "--domain", "j", "--compact"])
        assert [p["slug"] for p in json.loads(r.output)["pages"]] == ["notice"]
        r = CliRunner().invoke(cli, ["query", "topics", "list", "--stale", "--compact"])
        assert json.loads(r.output)["pages"] == []


def test_topic_delete(tmp_path):
    store.write_page(tmp_path, "notice", META, "x")
    r = CliRunner().invoke(cli, ["topic", "delete", "notice", "--out", str(tmp_path), "--compact"])
    assert r.exit_code == 0 and not (tmp_path / "notice.md").exists()
    assert json.loads(r.stdout)["deleted"] == str(tmp_path / "notice.md")
    assert "deleted" in r.stderr


def test_topic_refresh_stale_rebuilds_only_stale_pages(tmp_path):
    store.write_page(tmp_path, "notice", META, "x")
    store.write_page(tmp_path, "fresh", {**META, "slug": "fresh", "topic": "Fresh"}, "x")
    with patch("artmind.topics.stale.staleness", return_value={"notice": ["source changed: a.md"], "fresh": []}), \
         patch("artmind.cli.read_session_for_topics", return_value=_null()), \
         patch("artmind.topics.build.build_page", return_value={"slug": "notice"}) as b:
        r = CliRunner().invoke(cli, ["topic", "refresh", "--stale", "--out", str(tmp_path), "--compact"])
    assert r.exit_code == 0, r.output
    args, kwargs = b.call_args
    assert args == ("Notice", ["j"]) and kwargs["slug"] == "notice" and b.call_count == 1


def test_survey_cli_asks_for_topics():
    with patch("artmind.survey.survey_search", return_value={"documents": []}) as s:
        CliRunner().invoke(cli, ["query", "survey", "--domain", "j", "q"])
    from artmind.topics import stale
    assert s.call_args.kwargs["topics"] is stale.survey_topics


def test_a_shown_page_counts_as_one_row():
    from artmind.traces import evidence
    assert evidence.summarize({"page": {"slug": "x"}})["rows"] == 1


class _null:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False
