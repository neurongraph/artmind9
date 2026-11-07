# P3a executor, slices S1–S4: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `artmind query run`: run a plan's program in process and return one bounded evidence bundle, with nested trace spans and a replay gate for latency and bundle reach.

**Architecture:** A new package, `artmind/query_run/`. `capture` runs one step as a real Click command and catches its JSON in `_echo_json` instead of printing it. `facts` computes step predicates and document keys from a payload. `executor` walks the plan payload's `program` JSON (the planner fills; the executor runs). `adapters` and `bundle` turn step payloads into ranked, budgeted bundle rows. `replay_run` measures the P3a gate. The CLI gains `query run` and `plan replay --run`.

**Tech stack:** Python 3.14, Click 8.4 / rich-click, pytest (hermetic, no Neo4j), uv.

**Spec:** `docs/superpowers/specs/2026-11-07-p3-executor-design.md` (§3, §6, §7.1, §9.1, §11 S1–S4).

---

## Before you start

- Work on a branch cut from `claude/p3-design-a9f633`, which holds the spec and this plan, or from master once that branch is merged. Agent worktrees are cut from master: run `git merge claude/p3-design-a9f633` first.
- Read `CLAUDE.md` §"Testing implications": a running `serve` serves stale code, so check live runs with `ARTMIND_NO_PROXY=1`. Hermetic tests mock the graph.
- Run tests with `uv run --group dev pytest <path> -q`. Run the full suite (`uv run --group dev pytest -q test/`, about 6,700 tests, 3–4 min) at the end of each slice.
- Privacy: test data is invented. Never paste vault content into tests or commits.
- Commit after every task, with the message given. End each commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push.

## File structure

| File | Slice | Responsibility |
|---|---|---|
| `artmind/query_run/__init__.py` | S1 | package marker |
| `artmind/query_run/capture.py` | S1 | capture contextvar; `StepResult`; `allowed(argv)`; `invoke_step(argv, step_id)` |
| `artmind/query_run/facts.py` | S1 | `step_documents` (moved from reach), `doc_key`, `main_list`, `step_facts`, `merge`, `public`, `evaluate` |
| `artmind/cli.py` | S1–S4 | `_echo_json` capture branch; staleness callback skip; `query run`; `query page --list`; `plan replay --run`; COMMAND_GROUPS |
| `artmind/query_plan/reach.py` | S1 | imports `step_documents` and the needle facts from `facts` |
| `artmind/query_run/executor.py` | S2 | `Execution`, `execute(plan, ...)` |
| `artmind/query_plan/planner.py` | S2 | `replacements` key; `needs_unmet` on node JSON |
| `artmind/_entry.py` | S2, S4 | `_proxiable` for `query run --plan/--previous/--each` |
| `artmind/query_run/adapters.py` | S3 | payload → `Rows(evidence, documents, facts, guides)` per command |
| `artmind/query_run/bundle.py` | S3 | RRF ranking, assembly to budget, handle |
| `artmind/cli_conventions.py` | S3 | `LIST_KEY["query run"] = "evidence"` |
| `artmind/db.py`, `artmind/traces/store.py`, `artmind/traces/span.py` | S4 | `parent_span_id`, run columns, child spans |
| `artmind/traces/evidence.py`, `artmind/provenance.py`, `artmind/traces/analysis.py` | S4 | bundle evidence; skip child spans for sources; top-level spans for signals; `run` stats block; nested `show` |
| `artmind/query_run/replay_run.py` | S4 | `plan replay --run` measurement |
| `CLAUDE.md`, `docs/question-routing.md` | S4 | document `query run` |

---

# Slice S1: capture and facts

### Task 1: The capture module

**Files:**
- Create: `artmind/query_run/__init__.py`
- Create: `artmind/query_run/capture.py`
- Test: `test/test_query_run_capture.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Capture mode: a step runs as a real Click command and its JSON is caught, not printed (P3 spec §3.3)."""

import pytest

from artmind.query_run import capture


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_ID", raising=False)
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _overview(monkeypatch, rows=({"domain": "bank"},)):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": list(rows)})


def test_allowed_query_and_read_only_db_only():
    assert capture.allowed(["query", "vector-text", "--domain", "b", "q"])
    assert capture.allowed(["db", "sql", "select 1"])
    assert capture.allowed(["db", "bridge", "--domain", "b"])
    assert not capture.allowed(["db", "bridge", "--domain", "b", "confirm"])
    assert not capture.allowed(["db", "mappings"])
    assert not capture.allowed(["query", "run", "q"])
    assert not capture.allowed(["query", "plan", "q"])
    assert not capture.allowed(["query", "page", "abc"])
    assert not capture.allowed(["ingest", "sync", "x"])
    assert not capture.allowed(["query", "vector-text", "--help"])
    assert not capture.allowed([])


def test_a_captured_step_returns_the_payload_and_prints_nothing(monkeypatch, capsys):
    _overview(monkeypatch)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.id == "s1" and result.exit_code == 0 and result.error is None
    assert result.payload["command"] == "query domains-overview"
    assert result.payload["rows"][0]["domain"] == "bank"
    assert result.chars > 0 and result.ms >= 0
    assert capsys.readouterr().out == ""


def test_the_payload_is_whole_whatever_the_output_budget(monkeypatch):
    _overview(monkeypatch, rows=[{"domain": f"d{i}", "note": "x" * 200} for i in range(50)])
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "500")
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert len(result.payload["rows"]) == 50 and "handle" not in result.payload


def test_a_usage_error_is_a_step_error():
    result = capture.invoke_step(["query", "vector-text", "--compact"], "s1")
    assert result.exit_code == 2 and result.payload is None
    assert "QUESTION" in result.error


def test_a_click_exception_is_a_step_error(monkeypatch):
    import click

    from artmind import cli as cli_mod

    def bad(*a, **k):
        raise click.ClickException("bad input here\nsecond line")

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", bad)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.exit_code == 1 and result.error == "bad input here"


def test_a_store_failure_exits_3(monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    from artmind import cli as cli_mod

    def down(*a, **k):
        raise ServiceUnavailable("connection refused")

    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", down)
    result = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert result.exit_code == 3


def test_a_command_that_prints_no_json_is_no_json(monkeypatch):
    class Quiet:
        def main(self, **kw):
            return None

    monkeypatch.setattr(capture, "_cli", lambda: Quiet())
    result = capture.invoke_step(["query", "domains-overview"], "s1")
    assert (result.exit_code, result.error) == (1, "no_json")


def test_a_refused_command_never_runs(monkeypatch):
    monkeypatch.setattr(capture, "_cli", lambda: pytest.fail("must not run"))
    result = capture.invoke_step(["ingest", "sync", "x"], "s1")
    assert (result.exit_code, result.error, result.skipped) == (1, "not_allowed", None)


def test_capture_works_inside_an_outer_cli_runner(monkeypatch):
    """The serve path: an outer CliRunner holds stdout; a captured step must not need its own."""
    import click
    from click.testing import CliRunner

    from artmind import cli as cli_mod

    _overview(monkeypatch)

    @click.command("probe-capture")
    def probe():
        r = capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
        cli_mod._echo_json({"inner": r.payload["command"], "exit": r.exit_code}, True)

    cli_mod.query.add_command(probe)
    try:
        out = CliRunner().invoke(cli_mod.cli, ["query", "probe-capture"])
    finally:
        cli_mod.query.commands.pop("probe-capture", None)
    assert out.exit_code == 0, out.output
    assert '"inner":"query domains-overview"' in out.stdout and out.stdout.count('"command"') == 1


def test_the_staleness_check_is_skipped_for_a_captured_step(monkeypatch):
    import artmind.vault_sync as vault_sync

    calls = []
    monkeypatch.delenv("ARTMIND_NO_STALENESS_CHECK", raising=False)
    monkeypatch.setattr(vault_sync, "query_staleness_warning", lambda *a, **k: calls.append(1))
    _overview(monkeypatch)
    capture.invoke_step(["query", "domains-overview", "--compact"], "s1")
    assert calls == []
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_capture.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.query_run'`.

- [ ] **Step 3: Write the module**

`artmind/query_run/__init__.py`:

```python
"""`artmind query run`: the plan executor and its evidence bundle (spec 2026-11-07 P3)."""
```

`artmind/query_run/capture.py`:

```python
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
```

- [ ] **Step 4: Hook `_echo_json` and the staleness callback**

In `artmind/cli.py`, in `_echo_json`, insert right after `observe_output_full = payload`:

```python
    from artmind.query_run import capture as _capture

    box = _capture.active()
    if box is not None:  # a step of `query run` (P3 §3.3): hand over the whole payload, print nothing
        text = json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))
        box["payload"], box["chars"] = json.loads(text), len(text)
        observe_output(observe_output_full, payload_chars=len(text), shown_chars=None, handle=None)
        return
```

In `_warn_if_vault_stale`, make the first lines:

```python
    if os.environ.get("ARTMIND_NO_STALENESS_CHECK"):
        return
    from artmind.query_run import capture as _capture

    if _capture.active() is not None:  # a step of `query run`: the run itself checks once
        return
```

The result callback runs after the subcommand returns, while `capturing(box)` is still set, so the check above sees the box.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_capture.py -q`
Expected: PASS (11 tests).

- [ ] **Step 6: Commit**

```bash
git add artmind/query_run/__init__.py artmind/query_run/capture.py artmind/cli.py test/test_query_run_capture.py
git commit -m "feat(query-run): capture mode runs a step as a Click command and catches its JSON (P3 S1)"
```

### Task 2: Step facts, with `step_documents` moved out of reach

**Files:**
- Create: `artmind/query_run/facts.py`
- Modify: `artmind/query_plan/reach.py` (the top through `step_documents`, plus `needle_answers_alone`)
- Test: `test/test_query_run_facts.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Step facts: every step predicate from a payload (P3 spec §3.4). Invented payloads."""

import math

from artmind.query_plan import program as program_mod
from artmind.query_run import facts


def _passage(doc, rel, text="t"):
    return {"rank": 1, "passage_id": f"p-{doc}-{rel}", "chunk_id": f"c-{doc}", "text": text, "relevance": rel,
            "doc": {"id": doc, "title": doc.upper(), "domain": "bank", "date": None, "status": "current"}}


VECTOR = {"command": "query vector-text", "rows": [_passage("d1", 0.9), _passage("d1", 0.5), _passage("d2", 0.4)]}
VECTOR_ARGV = ["query", "vector-text", "--shape", "brief", "--domain", "bank", "--topK", "10", "fees", "--compact"]
SURVEY = {"command": "query survey", "documents": [
    {"rank": 1, "doc_id": "s1", "title": "Rates", "gist": "g"}, {"rank": 2, "doc_id": "s2", "title": "Other"}]}
PATTERN7 = {"command": "query graph pattern7", "rows": [{"id": "e1", "documents": [{"id": "d9", "title": "X"}]}],
            "rows_total": 80, "truncated": True}


def test_every_predicate_name_is_present():
    got = facts.step_facts(VECTOR, VECTOR_ARGV)
    assert set(program_mod.PREDICATE_NAMES) <= set(got)


def test_vector_text_facts_follow_the_needle_rule():
    got = facts.step_facts(VECTOR, VECTOR_ARGV)
    assert got["rows"] == 3 and got["rows_total"] == 3 and got["truncated"] is False
    assert got["distinct_documents"] == 2 and got["docs_cited"] == 2
    assert got["score_drop"] == 0.4 and got["max_relevance"] == 0.9
    assert got["rows_below_k"] == 1  # 3 rows < topK 10


def test_survey_rows_are_documents_not_citations():
    got = facts.step_facts(SURVEY, ["query", "survey", "q"])
    assert got["rows"] == 2 and got["distinct_documents"] == 2 and got["docs_cited"] == 0
    assert got["score_drop"] is None and got["max_relevance"] is None


def test_envelope_totals_win():
    got = facts.step_facts(PATTERN7, ["query", "graph", "pattern7", "--limit", "50"])
    assert (got["rows"], got["rows_total"], got["truncated"]) == (1, 80, True)


def test_entity_docs_from_entity_context():
    payload = {"command": "query entity-context", "rows": [{"entity": {"id": "e1"}, "source_documents_total": 4,
                                                             "chunks": [_passage("d1", None)]}]}
    got = facts.step_facts(payload, ["query", "entity-context"])
    assert got["entity_docs"] == 4 and got["docs_cited"] == 1


def test_an_empty_or_missing_payload_reads_as_nothing():
    got = facts.step_facts(None, [])
    assert got["rows"] == 0 and got["truncated"] is False and got["distinct_documents"] == 0


def test_merge_unions_documents():
    a = facts.step_facts(VECTOR, VECTOR_ARGV)
    b = facts.step_facts(SURVEY, ["query", "survey", "q"])
    m = facts.merge([a, b])
    assert m["rows"] == 5 and m["distinct_documents"] == 4 and m["docs_cited"] == 2
    assert facts.merge([])["rows"] == 0


def test_public_drops_the_private_sets():
    assert not any(k.startswith("_") for k in facts.public(facts.step_facts(VECTOR, VECTOR_ARGV)))


def test_evaluate_qualified_and_default_nodes():
    by_node = {"needle": facts.step_facts(VECTOR, VECTOR_ARGV), "s1": facts.step_facts(PATTERN7, ["query"])}
    pred = program_mod.parse_predicate("needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD", "t")
    assert facts.evaluate(pred, by_node, "u") is False  # clear winner (0.4 >= 0.03) in 2 < 7 documents
    assert facts.evaluate(program_mod.parse_predicate("truncated", "t"), by_node, "s1") is True
    assert facts.evaluate(program_mod.parse_predicate("rows == 0", "t"), by_node, "missing") is True


def test_a_missing_score_drop_reads_as_minus_infinity():
    one = {"command": "query vector-text", "rows": [_passage("d1", 0.9)]}
    by_node = {"needle": facts.step_facts(one, VECTOR_ARGV)}
    assert by_node["needle"]["score_drop"] is None
    pred = program_mod.parse_predicate("needle.score_drop < NEEDLE_MARGIN", "t")
    assert facts.evaluate(pred, by_node, "u") is True
    assert facts.number(None, "score_drop") == -math.inf


def test_doc_key_prefers_ids():
    assert facts.doc_key({"id": "d1", "path": "a.md"}) == "d1"
    assert facts.doc_key({"path": "a.md"}) == "a.md"
    assert facts.doc_key({}) is None
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_facts.py -q`
Expected: FAIL with `ImportError` (`facts` does not exist).

- [ ] **Step 3: Write `artmind/query_run/facts.py`**

```python
"""Step facts (P3 spec §3.4): every step-predicate name, computed from one step's payload.

