"""Vault discovery and layout (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmind import vault


def test_finds_vault_in_the_directory_itself(tmp_path):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_walks_up_to_find_the_vault(tmp_path):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    deep = tmp_path / "notes" / "2026" / "august"
    deep.mkdir(parents=True)

    assert vault.find_vault(deep) == tmp_path.resolve()


def test_returns_none_outside_any_vault(tmp_path):
    assert vault.find_vault(tmp_path) is None


def test_innermost_vault_wins(tmp_path):
    """Nested vaults behave like nested git repos."""
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    inner = tmp_path / "inner"
    (inner / ".artmind").mkdir(parents=True)
    (inner / ".artmind" / "vault.yaml").write_text("ingest: {}\n")

    assert vault.find_vault(inner) == inner.resolve()


def test_a_file_named_dot_artmind_is_not_a_vault(tmp_path):
    (tmp_path / ".artmind").write_text("not a directory")
    assert vault.find_vault(tmp_path) is None


def test_explicit_path_beats_everything(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    (explicit / ".artmind").mkdir(parents=True)
    (explicit / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    other = tmp_path / "other"
    (other / ".artmind").mkdir(parents=True)
    (other / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    monkeypatch.setenv("ARTMIND_VAULT", str(other))
    monkeypatch.chdir(other)

    assert vault.resolve_vault(str(explicit)) == explicit.resolve()


def test_env_var_beats_the_walk_up(tmp_path, monkeypatch):
    """ARTMIND_VAULT exists for cron and anything with no meaningful cwd."""
    env_vault = tmp_path / "env"
    (env_vault / ".artmind").mkdir(parents=True)
    (env_vault / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    (cwd_vault / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    monkeypatch.setenv("ARTMIND_VAULT", str(env_vault))
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == env_vault.resolve()


def test_falls_back_to_the_walk_up(tmp_path, monkeypatch):
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    (cwd_vault / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == cwd_vault.resolve()


def test_resolves_to_none_outside_any_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert vault.resolve_vault() is None


def test_an_env_var_pointing_at_a_non_vault_is_refused(tmp_path, monkeypatch):
    """Silently falling back to the walk-up would ingest into the wrong vault."""
    monkeypatch.setenv("ARTMIND_VAULT", str(tmp_path / "nope"))

    with pytest.raises(vault.VaultError, match="ARTMIND_VAULT"):
        vault.resolve_vault()


def test_layout_places_everything_under_dot_artmind(tmp_path):
    layout = vault.VaultLayout(tmp_path)

    assert layout.artmind_dir == tmp_path / ".artmind"
    assert layout.config_env == tmp_path / ".artmind" / "config.env"
    assert layout.vault_yaml == tmp_path / ".artmind" / "vault.yaml"
    assert layout.state_json == tmp_path / ".artmind" / "state.json"
    assert layout.same_as == tmp_path / ".artmind" / "same_as.yaml"
    assert layout.schemas_dir == tmp_path / ".artmind" / "domains" / "schemas"
    assert layout.meta_yaml == tmp_path / ".artmind" / "domains" / "meta.yaml"
    assert layout.logs_dir == tmp_path / ".artmind" / "logs"
    assert layout.worker_pid == tmp_path / ".artmind" / "worker.pid"


def test_derived_data_is_isolated_under_one_directory(tmp_path):
    """Everything ignorable sits under data/, so one .gitignore line covers it."""
    layout = vault.VaultLayout(tmp_path)
    data = tmp_path / ".artmind" / "data"

    assert layout.data_dir == data
    assert layout.kg_dir == data / "kg"
    assert layout.originals_dir == data / "originals"
    assert layout.chunks_dir == data / "chunks"
    assert layout.registry_db == data / "document_registry.db"
    assert layout.structured_dir == data / "structured"
    assert layout.snapshots_dir == data / "snapshots"
    assert layout.jobs_dir == data / "jobs"
    assert layout.refine_dir == data / "refine"


def test_skills_land_where_claude_code_looks(tmp_path):
    """ClaudeAgentOptions.skills resolves names from .claude/skills relative to
    the agent's cwd, which is the vault."""
    assert vault.VaultLayout(tmp_path).skills_dir == tmp_path / ".claude" / "skills"


