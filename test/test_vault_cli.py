"""`artmind init` and `artmind vault` (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from artmind import vault
from artmind.cli import cli


def test_init_makes_the_current_directory_a_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert vault.VaultLayout(tmp_path).artmind_dir.is_dir()
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_init_next_steps_point_at_ingest_async(tmp_path, monkeypatch):
    """Step 5 of the onboarding review (docs/onboarding-review.md) found the
    command to actually ingest a vault's documents wasn't obvious -- point at
    it directly instead of leaving the user to find `ingest async` on their own."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "artmind ingest async ." in result.output


def test_init_runs_git_init_when_the_directory_is_not_a_repo(tmp_path, monkeypatch):
    """The vault IS a git repo: identity, history and the ingest cursor all
    depend on it."""
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    assert (tmp_path / ".git").is_dir()


def test_init_leaves_an_existing_repo_alone(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "note.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "first" in log


def test_init_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# edited\n")

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "# edited" in schema.read_text()


def test_init_accepts_an_explicit_path(tmp_path):
    target = tmp_path / "MyVault"
    target.mkdir()

    result = CliRunner().invoke(cli, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert (target / ".artmind").is_dir()


def test_vault_reports_the_active_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert str(tmp_path.resolve()) in result.output


def test_init_remote_flag_configures_origin_non_interactively(tmp_path, monkeypatch):
    """--remote works without --interactive -- e.g. from a script."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli, ["init", "--remote", "https://github.com/example/vault.git"]
    )

    assert result.exit_code == 0, result.output
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "https://github.com/example/vault.git" in remotes
    assert "Remote:   origin -> https://github.com/example/vault.git" in result.output


def test_init_default_is_non_interactive(tmp_path, monkeypatch):
    """No flags, no stdin available (CliRunner's default) -- must still exit 0
    with the plain-placeholder config.env, exactly like before --interactive
    existed. This is the automation path (`just dev-install`): nobody is at
    the keyboard, so prompting by default here would break it."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    config = vault.VaultLayout(tmp_path).config_env.read_text()
    assert "ARTMIND_KG_NEO4J_USERNAME=neo4j" in config


def test_init_interactive_prompts_for_neo4j_connection_and_remote(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    answers = "\n".join([
        "neo4j+s://mydb.databases.neo4j.io",  # Neo4j URI
        "myuser",  # Neo4j username
        "hunter2",  # Neo4j password
        "mydb",  # Neo4j database
        "https://github.com/example/vault.git",  # remote URL
    ]) + "\n"

    result = CliRunner().invoke(cli, ["init", "--interactive"], input=answers)

    assert result.exit_code == 0, result.output
    config = vault.VaultLayout(tmp_path).config_env.read_text()
    assert "ARTMIND_KG_NEO4J_URI=neo4j+s://mydb.databases.neo4j.io" in config
    assert "ARTMIND_KG_NEO4J_USERNAME=myuser" in config
    assert "ARTMIND_KG_NEO4J_PASSWORD=hunter2" in config
    assert "ARTMIND_KG_NEO4J_DATABASE=mydb" in config
    assert "ARTMIND_VAULT_GIT_PUSH" not in config
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "https://github.com/example/vault.git" in remotes


def test_init_interactive_skips_prompts_when_config_env_already_exists(tmp_path, monkeypatch):
    """Re-running `init --interactive` on an already-configured vault must not
    prompt at all -- there's no answers to gather, and a leftover config.env
    may hold hand-edited (e.g. Aura) values nothing here should touch."""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    # No input supplied: if this tried to prompt, it would abort on EOF.
    result = CliRunner().invoke(cli, ["init", "--interactive"], input="")

    assert result.exit_code == 0, result.output
    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_vault_outside_a_vault_explains_rather_than_guessing(tmp_path, monkeypatch):
    """`git status` outside a repo, not a silent default."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.delenv("ARTMIND_HOME", raising=False)

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code != 0
    assert "artmind init" in result.output


def test_vault_bare_invocation_shows_help_not_status(tmp_path, monkeypatch):
    """`vault` is now a plain group -- bare invocation shows --help (rather
    than running `status`), the same convention every other group in this
    CLI already follows (db, ingest, domains, query, ...): rich-click's
    `no_args_is_help` prints usage and exits non-zero (2), it doesn't exit 0.
    Use `vault status` for the status report."""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code == 2
    assert "Usage:" in result.output


def _init_and_commit(tmp_path):
    """`artmind init` only runs `git init` -- never a commit -- so every
    `vault sync` CLI test needs at least one real commit before it can call
    `head_sha()` successfully (see `vault_sync.head_sha`'s "no commits yet"
    handling)."""
    CliRunner().invoke(cli, ["init"])
    (tmp_path / "note.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)


def test_vault_sync_refuses_without_marker_or_bootstrap_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync"])

    assert result.exit_code != 0
    assert "bootstrapEmpty" in result.output


def test_vault_sync_bootstrap_synced_stamps_the_cursor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync", "--bootstrapSynced", "--compact"])

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["bootstrap"] == "synced"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert payload["graph_bookmark"] == head
    assert payload["structured_bookmark"] == head
    state = vault.read_state(vault.VaultLayout(tmp_path))
    assert state == {"last_structured_commit": head}


def test_vault_sync_store_option_reaches_sync(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(
        cli, ["vault", "sync", "--store", "structured", "--bootstrapSynced", "--compact"]
    )

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["stores"] == ["structured"]
    assert "graph_bookmark" not in payload


def test_vault_sync_dry_run_reports_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync", "--bootstrapEmpty", "--dryRun", "--compact"])

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    from artmind import vault as vault_mod
    assert vault_mod.read_state(vault_mod.VaultLayout(tmp_path)) == {}


def test_vault_sync_domain_option_reaches_the_cli_layer(tmp_path, monkeypatch):
    """Only proves Click's option parsing and the `_parse_domains(domain) if
    domain else None` wiring don't blow up when `--domain` is passed --
    `sync()`'s own domain-scoping logic is already covered by its own unit
    tests, not re-verified here."""
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(
        cli, ["vault", "sync", "--bootstrapEmpty", "--dryRun", "--domain", "banking", "--compact"]
    )

    assert result.exit_code == 0, result.output


def test_vault_doctor_reports_json_and_exits_nonzero_on_failure(tmp_path, monkeypatch):
    import json

    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(cli, ["init"]).exit_code == 0
    subprocess.run(["git", "config", "pull.rebase", "true"], cwd=tmp_path, check=True)

    result = CliRunner().invoke(cli, ["vault", "doctor", "--compact"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert {c["name"]: c["status"] for c in payload["checks"]}["pull.rebase"] == "fail"


# ── `vault status`: the sync half (spec 2026-09-26 §10) ──────────────────────


def _fake_graph(monkeypatch, bookmark=None, fingerprints=None, unreachable=False):
    from artmind import sync_state

    def _read_bookmark(vault_id, *, timeout=None):
        if unreachable:
            raise OSError("connection refused")
        return bookmark

    monkeypatch.setattr(sync_state, "read_graph_bookmark", _read_bookmark)
    monkeypatch.setattr(
        sync_state, "read_document_fingerprints",
        lambda doc_ids, *, timeout=None: {d: (fingerprints or {})[d] for d in doc_ids if d in (fingerprints or {})},
    )


def _vault_with_docs(tmp_path, monkeypatch):
    """An init'd vault whose KG staging dir is the vault's own, with one
    committed document and a structured bookmark at that commit."""
    import paths

    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)
    kg = tmp_path / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", kg)
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", tmp_path / ".artmind" / "data" / "structured_text")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    folder = kg / "banking" / "doc1"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text('{"id": "docid-1"}')
    (folder / "observations.json").write_text("[]")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "doc1"], cwd=tmp_path, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    vault.write_state(vault.VaultLayout(tmp_path), {"last_structured_commit": base})
    return base, head


def test_vault_status_reports_head_bookmarks_and_pending_work_as_json(tmp_path, monkeypatch):
    import json

    base, head = _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, bookmark=base)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    assert result.exit_code == 0, result.output
    sync = json.loads(result.stdout)["sync"]
    assert sync["head"] == head
    from artmind.manifest import read_vault_id
    assert sync["vault_id"] == read_vault_id(tmp_path)
    assert sync["stores"]["graph"]["bookmark"] == base
    assert (sync["stores"]["graph"]["state"], sync["stores"]["graph"]["docs"]) == ("behind", 1)
    assert sync["stores"]["structured"]["state"] == "current"
    assert sync["operation_in_progress"] is None
    assert sync["unresolved_conflicts"] == []
    assert sync["message"] == "artmind: graph is 1 docs / 0 tables behind the vault — run `artmind vault sync`"


def test_vault_status_human_output_names_each_store(tmp_path, monkeypatch):
    base, head = _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, bookmark=head)

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert f"HEAD:     {head}" in result.output
    assert f"Sync:     graph      {head}  current" in result.output
    assert f"Sync:     structured {base}  current" in result.output
    assert "Merge:    none in progress" in result.output
    assert "Conflicts: none under .artmind/" in result.output


def test_vault_status_survives_an_unreachable_graph(tmp_path, monkeypatch):
    import json

    _vault_with_docs(tmp_path, monkeypatch)
    _fake_graph(monkeypatch, unreachable=True)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    assert result.exit_code == 0, result.output
    sync = json.loads(result.stdout)["sync"]
    assert sync["graph_error"].startswith("graph unreachable")
    assert list(sync["stores"]) == ["structured"]


def test_vault_status_shows_a_merge_and_artmind_conflicts(tmp_path, monkeypatch):
    import json

    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)
    _fake_graph(monkeypatch)
    path = tmp_path / ".artmind" / "same_as.yaml"
    path.write_text("base\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-qb", "other"], cwd=tmp_path, check=True)
    path.write_text("theirs\n")
    subprocess.run(["git", "commit", "-qam", "theirs"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-"], cwd=tmp_path, check=True)
    path.write_text("ours\n")
    subprocess.run(["git", "commit", "-qam", "ours"], cwd=tmp_path, check=True)
    subprocess.run(["git", "merge", "other"], cwd=tmp_path, capture_output=True)

    result = CliRunner().invoke(cli, ["vault", "status", "--compact"])

    sync = json.loads(result.stdout)["sync"]
    assert sync["operation_in_progress"] == "a merge"
    assert sync["unresolved_conflicts"] == [".artmind/same_as.yaml"]


def test_vault_status_in_a_vault_with_no_commits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    _fake_graph(monkeypatch)

    result = CliRunner().invoke(cli, ["vault", "status"])

    assert result.exit_code == 0, result.output
    assert "HEAD:     (no commits yet)" in result.output


def _base_sync_report(**overrides):
    """A minimal `status_report()`-shaped dict, for testing `_echo_sync_status`
    directly without going through a real vault/git/graph."""
    report = {
        "head": "abc123",
        "vault_id": "vault-under-test",
        "operation_in_progress": None,
        "unresolved_conflicts": [],
        "legacy_cursor": None,
        "graph_error": None,
        "stores": {
            "graph": {"bookmark": "abc123", "state": "current", "docs": 0, "tables": [], "detail": None},
            "structured": {"bookmark": "abc123", "state": "current", "docs": 0, "tables": [], "detail": None},
        },
        "message": None,
    }
    report.update(overrides)
    return report


def test_echo_sync_status_collapses_a_multiline_error_detail_to_one_line(capsys):
    """Mirrors `test_staleness_message_collapses_a_multiline_detail_to_one_stderr_line`
    in test_vault_sync.py: a broken table mapping's PyYAML error is genuinely
    multi-line (header, `in "...", line N, column M:`, source snippet, `^`
    caret). `_echo_sync_status`'s per-store `state == "error"` branch must
    collapse it the same way `staleness_message` already does, or it breaks
    the one-`Sync:`-line-per-store contract the rest of the human-readable
    output relies on."""
    from artmind.cli import _echo_sync_status

    multiline_detail = (
        'mapping values are not allowed here\n'
        '  in "<unicode string>", line 1, column 5:\n'
        '    a: b: c\n'
        '        ^'
    )
    sync = _base_sync_report(
        stores={
            "graph": {"bookmark": "abc123", "state": "error", "docs": 0, "tables": [], "detail": multiline_detail},
            "structured": {"bookmark": "abc123", "state": "current", "docs": 0, "tables": [], "detail": None},
        },
    )

    _echo_sync_status(sync)

    out = capsys.readouterr().out
    graph_line = next(line for line in out.splitlines() if line.startswith("Sync:     graph"))
    assert "\n" not in graph_line
    assert graph_line == (
        "Sync:     graph      abc123  cannot tell — mapping values are not allowed here "
        'in "<unicode string>", line 1, column 5: a: b: c ^'
    )


def test_echo_sync_status_collapses_a_multiline_graph_error_to_one_line(capsys):
    """Same hazard as above, for the "store absent -> (unknown — ...)" branch:
    `sync["graph_error"]` (e.g. a multi-line exception message) must also be
    collapsed to one line before splicing into the `Sync:` line."""
    from artmind.cli import _echo_sync_status

    sync = _base_sync_report(
        graph_error="graph unreachable: line one\nline two",
        stores={"structured": {"bookmark": "abc123", "state": "current", "docs": 0, "tables": [], "detail": None}},
    )

    _echo_sync_status(sync)

    out = capsys.readouterr().out
    graph_line = next(line for line in out.splitlines() if line.startswith("Sync:     graph"))
    assert "\n" not in graph_line
    assert graph_line == "Sync:     graph      (unknown — graph unreachable: line one line two)"
