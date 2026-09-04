"""Run-folder seeding policy: package assets refresh, user data is preserved.

The distinction matters because the chat agent runs with cwd set to the run
folder, so it reads the seeded *copies* of the skills — not the package. When
seeding skipped entries that already existed, a fixed skill stayed broken there
indefinitely and no reinstall could dislodge it.
"""

import inspect

from artmind.setup import _seed_tree, _setup_neo4j, setup_all


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ── package assets (overwrite=True) ───────────────────────────────────────────


def test_package_asset_edit_reaches_an_existing_dest(tmp_path):
    """The regression: an edited skill must land even though the name exists."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "artmind-query" / "SKILL.md", "artmind query graph pattern10")
    _write(dest / "artmind-query" / "SKILL.md", "pattern10")  # stale seeded copy

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert (dest / "artmind-query" / "SKILL.md").read_text() == "artmind query graph pattern10"


def test_overwrite_replaces_wholesale_so_removed_files_disappear(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "skill" / "SKILL.md", "current")
    _write(dest / "skill" / "SKILL.md", "old")
    _write(dest / "skill" / "reference.md", "dropped from the package")

    _seed_tree(src, dest, overwrite=True)

    assert (dest / "skill" / "SKILL.md").read_text() == "current"
    assert not (dest / "skill" / "reference.md").exists()


def test_overwrite_leaves_names_the_package_does_not_ship(tmp_path):
    """A user's own skill in the run folder is not pruned."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "shipped" / "SKILL.md", "shipped")
    _write(dest / "mine" / "SKILL.md", "hand-written")

    _seed_tree(src, dest, overwrite=True)

    assert (dest / "mine" / "SKILL.md").read_text() == "hand-written"
    assert (dest / "shipped" / "SKILL.md").exists()


def test_overwrite_handles_single_files(tmp_path):
    """opencode seeds a file tree, not only directories."""
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "agent.md", "new persona")
    _write(dest / "agent.md", "old persona")

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert (dest / "agent.md").read_text() == "new persona"


# ── user data (overwrite=False) ───────────────────────────────────────────────


def test_user_edits_survive_when_not_overwriting(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "banking.yaml", "shipped default")
    _write(dest / "banking.yaml", "locally tuned")

    assert _seed_tree(src, dest) == 0
    assert (dest / "banking.yaml").read_text() == "locally tuned"


def test_new_entries_are_seeded_either_way(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / "fresh.yaml", "shipped")
    dest.mkdir()

    assert _seed_tree(src, dest) == 1
    assert (dest / "fresh.yaml").read_text() == "shipped"


# ── shared behaviour ──────────────────────────────────────────────────────────


def test_missing_source_is_a_noop(tmp_path):
    assert _seed_tree(tmp_path / "absent", tmp_path / "run", overwrite=True) == 0


def test_ds_store_is_never_seeded(tmp_path):
    src, dest = tmp_path / "pkg", tmp_path / "run"
    _write(src / ".DS_Store", "junk")
    _write(src / "skill" / "SKILL.md", "real")

    assert _seed_tree(src, dest, overwrite=True) == 1
    assert not (dest / ".DS_Store").exists()


# ── scaffold_run_folder ───────────────────────────────────────────────────────


