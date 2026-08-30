"""Lightweight console entry point for the `artmind` script.

When a `artmind serve` daemon is running, `artmind query ...` calls are proxied
to it over localhost HTTP, skipping the ~2s of heavy imports every CLI process
otherwise pays. All other commands — and query calls when no daemon is up —
fall through to the full CLI unchanged.

This module must import stdlib only; the fast path's entire point is avoiding
artmind.cli's import tree. Set ARTMIND_NO_PROXY=1 to always run in-process.
"""

import json
import os
import sys
import urllib.request

DEFAULT_PORT = 8377
HEALTH_TIMEOUT = 0.25
QUERY_TIMEOUT = 600


def _base_url() -> str:
    port = os.environ.get("ARTMIND_SERVE_PORT", str(DEFAULT_PORT))
    return f"http://127.0.0.1:{port}"


def _daemon_alive(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=HEALTH_TIMEOUT) as resp:
            return json.loads(resp.read()).get("service") == "artmind"
    except Exception:
        return False


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
