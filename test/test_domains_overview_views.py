"""domains-overview rows carry `views` and `insights` (B9); synthetic data only."""

import pytest
import yaml

import paths
from artmind import graph_query
from artmind.kg_views import store as views_store


def _view(root, name, domains):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "view.yaml").write_text(yaml.safe_dump({
        "name": name, "version": 1, "domains": list(domains), "summary": "s", "examples": ["q"],
        "cypher": "MATCH (n:Entity) WHERE n._domain IN $domains RETURN n.name AS name",
        "presentation": {"format": "list", "list": {"label": "name"}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }))
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\n\n# b\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    views = tmp_path / "views"
    ins = tmp_path / "insights"
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", views)
    monkeypatch.setattr(paths, "INSIGHTS_DIR", ins)
    monkeypatch.setattr(
        graph_query, "_run_read_query",
        lambda c, p: [
            {"domain": d, "parts": [{"k": "documents", "c": 1, "names": ["a.md"]}]}
            for d in ("alpha", "alpha.sub", "beta")
        ],
    )
    import artmind.structured.registry as registry
    monkeypatch.setattr(registry, "list_tables", lambda *a, **k: [])
    return views, ins


def rows(out):
    return {r["domain"]: r for r in out["rows"]}


def test_zero_views_and_counts(env):
    views, ins = env
    r = rows(graph_query.domains_overview())
    assert all(r[d]["views"] == 0 and r[d]["insights"] is False for d in r)
    _view(views, "v1", ["alpha"])
    _view(views, "v2", ["alpha.sub"])
    (views / "broken").mkdir()  # invalid: not counted
    r = rows(graph_query.domains_overview())
    assert r["alpha"]["views"] == 2  # same relation as `views list --domain`
    assert r["alpha.sub"]["views"] == 2
    assert r["beta"]["views"] == 0


def test_insights_flag(env):
    _, ins = env
    (ins / "beta").mkdir(parents=True)
    (ins / "beta" / "latest.json").write_text("{}")
    r = rows(graph_query.domains_overview())
    assert r["beta"]["insights"] is True and r["alpha"]["insights"] is False


def test_store_failure_omits_key_and_warns(env, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(views_store, "list_views", boom)
    out = graph_query.domains_overview()
    assert all("views" not in r for r in out["rows"])
    assert all("insights" in r for r in out["rows"])
    assert any("views count unavailable" in w for w in out["warnings"])