The single home of these rules: the executor, `plan replay --reach` and `plan replay --run` all
read them here. Private sets (`_docs`, `_cited`) carry document keys so `merge` can union them;
`public` drops them before anything is printed.
"""

from __future__ import annotations

import math

from artmind.cli_conventions import LIST_KEY

DOC_KEYS = ("document", "doc", "documents", "source_documents")
TABLE_KEY = "source_tables"   # text2sql: [{table, source_file}]
_FALLBACK_LISTS = ("rows", "results", "documents", "findings", "observations")
_K_OPTIONS = ("--topK", "--limit")


# ── documents in a payload (moved from query_plan/reach.py) ─────────────────

def step_documents(payload) -> list[dict]:
    """Every document dict in a step payload: values under `DOC_KEYS` (a dict or a list of dicts) and any
    dict with a `doc_id`, at any depth. Each is normalised to {path, source_path, name, id, title}."""
    found: list[dict] = []

    def add(d: dict) -> None:
        ref = {"path": d.get("path"), "source_path": d.get("source_path"), "name": d.get("name"),
               "id": d.get("id") if "id" in d else d.get("doc_id"), "title": d.get("title")}
        if any(v for v in ref.values()):
            found.append(ref)

    def walk(node, key=None) -> None:
        if isinstance(node, dict):
            if key == TABLE_KEY:
                if node.get("source_file"):
                    found.append({"path": node["source_file"], "source_path": None, "name": None,
                                  "id": None, "title": None})
                return
            if key in DOC_KEYS or "doc_id" in node:
                add(node)
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(payload)
    return found


def doc_key(doc: dict) -> str | None:
    for field in ("id", "path", "source_path", "name", "title"):
        value = doc.get(field)
        if value:
            return str(value)
    return None


def main_list(payload) -> list:
    if not isinstance(payload, dict):
        return []
    declared = LIST_KEY.get(str(payload.get("command") or ""))
    for key in (declared, *_FALLBACK_LISTS):
        if key and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _row_doc(row: dict) -> str | None:
    """The document key of a row carrying text: brief `doc`, full `document`, or a chunk's `doc_id`."""
    for key in ("doc", "document"):
        if isinstance(row.get(key), dict) and doc_key(row[key]):
            return doc_key(row[key])
    chunk = row.get("chunk") if isinstance(row.get("chunk"), dict) else {}
    return row.get("doc_id") or chunk.get("doc_id")


def _cited(payload) -> set[str]:
    """Documents of rows that carry text (passages, chunks), at any depth of the main list."""
    out: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            chunk = node.get("chunk") if isinstance(node.get("chunk"), dict) else {}
            if isinstance(node.get("text"), str) or isinstance(chunk.get("text"), str):
                key = _row_doc(node)
                if key:
                    out.add(str(key))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(main_list(payload))
    return out


def _option_int(argv, names) -> int | None:
    argv = list(argv or [])
    for i, token in enumerate(argv[:-1]):
        if token in names:
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
    return None


def _relevances(rows) -> list:
    return [r.get("relevance") for r in rows[:2] if isinstance(r, dict)]


def empty() -> dict:
    return {"rows": 0, "rows_total": 0, "truncated": False, "distinct_documents": 0, "docs_cited": 0,
            "max_relevance": None, "score_drop": None, "payload_chars": 0, "persisted": False,
            "entity_docs": 0, "rows_below_k": 0, "twins": 0, "stale": False, "view_status": "",
            "history_matches": 0, "concentration": 0, "level_agreement": 0, "_docs": [], "_cited": []}


def step_facts(payload, argv) -> dict:
    out = empty()
    if not isinstance(payload, dict):
        return out
    rows = main_list(payload)
    out["rows"] = len(rows)
    total = payload.get("rows_total")
    out["rows_total"] = total if isinstance(total, int) else len(rows)
    out["truncated"] = bool(payload.get("truncated"))
    docs = {k for k in (doc_key(d) for d in step_documents(payload)) if k}
    cited = _cited(payload)
    out["_docs"], out["_cited"] = sorted(docs), sorted(cited)
    out["distinct_documents"], out["docs_cited"] = len(docs), len(cited)
    rel = _relevances(rows)
    if rel and isinstance(rel[0], (int, float)):
        out["max_relevance"] = rel[0]
    if len(rel) == 2 and all(isinstance(v, (int, float)) for v in rel):
        out["score_drop"] = round(rel[0] - rel[1], 4)
    if payload.get("command") == "query vector-text":  # the B2 needle rule reads the first ten rows
        from artmind.retrieval_eval import metrics

        stats = metrics.needle_stats(rows)
        out["distinct_documents"], out["score_drop"] = stats["distinct_documents"], stats["score_drop"]
    out["payload_chars"] = len(str(payload))
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    if payload.get("command") == "query entity-context":
        out["entity_docs"] = int(first.get("source_documents_total") or 0)
    elif payload.get("command") == "query entity-history":
        out["entity_docs"] = len({r.get("doc_id") for r in rows if isinstance(r, dict) and r.get("doc_id")})
    k = _option_int(argv, _K_OPTIONS)
    out["rows_below_k"] = int(k is not None and len(rows) < k)
    out["stale"] = bool(payload.get("stale"))
    out["view_status"] = str(payload.get("status") or "")
    out["history_matches"] = len(payload.get("history_matches") or [])
    return out


def merge(items: list[dict]) -> dict:
    """Facts of a `par`, `map` or `pick`: counts summed, documents unioned, flags or-ed."""
    out = empty()
    if not items:
        return out
    docs = set().union(*(set(f.get("_docs") or ()) for f in items))
    cited = set().union(*(set(f.get("_cited") or ()) for f in items))
    out.update(
        rows=sum(f["rows"] for f in items), rows_total=sum(f["rows_total"] for f in items),
        truncated=any(f["truncated"] for f in items), distinct_documents=len(docs), docs_cited=len(cited),
        payload_chars=sum(f["payload_chars"] for f in items), entity_docs=max(f["entity_docs"] for f in items),
        rows_below_k=int(all(f["rows_below_k"] for f in items)), stale=any(f["stale"] for f in items),
        history_matches=sum(f["history_matches"] for f in items), _docs=sorted(docs), _cited=sorted(cited),
    )
    rels = [f["max_relevance"] for f in items if isinstance(f["max_relevance"], (int, float))]
    out["max_relevance"] = max(rels) if rels else None
    return out


def public(f: dict) -> dict:
    return {k: v for k, v in f.items() if not k.startswith("_")}


_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b}


def number(value, name: str):
    """A missing numeric fact: `score_drop` reads as minus infinity (no clear winner, B2), others as 0."""
    if value is None:
        return -math.inf if name == "score_drop" else 0
    return value


def evaluate(pred, by_node: dict, default_node: str) -> bool:
    """A step predicate (`program.parse_predicate`) over facts by node id; an unqualified name reads
    `default_node`. A node that has no facts (skipped, failed) reads as `empty()`."""
    from artmind.query_plan import program as program_mod

    def ev(node) -> bool:
        if isinstance(node, program_mod.Cmp):
            have = (by_node.get(node.node or default_node) or empty()).get(node.name)
            kind = program_mod.PREDICATE_TYPES.get(node.name)
            if kind == "num":
                have = number(have, node.name)
            elif kind == "bool":
                have = bool(have)
            else:
                have = str(have or "")
            return bool(_OPS[node.op](have, node.value))
        if isinstance(node, program_mod.Not):
            return not ev(node.operand)
        if isinstance(node, program_mod.Or):
            return any(ev(o) for o in node.operands)
        return all(ev(o) for o in node.operands)

    return ev(pred.ast)
```

- [ ] **Step 4: Point reach at the moved code**

In `artmind/query_plan/reach.py`, delete the two constants `DOC_KEYS` and `TABLE_KEY` and the whole `step_documents` function. Put this import after `from artmind.query_plan import program as program_mod`:

```python
from artmind.query_run.facts import DOC_KEYS, TABLE_KEY, step_documents  # noqa: F401  (re-exported)
```

Replace the body of `needle_answers_alone` with:

```python
    if not isinstance(needle_payload, dict):
        return False
    from artmind.query_run import facts as facts_mod

    by_node = {"needle": facts_mod.step_facts({**needle_payload, "command": "query vector-text"}, [])}
    return not facts_mod.evaluate(rule["when"], by_node, "needle")
```

Keep its docstring. The behaviour is the same: `step_facts` uses `metrics.needle_stats` for vector-text, and `evaluate` reads a missing `score_drop` as minus infinity.

- [ ] **Step 5: Run the facts tests and the existing reach tests**

Run: `uv run --group dev pytest test/test_query_run_facts.py test/test_query_plan_reach.py -q`
Expected: PASS (all tests in both files; reach's results are unchanged).

- [ ] **Step 6: Commit**

```bash
git add artmind/query_run/facts.py artmind/query_plan/reach.py test/test_query_run_facts.py
git commit -m "feat(query-run): step facts in one module; reach reads them (P3 S1)"
```

### Task 3: S1 gate

- [ ] **Step 1: Run the full suite**

Run: `uv run --group dev pytest -q test/`
Expected: all pass. A failure in `test_cli_conventions.py` or `test_traces_span.py` means the `_echo_json` branch changed something outside capture: fix it before going on.

- [ ] **Step 2: Live reach parity (needs the banking vault and its Neo4j)**

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind plan replay --classifier both --reach --compact
```

Expected: `reports.decision.totals.reached` = 76 and `reports.lexical.totals.reached` = 61 of 101 (the B3–B9 numbers). Any other number is a regression: stop and report it.

---

# Slice S2: the executor over a plan payload

### Task 4: Planner adds `replacements` and `needs_unmet`

**Files:**
- Modify: `artmind/query_plan/planner.py` (`_node_json`, around line 1404 where `program_json`/`guards_json` are built, and the returned dict)
- Test: `test/test_query_run_planner_payload.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The plan payload carries what the executor needs (P3 spec §3.5): replacement programs and unmet needs."""

import pytest

from artmind.query_plan import planner, program as prog, slots
from test_query_plan_planner import _plan, world  # noqa: F401  (fixture and helper)


@pytest.fixture
def common_words(monkeypatch):
    monkeypatch.setattr(slots, "rare_term", lambda *a, **k: False)


def test_a_replace_guard_ships_the_rendered_replacement(world, common_words):
    world["views"] = []
    result = _plan("Did I visit Lakeport again?")
    assert [g["then"] for g in result["guards"]] == [{"kind": "replace", "route": "search"}]  # unchanged
    rep = result["replacements"]["search"]
    first = rep["program"][0]
    assert first["kind"] == "par" and [c["id"] for c in first["children"]] == ["needle", "wide"]
    assert all(c["filled"] for c in first["children"])
    assert [g["then"]["kind"] for g in rep["guards"]] == ["page", "escalate"]


def test_search_is_always_a_replacement_except_for_search_itself(world, common_words):
    world["views"] = []
    assert "search" in _plan("Did I visit Lakeport again?")["replacements"]
    assert "search" not in _plan("How do I renew a passport?")["replacements"]


def test_an_unmet_need_is_marked_on_the_node():
    node = prog.Node("s1", "run", run="artmind query insights list --domain {domain} --compact",
                     needs=("insights_snapshot",))
    ctx = {"domain": "journal", "domain_list": ("journal",), "snapshot_domains": frozenset()}
    assert planner._node_json(node, ctx)["needs_unmet"] is True
    met = {**ctx, "snapshot_domains": frozenset({"journal"})}
    assert "needs_unmet" not in planner._node_json(node, met)
```

The second test assumes the lexical planner routes "How do I renew a passport?" to `search` (trigger `how do i`). If the fixture world routes it elsewhere, pick a question from `test_query_plan_planner.py` that the existing tests already show as `search`.

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_planner_payload.py -q`
Expected: FAIL with `KeyError: 'replacements'` and `KeyError: 'needs_unmet'`.

- [ ] **Step 3: Implement**

In `_node_json`, for a run node, after `out.update(run=rendered or node.run, filled=rendered is not None)`:

```python
        if node.needs and not _needs_met(node.needs, context):
            out["needs_unmet"] = True
```

In the per-domain branch at the top of `_node_json`, build each child as below, so each domain checks its own snapshot:

```python
               "children": [{"id": f"{node.id}.{i}", "kind": "run", "run": routes_mod.render(node.run, c) or node.run,
                             "filled": routes_mod.render(node.run, c) is not None,
                             **({"needs_unmet": True} if node.needs and not _needs_met(node.needs, c) else {})}
                            for i, c in enumerate(per_domain, 1)]}
```

At about line 1164, after `guards_json: list[dict] = []`, add `replacements_json: dict = {}`. In the `elif route_obj is not None:` branch, after `guards_json = ...`:

```python
            by_id = {r.id: r for r in all_routes}
            wanted = {g.then.route for g in used.guards if g.then.kind == "replace" and g.then.route}
            if route_obj.id != "search":
                wanted.add("search")  # an unfilled program runs the search instead (P3 §3.5)
            for rid in sorted(wanted):
                rep = by_id.get(rid)
                if rep is None:
                    continue
                rep_choice = rep.programs[-1]
                replacements_json[rid] = {"program": [_node_json(n, context, {}) for n in rep_choice.program],
                                          "guards": [_guard_json(g) for g in rep_choice.guards]}
```

In the returned dict, add `"replacements": replacements_json,` right after `"guards": guards_json,`. Non-retrieval and view plans keep `{}`.

- [ ] **Step 4: Run the new tests and the planner suite**

Run: `uv run --group dev pytest test/test_query_run_planner_payload.py test/test_query_plan_*.py -q`
Expected: PASS. If a test compares a whole plan dict, add `"replacements": {}`, or the expected replacement, to its expectation and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/planner.py test/test_query_run_planner_payload.py
git commit -m "feat(query-plan): plans carry rendered replacement programs and unmet step needs (P3 S2)"
```

### Task 5: The executor

