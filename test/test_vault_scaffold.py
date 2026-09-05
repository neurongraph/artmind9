"""What `artmind init` writes into a fresh directory (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmind import vault


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return tmp_path


def test_curation_and_schemas_are_committed(tmp_path):
    """same_as.yaml is authoritative curation; losing it means redoing human
    merge adjudication."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "domains" / "schemas").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "same_as.yaml").write_text("groups: []\n")
    (tmp_path / ".artmind" / "domains" / "schemas" / "general_schema.yaml").write_text("name: general\n")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert ".artmind/same_as.yaml" in status
    assert ".artmind/domains/schemas/general_schema.yaml" in status


def test_writing_the_gitignore_twice_does_not_duplicate_it(tmp_path):
    (tmp_path / ".artmind").mkdir()
    vault.write_gitignore(tmp_path)
    first = (tmp_path / ".gitignore").read_text()
    vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == first


def test_an_existing_gitignore_is_appended_to_not_replaced(tmp_path):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".gitignore").write_text(".DS_Store\n")

    vault.write_gitignore(tmp_path)

    content = (tmp_path / ".gitignore").read_text()
    assert ".DS_Store" in content
    assert ".artmind/data/" in content


def test_scaffold_creates_the_vault_skeleton(tmp_path):
    from artmind.setup import scaffold_vault

    result = scaffold_vault(tmp_path)

    layout = vault.VaultLayout(tmp_path)
    assert layout.artmind_dir.is_dir()
    assert layout.schemas_dir.is_dir()
    assert layout.data_dir.is_dir()
    assert layout.logs_dir.is_dir()
    assert layout.meta_yaml.is_file()
    assert layout.config_env.is_file()
    assert result["vault"] == str(tmp_path)


def test_scaffold_seeds_starter_schemas_only(tmp_path):
    """A personal vault has no use for the banking demo corpus's domains, and
    offering domains with no data degrades the agent's routing."""
    from artmind.setup import scaffold_vault

    seeded = scaffold_vault(tmp_path)["schemas"]

    assert "general" in seeded
    assert not [s for s in seeded if s.startswith("banking")], seeded


def test_scaffold_never_overwrites_an_edited_schema(tmp_path):
    """Overwrite-always was safe for one reseeded run folder; here it would
    destroy authored work."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# my edit\n")

    scaffold_vault(tmp_path)

    assert "# my edit" in schema.read_text()


def test_scaffold_never_overwrites_config_env(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    scaffold_vault(tmp_path)

    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_scaffold_writes_a_starter_vault_yaml(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    import yaml
    manifest = yaml.safe_load(vault.VaultLayout(tmp_path).vault_yaml.read_text())
    assert manifest["ingest"]["trigger"] == "manual"
    assert manifest["ingest"]["mappings"] == []


def test_the_starter_manifest_parses_with_the_real_reader(tmp_path):
    """The template is the first thing a user edits; if it does not parse, the
    first thing they see is an error."""
    from artmind.manifest import load
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    loaded = load(tmp_path)

    assert loaded.trigger == "manual"
    assert loaded.mappings == []


def test_scaffold_symlinks_skills_to_the_installed_copy(tmp_path):
    """One canonical copy, so an artmind upgrade reaches every vault without
    re-seeding."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    linked = vault.VaultLayout(tmp_path).skills_dir / "artmind-query"
    assert linked.is_symlink()
    assert (linked / "SKILL.md").is_file()


def test_scaffold_symlinks_opencode_agents_to_the_installed_copy(tmp_path):
    """Same rationale as skills: an ACP session's `cwd` must be the vault
    root for `.opencode/agent/*.md` (the `artmind`/`artmind-admin` persona
    modes) to be discoverable there at all -- see docs/vault.md, "ACP agent
    modes"."""
    from artmind.setup import scaffold_vault

    result = scaffold_vault(tmp_path)

    agents_dir = vault.VaultLayout(tmp_path).opencode_agents_dir
    linked = agents_dir / "artmind.md"
    assert linked.is_symlink()
    assert linked.is_file()
    assert (agents_dir / "artmind-admin.md").is_symlink()
    assert "artmind.md" in result["opencode_agents"]
    assert "artmind-admin.md" in result["opencode_agents"]


