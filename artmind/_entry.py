"""Lightweight console entry point for the `artmind` script.

When a `artmind serve` daemon is running, `artmind query ...` calls are proxied
to it over localhost HTTP, skipping the ~2s of heavy imports every CLI process
otherwise pays. All other commands — and query calls when no daemon is up —
fall through to the full CLI unchanged.

A daemon is only usable if it is bound to the SAME workspace as this process
(docs/workspaces.md, guardrail 2). Otherwise it answers confidently from another
knowledge base, which is worse than the stale-code problem CLAUDE.md already
warns about. So the fingerprint below mirrors `paths.workspace_fingerprint()` —
in pure stdlib, because this module must not import artmind.cli's tree, which is
the fast path's entire point. `test/test_workspace.py` asserts the two agree.

Set ARTMIND_NO_PROXY=1 to always run in-process.
"""

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8377
HEALTH_TIMEOUT = 0.25
QUERY_TIMEOUT = 600


def _base_url() -> str:
    port = os.environ.get("ARTMIND_SERVE_PORT", str(DEFAULT_PORT))
    return f"http://127.0.0.1:{port}"


_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _run_folder() -> Path:
    """Mirror of ``paths._resolve_run_folder``, stdlib only. Same precedence:
    ARTMIND_HOME, then ARTMIND_WORKSPACE, then the pointer file, then the
    pre-workspace layout. The pointer is plain text precisely so this can read
    it without yaml."""
    home = os.environ.get("ARTMIND_HOME")
    if home:
        return Path(home).expanduser().resolve()

    root = Path(
        os.environ.get("ARTMIND_ROOT") or (Path.home() / ".artmind")
    ).expanduser().resolve()

    name = (os.environ.get("ARTMIND_WORKSPACE") or "").strip()
    if not name:
        try:
            name = (root / "current").read_text(encoding="utf-8").strip()
        except OSError:
            name = ""

    if not name or ".." in name or not _NAME_RE.fullmatch(name):
        return root
    return (root / "workspaces" / name).resolve()


def _env_value(key: str, run_folder: Path) -> str:
    """`key` from the real environment, else from the same .env files
    ``paths.py`` loads, in the same most-specific-first order."""
    if key in os.environ:
        return os.environ[key]
    root = Path(
        os.environ.get("ARTMIND_ROOT") or (Path.home() / ".artmind")
    ).expanduser().resolve()
    for candidate in (run_folder / ".env", root / "config.env"):
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip().removeprefix("export ").strip() != key:
                continue
            return value.strip().strip("\"'")
    return ""


def _fingerprint() -> str:
    run_folder = _run_folder()
    database = _env_value("ARTMIND_KG_NEO4J_DATABASE", run_folder)
    return hashlib.sha256(
        f"{run_folder}\n{database}".encode("utf-8")
    ).hexdigest()[:16]


def _daemon_alive(base_url: str) -> bool:
    """Live AND bound to this process's workspace.

    A daemon that reports no fingerprint predates guardrail 2 and is treated as
    a mismatch rather than assumed to agree — the whole point is to not trust a
    daemon we cannot identify.
    """
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=HEALTH_TIMEOUT) as resp:
            body = json.loads(resp.read())
    except Exception:
        return False
    if body.get("service") != "artmind":
        return False
    return body.get("workspace_fingerprint") == _fingerprint()


def _proxy(base_url: str, args: list[str]) -> int:
    payload = json.dumps({"args": args}).encode()
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
