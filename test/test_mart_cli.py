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
