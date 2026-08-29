"""Workspace resolution, config precedence, and the daemon fingerprint.

docs/workspaces.md. The load-bearing test here is
``test_fingerprint_implementations_agree``: ``paths.workspace_fingerprint`` and
``artmind._entry._fingerprint`` are deliberately two implementations of one
rule (the second is stdlib-only so the proxy's fast path stays fast), and
nothing but a test stops them drifting apart. If they drift, the proxy either
refuses a good daemon forever or — far worse — accepts one bound to another
knowledge base.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import paths
from artmind import _entry

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── resolution ────────────────────────────────────────────────────────────────


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Point the workspace container at a temp dir, and clear every variable
    that outranks the pointer file."""
    container = tmp_path / "artmind_root"
    (container / "workspaces").mkdir(parents=True)
    monkeypatch.setattr(paths, "ARTMIND_ROOT", container)
    monkeypatch.setattr(paths, "WORKSPACES_DIR", container / "workspaces")
    monkeypatch.setattr(paths, "WORKSPACE_POINTER", container / "current")
    for var in ("ARTMIND_HOME", "ARTMIND_WORKSPACE"):
        monkeypatch.delenv(var, raising=False)
    return container


def test_artmind_home_wins_over_everything(root, monkeypatch):
    """The escape hatch CLAUDE.md documents and the test suite relies on."""
    (root / "current").write_text("personal\n")
    monkeypatch.setenv("ARTMIND_WORKSPACE", "work")
    monkeypatch.setenv("ARTMIND_HOME", str(root / "elsewhere"))

    folder, name = paths._resolve_run_folder()

    assert folder == (root / "elsewhere").resolve()
    assert name is None, "ARTMIND_HOME bypasses the registry, so there is no name"


def test_env_var_beats_pointer_file(root, monkeypatch):
    (root / "current").write_text("personal\n")
    monkeypatch.setenv("ARTMIND_WORKSPACE", "work")

    folder, name = paths._resolve_run_folder()

    assert folder == (root / "workspaces" / "work").resolve()
    assert name == "work"


def test_pointer_file_selects_the_workspace(root):
    (root / "current").write_text("personal\n")

    folder, name = paths._resolve_run_folder()

    assert folder == (root / "workspaces" / "personal").resolve()
    assert name == "personal"


def test_no_workspace_keeps_the_pre_workspace_layout(root):
    """An install that never adopts a workspace must behave exactly as before:
    ~/.artmind IS the run folder."""
    folder, name = paths._resolve_run_folder()

    assert folder == root
    assert name is None


def test_blank_pointer_file_is_not_a_workspace(root):
    (root / "current").write_text("   \n")

    folder, name = paths._resolve_run_folder()

    assert folder == root
    assert name is None


@pytest.mark.parametrize("evil", ["../escape", "..", "a/b", "/abs", ".hidden", ""])
def test_traversing_names_are_refused(root, monkeypatch, evil):
    """A name is joined onto a path, so it must never walk out of the container."""
    monkeypatch.setenv("ARTMIND_WORKSPACE", evil)

    if evil.strip() == "":
        # An empty name is "no workspace", not an error.
        assert paths._resolve_run_folder() == (root, None)
        return
    with pytest.raises(ValueError, match="Invalid workspace name"):
        paths._resolve_run_folder()


# ── the fingerprint ───────────────────────────────────────────────────────────


def test_fingerprint_implementations_agree():
    """paths (yaml/dotenv-capable) and _entry (stdlib-only) must agree here."""
    assert _entry._fingerprint() == paths.workspace_fingerprint()


