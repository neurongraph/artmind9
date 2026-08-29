"""The workspace registry (docs/workspaces.md).

A **workspace** is one knowledge base and everything scoped to it — its vault,
derived data, archive, graph, curation and logs. Exactly one is active per
process; which one is decided by ``paths._resolve_run_folder`` before this
module is ever imported.

Resolution deliberately does NOT live here. It has to run inside ``paths.py``
(imported by everything, at import time) and be reproducible by
``artmind/_entry.py`` (stdlib-only). This module holds the richer operations
that can afford yaml and an artmind import: reading and writing the registry,
describing the active workspace, and switching the pointer.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.request
from fnmatch import fnmatch
from pathlib import Path

import yaml

from paths import (
    ARTMIND_ROOT,
    LOADED_ENV_FILES,
    SHARED_ENV_FILE,
    WORKSPACE_POINTER,
    WORKSPACE_REGISTRY,
    WORKSPACES_DIR,
    ARTMIND_ARCHIVE_DIR,
    ARTMIND_DATA_DIR,
    ARTMIND_HOME,
    ARTMIND_VAULT_DIR,
    ARTMIND_WORKSPACE,
    valid_workspace_name,
    workspace_fingerprint,
)

REGISTRY_VERSION = 1


class WorkspaceError(Exception):
    """A workspace could not be resolved, registered, or switched to."""


# ── registry I/O ──────────────────────────────────────────────────────────────


def load_registry() -> dict:
    """The registry, or an empty one. A missing file is the normal state for an
    install that has never adopted a workspace — never an error."""
    try:
        raw = WORKSPACE_REGISTRY.read_text(encoding="utf-8")
    except OSError:
        return {"version": REGISTRY_VERSION, "workspaces": {}}
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise WorkspaceError(f"{WORKSPACE_REGISTRY} is not a YAML mapping")
    data.setdefault("version", REGISTRY_VERSION)
    data.setdefault("workspaces", {})
    if not isinstance(data["workspaces"], dict):
        raise WorkspaceError(f"{WORKSPACE_REGISTRY}: 'workspaces' must be a mapping")
    return data


def save_registry(registry: dict) -> None:
    WORKSPACE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_REGISTRY.write_text(
        yaml.safe_dump(registry, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def get(name: str) -> dict | None:
    return load_registry()["workspaces"].get(name)


def names() -> list[str]:
    return sorted(load_registry()["workspaces"])


def vault_paths(entry: dict) -> list[str]:
    """A workspace's vault paths.

    ``vaults`` is a LIST from the first commit even though exactly one entry is
    supported (docs/workspaces.md, "Toward many vaults") — so that many-vault
    support is a resolver change rather than a registry migration.
    """
    return [v.get("path", "") for v in entry.get("vaults", []) if v.get("path")]


def register(
    name: str,
    *,
    vault: str | None,
    data_dir: str,
    archive_dir: str,
    graph: dict,
    ports: dict | None = None,
    schemas: list[str] | None = None,
    frozen: bool = False,
) -> dict:
    """Add or replace a registry entry. Refuses a vault another workspace has
    already claimed — two workspaces sharing a vault would write conflicting
    identity rows against the same path keys (docs/workspaces.md, guardrail 4).
    """
    if not valid_workspace_name(name):
        raise WorkspaceError(
            f"Invalid workspace name {name!r}. Names are alphanumeric with . _ - "
            "and are used directly as directory names."
        )

    registry = load_registry()
    if vault:
        claimed = Path(vault).expanduser().resolve()
        for other, entry in registry["workspaces"].items():
            if other == name:
                continue
            for path in vault_paths(entry):
                if Path(path).expanduser().resolve() == claimed:
                    raise WorkspaceError(
                        f"Vault {vault} is already claimed by workspace {other!r}. "
                        "Two workspaces sharing a vault corrupt the path-keyed "
                        "document registry."
                    )

    entry = {
        "vaults": [{"name": "main", "path": vault}] if vault else [],
        "data_dir": data_dir,
        "archive_dir": archive_dir,
        "graph": dict(graph),
        "ports": ports or {},
        "schemas": schemas or [],
        "frozen": frozen,
    }
    registry["workspaces"][name] = entry
    save_registry(registry)
    return entry


# ── the pointer ───────────────────────────────────────────────────────────────


def current_name() -> str | None:
    """The name in the pointer file, if any. Not necessarily the ACTIVE
    workspace — ``ARTMIND_HOME`` and ``ARTMIND_WORKSPACE`` both outrank it."""
    try:
        name = WORKSPACE_POINTER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def set_current(name: str) -> None:
    if not valid_workspace_name(name):
        raise WorkspaceError(f"Invalid workspace name {name!r}")
    if get(name) is None:
        raise WorkspaceError(
            f"No workspace named {name!r}. `artmind workspace list` shows what exists."
        )
    WORKSPACE_POINTER.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACE_POINTER.write_text(f"{name}\n", encoding="utf-8")


# ── describing the active workspace ───────────────────────────────────────────


def _daemon_health(timeout: float = 0.25) -> dict | None:
    port = os.environ.get("ARTMIND_SERVE_PORT", "8377")
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=timeout
        ) as resp:
            body = json.loads(resp.read())
    except Exception:
        return None
    return body if body.get("service") == "artmind" else None


def active_name() -> str:
    """A display name for the active workspace.

    ``ARTMIND_HOME`` bypasses the registry, so a run folder reached that way has
    no name to report — saying so beats guessing one from the directory.
    """
    if ARTMIND_WORKSPACE:
        return ARTMIND_WORKSPACE
    if os.environ.get("ARTMIND_HOME"):
        return "(ARTMIND_HOME override)"
    return "(pre-workspace layout)"


def describe() -> dict:
    """Everything `artmind workspace` prints.

    Never includes ``ARTMIND_KG_NEO4J_PASSWORD`` or any other credential: this
    payload is printed, logged, and pasted into issues.
    """
    from artmind import vault_git

    name = active_name()
    fingerprint = workspace_fingerprint()
    health = _daemon_health()
    entry = get(ARTMIND_WORKSPACE) if ARTMIND_WORKSPACE else None

    daemon: dict = {"running": health is not None}
    if health is not None:
        served = health.get("workspace_fingerprint")
        daemon["fingerprint"] = served
        # A daemon with no fingerprint at all predates guardrail 2 — report it
        # as a mismatch rather than assuming agreement.
        daemon["matches"] = served == fingerprint
        daemon["workspace"] = health.get("workspace")

    return {
        "workspace": name,
        "registered": entry is not None,
        "frozen": bool(entry.get("frozen")) if entry else False,
        "run_folder": str(ARTMIND_HOME),
        "env_files": [str(p) for p in LOADED_ENV_FILES],
        "shared_env": str(SHARED_ENV_FILE) if SHARED_ENV_FILE.is_file() else None,
        "registry": str(WORKSPACE_REGISTRY) if WORKSPACE_REGISTRY.is_file() else None,
        "root": str(ARTMIND_ROOT),
        "data_dir": str(ARTMIND_DATA_DIR),
        "vault_dir": str(ARTMIND_VAULT_DIR) if ARTMIND_VAULT_DIR else None,
        "archive_dir": str(ARTMIND_ARCHIVE_DIR),
        "graph": {
            "uri": os.environ.get("ARTMIND_KG_NEO4J_URI", ""),
            "database": os.environ.get("ARTMIND_KG_NEO4J_DATABASE", ""),
        },
        "vault_git": {
            "head": vault_git.current_commit(),
            "dirty": vault_git.is_dirty(),
        },
        "fingerprint": fingerprint,
        "daemon": daemon,
        "workspaces_dir": str(WORKSPACES_DIR),
    }


# ── creating and adopting ─────────────────────────────────────────────────────

# Which schemas a NEW workspace starts with. The `banking.*` family is a demo
# corpus's schemas, not a default every knowledge base should inherit: seeding
# them everywhere puts domains with no data in front of `domains-overview` and
# the chat agent's routing (docs/workspaces.md, guardrail 3). They stay shipped
# and are one `artmind domains add` away.
STARTER_SCHEMAS = ("general", "personal_journal")

# docs/workspaces.md, "Config classification". Anything absent from both lists
# is left in the workspace .env and reported, rather than guessed at.
IDENTITY_KEYS = frozenset({
    "ARTMIND_USER",
    "ARTMIND_KG_LLM_PROVIDER", "ARTMIND_KG_LLM_MODEL", "ARTMIND_KG_LLM_URL",
    "ARTMIND_IMAGE_MODEL", "ARTMIND_OLLAMA_TIMEOUT",
    "ARTMIND_OPENROUTER_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "ARTMIND_KG_EMBEDDINGS_PROVIDER", "ARTMIND_KG_EMBEDDINGS_URL",
    "ARTMIND_KG_EMBEDDINGS_MODEL",
    # Follows from the embedding model, so it is identity — but it is also baked
    # into the Neo4j vector indexes at `artmind setup` (setup.py), so a shared
    # value against a graph built at another dimension degrades vector search
    # silently. `workspace use` should validate it against the live index; that
    # needs a graph connection and is not done here yet.
    "ARTMIND_KG_EMBEDDING_DIMENSIONS",
    "ARTMIND_SDK_MODEL", "ARTMIND_SDK_FALLBACK_MODEL", "ARTMIND_SDK_BASE_URL",
    "ARTMIND_ACP_MODEL",
    "ARTMIND_KG_CHUNK_SIZE", "ARTMIND_INGEST_MAX_WORKERS",
})

WORKSPACE_KEYS = frozenset({
    "ARTMIND_DATA_DIR", "ARTMIND_VAULT_DIR", "ARTMIND_ARCHIVE_DIR",
    "ARTMIND_KG_NEO4J_URI", "ARTMIND_KG_NEO4J_USERNAME",
    "ARTMIND_KG_NEO4J_PASSWORD", "ARTMIND_KG_NEO4J_DATABASE",
    "ARTMIND_VAULT_GIT_PUSH",
})

# What `adopt` carries across from a pre-workspace run folder. An allowlist, not
# a blanket copytree — the legacy run folder IS `ARTMIND_ROOT`, so it contains
# the `workspaces/` directory we are copying into, and a recursive copy would
# walk into its own destination.
ADOPT_ENTRIES = (".env", "same_as.yaml", "domains", ".claude", ".opencode", "logs")


def parse_env_file(path: Path) -> dict[str, str]:
    """Key/value pairs from a .env file. Comments and blanks dropped; this is
    for classifying keys, never for round-tripping a file."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if name:
            values[name] = value.strip().strip("\"'")
    return values


