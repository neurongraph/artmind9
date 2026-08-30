"""Long-running query server that keeps artmind's imports warm.

Exposes the read-only `artmind query ...` CLI surface over localhost HTTP.
Requests are executed through the real click commands in-process, so responses
are byte-identical to a direct CLI run — this is a transport layer, not a
reimplementation. Non-query commands are rejected; the daemon never writes.
"""

import threading
from importlib.metadata import PackageNotFoundError, version

from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from artmind._entry import DEFAULT_PORT  # noqa: F401  (re-exported for callers)
from artmind.cli import cli

# CliRunner redirects the process-wide stdout, so invocations must not overlap.
_invoke_lock = threading.Lock()


class QueryRequest(BaseModel):
    args: list[str]


def _service_version() -> str:
    try:
        return version("artmind9")
    except PackageNotFoundError:
        return "unknown"


def run_cli_args(args: list[str]) -> dict:
    if not args or args[0] != "query":
        raise ValueError("only 'artmind query ...' commands are served")
    with _invoke_lock:
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
        return {"service": "artmind", "status": "ok", "version": _service_version()}

    @app.post("/query")
    def query(request: QueryRequest) -> dict:
        try:
            return run_cli_args(request.args)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return app
