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
    assert "(v1; this artmind writes v5)" in check["detail"]
    assert "artmind init" in check["fix"]


def test_a_v3_block_is_flagged_by_version_and_its_tracked_scratch_file_listed_after_init(repo):
    """A vault initialised before block v4 ignores scratch folders only: a
    curation record's temp file a crash left behind was committable. Doctor
    names both versions; after `artmind init` (write_gitignore) the file is
    ignored-but-tracked and doctor prints the exact untrack command."""
    from _historical_blocks import V3_GITIGNORE_BLOCK

    (repo / ".gitignore").write_text(V3_GITIGNORE_BLOCK)
    scratch = repo / ".artmind" / "data" / "curation" / "conflicts" / "abc.json.artmind-tmp"
    scratch.parent.mkdir(parents=True)
    scratch.write_text("{}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a crash left a record's scratch file, and it was committed")

    result = doc.run(repo)

    block = _by_name(result)[".gitignore artmind block"]
    assert block["status"] == "fail"
    assert block["detail"] == "artmind block outdated (v3; this artmind writes v5)"
    assert "artmind init" in block["fix"]
    assert _by_name(result)["ignored paths still tracked"]["status"] == "ok", "v3 does not ignore a file"

    assert vault.write_gitignore(repo) is True
    tracked = _by_name(doc.run(repo))["ignored paths still tracked"]

    assert tracked["status"] == "fail"
    assert tracked["fix"] == "git rm -r --cached -- .artmind/data/curation/conflicts/abc.json.artmind-tmp"


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


NAME = "review dossiers privacy"


def _track_dossier(repo):
    reviews = repo / ".artmind" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "rv-2026-01-02-abcdef.md").write_text("---\nreview_id: rv-2026-01-02-abcdef\n---\n")
    _git(repo, "add", ".artmind/reviews")


def test_review_privacy_no_dossiers(repo):
    assert _by_name(doc.run(repo))[NAME]["status"] == "ok"


def test_review_privacy_no_origin(repo):
    _track_dossier(repo)
    check = _by_name(doc.run(repo))[NAME]
    assert check["status"] == "ok" and "no origin" in check["detail"]


def test_review_privacy_not_github(repo):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://gitlab.example.com/me/vault.git")
    check = _by_name(doc.run(repo))[NAME]
    assert check["status"] == "ok" and "not on GitHub" in check["detail"]


def test_review_privacy_gh_missing(repo, monkeypatch):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "git@github.com:me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: None)
    assert _by_name(doc.run(repo))[NAME]["status"] == "unknown"


@pytest.mark.parametrize("visibility,status", [("PUBLIC", "warn"), ("PRIVATE", "ok"), ("INTERNAL", "ok")])
def test_review_privacy_visibility(repo, monkeypatch, visibility, status):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://github.com/me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    seen = {}

    def fake(slug, account=None):
        seen["slug"] = slug
        return 0, visibility + "\n", ""

    monkeypatch.setattr(doc, "_gh_visibility", fake)
    check = _by_name(doc.run(repo))[NAME]
    assert seen["slug"] == "me/vault"
    assert check["status"] == status
    if status == "warn":
        assert check["fix"]


def test_review_privacy_gh_error_is_unknown(repo, monkeypatch):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://github.com/me/vault")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(doc, "_gh_visibility", lambda slug, account=None: (1, "", "not logged in"))
    assert _by_name(doc.run(repo))[NAME]["status"] == "unknown"


def test_review_privacy_token_in_https_url_warns_without_leaking_token(repo, monkeypatch):
    """https://<token>@github.com/owner/repo.git must match as a GitHub URL and
    the token must never appear in the check's detail or fix strings."""
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://x-token-123@github.com/me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    seen = {}

    def fake(slug, account=None):
        seen["slug"] = slug
        return 0, "PUBLIC\n", ""

    monkeypatch.setattr(doc, "_gh_visibility", fake)
    check = _by_name(doc.run(repo))[NAME]
    assert check["status"] == "warn"
    assert seen.get("slug") == "me/vault"
    assert "x-token-123" not in (check.get("detail") or "")
    assert "x-token-123" not in (check.get("fix") or "")


def _run_recorder(monkeypatch, token_rc=0, token="tok-for-login\n"):
    """Patch doc.run_command; return the list of (argv, extra_env) it saw."""
    calls = []

    def fake(cmd, timeout=None, cwd=None, extra_env=None, expected_codes=()):
        calls.append((list(cmd), extra_env))
        if cmd[:3] == ["gh", "auth", "token"]:
            return token_rc, token if token_rc == 0 else "", ""
        return 0, "PRIVATE\n", ""

    monkeypatch.setattr(doc, "run_command", fake)
    return calls


def test_gh_visibility_uses_account_named_in_remote(monkeypatch):
    calls = _run_recorder(monkeypatch)
    assert doc._gh_visibility("me/vault", "me") == (0, "PRIVATE\n", "")
    assert calls[0][0] == ["gh", "auth", "token", "--user", "me"]
    view_argv, view_env = calls[1]
    assert view_argv[:3] == ["gh", "repo", "view"]
    assert view_env == {"GH_TOKEN": "tok-for-login"}


def test_gh_visibility_without_account_uses_active_gh_account(monkeypatch):
    calls = _run_recorder(monkeypatch)
    doc._gh_visibility("me/vault")
    assert [c[0][:3] for c in calls] == [["gh", "repo", "view"]]
    assert calls[0][1] is None


def test_gh_visibility_falls_back_when_gh_has_no_login_for_account(monkeypatch):
    calls = _run_recorder(monkeypatch, token_rc=1)
    doc._gh_visibility("me/vault", "me")
    assert calls[-1][0][:3] == ["gh", "repo", "view"]
    assert calls[-1][1] is None


def test_gh_visibility_never_passes_a_token_shaped_userinfo_to_gh(monkeypatch):
    calls = _run_recorder(monkeypatch)
    doc._gh_visibility("me/vault", "ghp_abc123SECRET")
    assert all("ghp_abc123SECRET" not in " ".join(argv) for argv, _ in calls)
    assert [c[0][:3] for c in calls] == [["gh", "repo", "view"]]


def test_review_privacy_passes_remote_login_to_gh(repo, monkeypatch):
    _track_dossier(repo)
    _git(repo, "remote", "add", "origin", "https://me@github.com/me/vault.git")
    monkeypatch.setattr(doc.shutil, "which", lambda name: "/usr/bin/gh")
    seen = {}

    def fake(slug, account=None):
        seen.update(slug=slug, account=account)
        return 0, "PRIVATE\n", ""

    monkeypatch.setattr(doc, "_gh_visibility", fake)
    assert _by_name(doc.run(repo))[NAME]["status"] == "ok"
    assert seen == {"slug": "me/vault", "account": "me"}