def test_a_new_vault_keeps_its_data_inside_itself(tmp_path):
    """The seeded config must not hijack the data dir.

    `_render_config_env`'s template is a separate, hand-authored vault-level
    template --
    NOT derived from `artmind/env.example` (the machine-level template, which
    no longer carries ARTMIND_DATA_DIR at all, precisely so it can never be
    seeded into a vault's config.env this way again). This guards the
    invariant directly regardless of where either template's content comes
    from: a fresh vault's own config.env must never set ARTMIND_DATA_DIR,
    since doing so would send every new vault's data to one shared directory
    — the exact coupling the vault model exists to remove.
    """
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env.read_text()

    active = [ln for ln in config.splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    assert not [ln for ln in active if ln.startswith("ARTMIND_DATA_DIR=")], active


def test_a_new_vault_config_holds_no_machine_level_identity(tmp_path):
    """Credentials and models belong to the machine; a vault is a repo you may
    push (docs/vault.md)."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env.read_text()

    active = [ln for ln in config.splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    for leaked in ("ARTMIND_OPENROUTER_API_KEY", "ARTMIND_KG_LLM_MODEL",
                   "ANTHROPIC_AUTH_TOKEN", "ARTMIND_KG_EMBEDDINGS_MODEL"):
        assert not [ln for ln in active if ln.startswith(f"{leaked}=")], leaked


def test_derived_output_is_committed(tmp_path):
    """The ownership rule: .artmind/ is artmind's, and it is versioned."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "markdowns").mkdir(parents=True)
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "markdowns" / "deck.md").write_text("# deck")
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc" / "chunks.json").write_text("[]")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "data/markdowns/deck.md" in status
    assert "data/kg/general/doc/chunks.json" in status


def test_the_graph_password_is_still_never_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\n")

    assert "config.env" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_the_registry_is_not_committed(tmp_path):
    """A SQLite binary rewritten on every ingest merges catastrophically, and
    `docs reindex` rebuilds it. Covers the -shm/-wal siblings too: SQLite
    leaves those beside the .db file, and they are exactly as churning."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "document_registry.db").write_bytes(b"sqlite")
    (tmp_path / ".artmind" / "data" / "document_registry.db-shm").write_bytes(b"shm")
    (tmp_path / ".artmind" / "data" / "document_registry.db-wal").write_bytes(b"wal")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "document_registry.db" not in status
    assert "document_registry.db-shm" not in status
    assert "document_registry.db-wal" not in status


def test_archives_are_never_committed_wherever_they_are(tmp_path):
    """Snapshots are large opaque duplicates of what git already versions, and
    the rule is by extension so one dropped anywhere stays out -- for any of
    the three archive extensions the block excludes."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "graph_snapshot").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "graph_snapshot" / "s.zip").write_bytes(b"zip")
    (tmp_path / "stray.tar.gz").write_bytes(b"tgz")
    (tmp_path / "another.tgz").write_bytes(b"tgz")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "s.zip" not in status
    assert "stray.tar.gz" not in status
    assert "another.tgz" not in status


def test_binaries_in_the_vault_are_now_committed(tmp_path):
    """This reverses the previous model, which gitignored them and left them
    with no version history and no second copy."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / "area1").mkdir()
    (tmp_path / "area1" / "deck.pptx").write_bytes(b"binary")

    assert "area1/deck.pptx" in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_the_embedding_sidecar_is_not_committed(tmp_path):
    """Vectors are cached locally so an ingest does not embed twice, but they
    are undeltable and a clone rebuilds them."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc" / "embeddings.json").write_text("{}")
    (tmp_path / ".artmind" / "data" / "kg" / "general" / "doc" / "chunks.json").write_text("[]")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "embeddings.json" not in status
    assert "chunks.json" in status, "the staging itself is still committed"


def test_logs_and_runtime_state_are_not_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "logs").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "logs" / "x.log").write_text("log")
    (tmp_path / ".artmind" / "state.json").write_text("{}")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert "x.log" not in status
    assert "state.json" not in status


def test_symlinked_skills_are_actually_ignored_by_the_written_gitignore(tmp_path):
    """`GITIGNORE_BLOCK`'s `.claude/skills/artmind-*` pattern is written
    relative to the vault root and must match `VaultLayout.skills_dir`
    (`<vault>/.claude/skills/`) exactly -- a plausible-looking pattern that is
    off by one path segment (e.g. missing/extra a leading directory) silently
    stops matching and the symlink gets committed as vault content, or worse,
    `git add -A` follows it and commits the *installed package's* skill files
    through it. Exercised against real git, not string-matched against the
    pattern, so a rewritten pattern that merely looks plausible still gets
    caught here."""
    from artmind.setup import scaffold_vault

    _init_repo(tmp_path)
    scaffold_vault(tmp_path)
    vault.write_gitignore(tmp_path)

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert ".claude/skills/artmind-query" not in status, status