**Files:**
- Create: `artmind/query_run/executor.py`
- Test: `test/test_query_run_executor.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The executor over plan payloads (P3 spec §3.5–§3.8). Fake steps, invented payloads, no Neo4j."""

from artmind.query_run import executor
from artmind.query_run.capture import StepResult

NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 fees --compact"
WIDE = "artmind query survey --shape brief --domain bank 'fees?' --compact"
P7 = "artmind query graph pattern7 --domain bank --searchTerm fees --limit 50 --shape brief --compact"


def _passage(doc, rel):
    return {"passage_id": f"p-{doc}-{rel}", "chunk_id": f"c-{doc}", "text": "t", "relevance": rel,
            "doc": {"id": doc, "title": doc}}


def _needle(pair, docs=3):
    rows = [_passage(f"d{i % docs}", 0.5) for i in range(10)]
    rows[0]["relevance"], rows[1]["relevance"] = pair
    return {"command": "query vector-text", "rows": rows}


SURVEY = {"command": "query survey", "documents": [{"doc_id": "s1", "title": "Rates"}]}


class Steps:
    """step_fn: payload by the command's third word (vector-text, survey, graph...) or an exception-like exit."""

    def __init__(self, by_word):
        self.by_word, self.calls = by_word, []

    def __call__(self, argv, step_id):
        self.calls.append((step_id, list(argv)))
        value = self.by_word[argv[1] if argv[1] != "graph" else argv[2]]
        if isinstance(value, int):
            return StepResult(step_id, list(argv), value, "boom", None, 1.0, 0)
        return StepResult(step_id, list(argv), 0, None, value, 1.0, 100)


def _search_plan(guards=None):
    return {"program": [
        {"id": "p", "kind": "par", "children": [{"id": "needle", "kind": "run", "run": NEEDLE, "filled": True},
                                                  {"id": "wide", "kind": "run", "run": WIDE, "filled": True}]},
        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []},
         "when": "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"}],
        "guards": guards if guards is not None else [{"on": "u", "if": "distinct_documents == 0", "then": {"kind": "escalate"}}],
        "route": {"id": "search"}, "suggested_steps": [], "fallbacks": [], "replacements": {}}


def test_par_runs_both_and_pick_unions():
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert [c[0] for c in steps.calls] == ["needle", "wide"]
    assert ex.facts["u"]["distinct_documents"] == 4 and ex.escalate is None
    assert ex.answer_ids == ["needle", "wide"] and ex.executed == 2


def test_a_clear_needle_short_circuits_the_survey():
    steps = Steps({"vector-text": _needle((0.9, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert [c[0] for c in steps.calls] == ["needle"]
    assert next(p for p in ex.path if p["id"] == "wide")["skipped"] == "short_circuit"
    assert ex.answer_ids == ["needle"]


def test_a_guard_escalates_with_its_predicate():
    steps = Steps({"vector-text": {"command": "query vector-text", "rows": []}, "survey": {"command": "query survey", "documents": []}})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert ex.escalate["reason"] == "guard" and ex.escalate["node"] == "u"
    assert ex.escalate["predicate"] == "distinct_documents == 0"


def test_replace_runs_the_replacement_once():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True}],
            "guards": [{"on": "s1", "if": "rows == 0", "then": {"kind": "replace", "route": "search"}}],
            "route": {"id": "existence"}, "suggested_steps": [], "fallbacks": [],
            "replacements": {"search": _search_plan()}}
    steps = Steps({"pattern7": {"command": "query graph pattern7", "rows": []},
                   "vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by == "search" and [c[0] for c in steps.calls] == ["s1", "needle", "wide"]
    assert "s1" not in ex.results and ex.escalate is None
    assert any(p.get("kind") == "replace" for p in ex.path)


def test_an_unfilled_program_runs_the_search_instead():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query entity-context --entityId {entity_id}", "filled": False}],
            "guards": [], "route": {"id": "entity_profile"}, "suggested_steps": [], "fallbacks": [],
            "replacements": {"search": _search_plan()}}
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by == "search" and ex.path[0]["reason"] == "unfilled"


def test_an_optional_unfilled_node_is_skipped_quietly():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True},
                        {"id": "s2", "kind": "run", "run": "x {class}", "filled": False, "optional": True}],
            "guards": [], "route": {"id": "inventory"}, "replacements": {}}
    steps = Steps({"domains-overview": {"command": "query domains-overview", "rows": [{"domain": "bank"}]}})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by is None and ex.escalate is None
    assert next(p for p in ex.path if p["id"] == "s2")["skipped"] == "unfilled"


def test_a_when_on_a_run_node_reads_earlier_facts():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True},
                        {"id": "s2", "kind": "run", "run": NEEDLE, "filled": True, "when": "s1.rows == 0"}],
            "guards": [], "route": {"id": "path"}, "replacements": {}}
    steps = Steps({"pattern7": {"command": "query graph pattern7", "rows": [{"id": "e1"}]}, "vector-text": _needle((0.5, 0.4))})
    ex = executor.execute(plan, step_fn=steps)
    assert [c[0] for c in steps.calls] == ["s1"]
    assert next(p for p in ex.path if p["id"] == "s2")["skipped"] == "when"


def test_a_page_node_reruns_with_a_larger_limit():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True},
                        {"id": "more", "kind": "page", "page": {"until": "rows_total", "k": None, "from": "s1"}, "when": "s1.truncated"}],
            "guards": [{"on": "more", "if": "truncated", "then": {"kind": "escalate"}}], "route": {"id": "enumeration"}, "replacements": {}}
    first = {"command": "query graph pattern7", "rows": [{"id": f"e{i}"} for i in range(50)], "rows_total": 120, "truncated": True}
    full = {"command": "query graph pattern7", "rows": [{"id": f"e{i}"} for i in range(120)], "rows_total": 120, "truncated": False}
    calls = iter([first, full])

    def step_fn(argv, step_id):
        return StepResult(step_id, list(argv), 0, None, next(calls), 1.0, 10)

    ex = executor.execute(plan, step_fn=step_fn)
    assert ex.results["s1"].argv[ex.results["s1"].argv.index("--limit") + 1] == "120"
    assert ex.facts["more"]["truncated"] is False and ex.escalate is None and ex.executed == 2


def test_a_failed_step_continues_and_all_failed_is_step_error():
    steps = Steps({"vector-text": 1, "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps)
    assert ex.results["needle"].exit_code == 1 and ex.escalate is None  # the survey still produced evidence
    steps = Steps({"vector-text": 3, "survey": 3})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps)
    assert ex.escalate["reason"] == "step_error" and ex.exit_code == 3


def test_the_step_limit_stops_the_run():
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps, max_steps=1)
    assert ex.executed == 1 and ex.escalate["reason"] == "limit"


def test_the_deadline_stops_before_the_next_step():
    ticks = iter([0.0, 0.0, 50.0, 50.0, 50.0])
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps, deadline_s=20, clock=lambda: next(ticks))
    assert ex.executed == 1 and ex.escalate["reason"] == "deadline" and ex.partial is True


def test_no_program_is_no_lookup():
    ex = executor.execute({"program": [], "answer_from": "previous", "action": None}, step_fn=Steps({}))
    assert ex.escalate == {"reason": "no_lookup", "answer_from": "previous", "action": None, "suggested": []}


def test_a_map_runs_each_expanded_item_with_unique_ids():
    do = {"id": "version", "kind": "run", "run": "artmind query graph pattern10 --documentName {doc}", "filled": True}
    exp = [{**do, "run": f"artmind query graph pattern10 --documentName d{i} --compact"} for i in (1, 2)]
    plan = {"program": [{"id": "versions", "kind": "map", "map": {"over": "family_documents", "as": "doc", "max": 10,
                                                                   "items": ["d1", "d2"], "do": do, "expanded": exp}}],
            "guards": [], "route": {"id": "document"}, "replacements": {}}
    steps = Steps({"pattern10": {"command": "query graph pattern10", "rows": [{"chunk": {"id": "c", "text": "t", "doc_id": "d"}}]}})
    ex = executor.execute(plan, step_fn=steps)
    assert [c[0] for c in steps.calls] == ["version.1", "version.2"] and ex.facts["versions"]["rows"] == 2


def test_an_unmet_need_skips_the_node():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query insights list --domain j --compact", "filled": True,
                         "needs_unmet": True}], "guards": [], "route": {"id": "insights"}, "replacements": {}}
    ex = executor.execute(plan, step_fn=Steps({}))
    assert ex.path[0]["skipped"] == "needs" and ex.escalate["reason"] == "no_evidence"


def test_unsupported_guard_actions_escalate():
    plan = _search_plan(guards=[{"on": "u", "if": "rows >= 0", "then": {"kind": "retry_with", "slots": {"x": "y"}}}])
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.escalate["reason"] == "guard" and ex.escalate["action"] == "retry_with"


def test_a_refused_command_is_recorded_not_run():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind ingest sync x", "filled": True}],
            "guards": [], "route": {"id": "x"}, "replacements": {}}
    ex = executor.execute(plan, step_fn=Steps({}))
    assert ex.results["s1"].error == "not_allowed" and ex.executed == 0
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_executor.py -q`
Expected: FAIL with `ImportError` (`executor` does not exist).

- [ ] **Step 3: Write `artmind/query_run/executor.py`**

```python
"""Run a plan's program (P3 spec §3.5–§3.8).

Input is the plan payload as `planner.plan` returns it: `program` (node JSON with every plan-time
slot rendered), `guards`, `replacements` (rendered programs of the routes a guard may replace with,
plus `search`), `suggested_steps`, `fallbacks`. The planner fills; the executor runs and never
re-reads routes.yaml.

`par` runs its children in order (sequential in P3, spec §13 Q3). Guards are evaluated after their
`on` node, first match wins, each at most once. A `replace` runs the replacement program once; a
replacement cannot replace again. Guard actions no packaged route uses yet (`run`, `retry_with`,
`widen_domains`) escalate.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from artmind.query_plan import program as program_mod
from artmind.query_run import capture, facts as facts_mod

DEFAULT_MAX_STEPS = 12
DEFAULT_DEADLINE_S = 20.0
#: Largest `--limit` a page node may re-run a command with (graph_query.PATTERN7_MAX_LIMIT; a test pins it).
PAGE_LIMITS = {"query graph pattern7": 500}
_UNSUPPORTED = ("run", "retry_with", "widen_domains")


@dataclass
class Execution:
    results: dict = field(default_factory=dict)     # run node id -> StepResult (ran or refused)
    facts: dict = field(default_factory=dict)       # node id -> facts
    path: list = field(default_factory=list)
    escalate: dict | None = None
    executed: int = 0
    replaced_by: str | None = None
    answer_ids: list = field(default_factory=list)
    partial: bool = False
    exec_ms: float = 0.0
    unfilled: bool = False

    @property
    def exit_code(self) -> int:
        ran = [r for r in self.results.values() if r.error != "not_allowed"]
        return 3 if ran and all(r.exit_code == 3 for r in ran) else 0


class _Stop(Exception):
    def __init__(self, reason: str, **extra):
        super().__init__(reason)
        self.reason, self.extra = reason, extra


class _Replace(Exception):
    def __init__(self, route: str):
        super().__init__(route)
        self.route = route


def _pred(text: str):
    return program_mod.parse_predicate(text, "query run")


def _cut(text, n=300) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


class _Run:
    def __init__(self, plan, step_fn, max_steps, deadline_s, clock):
        self.plan, self.step_fn, self.max_steps, self.deadline_s, self.clock = plan, step_fn, max_steps, deadline_s, clock
        self.ex = Execution()
        self.start = clock()
        self.fired: set[int] = set()
        self.guards: list = []
        self.program: list = []

    # ── walking ──────────────────────────────────────────────────────────
    def nodes(self, nodes) -> None:
        for i, node in enumerate(nodes):
            self.node(node, nodes[i + 1:])

    def node(self, node, rest) -> None:
        if node.get("when") and node.get("kind") != "pick" and not facts_mod.evaluate(_pred(node["when"]), self.ex.facts, node["id"]):
            self.ex.path.append({"id": node["id"], "kind": node.get("kind"), "skipped": "when"})
            return
        kind = node.get("kind")
        if kind == "run":
            self.run(node)
        elif kind == "par":
            self.par(node, rest)
        elif kind == "map":
            self.map(node)
        elif kind == "pick":
            if not self.pick(node):
                return
        elif kind == "page":
            self.page(node["id"], node["page"]["from"], node["page"])
        self.apply_guards(node["id"])

    def run(self, node, argv_override=None) -> None:
        nid = node["id"]
        if node.get("needs_unmet"):
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "needs"})
            return
        if not node.get("filled", True):
            if not node.get("optional"):
                self.ex.unfilled = True
            self.ex.path.append({"id": nid, "kind": "run", "skipped": "unfilled"})
            return
        argv = argv_override or capture.argv_of(node.get("run") or "")
        if argv is None or not capture.allowed(argv):
            self.ex.results[nid] = capture.StepResult(nid, argv or [], 1, "not_allowed", None, 0.0, 0)
            self.ex.path.append({"id": nid, "kind": "run", "cmd": _cut(node.get("run")), "exit_code": 1, "error": "not_allowed"})
            return
        if self.ex.executed >= self.max_steps:
            raise _Stop("limit", node=nid)
        if self.clock() - self.start > self.deadline_s:
            self.ex.partial = True
            raise _Stop("deadline", node=nid)
        result = self.step_fn(argv, nid)
        self.ex.executed += 1
        self.ex.results[nid] = result
        self.ex.facts[nid] = facts_mod.step_facts(result.payload, argv)
        self.ex.path.append({"id": nid, "kind": "run", "cmd": _cut(" ".join(["artmind", *argv])),
                             "exit_code": result.exit_code, "error": result.error, "rows": self.ex.facts[nid]["rows"],
                             "ms": result.ms, "chars": result.chars, "skipped": None,
                             "facts": facts_mod.public(self.ex.facts[nid])})

    def par(self, node, rest) -> None:
        children = node.get("children") or []
        pick = next((n for n in rest if n.get("kind") == "pick" and (n.get("pick") or {}).get("from") == node["id"]
                     and n.get("when")), None)
        ran: list[str] = []
        for i, child in enumerate(children):
            self.node(child, [])
            ran.append(child["id"])
            if pick is not None and i + 1 < len(children):
                pred = _pred(pick["when"])
                if pred.nodes() and pred.nodes() <= set(ran) and not facts_mod.evaluate(pred, self.ex.facts, pick["id"]):
                    for skipped in children[i + 1:]:
                        self.ex.path.append({"id": skipped["id"], "kind": skipped.get("kind"), "skipped": "short_circuit"})
                    break
        self.ex.facts[node["id"]] = facts_mod.merge([self.ex.facts[c["id"]] for c in children if c["id"] in self.ex.facts])

    def map(self, node) -> None:
        spec = node.get("map") or {}
        ids = []
        for i, child in enumerate(spec.get("expanded") or [], 1):
            unique = {**child, "id": f"{child['id']}.{i}"}
            self.node(unique, [])
            ids.append(unique["id"])
        merged = facts_mod.merge([self.ex.facts[i] for i in ids if i in self.ex.facts])
        self.ex.facts[node["id"]] = merged
        do_id = (spec.get("do") or {}).get("id")
        if do_id:
            self.ex.facts[do_id] = merged

    def _leaves(self, node_id) -> list[str]:
        node = next((n for n in _walk(self.program) if n.get("id") == node_id), None)
        if node is None:
            return [node_id] if node_id in self.ex.results else []
        if node.get("kind") == "par":
            return [i for c in node.get("children") or [] for i in self._leaves(c["id"])]
        if node.get("kind") == "map":
            n = len((node.get("map") or {}).get("expanded") or [])
            return [f"{node['map']['do']['id']}.{i}" for i in range(1, n + 1) if f"{node['map']['do']['id']}.{i}" in self.ex.results]
        return [node_id] if node_id in self.ex.results else []

    def pick(self, node) -> bool:
        spec = node.get("pick") or {}
        source = spec.get("from")
        if node.get("when") and not facts_mod.evaluate(_pred(node["when"]), self.ex.facts, node["id"]):
            self.ex.path.append({"id": node["id"], "kind": "pick", "skipped": "when"})
            self.ex.answer_ids = [i for i in self._leaves(source) if self.ex.results[i].exit_code == 0]
            return False
        leaves = [i for i in self._leaves(source) if self.ex.results[i].exit_code == 0]
        items = [self.ex.facts[i] for i in leaves]
        how = spec.get("how")
        if how == "first_ok":
            chosen = next((f for f in items if f["rows"] > 0), facts_mod.empty())
        elif how == "best_of":
            chosen = max(items, key=lambda f: f["rows"], default=facts_mod.empty())
        else:
            chosen = facts_mod.merge(items)
        self.ex.facts[node["id"]] = chosen
        self.ex.answer_ids = leaves
        self.ex.path.append({"id": node["id"], "kind": "pick", "how": how, "rows": chosen["rows"]})
        return True

    def page(self, node_id, source_id, spec) -> None:
        src = self.ex.results.get(source_id)
        if src is None or src.exit_code != 0:
            self.ex.path.append({"id": node_id, "kind": "page", "skipped": "no_source"})
            return
        src_facts = self.ex.facts[source_id]
        target = spec.get("k") if spec.get("until") == "k" else src_facts["rows_total"]
        argv = list(src.argv)
        ceiling = PAGE_LIMITS.get(capture.command_key(argv))
        current = int(argv[argv.index("--limit") + 1]) if "--limit" in argv[:-1] else None
        if ceiling is None or current is None or not target or min(int(target), ceiling) <= current:
            self.ex.facts[node_id] = src_facts
            self.ex.path.append({"id": node_id, "kind": "page", "skipped": "page_unsupported"})
            return
        argv[argv.index("--limit") + 1] = str(min(int(target), ceiling))
        self.run({"id": source_id, "kind": "run", "run": " ".join(argv)}, argv_override=argv)
        self.ex.facts[node_id] = self.ex.facts[source_id]

    def apply_guards(self, node_id) -> None:
        for index, guard in enumerate(self.guards):
            if guard.get("on") != node_id or index in self.fired:
                continue
            if node_id not in self.ex.facts:
                continue
            if not facts_mod.evaluate(_pred(guard["if"]), self.ex.facts, node_id):
                continue
            self.fired.add(index)
            then = guard.get("then") or {}
            kind = then.get("kind")
            self.ex.path.append({"id": node_id, "kind": "guard", "if": guard["if"], "then": kind})
            if kind == "escalate":
                raise _Stop("guard", node=node_id, predicate=guard["if"])
            if kind == "replace":
                raise _Replace(then.get("route"))
            if kind == "page":
                self.page(f"{node_id}_page", node_id, then.get("page") or {})
                return
            raise _Stop("guard", node=node_id, predicate=guard["if"], action=kind)

    # ── the whole run ────────────────────────────────────────────────────
    def suggested(self) -> list[str]:
        return [s["cmd"] for s in [*(self.plan.get("suggested_steps") or []), *(self.plan.get("fallbacks") or [])]
                if isinstance(s, dict) and s.get("cmd")]

    def replacement(self, route):
        rep = (self.plan.get("replacements") or {}).get(route)
        return (rep.get("program") or [], rep.get("guards") or []) if rep else None

    def execute(self) -> Execution:
        program = self.plan.get("program") or []
        if not program:
            self.ex.escalate = {"reason": "no_lookup", "answer_from": self.plan.get("answer_from"),
                                "action": self.plan.get("action"), "suggested": self.suggested()}
            return self.ex
        self.program, self.guards = program, list(self.plan.get("guards") or [])
        if _has_unfilled(program) and self.replacement("search") and (self.plan.get("route") or {}).get("id") != "search":
            self.ex.path.append({"id": "-", "kind": "replace", "route": "search", "reason": "unfilled"})
            self.ex.replaced_by = "search"
            self.program, self.guards = self.replacement("search")
        try:
            try:
                self.nodes(self.program)
            except _Replace as rep:
                found = self.replacement(rep.route)
                if not found or self.ex.replaced_by:
                    raise _Stop("guard", action="replace", route=rep.route) from None
                self.ex.path.append({"id": "-", "kind": "replace", "route": rep.route, "reason": "guard"})
                self.ex.replaced_by = rep.route
                self.ex.results.clear()
                self.ex.facts.clear()
                self.ex.answer_ids = []
                self.program, self.guards = found
                self.fired = set()
                self.nodes(self.program)
        except _Replace as rep:  # a replacement tried to replace again
            self.ex.escalate = {"reason": "guard", "action": "replace", "route": rep.route, "suggested": self.suggested()}
        except _Stop as stop:
            self.ex.escalate = {"reason": stop.reason, **stop.extra, "suggested": self.suggested()}
        self.finish()
        return self.ex

    def finish(self) -> None:
        ok = [i for i, r in self.ex.results.items() if r.exit_code == 0 and r.error is None]
        if not self.ex.answer_ids:
            self.ex.answer_ids = ok
        self.ex.exec_ms = round((self.clock() - self.start) * 1000, 1)
        if self.ex.escalate is not None:
            return
        ran = [r for r in self.ex.results.values() if r.error != "not_allowed"]
        if ran and not ok:
            reason = "step_error"
        elif self.ex.unfilled and not ok:
            reason = "unfilled"
        elif not any(self.ex.facts[i]["rows"] for i in self.ex.answer_ids if i in self.ex.facts):
            reason = "no_evidence"
        else:
            return
        self.ex.escalate = {"reason": reason, "suggested": self.suggested()}


def _walk(nodes):
    for n in nodes or []:
        yield n
        yield from _walk(n.get("children"))
        if n.get("kind") == "map":
            yield from _walk([(n.get("map") or {}).get("do") or {}])


def _has_unfilled(program) -> bool:
    return any(n.get("kind") == "run" and n.get("filled") is False and not n.get("optional")
               for n in _walk(program) if n)


def execute(plan: dict, *, step_fn: Callable | None = None, max_steps: int = DEFAULT_MAX_STEPS,
            deadline_s: float = DEFAULT_DEADLINE_S, clock: Callable[[], float] = time.monotonic) -> Execution:
    return _Run(plan, step_fn or capture.invoke_step, max_steps, deadline_s, clock).execute()
```

