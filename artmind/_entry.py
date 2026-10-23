"""Lightweight console entry point for the `artmind` script.

When a `artmind serve` daemon is running, `artmind query ...` calls are proxied
to it over localhost HTTP, skipping the ~2s of heavy imports every CLI process
otherwise pays. All other commands — and query calls when no daemon is up —
fall through to the full CLI unchanged.

A daemon is used only when its `/health` names the vault this process would
resolve (or none, for both): two vaults can be open at once, and a query
answered from another vault's graph is worse than a slow one.

This module must import stdlib only (artmind.vault is stdlib-only too); the fast
path's entire point is avoiding artmind.cli's import tree. Set
ARTMIND_NO_PROXY=1 to always run in-process.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from artmind.vault import VaultError, resolve_vault

DEFAULT_PORT = 8377
HEALTH_TIMEOUT = 0.25
QUERY_TIMEOUT = 600

#: Query-trace id sources (docs/superpowers/specs/2026-10-11-query-review-loop-design.md
#: §5.2): an explicit id, else the per-session file the admin console rewrites
#: at the start of every turn (the agent process outlives a turn, so the id
#: cannot travel in its environment).
TRACE_ID_ENV = "ARTMIND_TRACE_ID"
TRACE_CONTEXT_ENV = "ARTMIND_TRACE_CONTEXT"


def _trace_id() -> str | None:
    """The query-trace id this process should record under, or None. Never raises."""
    explicit = os.environ.get(TRACE_ID_ENV, "").strip()
    if explicit:
        return explicit
    path = os.environ.get(TRACE_CONTEXT_ENV)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _base_url() -> str:
    port = os.environ.get("ARTMIND_SERVE_PORT", str(DEFAULT_PORT))
    return f"http://127.0.0.1:{port}"


def _same_vault(served: str | None, ours: Path | None) -> bool:
    if served is None or ours is None:
        return served is None and ours is None
    try:
        return Path(served).resolve() == ours
    except OSError:
        return False


def _daemon_alive(base_url: str) -> bool:
    """An artmind serve for this process's vault answers on `base_url`.

    One that does not name its vault (it predates the field) is not used. An
    unresolvable vault (a bad ARTMIND_VAULT) runs in-process, where the CLI
    reports it."""
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=HEALTH_TIMEOUT) as resp:
            health = json.loads(resp.read())
    except Exception:
        return False
    if health.get("service") != "artmind" or "vault" not in health:
        return False
    try:
        ours = resolve_vault()
    except VaultError:
        return False
    if _same_vault(health["vault"], ours):
        return True
    sys.stderr.write(
        f"artmind: the serve on {base_url} serves {health['vault'] or 'no vault'}, "
        f"not {ours or 'this directory'}; running in-process. "
        "Give each vault its own ARTMIND_SERVE_PORT.\n"
    )
    return False


def _proxy(base_url: str, args: list[str]) -> int:
    payload = json.dumps({"args": args, "trace_id": _trace_id()}).encode()
    request = urllib.request.Request(
        f"{base_url}/query",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=QUERY_TIMEOUT) as resp:
        body = json.loads(resp.read())
    sys.stdout.write(body["output"])
    if body.get("stderr"):
        sys.stderr.write(body["stderr"])
    return body["exit_code"]


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "query" and not os.environ.get("ARTMIND_NO_PROXY"):
        base_url = _base_url()
        if _daemon_alive(base_url):
            try:
                sys.exit(_proxy(base_url, args))
            except Exception:
                pass  # daemon died mid-request; fall through to in-process run

    from artmind.cli import cli

    cli()
