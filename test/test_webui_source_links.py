"""Source-document links: path validation, obsidian:// URIs, POST /api/open-source."""

import pytest
from fastapi.testclient import TestClient

import paths
from artmind.webui import app as webui_app
from artmind.webui.app import create_app
from artmind.webui.sessions import SessionRegistry
from artmind.webui.source_links import SourceLinkError, obsidian_uri, vault_relative


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "my_work"
    (root / "performance_management").mkdir(parents=True)
    (root / "performance_management" / "Delivery Leads.md").write_text("# Delivery Leads\n")
    (root / "performance_management" / "report.xlsx").write_bytes(b"xlsx")
    (root / ".artmind" / "data").mkdir(parents=True)
    (root / ".artmind" / "data" / "converted.md").write_text("internal copy\n")
    (tmp_path / "outside.md").write_text("not in the vault\n")
    return root


def test_vault_relative_accepts_relative_and_absolute_inside_vault(vault):
    rel = "performance_management/Delivery Leads.md"
    assert vault_relative(rel, vault) == rel
    assert vault_relative(str(vault / rel), vault) == rel


@pytest.mark.parametrize(
    "path, reason",
    [
        ("../outside.md", "not inside the vault"),
        (".artmind/data/converted.md", "hidden folder"),
        ("performance_management/missing.md", "does not exist"),
    ],
)
def test_vault_relative_rejects(vault, path, reason):
    with pytest.raises(SourceLinkError, match=reason):
        vault_relative(path, vault)


def test_vault_relative_rejects_absolute_path_outside_vault(vault):
    with pytest.raises(SourceLinkError, match="not inside the vault"):
        vault_relative(str(vault.parent / "outside.md"), vault)


def test_obsidian_uri_drops_md_extension_only(vault):
    assert obsidian_uri(vault, "performance_management/Delivery Leads.md") == (
        "obsidian://open?vault=my_work&file=performance_management%2FDelivery%20Leads&paneType=tab"
    )
    assert obsidian_uri(vault, "performance_management/report.xlsx") == (
        "obsidian://open?vault=my_work&file=performance_management%2Freport.xlsx&paneType=tab"
    )


@pytest.fixture
def client_and_opened(vault, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(webui_app, "open_uri", opened.append)
    client = TestClient(create_app(registry=SessionRegistry(client_factory=lambda backend: None)))
    return client, opened


def test_open_source_hands_obsidian_uri_to_os(client_and_opened):
    client, opened = client_and_opened
    response = client.post("/api/open-source", json={"path": "performance_management/Delivery Leads.md"})
    assert response.status_code == 200
    assert opened == ["obsidian://open?vault=my_work&file=performance_management%2FDelivery%20Leads&paneType=tab"]
    assert response.json()["path"] == "performance_management/Delivery Leads.md"


def test_open_source_refuses_path_outside_vault_without_opening(client_and_opened):
    client, opened = client_and_opened
    response = client.post("/api/open-source", json={"path": "../outside.md"})
    assert response.status_code == 404
    assert "not inside the vault" in response.json()["detail"]
    assert opened == []


def test_open_source_without_vault_is_409(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", None)
    monkeypatch.setattr(webui_app, "open_uri", opened.append)
    client = TestClient(create_app(registry=SessionRegistry(client_factory=lambda backend: None)))
    response = client.post("/api/open-source", json={"path": "a.md"})
    assert response.status_code == 409
    assert opened == []
