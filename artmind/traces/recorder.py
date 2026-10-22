"""Turn side of trace capture (spec 2026-10-11 §5.1, §5.6).

One `TraceRecorder` per question -> answer turn. `observe(event)` records a
neutral UI event (with the mapper's private `_trace` data) and returns the
event as the UI should see it. `finish()` writes the events, assigns CLI
spans to the tool calls that ran them, and stores the derived columns.
Nothing here may break a turn: a recorder that fails to start is disabled
and still strips `_trace`; `finish()` swallows its own failures.
"""

import hashlib
import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from artmind.query_plan import shadow
from artmind.traces import path as trace_path
from artmind.traces import store

logger = logging.getLogger(__name__)

#: Seconds of slack when matching a span's start to a tool call's window.
SKEW_S = 0.5


def public_event(event: dict) -> dict:
    return {k: v for k, v in event.items() if k != "_trace"} if "_trace" in event else event


class TraceRecorder:
    def __init__(self, trace_id: str | None, context_path: Path | None) -> None:
        self.trace_id = trace_id
        self.enabled = trace_id is not None
        self._context_path = Path(context_path) if context_path else None
        self._events: list[dict] = []
        self._tools: dict[str, dict] = {}
        self._tool_event: dict[str, dict] = {}
        self._buffers = {"text": [], "thinking": []}
        self._answer: list[str] = []
        self._done: dict = {}
        self._shadow = None

    @classmethod
    def start(cls, *, source: str, question: str, session_id: str | None = None, profile: str | None = None,
              backend: str | None = None, benchmark_run_id: str | None = None, benchmark_seq: int | None = None,
              context_path: Path | None = None, shadow_question: str | None = None,
              shadow_domains: list[str] | None = None) -> "TraceRecorder":
        """`shadow_question` (the user's own text) starts a shadow plan for the turn (spec 2026-10-22 §6)."""
        try:
            trace_id = store.new_trace_id()
            store.insert_turn(trace_id, source=source, question=question, session_id=session_id, profile=profile,
                              backend=backend, benchmark_run_id=benchmark_run_id, benchmark_seq=benchmark_seq)
            if context_path:
                Path(context_path).write_text(trace_id)
            recorder = cls(trace_id, context_path)
        except Exception:
            logger.exception("trace capture disabled for this turn")
            return cls(None, None)
        if shadow_question:
            try:
                followup = bool(session_id) and store.count_session_turns(session_id) > 1
            except Exception:
                followup = False
            recorder._shadow = shadow.start(shadow_question, shadow_domains, followup)
        return recorder

    # ── per event ────────────────────────────────────────────────────────
    def observe(self, event: dict) -> dict:
        public = public_event(event)
        if self.enabled:
            try:
                self._record(public, event.get("_trace") or {})
            except Exception:
                logger.exception("trace event dropped")
        return public

    def _add(self, type_: str, payload: Any, *, tool_id=None, parent=None, name=None) -> dict:
        row = {"seq": len(self._events), "ts": time.time(), "type": type_, "tool_id": tool_id,
               "parent_tool_id": parent, "name": name, "payload": store.cap(payload)}
        self._events.append(row)
        return row

    def _flush(self, block: str) -> None:
        if self._buffers[block]:
            self._add(block, "".join(self._buffers[block]))
            self._buffers[block] = []

    def _record(self, event: dict, raw: dict) -> None:
        kind = event.get("type")
        if kind == "text_delta":
            self._buffers["text"].append(event.get("text", ""))
            self._answer.append(event.get("text", ""))
        elif kind == "thinking_delta":
            self._buffers["thinking"].append(event.get("text", ""))
        elif kind == "block_done":
            self._flush("text" if event.get("block") == "text" else "thinking")
        elif kind == "tool_call":
            raw_input = raw.get("input", event.get("input"))
            row = self._add("tool_call", raw_input, tool_id=event.get("id"), parent=raw.get("parent_tool_id"),
                            name=event.get("name"))
            self._tools[event.get("id")] = {"input": raw_input, "call_ts": row["ts"], "result_ts": None}
            self._tool_event[event.get("id")] = row
        elif kind == "tool_call_update":
            raw_input = raw.get("input", event.get("input"))
            if event.get("id") in self._tool_event:
                self._tool_event[event["id"]]["payload"] = store.cap(raw_input)
                self._tools[event["id"]]["input"] = raw_input
        elif kind == "tool_result":
            self._add("tool_result", raw.get("content", event.get("content")), tool_id=event.get("tool_id"),
                      parent=raw.get("parent_tool_id"))
            if event.get("tool_id") in self._tools:
                self._tools[event["tool_id"]]["result_ts"] = time.time()
            # Reset answer buffer: the stored answer is text emitted AFTER the
            # last tool_result, so mid-turn narration is not included.
            self._answer = []
        elif kind == "turn_done":
            self._done = {**event, **raw}
        elif kind == "error":
            self._add("error", event.get("message"))

    # ── end of turn ──────────────────────────────────────────────────────
    def finish(self, error: str | None = None) -> None:
        if not self.enabled:
            return
        try:
            self._flush("thinking")
            self._flush("text")
            if self._shadow is not None:
                self._add("plan_shadow", self._shadow.result(), name="query plan")
            store.insert_events(self.trace_id, self._events)
            spans = store.list_spans(self.trace_id)
            pairs = match_tool_ids(spans, self._tools)
            store.set_span_tool_ids(pairs)
            answer = "".join(self._answer) or self._done.get("result")
            derived = trace_path.derive(spans, answer)
            domains = domains_of(spans)
            usage = self._done.get("usage") or {}
            input_tokens = sum(usage.get(k) or 0 for k in
                               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) or None
            store.update_turn(
                self.trace_id,
                answer=answer or None,
                domains=json.dumps(domains),
                model=self._done.get("model"),
                turns=self._done.get("turns"),
                duration_s=self._done.get("duration_s"),
                cost_usd=self._done.get("cost"),
                input_tokens=input_tokens,
                output_tokens=usage.get("output_tokens"),
                graph_state=json.dumps(graph_state(domains)),
                retrieval_path=json.dumps(derived["retrieval_path"]),
                graph_attempts=derived["graph_attempts"],
                graph_productive=derived["graph_productive"],
                fell_through=int(derived["fell_through"]),
                answer_basis=derived["answer_basis"],
                status="error" if error else "done",
                error_message=error,
                completed_at=time.time(),
            )
        except Exception:
            logger.exception("trace finish failed")
        finally:
            self._clear_context()

    def _clear_context(self) -> None:
        try:
            if self._context_path is not None and self._context_path.exists():
                self._context_path.write_text("")
        except OSError:
            pass