def _render_env(values: dict[str, str], header: str) -> str:
    body = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    return f"# {header}\n{body}\n"


def scaffold(run_folder: Path, schemas: "list[str] | None" = None) -> dict:
    """Create a run folder and seed package assets into it.

    Deliberately not `setup.scaffold_run_folder()`: that one resolves every
    destination from `paths`' import-time constants, so it can only ever seed
    the run folder THIS process resolved to. Creating a different one needs the
    destination passed in.
    """
    from paths import (
        PACKAGE_META_YAML, PACKAGE_OPENCODE_DIR, PACKAGE_SCHEMAS_DIR,
        PACKAGE_SKILLS_DIR,
    )
    from artmind.setup import _seed_tree

    wanted = list(schemas) if schemas else list(STARTER_SCHEMAS)
    if "general" not in wanted:
        # cli._get_available_domains always offers `general`; a run folder
        # without its schema would advertise a domain it cannot extract.
        wanted.append("general")

    schemas_dir = run_folder / "domains" / "schemas"
    for directory in (run_folder, run_folder / "logs", schemas_dir,
                      run_folder / ".claude" / "skills", run_folder / ".opencode"):
        directory.mkdir(parents=True, exist_ok=True)

    skills = _seed_tree(PACKAGE_SKILLS_DIR, run_folder / ".claude" / "skills", overwrite=True)
    opencode = _seed_tree(PACKAGE_OPENCODE_DIR, run_folder / ".opencode", overwrite=True)

    seeded: list[str] = []
    for src in sorted(PACKAGE_SCHEMAS_DIR.glob("*_schema.yaml")):
        name = src.name.removesuffix("_schema.yaml")
        if not any(fnmatch(name, pattern) for pattern in wanted):
            continue
        shutil.copy2(src, schemas_dir / src.name)
        seeded.append(name)

    if PACKAGE_META_YAML.is_file():
        shutil.copy2(PACKAGE_META_YAML, run_folder / "domains" / "meta.yaml")

    return {"skills": skills, "opencode": opencode, "schemas": seeded}


