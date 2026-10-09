"""CliRunner over `query views list/show/run` (the read path, proxied through `serve`)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

import paths
from artmind.cli import cli
from artmind.kg_views.params import Resolution

CYPHER = (
    "MATCH (p:Entity {_id: $product}) WHERE p._domain IN $domains "
    "RETURN p._id AS _id, p.name AS name"
)


def write_view(root: Path, name="v1", version=1, cypher=CYPHER, domains=("finance",)) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "view.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name, "version": version, "domains": list(domains), "summary": "A summary",
                "examples": ["Which products?"],
                "params": {"product": {"type": "entity", "entity_class": "Product", "description": "start"}},
                "cypher": cypher,
                "presentation": {"format": "list", "list": {"label": "name"}},
                "provenance": {"created": "2026-10-09", "origin": "authored"},
            }
        )
    )
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: use me\n---\n\nGuidance.\n")
    return folder


@pytest.fixture
def views_root(tmp_path, monkeypatch):
    root = tmp_path / "home" / "domains" / "views"
    monkeypatch.setattr(paths, "DOMAIN_VIEWS_DIR", root)
    return root


def invoke(*args):
    return CliRunner().invoke(cli, ["--help"] if not args else list(args))


def ok_envelope(**kw):
    base = {"view": "v1", "version": 1, "domains": ["finance"], "status": "ok", "params": {},
            "rows": [], "rows_total": 0, "truncated": False,
            "presentation": {"format": "list", "list": {"label": "name"}}}
    base.update(kw)
    return base


def test_query_views_list_filters_and_reports_invalid(views_root):
    write_view(views_root, "fin", domains=("finance",))
    write_view(views_root, "hr", domains=("hr",))
    (views_root / "broken").mkdir()
    res = invoke("query", "views", "list", "--domain", "finance", "--compact")
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert [v["name"] for v in data["views"]] == ["fin"]
    entry = data["views"][0]
    assert entry["params"]["product"] == {
        "type": "entity", "required": True, "description": "start", "entity_class": "Product",
    }
    assert entry["presentation_format"] == "list" and entry["examples"] == ["Which products?"]
    assert [i["name"] for i in data["invalid"]] == ["broken"]


def test_query_views_show_returns_yaml_and_skill(views_root):
    write_view(views_root, "fin")
    res = invoke("query", "views", "show", "fin", "--compact")
    data = json.loads(res.stdout)
    assert data["view"]["name"] == "fin" and data["view"]["provenance"]["created"] == "2026-10-09"
    assert "Guidance." in data["skill_md"]
    bad = invoke("query", "views", "show", "ghost")
    assert bad.exit_code != 0 and "no saved view" in bad.output


def test_query_views_run_passes_params_domains_and_render(views_root):
    write_view(views_root, "fin")
    with patch("artmind.kg_views.runner.run_view", return_value=ok_envelope()) as run:
        res = invoke("query", "views", "run", "fin", "--domain", "finance,finance.retail",
                     "--param", "product=@e1", "--param", "x=a=b", "--render", "markdown", "--compact")
    assert res.exit_code == 0, res.output
    spec, domains, raw = run.call_args.args
    assert spec.name == "fin" and domains == ["finance", "finance.retail"]
    assert raw == {"product": "@e1", "x": "a=b"}
    assert run.call_args.kwargs == {"render": True}
    assert json.loads(res.stdout)["status"] == "ok"


def test_query_views_run_without_render_flag(views_root):
    write_view(views_root, "fin")
    with patch("artmind.kg_views.runner.run_view", return_value=ok_envelope()) as run:
        invoke("query", "views", "run", "fin", "--domain", "finance")
    assert run.call_args.kwargs == {"render": False}


def test_query_views_run_end_to_end_with_a_mocked_graph(views_root):
    """Through the real runner + params: the Cypher and bindings that reach the session."""
    from unittest.mock import MagicMock

    write_view(views_root, "fin")
    session = MagicMock()
    session.run.return_value = iter([{"_id": "e1", "name": "Mortgage"}])
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    with patch("artmind.kg_views.runner.read_session", return_value=ctx), patch(
        "artmind.kg_views.runner.expand_domain_family", return_value=["finance"]
    ), patch(
        "artmind.kg_views.params.resolve_params",
        return_value=Resolution(bindings={"product": "e1"}, echo={"product": {"_id": "e1"}}),
    ):
        res = invoke("query", "views", "run", "fin", "--domain", "finance",
                     "--param", "product=Mortgage", "--render", "markdown", "--compact")
    assert res.exit_code == 0, res.output
    sent, bindings = session.run.call_args.args  # the runner wraps the Cypher in a timeout Query
    assert sent.text == CYPHER
    assert bindings == {"product": "e1", "domains": ["finance"]}
    assert json.loads(res.stdout)["rendered"] == "- **Mortgage**"


def test_domain_outside_the_view_is_a_click_error(views_root):
    write_view(views_root, "fin")
    res = invoke("query", "views", "run", "fin", "--domain", "hr")
    assert res.exit_code != 0 and "cannot run for ['hr']" in res.output
