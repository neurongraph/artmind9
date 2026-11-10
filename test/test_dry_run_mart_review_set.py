import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.mart import cli as mart_cli


def test_review_set_dry_run_writes_nothing(monkeypatch):
    written = []
    monkeypatch.setattr(mart_cli.records, "put_dimension", lambda *a, **k: written.append(a))
    r = CliRunner().invoke(cli, ["mart", "review", "set", "--domain", "j", "--dimension", "place",
                                 "--member", "x|LOCATION|j", "--level", "country=Somewhere", "--dryRun", "--compact"])
    out = json.loads(r.output)
    assert out["dry_run"] is True and written == []
    assert_effect_shape(out["effect"])