def test_a_bare_artmind_directory_is_not_a_vault(tmp_path):
    """The manifest is the marker, not the directory.

    `~/.artmind` is also the machine-wide config directory. Keying discovery on
    the directory alone made $HOME itself resolve as a vault, so every command
    run from anywhere beneath it — which is most places — silently keyed
    document identity off $HOME.
    """
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "config.env").write_text("ARTMIND_USER=someone\n")

    assert vault.find_vault(tmp_path) is None
    assert vault.is_vault(tmp_path) is False


def test_a_legacy_run_folder_does_not_shadow_a_real_vault(tmp_path):
    """A legacy ~/.artmind above a real vault must not win the walk-up."""
    home = tmp_path / "home"
    (home / ".artmind" / "domains").mkdir(parents=True)
    (home / ".artmind" / ".env").write_text("ARTMIND_USER=someone\n")
    real = home / "Notes"
    (real / ".artmind").mkdir(parents=True)
    (real / ".artmind" / "vault.yaml").write_text("ingest: {}\n")

    assert vault.find_vault(real / "subdir") is None or vault.find_vault(real) == real.resolve()
    assert vault.find_vault(real) == real.resolve()


def test_read_state_is_empty_dict_when_state_json_absent(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    assert vault.read_state(layout) == {}


def test_write_state_then_read_state_round_trips(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_synced_commit": "abc123"})
    assert vault.read_state(layout) == {"last_synced_commit": "abc123"}


def test_write_state_merges_rather_than_overwrites(tmp_path):
    """Both cursors (`last_ingested_commit`, `last_synced_commit`) share this
    one file -- a naive overwrite would erase whichever this call didn't
    mention (spec 2026-09-25-vault-sync-design.md §3)."""
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_ingested_commit": "old-cursor"})

    vault.write_state(layout, {"last_synced_commit": "new-cursor"})

    assert vault.read_state(layout) == {
        "last_ingested_commit": "old-cursor",
        "last_synced_commit": "new-cursor",
    }


def test_write_state_removes_named_keys_and_keeps_the_rest(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_synced_commit": "old", "last_ingested_commit": "keep"})

    vault.write_state(layout, {"last_structured_commit": "new"}, remove=("last_synced_commit",))

    assert vault.read_state(layout) == {"last_ingested_commit": "keep", "last_structured_commit": "new"}


def test_read_state_tolerates_corrupt_json(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    layout.state_json.parent.mkdir(parents=True, exist_ok=True)
    layout.state_json.write_text("{not json", encoding="utf-8")
    assert vault.read_state(layout) == {}


def test_gitignore_block_covers_the_worker_pid_file(tmp_path):
    """The worker's pid file now lives at `.artmind/worker.pid`
    (`paths.WORKER_PID_FILE`, `VaultLayout.worker_pid`) -- the GITIGNORE_BLOCK
    line that already names that exact path must actually ignore it, not just
    `.artmind/data/worker.pid` (item 1: the old location, gitignored only by
    a rule that didn't match it)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    vault.write_gitignore(tmp_path)
    layout = vault.VaultLayout(tmp_path)
    layout.artmind_dir.mkdir(parents=True, exist_ok=True)
    layout.worker_pid.write_text("4242")

    result = subprocess.run(
        ["git", "check-ignore", str(layout.worker_pid.relative_to(tmp_path))],
        cwd=tmp_path, capture_output=True, text=True,
    )

    assert result.returncode == 0, f"worker.pid not ignored: {result.stdout!r} {result.stderr!r}"


def test_note_scratch_dir_is_the_vaults_data_dir_only_for_a_real_vault(tmp_path):
    from artmind.vault import note_scratch_dir

    (tmp_path / "plain").mkdir()
    (tmp_path / "real" / ".artmind").mkdir(parents=True)

    assert note_scratch_dir(None) is None
    assert note_scratch_dir(tmp_path / "plain") is None
    assert note_scratch_dir(tmp_path / "real") == tmp_path / "real" / ".artmind" / "data"
    assert note_scratch_dir(str(tmp_path / "real")) == tmp_path / "real" / ".artmind" / "data"