Two notes for the implementer:
- `test_the_deadline_stops_before_the_next_step` feeds `clock` a fixed sequence: one read at start and one before each step. If your code reads the clock elsewhere, keep the reads in that order or the test's iterator runs out. Do not add other `clock()` calls before `finish`.
- `_walk` over a map yields its `do` template only, not `expanded`. `_has_unfilled` therefore checks the template's `filled`, which is what the planner sets when a map's item slot cannot fill.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_executor.py -q`
Expected: PASS (18 tests). Add one assertion to the file and run it again:

```python
def test_page_limit_matches_the_command():
    from artmind import graph_query
    assert executor.PAGE_LIMITS["query graph pattern7"] == graph_query.PATTERN7_MAX_LIMIT
```

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/executor.py test/test_query_run_executor.py
git commit -m "feat(query-run): executor over plan payloads: nodes, guards, replace, limits, deadline (P3 S2)"
```

### Task 6: `query run --plan` (in-process), proxy rule, command groups

**Files:**
- Modify: `artmind/cli.py` (new command after `query_plan_command`; `COMMAND_GROUPS` "Planning")
- Modify: `artmind/_entry.py` (`_proxiable`)
- Test: `test/test_query_run_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
"""`artmind query run --plan` (P3 S2): executes a given plan in process. Invented plan, no Neo4j."""

import json

from click.testing import CliRunner

from artmind import _entry
from artmind.cli import cli


def _plan_file(tmp_path, cmd="artmind query domains-overview --compact"):
    plan = {"program": [{"id": "s1", "kind": "run", "run": cmd, "filled": True}], "guards": [],
            "route": {"id": "inventory"}, "suggested_steps": [], "fallbacks": [], "replacements": {}}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_run_a_given_plan(tmp_path, monkeypatch):
    from artmind import cli as cli_mod

    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    out = CliRunner().invoke(cli, ["query", "run", "--plan", str(_plan_file(tmp_path)), "--compact"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert payload["command"] == "query run"
    assert [p["id"] for p in payload["path"]] == ["s1"] and payload["path"][0]["exit_code"] == 0
    assert payload["escalate"] is None


def test_a_question_or_a_plan_is_required():
    out = CliRunner().invoke(cli, ["query", "run", "--compact"])
    assert out.exit_code == 1 and "question" in out.output


def test_a_bad_plan_file_is_bad_input(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    out = CliRunner().invoke(cli, ["query", "run", "--plan", str(bad)])
    assert out.exit_code == 1


def test_run_inside_serve_holds_the_lock_once(tmp_path, monkeypatch):
    from artmind import cli as cli_mod
    from artmind import server

    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    body = server.run_cli_args(["query", "run", "--plan", str(_plan_file(tmp_path)), "--compact"])
    assert body["exit_code"] == 0, body
    assert json.loads(body["output"])["path"][0]["exit_code"] == 0


def test_file_flags_run_in_process():
    assert _entry._proxiable(["query", "run", "q"]) is True
    assert _entry._proxiable(["query", "run", "--plan", "p.json"]) is False
    assert _entry._proxiable(["query", "run", "--plan=p.json"]) is False
    assert _entry._proxiable(["query", "run", "--previous", "-", "q"]) is False
    assert _entry._proxiable(["query", "run", "--each", "c.yaml"]) is False
    assert _entry._proxiable(["query", "plan", "--previous", "-", "q"]) is False
    assert _entry._proxiable(["query", "vector-text", "--plan", "x"]) is True
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_cli.py -q`
Expected: FAIL (`No such command 'run'`; `_proxiable` returns True for `--plan`).

- [ ] **Step 3: Update `_entry._proxiable`**

Replace the function in `artmind/_entry.py`:

```python
#: Options naming a file (or stdin) of the caller's shell, which the daemon cannot see: such calls run in-process.
_FILE_FLAGS = {("query", "plan"): ("--previous",), ("query", "run"): ("--previous", "--plan", "--each")}


def _proxiable(args: list[str]) -> bool:
    """`query plan --previous` and `query run --previous/--plan/--each` read a file (relative to this
    shell) or stdin, neither of which the daemon can see: they run in-process."""
    flags = _FILE_FLAGS.get(tuple(args[:2]))
    if not flags:
        return True
    return not any(a == f or a.startswith(f + "=") for a in args for f in flags)
```

- [ ] **Step 4: Add the command**

In `artmind/cli.py`, after `query_plan_command`, add the code below. S3 replaces its output with the bundle, and S4 adds the question path.

```python
def _run_deadline(value: float | None) -> float:
    from artmind.query_run import executor

    if value is not None:
        return value
    raw = os.environ.get("ARTMIND_RUN_DEADLINE_S")
    if not raw:
        return executor.DEFAULT_DEADLINE_S
    try:
        parsed = float(raw)
    except ValueError:
        parsed = -1.0
    if not 1 <= parsed <= 120:
        raise click.ClickException(f"ARTMIND_RUN_DEADLINE_S must be seconds between 1 and 120, got {raw!r}")
    return parsed


def _load_plan_file(plan_file: str) -> dict:
    try:
        text = sys.stdin.read() if plan_file == "-" else Path(plan_file).read_text(encoding="utf-8")
        plan = json.loads(text)
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"cannot read the plan {plan_file!r}: {exc}") from exc
    if not isinstance(plan, dict) or not isinstance(plan.get("program", []), list):
        raise click.ClickException("the plan must be a `query plan` payload (an object with a `program` list)")
    return plan


@query.command("run")
@click.option("--plan", "plan_file", default=None, help="Execute this plan (a `query plan` payload): a file, or - for stdin. Not proxied to serve")
@click.option("--maxSteps", "max_steps", type=click.IntRange(1, 40), default=12, show_default=True,
              help="Most commands one run executes (pages and replacements included)")
@click.option("--deadline", "deadline", type=click.FloatRange(1, 120), default=None,
              help="Seconds for the whole run, checked between steps (default ARTMIND_RUN_DEADLINE_S or 20)")
@compact_option
@click.argument("question", required=False)
def query_run(plan_file: str | None, max_steps: int, deadline: float | None, compact: bool, question: str | None) -> None:
    """Plan a question and execute the plan's program in process: one evidence bundle (P3).

    Read-only: steps are `artmind query ...` commands and read-only `artmind db ...` commands only.
    """
    from artmind.query_run import executor

    if plan_file is None:
        raise click.ClickException("give a question, or --plan FILE")
    plan = _load_plan_file(plan_file)
    ex = executor.execute(plan, max_steps=max_steps, deadline_s=_run_deadline(deadline))
    _echo_json({"path": ex.path, "escalate": ex.escalate, "replaced_by": ex.replaced_by,
                "timing": {"plan_ms": None, "exec_ms": ex.exec_ms}}, compact)
    if ex.exit_code:
        raise SystemExit(ex.exit_code)
```

Check that `sys`, `json` and `Path` are already imported at the top of `cli.py`, and add any that are missing. In `COMMAND_GROUPS["artmind query"]`, change `{"name": "Planning", "commands": ["plan"]}` to `{"name": "Planning", "commands": ["plan", "run"]}`. In the `query` group docstring, after the sentence about `plan`, add: "`run` plans a question and executes its program in process, returning one evidence bundle."

- [ ] **Step 5: Run the tests, the CLI contract tests and the guide test**

Run: `uv run --group dev pytest test/test_query_run_cli.py test/test_cli_conventions.py test/test_cli_guide.py test/test_entry_proxy.py test/test_query_plan_v2.py -q`
Expected: PASS. If `test_cli_conventions.py` reports `query run` as writing (its source check), remove the write-like name it found. Never list `query run` in `_READ_ONLY` to silence it.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py artmind/_entry.py test/test_query_run_cli.py
git commit -m "feat(query-run): query run --plan executes a given plan in process (P3 S2)"
```

### Task 7: S2 gate

- [ ] **Step 1: Full suite**

Run: `uv run --group dev pytest -q test/`
Expected: all pass.

- [ ] **Step 2: Reach parity with the short-circuit, on real plans (banking)**

Save one lexical plan per labelled question and run it through the executor. The script below goes in `/tmp`, not the repo, because it reads vault labels:

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 uv run --project ~/projects/artmind9 python - <<'EOF'
from pathlib import Path
from artmind.query_plan import labels as L, planner, reach
from artmind.query_run import executor, facts
loaded = [lb for lb in L.load_labels(Path("benchmarking/plan_labels.yaml")) if lb.expected_documents]
same = differ = 0
for lb in loaded:
    plan = planner.plan(lb.question, None, classifier="lexical")
    r = reach.reach_label(lb, plan)
    ex = executor.execute(plan)
    docs = [d for i in ex.answer_ids for d in facts.step_documents(ex.results[i].payload)]
    got, _ = reach.reached_documents(list(lb.expected_documents), docs)
    same += len(got) == r["reached"]; differ += len(got) != r["reached"]
    if len(got) != r["reached"]: print("differs:", lb.id, len(got), r["reached"])
print("same", same, "differ", differ)
EOF
```

