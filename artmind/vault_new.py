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
