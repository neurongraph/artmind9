"""`artmind vault new` (spec docs/superpowers/specs/2026-10-02-vault-new-design.md)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from artmind import vault_new as vn


def test_python_dash_m_artmind_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "artmind", "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "vault" in result.stdout


NEO4J = {
    "name": "demo", "container_name": "neo4j-demo", "state": "running",
    "bolt_url": "bolt://127.0.0.1:7688", "http_url": "http://127.0.0.1:7475",
    "bolt_port": 7688, "http_port": 7475, "user": "neo4j", "password": "pw-123",
    "image": "neo4j:latest", "data_dir": "/tmp/neo4j-data/demo/data",
}


def ok(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout, "")


def fail(stderr="error"):
    return subprocess.CompletedProcess([], 1, "", stderr)


class FakeWorld:
    """Stands in for gh, neo4j-manager, `open` and the artmind child process,
    with GitHub repos and neo4j instances as state. Runs git for real (inside
    tmp dirs) except `git push`. Records every call so tests can assert on
    exactly what was sent."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.calls: list[dict] = []
        self.repos: set[str] = set()
        self.instances: set[str] = set()
        self.login = "neurongraph"
        self.fail_child: set[str] = set()

    def __call__(self, cmd, *, cwd=None, env=None):
        cmd = [str(c) for c in cmd]
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env})
        if cmd[0] == "git":
            if cmd[1] == "push":
                return ok()
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
        if cmd[:3] == ["gh", "api", "user"]:
            return ok(self.login + "\n") if self.login else fail("not logged in")
        if cmd[:3] == ["gh", "repo", "view"]:
            return ok("{}") if cmd[3] in self.repos else fail("Could not resolve to a Repository")
        if cmd[:3] == ["gh", "repo", "create"]:
            self.repos.add(cmd[3])
            return ok()
        if cmd[:3] == ["gh", "repo", "clone"]:
            return self._clone(Path(cmd[4]))
        if cmd[:2] == ["neo4j-manager", "status"]:
            if cmd[2] in self.instances:
                return ok(json.dumps({**NEO4J, "name": cmd[2]}))
            return fail('{"error": "no such instance"}')
        if cmd[:2] == ["neo4j-manager", "create"]:
            self.instances.add(cmd[2])
            return ok(json.dumps({**NEO4J, "name": cmd[2]}))
        if cmd[:3] == [sys.executable, "-m", "artmind"]:
            return fail("boom") if cmd[3] in self.fail_child else ok("{}")
        if cmd[0] == "open":
            return ok()
        raise AssertionError(f"unexpected command: {cmd}")

    def _clone(self, dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "laptop A's history"]):
            subprocess.run(["git", *args], cwd=dest, check=True, capture_output=True)
        return ok()

    def sent(self, *prefix) -> list[list[str]]:
        return [c["cmd"] for c in self.calls if c["cmd"][: len(prefix)] == list(prefix)]

    def index(self, *prefix) -> int:
        return next(i for i, c in enumerate(self.calls) if c["cmd"][: len(prefix)] == list(prefix))


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeWorld(tmp_path)
    monkeypatch.setattr(vn, "RUN", fake)
    monkeypatch.setattr(vn, "WHICH", lambda tool: f"/usr/local/bin/{tool}")
    monkeypatch.setattr(vn, "STATE_DIR", tmp_path / "state")
    machine = tmp_path / "machine.env"
    machine.write_text("ARTMIND_KG_LLM_PROVIDER=ollama\n")
    monkeypatch.setattr(vn, "MACHINE_CONFIG_ENV", machine)
    for key in ("ARTMIND_KG_LLM_PROVIDER", "ARTMIND_OPENROUTER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Test User\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    return fake


def _plan(world, **kwargs):
    kwargs.setdefault("directory", str(world.tmp / "vaults" / "demo"))
    kwargs.setdefault("plugins", False)
    kwargs.setdefault("open_obsidian", False)
    return vn.build_plan("demo", **kwargs)


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout


def _quiet(*_):
    pass


# ── build_plan ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["", "my vault", "../x", ".hidden", "a" * 101, "x/y"])
def test_invalid_names_are_rejected_not_altered(world, name):
    with pytest.raises(vn.VaultNewError, match="not a valid vault name"):
        vn.build_plan(name, local_only=True)


def test_owner_is_the_active_gh_account(world):
    plan = _plan(world)

    assert plan.repo == "neurongraph/demo"
    assert plan.remote_url == "https://github.com/neurongraph/demo.git"
    assert world.sent("gh", "api", "user") == [["gh", "api", "user", "--jq", ".login"]]


def test_github_owner_overrides_the_gh_account(world):
    plan = _plan(world, github_owner="some-org")

    assert plan.repo == "some-org/demo"
    assert world.sent("gh", "api", "user") == []


def test_not_logged_in_to_gh_explains(world):
    world.login = ""

    with pytest.raises(vn.VaultNewError, match="gh auth login"):
        _plan(world)


def test_local_only_has_no_repo(world):
    plan = _plan(world, local_only=True)

    assert plan.owner is None and plan.repo is None
    assert world.calls == []


def test_join_and_local_only_conflict(world):
    with pytest.raises(vn.VaultNewError, match="--join"):
        _plan(world, join=True, local_only=True)


def test_neo4j_flags_go_together(world):
    with pytest.raises(vn.VaultNewError, match="go together"):
        _plan(world, neo4j_uri="neo4j+s://x.databases.neo4j.io")


def test_default_dir_is_under_artmind_vaults(world):
    plan = vn.build_plan("demo", local_only=True)

    assert plan.dir == (Path.home() / "artmind_vaults" / "demo").resolve()


# ── preflight ─────────────────────────────────────────────────────────────────


def test_preflight_passes_on_a_clean_machine(world):
    assert vn.preflight(_plan(world)) is False


def test_preflight_reports_every_clash_at_once(world):
    plan = _plan(world)
    world.repos.add("neurongraph/demo")
    world.instances.add("demo")
    plan.dir.mkdir(parents=True)
    (plan.dir / "notes.md").write_text("mine")

    with pytest.raises(vn.VaultNewError) as excinfo:
        vn.preflight(plan)

    message = str(excinfo.value)
    assert "GitHub repo neurongraph/demo already exists" in message
    assert "--join" in message
    assert f"{plan.dir} already exists and is not empty" in message
    assert "neo4j-manager instance 'demo' already exists" in message
    assert "neo4j-manager remove demo" in message


def test_preflight_join_needs_the_repo(world):
    with pytest.raises(vn.VaultNewError, match="does not exist — nothing to join"):
        vn.preflight(_plan(world, join=True))


def test_preflight_join_accepts_an_existing_repo(world):
    world.repos.add("neurongraph/demo")

    assert vn.preflight(_plan(world, join=True)) is False


def test_preflight_reports_missing_tools(world, monkeypatch):
    monkeypatch.setattr(vn, "WHICH", lambda tool: None if tool == "neo4j-manager" else f"/bin/{tool}")

    with pytest.raises(vn.VaultNewError, match="not on PATH: neo4j-manager"):
        vn.preflight(_plan(world))


def test_preflight_needs_the_machine_config(world, monkeypatch):
    monkeypatch.setattr(vn, "MACHINE_CONFIG_ENV", world.tmp / "absent.env")

    with pytest.raises(vn.VaultNewError, match="bootstrap.sh"):
        vn.preflight(_plan(world))


def test_preflight_needs_the_providers_credential(world):
    vn.MACHINE_CONFIG_ENV.write_text("ARTMIND_KG_LLM_PROVIDER=openrouter\nARTMIND_OPENROUTER_API_KEY=\n")

    with pytest.raises(vn.VaultNewError, match="needs ARTMIND_OPENROUTER_API_KEY"):
        vn.preflight(_plan(world))

    vn.MACHINE_CONFIG_ENV.write_text("ARTMIND_KG_LLM_PROVIDER=openrouter\nARTMIND_OPENROUTER_API_KEY=sk-1\n")
    assert vn.preflight(_plan(world)) is False


def test_preflight_needs_a_git_identity_for_a_new_vault(world, monkeypatch):
    empty = world.tmp / "empty-gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))

    with pytest.raises(vn.VaultNewError, match="git user.name is not set"):
        vn.preflight(_plan(world))


def test_describe_summarises_what_will_be_created(world):
    text = vn.describe(_plan(world), resuming=False)

    assert text.splitlines()[0] == "Create vault demo:"
    assert "neo4j    demo  (local instance, via neo4j-manager)" in text
    assert "github   neurongraph/demo  (new, private)" in text