def test_scaffold_run_folder_creates_structured_snapshot_dir(tmp_path, monkeypatch):
    import artmind.setup as setup

    home = tmp_path / "home"
    data = tmp_path / "data"
    monkeypatch.setattr(setup, "ARTMIND_HOME", home)
    monkeypatch.setattr(setup, "ARTMIND_DATA_DIR", data)
    monkeypatch.setattr(setup, "DOMAIN_SCHEMAS_DIR", home / "domains" / "schemas")
    monkeypatch.setattr(setup, "LOGS_DIR", home / "logs")
    monkeypatch.setattr(setup, "ORIGINALS_DIR", data / "documents" / "originals")
    monkeypatch.setattr(setup, "MARKDOWNS_DIR", data / "documents" / "markdowns")
    monkeypatch.setattr(setup, "JOBS_DIR", data / "ingestion_jobs")
    monkeypatch.setattr(setup, "KG_DIR", data / "kg")
    monkeypatch.setattr(setup, "REFINE_DIR", data / "refine")
    monkeypatch.setattr(setup, "GRAPH_SNAPSHOT_DIR", data / "graph_snapshot")
    monkeypatch.setattr(setup, "STRUCTURED_DIR", data / "structured")
    monkeypatch.setattr(setup, "STRUCTURED_SNAPSHOT_DIR", data / "structured_snapshot")
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", tmp_path / "no-such-env-example")
    monkeypatch.setattr(setup, "PACKAGE_SKILLS_DIR", tmp_path / "no-such-skills")
    monkeypatch.setattr(setup, "PACKAGE_OPENCODE_DIR", tmp_path / "no-such-opencode")
    monkeypatch.setattr(setup, "PACKAGE_SCHEMAS_DIR", tmp_path / "no-such-schemas")

    setup.scaffold_run_folder()

    assert (data / "structured_snapshot").is_dir()


def _patch_scaffold_dirs(setup, monkeypatch, home, data):
    monkeypatch.setattr(setup, "ARTMIND_HOME", home)
    monkeypatch.setattr(setup, "ARTMIND_DATA_DIR", data)
    monkeypatch.setattr(setup, "DOMAIN_SCHEMAS_DIR", home / "domains" / "schemas")
    monkeypatch.setattr(setup, "LOGS_DIR", home / "logs")
    monkeypatch.setattr(setup, "ORIGINALS_DIR", data / "documents" / "originals")
    monkeypatch.setattr(setup, "MARKDOWNS_DIR", data / "documents" / "markdowns")
    monkeypatch.setattr(setup, "JOBS_DIR", data / "ingestion_jobs")
    monkeypatch.setattr(setup, "KG_DIR", data / "kg")
    monkeypatch.setattr(setup, "REFINE_DIR", data / "refine")
    monkeypatch.setattr(setup, "GRAPH_SNAPSHOT_DIR", data / "graph_snapshot")
    monkeypatch.setattr(setup, "STRUCTURED_DIR", data / "structured")
    monkeypatch.setattr(setup, "STRUCTURED_SNAPSHOT_DIR", data / "structured_snapshot")
    monkeypatch.setattr(setup, "PACKAGE_SKILLS_DIR", home.parent / "no-such-skills")
    monkeypatch.setattr(setup, "PACKAGE_OPENCODE_DIR", home.parent / "no-such-opencode")
    monkeypatch.setattr(setup, "PACKAGE_SCHEMAS_DIR", home.parent / "no-such-schemas")


def test_scaffold_run_folder_skips_env_seed_when_home_is_a_vault(tmp_path, monkeypatch):
    """A vault's own `.artmind/` must never receive the machine-wide `.env`
    template: its literal `ARTMIND_KG_LLM_PROVIDER=ollama` and
    `ARTMIND_DATA_DIR=~/artmind_data` defaults outrank the real machine
    config in paths.py's load order (docs/vault.md), so seeding it here
    silently sends every fresh vault's LLM calls to local Ollama and its
    ingest output to the shared ~/artmind_data instead of the vault."""
    import artmind.setup as setup
    from artmind.vault import VaultLayout

    vault_root = tmp_path / "myvault"
    vault_root.mkdir()
    home = VaultLayout(vault_root).artmind_dir
    data = tmp_path / "data"
    _patch_scaffold_dirs(setup, monkeypatch, home, data)

    template = tmp_path / "env.example"
    template.write_text("ARTMIND_KG_LLM_PROVIDER=ollama\n")
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", template)
    monkeypatch.setattr(setup, "resolve_vault", lambda: vault_root)

    result = setup.scaffold_run_folder()

    assert not (home / ".env").exists()
    assert result["env"] == "skipped (vault-resident; see ~/.artmind/config.env)"


