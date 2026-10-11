"""Review dossier files (spec 2026-10-11 §8)."""

from pathlib import Path


def test_reviews_dir_is_under_artmind_home():
    import paths

    assert paths.REVIEWS_DIR == paths.ARTMIND_HOME / "reviews"


def test_vault_layout_reviews_dir():
    from artmind.vault import VaultLayout

    assert VaultLayout(Path("/v")).reviews_dir == Path("/v/.artmind/reviews")
