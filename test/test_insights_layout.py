"""Where insights live (spec 2026-10-17 §4.3, §9.1)."""

import paths
from artmind.vault import VaultLayout


def test_vault_layout_insights_dirs(tmp_path):
    layout = VaultLayout(tmp_path)
    assert layout.insights_config_dir == tmp_path / ".artmind" / "domains" / "insights"
    assert layout.insights_dir == tmp_path / ".artmind" / "insights"


def test_paths_insights_dirs_follow_artmind_home():
    assert paths.DOMAIN_INSIGHTS_DIR == paths.ARTMIND_HOME / "domains" / "insights"
    assert paths.INSIGHTS_DIR == paths.ARTMIND_HOME / "insights"
    # Tuning lives beside views, never inside schemas/: schema_set_hash() hashes schemas/*.yaml.
    assert paths.DOMAIN_INSIGHTS_DIR.parent == paths.DOMAIN_SCHEMAS_DIR.parent
    assert paths.DOMAIN_INSIGHTS_DIR != paths.DOMAIN_SCHEMAS_DIR


def test_networkx_algorithms_used_by_insights_are_available():
    import networkx as nx
    from networkx.algorithms import community

    g = nx.karate_club_graph()
    assert community.louvain_communities(g, seed=7)
    assert nx.betweenness_centrality(g)
