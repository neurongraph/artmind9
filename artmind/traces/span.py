"""CLI side of trace capture (spec 2026-10-11 §5.2-§5.5): one span per
`artmind query` invocation, run in-process or inside `serve`.

`TracedGroup` wraps the `query` group's invoke: it opens a span when a trace
id resolves, sees the exit code from whatever the subcommand raised, and
writes the span in `finally`. `_echo_json` hands the payload it prints to
`observe_output`; `log_llm_call` hands each LLM call to `record_llm_call`.
With no trace id, nothing here touches the DB. Every write is swallowed --
capture must never change a query's output or exit code.
"""

import contextvars
import json
import time
import uuid
from contextlib import contextmanager

import click
from rich_click import RichGroup

from artmind._entry import _trace_id as _env_trace_id
from artmind.traces import evidence

_UNSET = object()
_override: contextvars.ContextVar = contextvars.ContextVar("artmind_trace_override", default=_UNSET)
_active: contextvars.ContextVar = contextvars.ContextVar("artmind_trace_span", default=None)


def current_trace_id() -> str | None:
    """The in-process override when one is set (even to None: `serve` never
    reads its own environment), else ARTMIND_TRACE_ID / ARTMIND_TRACE_CONTEXT."""
    value = _override.get()
    if value is not _UNSET:
        return value
    return _env_trace_id()


@contextmanager
def trace_override(trace_id: str | None):
    token = _override.set(trace_id or None)
    try:
        yield
    finally:
        _override.reset(token)


def _command_path(group: click.Group, ctx: click.Context, tokens: list[str]) -> str:
    path = [group.name or "query"]
    cmd: click.Command = group
    for token in tokens:
        if token.startswith("-") or not isinstance(cmd, click.Group):
            break
        sub = cmd.get_command(ctx, token)
        if sub is None:
            break
        path.append(token)
        cmd = sub
    return " ".join(path)


class TracedGroup(RichGroup):
    """The `query` group. Nested groups (`query graph`, `query views`) are
    plain RichGroups -- RichGroup's `group_class = type` would otherwise make
    them TracedGroups too -- and run inside this group's span."""

    group_class = RichGroup
    #: When set, only invocations whose command path is in this set open a span
    #: (the `db` group: read-only subcommands only).
    traced_commands: frozenset | None = None

    def resolve_command(self, ctx: click.Context, args: list[str]):
        """Stock resolution, except that a top-level group named in the wrong place
        (`artmind query db schema`) is answered with the command that works."""
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError as exc:
            name = args[0] if args else ""
            root = ctx.find_root().command
            if (
                str(exc.message).startswith("No such command")
                and name
                and not name.startswith("-")
                and isinstance(root, click.Group)
                and isinstance(root.commands.get(name), click.Group)
            ):
                rest = " ".join(args[1:])
                fixed = f"artmind {name} {rest}".rstrip()
                raise click.UsageError(
                    f"No such command '{name}' under `{self.name}`. It is a top-level group: {fixed}", ctx
                ) from exc
            raise

    def _eligible(self, ctx: click.Context, tokens: list[str]) -> bool:
        if self.traced_commands is None:
            return True
        # `db bridge --domain x confirm`: options before the subcommand hide it from the path walk.
        if "bridge" in tokens and {"confirm", "clear"} & set(tokens):
            return False
        return _command_path(self, ctx, tokens) in self.traced_commands

    def invoke(self, ctx: click.Context):
        if _active.get() is not None:
            return super().invoke(ctx)
        tokens = [*getattr(ctx, "_protected_args", []), *ctx.args]
        try:
            trace_id = None if "--help" in tokens else current_trace_id()
        except Exception:
            trace_id = None
        if not trace_id or not self._eligible(ctx, tokens):
            return super().invoke(ctx)

        try:
            span = {
                "span_id": uuid.uuid4().hex, "trace_id": trace_id, "argv": list(tokens),
                "command": _command_path(self, ctx, tokens), "started_at": time.time(),
                "summary": None, "params": None, "llm_calls": [],
            }
            token = _active.set(span)
        except Exception:
            return super().invoke(ctx)
        exit_code = 0
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit as exc:
            exit_code = exc.exit_code
            raise
        except click.ClickException as exc:
            exit_code = exc.exit_code
            raise
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            raise
        except BaseException:
            exit_code = 1
            raise
        finally:
            _active.reset(token)
            _record(span, exit_code)


def observe_output(payload, *, payload_chars: int | None = None, shown_chars: int | None = None,
                   handle: str | None = None) -> None:
    """Called by `cli._echo_json` with the payload it is about to print."""
    span = _active.get()
    if span is None:
        return
    try:
        span["summary"] = evidence.summarize(payload)
        span["payload_chars"], span["shown_chars"], span["handle"] = payload_chars, shown_chars, handle
        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            span["params"] = dict(ctx.params)
    except Exception:
        pass


def record_llm_call(call_type: str, model: str, prompt: str, response: str) -> None:
    """Called by `utils.functions.log_llm_call`; a no-op outside a span."""
    span = _active.get()
    if span is None:
        return
    span["llm_calls"].append({"seq": len(span["llm_calls"]), "call_type": call_type, "model": model,
                              "prompt": prompt, "response": response, "duration_ms": None})


def _record(span: dict, exit_code: int | None) -> None:
    try:
        from artmind.traces import store

        summary = span["summary"] or {"rows": 0, "truncated": False, "rows_total": None, "evidence": {}}
        args = span["params"] if span["params"] is not None else span["argv"]
        store.insert_span(
            {
                "span_id": span["span_id"], "trace_id": span["trace_id"], "command": span["command"],
                "args": json.dumps(args, default=str), "exit_code": exit_code if exit_code is not None else 0,
                "duration_ms": int((time.time() - span["started_at"]) * 1000),
                "rows": summary["rows"], "rows_total": summary["rows_total"],
                "truncated": int(bool(summary["truncated"])),
                "evidence": json.dumps(summary["evidence"], default=str),
                "started_at": span["started_at"], "payload_chars": span.get("payload_chars"),
                "shown_chars": span.get("shown_chars"), "handle": span.get("handle"),
            },
            span["llm_calls"],
        )
    except Exception:
        pass


class TracedDbGroup(TracedGroup):
    """The `db` group: spans for its read-only subcommands only (B8). `mappings` (its default action
    writes with --acceptProposed), `grain` (--set), `catalogue` (rebuilds the graph projection),
    `bridge confirm|clear`, `propose`, `refresh`, `connect`, `backup`, `restore`, `export-text` and
    `restore-text` are not traced."""

    traced_commands = frozenset({"db list", "db schema", "db sql", "db timeline", "db bridge", "db review"})
