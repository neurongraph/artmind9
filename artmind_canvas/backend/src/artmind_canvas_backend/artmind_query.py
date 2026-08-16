"""Read the knowledge graph through artmind's own front door (ADR 0003).

The canvas backend is a **pure client** and never opens a Neo4j driver of its
own. Instead it POSTs read-only ``query`` commands to the warm ``artmind serve``
daemon — the same transport artmind's CLI proxy uses (``artmind/_entry.py``).
The daemon runs the real ``artmind query ...`` Click command in-process
(``CliRunner``) and returns its stdout, which for ``--compact`` is JSON. So graph
reads stay entirely behind artmind's surface: no DB coupling, no Neo4j env, no
ingestion logic re-implemented here.

Unlike the CLI proxy, we deliberately do **not** fall back to an in-process run
when the daemon is down — that would open a Neo4j connection inside the canvas
process, which the architecture forbids. A missing daemon is a clean failure
(``ServeUnavailable``) the route surfaces as 503.
"""

import asyncio
import json
import os
import urllib.error
import urllib.request

DEFAULT_SERVE_PORT = 8377
QUERY_TIMEOUT_S = 30.0


class ServeUnavailable(RuntimeError):
    """The ``artmind serve`` daemon could not be reached."""


class ArtmindQueryError(RuntimeError):
    """The daemon ran the command but it failed (non-zero exit / bad output)."""

    def __init__(self, message: str, *, exit_code: int | None = None, stderr: str | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


def _base_url() -> str:
    port = os.environ.get("ARTMIND_SERVE_PORT") or DEFAULT_SERVE_PORT
    return f"http://127.0.0.1:{port}"


def _post_query(args: list[str]) -> dict:
    """Synchronous POST to the daemon; run off-thread via :func:`run_query`."""
    url = _base_url() + "/query"
    data = json.dumps({"args": args}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=QUERY_TIMEOUT_S) as resp:
            body = json.loads(resp.read())
    except urllib.error.URLError as exc:  # connection refused, DNS, timeout
        raise ServeUnavailable(
            f"artmind serve is not reachable at {_base_url()} — start it with `artmind serve`"
        ) from exc

    if body.get("exit_code") != 0:
        stderr = body.get("stderr") or ""
        raise ArtmindQueryError(
            stderr.strip() or "artmind query failed",
            exit_code=body.get("exit_code"),
            stderr=stderr,
        )
    output = body.get("output", "")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ArtmindQueryError(f"artmind query returned non-JSON output: {output[:200]!r}") from exc


async def run_query(subcommand_args: list[str]) -> dict:
    """Run ``artmind query <subcommand_args...> --compact`` via the serve daemon.

    ``subcommand_args`` are everything after ``query`` (e.g.
    ``["entity-context", "--domain", "eng", "--entityId", "x"]``). ``--compact``
    is appended so the daemon's stdout is compact JSON we can parse.
    """
    args = ["query", *subcommand_args, "--compact"]
    return await asyncio.to_thread(_post_query, args)


def _domain_flags(domains: list[str]) -> list[str]:
    flags: list[str] = []
    for d in domains:
        flags += ["--domain", d]
    return flags


async def entity_resolve(reference: str, domains: list[str], top_k: int = 5) -> dict:
    """Free-text name/description → canonical entities (``query entity-resolve``)."""
    args = ["entity-resolve", *_domain_flags(domains), "--topK", str(top_k), reference]
    return await run_query(args)


async def entity_context(entity_id: str, domains: list[str], include_chunks: int = 8) -> dict:
    """Entity id → entity + one-hop relationships + source chunks/documents."""
    args = [
        "entity-context",
        *_domain_flags(domains),
        "--entityId",
        entity_id,
        "--includeChunks",
        str(include_chunks),
    ]
    return await run_query(args)
