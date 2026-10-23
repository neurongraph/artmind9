"""Long-running query server that keeps artmind's imports warm.

Exposes the read-only `artmind query ...` CLI surface over localhost HTTP.
Requests are executed through the real click commands in-process, so responses
are byte-identical to a direct CLI run — this is a transport layer, not a
reimplementation. Non-query commands are rejected; the daemon never writes.
"""

import os
import threading
from importlib.metadata import PackageNotFoundError, version

from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from artmind._entry import DEFAULT_PORT  # noqa: F401  (re-exported for callers)
from artmind.cli import cli
from artmind.traces.span import trace_override

# CliRunner redirects the process-wide stdout, so invocations must not overlap.
_invoke_lock = threading.Lock()


class QueryRequest(BaseModel):
    args: list[str]
    #: The caller's query-trace id (spec 2026-10-11 §5.4), forwarded by
    #: `_entry._proxy`. The daemon never reads a trace id from its own env.
    trace_id: str | None = None


def _service_version() -> str:
    try:
        return version("artmind9")
    except PackageNotFoundError:
        return "unknown"


def run_cli_args(args: list[str], trace_id: str | None = None) -> dict:
    if not args or args[0] != "query":
        raise ValueError("only 'artmind query ...' commands are served")
    with _invoke_lock, trace_override(trace_id):
        result = CliRunner().invoke(cli, args)
    stderr = result.stderr
    if result.exception is not None and result.exit_code != 0 and not stderr:
        stderr = repr(result.exception)
    # result.output mixes both streams in click 8.2+; stdout keeps them separate
    return {"exit_code": result.exit_code, "output": result.stdout, "stderr": stderr}


def create_app() -> FastAPI:
    app = FastAPI(title="artmind serve", version=_service_version())

    @app.get("/health")
    def health() -> dict:
        """`vault` and `pid` say whose daemon this is: the Obsidian plugin
        reuses a serve only when it serves the plugin's vault, and stops only
        the pid it started."""
        import paths

        vault = paths.ARTMIND_VAULT_DIR
        return {
            "service": "artmind",
            "status": "ok",
            "version": _service_version(),
            "vault": str(vault) if vault else None,
            "pid": os.getpid(),
        }

    @app.post("/query")
    def query(request: QueryRequest) -> dict:
        try:
            return run_cli_args(request.args, request.trace_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return app
