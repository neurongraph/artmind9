"""`artmind vault new`: a ready-to-open vault in one command (spec
docs/superpowers/specs/2026-10-02-vault-new-design.md).

A new vault gets a neo4j-manager instance, a git repo, the artmind scaffold,
the community plugins, the graph schema, a bootstrap commit pushed to a new
private GitHub repo, and a first sync bookmark. `--join` does the same for a
second machine, but clones the existing repo and rebuilds this machine's graph
from its commits.

Every external command goes through `RUN` and every download through `FETCH`
-- module globals the tests replace. Each step is idempotent and recorded in a
machine-level state file, so re-running a failed `vault new` resumes it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import dotenv_values

import paths
from artmind import obsidian_community


class VaultNewError(Exception):
    """A pre-flight problem or a failed step. The message is for the user."""


def _subprocess_run(cmd, *, cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


RUN: Callable[..., subprocess.CompletedProcess] = _subprocess_run
FETCH: Callable[[str], bytes] = obsidian_community.default_fetch
WHICH: Callable[[str], str | None] = shutil.which
STATE_DIR = paths.MACHINE_CONFIG_DIR / "vault_new"
MACHINE_CONFIG_ENV = paths.MACHINE_CONFIG_ENV

# The vault name is also the folder, GitHub repo and neo4j-manager instance
# name, so it must be valid as all of them -- and it is never altered, so the
# four always agree.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

# What each LLM provider cannot run without (artmind/llm_providers.py,
# artmind/extraction.py). Embeddings are always ollama and need no key.
PROVIDER_CREDENTIALS = {
    "ollama": (),
    "openrouter": ("ARTMIND_OPENROUTER_API_KEY",),
    "ibm_ica": ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"),
}


@dataclass(frozen=True)
class Plan:
    name: str
    dir: Path
    join: bool = False
    owner: str | None = None  # None: --localOnly, no GitHub at all
    neo4j_uri: str | None = None  # None: provision through neo4j-manager
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    neo4j_database: str = "neo4j"
    plugins: bool = True
    open_obsidian: bool = True

    @property
    def repo(self) -> str | None:
        return f"{self.owner}/{self.name}" if self.owner else None

    @property
    def remote_url(self) -> str | None:
        return f"https://github.com/{self.repo}.git" if self.owner else None

    @property
    def uses_neo4j_manager(self) -> bool:
        return self.neo4j_uri is None

    def key(self) -> dict:
        """What a resumed run must agree on. No secrets."""
        return {
            "dir": str(self.dir),
            "join": self.join,
            "owner": self.owner,
            "neo4j_manager": self.uses_neo4j_manager,
        }


def build_plan(
    name: str,
    *,
    join: bool = False,
    directory: str | None = None,
    github_owner: str | None = None,
    local_only: bool = False,
    neo4j_uri: str | None = None,
    neo4j_user: str | None = None,
    neo4j_password: str | None = None,
    neo4j_database: str = "neo4j",
    plugins: bool = True,
    open_obsidian: bool = True,
    run=None,
) -> Plan:
    if not NAME_RE.fullmatch(name):
        raise VaultNewError(
            f"{name!r} is not a valid vault name: use letters, digits, '.', '_' and '-', "
            "starting with a letter or digit, at most 100 characters. It is also the "
            "GitHub repo and neo4j-manager instance name, so it is never altered."
        )
    if join and local_only:
        raise VaultNewError("--join clones the vault's GitHub repo, so it cannot be combined with --localOnly.")
    connection = (neo4j_uri, neo4j_user, neo4j_password)
    if any(connection) and not all(connection):
        raise VaultNewError("--neo4jUri, --neo4jUser and --neo4jPassword go together.")
    owner = None if local_only else (github_owner or resolve_owner(run))
    vault_dir = Path(directory).expanduser() if directory else Path.home() / "artmind_vaults" / name
    return Plan(
        name=name, dir=vault_dir.resolve(), join=join, owner=owner,
        neo4j_uri=neo4j_uri, neo4j_user=neo4j_user, neo4j_password=neo4j_password,
        neo4j_database=neo4j_database, plugins=plugins, open_obsidian=open_obsidian,
    )


def resolve_owner(run=None) -> str:
    """The active gh account's login: the default owner of the vault's repo."""
    run = run or RUN
    if WHICH("gh") is None:
        raise VaultNewError("gh is not installed: run scripts/bootstrap.sh, or pass --localOnly.")
    result = run(["gh", "api", "user", "--jq", ".login"])
    login = result.stdout.strip()
    if result.returncode != 0 or not login:
        raise VaultNewError("gh is not logged in: run `gh auth login`, or pass --localOnly.")
    return login


def state_path(name: str) -> Path:
    return STATE_DIR / f"{name}.json"


class State:
    """Progress of one `vault new NAME` (spec §2, "Resume").

    Lives in the machine config dir, outside the vault, so it exists before the
    vault folder does and needs no gitignore entry. A step is recorded as
    started before it runs and done after, so whatever a crashed step created
    is still attributed to this run when it is resumed.
    """

    def __init__(self, path: Path, data: dict):
        self.path, self.data = path, data

    @classmethod
    def load(cls, name: str) -> "State | None":
        path = state_path(name)
        if not path.is_file():
            return None
        return cls(path, json.loads(path.read_text(encoding="utf-8")))

    @classmethod
    def begin(cls, plan: Plan) -> "State":
        return cls.load(plan.name) or cls(
            state_path(plan.name), {"plan": plan.key(), "started": [], "done": []}
        )

    def started(self, step: str) -> bool:
        return step in self.data["started"]

    def done(self, step: str) -> bool:
        return step in self.data["done"]

    def start(self, step: str) -> None:
        if step not in self.data["started"]:
            self.data["started"].append(step)
        self._save()

    def finish(self, step: str) -> None:
        self.data["done"].append(step)
        self._save()

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


def machine_config_problems() -> list[str]:
    """The shared LLM config `bootstrap.sh` seeds and the user then edits."""
    if not MACHINE_CONFIG_ENV.is_file():
        return [
            f"{MACHINE_CONFIG_ENV} does not exist — run scripts/bootstrap.sh, then edit it "
            "(LLM provider, model, API key)."
        ]
    values = {**dotenv_values(MACHINE_CONFIG_ENV), **os.environ}
    provider = (values.get("ARTMIND_KG_LLM_PROVIDER") or "ollama").strip()
    if provider not in PROVIDER_CREDENTIALS:
        return [
            f"ARTMIND_KG_LLM_PROVIDER={provider!r} in {MACHINE_CONFIG_ENV} is not one of "
            f"{', '.join(PROVIDER_CREDENTIALS)}."
        ]
    return [
        f"ARTMIND_KG_LLM_PROVIDER={provider} needs {key}, which is empty — set it in {MACHINE_CONFIG_ENV}."
        for key in PROVIDER_CREDENTIALS[provider]
        if not (values.get(key) or "").strip()
    ]


def preflight(plan: Plan, run=None) -> bool:
    """Check everything before anything is created, and raise one VaultNewError
    listing every problem. Returns True when resuming an unfinished run.

    A folder, neo4j instance or repo that an unfinished run of this same
    `vault new` created is resumable, not a clash.
    """
    run = run or RUN
    problems: list[str] = []
    state = State.load(plan.name)
    if state is not None and state.data["plan"] != plan.key():
        problems.append(
            f"an unfinished `artmind vault new {plan.name}` used different options "
            f"({state.data['plan']}) — re-run it with those, or delete {state.path} to start over."
        )
        state = None

    def ours(step: str) -> bool:
        return state is not None and state.started(step)

    problems += machine_config_problems()

    tools = ["git"]
    if plan.owner:
        tools.append("gh")
    if plan.uses_neo4j_manager:
        tools.append("neo4j-manager")
    missing = [tool for tool in tools if WHICH(tool) is None]
    if missing:
        problems.append(f"not on PATH: {', '.join(missing)} — run scripts/bootstrap.sh.")

    if not plan.join and "git" not in missing:
        for key in ("user.name", "user.email"):
            if not run(["git", "config", "--get", key]).stdout.strip():
                problems.append(
                    f"git {key} is not set (the bootstrap commit needs it): git config --global {key} <value>"
                )

    if plan.owner and "gh" not in missing:
        exists = run(["gh", "repo", "view", plan.repo, "--json", "name"]).returncode == 0
        if plan.join and not exists:
            problems.append(f"GitHub repo {plan.repo} does not exist — nothing to join. Drop --join to create it.")
        elif not plan.join and exists and not ours("publish"):
            problems.append(
                f"GitHub repo {plan.repo} already exists — pass --join to clone it, or choose another name."
            )

    if plan.dir.exists() and not ours("folder"):
        if not plan.dir.is_dir() or any(plan.dir.iterdir()):
            problems.append(f"{plan.dir} already exists and is not empty — choose another name or --dir.")

    if plan.uses_neo4j_manager and "neo4j-manager" not in missing and not ours("neo4j"):
        if run(["neo4j-manager", "status", plan.name, "--json"]).returncode == 0:
            problems.append(
                f"neo4j-manager instance {plan.name!r} already exists and may hold another vault's "
                f"data — `neo4j-manager remove {plan.name}`, or choose another name."
            )

    if problems:
        raise VaultNewError("Cannot create the vault:\n" + "\n".join(f"  - {p}" for p in problems))
    return state is not None


def describe(plan: Plan, resuming: bool) -> str:
    verb = "Resume" if resuming else ("Join" if plan.join else "Create")
    if plan.uses_neo4j_manager:
        neo4j = f"{plan.name}  (local instance, via neo4j-manager)"
    else:
        neo4j = f"{plan.neo4j_uri}  (given)"
    if plan.owner is None:
        github = "none  (--localOnly)"
    elif plan.join:
        github = f"{plan.repo}  (existing, cloned)"
    else:
        github = f"{plan.repo}  (new, private)"
    return "\n".join([
        f"{verb} vault {plan.name}:",
        f"  folder   {plan.dir}",
        f"  neo4j    {neo4j}",
        f"  github   {github}",
    ])


# ── steps ─────────────────────────────────────────────────────────────────────

# Keys that pick the vault or run folder. A child must find the new vault, not
# inherit the parent's.
_ANCHOR_KEYS = ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT", "ARTMIND_VAULT_DIR")


@dataclass
class _Ctx:
    plan: Plan
    run: Callable[..., subprocess.CompletedProcess]
    fetch: Callable[[str], bytes]
    echo: Callable[[str], None]
    neo4j: dict | None = None  # neo4j-manager's JSON for this vault's instance


def _check(result: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        raise VaultNewError(f"{what} failed (exit {result.returncode}): " + " | ".join(detail))
    return result


def _git(ctx: _Ctx, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = ctx.run(["git", *args], cwd=ctx.plan.dir)
    return _check(result, f"git {args[0]}") if check else result


def child_env(vault_dir: Path) -> dict:
    """Environment for an `artmind` child anchored at `vault_dir`.

    `paths.py` loads config files with `load_dotenv(override=False)`, so a key
    this process loaded -- the Neo4j URI of whatever vault `vault new` was run
    from -- would beat the new vault's own config.env in the child. Drop every
    key that came from a loaded file, plus the anchor keys, and point
    ARTMIND_VAULT at the new vault.
    """
    loaded: set[str] = set()
    for env_file in paths.LOADED_ENV_FILES:
        loaded.update(dotenv_values(env_file))
    env = {k: v for k, v in os.environ.items() if k not in loaded and k not in _ANCHOR_KEYS}
    env["ARTMIND_VAULT"] = str(vault_dir)
    return env


def _artmind(ctx: _Ctx, *args: str) -> subprocess.CompletedProcess:
    """Run `artmind ARGS` inside the new vault: a child process, because
    `paths.py` resolves the active vault once, at import."""
    cmd = [sys.executable, "-m", "artmind", *args]
    result = ctx.run(cmd, cwd=ctx.plan.dir, env=child_env(ctx.plan.dir))
    return _check(result, f"artmind {' '.join(args)}")


def _neo4j_connection(ctx: _Ctx) -> dict:
    """`config_answers` for scaffold_vault, from the flags or neo4j-manager."""
    plan = ctx.plan
    if not plan.uses_neo4j_manager:
        return {
            "neo4j_uri": plan.neo4j_uri, "neo4j_username": plan.neo4j_user,
            "neo4j_password": plan.neo4j_password, "neo4j_database": plan.neo4j_database,
        }
    if ctx.neo4j is None:  # resumed after the neo4j step: ask again
        status = _check(ctx.run(["neo4j-manager", "status", plan.name, "--json"]), "neo4j-manager status")
        ctx.neo4j = json.loads(status.stdout)
    return {
        "neo4j_uri": ctx.neo4j["bolt_url"], "neo4j_username": ctx.neo4j["user"],
        "neo4j_password": ctx.neo4j["password"], "neo4j_database": plan.neo4j_database,
    }


def _step_neo4j(ctx: _Ctx) -> None:
    plan = ctx.plan
    if not plan.uses_neo4j_manager:
        ctx.echo(f"  neo4j: using {plan.neo4j_uri}")
        return
    status = ctx.run(["neo4j-manager", "status", plan.name, "--json"])
    if status.returncode == 0:  # created by the unfinished run being resumed
        ctx.neo4j = json.loads(status.stdout)
        if ctx.neo4j.get("state") != "running":
            _check(ctx.run(["neo4j-manager", "start", plan.name]), "neo4j-manager start")
        ctx.echo(f"  neo4j: reusing instance {plan.name} at {ctx.neo4j['bolt_url']}")
        return
    created = _check(ctx.run(["neo4j-manager", "create", plan.name, "--json", "--wait"]), "neo4j-manager create")
    ctx.neo4j = json.loads(created.stdout)
    ctx.echo(f"  neo4j: created instance {plan.name} at {ctx.neo4j['bolt_url']}")


def _step_folder(ctx: _Ctx) -> None:
    plan = ctx.plan
    if plan.join:
        if not (plan.dir / ".git").exists():
            _check(ctx.run(["gh", "repo", "clone", plan.repo, str(plan.dir)]), "gh repo clone")
    else:
        plan.dir.mkdir(parents=True, exist_ok=True)
        if not (plan.dir / ".git").exists():
            _git(ctx, "init", "-q")
    # Before scaffold_vault, so it installs and enables the artmind plugin
    # (install_obsidian_plugin does nothing without .obsidian/).
    (plan.dir / ".obsidian").mkdir(exist_ok=True)
    ctx.echo(f"  folder: {plan.dir}")


def _step_scaffold(ctx: _Ctx) -> None:
    from artmind.setup import scaffold_vault

    summary = scaffold_vault(ctx.plan.dir, config_answers=_neo4j_connection(ctx))
    plugin = (summary.get("obsidian_plugin") or {}).get("status")
    ctx.echo(f"  scaffold: .artmind/ written (artmind plugin: {plugin})")


def _step_plugins(ctx: _Ctx) -> None:
    if not ctx.plan.plugins:
        ctx.echo("  plugins: skipped (--noPlugins)")
        return
    results = obsidian_community.install_community_plugins(ctx.plan.dir / ".obsidian", fetch=ctx.fetch)
    for r in results:
        ctx.echo(f"  plugin {r['id']}: {r['status']}" + (f" -- {r['error']}" if r["error"] else ""))


def _step_setup(ctx: _Ctx) -> None:
    _artmind(ctx, "setup")
    ctx.echo("  setup: graph constraints and indexes created")


def _step_publish(ctx: _Ctx) -> None:
    plan = ctx.plan
    if plan.join:
        return
    has_head = _git(ctx, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0
    dirty = bool(_git(ctx, "status", "--porcelain", check=False).stdout.strip())
    if not has_head or dirty:
        _git(ctx, "add", "-A")
        _git(ctx, "commit", "-q", "-m", f"artmind: initialise vault {plan.name}")
    if plan.owner is None:
        ctx.echo("  publish: committed locally (--localOnly)")
        return
    if ctx.run(["gh", "repo", "view", plan.repo, "--json", "name"]).returncode != 0:
        _check(ctx.run(["gh", "repo", "create", plan.repo, "--private"]), "gh repo create")
    if _git(ctx, "remote", "get-url", "origin", check=False).returncode != 0:
        _git(ctx, "remote", "add", "origin", plan.remote_url)
    _git(ctx, "push", "-q", "-u", "origin", "HEAD")
    ctx.echo(f"  publish: pushed to {plan.remote_url} (private)")


def _step_bookmark(ctx: _Ctx) -> None:
    # New: an empty graph and a one-commit vault are already in sync.
    # Join: this machine's graph is empty, so replay everything committed.
    flag = "--bootstrapEmpty" if ctx.plan.join else "--bootstrapSynced"
    _artmind(ctx, "vault", "sync", flag, "--compact")
    ctx.echo(f"  bookmark: vault sync {flag}")


def _handoff(ctx: _Ctx) -> None:
    plan = ctx.plan
    if plan.open_obsidian and ctx.run(["open", "-a", "Obsidian"]).returncode != 0:
        ctx.echo("  Obsidian did not open -- is it installed? (brew install --cask obsidian)")
    ctx.echo(
        f"\nVault {plan.name} is ready. In Obsidian:\n"
        f"  1. Open folder as vault -> {plan.dir}\n"
        f"  2. Turn on community plugins when asked -- artmind and the community\n"
        f"     plugins are already installed and enabled."
    )


STEPS: dict[str, Callable[[_Ctx], None]] = {
    "neo4j": _step_neo4j,
    "folder": _step_folder,
    "scaffold": _step_scaffold,
    "plugins": _step_plugins,
    "setup": _step_setup,
    "publish": _step_publish,
    "bookmark": _step_bookmark,
}


def run_plan(plan: Plan, *, run=None, fetch=None, echo: Callable[[str], None] = print) -> None:
    """Run every step not already done by an earlier run of this `vault new`."""
    ctx = _Ctx(plan=plan, run=run or RUN, fetch=fetch or FETCH, echo=echo)
    state = State.begin(plan)
    for name, step in STEPS.items():
        if state.done(name):
            echo(f"  {name}: done in an earlier run")
            continue
        state.start(name)
        try:
            step(ctx)
        except (VaultNewError, OSError, ValueError, KeyError) as e:
            raise VaultNewError(
                f"step '{name}' failed: {e}\n"
                f"Fix the cause and re-run the same `artmind vault new {plan.name} ...` command: "
                "finished steps are skipped."
            ) from e
        state.finish(name)
    _handoff(ctx)
    state.clear()