def _input_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


#: A single- or double-quoted shell argument (double quotes may contain \" escapes).
_QUOTED = re.compile(r"'[^']*'" r'|"(?:[^"\\]|\\.)*"')


def _command_text(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("command"), str):
        # Strip quoted shell arguments so the leaf match only tests CLI
        # subcommand tokens, not free-text question content inside quotes.
        return _QUOTED.sub(" ", value["command"])
    return _input_text(value)


def _arg_values(span: dict) -> list[str]:
    """Distinctive string values from a span's args dict.

    Excludes keys `domain` and `compact` (common to all calls of the same
    command, so they don't help disambiguate between parallel calls).
    Only string values of length ≥ 3 are returned; boolean flags are skipped.
    """
    args = span.get("args")
    if not isinstance(args, dict):
        return []
    values: list[str] = []
    for k, v in args.items():
        if k in ("domain", "compact"):
            continue
        if isinstance(v, str) and len(v) >= 3:
            values.append(v)
        elif isinstance(v, (list, tuple)):
            for item in v:
                if isinstance(item, str) and len(item) >= 3:
                    values.append(item)
    return values


def match_tool_ids(spans: list[dict], tools: dict[str, dict]) -> list[tuple[str, str]]:
    """(span_id, tool_id) for each span whose start falls in a tool call's
    window and whose leaf command appears in that call's input next to
    `artmind`.

    Among candidates, each is scored by how many of the span's distinctive
    argument values (from span["args"]) appear in the raw command string.
    Highest score wins; ties go to the latest-starting call (existing behaviour).
    """
    pairs = []
    for span in spans:
        command = span.get("command") or ""
        leaf = command.split()[-1] if command else ""
        arg_vals = _arg_values(span)
        best, best_start, best_score = None, None, -1
        for tool_id, tool in tools.items():
            start = tool["call_ts"] - SKEW_S
            end = (tool["result_ts"] if tool["result_ts"] is not None else float("inf")) + SKEW_S
            if not (start <= span["started_at"] <= end):
                continue
            # Leaf matching uses the quote-stripped text to avoid false hits
            # on question content that happens to contain a subcommand name.
            stripped = _command_text(tool["input"])
            if "artmind" not in stripped:
                continue
            if not leaf or not re.search(rf"(?<![\w-]){re.escape(leaf)}(?![\w-])", stripped):
                continue
            # Score by how many distinctive arg values appear in the RAW command.
            raw_cmd = (tool["input"]["command"]
                       if isinstance(tool["input"], dict) and isinstance(tool["input"].get("command"), str)
                       else _input_text(tool["input"]))
            score = sum(1 for v in arg_vals if v in raw_cmd)
            if score > best_score or (score == best_score and (best_start is None or tool["call_ts"] > best_start)):
                best, best_start, best_score = tool_id, tool["call_ts"], score
        if best is not None:
            pairs.append((span["span_id"], best))
    return pairs


def domains_of(spans: list[dict]) -> list[str]:
    found: set[str] = set()
    for span in spans:
        args = span.get("args")
        values: list[str] = []
        if isinstance(args, dict):
            raw = args.get("domain") or []
            values = [raw] if isinstance(raw, str) else list(raw)
        elif isinstance(args, list):
            values = [args[i + 1] for i, tok in enumerate(args[:-1]) if tok == "--domain"]
        for value in values:
            found.update(part.strip() for part in str(value).split(",") if part.strip())
    return sorted(found)


def graph_state(domains: list[str]) -> dict:
    """What the turn ran against: package version, schema file hashes, vault HEAD."""
    import paths

    try:
        from importlib.metadata import version

        artmind_version = version("artmind9")
    except Exception:
        artmind_version = None
    schemas = {}
    for domain in domains:
        schema = paths.DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
        if schema.is_file():
            schemas[domain] = hashlib.sha256(schema.read_bytes()).hexdigest()
    vault_head = None
    if paths.ARTMIND_VAULT_DIR:
        try:
            out = subprocess.run(["git", "-C", str(paths.ARTMIND_VAULT_DIR), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=2)
            vault_head = out.stdout.strip() or None if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            vault_head = None
    return {"artmind_version": artmind_version, "schemas": schemas, "vault_head": vault_head}