def test_scaffold_run_folder_skips_skills_and_opencode_seed_in_a_vault(tmp_path, monkeypatch):
    """A vault's skills live at `<vault>/.claude/skills/` (symlinked by
    `scaffold_vault`/`init`), not `<vault>/.artmind/.claude/skills/` --
    seeding here would create a second, un-symlinked copy the vault's
    `.gitignore` (written relative to the vault root) does not match, so it
    would get committed as vault content despite being a reproducible
    package asset. `.opencode/` isn't part of the vault model at all
    (docs/vault.md) and belongs only at the true machine home."""
    import artmind.setup as setup
    from artmind.vault import VaultLayout

    vault_root = tmp_path / "myvault"
    vault_root.mkdir()
    home = VaultLayout(vault_root).artmind_dir
    data = tmp_path / "data"
    _patch_scaffold_dirs(setup, monkeypatch, home, data)

    _write(tmp_path / "pkg-skills" / "artmind-query" / "SKILL.md", "content")
    _write(tmp_path / "pkg-opencode" / "agent" / "artmind.md", "content")
    monkeypatch.setattr(setup, "PACKAGE_SKILLS_DIR", tmp_path / "pkg-skills")
    monkeypatch.setattr(setup, "PACKAGE_OPENCODE_DIR", tmp_path / "pkg-opencode")
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", tmp_path / "no-such-env-example")
    monkeypatch.setattr(setup, "resolve_vault", lambda: vault_root)

    result = setup.scaffold_run_folder()

    assert not (home / ".claude" / "skills").exists()
    assert not (home / ".opencode").exists()
    assert result["skills_refreshed"] == 0
    assert result["opencode_refreshed"] == 0


def test_scaffold_run_folder_still_seeds_skills_and_opencode_outside_a_vault(tmp_path, monkeypatch):
    """The machine home (no vault in play) keeps seeding skills/opencode."""
    import artmind.setup as setup

    home = tmp_path / "home"
    data = tmp_path / "data"
    _patch_scaffold_dirs(setup, monkeypatch, home, data)

    _write(tmp_path / "pkg-skills" / "artmind-query" / "SKILL.md", "content")
    _write(tmp_path / "pkg-opencode" / "agent" / "artmind.md", "content")
    monkeypatch.setattr(setup, "PACKAGE_SKILLS_DIR", tmp_path / "pkg-skills")
    monkeypatch.setattr(setup, "PACKAGE_OPENCODE_DIR", tmp_path / "pkg-opencode")
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", tmp_path / "no-such-env-example")
    monkeypatch.setattr(setup, "resolve_vault", lambda: None)

    result = setup.scaffold_run_folder()

    assert (home / ".claude" / "skills" / "artmind-query" / "SKILL.md").read_text() == "content"
    assert (home / ".opencode" / "agent" / "artmind.md").read_text() == "content"
    assert result["skills_refreshed"] == 1
    assert result["opencode_refreshed"] == 1


def test_scaffold_run_folder_still_seeds_env_outside_a_vault(tmp_path, monkeypatch):
    """The machine home (no vault in play) keeps seeding `.env` as before."""
    import artmind.setup as setup

    home = tmp_path / "home"
    data = tmp_path / "data"
    _patch_scaffold_dirs(setup, monkeypatch, home, data)

    template = tmp_path / "env.example"
    template.write_text("ARTMIND_KG_LLM_PROVIDER=ollama\n")
    monkeypatch.setattr(setup, "PACKAGE_ENV_EXAMPLE", template)
    monkeypatch.setattr(setup, "resolve_vault", lambda: None)

    result = setup.scaffold_run_folder()

    assert (home / ".env").read_text() == "ARTMIND_KG_LLM_PROVIDER=ollama\n"
    assert result["env"] == "seeded from template"


# ── _setup_neo4j: structured-store catalogue constraints ─────────────────────
# No live Neo4j in this suite, so these are structural checks: the DDL strings
# must appear verbatim in the function source (`session.run(...)` never
# actually executes here).


