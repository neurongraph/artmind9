"""Where kg_views live, and the two promises about them: committed, never seeded."""

from pathlib import Path

import paths
from artmind.vault import GITIGNORE_BLOCK, VaultLayout


def test_vault_layout_views_dir(tmp_path):
    assert VaultLayout(tmp_path).views_dir == tmp_path / ".artmind" / "domains" / "views"


def test_paths_views_dir_sits_beside_the_schemas():
    # ARTMIND_HOME is the vault's `.artmind/` inside a vault, ~/.artmind otherwise,
    # so one definition serves both and equals VaultLayout.views_dir in a vault.
    assert paths.DOMAIN_VIEWS_DIR == paths.ARTMIND_HOME / "domains" / "views"
    assert paths.DOMAIN_VIEWS_DIR.parent == paths.DOMAIN_SCHEMAS_DIR.parent


def test_gitignore_block_does_not_cover_views():
    assert "views" not in GITIGNORE_BLOCK


def test_scaffold_run_folder_leaves_views_untouched(tmp_path, monkeypatch):
    """Views are user data: `artmind setup` overwrites skills/schemas but never these."""
    import artmind.setup as setup

    home = tmp_path / "home"
    data = tmp_path / "data"
    keep = home / "domains" / "views" / "mine"
    keep.mkdir(parents=True)
    (keep / "view.yaml").write_text("name: mine\n")
    (keep / "SKILL.md").write_text("---\nname: mine\ndescription: d\n---\n")
    before = {p.name: p.read_text() for p in keep.iterdir()}
    for attr, value in {
        "ARTMIND_HOME": home, "ARTMIND_DATA_DIR": data,
        "DOMAIN_SCHEMAS_DIR": home / "domains" / "schemas", "LOGS_DIR": home / "logs",
        "ORIGINALS_DIR": data / "documents" / "originals",
        "MARKDOWNS_DIR": data / "documents" / "markdowns", "JOBS_DIR": data / "ingestion_jobs",
        "KG_DIR": data / "kg", "REFINE_DIR": data / "refine",
        "GRAPH_SNAPSHOT_DIR": data / "graph_snapshot", "STRUCTURED_DIR": data / "structured",
        "STRUCTURED_SNAPSHOT_DIR": data / "structured_snapshot",
        "PACKAGE_ENV_EXAMPLE": tmp_path / "no-env", "PACKAGE_SKILLS_DIR": tmp_path / "no-skills",
        "PACKAGE_OPENCODE_DIR": tmp_path / "no-opencode", "PACKAGE_SCHEMAS_DIR": tmp_path / "no-schemas",
        "DOMAIN_META_PATH": home / "domains" / "meta.yaml", "PACKAGE_META_YAML": tmp_path / "no-meta",
        "PACKAGE_OBSIDIAN_PLUGIN_DIR": tmp_path / "no-plugin",
        "MACHINE_CONFIG_DIR": tmp_path / "machine" / ".artmind",
        "MACHINE_CONFIG_ENV": tmp_path / "machine" / ".artmind" / "config.env",
    }.items():
        monkeypatch.setattr(setup, attr, value)

    setup.scaffold_run_folder()
    setup.scaffold_run_folder()  # idempotent re-run overwrites assets, not views

    assert {p.name: p.read_text() for p in keep.iterdir()} == before
