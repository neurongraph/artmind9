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


# ── create ────────────────────────────────────────────────────────────────────


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    """A temp workspace container with the registry and pointer redirected."""
    from artmind import workspace as ws

    container = tmp_path / "root"
    (container / "workspaces").mkdir(parents=True)
    for mod in (paths, ws):
        monkeypatch.setattr(mod, "ARTMIND_ROOT", container, raising=False)
        monkeypatch.setattr(mod, "WORKSPACES_DIR", container / "workspaces", raising=False)
        monkeypatch.setattr(mod, "WORKSPACE_POINTER", container / "current", raising=False)
        monkeypatch.setattr(mod, "WORKSPACE_REGISTRY", container / "workspaces.yaml", raising=False)
        monkeypatch.setattr(mod, "SHARED_ENV_FILE", container / "config.env", raising=False)
    return ws


def test_create_builds_run_folder_env_and_registry(workspaces, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    result = workspaces.create(
        "personal", vault=str(vault), graph_uri="neo4j://x", graph_database="personal"
    )

    run_folder = Path(result["run_folder"])
    assert (run_folder / ".env").is_file()
    assert (run_folder / "domains" / "meta.yaml").is_file()
    assert workspaces.get("personal")["graph"]["database"] == "personal"

    env = workspaces.parse_env_file(run_folder / ".env")
    assert env["ARTMIND_VAULT_DIR"] == str(vault.resolve())
    assert env["ARTMIND_KG_NEO4J_DATABASE"] == "personal"


def test_create_does_not_switch_to_the_new_workspace(workspaces, tmp_path):
    """Creating and switching are separate acts — a create must never move
    someone off the knowledge base they were working in."""
    vault = tmp_path / "vault"
    vault.mkdir()
    workspaces.create("personal", vault=str(vault))

    assert workspaces.current_name() is None


def test_create_seeds_starter_schemas_only(workspaces, tmp_path):
    """Guardrail 3: a new workspace must not inherit the banking demo corpus."""
    vault = tmp_path / "vault"
    vault.mkdir()

    result = workspaces.create("personal", vault=str(vault))

    seeded = result["seeded"]["schemas"]
    assert "general" in seeded
    assert not [s for s in seeded if s.startswith("banking")], seeded


def test_create_can_be_asked_for_specific_schemas(workspaces, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()

    result = workspaces.create("work", vault=str(vault), schemas=["banking.*"])

    seeded = result["seeded"]["schemas"]
    assert any(s.startswith("banking.") for s in seeded)
    # `general` is always added — cli._get_available_domains always offers it.
    assert "general" in seeded


def test_create_refuses_a_missing_vault(workspaces, tmp_path):
    with pytest.raises(workspaces.WorkspaceError, match="does not exist"):
        workspaces.create("personal", vault=str(tmp_path / "nope"))


def test_create_refuses_an_existing_name(workspaces, tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    workspaces.create("personal", vault=str(vault))
    with pytest.raises(workspaces.WorkspaceError, match="already exists"):
        workspaces.create("personal", vault=str(vault))


def test_create_writes_a_private_env_file(workspaces, tmp_path):
    """The workspace .env can carry ARTMIND_KG_NEO4J_PASSWORD."""
    vault = tmp_path / "v"
    vault.mkdir()
    result = workspaces.create("personal", vault=str(vault), graph_password="secret")
    mode = (Path(result["run_folder"]) / ".env").stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)


# ── adopt ─────────────────────────────────────────────────────────────────────


def _legacy_run_folder(container: Path) -> Path:
    """A pre-workspace run folder: ARTMIND_ROOT itself, holding .env and friends."""
    (container / "domains" / "schemas").mkdir(parents=True, exist_ok=True)
    (container / "logs").mkdir(exist_ok=True)
    (container / "domains" / "schemas" / "general_schema.yaml").write_text("name: general\n")
    (container / "same_as.yaml").write_text("groups: []\n")
    (container / ".env").write_text(
        "ARTMIND_USER=Someone\n"
        "ARTMIND_OPENROUTER_API_KEY=sk-secret\n"
        "ARTMIND_KG_LLM_PROVIDER=ollama\n"
        "ARTMIND_DATA_DIR=~/artmind_data\n"
        "ARTMIND_VAULT_DIR=~/Projects/corpus\n"
        "ARTMIND_KG_NEO4J_DATABASE=e94695dd\n"
        "ARTMIND_KG_NEO4J_PASSWORD=graphpass\n"
        "SOMETHING_UNKNOWN=1\n"
    )
    return container


def test_adopt_splits_env_by_lifetime(workspaces):
    container = workspaces.ARTMIND_ROOT
    _legacy_run_folder(container)

    result = workspaces.adopt("banking")

    shared = workspaces.parse_env_file(container / "config.env")
    ws_env = workspaces.parse_env_file(Path(result["run_folder"]) / ".env")

    assert shared["ARTMIND_OPENROUTER_API_KEY"] == "sk-secret"
    assert "ARTMIND_KG_NEO4J_PASSWORD" not in shared, "graph creds are workspace-scoped"
    assert ws_env["ARTMIND_KG_NEO4J_DATABASE"] == "e94695dd"
    assert "ARTMIND_OPENROUTER_API_KEY" not in ws_env


def test_adopt_keeps_unknown_keys_in_the_workspace_and_reports_them(workspaces):
    """Promoting an unrecognised key to config.env would leak a
    workspace-specific value into every workspace."""
    _legacy_run_folder(workspaces.ARTMIND_ROOT)

    result = workspaces.adopt("banking")

    assert result["unclassified"] == ["SOMETHING_UNKNOWN"]
    ws_env = workspaces.parse_env_file(Path(result["run_folder"]) / ".env")
    assert ws_env["SOMETHING_UNKNOWN"] == "1"


def test_adopt_leaves_the_original_in_place(workspaces):
    """Non-destructive by construction — a half-migrated run folder is
    indistinguishable from a corrupt one."""
    container = _legacy_run_folder(workspaces.ARTMIND_ROOT)

    workspaces.adopt("banking")

    assert (container / ".env").is_file()
    assert (container / "same_as.yaml").is_file()


def test_adopt_does_not_recurse_into_its_own_destination(workspaces):
    """The legacy run folder IS ARTMIND_ROOT, so it CONTAINS workspaces/. A
    blanket copytree would walk into the directory it is writing to."""
    container = _legacy_run_folder(workspaces.ARTMIND_ROOT)

    result = workspaces.adopt("banking")

    run_folder = Path(result["run_folder"])
    assert not (run_folder / "workspaces").exists()
    assert not (run_folder / "config.env").exists()


def test_adopt_carries_curation_across(workspaces):
    """same_as.yaml is authoritative curation — losing it in a migration means
    redoing human merge adjudication."""
    _legacy_run_folder(workspaces.ARTMIND_ROOT)

    result = workspaces.adopt("banking")

    assert (Path(result["run_folder"]) / "same_as.yaml").read_text() == "groups: []\n"
    assert "same_as.yaml" in result["copied"]


def test_adopt_refuses_a_folder_that_is_not_a_run_folder(workspaces, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(workspaces.WorkspaceError, match="not a run folder"):
        workspaces.adopt("x", empty)


def test_adopt_never_overwrites_an_existing_shared_config(workspaces):
    container = _legacy_run_folder(workspaces.ARTMIND_ROOT)
    (container / "config.env").write_text("ARTMIND_USER=Existing\n")

    result = workspaces.adopt("banking")

    assert workspaces.parse_env_file(container / "config.env")["ARTMIND_USER"] == "Existing"
    assert result["shared_env"] is None
    assert result["shared_env_existed"] is True


def test_adopt_registers_the_graph_and_vault(workspaces):
    _legacy_run_folder(workspaces.ARTMIND_ROOT)

    workspaces.adopt("banking")

    entry = workspaces.get("banking")
    assert entry["graph"]["database"] == "e94695dd"
    assert workspaces.vault_paths(entry) == ["~/Projects/corpus"]


def test_adopt_can_mark_the_workspace_frozen(workspaces):
    """`frozen` is how "keep the banking corpus for later work" becomes a
    machine-checkable property rather than a note in someone's head. Recorded
    here; enforcement in ingest/restore is not wired up yet."""
    _legacy_run_folder(workspaces.ARTMIND_ROOT)

    workspaces.adopt("banking", frozen=True)

    assert workspaces.get("banking")["frozen"] is True
