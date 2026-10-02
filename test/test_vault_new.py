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


# ── run_plan ──────────────────────────────────────────────────────────────────


def test_new_vault_runs_every_step_with_the_right_arguments(world):
    plan = _plan(world)
    vn.preflight(plan)

    vn.run_plan(plan, echo=_quiet)

    assert world.sent("neo4j-manager", "create") == [["neo4j-manager", "create", "demo", "--json", "--wait"]]
    config = (plan.dir / ".artmind" / "config.env").read_text()
    assert "ARTMIND_KG_NEO4J_URI=bolt://127.0.0.1:7688" in config
    assert "ARTMIND_KG_NEO4J_USERNAME=neo4j" in config
    assert "ARTMIND_KG_NEO4J_PASSWORD=pw-123" in config
    assert (plan.dir / ".obsidian").is_dir()
    children = [c[3:] for c in world.sent(sys.executable, "-m", "artmind")]
    assert children == [["setup"], ["vault", "sync", "--bootstrapSynced", "--compact"]]
    assert "initialise vault demo" in _git(plan.dir, "log", "--oneline")
    assert _git(plan.dir, "status", "--porcelain") == ""
    assert world.sent("gh", "repo", "create") == [["gh", "repo", "create", "neurongraph/demo", "--private"]]
    assert _git(plan.dir, "remote", "get-url", "origin").strip() == "https://github.com/neurongraph/demo.git"
    assert world.sent("git", "push") == [["git", "push", "-q", "-u", "origin", "HEAD"]]
    assert world.index("git", "commit") < world.index("gh", "repo", "create") < world.index("git", "push")
    assert world.index("git", "push") < world.index(sys.executable, "-m", "artmind", "vault")
    assert not vn.state_path("demo").exists()


def test_children_run_inside_the_new_vault(world):
    plan = _plan(world)

    vn.run_plan(plan, echo=_quiet)

    for call in world.calls:
        if call["cmd"][:3] == [sys.executable, "-m", "artmind"]:
            assert call["cwd"] == plan.dir
            assert call["env"]["ARTMIND_VAULT"] == str(plan.dir)
            assert "ARTMIND_HOME" not in call["env"]


def test_join_clones_and_rebuilds_the_graph(world):
    world.repos.add("neurongraph/demo")
    plan = _plan(world, join=True)

    vn.run_plan(plan, echo=_quiet)

    assert world.sent("gh", "repo", "clone") == [["gh", "repo", "clone", "neurongraph/demo", str(plan.dir)]]
    assert world.sent("neo4j-manager", "create") == [["neo4j-manager", "create", "demo", "--json", "--wait"]]
    assert world.sent("gh", "repo", "create") == []
    assert world.sent("git", "push") == []
    assert world.sent("git", "commit") == []
    children = [c[3:] for c in world.sent(sys.executable, "-m", "artmind")]
    assert children == [["setup"], ["vault", "sync", "--bootstrapEmpty", "--compact"]]
    assert "ARTMIND_KG_NEO4J_PASSWORD=pw-123" in (plan.dir / ".artmind" / "config.env").read_text()


def test_local_only_commits_but_never_touches_github(world):
    plan = _plan(world, local_only=True)

    vn.run_plan(plan, echo=_quiet)

    assert not (plan.dir / ".obsidian" / "core-plugins.json").exists()  # --noPlugins (the _plan default)

    assert world.sent("gh") == []
    assert world.sent("git", "push") == []
    assert "initialise vault demo" in _git(plan.dir, "log", "--oneline")


def test_a_given_neo4j_connection_skips_neo4j_manager(world):
    plan = _plan(world, local_only=True, neo4j_uri="neo4j+s://x.databases.neo4j.io",
                 neo4j_user="aura", neo4j_password="secret")

    vn.run_plan(plan, echo=_quiet)

    assert world.sent("neo4j-manager") == []
    config = (plan.dir / ".artmind" / "config.env").read_text()
    assert "ARTMIND_KG_NEO4J_URI=neo4j+s://x.databases.neo4j.io" in config
    assert "ARTMIND_KG_NEO4J_USERNAME=aura" in config


