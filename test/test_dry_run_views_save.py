"""`views save --dryRun`: every check runs, the views folder is untouched, the effect matches the real run."""
import json

import pytest
from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from test_kg_views_cli_authoring import views_root, write_view  # noqa: F401


def _save(draft, *args):
    return CliRunner().invoke(cli, ["views", "save", "--path", str(draft), *args, "--compact"])


def test_dry_run_writes_nothing_and_matches_the_real_run(views_root, tmp_path):
    draft = write_view(tmp_path / "drafts", "d1")

    dry = _save(draft, "--dryRun")
    assert dry.exit_code == 0, dry.output
    assert not views_root.exists() or list(views_root.iterdir()) == []
    dry = json.loads(dry.stdout)
    real = json.loads(_save(draft).stdout)

    assert dry["dry_run"] is True and "dry_run" not in real
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"] == [{"kind": "view_save", "count": 1, "sample": ["d1"]}]
    assert dry["effect"]["reversible"] is True and dry["effect"]["undo"] == "artmind views delete d1"
    assert (views_root / "d1" / "view.yaml").is_file()


def test_dry_run_replacing_a_view_marks_it_stale(views_root, tmp_path):
    _save(write_view(tmp_path / "a", "d1", version=1))
    before = (views_root / "d1" / "view.yaml").read_text()
    draft = write_view(tmp_path / "b", "d1", version=2)

    payload = json.loads(_save(draft, "--dryRun").stdout)

    assert payload["replaced"] is True and payload["effect"]["stale"]["views"] == ["d1"]
    assert payload["effect"]["reversible"] is False
    assert (views_root / "d1" / "view.yaml").read_text() == before


def test_dry_run_still_applies_the_version_guard(views_root, tmp_path):
    _save(write_view(tmp_path / "a", "d1", version=2))
    draft = write_view(tmp_path / "b", "d1", version=1)

    result = _save(draft, "--dryRun")

    assert result.exit_code == 1 and "downgrade" in result.output
