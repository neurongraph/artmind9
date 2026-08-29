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
import urllib.request
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