def test_plugins_step_installs_the_packaged_plugins(world, monkeypatch, tmp_path):
    plugin_list = tmp_path / "plugins.yaml"
    plugin_list.write_text("- id: obsidian-git\n")
    monkeypatch.setattr(vn.obsidian_community, "PLUGINS_FILE", plugin_list)
    repo = "vinzent03/obsidian-git"
    files = {
        vn.obsidian_community.REGISTRY_URL: json.dumps([{"id": "obsidian-git", "repo": repo}]).encode(),
        f"https://github.com/{repo}/releases/latest/download/main.js": b"js",
        f"https://github.com/{repo}/releases/latest/download/manifest.json": b"{}",
        f"https://github.com/{repo}/releases/latest/download/styles.css": b"css",
    }
    plan = _plan(world, local_only=True, plugins=True)

    messages = []
    vn.run_plan(plan, fetch=files.__getitem__, echo=messages.append)

    assert (plan.dir / ".obsidian" / "plugins" / "obsidian-git" / "main.js").read_bytes() == b"js"
    assert json.loads((plan.dir / ".obsidian" / "core-plugins.json").read_text()) == {"webviewer": True}
    assert "  plugin obsidian-git: downloading..." in messages
    assert messages.index("  plugin obsidian-git: downloading...") < messages.index("  plugin obsidian-git: installed")
    enabled = json.loads((plan.dir / ".obsidian" / "community-plugins.json").read_text())
    assert "obsidian-git" in enabled


def test_open_obsidian_runs_open(world):
    plan = _plan(world, local_only=True, open_obsidian=True)

    vn.run_plan(plan, echo=_quiet)

    assert world.sent("open") == [["open", "-a", "Obsidian"]]


def test_a_failed_step_resumes_without_redoing_finished_steps(world):
    plan = _plan(world)
    world.fail_child.add("setup")

    with pytest.raises(vn.VaultNewError, match="step 'setup' failed") as excinfo:
        vn.run_plan(plan, echo=_quiet)
    assert "boom" in str(excinfo.value)
    assert "re-run" in str(excinfo.value)
    assert vn.state_path("demo").exists()

    world.fail_child.clear()
    assert vn.preflight(plan) is True  # its own folder and instance are not clashes
    vn.run_plan(plan, echo=_quiet)

    assert len(world.sent("neo4j-manager", "create")) == 1
    assert len(world.sent(sys.executable, "-m", "artmind", "setup")) == 2
    assert len(world.sent("gh", "repo", "create")) == 1
    assert not vn.state_path("demo").exists()


def test_a_crash_after_creating_the_repo_resumes_without_a_clash(world):
    plan = _plan(world)
    world.fail_child.add("vault")  # the bookmark step, after publish

    with pytest.raises(vn.VaultNewError, match="step 'bookmark' failed"):
        vn.run_plan(plan, echo=_quiet)

    world.fail_child.clear()
    assert vn.preflight(plan) is True
    vn.run_plan(plan, echo=_quiet)
    assert len(world.sent("gh", "repo", "create")) == 1


def test_resuming_with_different_options_is_refused(world):
    vn.State.begin(_plan(world, local_only=True)).start("neo4j")

    with pytest.raises(vn.VaultNewError, match="used different options"):
        vn.preflight(_plan(world))


# ── child_env ─────────────────────────────────────────────────────────────────


def test_child_env_drops_loaded_config_and_anchors_the_vault(tmp_path, monkeypatch):
    loaded = tmp_path / "config.env"
    loaded.write_text("ARTMIND_KG_NEO4J_URI=bolt://other-vault:7687\n")
    monkeypatch.setattr(vn.paths, "LOADED_ENV_FILES", [loaded])
    monkeypatch.setenv("ARTMIND_KG_NEO4J_URI", "bolt://other-vault:7687")
    monkeypatch.setenv("ARTMIND_HOME", "/somewhere/else")
    monkeypatch.setenv("UNRELATED_SETTING", "kept")

    env = vn.child_env(tmp_path / "vault")

    assert "ARTMIND_KG_NEO4J_URI" not in env
    assert "ARTMIND_HOME" not in env
    assert env["UNRELATED_SETTING"] == "kept"
    assert env["ARTMIND_VAULT"] == str(tmp_path / "vault")
