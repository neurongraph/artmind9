"""kg_views.store: location, listing, version guard, and setup never touching views."""

from pathlib import Path

import pytest
import yaml

import paths
from artmind.kg_views import store
from artmind.kg_views.model import ViewError


def view_yaml(name="v1", version=1, cypher=None, domains=("finance",)):
    return {
        "name": name,
        "version": version,
        "domains": list(domains),
        "summary": "s",
        "examples": ["q"],
        "cypher": cypher
        or "MATCH (n:Entity) WHERE n._domain IN $domains RETURN n.name AS name",
        "presentation": {"format": "list", "list": {"label": "name"}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }


def write_view(root: Path, name="v1", skill_name=None, **kw) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "view.yaml").write_text(yaml.safe_dump(view_yaml(name, **kw)))
    (folder / "SKILL.md").write_text(
        f"---\nname: {skill_name or name}\ndescription: when to use it\n---\n\n# body\n"
    )
    return folder


@pytest.fixture
def views_root(tmp_path, monkeypatch):
    root = tmp_path / "home" / "domains" / "views"
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", root)
    return root


def test_views_dir_follows_paths(views_root):
    assert store.views_dir() == views_root


def test_list_reports_invalid_views_without_hiding_the_good_ones(views_root):
    write_view(views_root, "good")
    bad = write_view(views_root, "bad")
    (bad / "view.yaml").write_text("name: bad\n")
    write_view(views_root, "wrongskill", skill_name="other")
    good, invalid = store.list_views()
    assert [v.spec.name for v in good] == ["good"]
    assert {i["name"] for i in invalid} == {"bad", "wrongskill"}
    assert "frontmatter name" in next(i["error"] for i in invalid if i["name"] == "wrongskill")


def test_list_filters_by_domain_family(views_root):
    write_view(views_root, "fin", domains=("finance",))
    write_view(views_root, "hr", domains=("hr",))
    assert [v.spec.name for v in store.list_views(["finance.retail"])[0]] == ["fin"]
    assert [v.spec.name for v in store.list_views(["finance"])[0]] == ["fin"]
    assert [v.spec.name for v in store.list_views(["fin"])[0]] == []
    assert [v.spec.name for v in store.list_views()[0]] == ["fin", "hr"]


def test_list_on_a_missing_directory_is_empty(views_root):
    assert store.list_views() == ([], [])


def test_domain_covered():
    assert store.domain_covered("finance.retail", ["finance"])
    assert store.domain_covered("finance", ["finance"])
    assert not store.domain_covered("finance", ["finance.retail"])
    assert not store.domain_covered("financeX", ["finance"])


def test_load_view_rejects_path_tricks_and_unknown_names(views_root):
    with pytest.raises(ViewError, match="not a valid view name"):
        store.load_view("../etc")
    with pytest.raises(ViewError, match="no saved view"):
        store.load_view("ghost")


def test_save_copies_only_the_two_files(views_root, tmp_path):
    draft = write_view(tmp_path / "drafts", "v1")
    (draft / "scratch.txt").write_text("junk")
    out = store.save_view(draft)
    assert out == {"saved": "v1", "version": 1, "path": str(views_root / "v1"), "replaced": False}
    assert sorted(p.name for p in (views_root / "v1").iterdir()) == ["SKILL.md", "view.yaml"]
    assert store.load_view("v1").spec.version == 1


def test_save_refuses_changed_cypher_without_a_version_bump(views_root, tmp_path):
    store.save_view(write_view(tmp_path / "d1", "v1"))
    changed = write_view(
        tmp_path / "d2", "v1",
        cypher="MATCH (n:Entity) WHERE n._domain IN $domains RETURN n.description AS name",
    )
    with pytest.raises(ViewError, match="bump `version`"):
        store.save_view(changed)
    assert "n.name" in store.load_view("v1").spec.cypher  # untouched


def test_save_accepts_a_version_bump_and_a_prose_only_change(views_root, tmp_path):
    store.save_view(write_view(tmp_path / "d1", "v1"))
    bumped = write_view(
        tmp_path / "d2", "v1", version=2,
        cypher="MATCH (n:Entity) WHERE n._domain IN $domains RETURN n.description AS name",
    )
    assert store.save_view(bumped)["replaced"] is True
    assert store.load_view("v1").spec.version == 2
    same = write_view(tmp_path / "d3", "v1", version=2,
                      cypher="MATCH (n:Entity) WHERE n._domain IN $domains RETURN n.description AS name")
    (same / "SKILL.md").write_text("---\nname: v1\ndescription: reworded\n---\n")
    store.save_view(same)  # same fingerprint: allowed without a bump
    assert "reworded" in store.load_view("v1").skill_md


def test_save_validates_the_draft_first(views_root, tmp_path):
    draft = write_view(tmp_path / "d", "v1")
    (draft / "SKILL.md").unlink()
    with pytest.raises(ViewError, match="no SKILL.md"):
        store.save_view(draft)
    assert not views_root.exists()


def test_delete(views_root, tmp_path):
    store.save_view(write_view(tmp_path / "d", "v1"))
    assert store.delete_view("v1")["deleted"] == "v1"
    assert not (views_root / "v1").exists()
    with pytest.raises(ViewError, match="no saved view"):
        store.delete_view("v1")