def test_setup_neo4j_declares_catalogue_constraints():
    src = inspect.getsource(_setup_neo4j)
    assert (
        "CREATE CONSTRAINT cat_table_key IF NOT EXISTS FOR (n:Table) REQUIRE n.key IS UNIQUE"
        in src
    )
    assert (
        "CREATE CONSTRAINT cat_column_key IF NOT EXISTS FOR (n:TableColumn) REQUIRE n.key IS UNIQUE"
        in src
    )
    assert (
        "CREATE CONSTRAINT cat_entityclass_key IF NOT EXISTS FOR (n:EntityClass) REQUIRE n.key IS UNIQUE"
        in src
    )
    assert "CREATE INDEX cat_table_domain IF NOT EXISTS FOR (n:Table) ON (n.domain)" in src


def test_setup_all_summary_includes_catalogue_labels():
    src = inspect.getsource(setup_all)
    for name in ("cat_table_key", "cat_column_key", "cat_entityclass_key", "cat_table_domain"):
        assert name in src


def test_setup_all_summary_no_longer_mentions_entity_version():
    """The :EntityVersion zone (constraint + 3 indexes) is gone (Phase 4) —
    entity_history.py and `query graph entity-versions` went with it. Nothing
    in setup_all's summary should still reference it."""
    src = inspect.getsource(setup_all)
    for name in ("entity_version_id", "entity_version_entity", "entity_version_valid_to", "entity_version_domain"):
        assert name not in src


# ── A3: graph indices for scoped graph-view re-queries ───────────────────────


def test_setup_neo4j_declares_filing_metadata_indexes():
    """A2/A3: project + area indexes on Document and DocChunk keep filing
    filters index-backed instead of falling to full scans."""
    src = inspect.getsource(_setup_neo4j)
    for cypher in (
        "CREATE INDEX document_project IF NOT EXISTS FOR (n:Document) ON (n.project)",
        "CREATE INDEX document_area IF NOT EXISTS FOR (n:Document) ON (n.area)",
        "CREATE INDEX chunk_project IF NOT EXISTS FOR (n:DocChunk) ON (n.project)",
        "CREATE INDEX chunk_area IF NOT EXISTS FOR (n:DocChunk) ON (n.area)",
    ):
        assert cypher in src


def test_setup_neo4j_declares_a3_composite_indexes():
    """A3: composite (filter, domain) indexes let the planner start on the
    selective filter and stay index-scan for the domain narrow."""
    src = inspect.getsource(_setup_neo4j)
    for cypher in (
        "CREATE INDEX entity_class IF NOT EXISTS FOR (n:Entity) ON (n.entity_class)",
        # Entity carries `_domain` (Phase 4's `_`-prefix), not `domain`.
        "CREATE INDEX entity_class_domain IF NOT EXISTS FOR (n:Entity) ON (n.entity_class, n._domain)",
        "CREATE INDEX document_project_domain IF NOT EXISTS FOR (n:Document) ON (n.project, n.domain)",
        "CREATE INDEX document_area_domain IF NOT EXISTS FOR (n:Document) ON (n.area, n.domain)",
        "CREATE INDEX chunk_project_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.project, n.domain)",
        "CREATE INDEX chunk_area_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.area, n.domain)",
        "CREATE INDEX chunk_doc_id_id IF NOT EXISTS FOR (n:DocChunk) ON (n.doc_id, n.id)",
        "CREATE INDEX document_name IF NOT EXISTS FOR (n:Document) ON (n.name)",
    ):
        assert cypher in src


def test_setup_neo4j_declares_document_name_fulltext():
    """A3: pattern10's CONTAINS query on d.name benefits from a fulltext index."""
    src = inspect.getsource(_setup_neo4j)
    assert (
        "CREATE FULLTEXT INDEX document_name_ft IF NOT EXISTS FOR (d:Document) ON EACH [d.name, d.title]"
        in src
    )


def test_setup_all_summary_includes_a3_indexes():
    """setup_all's summary must list every index _setup_neo4j creates so
    `artmind setup` prints an accurate summary."""
    src = inspect.getsource(setup_all)
    for name in (
        "entity_class",
        "entity_class_domain",
        "document_project_domain",
        "document_area_domain",
        "chunk_project_domain",
        "chunk_area_domain",
        "chunk_doc_id_id",
        "document_name",
        "document_name_ft",
    ):
        assert name in src, f"setup_all summary missing {name}"
