"""Capture mode (P3 spec §3.3): run one program step as a real Click command and catch its JSON.

`_echo_json` checks `active()`. When a capture box is set it stores the enveloped, **uncut** payload
in the box and prints nothing. The step runs through `cli.main(standalone_mode=False)` in the
caller's thread: no `CliRunner`, so nothing is redirected and `serve`'s invoke lock is not taken again.

Light on purpose: `artmind.traces.span` imports this module, so it must not import `artmind.cli`
at module level.
"""

from __future__ import annotations

import contextvars
import shlex
import time
from contextlib import contextmanager
from dataclasses import dataclass

import click

_capture: contextvars.ContextVar = contextvars.ContextVar("artmind_query_run_capture", default=None)

#: Read-only `db` subcommands a step may run (the set `TracedDbGroup` traces).
DB_READ_ONLY = frozenset({"db list", "db schema", "db sql", "db timeline", "db bridge", "db review"})
#: `query` subcommands a step may never run: the executor itself, the planner, and paging.
QUERY_REFUSED = frozenset({"run", "plan", "page"})


@dataclass
class StepResult:
    id: str
    argv: list
    exit_code: int
    error: str | None
    payload: dict | None
    ms: float
    chars: int
    skipped: str | None = None


def active() -> dict | None:
    """The current capture box, or None outside a captured step."""
    return _capture.get()


@contextmanager
def capturing(box: dict):
    token = _capture.set(box)
    try:
        yield box
    finally:
        _capture.reset(token)


def _cli():
    from artmind.cli import cli

    return cli


def command_key(argv) -> str:
    """`["query", "graph", "pattern7", "--domain", ...]` -> `query graph pattern7` (words before the first option)."""
    words = []
    for token in argv:
        if str(token).startswith("-"):
            break
        words.append(str(token))
    return " ".join(words)


def allowed(argv) -> bool:
    """A step may be any `query` subcommand except run/plan/page, or a read-only `db` subcommand."""
    if not argv or any(a in ("--help", "-h") for a in argv):
        return False
    if argv[0] == "query":
        return len(argv) > 1 and argv[1] not in QUERY_REFUSED
    if argv[0] == "db":
        if "bridge" in argv and {"confirm", "clear"} & set(argv):
            return False
        return " ".join(argv[:2]) in DB_READ_ONLY
    return False


def argv_of(cmd: str) -> list[str] | None:
    """`artmind query ...` -> ["query", ...]; None when it is not an artmind command line."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if len(tokens) < 2 or tokens[0] != "artmind":
        return None
    return tokens[1:]


def _first_line(text) -> str:
    return (str(text or "").strip().splitlines() or [""])[0][:300]


def invoke_step(argv, step_id: str = "") -> StepResult:
    """Run one step; never raises for a failing command (KeyboardInterrupt propagates)."""
    argv = [str(a) for a in argv]
    started = time.perf_counter()

    def done(exit_code, error, payload=None, chars=0) -> StepResult:
        return StepResult(step_id, argv, exit_code, error, payload, round((time.perf_counter() - started) * 1000, 1), chars)

    if not allowed(argv):
        return done(1, "not_allowed")
    box: dict = {}
    try:
        with capturing(box):
            rv = _cli().main(args=argv, prog_name="artmind", standalone_mode=False)
    except KeyboardInterrupt:
        raise
    except click.ClickException as exc:
        return done(exc.exit_code, _first_line(exc.format_message()))
    except click.exceptions.Exit as exc:
        rv = exc.exit_code
    except click.exceptions.Abort:
        return done(1, "aborted")
    except SystemExit as exc:
        rv = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:  # a bug in a command: a step error, not a crash of the run
        return done(1, f"{type(exc).__name__}: {_first_line(exc)}")
    exit_code = rv if isinstance(rv, int) else 0
    if exit_code != 0:
        return done(exit_code, "exit")
    if "payload" not in box:
        return done(1, "no_json")
    return done(0, None, box["payload"], box.get("chars", 0))