Expected: `differ 0`, or only labels whose plan was replaced or escalated by a guard (list them with the reason from `ex.path`). Record the output in the PR description: counts and label ids only.

---

# Slice S3: the bundle

### Task 8: Adapters

**Files:**
- Create: `artmind/query_run/adapters.py`
- Test: `test/test_query_run_adapters.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Bundle adapters: one command's payload -> evidence / documents / facts / guides rows (P3 spec §6.2)."""

from artmind.query_run import adapters

DOC = {"id": "d1", "title": "Fees", "domain": "bank", "date": "2026-01-02", "status": "current"}


def test_vector_text_brief_rows_are_passages():
    rows = adapters.adapt({"command": "query vector-text", "rows": [
        {"rank": 1, "passage_id": "p1", "chunk_id": "c1", "text": "fee text", "relevance": 0.7, "doc": DOC}]})
    (ev,) = rows.evidence
    assert ev == {"kind": "passage", "text": "fee text", "passage_id": "p1", "chunk_id": "c1", "relevance": 0.7, "doc": DOC}


def test_vector_text_full_rows_read_chunk_and_document():
    rows = adapters.adapt({"command": "query vector-text", "rows": [
        {"chunk": {"id": "c1", "text": "full", "doc_id": "d1"}, "passage": {"id": "p1"}, "relevance": 0.5,
         "document": {"id": "d1", "name": "fees.md", "_domain": "bank", "valid_from": "2026-01-02", "status": "current"}}]})
    (ev,) = rows.evidence
    assert ev["passage_id"] == "p1" and ev["doc"]["title"] == "fees" and ev["doc"]["date"] == "2026-01-02"


def test_text_is_capped():
    rows = adapters.adapt({"command": "query vector-text", "rows": [{"chunk_id": "c1", "text": "x" * 5000, "doc": DOC}]})
    assert len(rows.evidence[0]["text"]) <= adapters.PASSAGE_CHARS


def test_survey_rows_are_documents_and_their_passage_is_evidence():
    rows = adapters.adapt({"command": "query survey", "topics": [{"slug": "fees", "title": "Fees", "stale": False}],
                           "documents": [
        {"rank": 1, "doc_id": "d1", "title": "Fees", "domain": "bank", "date": None, "status": "current",
         "gist": "g", "gist_source": "passage",
         "passage": {"passage_id": "p1", "chunk_id": "c1", "text": "t", "relevance": 0.6}},
        {"rank": 2, "doc_id": "d2", "title": "Other", "date": None, "status": "current"}]})
    assert [d["doc_id"] for d in rows.documents] == ["d1", "d2"]
    assert rows.evidence[0]["passage_id"] == "p1" and rows.evidence[0]["doc"]["id"] == "d1"
    assert rows.guides == [{"kind": "topic_page", "slug": "fees", "title": "Fees", "stale": False}]


def test_entity_context_brief_gives_a_fact_and_its_chunks():
    rows = adapters.adapt({"command": "query entity-context", "rows": [
        {"entity": {"id": "e1", "name": "Acme", "class": "ORG", "description": "a bank"},
         "connections": [{"id": "e2", "name": "Bob", "class": "PERSON"}] * 30, "connections_total": 30,
         "chunks": [{"rank": 1, "passage_id": None, "chunk_id": "c9", "text": "acme text", "relevance": None, "doc": DOC}],
         "source_documents": [{"id": "d1", "title": "Fees", "date": None}]}]})
    (fact,) = rows.facts
    assert fact["kind"] == "entity" and fact["row"]["entity"]["name"] == "Acme"
    assert len(fact["row"]["connections"]) == adapters.CONNECTIONS and fact["chunk_ids"] == ["c9"]
    assert rows.evidence[0]["chunk_id"] == "c9" and rows.documents[0]["doc_id"] == "d1"


def test_pattern10_chunks_carry_document_status():
    rows = adapters.adapt({"command": "query graph pattern10", "rows": [
        {"document": {"id": "d1", "name": "Policy v1.md", "superseded_by": "d2"},
         "chunk": {"id": "c1", "text": "old", "doc_id": "d1"}}]})
    assert rows.evidence[0]["doc"]["status"] == "superseded" and rows.evidence[0]["kind"] == "chunk"


def test_sql_rows_are_facts():
    rows = adapters.adapt({"command": "query text2sql", "rows": [{"n": 3}],
                           "source_tables": [{"table": "fees", "source_file": "tables/fees.csv"}]})
    assert rows.facts == [{"kind": "sql_row", "row": {"n": 3}, "chunk_ids": []}]


def test_insights_findings_are_guides():
    rows = adapters.adapt({"command": "query insights list", "findings": [{"key": "k1", "title": "T", "score": 0.4,
                                                                           "subjects": [{"_id": "e1"}]}]})
    assert rows.guides[0]["kind"] == "insight" and rows.guides[0]["key"] == "k1"


def test_an_unknown_command_goes_to_facts_with_scalars_only():
    rows = adapters.adapt({"command": "query graph hierarchy", "rows": [{"name": "A", "level": 2, "kids": [1, 2, 3],
                                                                         "blob": {"x": 1}}]})
    assert rows.facts == [{"kind": "row", "row": {"name": "A", "level": 2, "kids_count": 3}, "chunk_ids": []}]


def test_every_routes_yaml_command_has_an_adapter():
    import shlex

    from artmind.query_plan import program as prog, routes
    from artmind.query_run import capture

    for route in routes.load_routes():
        templates = [n.run for c in route.programs for n in prog.run_nodes(c.program)]
        templates += [t for v in route.variants.values() for t in v] + list(route.optional_steps)
        for template in templates:
            argv = shlex.split(routes.render(template, {k: "x" for k in (*routes.ALL_PLACEHOLDERS, "phrase", "twin", "doc")}))[1:]
            key = capture.command_key(argv)
            assert key in adapters.ADAPTERS, (route.id, key)
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_adapters.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `artmind/query_run/adapters.py`**

```python
"""Bundle adapters (P3 spec §6.2): one command's payload -> bundle rows.

`evidence` rows carry text and a document (the only thing an answer may cite); `documents` rows are
brief document lines; `facts` are graph and SQL rows (brief, linked to chunk ids); `guides` steer
(topic pages, insights) and are never cited. A routes test checks that every command routes.yaml can
emit has an adapter here.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from artmind.query_run.facts import main_list

PASSAGE_CHARS = 1200
SCALAR_CHARS = 300
CONNECTIONS = 10


@dataclass
class Rows:
    evidence: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    guides: list = field(default_factory=list)


def _cut(text, n) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def doc_ref(d: dict | None) -> dict:
    """Brief `doc` rows pass through; full `document` rows are reduced to the brief fields."""
    d = d or {}
    title = d.get("title") or (posixpath.splitext(posixpath.basename(str(d["name"])))[0] if d.get("name") else None)
    status = d.get("status") or ("superseded" if d.get("superseded_by") or d.get("superseded") else "current")
    return {"id": d.get("id") or d.get("doc_id"), "title": title, "domain": d.get("domain") or d.get("_domain"),
            "date": d.get("date") or d.get("valid_from"), "status": status}


def _evidence(kind, text, passage_id, chunk_id, relevance, doc) -> dict:
    return {"kind": kind, "text": _cut(text, PASSAGE_CHARS), "passage_id": passage_id, "chunk_id": chunk_id,
            "relevance": relevance, "doc": doc}


def _document_row(d: dict) -> dict:
    return {"doc_id": d.get("doc_id") or d.get("id"), "title": d.get("title"), "domain": d.get("domain"),
            "date": d.get("date"), "status": d.get("status") or "current", "gist": d.get("gist"),
            "gist_source": d.get("gist_source")}


def scalars(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            out[k] = _cut(v, SCALAR_CHARS)
        elif v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, list):
            out[f"{k}_count"] = len(v)
    return out


def _passage_rows(rows, out: Rows) -> None:
    for r in rows:
        if not isinstance(r, dict) or r.get("chat"):
            continue
        if isinstance(r.get("text"), str):  # brief passage row
            out.evidence.append(_evidence("passage" if r.get("passage_id") else "chunk", r["text"], r.get("passage_id"),
                                          r.get("chunk_id"), r.get("relevance"), doc_ref(r.get("doc"))))
            continue
        chunk = r.get("chunk") if isinstance(r.get("chunk"), dict) else {}
        if isinstance(chunk.get("text"), str):  # full row
            passage = r.get("passage") if isinstance(r.get("passage"), dict) else {}
            doc = doc_ref(r.get("document") or r.get("doc") or {"id": chunk.get("doc_id")})
            out.evidence.append(_evidence("passage" if passage.get("id") else "chunk", chunk["text"], passage.get("id"),
                                          chunk.get("id"), r.get("relevance"), doc))


def _vector_text(p, out):
    _passage_rows(main_list(p), out)


def _survey(p, out):
    for d in p.get("documents") or []:
        if not isinstance(d, dict):
            continue
        out.documents.append(_document_row(d))
        passages = [d["passage"]] if isinstance(d.get("passage"), dict) else [x for x in d.get("passages") or [] if isinstance(x, dict)]
        for x in passages:
            out.evidence.append(_evidence("passage", x.get("text"), x.get("passage_id"), x.get("chunk_id"),
                                          x.get("relevance"), doc_ref(d)))
    for t in p.get("topics") or []:
        if isinstance(t, dict):
            out.guides.append({"kind": "topic_page", "slug": t.get("slug"), "title": t.get("title"), "stale": bool(t.get("stale"))})


def _entity_context(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        entity = r.get("entity") or scalars(r.get("entityData") or {})
        chunks = [c for c in r.get("chunks") or [] if isinstance(c, dict)]
        out.facts.append({"kind": "entity", "row": {"entity": entity,
                                                   "connections": list(r.get("connections") or [])[:CONNECTIONS],
                                                   "connections_total": r.get("connections_total")},
                          "chunk_ids": [c.get("chunk_id") or c.get("id") for c in chunks]})
        _passage_rows(chunks, out)
        for d in r.get("source_documents") or []:
            if isinstance(d, dict):
                out.documents.append(_document_row(d))


def _entity_rows(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        out.facts.append({"kind": "entity", "row": scalars(r), "chunk_ids": list(r.get("chunk_ids") or [])})
        for d in r.get("documents") or []:
            if isinstance(d, dict):
                out.documents.append(_document_row(d))


def _chunk_rows(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        chunk = r.get("chunk") if isinstance(r.get("chunk"), dict) else {}
        if isinstance(chunk.get("text"), str):
            doc = doc_ref(r.get("doc") or r.get("document") or {"id": chunk.get("doc_id")})
            out.evidence.append(_evidence("chunk", chunk["text"], None, chunk.get("id"), None, doc))


def _observations(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            row = r.get("observation") if isinstance(r.get("observation"), dict) else r
            out.facts.append({"kind": "observation", "row": scalars(row), "chunk_ids": [row.get("chunk_id")] if row.get("chunk_id") else []})


def _document_history(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "document_history", "row": {"document": doc_ref(r.get("document")),
                                                                  "retired": r.get("retired"), "summary": r.get("summary"),
                                                                  "dropped_keys_count": len(r.get("dropped_keys") or [])},
                              "chunk_ids": []})


def _sql(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "sql_row", "row": scalars(r), "chunk_ids": []})


def _meta(p, out):
    body = {k: v for k, v in p.items() if k not in ("command", "warnings", "next")}
    out.facts.append({"kind": "meta", "row": scalars(body), "chunk_ids": []})


def _insights(p, out):
    for f in p.get("findings") or []:
        if isinstance(f, dict):
            out.guides.append({"kind": "insight", "key": f.get("key"), "title": f.get("title"), "score": f.get("score"),
                               "subjects": [s.get("_id") for s in f.get("subjects") or [] if isinstance(s, dict)][:10]})


def _generic(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "row", "row": scalars(r), "chunk_ids": []})


ADAPTERS = {
    "query vector-text": _vector_text, "query chunks": _chunk_rows, "query survey": _survey,
    "query entity-context": _entity_context, "query entity-resolve": _entity_rows,
    "query graph pattern1": _entity_rows, "query graph pattern2": _entity_rows, "query graph pattern7": _entity_rows,
    "query graph pattern5": _generic, "query graph pattern6": _generic, "query graph hierarchy": _generic,
    "query graph conflicts": _generic, "query graph text2cypher": _generic, "query graph filing-listing": _generic,
    "query graph pattern10": _chunk_rows, "query entity-history": _observations,
    "query document-history": _document_history, "query text2sql": _sql, "db sql": _sql,
    "db bridge": _meta, "db schema": _meta, "query domains-overview": _meta,
    "query insights list": _insights, "query views run": _generic,
}


def adapt(payload) -> Rows:
    out = Rows()
    if not isinstance(payload, dict):
        return out
    ADAPTERS.get(str(payload.get("command") or ""), _generic)(payload, out)
    return out
```

For `query graph pattern10`, also take the document status from `r["document"]` in `_chunk_rows`. That already happens, because `doc_ref` reads `superseded_by`, and `test_pattern10_chunks_carry_document_status` checks it. If a full survey row's passages carry no text, they become evidence with empty text; leave that, since the routes always pass `--shape brief`.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_adapters.py -q`
Expected: PASS. If `test_every_routes_yaml_command_has_an_adapter` names a missing command, add it to `ADAPTERS` (with `_generic` when it returns plain rows) and run again.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/adapters.py test/test_query_run_adapters.py
git commit -m "feat(query-run): adapters from step payloads to bundle rows (P3 S3)"
```

### Task 9: Bundle ranking, assembly, handle; `query page --list`

**Files:**
- Create: `artmind/query_run/bundle.py`
- Modify: `artmind/cli_conventions.py` (`LIST_KEY`)
- Modify: `artmind/cli.py` (`query_run` output; `query_page` `--list`)
- Test: `test/test_query_run_bundle.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The bundle: RRF over steps, assembly to a budget, the full bundle under a handle (P3 spec §6)."""

import json

from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_run import bundle, executor
from artmind.query_run.capture import StepResult

PLAN = {"route": {"id": "search"}, "intent": {"id": "retrieve"}, "program_kind": "cross_document",
        "confidence": {"level": "low", "score": 0.6, "threshold": 0.7, "weakest": "slot_quality"},
        "classifier": {"backend": "lexical"}, "scope": {"domains": ["bank"]}, "gaps": []}


def _doc(i):
    return {"id": f"d{i}", "title": f"Doc {i}", "domain": "bank", "date": None, "status": "current"}


def _passages(prefix, n, text="t"):
    return {"command": "query vector-text",
            "rows": [{"passage_id": f"{prefix}{i}", "chunk_id": f"c{prefix}{i}", "text": text, "relevance": 0.5, "doc": _doc(i)}
                     for i in range(n)]}


def _ex(payloads):
    ex = executor.Execution()
    for sid, payload in payloads.items():
        ex.results[sid] = StepResult(sid, ["query", "vector-text"], 0, None, payload, 1.0, 10)
    ex.answer_ids = list(payloads)
    return ex


