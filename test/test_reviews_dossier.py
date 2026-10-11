"""Review dossier files (spec 2026-10-11 §8)."""

from datetime import date
from pathlib import Path

import paths as _paths

# Capture the unpatched value at import time so test_reviews_dir_is_under_artmind_home
# survives the autouse _isolated_reviews fixture (which monkeypatches paths.REVIEWS_DIR).
_ORIG_REVIEWS_DIR = _paths.REVIEWS_DIR

import pytest

from artmind.reviews import dossier as dz


@pytest.fixture(autouse=True)
def _isolated_reviews(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def test_reviews_dir_is_under_artmind_home():
    import paths

    assert _ORIG_REVIEWS_DIR == paths.ARTMIND_HOME / "reviews"


def test_vault_layout_reviews_dir():
    from artmind.vault import VaultLayout

    assert VaultLayout(Path("/v")).reviews_dir == Path("/v/.artmind/reviews")


def _dossier(**meta):
    base = {"review_id": "rv-2026-01-02-abcdef", "status": "open", "stage": None, "elements": [],
            "trace_ids": ["t1"], "private_terms": [], "graph_state": {}, "created": "2026-01-02",
            "updated": "2026-01-02"}
    base.update(meta)
    return dz.Dossier(meta=base, sections={t: f"body of {t}" for t in dz.SECTIONS})


def test_new_review_id_shape():
    rid = dz.new_review_id(date(2026, 1, 2))
    assert rid.startswith("rv-2026-01-02-") and len(rid) == len("rv-2026-01-02-abcdef")
    assert dz.new_review_id() != dz.new_review_id()


def test_dossier_path_rejects_non_ids():
    import paths

    assert dz.dossier_path("rv-2026-01-02-abcdef") == paths.REVIEWS_DIR / "rv-2026-01-02-abcdef.md"
    with pytest.raises(dz.DossierError):
        dz.dossier_path("../etc/passwd")


def test_render_parse_round_trip():
    d = _dossier(stage="schema", elements=["d/Trip-WENT_TO->"])
    back = dz.parse(dz.render(d))
    assert back.meta == d.meta
    assert back.sections == d.sections


def test_render_orders_sections_and_quotes_dates():
    text = dz.render(_dossier())
    assert text.startswith("---\nreview_id: rv-2026-01-02-abcdef\n")
    assert "created: '2026-01-02'" in text
    positions = [text.index(f"\n## {t}\n") for t in dz.SECTIONS]
    assert positions == sorted(positions)


def test_parse_keeps_unknown_and_fenced_headings_as_content():
    d = _dossier()
    d.sections["Proposal"] = "```yaml\n## not a section\nkey: v\n```\n\n## Not a known section\nmore"
    back = dz.parse(dz.render(d))
    assert back.sections["Proposal"] == d.sections["Proposal"]


def test_parse_normalizes_crlf_and_unquoted_dates():
    text = dz.render(_dossier()).replace("created: '2026-01-02'", "created: 2026-01-02").replace("\n", "\r\n")
    assert dz.parse(text).meta["created"] == "2026-01-02"


def test_parse_errors():
    with pytest.raises(dz.DossierError):
        dz.parse("no frontmatter here")
    with pytest.raises(dz.DossierError):
        dz.parse("---\n- a list\n---\n")


def test_save_load_iter_and_append_log():
    d = _dossier()
    path = dz.save(d)
    assert path.exists()
    assert not any(path.parent.glob("*.tmp"))  # temp files never land in the committed folder
    loaded = dz.load("rv-2026-01-02-abcdef")
    assert loaded.sections == d.sections and loaded.path == path

    dz.append_log(loaded, "status open → fixed: patched", today=date(2026, 1, 3))
    assert loaded.sections["Log"].endswith("\n- 2026-01-03 status open → fixed: patched")

    (path.parent / "broken.md").write_text("garbage")
    found = list(dz.iter_dossiers())
    assert [(p.name, e is None) for p, _, e in found] == [("broken.md", False), (path.name, True)]


def test_load_missing_raises():
    with pytest.raises(dz.DossierError):
        dz.load("rv-2026-01-02-000000")