def test_symlinked_opencode_agents_are_actually_ignored_by_the_written_gitignore(tmp_path):
    """Mirrors the skills version above: `.opencode/agent/artmind*.md` must
    match `VaultLayout.opencode_agents_dir` (`<vault>/.opencode/agent/`)
    exactly, or the symlinked persona files get committed as vault content."""
    from artmind.setup import scaffold_vault

    _init_repo(tmp_path)
    scaffold_vault(tmp_path)
    vault.write_gitignore(tmp_path)

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert ".opencode/agent/artmind.md" not in status, status
    assert ".opencode/agent/artmind-admin.md" not in status, status


def test_a_user_authored_opencode_agent_stays_committable(tmp_path):
    """The gitignore pattern is scoped to artmind's own personas
    (`artmind*.md`) -- a hand-written custom agent must stay vault content,
    same as a hand-written skill does."""
    from artmind.setup import scaffold_vault

    _init_repo(tmp_path)
    scaffold_vault(tmp_path)
    vault.write_gitignore(tmp_path)
    custom = vault.VaultLayout(tmp_path).opencode_agents_dir / "my-custom-agent.md"
    custom.write_text("---\ndescription: mine\n---\nHello.\n")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")

    assert ".opencode/agent/my-custom-agent.md" in status, status


def test_scaffold_run_folder_does_not_plant_an_uningored_skills_copy_in_a_vault(tmp_path, monkeypatch):
    """Regression: `artmind setup` (`scaffold_run_folder`, via `setup_all`)
    used to seed a SECOND skills copy at `<vault>/.artmind/.claude/skills/`
    (because `ARTMIND_HOME` is the vault's `.artmind/` when run inside one) --
    a real, un-symlinked copy the gitignore pattern above does not reach
    (that pattern is relative to the vault root, one level up from
    `.artmind/`), so package-shipped skill files got committed as vault
    content. `init` (`scaffold_vault`) alone must be the only thing that
    seeds a vault's skills.

    `resolve_vault()` walks up from the cwd, so this chdirs into the vault
    for real rather than mocking it -- the same discovery path `artmind
    setup` uses live."""
    import artmind.setup as setup_mod
    from artmind.setup import scaffold_vault

    _init_repo(tmp_path)
    scaffold_vault(tmp_path)
    vault.write_gitignore(tmp_path)

    home = vault.VaultLayout(tmp_path).artmind_dir
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup_mod, "ARTMIND_HOME", home)
    setup_mod.scaffold_run_folder()

    assert not (home / ".claude" / "skills").exists()
    assert not (home / ".opencode").exists()

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert ".artmind/.claude/skills" not in status, status
    assert ".artmind/.opencode" not in status, status


# ── config_answers / git_remote (artmind init --interactive) ────────────────


def test_scaffold_writes_supplied_config_answers(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path, config_answers={
        "neo4j_uri": "neo4j+s://mydb.databases.neo4j.io",
        "neo4j_username": "myuser",
        "neo4j_password": "hunter2",
        "neo4j_database": "mydb",
        "git_push": True,
    })

    config = vault.VaultLayout(tmp_path).config_env.read_text()
    assert "ARTMIND_KG_NEO4J_URI=neo4j+s://mydb.databases.neo4j.io" in config
    assert "ARTMIND_KG_NEO4J_USERNAME=myuser" in config
    assert "ARTMIND_KG_NEO4J_PASSWORD=hunter2" in config
    assert "ARTMIND_KG_NEO4J_DATABASE=mydb" in config
    assert "ARTMIND_VAULT_GIT_PUSH=1" in config.splitlines()


def test_scaffold_config_answers_never_overwrite_an_existing_config_env(tmp_path):
    """Same idempotence rule as the no-answers path: a second `init` (even an
    interactive one) must not clobber a config.env the user may have hand-edited."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    scaffold_vault(tmp_path, config_answers={"neo4j_database": "clobbered"})

    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_scaffold_configures_a_git_remote_when_given_one(tmp_path):
    from artmind.setup import scaffold_vault

    _init_repo(tmp_path)
    result = scaffold_vault(tmp_path, git_remote="https://github.com/example/vault.git")

    assert result["git_remote"] == "added"
    remotes = _git(tmp_path, "remote", "-v")
    assert "https://github.com/example/vault.git" in remotes


def test_scaffold_reports_no_git_remote_action_when_none_given(tmp_path):
    from artmind.setup import scaffold_vault

    result = scaffold_vault(tmp_path)

    assert result["git_remote"] is None
