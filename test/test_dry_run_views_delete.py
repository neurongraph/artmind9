"""`views delete --dryRun`: the folder stays; the effect matches the real run."""
import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from test_kg_views_cli_authoring import views_root, write_view  # noqa: F401


def _delete(*args):
    return CliRunner().invoke(cli, ["views", "delete", *args, "--compact"])


def test_dry_run_deletes_nothing_and_matches_the_real_run(views_root):
    write_view(views_root, "d1")

    dry = _delete("d1", "--dryRun")
    assert dry.exit_code == 0, dry.output
    assert (views_root / "d1" / "view.yaml").is_file()
    dry = json.loads(dry.stdout)
    real = json.loads(_delete("d1").stdout)

    assert dry["dry_run"] is True and "dry_run" not in real
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"] == [{"kind": "view_delete", "count": 1, "sample": ["d1"]}]
    assert dry["effect"]["stale"]["views"] == ["d1"]
    assert not (views_root / "d1").exists()


def test_dry_run_of_a_missing_view_fails_like_the_real_run(views_root):
    result = _delete("nope", "--dryRun")

    assert result.exit_code == 1 and "no saved view" in result.output
