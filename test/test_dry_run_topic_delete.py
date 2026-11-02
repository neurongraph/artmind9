"""`topic delete --dryRun`: the page file stays; the effect matches the real run."""
import json

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.topics import store

META = {"artmind_topic": 1, "topic": "Notice", "slug": "notice", "domains": ["j"], "anchor": {"kind": "topic"},
        "built": "2026-11-02", "sources": [], "checks": {}}


def _delete(tmp_path, *args):
    return CliRunner().invoke(cli, ["topic", "delete", "notice", "--out", str(tmp_path), *args, "--compact"])


def test_dry_run_keeps_the_page_and_matches_the_real_run(tmp_path):
    store.write_page(tmp_path, "notice", META, "Body")
    before = (tmp_path / "notice.md").read_bytes()

    dry = _delete(tmp_path, "--dryRun")
    assert dry.exit_code == 0, dry.output
    assert (tmp_path / "notice.md").read_bytes() == before
    assert "deleted" not in dry.stderr
    dry = json.loads(dry.stdout)
    real = json.loads(_delete(tmp_path).stdout)

    assert dry["dry_run"] is True and "dry_run" not in real
    assert dry["effect"] == real["effect"]
    assert_effect_shape(dry["effect"])
    assert dry["effect"]["changes"] == [{"kind": "topic_delete", "count": 1, "sample": ["notice"]}]
    assert dry["effect"]["records"] == [str(tmp_path / "notice.md")]
    assert not (tmp_path / "notice.md").exists()


def test_dry_run_names_notes_that_would_go(tmp_path):
    store.write_page(tmp_path, "notice", META, "Body")
    path = tmp_path / "notice.md"
    path.write_text(path.read_text().rstrip() + "\n\nMy own words.\n")

    payload = json.loads(_delete(tmp_path, "--dryRun").stdout)

    assert "My notes" in payload["effect"]["summary"]


def test_dry_run_of_a_missing_page_fails_like_the_real_run(tmp_path):
    result = _delete(tmp_path, "--dryRun")

    assert result.exit_code == 1 and "no topic page" in result.output