def test_rrf_merges_duplicates_and_keeps_every_source():
    a = _passages("p", 3)
    b = {"command": "query vector-text", "rows": [a["rows"][2], *_passages("q", 1)["rows"]]}
    printed, full = bundle.build(PLAN, _ex({"needle": a, "wide": b}), budget=24_000)
    ids = [e["passage_id"] for e in full["evidence"]]
    assert ids[0] == "p2"  # rank 3 in one step and rank 1 in the other beats rank 1 in one
    assert full["evidence"][0]["from"] == ["needle", "wide"] and len(ids) == 4
    assert [e["eid"] for e in full["evidence"]] == ["e1", "e2", "e3", "e4"]
    assert printed["truncated"] is False and printed["handle"] is None


def test_the_cap_is_ten_then_one_per_further_document():
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5, "doc": _doc(i % 12)} for i in range(30)]
    printed, full = bundle.build(PLAN, _ex({"needle": {"command": "query vector-text", "rows": rows}}), budget=200_000)
    assert len(full["evidence"]) == 30
    assert len(printed["evidence"]) == 12  # 10 + documents d10, d11 not yet shown
    assert printed["truncated"] is True and printed["rows_total"] == 30 and printed["handle"]
    assert printed["next"] == f"artmind query page {printed['handle']} --list evidence --from 12"


def test_a_small_budget_keeps_the_top_ranked_evidence():
    payload = _passages("p", 10, text="x" * 1000)
    printed, _ = bundle.build(PLAN, _ex({"needle": payload}), budget=5_000)
    assert len(json.dumps(printed, separators=(",", ":"))) <= 5_000
    assert [e["passage_id"] for e in printed["evidence"]] == [f"p{i}" for i in range(len(printed["evidence"]))]
    assert printed["truncated"] is True


def test_the_fixed_parts_and_documents_are_present():
    survey = {"command": "query survey", "documents": [{"doc_id": "d1", "title": "Doc 1", "status": "current"}]}
    printed, _ = bundle.build(PLAN, _ex({"needle": _passages("p", 2), "wide": survey}), budget=24_000)
    for key in ("bundle_version", "mode", "plan", "scope", "path", "evidence", "documents", "facts", "guides",
                "coverage", "gaps", "escalate", "offer", "timing"):
        assert key in printed
    assert printed["mode"] == "reasoning" and printed["plan"]["route"] == "search"
    assert printed["documents"][0]["evidence"] == ["e2"]  # d1 is the document of the second passage
    assert printed["coverage"]["found"] == 2


def test_the_bundle_budget_setting(monkeypatch):
    monkeypatch.delenv("ARTMIND_BUNDLE_BUDGET", raising=False)
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    assert bundle.bundle_budget() == 24_000
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "9000")
    assert bundle.bundle_budget() == 9000
    monkeypatch.setenv("ARTMIND_BUNDLE_BUDGET", "12000")
    assert bundle.bundle_budget() == 12000


def test_page_list_reads_another_list_of_a_stored_bundle():
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5, "doc": _doc(i % 12)} for i in range(30)]
    survey = {"command": "query survey", "documents": [{"doc_id": f"x{i}", "title": "X"} for i in range(5)]}
    printed, _ = bundle.build(PLAN, _ex({"needle": {"command": "query vector-text", "rows": rows}, "wide": survey}),
                              budget=200_000)
    out = CliRunner().invoke(cli, ["query", "page", printed["handle"], "--list", "documents", "--from", "2", "--compact"])
    assert out.exit_code == 0, out.output
    page = json.loads(out.stdout)
    assert page["rows_from"] == 2 and page["rows_shown"] == len(page["documents"]) > 0
    default = json.loads(CliRunner().invoke(cli, ["query", "page", printed["handle"], "--compact"]).stdout)
    assert len(default["evidence"]) == 30  # LIST_KEY["query run"] = evidence
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_bundle.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `artmind/query_run/bundle.py`**

```python
"""The evidence bundle (P3 spec §6): rows from every step, ranked by RRF, assembled to a budget.

Scores from different commands cannot be compared, so evidence is ranked by reciprocal-rank fusion
(k = 60) over each successful step's own order. Duplicates (same passage id, else chunk id) merge and
list every step they came from. The printed bundle holds the top 10 evidence rows, then one per
further document, then documents, facts and guides while they fit. The full bundle is stored under a
handle whenever anything was left out (`query page <handle> --list evidence|documents|facts|guides`).
"""

from __future__ import annotations

import json
import os

from artmind.query_run import adapters
from artmind.query_run import facts as facts_mod

BUNDLE_VERSION = 1
RRF_K = 60
EVIDENCE_TOP = 10
DEFAULT_BUDGET = 24_000
CMD_CHARS = 300
_RESERVE = 400   # truncated / rows_total / handle / next / a warning line
_LISTS = ("evidence", "documents", "facts", "guides")


def bundle_budget() -> int:
    raw = os.environ.get("ARTMIND_BUNDLE_BUDGET")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value <= 0:
            raise ValueError(f"ARTMIND_BUNDLE_BUDGET must be a positive number of characters, got {raw!r}")
        return value
    from artmind import result_store

    try:
        return result_store.current_budget() or DEFAULT_BUDGET
    except result_store.BudgetError as exc:
        raise ValueError(str(exc)) from None


def _dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _key(row: dict) -> str:
    return str(row.get("passage_id") or row.get("chunk_id") or row.get("text", "")[:80])


def _rank(ex) -> tuple[list, list, list, list]:
    ev: dict[str, dict] = {}
    ev_score: dict[str, float] = {}
    docs: dict[str, dict] = {}
    doc_score: dict[str, float] = {}
    facts: list = []
    guides: list = []
    for sid, result in ex.results.items():
        if result.exit_code != 0 or result.payload is None:
            continue
        rows = adapters.adapt(result.payload)
        for r, row in enumerate(rows.evidence, 1):
            k = _key(row)
            if k not in ev:
                ev[k] = {**row, "from": [], "clause": None}
            if sid not in ev[k]["from"]:
                ev[k]["from"].append(sid)
            ev_score[k] = ev_score.get(k, 0.0) + 1.0 / (RRF_K + r)
            doc = row.get("doc") or {}
            if doc.get("id"):
                docs.setdefault(doc["id"], {"doc_id": doc["id"], "title": doc.get("title"), "domain": doc.get("domain"),
                                            "date": doc.get("date"), "status": doc.get("status"), "gist": None, "gist_source": None})
                doc_score[doc["id"]] = doc_score.get(doc["id"], 0.0) + 1.0 / (RRF_K + r)
        for r, d in enumerate(rows.documents, 1):
            if not d.get("doc_id"):
                continue
            base = docs.setdefault(d["doc_id"], dict(d))
            for field in ("gist", "gist_source", "title", "date", "status", "domain"):
                if base.get(field) is None and d.get(field) is not None:
                    base[field] = d[field]
            doc_score[d["doc_id"]] = doc_score.get(d["doc_id"], 0.0) + 1.0 / (RRF_K + r)
        facts += [{**f, "from": sid} for f in rows.facts]
        guides += rows.guides
    evidence = [ev[k] for k in sorted(ev, key=lambda k: -ev_score[k])]
    for i, row in enumerate(evidence, 1):
        row["eid"], row["rank"] = f"e{i}", i
    documents = [docs[k] for k in sorted(docs, key=lambda k: -doc_score[k])]
    by_doc: dict[str, list] = {}
    for row in evidence:
        by_doc.setdefault((row.get("doc") or {}).get("id"), []).append(row["eid"])
    for i, d in enumerate(documents, 1):
        d["rank"], d["evidence"] = i, by_doc.get(d["doc_id"], [])
    for i, f in enumerate(facts, 1):
        f["fid"] = f"f{i}"
    return evidence, documents, facts, guides


def _selected_evidence(evidence: list) -> list:
    out, seen = list(evidence[:EVIDENCE_TOP]), {(e.get("doc") or {}).get("id") for e in evidence[:EVIDENCE_TOP]}
    for row in evidence[EVIDENCE_TOP:]:
        doc = (row.get("doc") or {}).get("id")
        if doc not in seen:
            out.append(row)
            seen.add(doc)
    return out


def _coverage(ex, evidence) -> dict:
    ran = [(sid, r) for sid, r in ex.results.items() if r.exit_code == 0]
    read = set().union(*(set(ex.facts.get(sid, {}).get("_docs") or ()) for sid, _ in ran)) if ran else set()
    depths = [facts_mod._option_int(r.argv, ("--topK", "--limit")) for _, r in ran]
    return {"documents_read": len(read), "documents_total": None,
            "found": len({(e.get("doc") or {}).get("id") for e in evidence if (e.get("doc") or {}).get("id")}),
            "exhaustive": not any(ex.facts.get(sid, {}).get("truncated") for sid, _ in ran),
            "partial": ex.partial, "probes": [], "depth": max([d for d in depths if d] or [0]) or None}


def _plan_block(plan: dict) -> dict:
    conf = plan.get("confidence") or {}
    return {"intent": (plan.get("intent") or {}).get("id"), "route": (plan.get("route") or {}).get("id"),
            "program_kind": plan.get("program_kind"),
            "confidence": {k: conf.get(k) for k in ("level", "score", "threshold", "weakest")},
            "classifier": (plan.get("classifier") or {}).get("backend"), "subplans": []}


def _path(ex) -> list:
    return [{**p, "cmd": p["cmd"][:CMD_CHARS]} if isinstance(p.get("cmd"), str) else p for p in ex.path]


def build(plan: dict, ex, *, budget: int, plan_ms: float | None = None) -> tuple[dict, dict]:
    """(printed bundle, full bundle). The printed one fits `budget` characters as compact JSON."""
    evidence, documents, facts, guides = _rank(ex)
    base = {"command": "query run", "warnings": [], "next": None, "bundle_version": BUNDLE_VERSION, "mode": "reasoning",
            "plan": _plan_block(plan), "scope": plan.get("scope"), "path": _path(ex),
            "coverage": _coverage(ex, evidence), "gaps": plan.get("gaps") or [], "escalate": ex.escalate, "offer": None,
            "timing": {"plan_ms": plan_ms, "exec_ms": ex.exec_ms}}
    full = {**base, "evidence": evidence, "documents": documents, "facts": facts, "guides": guides}
    printed = {**base, "evidence": [], "documents": [], "facts": [], "guides": []}
    candidates = {"evidence": _selected_evidence(evidence), "documents": documents, "facts": facts, "guides": guides}
    size = len(_dumps(printed)) + _RESERVE
    cut = len(candidates["evidence"]) < len(evidence)
    for name in _LISTS:
        for row in candidates[name]:
            n = len(_dumps(row)) + 1
            if size + n > budget:
                cut = True
                break
            printed[name].append(row)
            size += n
        else:
            continue
        break
    for name in _LISTS:
        if len(printed[name]) < len(full[name]):
            cut = True
    printed.update(truncated=False, rows_total=len(evidence), handle=None)
    if cut:
        import paths
        from artmind import result_store

        handle = result_store.store(paths.LOGS_DIR / "results", full, _dumps)
        shown = len(printed["evidence"])
        printed.update(truncated=True, handle=handle, next=f"artmind query page {handle} --list evidence --from {shown}")
        printed["warnings"] = [f"bundle budget {budget:,} characters: showing {shown} of {len(evidence)} evidence rows, "
                               f"{len(printed['documents'])} of {len(documents)} documents, "
                               f"{len(printed['facts'])} of {len(facts)} facts"]
    return printed, full
```

`for … else: continue; break` stops filling every later list as soon as one row of any list does not fit. That keeps the order of the spec's §6.3: evidence first, then documents, facts and guides.

- [ ] **Step 4: `LIST_KEY` and `query page --list`**

In `artmind/cli_conventions.py`, add `"query run": "evidence",` to `LIST_KEY`.

In `artmind/cli.py`, `query_page`: add the option `@click.option("--list", "list_name", default=None, help="Which list of the stored result to page (default: its main list, e.g. evidence for query run)")` right after the `--rows` option, add `list_name: str | None` to the signature, and replace the `key = ...` lines with:

```python
    key = list_name or result_store.list_key(stored, LIST_KEY.get(str(stored.get("command") or "")))
    if key is None or not isinstance(stored.get(key), list):
        raise click.ClickException(f"stored result {handle} has no list {key!r} to page" if key else
                                   f"stored result {handle} has no list to page")
```

Change the `next` line to keep `--list` when it was given:

```python
    more = f" --list {list_name}" if list_name else ""
    page["next"] = f"artmind query page {handle}{more} --from {end}" if end < len(rows) else None
```

- [ ] **Step 5: `query run` prints the bundle**

In `query_run`, replace the `_echo_json({... "path": ...})` call with:

```python
    from artmind.query_run import bundle as bundle_mod

    try:
        budget = bundle_mod.bundle_budget()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    printed, _ = bundle_mod.build(plan, ex, budget=budget)
    _echo_json(printed, compact)
```

Update `test_run_a_given_plan` in `test/test_query_run_cli.py`: the payload now has `bundle_version == 1`, and `path` is unchanged. Add `assert payload["bundle_version"] == 1`.

