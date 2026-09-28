"""`artmind vault doctor` (spec 2026-09-26 §5 R8): read-only readiness checks
against real throwaway git repos."""
import json
import subprocess
from pathlib import Path

import pytest

from artmind import vault
from artmind import vault_doctor as doc


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTMIND_VAULT_GIT_PUSH", raising=False)
    root = tmp_path / "v"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "pull.rebase", "false")
    (root / ".artmind").mkdir()
    vault.write_gitignore(root)
    vault.write_gitattributes(root)
    return root


def _by_name(result: dict) -> dict:
    return {c["name"]: c for c in result["checks"]}


def _head_and_index(repo):
    head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    return head, _git(repo, "ls-files", "--stage")


def test_a_ready_vault_has_no_failures_and_nothing_is_written(repo):
    (repo / "note.md").write_text("hi")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    before = _head_and_index(repo), (repo / ".gitignore").read_text(), sorted(p.name for p in repo.iterdir())

    result = doc.run(repo)

    after = _head_and_index(repo), (repo / ".gitignore").read_text(), sorted(p.name for p in repo.iterdir())
    assert before == after
    assert result["ok"] is True, result
    checks = _by_name(result)
    assert checks[".gitignore artmind block"]["status"] == "ok"
    assert checks[".gitattributes artmind block"]["status"] == "ok"
    assert checks["ignored paths still tracked"]["status"] == "ok"
    assert checks["pull.rebase"]["status"] == "ok"


def test_an_outdated_gitignore_block_fails_and_points_at_init(repo):
    (repo / ".gitignore").write_text("# ── artmind ───\n.artmind/config.env\n# ── end artmind ───\n")

    result = doc.run(repo)

    check = _by_name(result)[".gitignore artmind block"]
    assert result["ok"] is False
    assert check["status"] == "fail"
    assert "outdated" in check["detail"]
    assert "artmind init" in check["fix"]


def test_a_missing_gitattributes_fails(repo):
    (repo / ".gitattributes").unlink()

    check = _by_name(doc.run(repo))[".gitattributes artmind block"]

    assert check["status"] == "fail"
    assert "missing" in check["detail"]


def test_tracked_table_folders_are_reported_with_the_exact_untrack_command(repo):
    table = repo / ".artmind" / "data" / "kg" / "banking" / "table__accounts"
    table.mkdir(parents=True)
    (table / "document.json").write_text("{}")
    (table / "observations.json").write_text("[]")
    _git(repo, "add", "-f", "-A")  # tracked before the rule existed
    _git(repo, "commit", "-qm", "old commit")

    check = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert check["status"] == "fail"
    assert check["fix"] == "git rm -r --cached -- .artmind/data/kg/banking/table__accounts"
    # doctor printed it, never ran it
    assert ".artmind/data/kg/banking/table__accounts/document.json" in _git(repo, "ls-files")


def test_a_duplicate_artmind_block_fails_with_a_hand_edit_fix(repo):
    text = (repo / ".gitignore").read_text()
    (repo / ".gitignore").write_text(text + "\n" + vault.GITIGNORE_BLOCK)

    check = _by_name(doc.run(repo))[".gitignore artmind block"]

    assert check["status"] == "fail"
    assert "more than one" in check["detail"]
    assert "exactly one" in check["fix"]


def test_pull_rebase_true_fails(repo):
    _git(repo, "config", "pull.rebase", "true")

    check = _by_name(doc.run(repo))["pull.rebase"]

    assert check["status"] == "fail"
    assert check["fix"] == "git config pull.rebase false"


def test_pull_rebase_unset_is_ok(repo):
    _git(repo, "config", "--unset", "pull.rebase")

    assert _by_name(doc.run(repo))["pull.rebase"]["status"] == "ok"


def test_obsidian_git_absent_is_a_warning_not_a_failure(repo):
    result = doc.run(repo)

    check = _by_name(result)["Obsidian Git"]
    assert check["status"] == "warn"
    assert result["ok"] is True


def test_obsidian_git_unknown_keys_are_reported_unknown_not_failed(repo):
    data = repo / ".obsidian" / "plugins" / "obsidian-git" / "data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"someFutureKey": 1}))

    result = doc.run(repo)

    obsidian = [c for c in result["checks"] if c["name"].startswith("Obsidian Git:")]
    assert obsidian and all(c["status"] == "unknown" for c in obsidian)
    assert result["ok"] is True


def test_obsidian_git_known_keys_are_checked(repo):
    data = repo / ".obsidian" / "plugins" / "obsidian-git" / "data.json"
    data.parent.mkdir(parents=True)
    data.write_text(json.dumps({"syncMethod": "rebase", "autoPullOnBoot": True, "autoSaveInterval": 5}))

    checks = _by_name(doc.run(repo))

    assert checks["Obsidian Git: sync method"]["status"] == "warn"
    assert "rebase" in checks["Obsidian Git: sync method"]["detail"]
    assert checks["Obsidian Git: pull on startup"]["status"] == "ok"
    assert checks["Obsidian Git: auto commit interval (minutes)"]["status"] == "ok"


def test_deprecated_push_setting_in_config_env_is_warned(repo):
    (repo / ".artmind" / "config.env").write_text("ARTMIND_VAULT_GIT_PUSH=1\n")

    check = _by_name(doc.run(repo))["ARTMIND_VAULT_GIT_PUSH"]

    assert check["status"] == "warn"
    assert "config.env" in check["detail"]


def test_a_dropbox_ancestor_is_warned(repo):
    (repo.parent / ".dropbox").write_text("")

    check = _by_name(doc.run(repo))["other sync tools"]

    assert check["status"] == "warn"
    assert "Dropbox" in check["detail"]


@pytest.mark.parametrize("core_plugins", [["sync", "graph"], {"sync": True, "graph": True}])
def test_obsidian_sync_is_detected_in_either_core_plugins_shape(repo, core_plugins):
    (repo / ".obsidian").mkdir()
    (repo / ".obsidian" / "core-plugins.json").write_text(json.dumps(core_plugins))

    check = _by_name(doc.run(repo))["other sync tools"]

    assert check["status"] == "warn"
    assert "Obsidian Sync" in check["detail"]


def test_a_tracked_table_folder_with_a_non_ascii_name_gets_an_exact_untrack_command(repo):
    """Spec 2026-09-26 §14 A5: without `ls-files -z`, git C-quotes the name and
    the printed command names a path that does not exist."""
    table = repo / ".artmind" / "data" / "kg" / "banking" / "table__Café"
    table.mkdir(parents=True)
    (table / "document.json").write_text("{}")
    _git(repo, "add", "-f", "-A")
    _git(repo, "commit", "-qm", "old commit")

    check = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert check["fix"] == "git rm -r --cached -- '.artmind/data/kg/banking/table__Café'"
