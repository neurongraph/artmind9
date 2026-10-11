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

    def invoke(self, ctx: click.Context):
        if _active.get() is not None:
            return super().invoke(ctx)
        tokens = [*getattr(ctx, "_protected_args", []), *ctx.args]
        try:
            trace_id = None if "--help" in tokens else current_trace_id()
        except Exception:
            trace_id = None
        if not trace_id:
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
        except BaseException:
            exit_code = 1
            raise
        finally:
            _active.reset(token)
            _record(span, exit_code)


def observe_output(payload) -> None:
    """Called by `cli._echo_json` with the payload it is about to print."""
    span = _active.get()
    if span is None:
        return
    try:
        span["summary"] = evidence.summarize(payload)
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
                "started_at": span["started_at"],
            },
            span["llm_calls"],
        )
    except Exception:
        pass
