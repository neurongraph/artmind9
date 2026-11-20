import json

from click.testing import CliRunner

from artmind.cli import cli
from artmind.mart import cli as mart_cli


def test_review_list(monkeypatch):
    monkeypatch.setattr(mart_cli.records, "proposed", lambda d: [{"id": "r1", "dimension": "place",
                                                                 "member_key": "x", "levels": {"city": "A"},
                                                                 "confidence": 0.5}])
    r = CliRunner().invoke(cli, ["mart", "review", "list", "--domain", "j", "--compact"])
    out = json.loads(r.output)
    assert out["rows"][0]["id"] == "r1" and out["rows_total"] == 1 and out["truncated"] is False


def test_review_list_pages_and_filters(monkeypatch):
    rows = [{"id": f"r{i}", "dimension": "place" if i % 2 else "person", "member_key": f"m{i}",
             "levels": {}, "confidence": i / 10} for i in range(10)]
    monkeypatch.setattr(mart_cli.records, "proposed", lambda d: rows)
    r = CliRunner().invoke(cli, ["mart", "review", "list", "--domain", "j", "--dimension", "place",
                                 "--limit", "2", "--offset", "1", "--compact"])
    out = json.loads(r.output)
    assert [x["id"] for x in out["rows"]] == ["r3", "r5"]
    assert out["rows_total"] == 5 and out["truncated"] is True and out["offset"] == 1
    assert out["by_dimension"] == {"person": 5, "place": 5}