The bundle is measured as compact JSON. Without `--compact`, `_echo_json` indents the output, which may exceed `ARTMIND_OUTPUT_BUDGET` and be cut once more under a second handle. That is accepted: the agent and the skill always pass `--compact`.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_bundle.py test/test_query_run_cli.py test/test_output_budget.py test/test_cli_conventions.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/query_run/bundle.py artmind/cli_conventions.py artmind/cli.py test/test_query_run_bundle.py test/test_query_run_cli.py
git commit -m "feat(query-run): the evidence bundle: RRF, assembly to budget, handle; query page --list (P3 S3)"
```

### Task 10: S3 gate

- [ ] **Step 1: Full suite**

Run: `uv run --group dev pytest -q test/`
Expected: all pass. Also run `ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q` and then `git status`. Expected: no fixture changes, because `query page` without `--list` prints the same as before. If a fixture changed, the default output of `query page` changed: fix it.

---

# Slice S4: the question path, proxying, spans, `plan replay --run`

### Task 11: `query run "<question>"`

**Files:**
- Modify: `artmind/cli.py` (`query_run`)
- Test: `test/test_query_run_cli.py` (add tests)

- [ ] **Step 1: Write the failing tests** (append to `test/test_query_run_cli.py`)

```python
def test_run_a_question_plans_then_executes(monkeypatch):
    from artmind import cli as cli_mod
    from artmind.query_plan import planner

    seen = {}

    def fake_plan(question, domains, **kw):
        seen.update(question=question, domains=domains, **kw)
        return {"program": [{"id": "s1", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True}],
                "guards": [], "route": {"id": "inventory"}, "replacements": {}, "suggested_steps": [], "fallbacks": []}

    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.setattr(planner, "plan", fake_plan)
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    out = CliRunner().invoke(cli, ["query", "run", "--domain", "bank", "--classifier", "lexical", "--exclude", "x.md",
                                   "--compact", "what documents are there?"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.stdout)
    assert seen["question"] == "what documents are there?" and seen["domains"] == ["bank"]
    assert seen["classifier"] == "lexical" and seen["exclude"] == ["x.md"] and seen["previous"] is None
    assert payload["timing"]["plan_ms"] is not None and payload["path"][0]["exit_code"] == 0


def test_a_plan_error_is_bad_input(monkeypatch):
    from artmind.query_plan import planner

    def boom(*a, **k):
        raise planner.PlanError("the question is empty")

    monkeypatch.setattr(planner, "plan", boom)
    out = CliRunner().invoke(cli, ["query", "run", " "])
    assert out.exit_code == 1 and "empty" in out.output
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_cli.py -q`
Expected: the two new tests FAIL (no such option `--domain`).

- [ ] **Step 3: Replace the command with the full version**

Replace the `query_run` decorators and function with:

```python
@query.command("run")
@click.option("--domain", "domain", multiple=True, help="Scope to these domains (repeatable; comma-splittable). Omit and the plan chooses")
@click.option("--exclude", "exclude", multiple=True, help="Leave this document (id or name) out of the scope (repeatable; comma-splittable)")
@click.option("--previous", "previous_file", default=None,
              help="The previous turn as JSON (as for query plan): a file, or - for stdin. Not proxied to serve")
@click.option("--classifier", type=click.Choice(["auto", "lexical", "decision"]), default="auto", show_default=True,
              help="auto: the decision model when ARTMIND_DECISION_URL is set, else lexical rules")
@click.option("--plan", "plan_file", default=None, help="Execute this plan (a `query plan` payload): a file, or - for stdin. Not proxied to serve")
@click.option("--maxSteps", "max_steps", type=click.IntRange(1, 40), default=12, show_default=True,
              help="Most commands one run executes (pages and replacements included)")
@click.option("--deadline", "deadline", type=click.FloatRange(1, 120), default=None,
              help="Seconds for the whole run, checked between steps (default ARTMIND_RUN_DEADLINE_S or 20)")
@compact_option
@click.argument("question", required=False)
def query_run(domain: tuple, exclude: tuple, previous_file: str | None, classifier: str, plan_file: str | None,
              max_steps: int, deadline: float | None, compact: bool, question: str | None) -> None:
    """Plan a question and execute the plan's program in process: one evidence bundle (P3).

    Read-only: steps are `artmind query ...` commands and read-only `artmind db ...` commands only.
    `evidence` is what an answer may cite; `documents`, `facts` and `guides` give coverage and steer.
    `escalate` says why the bundle may not be enough and `suggested` what to run next. A bundle over
    the budget (ARTMIND_BUNDLE_BUDGET, else ARTMIND_OUTPUT_BUDGET, else 24,000) carries a `handle`:
    `artmind query page <handle> --list evidence|documents|facts|guides`.
    """
    import time as _time

    from neo4j.exceptions import DriverError, Neo4jError

    from artmind.query_plan import intents, planner
    from artmind.query_run import bundle as bundle_mod
    from artmind.query_run import executor

    plan_ms = None
    if plan_file is not None:
        plan = _load_plan_file(plan_file)
    elif question is None:
        raise click.ClickException("give a question, or --plan FILE")
    else:
        previous = None
        if previous_file is not None:
            try:
                previous = intents.load_previous(previous_file)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
        started = _time.perf_counter()
        try:
            plan = planner.plan(question, _parse_domains(domain) if domain else None, classifier=classifier,
                                previous=previous, exclude=list(exclude))
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        except (DriverError, Neo4jError) as exc:
            raise click.ClickException(f"Neo4j is unreachable or failed: {exc}") from exc
        plan_ms = round((_time.perf_counter() - started) * 1000, 1)
    ex = executor.execute(plan, max_steps=max_steps, deadline_s=_run_deadline(deadline))
    try:
        budget = bundle_mod.bundle_budget()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    printed, _ = bundle_mod.build(plan, ex, budget=budget, plan_ms=plan_ms)
    _echo_json(printed, compact)
    if ex.exit_code:
        raise SystemExit(ex.exit_code)
```

`planner.PlanError` subclasses `ValueError`, so `except ValueError` covers it.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_cli.py test/test_cli_conventions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_query_run_cli.py
git commit -m "feat(query-run): query run plans a question, then executes it (P3 S4)"
```

### Task 12: Nested spans

**Files:**
- Modify: `artmind/db.py` (trace_spans migration loop at about line 434)
- Modify: `artmind/traces/store.py` (`insert_span`)
- Modify: `artmind/traces/span.py` (`TracedGroup.invoke`, `observe_output`, `_record`)
- Test: `test/test_query_run_spans.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Nested spans: a traced `query run` is the parent of one span per executed step (P3 spec §7.1)."""

import json

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_NO_STALENESS_CHECK", "1")
    monkeypatch.delenv("ARTMIND_TRACE_CONTEXT", raising=False)


def _plan(tmp_path):
    plan = {"program": [{"id": "p", "kind": "par", "children": [
        {"id": "a", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True},
        {"id": "b", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True}]}],
        "guards": [], "route": {"id": "inventory"}, "replacements": {}, "suggested_steps": [], "fallbacks": []}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    return path


def test_a_traced_run_has_child_spans(tmp_path, monkeypatch):
    from artmind import cli as cli_mod
    from artmind.traces import store

    store.insert_turn("t1", source="admin", question="q")
    monkeypatch.setattr(cli_mod.graph_query, "domains_overview", lambda *a, **k: {"rows": [{"domain": "bank"}]})
    monkeypatch.setenv("ARTMIND_TRACE_ID", "t1")
    out = CliRunner().invoke(cli_mod.cli, ["query", "run", "--plan", str(_plan(tmp_path)), "--compact"])
    assert out.exit_code == 0, out.output
    spans = store.list_spans("t1")
    run = next(s for s in spans if s["command"] == "query run")
    children = [s for s in spans if s["parent_span_id"] == run["span_id"]]
    assert [s["command"] for s in children] == ["query domains-overview"] * 2
    assert run["parent_span_id"] is None and run["run_mode"] == "reasoning"
    assert run["bundle_chars"] and run["escalate_reason"] is None
    assert all(s["rows"] == 1 for s in children)


def test_a_plain_nested_invocation_keeps_todays_behaviour(monkeypatch):
    """Outside capture, a nested invocation still runs inside its parent's span (no child)."""
    from artmind.traces import span as span_mod

    assert span_mod._child_wanted() is False
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_spans.py -q`
Expected: FAIL (no column `parent_span_id`; no `_child_wanted`).

- [ ] **Step 3: Migration and store**

In `artmind/db.py`, extend the `for col, decl in (...)` tuple for `trace_spans` with:

```python
("parent_span_id", "TEXT"), ("run_mode", "TEXT"), ("escalate_reason", "TEXT"), ("bundle_chars", "INTEGER")
```

Add `cursor.execute("CREATE INDEX IF NOT EXISTS idx_trace_spans_parent ON trace_spans(parent_span_id)")` after the existing index line.

In `artmind/traces/store.py` `insert_span`, change the INSERT to:

```python
        conn.execute(
            "INSERT INTO trace_spans (span_id, trace_id, command, args, exit_code, duration_ms, rows,"
            " rows_total, truncated, evidence, started_at, payload_chars, shown_chars, handle,"
            " parent_span_id, run_mode, escalate_reason, bundle_chars)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (span["span_id"], span["trace_id"], span["command"], span["args"], span["exit_code"],
             span["duration_ms"], span["rows"], span["rows_total"], span["truncated"], span["evidence"],
             span["started_at"], span.get("payload_chars"), span.get("shown_chars"), span.get("handle"),
             span.get("parent_span_id"), span.get("run_mode"), span.get("escalate_reason"), span.get("bundle_chars")),
        )
```

- [ ] **Step 4: Child spans in `TracedGroup`**

In `artmind/traces/span.py`, add:

```python
def _child_wanted() -> bool:
    """A step of `query run` (capture mode) under an open span gets its own child span."""
    from artmind.query_run import capture

    return capture.active() is not None and _active.get() is not None
```

In `TracedGroup.invoke`, replace the first two lines:

```python
        if _active.get() is not None:
            return super().invoke(ctx)
```

with:

```python
        parent = _active.get()
        if parent is not None and not _child_wanted():
            return super().invoke(ctx)
```

Then, where the span dict is built, use the parent's trace id for a child and add the parent link:

```python
        tokens = [*getattr(ctx, "_protected_args", []), *ctx.args]
        try:
            trace_id = parent["trace_id"] if parent is not None else (None if "--help" in tokens else current_trace_id())
        except Exception:
            trace_id = None
        if not trace_id or not self._eligible(ctx, tokens):
            return super().invoke(ctx)

        try:
            span = {
                "span_id": uuid.uuid4().hex, "trace_id": trace_id, "argv": list(tokens),
                "command": _command_path(self, ctx, tokens), "started_at": time.time(),
                "summary": None, "params": None, "llm_calls": [],
                "parent_span_id": parent["span_id"] if parent is not None else None,
            }
            token = _active.set(span)
```

The rest of the method is unchanged. In `observe_output`, after the `span["payload_chars"], ...` line:

```python
        if isinstance(payload, dict) and "bundle_version" in payload:
            span["run_mode"] = payload.get("mode")
            span["escalate_reason"] = (payload.get("escalate") or {}).get("reason")
            span["bundle_chars"] = payload_chars
```

In `_record`, add these keys to the dict passed to `store.insert_span`: `"parent_span_id": span.get("parent_span_id"), "run_mode": span.get("run_mode"), "escalate_reason": span.get("escalate_reason"), "bundle_chars": span.get("bundle_chars"),`.

- [ ] **Step 5: Bundle evidence for the run span**

In `artmind/traces/evidence.py`, `_count`: make the first lines

```python
    if "bundle_version" in payload:
        return len(payload.get("evidence") or [])
```

In `_provenance`, change the loop head to include the bundle's evidence rows:

```python
    bundle_rows = payload.get("evidence") if "bundle_version" in payload else None
    for row in [*(payload.get("rows") or []), *(payload.get("passages") or []), *(bundle_rows or [])]:
```

In `artmind/provenance.py` `select_evidence_spans`, skip child spans, so the run span (what the agent saw) is the citation source:

```python
        if span.get("parent_span_id"):
            continue
```

Put it as the first check in the loop.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_spans.py test/test_traces_span.py test/test_traces_store.py test/test_traces_evidence.py test/test_provenance*.py -q`
Expected: PASS. If `test_traces_store.py` asserts the exact column set, add the four new columns to its expectation.

- [ ] **Step 7: Commit**

```bash
git add artmind/db.py artmind/traces/store.py artmind/traces/span.py artmind/traces/evidence.py artmind/provenance.py test/test_query_run_spans.py
git commit -m "feat(traces): a query run span is the parent of its steps' spans (P3 S4)"
```

### Task 13: Analysis for run turns

**Files:**
- Modify: `artmind/traces/analysis.py` (`signals` callers in `triage`, `_shadow_rates`, `views_checks` input, `stats`, `show`)
- Test: `test/test_query_run_analysis.py`

- [ ] **Step 1: Write the failing tests**

```python
"""traces analysis on run turns (P3 spec §7.1): the agent's calls are top-level spans; steps are children."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _span(trace_id, sid, command, parent=None, started=1_700_000_000.5):
    store.insert_span({"span_id": sid, "trace_id": trace_id, "command": command, "args": json.dumps({"domain": ["b"]}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0, "evidence": "{}",
                       "started_at": started, "parent_span_id": parent}, [])


def _turn(trace_id):
    store.insert_turn(trace_id, source="admin", question="q", session_id=f"s-{trace_id}", started_at=1_700_000_000.0)
    store.update_turn(trace_id, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)


def test_top_level_drops_children():
    spans = [{"span_id": "r", "parent_span_id": None}, {"span_id": "c", "parent_span_id": "r"}]
    assert [s["span_id"] for s in analysis.top_level(spans)] == ["r"]


def test_stats_count_run_turns_and_added_after_run():
    _turn("t1")
    _span("t1", "r1", "query run")
    for i, cmd in enumerate(("query vector-text", "query survey", "query graph pattern7")):
        _span("t1", f"c{i}", cmd, parent="r1", started=1_700_000_000.6)
    _turn("t2")
    _span("t2", "r2", "query run")
    _span("t2", "x2", "query vector-text", started=1_700_000_000.9)
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["run"] == {"run_turns": 2, "added_after_run": 0.5}


def test_child_spans_do_not_raise_deep_fallback():
    _turn("t1")
    _span("t1", "r1", "query run")
    for i, cmd in enumerate(("query vector-text", "query survey", "query graph pattern7")):
        _span("t1", f"c{i}", cmd, parent="r1")
    sig = analysis.signals({"turns": 1}, analysis.top_level(store.list_spans("t1")), None, {"status": "n/a"})
    assert "deep_fallback" not in sig


def test_show_nests_children_under_their_run():
    _turn("t1")
    _span("t1", "r1", "query run")
    _span("t1", "c1", "query vector-text", parent="r1")
    shown = analysis.show("t1")
    (run,) = [s for s in shown["unattributed_spans"] if s["span_id"] == "r1"]
    assert [c["span_id"] for c in run["children"]] == ["c1"]
    assert all(s["span_id"] != "c1" for s in shown["unattributed_spans"])
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_analysis.py -q`
Expected: FAIL (`top_level` missing; no `run` key; `children` missing).

- [ ] **Step 3: Implement**

In `artmind/traces/analysis.py`, add near the top (after imports):

```python
def top_level(spans: list[dict]) -> list[dict]:
    """The agent's own calls: spans with no parent. A `query run` step's span is a child of its run."""
    return [s for s in spans if not s.get("parent_span_id")]


def _run_block(turns: list[dict], spans_by_trace: dict[str, list[dict]]) -> dict:
    """Run turns (a top-level `query run` span) and the share where the agent ran a lookup after it."""
    runs = after = 0
    for turn in turns:
        spans = top_level(spans_by_trace.get(turn["trace_id"], []))
        run = next((s for s in spans if s.get("command") == "query run"), None)
        if run is None:
            continue
        runs += 1
        after += any(_is_looked_up(s.get("command")) and s.get("command") not in ("query run", "query page")
                     and (s.get("started_at") or 0) > (run.get("started_at") or 0) for s in spans)
    return {"run_turns": runs, "added_after_run": after / runs if runs else None}
```

Then:
- In `triage`, pass `top_level(spans_by_trace.get(turn["trace_id"], []))` to `signals` instead of the raw list. The signals (`deep_fallback`, `empty_rows`, `many_turns`) describe what the agent did; executor steps would inflate them.
- In `stats`, add `"run": _run_block(turns, spans_by_trace),` to each row dict, after `"plan_shadow": ...`.
- Leave `views_checks` and `_shadow_rates` reading all spans, children included. On a run turn, the executor's steps are the plan's own commands, so shadow agreement is true by construction (spec §7.1) and the views check sees the view steps the run made. Reading only top-level spans there would make every run turn look like a disagreement.
- In `show`, after the `for span in spans: span["llm_calls"] = ...` loop:

```python
    children: dict[str, list[dict]] = {}
    for span in spans:
        if span.get("parent_span_id"):
            children.setdefault(span["parent_span_id"], []).append(span)
    for span in spans:
        span["children"] = children.get(span["span_id"], [])
    spans = top_level(spans)
```

The `by_tool` and `unattributed_spans` code below that line then reads only top-level spans.

- [ ] **Step 4: Run the tests and the traces suite**

Run: `uv run --group dev pytest test/test_query_run_analysis.py test/test_traces_*.py -q`
Expected: PASS. The existing `plan_shadow` dict-equality tests stay green, because the new key is a sibling (`run`), not inside `plan_shadow`.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/analysis.py test/test_query_run_analysis.py
git commit -m "feat(traces): run turns: top-level spans for signals, a run block in stats, nested show (P3 S4)"
```

### Task 14: `plan replay --run`

**Files:**
- Create: `artmind/query_run/replay_run.py`
- Modify: `artmind/cli.py` (`plan_replay`)
- Test: `test/test_query_run_replay.py`

- [ ] **Step 1: Write the failing tests**

```python
"""`plan replay --run`: latency and bundle reach of executed plans (P3 spec §9.1). Stub plans and steps."""

import json

from artmind.query_plan.labels import Label
from artmind.query_run import replay_run
from artmind.query_run.capture import StepResult

NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 fees --compact"


def _label(lid="L1", expected=("policies/fees.md", "Rates.md")):
    return Label(id=lid, question="invented question", source="written", domains=("bank",), route="search",
                 view=None, params={}, expected_documents=tuple(expected))


def _plan(*a, **k):
    return {"program": [{"id": "needle", "kind": "run", "run": NEEDLE, "filled": True}], "guards": [],
            "route": {"id": "search"}, "replacements": {}, "suggested_steps": [], "fallbacks": [],
            "steps": [{"cmd": NEEDLE}], "domains": {"selected": ["bank"]}, "confidence": {"level": "low"},
            "classifier": {"latency_ms": 1.0, "fell_back": False}}


def _step(argv, step_id):
    rows = [{"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5,
             "doc": {"id": f"d{i}", "title": t}} for i, t in enumerate(("fees", "other"))]
    return StepResult(step_id, list(argv), 0, None, {"command": "query vector-text", "rows": rows}, 5.0, 100)


def test_per_label_counts_and_totals():
    ticks = iter(range(0, 10_000, 100))
    report = replay_run.run_labels([_label(), _label("L2", expected=())], "lexical", plan_fn=_plan, step_fn=_step,
                                   clock=lambda: next(ticks) / 1000)
    (row,) = report["labels"]
    assert row["id"] == "L1" and row["expected"] == 2 and row["steps_reached"] == 1 and row["bundle_reached"] == 1
    assert row["executed"] == 1 and row["plan_ms"] is not None and row["escalate"] is None
    assert report["totals"]["labels"] == 1 and report["totals"]["bundle_reached"] == 1
    assert "invented question" not in json.dumps(replay_run.public_report(report))


def test_p90():
    assert replay_run.p90([]) is None
    assert replay_run.p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 9
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_run_replay.py -q`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `artmind/query_run/replay_run.py`**

```python
"""`artmind plan replay --run` (P3 spec §9.1): plan each label once, execute it, and measure.

Per label: plan_ms, exec_ms, executed steps, escalation, and expected documents reached by any step
(`steps_reached`) and by the placed bundle (`bundle_reached`). Counts and label ids are printed;
matched and missed paths go to <ARTMIND_HOME>/logs/plan-run-*.json (private).
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

from artmind.query_plan import reach as reach_mod
from artmind.query_run import bundle as bundle_mod
from artmind.query_run import executor, facts as facts_mod


def p90(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[max(0, math.ceil(0.9 * len(values)) - 1)]


def _bundle_docs(printed: dict) -> list[dict]:
    docs = [{"id": (e.get("doc") or {}).get("id"), "title": (e.get("doc") or {}).get("title"), "path": None,
             "source_path": None, "name": None} for e in printed.get("evidence") or []]
    docs += [{"id": d.get("doc_id"), "title": d.get("title"), "path": None, "source_path": None, "name": None}
             for d in printed.get("documents") or []]
    return docs


def run_labels(labels, classifier: str, *, model=None, given_domains=False, plan_fn=None, step_fn=None,
               clock=time.perf_counter, budget: int = bundle_mod.DEFAULT_BUDGET) -> dict:
    from artmind.query_plan import planner
    from artmind.query_plan import replay as replay_mod

    base = plan_fn or planner.plan
    timings: dict[str, float] = {}

    def timed(question, *a, **k):
        started = clock()
        try:
            return base(question, *a, **k)
        finally:
            timings[question] = round((clock() - started) * 1000, 1)

    subset = [lb for lb in labels if lb.expected_documents]
    plans: dict[str, dict] = {}
    report = replay_mod.replay(subset, classifier, 1, given_domains=given_domains, model=model, plan_fn=timed,
                               threads={}, plans_out=plans)
    errors = {r["id"] for r in report["errors"]}
    rows = []
    for lb in subset:
        expected = list(lb.expected_documents)
        if lb.id in errors:
            rows.append({"id": lb.id, "expected": len(expected), "steps_reached": 0, "bundle_reached": 0, "plan_ms": None,
                         "exec_ms": None, "executed": 0, "escalate": "plan_error", "detail": {}})
            continue
        plan = plans[lb.id]
        ex = executor.execute(plan, step_fn=step_fn, clock=clock)
        printed, _ = bundle_mod.build(plan, ex, budget=budget)
        step_docs = [d for r in ex.results.values() if r.payload for d in facts_mod.step_documents(r.payload)]
        steps_matched, _ = reach_mod.reached_documents(expected, step_docs)
        bundle_matched, missed = reach_mod.reached_documents(expected, _bundle_docs(printed))
        rows.append({"id": lb.id, "expected": len(expected), "steps_reached": len(steps_matched),
                     "bundle_reached": len(bundle_matched), "plan_ms": timings.get(lb.question), "exec_ms": ex.exec_ms,
                     "executed": ex.executed, "escalate": (ex.escalate or {}).get("reason"),
                     "detail": {"matched": bundle_matched, "missed": missed, "path": ex.path}})
    small = [r["exec_ms"] for r in rows if r["exec_ms"] is not None and 1 <= r["executed"] <= 4]
    totals = {"labels": len(rows), "expected": sum(r["expected"] for r in rows),
              "steps_reached": sum(r["steps_reached"] for r in rows), "bundle_reached": sum(r["bundle_reached"] for r in rows),
              "plan_ms_p90": p90([r["plan_ms"] for r in rows]), "exec_ms_p90_1_4_steps": p90(small),
              "exec_ms_p90_all": p90([r["exec_ms"] for r in rows]),
              "escalated": sum(1 for r in rows if r["escalate"])}
    return {"classifier": classifier, "totals": totals, "labels": rows}


def public_report(report: dict) -> dict:
    return {**report, "labels": [{k: v for k, v in r.items() if k != "detail"} for r in report["labels"]]}


def write_detail(report: dict) -> Path:
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-run-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
```

`clock` is used for both plan timing and the executor's deadline. With the test's fake clock, the deadline (20 s) is never reached because ticks step 0.1 s. Real runs use `time.perf_counter`.

- [ ] **Step 4: Add the `--run` flag**

In `artmind/cli.py` `plan_replay`, add this option next to `--reach`:

```python
@click.option("--run", "run_programs", is_flag=True,
              help="Instead of grading the plans, execute each plan (labels with expected_documents) through the "
                   "executor and report plan/exec latency and bundle reach (P3 gate). Slow; needs Neo4j; first plan only")
```

Add `run_programs: bool` to the signature. Right after the `if reach:` block's `return`, add:

```python
    if run_programs:
        if reach:
            raise click.ClickException("--reach and --run are separate measurements; give one")
        from artmind.query_run import replay_run

        out = {}
        for backend in backends:
            report = replay_run.run_labels(loaded, backend, model=model, given_domains=given_domains)
            out[backend] = {**replay_run.public_report(report), "detail_file": str(replay_run.write_detail(report))}
        _echo_json({"command": "plan replay", "file": str(path), "run": True, "reports": out}, compact)
        return
```

Put the `--reach and --run` check before the `if reach:` block, so it fires first. Add one sentence to the docstring: "With --run (P3 §9.1), each plan is executed by `query run`'s executor; the report gives plan_ms, exec_ms and bundle reach (counts and label ids only)."

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_query_run_replay.py test/test_query_plan_reach.py test/test_query_plan_replay.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/query_run/replay_run.py artmind/cli.py test/test_query_run_replay.py
git commit -m "feat(plan-replay): --run measures plan/exec latency and bundle reach (P3 S4)"
```

### Task 15: Docs

**Files:**
- Modify: `CLAUDE.md` (repo layout table; command routing quick reference)
- Modify: `docs/question-routing.md` (§7)

- [ ] **Step 1: CLAUDE.md**

Add a row to the repo layout table, after the `artmind/query_plan/` row:

```markdown
| `artmind/query_run/` | `artmind query run` (spec `docs/superpowers/specs/2026-11-07-p3-executor-design.md`): plans a question (or takes `--plan FILE`) and executes the plan's program in process. `capture` (a step runs as a real Click command; `_echo_json` hands the uncut payload to the capture box and prints nothing; only `query` commands other than run/plan/page and the read-only `db` set may run), `facts` (every step predicate from a payload; also used by `plan replay --reach`), `executor` (nodes, guards, `replacements` from the plan, `--maxSteps`, `--deadline`/`ARTMIND_RUN_DEADLINE_S`; an unfilled program runs `search` instead), `adapters` + `bundle` (RRF over steps; top 10 evidence + 1 per further document within `ARTMIND_BUNDLE_BUDGET`, else the output budget, else 24,000; the full bundle under a handle, `query page <h> --list evidence|documents|facts|guides`), `replay_run` (`plan replay --run`: plan_ms, exec_ms, bundle reach). Proxied to `serve` except with `--plan`/`--previous`/`--each`; a traced run's steps are child spans (`trace_spans.parent_span_id`). |
```

In "Command routing quick reference", in the `artmind query <cmd>` line, after the `plan (...)` entry, add: `run` (`--domain`, `--exclude`, `--previous FILE|-`, `--plan FILE|-`, `--maxSteps`, `--deadline`; one evidence bundle; proxied except with file flags; traced with child spans),

- [ ] **Step 2: docs/question-routing.md**

At the end of §7 ("Confidence, abstention and the fast path"), add a paragraph:

```markdown
**The executor (P3a, built).** `artmind query run` executes a plan's program in process and returns one evidence
bundle: every step's rows ranked by reciprocal-rank fusion, bounded by the bundle budget, the rest under a result
handle. Low plans are executed too: the bundle is the agent's starting evidence, and `mode` stays `reasoning`
until a route passes the fast-path gate (spec `2026-11-07-p3-executor-design.md` §9.3).
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/question-routing.md
git commit -m "docs: query run and artmind/query_run in CLAUDE.md and question-routing (P3 S4)"
```

### Task 16: S4 gate (P3a latency and bundle reach)

- [ ] **Step 1: Full suite**

Run: `uv run --group dev pytest -q test/`
Expected: all pass.

- [ ] **Step 2: Reinstall, stop daemons, check the gateway**

```bash
cd ~/projects/artmind9 && just dev-install && just dev-stop-daemons
```

```bash
env | grep ^ANTHROPIC_
```

`text2cypher`/`text2sql` steps make LLM calls. If `ANTHROPIC_BASE_URL` shows up, run the commands below with `env -u ANTHROPIC_BASE_URL` (CLAUDE.md §5).

- [ ] **Step 3: One live run per vault**

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind query run --classifier lexical --compact "What are the steps to escalate a customer complaint?" | head -c 2000
```

Expected: a bundle with `bundle_version: 1`, `path` entries with `exit_code: 0`, and non-empty `evidence`.

- [ ] **Step 4: The gate**

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind plan replay --classifier both --run --compact
```

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 ARTMIND_PLAN_SHADOW=0 artmind plan replay --classifier both --run --compact
```

Read from each `reports.<backend>.totals` and compare with spec §9.1:

| Gate | Pass when |
|---|---|
| plan latency | `plan_ms_p90` ≤ 1500 for `lexical` (decision: report only) |
| execution latency | `exec_ms_p90_1_4_steps` ≤ 3000 |
| bundle keeps the evidence | banking `bundle_reached` ≥ 0.95 × `steps_reached` |
| no regressions | the S1 reach numbers are unchanged (re-run S1 Step 2) |

brain01 has no `expected_documents` labels, so its report covers latency only (labels with expected documents is 0). In that case run a brain01 benchmark through the admin UI with a restarted `serve` and read the run spans' `duration_ms`. The `run_shadow` measure is S6's job.

- [ ] **Step 5: Report**

Write the numbers (counts, label ids and milliseconds only) into the handover's "Next steps" entry for P3 on the branch, and commit:

```bash
git add docs/query-routing-study/15-handover-2026-11-07.md
git commit -m "docs(query-routing-study): P3a S1-S4 gate numbers"
```

Do not push. Do not merge.

---

## Self-review notes (done while writing)

- **Spec coverage, S1–S4:**
  - capture (§3.3) → T1;
  - facts (§3.4) → T2;
  - planner payload (§3.5 replace, needs) → T4;
  - node kinds, guards, short-circuit, limits, deadline, errors, escalation reasons (§3.5–§3.8) → T5;
  - `--plan` and the proxy rule (§3.1–§3.2) → T6 and T11;
  - adapters (§6.2) → T8;
  - ranking, assembly, handle and `query page --list` (§6.3) → T9;
  - the bundle shape (§6.1) → T9; `subplans` stays empty until S11 and `offer` stays null;
  - nested spans and the run columns (§7.1) → T12;
  - analysis (§7.1) → T13;
  - `plan replay --run` (§9.1) → T14;
  - docs (§12) → T15.
- **Spec refinements this plan makes,** recorded in the spec in the same commit as this plan:
  - guards keep their JSON, and the rendered replacement programs go into a top-level `replacements` key (tests pin guard JSON);
  - an unfilled program runs `search` instead of escalating at once;
  - the `run`, `retry_with` and `widen_domains` guard actions escalate until a route uses them;
  - ranking is RRF over every successful step, not only the last pick's sources.
- **Not in S1–S4, by design:** bindings (S5), shadow dry runs (S6), `--each` (S7; its flag is already refused by the proxy rule), `previous` from the UIs (S8). `count`/`latest` markers and `coverage.probes` are filled when those programs gain bindings and need them; S3 ships `probes: []`.
