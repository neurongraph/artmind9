"""CliRunner over the authoring group: `views validate/test/save/delete`."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

import paths
from artmind.cli import cli

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


def test_validate_draft_saved_and_all(views_root, tmp_path):
    draft = write_view(tmp_path / "drafts", "d1")
    ok = invoke("views", "validate", "--path", str(draft), "--compact")
    assert ok.exit_code == 0 and json.loads(ok.stdout)["results"] == [{"name": "d1", "valid": True, "version": 1}]

    (draft / "view.yaml").write_text("name: d1\n")
    bad = invoke("views", "validate", "--path", str(draft), "--compact")
    assert bad.exit_code == 1
    assert json.loads(bad.stdout)["results"][0]["valid"] is False

    write_view(views_root, "saved")
    everything = invoke("views", "validate", "--compact")
    assert everything.exit_code == 0
    assert [r["name"] for r in json.loads(everything.stdout)["results"]] == ["saved"]
    assert invoke("views", "validate", "saved", "--path", str(draft)).exit_code != 0


def test_test_command_always_renders_explains_and_warns_on_zero_rows(tmp_path):
    draft = write_view(tmp_path / "drafts", "d1")
    envelope = ok_envelope(rendered="_No rows._", plan={"operator": "ProduceResults", "children": []})
    with patch("artmind.kg_views.runner.run_view", return_value=envelope) as run:
        res = invoke("views", "test", "--path", str(draft), "--domain", "finance",
                     "--param", "product=@e1", "--compact")
    assert res.exit_code == 0, res.output
    assert run.call_args.kwargs == {"render": True, "explain": True}
    out = json.loads(res.stdout)
    assert out["plan"]["operator"] == "ProduceResults"
    assert out["warnings"] and "zero rows" in out["warnings"][0]


def test_save_then_delete_round_trip(views_root, tmp_path):
    draft = write_view(tmp_path / "drafts", "d1")
    saved = invoke("views", "save", "--path", str(draft), "--compact")
    assert saved.exit_code == 0, saved.output
    assert json.loads(saved.stdout)["saved"] == "d1"
    assert (views_root / "d1" / "view.yaml").is_file()

    changed = write_view(tmp_path / "d2", "d1", cypher=CYPHER.replace("p.name", "p.description AS name, p.name"))
    refused = invoke("views", "save", "--path", str(changed))
    assert refused.exit_code != 0 and "bump `version`" in refused.output

    gone = invoke("views", "delete", "d1", "--compact")
    assert json.loads(gone.stdout)["deleted"] == "d1" and not (views_root / "d1").exists()