def create(
    name: str,
    *,
    vault: str | None,
    data_dir: str | None = None,
    archive_dir: str | None = None,
    graph_uri: str | None = None,
    graph_database: str | None = None,
    graph_username: str | None = None,
    graph_password: str | None = None,
    schemas: "list[str] | None" = None,
    serve_port: int | None = None,
) -> dict:
    """Create a workspace: run folder, its .env, and a registry entry.

    Does NOT touch the pointer file — creating a workspace and switching to it
    are separate acts, so a create can never silently move the user off the
    knowledge base they were working in.
    """
    if not valid_workspace_name(name):
        raise WorkspaceError(f"Invalid workspace name {name!r}")
    if get(name) is not None:
        raise WorkspaceError(f"Workspace {name!r} already exists.")

    run_folder = WORKSPACES_DIR / name
    if run_folder.exists() and any(run_folder.iterdir()):
        raise WorkspaceError(
            f"{run_folder} already exists and is not empty. Remove it, or pick "
            "another name."
        )

    if vault:
        vault_path = Path(vault).expanduser()
        if not vault_path.is_dir():
            raise WorkspaceError(f"Vault {vault_path} does not exist.")
        vault = str(vault_path.resolve())

    data_dir = data_dir or str(Path.home() / f"artmind_data_{name}")
    archive_dir = archive_dir or str(Path.home() / f"artmind_archive_{name}")

    env: dict[str, str] = {"ARTMIND_DATA_DIR": data_dir, "ARTMIND_ARCHIVE_DIR": archive_dir}
    if vault:
        env["ARTMIND_VAULT_DIR"] = vault
    for key, value in (
        ("ARTMIND_KG_NEO4J_URI", graph_uri),
        ("ARTMIND_KG_NEO4J_DATABASE", graph_database),
        ("ARTMIND_KG_NEO4J_USERNAME", graph_username),
        ("ARTMIND_KG_NEO4J_PASSWORD", graph_password),
    ):
        if value:
            env[key] = value

    # Register FIRST: it is the step that can legitimately refuse (a vault
    # another workspace already claims), and refusing before anything is written
    # leaves nothing half-created to clean up.
    entry = register(
        name,
        vault=vault,
        data_dir=data_dir,
        archive_dir=archive_dir,
        graph={"uri": graph_uri or "", "database": graph_database or ""},
        ports={"serve": serve_port} if serve_port else {},
        schemas=list(schemas) if schemas else list(STARTER_SCHEMAS),
    )

    seeded = scaffold(run_folder, schemas)
    env_path = run_folder / ".env"
    env_path.write_text(
        _render_env(env, f"artmind workspace {name!r} — workspace-scoped config only. "
                         f"Shared identity lives in {SHARED_ENV_FILE}."),
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    return {
        "workspace": name,
        "run_folder": str(run_folder),
        "env": str(env_path),
        "vault": vault,
        "data_dir": data_dir,
        "archive_dir": archive_dir,
        "graph": entry["graph"],
        "seeded": seeded,
        "next": [
            f"ARTMIND_WORKSPACE={name} artmind setup",
            f"artmind workspace use {name}",
        ],
    }


def adopt(name: str, source: Path | None = None, *, frozen: bool = False) -> dict:
    """Migrate a pre-workspace run folder into a named workspace.

    ``frozen`` marks the result as preserved rather than live. NOTE: it is
    currently recorded only — nothing reads it yet. Wiring it into `ingest`,
    `projection rebuild` and `snapshot restore` is the next step
    (docs/workspaces.md, "The registry").

    Non-destructive by construction: this COPIES. A half-migrated run folder is
    indistinguishable from a corrupt one, so the original is left exactly where
    it was and removing it stays the user's decision
    (docs/workspaces.md, "Adopting the current install").
    """
    if not valid_workspace_name(name):
        raise WorkspaceError(f"Invalid workspace name {name!r}")
    if get(name) is not None:
        raise WorkspaceError(f"Workspace {name!r} already exists.")

    source = (source or ARTMIND_ROOT).expanduser().resolve()
    source_env = source / ".env"
    if not source_env.is_file():
        raise WorkspaceError(
            f"{source} has no .env — it is not a run folder to adopt. Use "
            "`artmind workspace create` for a new one."
        )

    run_folder = WORKSPACES_DIR / name
    if run_folder.exists() and any(run_folder.iterdir()):
        raise WorkspaceError(f"{run_folder} already exists and is not empty.")

    values = parse_env_file(source_env)
    identity = {k: v for k, v in values.items() if k in IDENTITY_KEYS}
    workspace_env = {k: v for k, v in values.items() if k in WORKSPACE_KEYS}
    unclassified = sorted(set(values) - IDENTITY_KEYS - WORKSPACE_KEYS)
    # An unrecognised key stays where it already worked. Guessing it into the
    # shared file would leak a workspace-specific value into every workspace.
    workspace_env.update({k: values[k] for k in unclassified})

    run_folder.mkdir(parents=True, exist_ok=True)
    copied = []
    for entry_name in ADOPT_ENTRIES:
        src = source / entry_name
        if not src.exists():
            continue
        dest = run_folder / entry_name
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dest)
        copied.append(entry_name)

    (run_folder / ".env").write_text(
        _render_env(workspace_env, f"artmind workspace {name!r} — workspace-scoped config only."),
        encoding="utf-8",
    )
    (run_folder / ".env").chmod(0o600)

    shared_written = False
    if identity and not SHARED_ENV_FILE.exists():
        SHARED_ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        SHARED_ENV_FILE.write_text(
            _render_env(identity, "artmind shared identity — provider, credentials, models. "
                                  "Every workspace inherits this; a workspace .env overrides it."),
            encoding="utf-8",
        )
        SHARED_ENV_FILE.chmod(0o600)
        shared_written = True

    register(
        name,
        vault=workspace_env.get("ARTMIND_VAULT_DIR"),
        data_dir=workspace_env.get("ARTMIND_DATA_DIR", ""),
        archive_dir=workspace_env.get("ARTMIND_ARCHIVE_DIR", ""),
        graph={
            "uri": workspace_env.get("ARTMIND_KG_NEO4J_URI", ""),
            "database": workspace_env.get("ARTMIND_KG_NEO4J_DATABASE", ""),
        },
        schemas=[],
        frozen=frozen,
    )

    return {
        "workspace": name,
        "source": str(source),
        "run_folder": str(run_folder),
        "copied": copied,
        "identity_keys": sorted(identity),
        "workspace_keys": sorted(workspace_env),
        "unclassified": unclassified,
        "shared_env": str(SHARED_ENV_FILE) if shared_written else None,
        "shared_env_existed": bool(identity) and not shared_written,
        "source_left_in_place": True,
    }
