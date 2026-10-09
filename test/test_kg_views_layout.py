"""Where kg_views live. (Never-seeded and not-ignored promises are pinned in
test_scaffold_run_folder.py and test_vault_scaffold.py.)"""

import paths
from artmind.vault import VaultLayout


def test_vault_layout_views_dir(tmp_path):
    assert VaultLayout(tmp_path).views_dir == tmp_path / ".artmind" / "domains" / "views"


def test_paths_views_dir_sits_beside_the_schemas():
    # ARTMIND_HOME is the vault's `.artmind/` inside a vault, ~/.artmind otherwise,
    # so one definition serves both and equals VaultLayout.views_dir in a vault.
    assert paths.DOMAIN_VIEWS_DIR == paths.ARTMIND_HOME / "domains" / "views"
    assert paths.DOMAIN_VIEWS_DIR.parent == paths.DOMAIN_SCHEMAS_DIR.parent
