"""Tests for `artmind ingest sync` CLI command."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli

_OK_RESULT = {"filename": "sample.md", "status": "ok"}
_FAIL_RESULT = {"filename": "sample.md", "status": "failed", "error": "boom"}


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def sample_file(tmp_path) -> Path:
    # A supported suffix: naming a file of a type artmind cannot ingest at
    # all (e.g. .txt) is now refused up front (artmind/ingest.py
    # SUPPORTED_SUFFIXES) rather than reaching these mocked calls.
    f = tmp_path / "sample.md"
    f.write_text("hello")
    return f


@pytest.fixture()
def sample_dir(tmp_path) -> Path:
    # Supported suffixes only: a directory walk filters unsupported types
    # (artmind/ingest.py SUPPORTED_SUFFIXES).
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text("aaa")
    (d / "b.md").write_text("bbb")
    return d


# ── single file ───────────────────────────────────────────────────────────────

class TestIngestSyncFile:
    def test_exits_zero_on_success(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg"):
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        assert result.exit_code == 0

    def test_calls_ingest_file_with_domain(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT) as mock_ingest, \
             patch("artmind.cli.ingest_to_kg"):
            runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_ingest.assert_called_once()
        _, kwargs = mock_ingest.call_args
        assert mock_ingest.call_args[0][2] == "general"

    def test_calls_ingest_to_kg_on_ok(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg") as mock_kg:
            runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_kg.assert_called_once()

    def test_skips_ingest_to_kg_on_failed(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_FAIL_RESULT), \
             patch("artmind.cli.ingest_to_kg") as mock_kg:
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_kg.assert_not_called()
        assert result.exit_code == 0

    def test_nonexistent_path_fails(self, runner):
        result = runner.invoke(cli, ["ingest", "sync", "/no/such/file.txt", "--domain", "general"])
        assert result.exit_code != 0


# ── directory ─────────────────────────────────────────────────────────────────

class TestIngestSyncDirectory:
    def test_processes_all_files(self, runner, sample_dir):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT) as mock_ingest, \
             patch("artmind.cli.ingest_to_kg"):
            runner.invoke(cli, ["ingest", "sync", str(sample_dir), "--domain", "general"])
        assert mock_ingest.call_count == 2

    def test_counts_failures_without_crashing(self, runner, sample_dir):
        with patch("artmind.cli.ingest_file", side_effect=RuntimeError("fail")), \
             patch("artmind.cli.ingest_to_kg"):
            result = runner.invoke(cli, ["ingest", "sync", str(sample_dir), "--domain", "general"])
        assert result.exit_code == 0


# ── domain prompt fallback ────────────────────────────────────────────────────

class TestIngestSyncDomainPrompt:
    def test_prompts_when_domain_omitted(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg"), \
             patch("artmind.cli._prompt_for_domain", return_value="general") as mock_prompt:
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file)])
        mock_prompt.assert_called_once()
        assert result.exit_code == 0


# ── --stage-only ──────────────────────────────────────────────────────────────

def test_ingest_sync_stage_only_passes_flag(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "ingest_file", lambda *a, **k: {"status": "ok"})

    def fake_kg(result, domain, tm, em, cs, stage_only=False, defer_rebuild=False):
        seen["stage_only"] = stage_only
        seen["defer_rebuild"] = defer_rebuild
        return True

    monkeypatch.setattr(cli, "ingest_to_kg", fake_kg)
    monkeypatch.setattr(cli, "load_env", lambda: {})
    monkeypatch.setattr(cli, "resolve_llm_model", lambda env: "m")

    f = tmp_path / "a.md"
    f.write_text("x")
    result = CliRunner().invoke(cli.ingest_sync, [str(f), "--domain", "general", "--stage-only"])
    assert result.exit_code == 0
    assert seen["stage_only"] is True


def test_ingest_sync_default_stage_only_false(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "ingest_file", lambda *a, **k: {"status": "ok"})

    def fake_kg(result, domain, tm, em, cs, stage_only=False, defer_rebuild=False):
        seen["stage_only"] = stage_only
        seen["defer_rebuild"] = defer_rebuild
        return True

    monkeypatch.setattr(cli, "ingest_to_kg", fake_kg)
    monkeypatch.setattr(cli, "load_env", lambda: {})
    monkeypatch.setattr(cli, "resolve_llm_model", lambda env: "m")

    f = tmp_path / "a.md"
    f.write_text("x")
    result = CliRunner().invoke(cli.ingest_sync, [str(f), "--domain", "general"])
    assert result.exit_code == 0
    assert seen["stage_only"] is False


def test_ingest_async_stage_only_passes_flag(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        return "job-123"

    monkeypatch.setattr(cli, "_create_job", fake_create_job)
    monkeypatch.setattr(cli, "_ensure_worker_running", lambda: None)

    f = tmp_path / "a.md"
    f.write_text("x")
    result = CliRunner().invoke(cli.ingest_async, [str(f), "--domain", "general", "--stage-only"])
    assert result.exit_code == 0
    assert seen["stage_only"] is True


def test_ingest_async_default_stage_only_false(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        return "job-123"

    monkeypatch.setattr(cli, "_create_job", fake_create_job)
    monkeypatch.setattr(cli, "_ensure_worker_running", lambda: None)

    f = tmp_path / "a.md"
    f.write_text("x")
    result = CliRunner().invoke(cli.ingest_async, [str(f), "--domain", "general"])
    assert result.exit_code == 0
    assert seen["stage_only"] is False


# ── trap 11: a directory defers the projection to one rebuild at the end ────


def test_a_single_file_rebuilds_incrementally(monkeypatch, tmp_path):
    """One file: the rebuild is a step inside its own commit."""
    import artmind.cli as cli_mod

    deferred: list = []
    monkeypatch.setattr(
        cli_mod, "ingest_to_kg",
        lambda *a, **k: deferred.append(k.get("defer_rebuild")) or True,
    )
    monkeypatch.setattr(
        cli_mod, "ingest_file",
        lambda f, *a, **k: {"status": "ok", "domain": "general", "touched_path": None},
    )
    monkeypatch.setattr(cli_mod, "collect_ingest_files", lambda p: [tmp_path / "a.md"])
    rebuilds: list = []
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: rebuilds.append(d) or {})

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert deferred == [False], "a single file rebuilds inside its own commit"
    assert rebuilds == [], "and needs no separate full rebuild"


def test_a_directory_defers_to_one_full_rebuild_at_the_end(monkeypatch, tmp_path):
    """Rebuilding per document would recompute the same aggregates once per
    contributing file, and would sweep embeddings against descriptions the next
    file is about to change."""
    import artmind.cli as cli_mod

    deferred: list = []
    monkeypatch.setattr(
        cli_mod, "ingest_to_kg",
        lambda *a, **k: deferred.append(k.get("defer_rebuild")) or True,
    )
    monkeypatch.setattr(
        cli_mod, "ingest_file",
        lambda f, *a, **k: {"status": "ok", "domain": "general", "touched_path": None},
    )
    monkeypatch.setattr(
        cli_mod, "collect_ingest_files",
        lambda p: [tmp_path / "a.md", tmp_path / "b.md", tmp_path / "c.md"],
    )
    rebuilds: list = []
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: rebuilds.append(d) or {})

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert deferred == [True, True, True], "every per-document commit defers"
    assert rebuilds == ["general"], "exactly one full rebuild, at the end"


def test_a_batch_of_all_no_op_files_defers_to_zero_rebuilds(monkeypatch, tmp_path):
    """Regression: `ingest_to_kg` returning True (success) for a
    short-circuited no_op/metadata_only file was indistinguishable from one
    that did real extraction, so a resync with nothing changed still queued a
    full projection rebuild for every domain touched, on every run."""
    import artmind.cli as cli_mod

    monkeypatch.setattr(cli_mod, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr(
        cli_mod, "ingest_file",
        lambda f, *a, **k: {
            "status": "ok", "domain": "general", "touched_path": None, "tier": "no_op",
        },
    )
    monkeypatch.setattr(
        cli_mod, "collect_ingest_files",
        lambda p: [tmp_path / "a.md", tmp_path / "b.md"],
    )
    rebuilds: list = []
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: rebuilds.append(d) or {})

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert rebuilds == [], "nothing changed -- no domain should need a rebuild"


def test_a_mixed_batch_only_rebuilds_the_domain_that_actually_changed(monkeypatch, tmp_path):
    import artmind.cli as cli_mod

    files = [tmp_path / "changed.md", tmp_path / "unchanged.md"]
    results_by_file = {
        files[0]: {"status": "ok", "domain": "general", "touched_path": None, "tier": "content"},
        files[1]: {"status": "ok", "domain": "general", "touched_path": None, "tier": "metadata_only"},
    }
    monkeypatch.setattr(cli_mod, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr(cli_mod, "ingest_file", lambda f, *a, **k: results_by_file[f])
    monkeypatch.setattr(cli_mod, "collect_ingest_files", lambda p: files)
    rebuilds: list = []
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: rebuilds.append(d) or {})

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])

    assert result.exit_code == 0, result.output
    assert rebuilds == ["general"], "the one file that actually changed still triggers its rebuild"


# ── durability: the vault commit happens per document, not per batch ─────────
#
# `ingest_file` writes artmind frontmatter (_artmind_id, _version, ...) into the
# user's own vault file and hands the path back as `touched_path`. Committing
# those only after the whole batch finished put every chunk split and every LLM
# extraction call between the disk write and the durable record — so one Ctrl-C
# or one provider timeout left the entire batch modified-but-uncommitted in the
# user's vault. The async worker has always committed per file (worker.py:132).


def _seeded_md(directory, name):
    f = directory / name
    f.write_text(f"# {name}\n\nBody.\n", encoding="utf-8")
    return f


def _stub_batch(monkeypatch, files):
    """Drive `ingest sync` over `files` with the pipeline stubbed out."""
    import artmind.cli as cli_mod

    monkeypatch.setattr(cli_mod, "collect_ingest_files", lambda p: files)
    monkeypatch.setattr(
        cli_mod, "ingest_file",
        lambda f, *a, **k: {"status": "ok", "domain": "general", "touched_path": f},
    )
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: {})
    monkeypatch.setattr("artmind.vault_git.load_env", lambda: {})


def test_each_document_is_committed_before_the_next_is_extracted(monkeypatch, tmp_path):
    """The commit is what makes the frontmatter write durable, so it must land
    before the slow, interruptible work — not after all of it."""
    import artmind.cli as cli_mod

    files = [_seeded_md(tmp_path, n) for n in ("a.md", "b.md", "c.md")]
    _stub_batch(monkeypatch, files)

    events: list[tuple] = []
    monkeypatch.setattr(
        "artmind.vault_git.commit_paths",
        lambda paths, message: events.append(
            ("commit", [Path(p).name for p in paths], message)
        ) or True,
    )
    monkeypatch.setattr("artmind.vault_git.maybe_push", lambda: events.append(("push",)))
    monkeypatch.setattr(
        cli_mod, "ingest_to_kg",
        lambda result, *a, **k: events.append(
            ("extract", Path(result["touched_path"]).name)
        ) or True,
    )

    result = CliRunner().invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])
    assert result.exit_code == 0, result.output

    assert events == [
        ("commit", ["a.md"], "artmind: ingest a.md"),
        ("extract", "a.md"),
        ("commit", ["b.md"], "artmind: ingest b.md"),
        ("extract", "b.md"),
        ("commit", ["c.md"], "artmind: ingest c.md"),
        ("extract", "c.md"),
        ("push",),
    ], "each document commits alone, before its own extraction"


def test_an_interruption_mid_batch_leaves_earlier_documents_committed(monkeypatch, tmp_path):
    """The exact failure that stranded 10 files in the corpus vault: a batch
    interrupted partway through used to commit nothing at all."""
    import artmind.cli as cli_mod

    files = [_seeded_md(tmp_path, n) for n in ("a.md", "b.md", "c.md")]
    _stub_batch(monkeypatch, files)

    committed: list[list[str]] = []
    monkeypatch.setattr(
        "artmind.vault_git.commit_paths",
        lambda paths, message: committed.append([Path(p).name for p in paths]) or True,
    )

    def interrupt_on_b(result, *a, **k):
        if Path(result["touched_path"]).name == "b.md":
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(cli_mod, "ingest_to_kg", interrupt_on_b)

    result = CliRunner().invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])
    assert result.exit_code != 0, "Ctrl-C aborts the run"

    assert committed == [["a.md"], ["b.md"]], (
        "every document whose frontmatter was written is already committed; "
        "the one never reached is not"
    )


def test_nothing_is_committed_or_pushed_when_no_vault_file_was_touched(monkeypatch, tmp_path):
    """A file outside a vault has no `touched_path` — no commit, and no push."""
    import artmind.cli as cli_mod

    files = [_seeded_md(tmp_path, "a.md")]
    _stub_batch(monkeypatch, files)
    monkeypatch.setattr(
        cli_mod, "ingest_file",
        lambda f, *a, **k: {"status": "ok", "domain": "general", "touched_path": None},
    )
    monkeypatch.setattr(cli_mod, "ingest_to_kg", lambda *a, **k: True)

    calls: list[str] = []
    monkeypatch.setattr(
        "artmind.vault_git.commit_paths",
        lambda paths, message: calls.append("commit") or True,
    )
    monkeypatch.setattr("artmind.vault_git.maybe_push", lambda: calls.append("push"))

    result = CliRunner().invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])
    assert result.exit_code == 0, result.output
    assert calls == []


def test_push_is_still_batched_to_one_call_after_the_loop(monkeypatch, tmp_path):
    """Push is a network courtesy, not the durable write, so it stays batched —
    N documents must not mean N round trips."""
    import artmind.cli as cli_mod

    files = [_seeded_md(tmp_path, n) for n in ("a.md", "b.md", "c.md")]
    _stub_batch(monkeypatch, files)
    monkeypatch.setattr(cli_mod, "ingest_to_kg", lambda *a, **k: True)
    monkeypatch.setattr("artmind.vault_git.commit_paths", lambda paths, message: True)

    pushes: list[int] = []
    monkeypatch.setattr("artmind.vault_git.maybe_push", lambda: pushes.append(1))

    result = CliRunner().invoke(cli, ["ingest", "sync", str(tmp_path), "--domain", "general"])
    assert result.exit_code == 0, result.output
    assert len(pushes) == 1


# ── the same property against a real git repo ───────────────────────────────


def _init_git_repo(path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def test_interrupted_batch_leaves_a_real_vault_clean_for_the_files_it_touched(
    monkeypatch, tmp_path
):
    """End to end through real `git`: the stranded-frontmatter failure showed up
    as `git status` dirt in the user's vault, so assert on the vault itself."""
    import subprocess

    import artmind.cli as cli_mod
    import artmind.vault_git as vault_git

    vault = tmp_path / "vault"
    vault.mkdir()
    files = [_seeded_md(vault, n) for n in ("a.md", "b.md", "c.md")]
    _init_git_repo(vault)

    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(vault_git, "load_env", lambda: {})
    monkeypatch.setattr(cli_mod, "collect_ingest_files", lambda p: files)
    monkeypatch.setattr("artmind.ingest.rebuild_projection", lambda d: {})

    def fake_ingest_file(f, *a, **k):
        # What the real ingest_file does to the user's file: write frontmatter.
        f.write_text(f"---\n_artmind_id: id-{f.stem}\n_version: 1\n---\n\n# {f.name}\n", encoding="utf-8")
        return {"status": "ok", "domain": "general", "touched_path": f}

    def interrupt_on_c(result, *a, **k):
        if Path(result["touched_path"]).name == "c.md":
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(cli_mod, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(cli_mod, "ingest_to_kg", interrupt_on_c)

    result = CliRunner().invoke(cli, ["ingest", "sync", str(vault), "--domain", "general"])
    assert result.exit_code != 0

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert status.strip() == "", f"vault left dirty after an interrupted batch:\n{status}"

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    for name in ("a.md", "b.md", "c.md"):
        assert f"artmind: ingest {name}" in log, f"{name} never reached git history"

    committed_a = subprocess.run(
        ["git", "show", "HEAD~2:a.md"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "_artmind_id: id-a" in committed_a, "a.md's frontmatter is in history, not just on disk"