@pytest.mark.parametrize(
    "env,pointer",
    [
        ({"ARTMIND_WORKSPACE": "personal"}, None),
        ({}, "personal"),
        ({}, None),
    ],
)
def test_fingerprint_implementations_agree_under_workspace_resolution(tmp_path, env, pointer):
    """The agreement above runs under conftest's ARTMIND_HOME, which short-
    circuits resolution. This exercises the branches that actually differ, in a
    subprocess so `paths`'s import-time constants are recomputed for real."""
    container = tmp_path / "root"
    run_folder = container / "workspaces" / "personal"
    run_folder.mkdir(parents=True)
    (run_folder / ".env").write_text("ARTMIND_KG_NEO4J_DATABASE=wsdb\n")
    (container / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=shared\n")
    if pointer:
        (container / "current").write_text(pointer)

    child = {
        **os.environ,
        "ARTMIND_ROOT": str(container),
        "PYTHONPATH": str(REPO_ROOT),
        **env,
    }
    child.pop("ARTMIND_HOME", None)
    child.pop("ARTMIND_WORKSPACE", None)
    child.pop("ARTMIND_KG_NEO4J_DATABASE", None)
    child.update(env)

    result = subprocess.run(
        [
            sys.executable, "-c",
            "import paths;from artmind import _entry;"
            "print(paths.workspace_fingerprint());print(_entry._fingerprint());"
            "print(paths.ARTMIND_HOME)",
        ],
        capture_output=True, text=True, env=child, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    from_paths, from_entry, run = result.stdout.split()
    assert from_paths == from_entry, f"drifted for env={env} pointer={pointer}"


def test_daemon_with_no_fingerprint_is_refused(monkeypatch):
    """A daemon predating guardrail 2 cannot prove which workspace it serves, so
    it is treated as a mismatch rather than assumed to agree."""
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"service": "artmind", "status": "ok"}'

    monkeypatch.setattr(_entry.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert _entry._daemon_alive("http://127.0.0.1:8377") is False


def test_daemon_for_another_workspace_is_refused(monkeypatch):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return b'{"service": "artmind", "status": "ok", "workspace_fingerprint": "deadbeefdeadbeef"}'

    monkeypatch.setattr(_entry.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert _entry._daemon_alive("http://127.0.0.1:8377") is False


# ── .env parsing in the stdlib mirror ─────────────────────────────────────────


def test_env_value_reads_workspace_env_before_shared(tmp_path, monkeypatch):
    container = tmp_path / "root"
    run_folder = container / "run"
    run_folder.mkdir(parents=True)
    (run_folder / ".env").write_text('ARTMIND_KG_NEO4J_DATABASE="specific"\n')
    (container / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=shared\n")
    monkeypatch.setenv("ARTMIND_ROOT", str(container))
    monkeypatch.delenv("ARTMIND_KG_NEO4J_DATABASE", raising=False)

    assert _entry._env_value("ARTMIND_KG_NEO4J_DATABASE", run_folder) == "specific"


def test_env_value_ignores_comments_and_honours_real_environment(tmp_path, monkeypatch):
    run_folder = tmp_path / "run"
    run_folder.mkdir()
    (run_folder / ".env").write_text("# ARTMIND_KG_NEO4J_DATABASE=commented\nOTHER=1\n")
    monkeypatch.setenv("ARTMIND_ROOT", str(tmp_path))
    monkeypatch.delenv("ARTMIND_KG_NEO4J_DATABASE", raising=False)
    assert _entry._env_value("ARTMIND_KG_NEO4J_DATABASE", run_folder) == ""

    monkeypatch.setenv("ARTMIND_KG_NEO4J_DATABASE", "from-env")
    assert _entry._env_value("ARTMIND_KG_NEO4J_DATABASE", run_folder) == "from-env"


# ── the registry ──────────────────────────────────────────────────────────────


@pytest.fixture
def registry(tmp_path, monkeypatch):
    from artmind import workspace as ws

    monkeypatch.setattr(paths, "WORKSPACE_REGISTRY", tmp_path / "workspaces.yaml")
    monkeypatch.setattr(ws, "WORKSPACE_REGISTRY", tmp_path / "workspaces.yaml")
    monkeypatch.setattr(ws, "WORKSPACE_POINTER", tmp_path / "current")
    return ws


def test_missing_registry_is_not_an_error(registry):
    """The normal state for an install that has never adopted a workspace."""
    assert registry.load_registry()["workspaces"] == {}
    assert registry.names() == []


def test_register_round_trips(registry, tmp_path):
    registry.register(
        "personal",
        vault=str(tmp_path / "vault"),
        data_dir=str(tmp_path / "data"),
        archive_dir=str(tmp_path / "archive"),
        graph={"uri": "neo4j://x", "database": "personal"},
    )
    entry = registry.get("personal")
    assert registry.vault_paths(entry) == [str(tmp_path / "vault")]
    assert entry["graph"]["database"] == "personal"
    assert entry["frozen"] is False


def test_vaults_is_a_list_for_model_b(registry, tmp_path):
    """Shape fixed from the first commit so many-vault support is additive."""
    registry.register(
        "personal", vault=str(tmp_path / "v"), data_dir="d", archive_dir="a", graph={}
    )
    assert isinstance(registry.get("personal")["vaults"], list)


def test_two_workspaces_cannot_claim_one_vault(registry, tmp_path):
    """Guardrail 4 — a shared vault corrupts the path-keyed document registry."""
    vault = tmp_path / "shared"
    vault.mkdir()
    registry.register("a", vault=str(vault), data_dir="d1", archive_dir="x1", graph={})

    with pytest.raises(registry.WorkspaceError, match="already claimed"):
        registry.register("b", vault=str(vault), data_dir="d2", archive_dir="x2", graph={})


def test_reregistering_the_same_workspace_keeps_its_own_vault(registry, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    registry.register("a", vault=str(vault), data_dir="d", archive_dir="x", graph={})
    registry.register("a", vault=str(vault), data_dir="d2", archive_dir="x", graph={})
    assert registry.get("a")["data_dir"] == "d2"


def test_use_refuses_an_unregistered_name(registry):
    with pytest.raises(registry.WorkspaceError, match="No workspace named"):
        registry.set_current("ghost")


def test_use_writes_the_pointer(registry, tmp_path):
    registry.register("personal", vault=None, data_dir="d", archive_dir="a", graph={})
    registry.set_current("personal")
    assert registry.current_name() == "personal"
