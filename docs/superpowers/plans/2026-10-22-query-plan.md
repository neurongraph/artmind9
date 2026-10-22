# `artmind query plan` v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `artmind query plan`, a read-only, deterministic command that turns a question into a ready-to-run retrieval plan, together with the honest-command fixes it depends on, a labelled set with offline replay, and shadow mode on the traced surfaces.

**Architecture:** A new package `artmind/query_plan/` computes deterministic candidates (domains, saved views, mentions, resolved entities, twins), asks a pluggable classifier (lexical rules, or a decision model over the Jev API) at most two batched questions, fills step templates from a packaged `routes.yaml`, and returns the payload of spec §4.2. Four small changes to existing commands (H1–H4) make the commands the plan emits report honestly. Shadow mode computes a plan beside every traced turn and stores it as a trace event.

**Tech Stack:** Python ≥3.14 (uv), Click / rich-click, Neo4j (read queries via `artmind.graph_query._run_read_query`), PyYAML, stdlib `urllib.request` for the decision endpoint, SQLite registry (`artmind.db`) for traces, pytest (hermetic: no Neo4j, no network).

**Spec:** `docs/superpowers/specs/2026-10-22-query-plan-design.md`. Section references (§n) below point into it.

---

## Ground rules for every task

- **Test command:** `uv run --group dev pytest <file> -v`. The full suite stalls on this Mac (vault-radar git hook); run the files a task names, and `uv run --group dev pytest test/ -q -x` only at the phase checkpoints.
- **Hermetic tests.** `test/conftest.py` blocks Neo4j and redirects `ARTMIND_HOME` to a temp dir. Never reach Neo4j or the network from a test. Patch `artmind.graph_query._run_read_query`, `artmind.vector_query.entity_resolve`, `artmind.graph_query.domains_overview`, or the module function the code under test calls.
- **Assert what was sent** (CLAUDE.md §3): for graph code, record the Cypher and parameters a fake `_run_read_query` received and assert on them, never on summary counts alone.
- **Synthetic text only.** The checkout is public. No corpus names, questions or answers in tests, fixtures or commits. Use invented names (`Ada`, `Lakeport`, `policy_alpha.md`).
- **Stale daemon.** `artmind query …` is proxied to a running `serve` daemon (CLAUDE.md §1). Every live probe in this plan uses `ARTMIND_NO_PROXY=1`.
- **Commits.** One commit per task, on a feature branch `feat/query-plan` (create it before Task 1: `git checkout -b feat/query-plan`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Do not push; the user merges (`--no-ff` into `master`) and pushes.
- **Credential literals.** Never write `PASSWORD=`/`API_KEY=` literals in tests (the vault-radar hook blocks the push); use `test/_fake_credentials.FAKE_CREDENTIAL`.

## Spec deltas (decided while planning; flag to the user at the end)

1. A `{keywords}` placeholder (the question's content words) is added to the routes vocabulary of §4.3: `enumeration` needs a search term that is not an entity name.
2. `store.matched_values` is always `[]` in v1: `routing_surface()` carries no cell values. `records_plus_guidance` is decided by guidance wording plus a bridged class.
3. The `view` pseudo-route's `read` is `[]`: `references/views.md` is written by the skill wire-in (study W3), not by this plan.
4. The `inventory` route has one step (`domains-overview`); listing a class is left to the agent.
5. Replay's domain match: equal sets, or a single selected parent that covers every label domain (the "label lists every sibling" refinement is dropped).
6. In `traces/path.py`, an executed plan (`query plan --execute`, rows > 0) is layer `view`; any other plan span is layer `plan`, which is neither a graph attempt nor evidence.
7. `plan labels validate` checks entity names against Neo4j only with `--checkEntities`, so validating the file's structure works without a graph.

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/text2cypher.py`, `artmind/text2sql.py` | modify | H1: dry-run payload without `rows` |
| `artmind/cli.py` | modify | H1 `next`; H4 `--includeHistory`, history hints; `query plan`; `plan` group; COMMAND_GROUPS |
| `artmind/traces/path.py` | modify | H1 dry-run spans are discovery; `plan` layer; plan counts as a views check |
| `artmind/traces/analysis.py` | modify | H1 empty-rows signal; shadow plan in `show`/`stats`/`triage` |
| `artmind/traces/store.py` | modify | `list_events_of_type`, `count_session_turns` |
| `artmind/traces/recorder.py` | modify | start/finish the shadow plan |
| `artmind/llm_providers.py` | modify | H2: persisted temperature capability |
| `artmind/graph_query.py` | modify | H3 `pattern7`; H4 `supersedes`, `pattern10` history, `document_history` names |
| `artmind/vector_query.py` | modify | H4 `supersedes` on the vector-text document block |
| `paths.py` | modify | `DOMAIN_PLAN_DIR` |
| `pyproject.toml` | modify | ship `query_plan/*.yaml` |
| `artmind/query_plan/__init__.py` | create | package marker |
| `artmind/query_plan/routes.yaml` | create | the 17 routes (§4.3) |
| `artmind/query_plan/routes.py` | create | load, validate, render templates |
| `artmind/query_plan/config.py` | create | per-domain plan config (§4.5) |
| `artmind/query_plan/classify/__init__.py` | create | `select_classifier`, re-exports |
| `artmind/query_plan/classify/base.py` | create | `Decision`, `Answer`, `ClassifierUnavailable` |
| `artmind/query_plan/classify/lexical.py` | create | tokens, triggers, scorers, `LexicalClassifier` |
| `artmind/query_plan/classify/jev.py` | create | Jev-API client, fallback, circuit breaker |
| `artmind/query_plan/candidates.py` | create | mentions, domain options, resolution, twins, documents, store facts |
| `artmind/query_plan/fill.py` | create | scalar view params, combinations |
| `artmind/query_plan/planner.py` | create | stages → payload; `--execute` |
| `artmind/query_plan/labels.py` | create | labelled-set parser/validator (§4.6) |
| `artmind/query_plan/replay.py` | create | replay and scoring |
| `artmind/query_plan/shadow.py` | create | background plan for a traced turn (§6) |
| `artmind/webui/app.py`, `artmind/benchmark.py` | modify | pass the shadow question |
| `artmind/skills/artmind-query/SKILL.md`, `references/temporal.md` | modify | dry-run line, `--includeHistory` |
| `CLAUDE.md`, `docs/INSTALL.md`, `artmind/env.example`, `justfile` | modify | docs and recipe |
| `test/test_*.py` | create/modify | one test file per new module, plus additions named per task |

---

# Phase A — Honest commands (H1–H4)

### Task 1: H1 — a dry run carries no `rows`, and says what to run next

**Files:**
- Modify: `artmind/text2cypher.py` (the `if dry_run:` block in `execute_text2cypher`)
- Modify: `artmind/text2sql.py` (the `if dry_run:` block in `execute_text2sql`)
- Modify: `artmind/cli.py` (`graph_text2cypher`, `query_text2sql`)
- Test: `test/test_text2cypher.py`, `test/test_text2sql.py`, `test/test_dry_run_cli.py` (create)

- [ ] **Step 1: Update the existing dry-run assertions to the new contract**

In `test/test_text2cypher.py`, `test_execute_text2cypher_dry_run_skips_execution`, replace

```python
    assert result["dry_run"] is True
    assert result["rows"] == []
```

with

```python
    assert result["dry_run"] is True
    # A dry run executed nothing: an empty `rows` list would read as "no results".
    assert "rows" not in result
```

Run `grep -n "dry_run" test/test_text2sql.py` and make the same replacement in the text2sql dry-run test it finds (an `assert result["rows"] == []` next to `assert result["dry_run"] is True`).

- [ ] **Step 2: Write the CLI test**

Create `test/test_dry_run_cli.py`:

```python
"""H1 (spec 2026-10-22 §5.1): a dry run says it ran nothing and names the command that would."""

import json

from click.testing import CliRunner

from artmind import cli as cli_mod
from artmind.cli import cli


def test_text2cypher_dry_run_names_the_command_to_run(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2cypher, "execute_text2cypher",
        lambda **kw: {"command": "text2cypher", "generated_cypher": "MATCH (n) RETURN n", "dry_run": True},
    )
    result = CliRunner().invoke(
        cli, ["query", "graph", "text2cypher", "--domain", "fiction", "--dry-run", "--compact", "how many people?"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "rows" not in payload
    assert payload["next"] == "artmind query graph text2cypher --domain fiction 'how many people?' --compact"


def test_text2cypher_without_dry_run_has_no_next(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2cypher, "execute_text2cypher",
        lambda **kw: {"command": "text2cypher", "rows": [{"n": 1}]},
    )
    result = CliRunner().invoke(cli, ["query", "graph", "text2cypher", "--domain", "fiction", "--compact", "q"])
    assert result.exit_code == 0, result.output
    assert "next" not in json.loads(result.output)


def test_text2sql_dry_run_names_the_command_to_run(monkeypatch):
    monkeypatch.setattr(
        cli_mod.text2sql, "execute_text2sql",
        lambda question, domains, dry_run=False, as_of=None: {"command": "text2sql", "dry_run": True},
    )
    result = CliRunner().invoke(
        cli, ["query", "text2sql", "--domain", "bank", "--dry-run", "--asOf", "2026-01-01", "--compact", "total fees"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["next"] == "artmind query text2sql --domain bank --asOf 2026-01-01 'total fees' --compact"
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run --group dev pytest test/test_text2cypher.py test/test_text2sql.py test/test_dry_run_cli.py -v`
Expected: the two module tests FAIL on `assert "rows" not in result`; the CLI dry-run tests FAIL with `KeyError: 'next'`.

- [ ] **Step 4: Implement**

In `artmind/text2cypher.py`, replace

```python
    if dry_run:
        output["rows"] = []
        output["dry_run"] = True
        return output
```

with

```python
    if dry_run:
        # No `rows` key: an empty list reads as "the query found nothing", and
        # agents took it that way (query-routing study P20). The CLI adds `next`.
        output["dry_run"] = True
        return output
```

In `artmind/text2sql.py`, make the identical replacement in `execute_text2sql`.

In `artmind/cli.py`, add `import shlex` to the module imports if it is not there (`grep -n "^import shlex" artmind/cli.py`), then in `graph_text2cypher` replace

```python
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)


@graph.command("conflicts")
```

with

```python
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if dry_run:
        result["next"] = (
            f"artmind query graph text2cypher --domain {shlex.quote(','.join(domains))} "
            f"{shlex.quote(question)} --compact"
        )
    _echo_json(result, compact)


@graph.command("conflicts")
```

and in `query_text2sql` replace

```python
        result = text2sql.execute_text2sql(question, domains, dry_run=dry_run, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

with

```python
        result = text2sql.execute_text2sql(question, domains, dry_run=dry_run, as_of=as_of)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if dry_run:
        as_of_arg = f" --asOf {shlex.quote(as_of)}" if as_of else ""
        result["next"] = (
            f"artmind query text2sql --domain {shlex.quote(','.join(domains))}{as_of_arg} "
            f"{shlex.quote(question)} --compact"
        )
    _echo_json(result, compact)
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `uv run --group dev pytest test/test_text2cypher.py test/test_text2sql.py test/test_dry_run_cli.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/text2cypher.py artmind/text2sql.py artmind/cli.py test/test_text2cypher.py test/test_text2sql.py test/test_dry_run_cli.py
git commit -m "fix(query): a dry run carries no rows and names the command to run (H1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: H1 — traces count a dry run as discovery, not as an unproductive graph attempt

**Files:**
- Modify: `artmind/traces/path.py`
- Modify: `artmind/traces/analysis.py` (`signals`)
- Test: `test/test_traces_path.py`, `test/test_traces_analysis.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_traces_path.py`:

```python
def _dry_span(started_at=1.0):
    return {"span_id": "d1", "command": "query graph text2cypher", "args": {"domain": ["b"], "dry_run": True},
            "exit_code": 0, "rows": 0, "started_at": started_at, "evidence": {}}


def test_a_dry_run_is_discovery_not_a_graph_attempt():
    from artmind.traces import path

    derived = path.derive([_dry_span()], answer=None)
    assert derived["graph_attempts"] == 0
    assert derived["retrieval_path"] == []
    assert path.span_layer(_dry_span()) == "discovery"


def test_an_executed_text2cypher_still_counts():
    from artmind.traces import path

    span = {**_dry_span(), "args": {"domain": ["b"], "dry_run": False}}
    assert path.span_layer(span) == "text2cypher"
    assert path.derive([span], answer=None)["graph_attempts"] == 1
```

Append to `test/test_traces_analysis.py`:

```python
def test_a_dry_run_is_not_an_empty_result():
    from artmind.traces import analysis

    span = {"command": "query graph text2cypher", "args": {"dry_run": True}, "exit_code": 0, "rows": 0}
    sig = analysis.signals({"turns": 1}, [span], None, {"status": "n/a"})
    assert "empty_rows" not in sig
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_traces_path.py test/test_traces_analysis.py -v -k "dry_run or executed_text2cypher"`
Expected: FAIL with `AttributeError: module 'artmind.traces.path' has no attribute 'span_layer'` and the `empty_rows` assertion.

- [ ] **Step 3: Implement in `artmind/traces/path.py`**

After `layer_of`, add:

```python
def is_dry_run(span: dict) -> bool:
    """A `--dry-run` text2cypher/text2sql span: it generated a query and ran nothing."""
    args = span.get("args")
    return isinstance(args, dict) and args.get("dry_run") is True


def span_layer(span: dict) -> str:
    """`layer_of` the span's command, except that a dry run is discovery:
    it executed nothing, so it is neither a graph attempt nor evidence."""
    if is_dry_run(span):
        return "discovery"
    return layer_of(span.get("command"))
```

Then switch every span-level use of `layer_of(...command...)` in this file to `span_layer(span)`:

- in `_cited_groups`: `groups.add(_group(layer_of(span.get("command") or "")))` → `groups.add(_group(span_layer(span)))`
- in `answer_basis`: `layer_of(s.get("command")) != "discovery"` → `span_layer(s) != "discovery"`, and `_group(layer_of(s["command"]))` → `_group(span_layer(s))`
- in `derive`: `layer = layer_of(span.get("command"))` → `layer = span_layer(span)`, and in the `productive_spans` comprehension `layer_of(s.get("command")) != "discovery"` → `span_layer(s) != "discovery"`

- [ ] **Step 4: Implement in `artmind/traces/analysis.py`**

Change the import line `from artmind.traces.path import GRAPH_LAYERS, is_graph_miss, layer_of, views_check` to

```python
from artmind.traces.path import GRAPH_LAYERS, is_dry_run, is_graph_miss, layer_of, views_check
```

and in `signals` replace

```python
    if any(s.get("exit_code") == 0 and (s.get("rows") or 0) == 0 and s.get("command") != "query views list"
           for s in spans):
```

with

```python
    if any(s.get("exit_code") == 0 and (s.get("rows") or 0) == 0 and s.get("command") != "query views list"
           and not is_dry_run(s) for s in spans):
```

- [ ] **Step 5: Run the trace tests**

Run: `uv run --group dev pytest test/test_traces_path.py test/test_traces_analysis.py test/test_traces_recorder.py test/test_traces_views_check.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/path.py artmind/traces/analysis.py test/test_traces_path.py test/test_traces_analysis.py
git commit -m "fix(traces): a dry run is discovery, not an unproductive graph attempt (H1)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: H2 — the temperature refusal is learned once per machine

**Files:**
- Modify: `artmind/llm_providers.py`
- Test: `test/test_llm_providers.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_llm_providers.py` (the module already has `_calls_with_a_client`, `_bad_request`, `_ok`, `LITELLM_TEMPERATURE_400` and an autouse `_reset_clients` fixture):

```python
# ── the learned refusal survives the process (spec 2026-10-22 §5.2) ──────────

def _forget_in_memory_only():
    """What a new process looks like: the in-memory set is empty, the file stays."""
    from artmind import llm_providers

    llm_providers._no_temperature.clear()
    llm_providers._capabilities_loaded = False


def test_a_learned_refusal_is_written_to_the_capability_file():
    from artmind import llm_providers

    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    data = json.loads(llm_providers._capabilities_path().read_text())
    (key, entry), = data.items()
    assert key.endswith("|claude-haiku-5-5")
    assert entry["no_temperature"] is True
    assert "learned_at" in entry


def test_a_new_process_skips_the_failing_attempt():
    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert [("temperature" in kw) for kw in sent] == [False]


def test_an_expired_entry_is_ignored():
    from artmind import llm_providers

    _calls_with_a_client([_bad_request(LITELLM_TEMPERATURE_400), _ok()], "claude-haiku-5-5")
    path = llm_providers._capabilities_path()
    data = json.loads(path.read_text())
    for entry in data.values():
        entry["learned_at"] = "2000-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert sent[0]["temperature"] == 0


def test_a_corrupt_capability_file_is_treated_as_empty():
    from artmind import llm_providers

    path = llm_providers._capabilities_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    _forget_in_memory_only()
    sent = _calls_with_a_client([_ok()], "claude-haiku-5-5")
    assert sent[0]["temperature"] == 0
```

Add `import json` to the test module's imports if it is not there.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_llm_providers.py -v -k "capability or new_process or expired or corrupt"`
Expected: FAIL with `AttributeError: ... has no attribute '_capabilities_path'`.

- [ ] **Step 3: Implement**

In `artmind/llm_providers.py`, add to the imports:

```python
import json
import os
from datetime import datetime, timezone
```

After the `_no_temperature` declaration, add:

```python
# The learned refusals, persisted so each new `artmind` process (every CLI call
# is one) does not pay a failed request and print the warning again (spec
# 2026-10-22 §5.2). Entries expire, so a provider that changes is re-probed.
_CAPABILITY_TTL_S = 30 * 86400
_capabilities_loaded = False


def _capabilities_path() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "llm_capabilities.json"


def _read_capabilities() -> dict:
    try:
        data = json.loads(_capabilities_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_capabilities() -> None:
    """Fold unexpired persisted refusals into `_no_temperature`, once per process."""
    global _capabilities_loaded
    if _capabilities_loaded:
        return
    _capabilities_loaded = True
    now = datetime.now(timezone.utc).timestamp()
    for key, entry in _read_capabilities().items():
        if not isinstance(key, str) or not isinstance(entry, dict) or not entry.get("no_temperature"):
            continue
        url, sep, model = key.rpartition("|")
        try:
            learned = datetime.fromisoformat(str(entry.get("learned_at"))).timestamp()
        except ValueError:
            continue
        if sep and now - learned <= _CAPABILITY_TTL_S:
            _no_temperature.add((url, model))


def _remember_no_temperature(kind: tuple[str, str]) -> None:
    """Persist one refusal; never raises (a read-only run folder just re-learns)."""
    path = _capabilities_path()
    data = _read_capabilities()
    data[f"{kind[0]}|{kind[1]}"] = {"no_temperature": True, "learned_at": datetime.now(timezone.utc).isoformat()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.debug("could not persist the temperature capability of {}: {}", kind, exc)
```

Replace `_reset_clients` with:

```python
def _reset_clients() -> None:
    """Drop cached SDK clients and learned model limits. Intended for test isolation only."""
    global _capabilities_loaded
    _openrouter_clients.clear()
    _no_temperature.clear()
    _capabilities_loaded = False
    _capabilities_path().unlink(missing_ok=True)
    _ollama_clients.clear()
```

Keep any further lines the existing `_reset_clients` has after `_ollama_clients.clear()` (for example `_ollama_embed_clients.clear()`); the replacement above only adds the three capability lines.

In `call_llm_openrouter`, insert `_load_capabilities()` immediately before `kind = (env.get("ARTMIND_KG_LLM_URL") or OPENROUTER_BASE_URL, model)`, and replace `_no_temperature.add(kind)` with:

```python
        _no_temperature.add(kind)
        _remember_no_temperature(kind)
```

- [ ] **Step 4: Run the provider tests**

Run: `uv run --group dev pytest test/test_llm_providers.py -v`
Expected: all PASS, including the four older temperature tests (the autouse fixture now also deletes the file between tests).

- [ ] **Step 5: Commit**

```bash
git add artmind/llm_providers.py test/test_llm_providers.py
git commit -m "fix(llm): remember a temperature refusal across processes (H2)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: H3 — `pattern7` reports truncation and the documents each entity came from

**Files:**
- Modify: `artmind/graph_query.py` (the `pattern7` branch of `_pattern_query`; `execute_pattern`)
- Test: `test/test_graph_query.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_query.py`:

```python
# ── H3: pattern7 truncation and provenance (spec 2026-10-22 §5.3) ────────────

def test_pattern7_counts_hits_and_collects_documents():
    cypher, params = graph_query._pattern_query(
        "pattern7", {"domains": ["fiction"], "searchTerm": "harbour", "limit": 10}
    )
    assert "entity_name_ft" in cypher
    assert "_rowsTotal" in cypher and "_rawHits" in cypher
    assert "[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)-[:PART_OF]->(d:Document)" in cypher
    assert params["limit"] == 10
    assert params["countCap"] == graph_query.PATTERN7_COUNT_CAP


def test_execute_pattern7_lifts_totals_and_says_next(monkeypatch):
    sent = {}

    def fake_run(cypher, params):
        sent.update(params)
        return [
            {"entityData": {"name": f"E{i}"}, "documents": [{"id": "d", "name": "n.md", "path": "n.md"}],
             "document_count": 1, "_rowsTotal": 44, "_rawHits": 44}
            for i in range(10)
        ]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    result = graph_query.execute_pattern("fiction", "pattern7", searchTerm="harbour", limit=10)
    assert sent["searchTerm"] == "harbour"
    assert result["rows_total"] == 44
    assert result["truncated"] is True
    assert result["rows_total_capped"] is False
    assert result["next"] == "raise --limit (rows_total=44)"
    assert result["rows"][0]["documents"][0]["path"] == "n.md"
    assert "_rawHits" not in result["rows"][0]


def test_execute_pattern7_with_no_hits_reports_zero(monkeypatch):
    monkeypatch.setattr(graph_query, "_run_read_query", lambda cypher, params: [])
    result = graph_query.execute_pattern("fiction", "pattern7", searchTerm="harbour", limit=10)
    assert result["rows"] == []
    assert result["rows_total"] == 0
    assert result["truncated"] is False
    assert "next" not in result
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_query.py -v -k pattern7`
Expected: the three new tests FAIL (`_rawHits` not in the Cypher; `PATTERN7_COUNT_CAP` missing; no `rows_total`). The two older pattern7 tests still PASS.

- [ ] **Step 3: Implement the query**

In `artmind/graph_query.py`, next to `PATTERN10_DEFAULT_LIMIT`, add:

```python
# pattern7 counts fulltext hits up to this many (spec 2026-10-22 §5.3); beyond
# it `rows_total_capped` says the true total is larger.
PATTERN7_COUNT_CAP = 1000
```

Replace the whole `if pattern == "pattern7":` branch of `_pattern_query` with:

```python
    if pattern == "pattern7":
        search_term = sanitize_lucene_query(parameters["searchTerm"])
        if not search_term:
            raise ValueError("--searchTerm contains no searchable text")
        # Count before slicing (the default --limit 10 silently turned an
        # enumeration into a top-10, study P21), and name the documents each
        # entity was observed in so a caller can cite them (P24).
        return (
            f"""
            CALL db.index.fulltext.queryNodes('entity_name_ft', $searchTerm, {{limit: $countCap}})
            YIELD node AS e, score AS ftScore
            WITH collect({{e: e, s: ftScore}}) AS raw
            WITH raw, size(raw) AS _rawHits
            UNWIND raw AS hit
            WITH _rawHits, hit.e AS e, hit.s AS ftScore
            WHERE {domain_predicate("e")}
            WITH _rawHits, e, ftScore
            ORDER BY ftScore DESC, e.name
            WITH _rawHits, collect(e) AS hits
            WITH _rawHits, size(hits) AS _rowsTotal, hits[0..$limit] AS page
            UNWIND range(0, size(page) - 1) AS i
            WITH _rawHits, _rowsTotal, i, page[i] AS e
            CALL (e) {{
              OPTIONAL MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(:DocChunk)-[:PART_OF]->(d:Document)
              WHERE {domain_predicate("d")}
              WITH DISTINCT d
              ORDER BY d.name
              RETURN collect(d {{ .id, .name, .path }})[0..3] AS documents, count(d) AS document_count
            }}
            RETURN e {{.*, label: labels(e)}} AS entityData, documents, document_count, _rowsTotal, _rawHits
            ORDER BY i
            """,
            {
                "domains": parameters["domains"],
                "searchTerm": search_term,
                "limit": parameters.get("limit", 10),
                "countCap": PATTERN7_COUNT_CAP,
            },
        )
```

- [ ] **Step 4: Implement the lifting in `execute_pattern`**

In `execute_pattern`, replace

```python
    rows_total = None
    for row in rows:
        if isinstance(row, dict) and "_rowsTotal" in row:
            rows_total = row.pop("_rowsTotal")
```

with

```python
    rows_total = None
    raw_hits = None
    for row in rows:
        if isinstance(row, dict) and "_rowsTotal" in row:
            rows_total = row.pop("_rowsTotal")
        if isinstance(row, dict) and "_rawHits" in row:
            raw_hits = row.pop("_rawHits")
    if pattern == "pattern7" and rows_total is None:
        rows_total = 0  # no hits at all: the count query returned no row to ride on
```

and replace

```python
    if rows_total is not None:
        result["rows_total"] = rows_total
        result["truncated"] = rows_total > len(rows)
    return result
```

with

```python
    if rows_total is not None:
        result["rows_total"] = rows_total
        result["truncated"] = rows_total > len(rows)
    if pattern == "pattern7":
        result["rows_total_capped"] = bool(raw_hits is not None and raw_hits >= PATTERN7_COUNT_CAP)
        if result["truncated"]:
            result["next"] = f"raise --limit (rows_total={rows_total})"
    return result
```

- [ ] **Step 5: Run the graph query tests**

Run: `uv run --group dev pytest test/test_graph_query.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/graph_query.py test/test_graph_query.py
git commit -m "fix(query): pattern7 reports rows_total/truncated and source documents (H3)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: H4a — document rows name the documents they superseded

**Files:**
- Modify: `artmind/graph_query.py` (`pattern10` `doc_return`, `_chunks_query`)
- Modify: `artmind/vector_query.py` (`vector_search`, `full_text_search` chunk queries)
- Test: `test/test_graph_query.py`, `test/test_vector_query.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_query.py`:

```python
# ── H4a: supersedes on document rows (spec 2026-10-22 §5.4.1) ────────────────

def test_document_rows_carry_supersedes():
    field = "supersedes: [(d)-[:SUPERSEDES]->(older) | older { .id, .name, .path }]"
    for params in ({"domains": ["f"], "documentName": "a.md"},
                   {"domains": ["f"], "documentName": "a.md", "includeHistory": True}):
        cypher, _ = graph_query._pattern_query("pattern10", params)
        assert field in cypher
    assert field in graph_query._chunks_query(0, None)
    assert ".path, .source_path" in graph_query._chunks_query(0, None)
```

Append to `test/test_vector_query.py`:

```python
def test_vector_text_document_block_carries_supersedes(monkeypatch):
    seen = []

    class FakeSession:
        def run(self, cypher, **params):
            seen.append(cypher)
            return []

    class Ctx:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1])
    monkeypatch.setattr(vector_query, "read_session", lambda: Ctx())
    vector_query.vector_search("fiction", "q", 2)
    vector_query.full_text_search("fiction", "harbour", 2)
    chunk_queries = [c for c in seen if "DocChunk" in c or "chunk_text_ft" in c]
    assert chunk_queries
    for cypher in chunk_queries:
        assert "supersedes: [(document)-[:SUPERSEDES]->(older) | older { .id, .name, .path }]" in cypher
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_query.py test/test_vector_query.py -v -k supersedes`
Expected: FAIL (the field is absent).

- [ ] **Step 3: Implement**

In `artmind/graph_query.py`, after `asof_predicate`, add:

```python
def supersedes_field(var: str) -> str:
    """Map-projection entry naming the documents `var` replaced (its outgoing
    SUPERSEDES edges, to live or retired documents). Derived at query time, like
    `superseded_by` is derived by supersession_records (spec 2026-10-22 §5.4.1)."""
    return f"supersedes: [({var})-[:SUPERSEDES]->(older) | older {{ .id, .name, .path }}]"
```

In the `pattern10` branch replace

```python
        doc_return = "d { .id, .name, .path, .source_path, ._domain, .valid_from, .valid_to, .superseded_by } AS document"
```

with

```python
        doc_return = (
            "d { .id, .name, .path, .source_path, ._domain, .valid_from, .valid_to, .superseded_by, "
            + supersedes_field("d") + " } AS document"
        )
```

In `_chunks_query` replace

```python
           d {{ .id, .name, .path, .source_path, ._domain, .valid_from, .valid_to, .superseded_by }} AS document{neighbor_return}
```

with

```python
           d {{ .id, .name, .path, .source_path, ._domain, .valid_from, .valid_to, .superseded_by, {supersedes_field("d")} }} AS document{neighbor_return}
```

In `artmind/vector_query.py`, add `supersedes_field` to the `from artmind.graph_query import (...)` block at the top. Then in both `vector_search` and `full_text_search`, replace the chunk-query line

```python
           document {{ .id, .name, .path, .source_path, ._domain }} AS document,
```

with

```python
           document {{ .id, .name, .path, .source_path, ._domain, {supersedes_field("document")} }} AS document,
```

(`grep -n "document {{ .id, .name, .path, .source_path, ._domain }} AS document" artmind/vector_query.py` shows the two lines; both are inside f-strings.)

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_graph_query.py test/test_vector_query.py -v`
Expected: all PASS (the older `test_vector_search_shapes_results_and_excludes_embedding` is unaffected: its fake rows already define `document`).

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py artmind/vector_query.py test/test_graph_query.py test/test_vector_query.py
git commit -m "feat(query): document rows name the documents they superseded (H4a)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: H4b/c — `pattern10 --includeHistory`, and a hint when only retired documents match

**Files:**
- Modify: `artmind/graph_query.py` (`pattern10` branch; new `pattern10_history_matches`)
- Modify: `artmind/cli.py` (`graph_pattern10`)
- Test: `test/test_graph_query.py`, `test/test_pattern10_history_cli.py` (create)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_graph_query.py`:

```python
# ── H4b: --includeHistory (spec 2026-10-22 §5.4.2) ───────────────────────────

def test_pattern10_include_history_reaches_history_labels():
    cypher, _ = graph_query._pattern_query(
        "pattern10", {"domains": ["f"], "documentName": "a", "includeHistory": True}
    )
    assert "DocumentHistory" in cypher and "DocChunkHistory" in cypher
    cypher, _ = graph_query._pattern_query("pattern10", {"domains": ["f"], "documentName": "a"})
    assert "DocumentHistory" not in cypher


def test_pattern10_history_matches_queries_retired_documents(monkeypatch):
    sent = {}

    def fake_run(cypher, params):
        sent["cypher"], sent["params"] = cypher, params
        return [{"name": "policy_alpha.md"}]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    assert graph_query.pattern10_history_matches(["f"], "alpha") == ["policy_alpha.md"]
    assert "MATCH (d:DocumentHistory)" in sent["cypher"]
    assert sent["params"] == {"domains": ["f"], "documentName": "alpha", "limit": 5}
```

Create `test/test_pattern10_history_cli.py`:

```python
"""H4b/c (spec 2026-10-22 §5.4.2-3): --includeHistory, the --asOf alias, retired-only hints."""

import json

from click.testing import CliRunner

from artmind import graph_query
from artmind.cli import cli


def _run(args):
    result = CliRunner().invoke(cli, ["query", "graph", "pattern10", "--domain", "f", "--compact", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_include_history_is_passed_through(monkeypatch):
    seen = {}

    def fake_execute(**kw):
        seen.update(kw)
        return {"rows": [{"document": {}, "chunk": {}}]}

    monkeypatch.setattr(graph_query, "execute_pattern", fake_execute)
    _run(["--documentName", "a.md", "--includeHistory"])
    assert seen["includeHistory"] is True and seen["as_of"] is None


def test_as_of_is_an_alias_with_a_warning(monkeypatch):
    seen = {}

    def fake_execute(**kw):
        seen.update(kw)
        return {"rows": [{"document": {}, "chunk": {}}]}

    monkeypatch.setattr(graph_query, "execute_pattern", fake_execute)
    payload = _run(["--documentName", "a.md", "--asOf", "2026-01-01"])
    assert seen["includeHistory"] is True
    assert any("--includeHistory" in w for w in payload["warnings"])


def test_retired_only_match_says_add_include_history(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", lambda domains, name: ["policy_alpha.md"])
    payload = _run(["--documentName", "alpha"])
    assert payload["history_matches"] == ["policy_alpha.md"]
    assert payload["next"] == "add --includeHistory: 1 retired document(s) match"


def test_no_match_anywhere_has_no_hint(monkeypatch):
    monkeypatch.setattr(graph_query, "execute_pattern", lambda **kw: {"rows": []})
    monkeypatch.setattr(graph_query, "pattern10_history_matches", lambda domains, name: [])
    payload = _run(["--documentName", "nothing"])
    assert "next" not in payload and "history_matches" not in payload
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_graph_query.py test/test_pattern10_history_cli.py -v -k "history or as_of or retired or no_match_anywhere"`
Expected: FAIL (`includeHistory` ignored; no `pattern10_history_matches`; no `--includeHistory` option).

- [ ] **Step 3: Implement in `artmind/graph_query.py`**

In the `pattern10` branch replace

```python
        as_of = parameters.get("asOf")
```

with

```python
        include_history = bool(parameters.get("includeHistory") or parameters.get("asOf"))
```

and `if as_of:` (the line that picks the UNION query) with `if include_history:`. Update the branch's leading comment's first sentence to say that `--includeHistory` (with `--asOf` as its alias) is the presence flag.

After `execute_pattern`, add:

```python
def pattern10_history_matches(domains: "str | Sequence[str]", document_name: str, limit: int = 5) -> list[str]:
    """Names of RETIRED documents matching a pattern10 --documentName (spec
    2026-10-22 §5.4.3). Asked only after the current-only query found nothing,
    so a caller holding a retired name learns to add --includeHistory (Q10)."""
    domains = normalize_domains(domains)
    rows = _run_read_query(
        f"""
        MATCH (d:DocumentHistory)
        WHERE {domain_predicate("d")} AND toLower(d.name) CONTAINS toLower($documentName)
        RETURN DISTINCT d.name AS name
        ORDER BY name
        LIMIT $limit
        """,
        {"domains": domains, "documentName": document_name, "limit": limit},
    )
    return [r["name"] for r in rows if r.get("name")]
```

- [ ] **Step 4: Implement in `artmind/cli.py`**

Replace the whole `graph_pattern10` command (decorators and body) with:

```python
@graph.command("pattern10")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--documentName", "document_name", required=True, help="Document name (substring match)")
@click.option("--includeHistory", "include_history", is_flag=True, help="Also reach retired/superseded documents and chunks (:DocumentHistory / :DocChunkHistory). Omit for current content only")
@click.option("--asOf", "as_of", default=None, help="Alias of --includeHistory kept for old callers: any value turns history on. It is NOT a date filter here (chunks carry no date of their own)")
@click.option("--limit", type=int, default=graph_query.PATTERN10_DEFAULT_LIMIT, show_default=True, help="Max chunks; rows_total carries the full count")
@click.option("--offset", type=int, default=0, show_default=True, help="Skip this many chunks (ordered by chunk id) before --limit; page past a truncated result with offset += limit")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question", required=False)
def graph_pattern10(
    domain: tuple, document_name: str, include_history: bool, as_of: str | None, limit: int, offset: int,
    compact: bool, question: str | None,
) -> None:
    """Retrieve all text chunks for a named document (current only, or with history)."""
    domains = _parse_domains(domain)
    history = include_history or as_of is not None
    try:
        result = graph_query.execute_pattern(
            domains=domains, pattern="pattern10", question=question, as_of=None,
            documentName=document_name, includeHistory=history, limit=limit, offset=offset,
        )
        if not history and not result.get("rows"):
            matches = graph_query.pattern10_history_matches(domains, document_name)
            if matches:
                result["history_matches"] = matches
                result["next"] = f"add --includeHistory: {len(matches)} retired document(s) match"
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_of is not None:
        result.setdefault("warnings", []).append(
            "--asOf on pattern10 only switches history on (use --includeHistory); it does not filter by date"
        )
    _echo_json(result, compact)
```

(`as_of=None` is passed to `execute_pattern` on purpose: `resolve_as_of` would otherwise reject a non-ISO value that the old flag accepted as a presence marker.)

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_graph_query.py test/test_pattern10_history_cli.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/graph_query.py artmind/cli.py test/test_graph_query.py test/test_pattern10_history_cli.py
git commit -m "feat(query): pattern10 --includeHistory and a hint when only retired documents match (H4b/c)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: H4d — `document-history` tolerates a missing suffix and suggests close names

**Files:**
- Modify: `artmind/graph_query.py` (`document_history`)
- Test: `test/test_entity_document_history.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_entity_document_history.py`:

```python
# ── H4d: document-history name matching (spec 2026-10-22 §5.4.4) ─────────────

def _dispatch(monkeypatch, *, exact_rows, suffix_names, closest_names):
    from artmind import graph_query

    calls = []

    def fake_run(cypher, params):
        calls.append((cypher, params))
        if "IN $names" in cypher:
            return [{"name": n} for n in suffix_names]
        if "CONTAINS toLower($fragment)" in cypher:
            return [{"name": n} for n in closest_names]
        if params.get("documentName") in exact_rows:
            return exact_rows[params["documentName"]]
        return []

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    return calls


_ROW = {"document": {"id": "d1", "name": "policy_alpha.md", "path": "p/policy_alpha.md", "version": 2},
        "retired": False, "observations": []}


def test_a_unique_suffixed_name_is_used_with_a_warning(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={"policy_alpha.md": [_ROW]},
                      suffix_names=["policy_alpha.md"], closest_names=[])
    result = graph_query.document_history(["f"], "policy_alpha")
    assert result["rows"][0]["document"]["name"] == "policy_alpha.md"
    assert result["matched_name"] == "policy_alpha.md"
    assert result["warnings"] == ["no document named 'policy_alpha'; matched 'policy_alpha.md'"]
    suffix_call = next(p for c, p in calls if "IN $names" in c)
    assert suffix_call["names"] == ["policy_alpha.md", "policy_alpha.pdf", "policy_alpha.docx",
                                    "policy_alpha.txt", "policy_alpha.html"]


def test_no_match_returns_closest_names_and_next(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=[], closest_names=["policy_alpha.md", "policy_alpha_v2.md"])
    result = graph_query.document_history(["f"], "alpha")
    assert result["rows"] == []
    assert result["closest"] == ["policy_alpha.md", "policy_alpha_v2.md"]
    assert result["next"].startswith("artmind query document-history --documentName")


def test_two_suffixed_candidates_are_not_guessed(monkeypatch):
    from artmind import graph_query

    _dispatch(monkeypatch, exact_rows={}, suffix_names=["a.md", "a.pdf"], closest_names=["a.md", "a.pdf"])
    result = graph_query.document_history(["f"], "a")
    assert result["rows"] == [] and result["closest"] == ["a.md", "a.pdf"]
    assert "matched_name" not in result


def test_an_exact_name_needs_no_second_query(monkeypatch):
    from artmind import graph_query

    calls = _dispatch(monkeypatch, exact_rows={"policy_alpha.md": [_ROW]}, suffix_names=[], closest_names=[])
    result = graph_query.document_history(["f"], "policy_alpha.md")
    assert len(calls) == 1 and "warnings" not in result
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_entity_document_history.py -v -k "suffix or closest or exact_name"`
Expected: FAIL (`matched_name`/`closest` missing).

- [ ] **Step 3: Implement**

In `artmind/graph_query.py`, before `def document_history`, add:

```python
DOCUMENT_NAME_SUFFIXES = (".md", ".pdf", ".docx", ".txt", ".html")
DOCUMENT_CLOSEST_LIMIT = 5


def _document_names_like(domains: list[str], *, names: list[str] | None = None, fragment: str | None = None) -> list[str]:
    """Live or retired document names: exact (casefolded) members of `names`, or containing `fragment`."""
    if names is not None:
        where, params = "toLower(d.name) IN $names", {"names": names}
    else:
        where, params = "toLower(d.name) CONTAINS toLower($fragment)", {"fragment": fragment}
    rows = _run_read_query(
        f"""
        MATCH (d)
        WHERE (d:Document OR d:DocumentHistory) AND {domain_predicate("d")} AND {where}
        RETURN DISTINCT d.name AS name
        ORDER BY name
        LIMIT $limit
        """,
        {"domains": domains, "limit": DOCUMENT_CLOSEST_LIMIT, **params},
    )
    return [r["name"] for r in rows if r.get("name")]
```

Inside `document_history`, rename the existing query-and-shape code into a helper so it can run twice. Concretely: move everything from `params = {` through the end of the `for row in found[...]` loop into a nested function

```python
    def _rows_for(name: str) -> tuple[list[dict], bool]:
        params = {
            "domains": domains,
            "documentName": name,
            "limit": DOCUMENT_HISTORY_MAX_DOCUMENTS + 1,
        }
        cypher = ...  # the existing cypher, unchanged
        found = _run_read_query(cypher, params)
        rows = []
        for row in found[:DOCUMENT_HISTORY_MAX_DOCUMENTS]:
            ...  # the existing loop body, unchanged
        return rows, len(found) > DOCUMENT_HISTORY_MAX_DOCUMENTS
```

(keep the existing Cypher text and loop body exactly; only their indentation changes), then replace the old `return {...}` with:

```python
    rows, truncated = _rows_for(document_name)
    out: dict = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "document_history",
        "document_name": document_name,
        "truncated": truncated,
        "rows": rows,
    }
    if rows:
        return out
    # Nothing by exact name, path or id (spec 2026-10-22 §5.4.4): a unique
    # suffixed name is used, with a warning; otherwise name the closest ones.
    low = document_name.casefold()
    suffixed = (
        []
        if low.endswith(DOCUMENT_NAME_SUFFIXES)
        else _document_names_like(domains, names=[low + s for s in DOCUMENT_NAME_SUFFIXES])
    )
    if len(suffixed) == 1:
        rows, truncated = _rows_for(suffixed[0])
        out.update(rows=rows, truncated=truncated, matched_name=suffixed[0],
                   warnings=[f"no document named {document_name!r}; matched {suffixed[0]!r}"])
        return out
    stem = document_name
    for suffix in DOCUMENT_NAME_SUFFIXES:
        if low.endswith(suffix):
            stem = document_name[: -len(suffix)]
            break
    out["closest"] = _document_names_like(domains, fragment=stem)
    out["next"] = "artmind query document-history --documentName <one of closest> (names match exactly, ignoring case)"
    return out
```

The `suffix_call["names"]` assertion in the test expects the list built from the casefolded input; keep `low + s`.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_entity_document_history.py -v`
Expected: all PASS, including the file's older tests (the exact-match path is unchanged).

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py test/test_entity_document_history.py
git commit -m "feat(query): document-history matches a missing suffix and suggests close names (H4d)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Skill lines for H1 and H4

**Files:**
- Modify: `artmind/skills/artmind-query/SKILL.md`
- Modify: `artmind/skills/artmind-query/references/temporal.md`
- Test: `test/test_query_skill_structural_schema.py`, `test/test_query_skill_views_step.py` (run only)

- [ ] **Step 1: Edit `SKILL.md`**

Replace

```
and is therefore current by construction; only `pattern10 --asOf` (see Retrieve) reaches
into history at all.
```

with

```
and is therefore current by construction; only `pattern10 --includeHistory` (see Retrieve)
reaches into history at all, and a document row's `supersedes` names the version it replaced.
```

Replace the table row that starts `| All chunks of / summarize a document |` with

```
| All chunks of / summarize a document | `artmind query graph pattern10 --documentName "<name>" [--limit N] [--offset N]` — add `--includeHistory` for retired/superseded versions; zero rows with `history_matches` means only a retired version matches |
```

In the text2cypher row (`| Aggregations, custom filters, multi-hop combinations none of the above cover |`), replace `run \`--dry-run\` first to inspect the Cypher;` with

```
run `--dry-run` first to inspect the Cypher, then run it without `--dry-run` (a dry run executes nothing and returns no rows);
```

- [ ] **Step 2: Edit `references/temporal.md`**

Replace

```
  `valid_from`. On `pattern10` specifically it is a **presence flag**, not a date value:
  passing it (any value) additionally matches `:DocumentHistory`/`:DocChunkHistory` for
  that document, surfacing retired chunks alongside current ones; omitting it matches
  only the current document.
```

with

```
  `valid_from`. On `pattern10` use **`--includeHistory`** instead: it additionally
  matches `:DocumentHistory`/`:DocChunkHistory` for that document, surfacing retired
  chunks alongside current ones; omitting it matches only the current document.
  (`--asOf` still works there as an alias, but it is a switch, not a date filter.)
```

and in `vector-text`, `chunks`, `docs list`, `pattern10`, `db timeline`, `db sql` (the list of commands that keep `--asOf`) remove `` `pattern10`, `` so it reads `` `vector-text`, `chunks`, `docs list`, `db timeline`, `db sql` ``.

- [ ] **Step 3: Run the skill tests**

Run: `uv run --group dev pytest test/test_query_skill_structural_schema.py test/test_query_skill_views_step.py -v`
Expected: all PASS (neither pins these lines).

- [ ] **Step 4: Commit**

```bash
git add artmind/skills/artmind-query/SKILL.md artmind/skills/artmind-query/references/temporal.md
git commit -m "docs(skills): run text2cypher after a dry run; pattern10 --includeHistory

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Phase A checkpoint:** `uv run --group dev pytest test/test_graph_query.py test/test_vector_query.py test/test_text2cypher.py test/test_text2sql.py test/test_llm_providers.py test/test_traces_path.py test/test_traces_analysis.py test/test_entity_document_history.py test/test_dry_run_cli.py test/test_pattern10_history_cli.py test/test_cli_guide.py -q` → all PASS.


---

# Phase B — The plan core (lexical classifier)

### Task 9: Package skeleton, `routes.yaml` and the routes loader

**Files:**
- Create: `artmind/query_plan/__init__.py`, `artmind/query_plan/routes.yaml`, `artmind/query_plan/routes.py`
- Modify: `pyproject.toml` (package-data)
- Test: `test/test_query_plan_routes.py` (create)

- [ ] **Step 1: Create the package marker**

`artmind/query_plan/__init__.py`:

```python
"""`artmind query plan`: deterministic routing in code (spec
docs/superpowers/specs/2026-10-22-query-plan-design.md)."""
```

- [ ] **Step 2: Write the failing tests**

Create `test/test_query_plan_routes.py`:

```python
"""routes.yaml and its loader (spec 2026-10-22 §4.3)."""

import shlex
from pathlib import Path

import pytest

from artmind.query_plan import routes

EXPECTED_IDS = {
    "existence", "enumeration", "aggregate", "superlative", "entity_profile", "entity_history",
    "time_history", "conflicts", "hierarchy", "path", "document", "inventory", "procedure",
    "structured", "insights", "filing", "search",
}
SKILL_DIR = Path(routes.__file__).resolve().parents[1] / "skills" / "artmind-query"


def test_the_packaged_routes_load():
    loaded = routes.load_routes()
    assert {r.id for r in loaded} == EXPECTED_IDS
    assert all(r.evidence for r in loaded)


def _command_and_options(template: str):
    from artmind.cli import cli

    filled = routes.render(template, {name: "x" for name in routes.PLACEHOLDERS})
    tokens = shlex.split(filled)
    assert tokens[0] == "artmind", template
    command, i = cli, 1
    while i < len(tokens) and hasattr(command, "commands") and tokens[i] in command.commands:
        command = command.commands[tokens[i]]
        i += 1
    known = {opt for param in command.params for opt in getattr(param, "opts", [])}
    used = [t for t in tokens[i:] if t.startswith("--")]
    return command, used, known


@pytest.mark.parametrize("route", routes.load_routes(), ids=lambda r: r.id)
def test_every_template_names_a_real_command_and_real_options(route):
    for template in [*route.steps, *(f.cmd for f in route.fallbacks)]:
        command, used, known = _command_and_options(template)
        assert not hasattr(command, "commands") or getattr(command, "invoke_without_command", False), template
        assert set(used) <= known, f"{template}: unknown {sorted(set(used) - known)}"


@pytest.mark.parametrize("route", routes.load_routes(), ids=lambda r: r.id)
def test_every_read_file_exists(route):
    for ref in route.read:
        assert (SKILL_DIR / ref).is_file(), ref


def _one(**over):
    base = {"id": "r1", "description": "A shape.", "triggers": ["x"], "needs": [],
            "steps": ["artmind query vector-text --domain {domains} {question} --compact"],
            "fallbacks": [], "read": [], "evidence": ["S1"]}
    return {"version": 1, "routes": [{**base, **over}]}


@pytest.mark.parametrize("data, message", [
    (_one(id="view"), "reserved"),
    (_one(id="Bad-Id"), "snake_case"),
    (_one(steps=["artmind query vector-text --domain {nope} --compact"]), "unknown placeholder"),
    (_one(steps=["artmind query vector-text --domain {domains}"]), "--compact"),
    (_one(evidence=[]), "must not be empty"),
    (_one(evidence=["Q7"]), "evidence"),
    (_one(fallbacks=[{"when": "later", "cmd": "artmind query chunks --compact"}]), "fallbacks"),
    (_one(needs=["magic"]), "unknown needs"),
    (_one(triggers=["on {when}"]), "unknown slot"),
    (_one(description="x" * 301), "description"),
])
def test_rule_violations_are_named(data, message):
    with pytest.raises(routes.RouteError, match=message):
        routes.parse_routes(data)


def test_duplicate_ids_are_refused():
    data = _one()
    data["routes"].append(dict(data["routes"][0]))
    with pytest.raises(routes.RouteError, match="duplicate"):
        routes.parse_routes(data)


def test_render_quotes_values_and_refuses_missing_ones():
    template = "artmind query vector-text --domain {domains} {question} --compact"
    assert routes.render(template, {"domains": "a,b", "question": "what's new?"}) == (
        "artmind query vector-text --domain a,b 'what'\"'\"'s new?' --compact"
    )
    assert routes.render(template, {"domains": "a", "question": ""}) is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.query_plan.routes'`.

- [ ] **Step 4: Write `artmind/query_plan/routes.py`**

```python
"""The routing data `artmind query plan` reads (spec 2026-10-22 §4.3).

`routes.yaml` is the one place routing lives: the planner fills its step
templates, and the test suite checks every command and option a template
names against the Click tree. A route's `description` is the text a decision
model reads for that option, so it describes the question shape.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

ROUTES_FILE = Path(__file__).with_name("routes.yaml")
RESERVED_IDS = frozenset({"view"})
NEEDS = frozenset({"entity", "two_entities", "document", "date", "class", "store", "insights_snapshot"})
PLACEHOLDERS = frozenset({
    "domains", "entity_id", "entity_ids", "entity_name", "other_entity_id", "document",
    "date", "year", "class", "question", "keywords", "view", "view_params",
})
TRIGGER_SLOTS = frozenset({"date", "year", "entity", "document"})
FALLBACK_WHEN = frozenset({"zero_rows", "truncated", "no_document", "needs_disambiguation", "error"})
MAX_DESCRIPTION = 300

_KEYS = frozenset({"id", "description", "triggers", "needs", "steps", "fallbacks", "read", "evidence"})
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EVIDENCE_RE = re.compile(r"^[SP]\d+$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


class RouteError(ValueError):
    """routes.yaml breaks a rule of spec §4.3."""


@dataclass(frozen=True)
class Fallback:
    when: str
    cmd: str


@dataclass(frozen=True)
class Route:
    id: str
    description: str
    triggers: tuple[str, ...]
    needs: tuple[str, ...]
    steps: tuple[str, ...]
    fallbacks: tuple[Fallback, ...]
    read: tuple[str, ...]
    evidence: tuple[str, ...]


def placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER_RE.findall(template))


def _strings(value, where: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    value = [] if value is None else value
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise RouteError(f"{where} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise RouteError(f"{where} must not be empty")
    return tuple(v.strip() for v in value)


def _template(cmd, where: str) -> str:
    if not isinstance(cmd, str) or not cmd.startswith("artmind "):
        raise RouteError(f"{where} must be a command starting with 'artmind '")
    unknown = placeholders(cmd) - PLACEHOLDERS
    if unknown:
        raise RouteError(f"{where} uses unknown placeholder(s) {sorted(unknown)}")
    if "--compact" not in cmd.split():
        raise RouteError(f"{where} must pass --compact")
    return cmd


def parse_routes(data) -> tuple[Route, ...]:
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("routes"), list):
        raise RouteError("routes.yaml must be a mapping with `version: 1` and a `routes` list")
    out: list[Route] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["routes"]):
        where = f"routes[{i}]"
        if not isinstance(raw, dict):
            raise RouteError(f"{where} must be a mapping")
        extra = set(raw) - _KEYS
        if extra:
            raise RouteError(f"{where} has unknown key(s) {sorted(extra)}")
        rid = raw.get("id")
        if not isinstance(rid, str) or not _ID_RE.match(rid):
            raise RouteError(f"{where}.id must be snake_case")
        if rid in RESERVED_IDS:
            raise RouteError(f"route id {rid!r} is reserved")
        if rid in seen:
            raise RouteError(f"duplicate route id {rid!r}")
        seen.add(rid)
        where = f"route {rid}"
        description = raw.get("description")
        if not isinstance(description, str) or not description.strip() or len(description.strip()) > MAX_DESCRIPTION:
            raise RouteError(f"{where}: description must be 1-{MAX_DESCRIPTION} characters")
        triggers = _strings(raw.get("triggers"), f"{where}.triggers")
        for trigger in triggers:
            bad = placeholders(trigger) - TRIGGER_SLOTS
            if bad:
                raise RouteError(f"{where}: trigger {trigger!r} uses unknown slot(s) {sorted(bad)}")
        needs = _strings(raw.get("needs"), f"{where}.needs")
        if set(needs) - NEEDS:
            raise RouteError(f"{where}: unknown needs {sorted(set(needs) - NEEDS)}")
        steps = tuple(
            _template(cmd, f"{where}.steps[{j}]")
            for j, cmd in enumerate(_strings(raw.get("steps"), f"{where}.steps", allow_empty=False))
        )
        fallbacks = []
        for j, fb in enumerate(raw.get("fallbacks") or []):
            if not isinstance(fb, dict) or set(fb) != {"when", "cmd"} or fb["when"] not in FALLBACK_WHEN:
                raise RouteError(f"{where}.fallbacks[{j}] must be {{when, cmd}} with when in {sorted(FALLBACK_WHEN)}")
            fallbacks.append(Fallback(fb["when"], _template(fb["cmd"], f"{where}.fallbacks[{j}]")))
        read = _strings(raw.get("read"), f"{where}.read")
        evidence = _strings(raw.get("evidence"), f"{where}.evidence", allow_empty=False)
        bad_evidence = [e for e in evidence if not _EVIDENCE_RE.match(e)]
        if bad_evidence:
            raise RouteError(f"{where}: evidence ids must look like S<n> or P<n>, got {bad_evidence}")
        out.append(Route(rid, description.strip(), triggers, needs, steps, tuple(fallbacks), read, evidence))
    if not out:
        raise RouteError("routes.yaml defines no routes")
    return tuple(out)


@lru_cache(maxsize=1)
def load_routes() -> tuple[Route, ...]:
    return parse_routes(yaml.safe_load(ROUTES_FILE.read_text(encoding="utf-8")))


def render(template: str, context: dict) -> str | None:
    """Fill a template's placeholders, shell-quoted; None when any value is missing."""
    missing = False

    def substitute(match: re.Match) -> str:
        nonlocal missing
        value = context.get(match.group(1))
        if value is None or value == "":
            missing = True
            return ""
        return shlex.quote(str(value))

    text = _PLACEHOLDER_RE.sub(substitute, template)
    return None if missing else text
```

- [ ] **Step 5: Write `artmind/query_plan/routes.yaml`**

```yaml
# Routing data for `artmind query plan`
# (spec docs/superpowers/specs/2026-10-22-query-plan-design.md §4.3).
#
# The one place routing lives. A route's `description` is what a decision model
# reads for that option, so it describes the QUESTION SHAPE, not the commands.
# Every route cites the evidence it came from: scenario (S<n>) and pattern (P<n>)
# ids in docs/query-routing-study/. Every template passes --compact; tests check
# each command and option against the Click tree.
version: 1
routes:
  - id: existence
    description: Whether something specific happened or is mentioned at all, or happened again (did I visit X again, any reference to X).
    triggers: ["did i", "did we", "any reference to", "any mention of", "ever", "again", "is there any", "was there any"]
    needs: [entity]
    steps:
      - "artmind query graph pattern7 --domain {domains} --searchTerm {entity_name} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query vector-text --domain {domains} {question} --compact"}
    read: []
    evidence: [S25, P25]

  - id: enumeration
    description: A complete list of every recorded item of some kind (all the X, every Y, the recorded Z).
    triggers: ["all the", "every", "list all", "list the", "recorded", "complete list", "each of", "which ones"]
    needs: []
    steps:
      - "artmind query graph pattern7 --domain {domains} --searchTerm {keywords} --limit 50 --compact"
    fallbacks:
      - {when: truncated, cmd: "artmind query graph pattern7 --domain {domains} --searchTerm {keywords} --limit 200 --compact"}
    read: []
    evidence: [S24, P21]

  - id: aggregate
    description: A count, total, average or frequency over events or entities, when no saved query fits.
    triggers: ["how many", "how often", "number of", "count of", "total", "average", "sum of", "frequency"]
    needs: []
    steps:
      - "artmind query graph text2cypher --domain {domains} {question} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query vector-text --domain {domains} {question} --compact"}
    read: []
    evidence: [S3, P2]

  - id: superlative
    description: The favourite, most, least, top or best item of a kind; a ranking that has to be inferred, not a stored fact.
    triggers: ["favourite", "favorite", "the most", "the least", "top", "best", "worst", "biggest", "most often"]
    needs: []
    steps:
      - "artmind query insights list --domain {domains} --compact"
      - "artmind query graph text2cypher --domain {domains} {question} --compact"
    fallbacks: []
    read: []
    evidence: [S22, P22]

  - id: entity_profile
    description: Who or what one specific person, place, organisation or thing is, and what is known about it.
    triggers: ["tell me about", "who is", "what is", "describe", "what do we know about", "profile of"]
    needs: [entity]
    steps:
      - "artmind query entity-context --domain {domains} --entityId {entity_id} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query vector-text --domain {domains} {question} --compact"}
    read: []
    evidence: [S10]

  - id: entity_history
    description: How what is known about one entity changed over time, including facts later corrected or removed.
    triggers: ["history of", "over time", "changed over", "evolved", "timeline of", "used to be"]
    needs: [entity]
    steps:
      - "artmind query entity-history --domain {domains} --entityId {entity_id} --compact"
    fallbacks: []
    read: [references/temporal.md]
    evidence: [S5, P5]

  - id: time_history
    description: Which version of a document applied on a date, or what an earlier, superseded version of a document said.
    triggers: ["as of {date}", "on {date}", "before {date}", "earlier version", "previous version", "which version", "used to say", "at the time", "in force"]
    needs: [document]
    steps:
      - "artmind query graph pattern10 --domain {domains} --documentName {document} --includeHistory --compact"
      - "artmind query document-history --domain {domains} --documentName {document} --compact"
    fallbacks:
      - {when: no_document, cmd: "artmind query vector-text --domain {domains} {question} --compact"}
    read: [references/temporal.md]
    evidence: [S14, S20, P12]

  - id: conflicts
    description: Whether documents or sources disagree or contradict each other, or which of two versions applies.
    triggers: ["conflict", "conflicting", "contradict", "disagree", "inconsistent", "differ", "which applies", "which one applies", "discrepancy"]
    needs: []
    steps:
      - "artmind query graph conflicts --domain {domains} --status all --compact"
      - "artmind query vector-text --domain {domains} {question} --compact"
    fallbacks: []
    read: [references/conflicts.md]
    evidence: [S13, P13]

  - id: hierarchy
    description: Who or what sits under, above or within something in a chain (reports, departments, parts of a whole).
    triggers: ["reports to", "report to", "under", "chain of", "hierarchy", "who manages", "manager of", "part of", "belongs to"]
    needs: [entity]
    steps:
      - "artmind query graph hierarchy --domain {domains} --entityId {entity_id} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query entity-context --domain {domains} --entityId {entity_id} --compact"}
    read: [references/hierarchy.md]
    evidence: [S11]

  - id: path
    description: How two named people, places or things are related or connected to each other.
    triggers: ["related to", "connection between", "connected to", "relationship between", "link between", "how are", "know each other"]
    needs: [two_entities]
    steps:
      - "artmind query graph pattern6 --domain {domains} --entityId1 {entity_id} --entityId2 {other_entity_id} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query graph pattern5 --domain {domains} --entityId1 {entity_id} --entityId2 {other_entity_id} --mode shortest --compact"}
    read: []
    evidence: [S12]

  - id: document
    description: What one named document says, or a summary of that document.
    triggers: ["summarise {document}", "summarize {document}", "what does {document} say", "{document}"]
    needs: [document]
    steps:
      - "artmind query graph pattern10 --domain {domains} --documentName {document} --compact"
    fallbacks:
      - {when: zero_rows, cmd: "artmind query graph pattern10 --domain {domains} --documentName {document} --includeHistory --compact"}
    read: []
    evidence: [S16]

  - id: inventory
    description: Which documents of some kind exist in a domain or corpus (what SOPs, which policies, what notes).
    triggers: ["what documents", "which documents", "what kinds of", "list the documents", "what are the main", "which policies", "what policies", "what sops"]
    needs: []
    steps:
      - "artmind query domains-overview --compact"
    fallbacks: []
    read: []
    evidence: [S18, P8]

  - id: procedure
    description: How to do something, or the steps, checks and escalation drawn from several procedures, policies or guides.
    triggers: ["how do i", "how do we", "how to", "steps to", "procedure for", "process for", "what should", "what happens when", "what are the steps"]
    needs: []
    steps:
      - "artmind query vector-text --domain {domains} {question} --topK 10 --compact"
    fallbacks: []
    read: []
    evidence: [S19, P14]

  - id: structured
    description: Numbers held in structured tables (accounts, cases, customers), alone or joined with what policy says about them.
    triggers: ["how many customers", "per customer", "accounts", "balance", "in the table", "records in"]
    needs: [store]
    steps:
      - "artmind db bridge --domain {domains} --compact"
      - "artmind query text2sql --domain {domains} {question} --compact"
    fallbacks: []
    read: [references/structured-store.md]
    evidence: [S7, S8, S9, P15]

  - id: insights
    description: What changed recently, who or what matters most, or who the user lost touch with; patterns across the whole domain.
    triggers: ["what changed", "who matters", "lost touch", "trend", "lately", "recently", "insight", "patterns"]
    needs: []
    steps:
      - "artmind query insights list --domain {domains} --compact"
    fallbacks: []
    read: []
    evidence: [S6, P6]

  - id: filing
    description: Notes filed under a tag, a project or an area.
    triggers: ["tagged", "tag", "project", "area", "filed under"]
    needs: []
    steps:
      - "artmind query graph filing-listing --domain {domains} --compact"
    fallbacks: []
    read: []
    evidence: [S17]

  - id: search
    description: Any other factual question, answered from the text of the documents.
    triggers: []
    needs: []
    steps:
      - "artmind query vector-text --domain {domains} {question} --compact"
    fallbacks: []
    read: []
    evidence: [S2]
```

- [ ] **Step 6: Ship the YAML in the wheel**

In `pyproject.toml`, in `[tool.setuptools.package-data]`, add `"query_plan/*.yaml"` to the `artmind = [...]` list (after `"insights/aliases.yaml"`).

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_routes.py -v`
Expected: all PASS. If a template test fails on an option, the route names an option the CLI lacks: fix the template, never the test.

- [ ] **Step 8: Commit**

```bash
git add artmind/query_plan/__init__.py artmind/query_plan/routes.py artmind/query_plan/routes.yaml pyproject.toml test/test_query_plan_routes.py
git commit -m "feat(plan): routes.yaml and its validating loader

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Per-domain plan config

**Files:**
- Modify: `paths.py`
- Create: `artmind/query_plan/config.py`
- Test: `test/test_query_plan_config.py` (create)

- [ ] **Step 1: Add the directory constant**

In `paths.py`, after the `DOMAIN_INSIGHTS_DIR = ...` line, add:

```python
# `query plan` per-domain config (spec 2026-10-22 §4.5): one <domain>.yaml each.
# User data like views and insights tuning -- never seeded -- and outside
# schemas/ so it never changes projection.schema_set_hash().
DOMAIN_PLAN_DIR = ARTMIND_HOME / "domains" / "plan"
```

- [ ] **Step 2: Write the failing tests**

Create `test/test_query_plan_config.py`:

```python
"""Per-domain plan config (spec 2026-10-22 §4.5)."""

import pytest

from artmind.query_plan import config


@pytest.fixture(autouse=True)
def _plan_dir(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    return tmp_path


def _write(tmp_path, domain, text):
    (tmp_path / f"{domain}.yaml").write_text(text)


def test_no_file_means_defaults():
    cfg = config.load_config("journal")
    assert cfg.narrator is None
    assert cfg.confidence_threshold == 0.7
    assert cfg.first_person == ("i", "me", "my", "mine", "myself")
    assert cfg.classifier == "auto"


def test_a_full_file_is_read(tmp_path):
    _write(tmp_path, "journal", "narrator: {entity_class: person, name: Diarist}\n"
                                "confidence_threshold: 0.8\nclassifier: lexical\ndecision_model: tev1\n")
    cfg = config.load_config("journal")
    assert cfg.narrator == config.Narrator("PERSON", "Diarist")
    assert (cfg.confidence_threshold, cfg.classifier, cfg.decision_model) == (0.8, "lexical", "tev1")


def test_a_parent_file_applies_to_children_unless_they_have_their_own(tmp_path):
    _write(tmp_path, "bank", "confidence_threshold: 0.9\n")
    _write(tmp_path, "bank.cases", "confidence_threshold: 0.6\n")
    assert config.load_config("bank.policy").confidence_threshold == 0.9
    assert config.load_config("bank.cases").confidence_threshold == 0.6


@pytest.mark.parametrize("text, message", [
    ("colour: blue\n", "unknown key"),
    ("confidence_threshold: 1.5\n", "confidence_threshold"),
    ("confidence_threshold: true\n", "confidence_threshold"),
    ("classifier: llm\n", "classifier"),
    ("narrator: Diarist\n", "narrator"),
    ("first_person: []\n", "first_person"),
    ("[not, a, mapping]\n", "mapping"),
    ("narrator: {entity_class: PERSON\n", "not valid YAML"),
])
def test_invalid_files_name_the_problem(tmp_path, text, message):
    _write(tmp_path, "journal", text)
    with pytest.raises(config.PlanConfigError, match=message):
        config.load_config("journal")


def test_merge_takes_the_most_cautious_threshold():
    a = config.PlanConfig(confidence_threshold=0.6, narrator=config.Narrator("PERSON", "Diarist"))
    b = config.PlanConfig(confidence_threshold=0.9, classifier="lexical")
    merged = config.merge([a, b])
    assert merged.confidence_threshold == 0.9
    assert merged.narrator == config.Narrator("PERSON", "Diarist")
    assert merged.classifier == "auto"  # they disagree


def test_an_odd_domain_name_has_no_file():
    assert config.config_file("../etc/passwd") is None
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_config.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write `artmind/query_plan/config.py`**

```python
"""Per-domain plan config: <ARTMIND_HOME>/domains/plan/<domain>.yaml (spec 2026-10-22 §4.5).

User data like views and insights tuning: never seeded, outside schemas/ so it
never moves schema_set_hash(), read on every call. A parent domain's file
applies to every child domain that has no file of its own (most specific wins,
never merged).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FIRST_PERSON = ("i", "me", "my", "mine", "myself")
DEFAULT_THRESHOLD = 0.7
CLASSIFIERS = ("auto", "lexical", "decision")
_KEYS = frozenset({"narrator", "first_person", "confidence_threshold", "classifier", "decision_model"})
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(\.[A-Za-z0-9_][A-Za-z0-9_-]*)*$")


class PlanConfigError(ValueError):
    """A plan config file breaks a rule of spec §4.5."""


@dataclass(frozen=True)
class Narrator:
    entity_class: str
    name: str


@dataclass(frozen=True)
class PlanConfig:
    narrator: Narrator | None = None
    first_person: tuple[str, ...] = DEFAULT_FIRST_PERSON
    confidence_threshold: float = DEFAULT_THRESHOLD
    classifier: str = "auto"
    decision_model: str | None = None
    source: str | None = None


def config_dir() -> Path:
    import paths

    return Path(paths.DOMAIN_PLAN_DIR)


def config_file(domain: str) -> Path | None:
    """The most specific file for `domain`: its own, else its nearest parent's."""
    if not isinstance(domain, str) or not _DOMAIN_RE.match(domain):
        return None
    parts = domain.split(".")
    for i in range(len(parts), 0, -1):
        path = config_dir() / f"{'.'.join(parts[:i])}.yaml"
        if path.is_file():
            return path
    return None


def parse_config(data, source: str) -> PlanConfig:
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise PlanConfigError(f"{source}: must be a mapping")
    unknown = set(data) - _KEYS
    if unknown:
        raise PlanConfigError(f"{source}: unknown key(s) {sorted(unknown)}")
    narrator = None
    if data.get("narrator") is not None:
        raw = data["narrator"]
        if (not isinstance(raw, dict) or set(raw) != {"entity_class", "name"}
                or not all(isinstance(raw[k], str) and raw[k].strip() for k in raw)):
            raise PlanConfigError(f"{source}: narrator must be {{entity_class, name}} (two strings)")
        narrator = Narrator(raw["entity_class"].strip().upper(), raw["name"].strip())
    first_person = DEFAULT_FIRST_PERSON
    if "first_person" in data:
        raw = data["first_person"]
        if not isinstance(raw, list) or not raw or not all(isinstance(w, str) and w.strip() for w in raw):
            raise PlanConfigError(f"{source}: first_person must be a non-empty list of words")
        first_person = tuple(w.strip().casefold() for w in raw)
    threshold = data.get("confidence_threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 1:
        raise PlanConfigError(f"{source}: confidence_threshold must be a number in (0, 1]")
    classifier = data.get("classifier", "auto")
    if classifier not in CLASSIFIERS:
        raise PlanConfigError(f"{source}: classifier must be one of {list(CLASSIFIERS)}")
    model = data.get("decision_model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise PlanConfigError(f"{source}: decision_model must be a non-empty string")
    return PlanConfig(narrator, first_person, float(threshold), classifier, model.strip() if model else None, source)


def load_config(domain: str) -> PlanConfig:
    path = config_file(domain)
    if path is None:
        return PlanConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PlanConfigError(f"{path} is not valid YAML: {exc}") from exc
    return parse_config(data, str(path))


def merge(configs: list[PlanConfig]) -> PlanConfig:
    """Several selected domains: the most cautious threshold, the first narrator,
    first-person list and model found, and the classifier only if they agree."""
    if not configs:
        return PlanConfig()
    if len(configs) == 1:
        return configs[0]
    classifiers = {c.classifier for c in configs}
    return PlanConfig(
        narrator=next((c.narrator for c in configs if c.narrator), None),
        first_person=next((c.first_person for c in configs if c.first_person != DEFAULT_FIRST_PERSON),
                          DEFAULT_FIRST_PERSON),
        confidence_threshold=max(c.confidence_threshold for c in configs),
        classifier=classifiers.pop() if len(classifiers) == 1 else "auto",
        decision_model=next((c.decision_model for c in configs if c.decision_model), None),
    )
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_config.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add paths.py artmind/query_plan/config.py test/test_query_plan_config.py
git commit -m "feat(plan): per-domain plan config (narrator, threshold, classifier)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Classifier types and the lexical classifier

**Files:**
- Create: `artmind/query_plan/classify/__init__.py`, `artmind/query_plan/classify/base.py`, `artmind/query_plan/classify/lexical.py`
- Test: `test/test_query_plan_lexical.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_lexical.py`:

```python
"""Lexical scoring and the lexical classifier (spec 2026-10-22 §4.4)."""

import pytest

from artmind.query_plan import routes
from artmind.query_plan.classify import Decision, LexicalClassifier, select_classifier
from artmind.query_plan.classify import lexical


def _facts(question, **over):
    base = dict(question=question.casefold(), mentions=0, document=None, has_store=False, has_date=False)
    return lexical.Facts(**{**base, **over})


def test_a_single_trigger_wins_the_route_with_high_confidence():
    scores = lexical.route_scores(routes.load_routes(), _facts("How often did the team meet?"))
    assert scores["aggregate"] == 1.5 and scores["search"] == 0.0
    decision = Decision("route", "?", {r.id: r.description for r in routes.load_routes()}, scores,
                        default="search", default_confidence=0.5, none_mass=0.5)
    answer = lexical.answer(decision)
    assert answer.choice == "aggregate" and answer.confidence == 0.75


def test_nothing_matching_is_the_default_at_its_default_confidence():
    decision = Decision("route", "?", {"search": "s", "aggregate": "a"}, {"search": 0.0, "aggregate": 0.0},
                        default="search", default_confidence=0.5, none_mass=0.5)
    answer = lexical.answer(decision)
    assert (answer.choice, answer.confidence) == ("search", 0.5)


def test_slot_triggers_need_their_fact():
    regex, needs = lexical.compile_trigger("summarise {document}")
    assert needs == frozenset({"document"}) and regex.search("please summarise policy alpha")
    regex, needs = lexical.compile_trigger("as of {date}")
    assert regex.search("what applied as of 2026-01-15") and regex.search("as of march 2026")
    assert lexical.compile_trigger("{document}") == (None, frozenset({"document"}))


def test_a_route_needing_an_entity_gets_no_bonus_without_one():
    facts = _facts("tell me about the weather", mentions=0)
    assert lexical.route_scores(routes.load_routes(), facts)["entity_profile"] == 1.0
    facts = _facts("tell me about the weather", mentions=1)
    assert lexical.route_scores(routes.load_routes(), facts)["entity_profile"] == 1.5


def test_view_scores_match_examples_not_names():
    from artmind.kg_views.model import parse_view

    spec = parse_view({
        "name": "visits_by_year", "version": 1, "domains": ["journal"],
        "summary": "Visits a person made to a place in a year",
        "examples": ["How many visits did Ada make to Lakeport in 2025?"],
        "params": {"year": {"type": "int", "description": "Year"}},
        "cypher": "MATCH (n:Entity) WHERE n._domain IN $domains AND $year > 0 RETURN n.name AS who",
        "presentation": {"format": "table", "table": {"columns": ["who"]}},
        "provenance": {"created": "2026-10-09", "origin": "authored"},
    }, "visits_by_year")
    assert lexical.view_scores("How many visits did Bo make to Port Ellen in 2024?", [spec]) == {"visits_by_year": 5.0}
    assert lexical.view_scores("What did Bo eat?", [spec]) == {"visits_by_year": 0.0}


def test_the_classifier_is_deterministic():
    decision = Decision("view", "?", {"v": "x", "none": "no"}, {"v": 5.0}, default="none",
                        default_confidence=1.0, none_mass=1.0)
    first = LexicalClassifier().decide("q", [decision])
    assert first == LexicalClassifier().decide("q", [decision])
    assert first["view"].choice == "v" and first["view"].confidence == pytest.approx(0.8333, abs=1e-4)


def test_triggered_lists_the_matching_patterns():
    route = next(r for r in routes.load_routes() if r.id == "aggregate")
    assert lexical.triggered(route, _facts("how many times, on average?")) == ["how many", "average"]


def test_auto_without_an_endpoint_is_lexical(monkeypatch):
    monkeypatch.delenv("ARTMIND_DECISION_URL", raising=False)
    classifier, reason = select_classifier("auto", "auto", None)
    assert classifier.backend == "lexical" and reason == "no_endpoint"
    classifier, reason = select_classifier("lexical", "auto", None)
    assert classifier.backend == "lexical" and reason is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_lexical.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `artmind/query_plan/classify/base.py`**

```python
"""What every plan classifier takes and returns (spec 2026-10-22 §4.4).

A `Decision` carries both the option text a decision model reads and the
deterministic lexical scores, so the lexical backend can always answer the same
decisions, including as a fallback halfway through a plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Decision:
    name: str
    instructions: str
    options: dict[str, str]
    lexical_scores: dict[str, float] = field(default_factory=dict)
    default: str = "none"
    default_confidence: float = 0.5
    none_mass: float = 1.0
    type: str = "choice"


@dataclass(frozen=True)
class Answer:
    choice: str
    probabilities: dict[str, float]
    confidence: float


class ClassifierUnavailable(Exception):
    """The decision backend cannot answer this call; `reason` goes in the payload."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Classifier(Protocol):
    backend: str
    model: str | None

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]: ...
```

- [ ] **Step 4: Write `artmind/query_plan/classify/lexical.py`**

```python
"""Deterministic scoring for `query plan` (spec 2026-10-22 §4.4, "Lexical classifier").

The planner computes these scores for every decision, so this backend is always
available and is also the fallback when a decision endpoint fails mid-plan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from artmind.query_plan.classify.base import Answer, Decision

STOPWORDS = frozenset("""
a about above after again all am an and any are as at be been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers
him his how i if in into is it its just me more most my no nor not now of off on once only or other our
out over own same she should so some such than that the their them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would
you your yours
""".split())
VIEW_SCALE = 5.0
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_DATE_PATTERN = (
    r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[a-z]{3,9}\s+\d{4}|[a-z]{3,9}\s+\d{4}|(?:19|20)\d{2}"
    r"|today|yesterday|last\s+(?:year|month|week))"
)
_DATE_RE = re.compile(r"\b" + _DATE_PATTERN + r"\b")


@dataclass(frozen=True)
class Facts:
    """What the question shows before any domain decision, for route scoring."""
    question: str          # casefolded
    mentions: int
    document: str | None
    has_store: bool
    has_date: bool


def has_date(question: str) -> bool:
    return _DATE_RE.search(question.casefold()) is not None


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") and not word.endswith("ss") else word


def content_tokens(text: str, *, drop_capitalised: bool = True) -> set[str]:
    """Lower-case content words. Capitalised words after the first are names,
    which differ between a view's examples and a real question, so they are dropped."""
    out: set[str] = set()
    for match in _WORD_RE.finditer(text):
        word = match.group(0)
        if drop_capitalised and match.start() > 0 and word[:1].isupper():
            continue
        low = word.casefold().strip("'’-")
        if len(low) >= 3 and low not in STOPWORDS:
            out.add(_stem(low))
    return out


@lru_cache(maxsize=512)
def compile_trigger(trigger: str) -> tuple[re.Pattern | None, frozenset[str]]:
    """A trigger's regex (None when it is only a fact slot) and the facts it needs."""
    text = trigger.casefold().strip()
    needs = frozenset(slot for slot in ("entity", "document") if "{" + slot + "}" in text)
    for slot in needs:
        text = text.replace("{" + slot + "}", " ")
    pieces = []
    for token in re.split(r"(\{date\}|\{year\})", text):
        if token == "{date}":
            pieces.append(_DATE_PATTERN)
        elif token == "{year}":
            pieces.append(r"(?:19|20)\d{2}")
        elif token.split():
            pieces.append(r"\s+".join(re.escape(w) for w in token.split()))
    if not pieces:
        return None, needs
    return re.compile(r"\b" + r"\s+".join(pieces) + r"\b"), needs


def _trigger_hits(route, facts: Facts) -> list[str]:
    hits = []
    for trigger in route.triggers:
        regex, needs = compile_trigger(trigger)
        if "entity" in needs and facts.mentions == 0:
            continue
        if "document" in needs and not facts.document:
            continue
        if regex is None or regex.search(facts.question):
            hits.append(trigger)
    return hits


def _needs_met(needs: tuple[str, ...], facts: Facts) -> bool:
    checks = {
        "entity": facts.mentions >= 1,
        "two_entities": facts.mentions >= 2,
        "document": bool(facts.document),
        "store": facts.has_store,
        "date": facts.has_date,
    }
    return all(checks.get(need, True) for need in needs)


def route_scores(routes, facts: Facts) -> dict[str, float]:
    """One point per matched trigger, plus 0.5 when the route's needs are met."""
    scores = {}
    for route in routes:
        hits = len(_trigger_hits(route, facts))
        scores[route.id] = float(hits) + (0.5 if hits and _needs_met(route.needs, facts) else 0.0)
    return scores


def triggered(route, facts: Facts) -> list[str]:
    return _trigger_hits(route, facts)


def _overlap(question: set[str], text: set[str]) -> float:
    return len(question & text) / len(text) if text else 0.0


def view_scores(question: str, specs) -> dict[str, float]:
    """VIEW_SCALE x the best share of a view example's (or summary's) content words in the question."""
    q = content_tokens(question)
    return {
        spec.name: VIEW_SCALE * max(_overlap(q, content_tokens(t)) for t in (spec.summary, *spec.examples))
        for spec in specs
    }


def domain_scores(question: str, options) -> dict[str, float]:
    """Words shared with a domain option's name, document names and classes."""
    q = content_tokens(question, drop_capitalised=False)
    scores = {}
    for option in options:
        words = set(re.split(r"[._\s-]+", option.key.casefold()))
        for text in (*option.documents, *option.top_classes):
            words |= content_tokens(text.replace("_", " ").replace("-", " "), drop_capitalised=False)
        scores[option.key] = float(len(q & {_stem(w) for w in words if len(w) >= 3}))
    return scores


def value_scores(question: str, values) -> dict[str, float]:
    low = question.casefold()
    return {v: (1.0 if v.casefold() in low else 0.0) for v in values}


def answer(decision: Decision) -> Answer:
    """Scores → probabilities, with `none_mass` added to the default option."""
    keys = list(decision.options)
    scores = {k: max(0.0, float(decision.lexical_scores.get(k, 0.0))) for k in keys}
    total = sum(scores.values())
    if total == 0:
        return Answer(decision.default, {k: (1.0 if k == decision.default else 0.0) for k in keys},
                      decision.default_confidence)
    denominator = total + decision.none_mass
    probabilities = {k: s / denominator for k, s in scores.items()}
    probabilities[decision.default] = probabilities.get(decision.default, 0.0) + decision.none_mass / denominator
    choice = max(keys, key=lambda k: (probabilities[k], -keys.index(k)))
    return Answer(choice, {k: round(p, 4) for k, p in probabilities.items()}, round(probabilities[choice], 4))


class LexicalClassifier:
    backend = "lexical"
    model = None

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        return {d.name: answer(d) for d in decisions}
```

- [ ] **Step 5: Write `artmind/query_plan/classify/__init__.py`**

```python
"""Plan classifiers: lexical rules always; a decision model when configured (spec 2026-10-22 §4.4)."""

from __future__ import annotations

from artmind.query_plan.classify.base import Answer, Classifier, ClassifierUnavailable, Decision
from artmind.query_plan.classify.lexical import LexicalClassifier

__all__ = ["Answer", "Classifier", "ClassifierUnavailable", "Decision", "LexicalClassifier", "select_classifier"]


def select_classifier(option: str, domain_choice: str, model_override: str | None) -> tuple[Classifier, str | None]:
    """(classifier, reason it is lexical) for `--classifier` and the domain config.
    The decision backend arrives in Task 16; until then only lexical exists."""
    if option == "decision":
        raise ValueError("--classifier decision needs ARTMIND_DECISION_URL and ARTMIND_DECISION_MODEL")
    if option == "lexical":
        return LexicalClassifier(), None
    return LexicalClassifier(), ("domain_config" if domain_choice == "lexical" else "no_endpoint")
```

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_lexical.py -v`
Expected: all PASS. (In `test_view_scores_match_examples_not_names` both questions drop their capitalised names; "visits/visit", "many", "make" overlap fully.)

- [ ] **Step 7: Commit**

```bash
git add artmind/query_plan/classify test/test_query_plan_lexical.py
git commit -m "feat(plan): decision types and the deterministic lexical classifier

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Candidates — mentions, domain options, resolution, twins, documents, store

**Files:**
- Create: `artmind/query_plan/candidates.py`
- Test: `test/test_query_plan_candidates.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_candidates.py`:

```python
"""Deterministic candidates for `query plan` (spec 2026-10-22 §3.1, §4.2 entities)."""

from artmind import graph_query, vector_query
from artmind.query_plan import candidates
from artmind.query_plan.config import Narrator


def _texts(question):
    return [(m.text, m.via) for m in candidates.find_mentions(question)]


def test_mentions_are_names_quotes_and_first_person():
    assert _texts("How many visits did I make to Port Ellen in May 2025?") == [
        ("I", "first_person"), ("Port Ellen", "capitalised"),
    ]
    assert _texts('Did Ada read "The Long Harbour" again?') == [
        ("Ada", "capitalised"), ("The Long Harbour", "quoted"),
    ]
    assert _texts("Bo's notes mention Lakeport.") == [("Bo", "capitalised"), ("Lakeport", "capitalised")]


def test_keywords_skip_mentions_and_stopwords():
    question = "recorded deaths in my family since Ada left"
    assert candidates.keywords(question, candidates.find_mentions(question)) == [
        "recorded", "deaths", "family", "left",  # "since" is a question word
    ]


def test_domain_options_add_parents_with_two_children():
    rows = [
        {"domain": "bank.policy", "document_count": 3, "documents": ["policy_alpha.md"], "top_classes": ["POLICY"]},
        {"domain": "bank.cases", "document_count": 2, "documents": ["case_one.md"], "top_classes": ["CASE"]},
        {"domain": "journal", "document_count": 9, "documents": ["2025-05-01.md"], "top_classes": ["PERSON"]},
    ]
    options = candidates.domain_options(rows)
    assert [(o.key, o.is_parent) for o in options] == [
        ("bank", True), ("bank.cases", False), ("bank.policy", False), ("journal", False),
    ]
    assert options[0].document_count == 5
    assert "All bank.* domains" in options[0].text()


def test_resolution_keeps_only_name_evidence(monkeypatch):
    def fake_resolve(domains, reference, top_k):
        assert (domains, reference, top_k) == (["journal"], "Ada", candidates.RESOLVE_TOP_K)
        return {"rows": [
            {"entity": {"_id": "p1", "name": "Ada", "entity_class": "PERSON", "_domain": "journal"},
             "matched_by": ["exact", "vector"]},
            {"entity": {"_id": "p2", "name": "Adam", "entity_class": "PERSON"}, "matched_by": ["vector"]},
        ]}

    monkeypatch.setattr(vector_query, "entity_resolve", fake_resolve)
    mention = candidates.Mention("Ada", 0, 3, "capitalised")
    found = candidates.resolve_mention(mention, ["journal"], None)
    assert [c["_id"] for c in found] == ["p1"]
    assert found[0]["matched_by"] == ["exact", "vector"]


def test_first_person_resolves_to_the_narrator(monkeypatch):
    sent = {}

    def fake_run(cypher, params):
        sent["cypher"], sent["params"] = cypher, params
        return [{"entity": {"_id": "n1", "name": "Diarist", "entity_class": "PERSON"}}]

    monkeypatch.setattr(graph_query, "_run_read_query", fake_run)
    mention = candidates.Mention("I", 0, 1, "first_person")
    found = candidates.resolve_mention(mention, ["journal"], Narrator("PERSON", "Diarist"))
    assert found[0]["_id"] == "n1" and found[0]["matched_by"] == ["narrator"]
    assert sent["params"]["cls"] == "PERSON" and sent["params"]["name"] == "Diarist"
    assert candidates.resolve_mention(mention, ["journal"], None) == []


def test_twins_group_by_class_and_casefolded_name():
    refs = [{"_id": "a", "name": "Lakeport", "entity_class": "LOCATION"},
            {"_id": "b", "name": "lakeport", "entity_class": "LOCATION"},
            {"_id": "c", "name": "Lakeport", "entity_class": "ORGANIZATION"}]
    assert [[r["_id"] for r in g] for g in candidates.twin_groups(refs)] == [["a", "b"], ["c"]]


def test_a_document_named_in_the_question_is_found():
    names = ["policy_alpha.md", "policy_alpha_v2.md", "notes.md"]
    assert candidates.match_document("What did policy alpha v2 say in January?", names) == "policy_alpha_v2.md"
    assert candidates.match_document("Summarise policy_alpha.md.", names) == "policy_alpha.md"
    assert candidates.match_document("What did I do?", names) is None


def test_store_facts_pick_a_kind(monkeypatch):
    from artmind.structured import registry

    monkeypatch.setattr(registry, "routing_surface", lambda domains: [
        {"table": "accounts", "entity_classes": [{"entity_class": "CUSTOMER"}]},
    ])
    hybrid = candidates.store_facts(["bank"], "What is the balance of 3 accounts?", {"CUSTOMER"})
    assert hybrid["kind"] == "hybrid" and hybrid["tables"] == ["accounts"]
    assert candidates.store_facts(["bank"], "average balance", set())["kind"] == "analytical"
    monkeypatch.setattr(registry, "routing_surface", lambda domains: [])
    assert candidates.store_facts(["bank"], "anything", set()) is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_candidates.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `artmind/query_plan/candidates.py`**

```python
"""Deterministic candidates for `query plan` (spec 2026-10-22 §3.1, §4.2).

What the question mentions and what the graph and catalogue hold. No classifier
here: everything is code, so the same question and graph give the same candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from artmind import graph_query, vector_query
from artmind.graph_query import domain_predicate
from artmind.query_plan.classify.lexical import STOPWORDS

MAX_MENTIONS = 8
MAX_CANDIDATES = 5
RESOLVE_TOP_K = 10
DOCUMENT_SUFFIXES = (".md", ".pdf", ".docx", ".txt", ".html")
QUESTION_WORDS = frozenset("""
how what when where which who whom whose why did does do is are was were can could should would will
has have had list show tell give find summarise summarize describe explain any all in on for from of the
a an between since during please
""".split())
MONTHS = frozenset("""
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
""".split())
AGGREGATE_WORDS = ("how many", "average", "total", "sum of", "count of", "per ", "number of")
GUIDANCE_WORDS = ("policy", "guidance", "should", "what to do", "rule", "procedure")

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w'’&-]*")
_QUOTED_RE = re.compile(r'"([^"]{2,})"|“([^”]{2,})”')
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


@dataclass(frozen=True)
class Mention:
    text: str
    start: int
    end: int
    via: str  # capitalised | quoted | first_person


def find_mentions(question: str, first_person: tuple[str, ...] = ("i", "me", "my", "mine", "myself")) -> list[Mention]:
    """Quoted spans, runs of capitalised words, and one first-person word."""
    mentions: list[Mention] = []
    taken: list[tuple[int, int]] = []
    for match in _QUOTED_RE.finditer(question):
        text = (match.group(1) or match.group(2)).strip()
        start = match.start() + 1
        mentions.append(Mention(text, start, start + len(text), "quoted"))
        taken.append((match.start(), match.end()))
    first = {w.casefold() for w in first_person}
    seen_first_person = False
    run: list[re.Match] = []

    def flush() -> None:
        if run:
            text = re.sub(r"['’]s$", "", question[run[0].start():run[-1].end()])
            mentions.append(Mention(text, run[0].start(), run[0].start() + len(text), "capitalised"))
            run.clear()

    for index, match in enumerate(_TOKEN_RE.finditer(question)):
        if any(a <= match.start() < b for a, b in taken):
            flush()
            continue
        word = match.group(0)
        low = re.sub(r"['’]s$", "", word).casefold()
        if low in first:
            flush()
            if not seen_first_person:
                mentions.append(Mention(word, match.start(), match.end(), "first_person"))
                seen_first_person = True
            continue
        sentence_start = index == 0 or re.search(r"[.?!]\s*$", question[:match.start()]) is not None
        is_name = (
            word[:1].isupper()
            and not (sentence_start and (low in QUESTION_WORDS or low in STOPWORDS))
            and low not in MONTHS
        )
        if is_name:
            if run and question[run[-1].end():match.start()].strip():
                flush()
            run.append(match)
        else:
            flush()
    flush()
    mentions.sort(key=lambda m: m.start)
    return mentions[:MAX_MENTIONS]


def keywords(question: str, mentions: list[Mention]) -> list[str]:
    """Content words outside the mentions, in question order (the `{keywords}` placeholder)."""
    spans = [(m.start, m.end) for m in mentions]
    out: list[str] = []
    for match in _WORD_RE.finditer(question):
        if any(a <= match.start() < b for a, b in spans):
            continue
        word = match.group(0).casefold().strip("'’-")
        if len(word) >= 3 and word not in STOPWORDS and word not in QUESTION_WORDS and word not in out:
            out.append(word)
    return out


@dataclass(frozen=True)
class DomainOption:
    key: str
    is_parent: bool
    document_count: int
    documents: tuple[str, ...]
    top_classes: tuple[str, ...]
    tables: tuple[str, ...]

    def text(self) -> str:
        """The option text a decision model reads (spec §4.4 call 1)."""
        head = f"All {self.key}.* domains" if self.is_parent else self.key
        parts = [f"{head}: {self.document_count} documents"]
        if self.documents:
            parts.append("e.g. " + ", ".join(self.documents[:5]))
        if self.top_classes:
            parts.append("classes " + ", ".join(self.top_classes))
        if self.tables:
            parts.append("tables " + ", ".join(self.tables))
        return "; ".join(parts)


def _option(key: str, rows: list[dict], is_parent: bool) -> DomainOption:
    documents: list[str] = []
    classes: list[str] = []
    tables: list[str] = []
    for row in rows:
        documents += [d for d in row.get("documents") or [] if d not in documents]
        classes += [c for c in row.get("top_classes") or [] if c not in classes]
        tables += [t for t in row.get("tables") or [] if t not in tables]
    return DomainOption(key, is_parent, sum(r.get("document_count") or 0 for r in rows),
                        tuple(documents[:10]), tuple(classes[:8]), tuple(tables))


def domain_options(rows: list[dict]) -> list[DomainOption]:
    """Every leaf domain from `domains-overview`, plus each family parent with two
    or more children (a parent scopes all of them; study P14)."""
    by_name = {r["domain"]: r for r in rows if isinstance(r.get("domain"), str)}
    children: dict[str, set[str]] = {}
    for name in by_name:
        parts = name.split(".")
        for i in range(1, len(parts)):
            children.setdefault(".".join(parts[:i]), set()).add(name)
    options = []
    for parent, kids in children.items():
        if len(kids) >= 2:
            members = [by_name[k] for k in sorted(kids)] + ([by_name[parent]] if parent in by_name else [])
            options.append(_option(parent, members, True))
    parents = {o.key for o in options}
    options += [_option(name, [row], False) for name, row in by_name.items() if name not in parents]
    return sorted(options, key=lambda o: o.key)


def entity_ref(entity: dict, matched_by) -> dict:
    return {
        "_id": entity.get("_id"),
        "name": entity.get("name"),
        "entity_class": entity.get("entity_class"),
        "domain": entity.get("_domain"),
        "observation_count": entity.get("observation_count"),
        "connection_count": entity.get("connection_count"),
        "matched_by": sorted(matched_by or []),
        "description": (entity.get("description") or "")[:120],
    }


def narrator_candidates(narrator, domains: list[str]) -> list[dict]:
    rows = graph_query._run_read_query(
        f"""
        MATCH (e:Entity)
        WHERE {domain_predicate("e")} AND e.entity_class = $cls AND toLower(e.name) = toLower($name)
        RETURN e {{ ._id, .name, .entity_class, .description, ._domain,
                    observation_count: e._observation_count,
                    connection_count: COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} }} AS entity
        ORDER BY e._observation_count DESC
        LIMIT $limit
        """,
        {"domains": domains, "cls": narrator.entity_class, "name": narrator.name, "limit": MAX_CANDIDATES},
    )
    return [entity_ref(r["entity"], {"narrator"}) for r in rows if r.get("entity")]


def resolve_mention(mention: Mention, domains: list[str], narrator) -> list[dict]:
    """Candidates for one mention. Only exact or fulltext evidence counts: the
    vector leg always returns neighbours, so it is not evidence (the kg_views rule)."""
    if mention.via == "first_person":
        return narrator_candidates(narrator, domains) if narrator else []
    result = vector_query.entity_resolve(domains, mention.text, RESOLVE_TOP_K)
    out = []
    for row in result.get("rows", []):
        entity, legs = row.get("entity"), set(row.get("matched_by") or [])
        if entity and legs & {"exact", "fulltext"}:
            out.append(entity_ref(entity, legs))
    return out[:MAX_CANDIDATES]


def twin_groups(refs: list[dict]) -> list[list[dict]]:
    """Candidates sharing a class and a casefolded name, in rank order (study P19)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for ref in refs:
        key = (str(ref.get("entity_class") or "").casefold(), str(ref.get("name") or "").casefold())
        groups.setdefault(key, []).append(ref)
    return list(groups.values())


def document_names(domains: list[str]) -> list[str]:
    rows = graph_query._run_read_query(
        f"""
        MATCH (d)
        WHERE (d:Document OR d:DocumentHistory) AND {domain_predicate("d")}
        RETURN DISTINCT d.name AS name
        """,
        {"domains": domains},
    )
    return sorted({r["name"] for r in rows if r.get("name")})


def _norm(text: str) -> str:
    text = re.sub(r"[^0-9a-z.]+", " ", text.casefold())
    return " ".join(re.sub(r"\.(?![0-9a-z])", " ", text).split())


def match_document(question: str, names: list[str]) -> str | None:
    """The longest document name (with or without its suffix) the question spells out."""
    q = f" {_norm(question)} "
    best: tuple[str, int] | None = None
    for name in names:
        stem = name
        for suffix in DOCUMENT_SUFFIXES:
            if stem.casefold().endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        for form in {_norm(name), _norm(stem)}:
            if len(form) >= 4 and f" {form} " in q and (best is None or len(form) > best[1]):
                best = (name, len(form))
    return best[0] if best else None


def store_facts(domains: list[str], question: str, resolved_classes: set) -> dict | None:
    """Which of structured-store.md's four cases applies (S7-S9), or None without tables."""
    try:
        from artmind.structured import registry

        surface = registry.routing_surface(domains)
    except Exception:  # a query-only host has no registry DB
        return None
    if not surface:
        return None
    low = question.casefold()
    bridged = sorted({c["entity_class"] for t in surface for c in t.get("entity_classes") or []})
    hit = set(bridged) & {c for c in resolved_classes if c}
    aggregate = any(w in low for w in AGGREGATE_WORDS)
    if hit and (aggregate or re.search(r"\d", low)):
        kind = "hybrid"
    elif aggregate:
        kind = "analytical"
    elif hit and any(w in low for w in GUIDANCE_WORDS):
        kind = "records_plus_guidance"
    else:
        kind = "graph"
    return {"kind": kind, "tables": sorted({t["table"] for t in surface}), "bridge_classes": bridged,
            "matched_values": []}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_candidates.py -v`
Expected: all PASS. If `test_mentions_are_names_quotes_and_first_person` fails on "May", check that `MONTHS` excludes it; if it fails on "Bo's", check the possessive strip in `flush`.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/candidates.py test/test_query_plan_candidates.py
git commit -m "feat(plan): deterministic candidates (mentions, domains, entities, twins, documents, store)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Scalar view params

**Files:**
- Create: `artmind/query_plan/fill.py`
- Test: `test/test_query_plan_fill.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_fill.py`:

```python
"""Scalar view params from the question (spec 2026-10-22 §4.2 view_match)."""

from datetime import date

from artmind.kg_views.model import ParamSpec
from artmind.query_plan import fill

TODAY = date(2026, 10, 22)


def _fill(spec, question):
    return fill.fill_scalar(spec, question, TODAY, set())


def test_an_int_in_range_is_taken_and_out_of_range_numbers_are_not():
    year = ParamSpec("year", "int", min=1900, max=2100)
    days = ParamSpec("merge_within_days", "int", default=10, min=0, max=60)
    used: set = set()
    assert fill.fill_scalar(year, "trips in 2025", TODAY, used)["value"] == 2025
    assert fill.fill_scalar(days, "trips in 2025", TODAY, used) == {"type": "int", "via": "default", "value": 10}


def test_relative_years():
    year = ParamSpec("year", "int", min=1900, max=2100)
    assert _fill(year, "what happened last year")["value"] == 2025
    assert _fill(year, "anything this year?")["value"] == 2026


def test_dates_in_three_spellings():
    when = ParamSpec("on", "date")
    assert _fill(when, "on 2026-01-15")["value"] == "2026-01-15"
    assert _fill(when, "on 15 January 2026")["value"] == "2026-01-15"
    assert _fill(when, "in March 2026")["value"] == "2026-03-01"


def test_a_string_is_only_ever_a_quoted_span():
    text = ParamSpec("phrase", "string")
    assert _fill(text, 'notes saying "low tide"')["value"] == "low tide"
    assert _fill(text, "notes saying low tide") is None


def test_bool_and_list_use_their_default_or_are_missing():
    assert _fill(ParamSpec("flag", "bool", default=False), "anything") == {"type": "bool", "via": "default", "value": False}
    assert _fill(ParamSpec("flag", "bool"), "anything") is None
    assert _fill(ParamSpec("names", "list", items="string"), "anything") is None


def test_combinations_are_capped():
    groups = {"a": [{"_id": 1}, {"_id": 2}], "b": [{"_id": 3}, {"_id": 4}, {"_id": 5}]}
    assert len(fill.combinations(groups, cap=6)) == 6
    assert fill.combinations(groups, cap=4) is None
    assert fill.combinations({}, cap=4) == [{}]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_fill.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write `artmind/query_plan/fill.py`**

```python
"""Fill a matched view's scalar params from the question (spec 2026-10-22 §4.2).

Numbers and dates by pattern, strings only from a quoted span, bool and list
from their defaults. Nothing is invented: a required param with no value is
`missing_param` for the planner.
"""

from __future__ import annotations

import itertools
import re
from datetime import date

from artmind.kg_views.model import ParamSpec, jsonable

MONTHS = {
    name: i + 1
    for i, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
        ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
        ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ])
    for name in names
}
_NUMBER_RE = re.compile(r"(?<![\w.-])(\d+(?:\.\d+)?)(?![\w.-])")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DMY_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b")
_MY_RE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{4})\b")
_QUOTED_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def _free(span: tuple[int, int], used: set) -> bool:
    return not any(a < span[1] and span[0] < b for a, b in used)


def _in_range(spec: ParamSpec, value: float) -> bool:
    return (spec.min is None or value >= spec.min) and (spec.max is None or value <= spec.max)


def _number(spec: ParamSpec, question: str, today: date, used: set):
    low = question.casefold()
    for phrase, year in (("last year", today.year - 1), ("this year", today.year)):
        index = low.find(phrase)
        span = (index, index + len(phrase))
        if index >= 0 and _free(span, used) and _in_range(spec, year):
            return year, phrase, span
    for match in _NUMBER_RE.finditer(question):
        span = match.span(1)
        text = match.group(1)
        value = float(text) if spec.type == "float" or "." in text else int(text)
        if spec.type == "int" and isinstance(value, float):
            continue
        if _free(span, used) and _in_range(spec, value):
            return value, text, span
    return None


def _date(question: str, today: date, used: set):
    low = question.casefold()
    if "today" in low:
        index = low.index("today")
        return today.isoformat(), "today", (index, index + 5)
    for regex, build in (
        (_ISO_RE, lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
        (_DMY_RE, lambda m: date(int(m[3]), MONTHS[m[2].casefold()], int(m[1]))),
        (_MY_RE, lambda m: date(int(m[2]), MONTHS[m[1].casefold()], 1)),
    ):
        for match in regex.finditer(question):
            try:
                value = build(match)
            except (KeyError, ValueError):
                continue
            if _free(match.span(), used):
                return value.isoformat(), match.group(0), match.span()
    return None


def _quoted(question: str, used: set):
    for match in _QUOTED_RE.finditer(question):
        if _free(match.span(), used):
            return (match.group(1) or match.group(2)), match.group(0), match.span()
    return None


def fill_scalar(spec: ParamSpec, question: str, today: date, used: set) -> dict | None:
    """`{type, via, input, value}`, the default, or None for a required param with no value.
    `used` holds character spans already taken by other params."""
    found = None
    if spec.type in ("int", "float"):
        found = _number(spec, question, today, used)
    elif spec.type == "date":
        found = _date(question, today, used)
    elif spec.type == "string":
        found = _quoted(question, used)
    if found is not None:
        value, text, span = found
        used.add(span)
        return {"type": spec.type, "via": "quoted" if spec.type == "string" else "regex", "input": text,
                "value": value}
    if not spec.required:
        return {"type": spec.type, "via": "default", "value": jsonable(spec.default)}
    return None


def combinations(groups: dict[str, list[dict]], cap: int) -> list[dict] | None:
    """Every pick of one entity per param; None when there are more than `cap`."""
    names = list(groups)
    count = 1
    for name in names:
        count *= len(groups[name])
    if count > cap:
        return None
    return [dict(zip(names, picks)) for picks in itertools.product(*(groups[n] for n in names))]
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_fill.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/fill.py test/test_query_plan_fill.py
git commit -m "feat(plan): fill scalar view params from the question

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: The planner

**Files:**
- Create: `artmind/query_plan/planner.py`
- Test: `test/test_query_plan_planner.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_planner.py`:

```python
"""The planner: stages → payload (spec 2026-10-22 §3.2, §4.2). Synthetic data only."""

from datetime import date
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import parse_view
from artmind.kg_views.store import LoadedView
from artmind.query_plan import candidates, planner

VIEW = {
    "name": "visits_by_year", "version": 1, "domains": ["journal"],
    "summary": "Visits a person made to a place in a year, one row per visit",
    "examples": ["How many visits did Ada make to Lakeport in 2025?", "When did Ada travel to Lakeport in 2024?"],
    "params": {
        "person": {"type": "entity", "entity_class": "PERSON", "description": "The traveller"},
        "place": {"type": "entity", "entity_class": "LOCATION", "description": "The destination"},
        "year": {"type": "int", "min": 1900, "max": 2100, "description": "Calendar year"},
    },
    "cypher": ("MATCH (p:Entity {_id: $person}), (l:Entity {_id: $place}) "
               "WHERE p._domain IN $domains AND l._domain IN $domains AND $year > 0 RETURN p.name AS who"),
    "presentation": {"format": "table", "table": {"columns": ["who"]}},
    "provenance": {"created": "2026-10-09", "origin": "authored"},
}
PAYLOAD_KEYS = {"plan_version", "question", "domains", "route", "view_match", "entities", "store", "confidence",
                "steps", "suggested_steps", "fallbacks", "read", "classifier", "warnings", "executed",
                "rows", "rows_total", "truncated"}
ENTITIES = {
    "Ada": [{"_id": "p1", "name": "Ada", "entity_class": "PERSON", "matched_by": ["exact"]}],
    "Lakeport": [{"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]}],
}


@pytest.fixture
def world(monkeypatch, tmp_path):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    state = {"views": [LoadedView(parse_view(VIEW, VIEW["name"]), "", Path("."))], "entities": dict(ENTITIES),
             "overview": [{"domain": "journal", "document_count": 3, "documents": ["2025-05-01.md"],
                           "top_classes": ["PERSON", "LOCATION"]}]}
    monkeypatch.setattr(graph_query, "domains_overview", lambda: {"rows": state["overview"]})
    monkeypatch.setattr(candidates, "document_names", lambda domains: [])
    monkeypatch.setattr(candidates, "store_facts", lambda domains, question, classes: None)
    monkeypatch.setattr(views_store, "list_views", lambda domains=None: (state["views"], []))

    def fake_resolve(mention, domains, narrator):
        if mention.via == "first_person":
            return ([{"_id": "n1", "name": narrator.name, "entity_class": "PERSON", "matched_by": ["narrator"]}]
                    if narrator else [])
        return list(state["entities"].get(mention.text, []))

    monkeypatch.setattr(candidates, "resolve_mention", fake_resolve)
    return state


def _plan(question, domains=("journal",), **kw):
    return planner.plan(question, list(domains) if domains else None, today=date(2026, 10, 22), **kw)


def test_a_view_question_gets_one_ready_view_run(world):
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert set(result) == PAYLOAD_KEYS
    assert result["route"]["id"] == "view"
    assert result["confidence"]["level"] == "high"
    assert result["view_match"]["status"] == "ok"
    assert result["view_match"]["params"]["year"]["value"] == 2025
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query views run visits_by_year --domain journal --param person=@p1 --param place=@l1 "
        "--param year=2025 --compact"
    ]
    assert result["suggested_steps"] == []


def test_the_same_question_gives_the_same_plan(world):
    first = _plan("How many visits did Ada make to Lakeport in 2025?")
    second = _plan("How many visits did Ada make to Lakeport in 2025?")
    first["classifier"].pop("latency_ms")
    second["classifier"].pop("latency_ms")
    assert first == second


def test_twins_get_one_run_each(world):
    world["entities"]["Lakeport"] = [
        {"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
        {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
    ]
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["view_match"]["params"]["place"]["twins"] is True
    assert result["view_match"]["params"]["place"]["chosen"] is None
    assert [s["cmd"].split("--param place=")[1].split()[0] for s in result["steps"]] == ["@l1", "@l2"]
    assert any("share the name 'Lakeport'" in w for w in result["warnings"])


def test_first_person_maps_to_the_configured_narrator(world, tmp_path):
    (tmp_path / "journal.yaml").write_text("narrator: {entity_class: PERSON, name: Diarist}\n")
    result = _plan("How many visits did I make to Lakeport in 2025?")
    person = result["view_match"]["params"]["person"]
    assert person["via"] == "narrator" and person["chosen"]["name"] == "Diarist"
    assert "--param person=@n1" in result["steps"][0]["cmd"]


def test_a_route_question_fills_the_route_templates(world):
    world["views"] = []
    result = _plan("How often did the team meet?")
    assert result["route"]["id"] == "aggregate"
    assert result["route"]["triggered_by"] == ["how often"]
    assert result["view_match"] is None
    assert [s["cmd"] for s in result["steps"]] == [
        "artmind query graph text2cypher --domain journal 'How often did the team meet?' --compact"
    ]


def test_nothing_matching_is_low_confidence_with_suggestions(world):
    result = _plan("Something about the weather?")
    assert result["route"]["id"] == "search"
    assert result["confidence"]["level"] == "low"
    assert result["confidence"]["weakest"] == "route"
    assert result["steps"] == []
    assert result["suggested_steps"][0]["cmd"].startswith("artmind query vector-text --domain journal")
    assert result["read"][-1] == "SKILL.md"


def test_an_unresolved_entity_makes_the_view_no_match(world):
    world["entities"].pop("Lakeport")
    result = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert result["view_match"]["status"] == "no_match"
    assert result["confidence"]["level"] == "low"


def test_the_domain_is_chosen_when_not_given(world):
    world["overview"].append({"domain": "bank.policy", "document_count": 4, "documents": ["policy_alpha.md"],
                              "top_classes": ["POLICY"]})
    result = _plan("How often is the journal mentioned?", domains=None)
    assert result["domains"]["source"] == "matched"
    assert result["domains"]["selected"] == ["journal"]


def test_execute_runs_a_single_high_confidence_view(world, monkeypatch):
    seen = {}

    def fake_run_view(spec, domains, raw):
        seen.update(name=spec.name, domains=domains, raw=raw)
        return {"view": spec.name, "status": "ok", "params": {}, "rows": [{"who": "Ada"}], "rows_total": 1,
                "truncated": False}

    monkeypatch.setattr(views_runner, "run_view", fake_run_view)
    result = _plan("How many visits did Ada make to Lakeport in 2025?", execute=True)
    assert seen == {"name": "visits_by_year", "domains": ["journal"],
                    "raw": {"person": "@p1", "place": "@l1", "year": "2025"}}
    assert result["rows"] == [{"who": "Ada"}] and result["rows_total"] == 1
    assert result["executed"]["status"] == "ok" and "rows" not in result["executed"]["result"]


def test_execute_is_ignored_on_a_low_confidence_plan(world):
    result = _plan("Something about the weather?", execute=True)
    assert result["executed"] is None
    assert any(w.startswith("--execute ignored") for w in result["warnings"])


def test_an_empty_question_is_refused(world):
    with pytest.raises(planner.PlanError):
        _plan("   ")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_planner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.query_plan.planner'`.

- [ ] **Step 3: Write `artmind/query_plan/planner.py`**

```python
"""`artmind query plan`: one deterministic plan for a question (spec
docs/superpowers/specs/2026-10-22-query-plan-design.md §3.2, §4.2).

Stages: candidates (code) → classifier call 1 (domain?, view?, route) →
classifier call 2 (the chosen view's entity/enum params) → fill → assemble.
Read-only. `execute=True` only ever runs `views run`, and only for a
high-confidence view match with one parameter combination.
"""

from __future__ import annotations

import shlex
import time
from datetime import date

from artmind import graph_query
from artmind.kg_views import runner as views_runner
from artmind.kg_views import store as views_store
from artmind.kg_views.model import ViewError, jsonable
from artmind.kg_views.store import domain_covered
from artmind.query_plan import candidates, fill
from artmind.query_plan import config as plan_config
from artmind.query_plan import routes as routes_mod
from artmind.query_plan.classify import ClassifierUnavailable, Decision, LexicalClassifier, select_classifier
from artmind.query_plan.classify import lexical

PLAN_VERSION = 1
MAX_COMBINATIONS = 4
ALL_DOMAINS = "__all__"
NO_VIEW = "none"
ROUTING_TABLE = "SKILL.md"
VIEW_FALLBACK = "artmind query vector-text --domain {domains} {question} --compact"


class PlanError(ValueError):
    """The question cannot be planned: empty, or a forced classifier that is not configured."""


class _Calls:
    """Classifier calls with lexical fallback, counted and timed for the payload."""

    def __init__(self, classifier, warnings: list[str]):
        self.classifier = classifier
        self.warnings = warnings
        self.calls = 0
        self.latency_ms = 0.0
        self.fell_back = False
        self.reason: str | None = None

    def decide(self, question: str, decisions: list[Decision]) -> dict:
        if not decisions:
            return {}
        started = time.perf_counter()
        try:
            return self.classifier.decide(question, decisions)
        except ClassifierUnavailable as exc:
            self.fell_back = True
            self.reason = exc.reason
            self.warnings.append(f"decision classifier unavailable ({exc.reason}); used the lexical classifier")
            return LexicalClassifier().decide(question, decisions)
        finally:
            self.calls += 1
            self.latency_ms += (time.perf_counter() - started) * 1000


def _top(probabilities: dict, n: int, *, exclude: tuple = ()) -> list[tuple[str, float]]:
    items = [(k, p) for k, p in probabilities.items() if k not in exclude]
    return sorted(items, key=lambda kp: (-kp[1], kp[0]))[:n]


def _view_text(spec) -> str:
    return f"{spec.summary} Examples: {' / '.join(spec.examples)}"[:600]


def _candidate_text(ref: dict, narrator) -> str:
    label = f"{ref.get('name')} ({ref.get('entity_class')})"
    if narrator and "narrator" in (ref.get("matched_by") or []):
        return f"{label}: the narrator, the 'I' of the notes"
    description = (ref.get("description") or "").strip()
    return f"{label}: {description}" if description else label


def _context(selected: list[str], question: str, entities: list[dict], document: str | None,
             mentions: list) -> dict:
    named = [e for e in entities if e["via"] != "first_person"]
    resolved = [e for e in named if e["candidates"]] + [
        e for e in entities if e["via"] == "first_person" and e["candidates"]
    ]
    entity_name = None
    if resolved and resolved[0]["via"] != "first_person":
        entity_name = resolved[0]["candidates"][0]["name"]
    elif named:
        entity_name = named[0]["mention"]
    return {
        "domains": ",".join(selected),
        "question": question,
        "keywords": " ".join(candidates.keywords(question, mentions)),
        "document": document,
        "entity_id": resolved[0]["candidates"][0]["_id"] if resolved else None,
        "other_entity_id": resolved[1]["candidates"][0]["_id"] if len(resolved) > 1 else None,
        "entity_name": entity_name,
    }


def _match_view(spec, question: str, entities: list[dict], cfg, threshold: float, today: date, calls: _Calls):
    """view_match payload, entity picks per param, status, and the param confidence stages."""
    used: set = set()
    params: dict = {}
    groups_by_param: dict[str, dict[str, list[dict]]] = {}
    mention_of: dict[str, tuple[str, str]] = {}
    for entity in entities:
        for ref in entity["candidates"]:
            mention_of.setdefault(ref["_id"], (entity["mention"], entity["via"]))
    decisions = []
    for name, pspec in spec.params.items():
        if pspec.type == "entity":
            wanted = str(pspec.entity_class or "").casefold()
            pool, seen = [], set()
            for entity in entities:
                for ref in entity["candidates"]:
                    if ref["_id"] not in seen and (not wanted or str(ref.get("entity_class") or "").casefold() == wanted):
                        seen.add(ref["_id"])
                        pool.append(ref)
            groups = {f"c{i + 1}": g for i, g in enumerate(candidates.twin_groups(pool))}
            groups_by_param[name] = groups
            if groups:
                decisions.append(Decision(
                    name=f"param_{name}",
                    instructions=f"Which candidate is {pspec.description or name} in the question?",
                    options={**{k: _candidate_text(g[0], cfg.narrator) for k, g in groups.items()},
                             "none": "None of these"},
                    lexical_scores={k: 1.0 for k in groups},
                    default="none", default_confidence=0.0, none_mass=0.0,
                ))
        elif pspec.type == "enum":
            decisions.append(Decision(
                name=f"param_{name}",
                instructions=pspec.description or f"Which {name} does the question ask about?",
                options={**{v: v for v in pspec.values}, "none": "None of these"},
                lexical_scores=lexical.value_scores(question, pspec.values),
                default="none", default_confidence=0.0, none_mass=0.0,
            ))
        else:
            params[name] = fill.fill_scalar(pspec, question, today, used) or {"type": pspec.type, "via": None,
                                                                               "value": None}
    answers = calls.decide(question, decisions)
    statuses: list[str] = []
    stages: list[tuple[str, float]] = []
    picks: dict[str, list[dict]] = {}
    for name, pspec in spec.params.items():
        answer = answers.get(f"param_{name}")
        if pspec.type == "entity":
            groups = groups_by_param[name]
            entry = {"type": "entity", "via": None, "input": None, "chosen": None,
                     "candidates": [g[0] for g in groups.values()], "twins": False, "confidence": 0.0}
            if not groups or answer is None or answer.choice == "none":
                statuses.append("no_match")
            else:
                group = groups[answer.choice]
                text, via = mention_of.get(group[0]["_id"], (None, "mention"))
                entry.update(input=text, via="narrator" if via == "first_person" else "mention",
                             confidence=answer.confidence)
                stages.append((f"param:{name}", answer.confidence))
                if answer.confidence < threshold:
                    statuses.append("needs_disambiguation")
                elif len(group) > 1:
                    entry.update(twins=True, candidates=group)
                    picks[name] = group
                else:
                    entry["chosen"] = group[0]
                    picks[name] = [group[0]]
            params[name] = entry
        elif pspec.type == "enum":
            if answer is not None and answer.choice != "none":
                params[name] = {"type": "enum", "via": "classifier", "value": answer.choice,
                                "confidence": answer.confidence}
            elif not pspec.required:
                params[name] = {"type": "enum", "via": "default", "value": jsonable(pspec.default)}
            else:
                params[name] = {"type": "enum", "via": None, "value": None}
                statuses.append("missing_param")
        elif params[name]["via"] is None:
            statuses.append("missing_param")
    status = next((s for s in ("no_match", "needs_disambiguation", "missing_param") if s in statuses), "ok")
    params = {name: params[name] for name in spec.params}  # the view's own order, so --param order is stable
    view_match = {"name": spec.name, "version": spec.version, "confidence": None, "status": status,
                  "params": params, "twins": [n for n, e in params.items() if e.get("twins")]}
    return view_match, picks, status, stages


def _view_args(params: dict, combo: dict) -> dict[str, str]:
    raw: dict[str, str] = {}
    for name, entry in params.items():
        if name in combo:
            raw[name] = "@" + combo[name]["_id"]
        elif entry.get("via") not in (None, "default"):
            value = entry["value"]
            raw[name] = ",".join(map(str, value)) if isinstance(value, list) else str(value)
    return raw


def _view_cmd(name: str, selected: list[str], raw: dict[str, str]) -> str:
    parts = ["artmind query views run", shlex.quote(name), "--domain", shlex.quote(",".join(selected))]
    parts += [f"--param {shlex.quote(f'{k}={v}')}" for k, v in raw.items()]
    return " ".join(parts) + " --compact"


def plan(question: str, domains: list[str] | None = None, *, execute: bool = False,
         classifier: str = "auto", today: date | None = None) -> dict:
    question = (question or "").strip()
    if not question:
        raise PlanError("the question is empty")
    today = today or date.today()
    warnings: list[str] = []
    all_routes = routes_mod.load_routes()

    # ── candidates that need no domain decision ──────────────────────────────
    overview = graph_query.domains_overview().get("rows") or []
    options = candidates.domain_options(overview)
    given_cfg = plan_config.merge([plan_config.load_config(d) for d in domains]) if domains else None
    try:
        clf, auto_reason = select_classifier(
            classifier, given_cfg.classifier if given_cfg else "auto", given_cfg.decision_model if given_cfg else None,
        )
    except ValueError as exc:
        raise PlanError(str(exc)) from exc
    calls = _Calls(clf, warnings)
    first_person = given_cfg.first_person if given_cfg else plan_config.DEFAULT_FIRST_PERSON
    mentions = candidates.find_mentions(question, first_person)
    scope = list(domains) if domains else [r["domain"] for r in overview if r.get("domain")]
    document = candidates.match_document(question, candidates.document_names(scope)) if scope else None
    has_store = any(
        r.get("table_count") for r in overview
        if any(r.get("domain") == d or str(r.get("domain")).startswith(d + ".") for d in scope)
    )
    facts = lexical.Facts(question=question.casefold(), mentions=len(mentions), document=document,
                          has_store=has_store, has_date=lexical.has_date(question))
    views, invalid = views_store.list_views(list(domains) if domains else None)
    for bad in invalid:
        warnings.append(f"view {bad['name']} is invalid and was skipped: {bad['error']}")
    views_by_name = {v.spec.name: v for v in views}

    # ── classifier call 1: domain?, view?, route ────────────────────────────
    decisions = []
    if not domains and len(options) > 1:
        decisions.append(Decision(
            name="domain", instructions="Which knowledge domain holds the answer to the question?",
            options={**{o.key: o.text() for o in options}, ALL_DOMAINS: "No single domain: search every domain."},
            lexical_scores=lexical.domain_scores(question, options),
            default=ALL_DOMAINS, default_confidence=0.0, none_mass=0.5,
        ))
    if views:
        decisions.append(Decision(
            name="view",
            instructions="Which saved query answers this question exactly? Choose none if no saved query fits.",
            options={**{n: _view_text(v.spec) for n, v in views_by_name.items()},
                     NO_VIEW: "No saved query answers this question."},
            lexical_scores=lexical.view_scores(question, [v.spec for v in views]),
            default=NO_VIEW, default_confidence=1.0, none_mass=1.0,
        ))
    decisions.append(Decision(
        name="route", instructions="What kind of question is this?",
        options={r.id: r.description for r in all_routes},
        lexical_scores=lexical.route_scores(all_routes, facts),
        default="search", default_confidence=0.5, none_mass=0.5,
    ))
    answers = calls.decide(question, decisions)

    # ── domains ─────────────────────────────────────────────────────────────
    domain_candidates: list[dict] = []
    if domains:
        selected, source, domain_conf = list(domains), "given", None
    elif len(options) == 1:
        selected, source, domain_conf = [options[0].key], ("parent" if options[0].is_parent else "matched"), 1.0
    elif not options:
        selected, source, domain_conf = [], "all", 0.0
    else:
        answer = answers["domain"]
        domain_candidates = [{"domain": k, "p": p} for k, p in _top(answer.probabilities, 3, exclude=(ALL_DOMAINS,))]
        if answer.choice == ALL_DOMAINS:
            selected, source, domain_conf = [r["domain"] for r in overview if r.get("domain")], "all", 0.0
        else:
            chosen = next(o for o in options if o.key == answer.choice)
            selected, source = [chosen.key], ("parent" if chosen.is_parent else "matched")
            domain_conf = answer.confidence
    cfg = given_cfg or plan_config.merge([plan_config.load_config(d) for d in selected])
    threshold = cfg.confidence_threshold

    # ── entities ────────────────────────────────────────────────────────────
    entities = []
    for mention in mentions:
        try:
            found = candidates.resolve_mention(mention, selected, cfg.narrator) if selected else []
        except Exception as exc:  # one mention failing must not sink the plan (spec §7)
            warnings.append(f"could not resolve {mention.text!r}: {exc}")
            found = []
        if mention.via == "first_person" and cfg.narrator and selected and not found:
            warnings.append(f"narrator {cfg.narrator.name!r} ({cfg.narrator.entity_class}) is not in the graph; "
                            "first-person words were not mapped")
        entities.append({"mention": mention.text, "span": [mention.start, mention.end], "via": mention.via,
                         "candidates": found})
    for entity in entities:
        for group in candidates.twin_groups(entity["candidates"]):
            if len(group) > 1:
                warnings.append(f"{len(group)} nodes share the name {group[0].get('name')!r} "
                                f"({group[0].get('entity_class')})")

    # ── view match (classifier call 2) or route ─────────────────────────────
    view_answer = answers.get("view")
    route_answer = answers["route"]
    chosen_view = None
    if view_answer is not None and view_answer.choice != NO_VIEW and view_answer.confidence >= threshold:
        loaded = views_by_name[view_answer.choice]
        if selected and all(domain_covered(d, loaded.spec.domains) for d in selected):
            chosen_view = loaded
        else:
            warnings.append(f"view {loaded.spec.name} matched but does not cover {selected}; not used")
    context = _context(selected, question, entities, document, mentions)
    stages: list[tuple[str, float]] = []
    if source != "given":
        stages.append(("domain", domain_conf if source != "all" else 0.0))
    if view_answer is not None:
        stages.append(("view", view_answer.confidence))
    view_match, status, unfilled, too_many = None, None, False, False
    rendered: list[dict] = []
    raws: list[dict] = []
    route_obj = None
    if chosen_view is not None:
        spec = chosen_view.spec
        view_match, picks, status, param_stages = _match_view(spec, question, entities, cfg, threshold, today, calls)
        view_match["confidence"] = view_answer.confidence
        stages += param_stages
        route_id, route_conf = "view", view_answer.confidence
        if status == "ok":
            combos = fill.combinations(picks, MAX_COMBINATIONS)
            too_many = combos is None
            for combo in (combos or []):
                raw = _view_args(view_match["params"], combo)
                raws.append(raw)
                why = f"saved view {spec.name} answers this shape (p={view_answer.confidence:.2f})"
                rendered.append({"cmd": _view_cmd(spec.name, selected, raw),
                                 "why": why + (" - one run per twin node" if combos and len(combos) > 1 else "")})
        fallback_cmd = routes_mod.render(VIEW_FALLBACK, context)
        fallbacks = ([{"when": "zero_rows", "cmd": fallback_cmd,
                       "why": "the view found nothing; search the notes for the same question"}]
                     if fallback_cmd else [])
        read: list[str] = []
    else:
        route_obj = next(r for r in all_routes if r.id == route_answer.choice)
        route_id, route_conf = route_obj.id, route_answer.confidence
        stages.append(("route", route_conf))
        for template in route_obj.steps:
            cmd = routes_mod.render(template, context)
            if cmd is None:
                unfilled = True
            else:
                rendered.append({"cmd": cmd, "why": f"route {route_obj.id} (p={route_conf:.2f})"})
        fallbacks = []
        for fallback in route_obj.fallbacks:
            cmd = routes_mod.render(fallback.cmd, context)
            if cmd:
                fallbacks.append({"when": fallback.when, "cmd": cmd, "why": f"if the first step returns {fallback.when}"})
        read = list(route_obj.read)
    rendered = [{"n": i, **step} for i, step in enumerate(rendered, 1)]

    # ── confidence ─────────────────────────────────────────────────────────
    weakest, score = min(stages, key=lambda s: s[1])
    high = (score >= threshold and status in (None, "ok") and not unfilled and not too_many
            and bool(rendered) and bool(selected))
    level = "high" if high else "low"
    steps, suggested = (rendered, []) if high else ([], rendered)
    if not high:
        read = [*read, ROUTING_TABLE]

    # ── --execute ───────────────────────────────────────────────────────────
    executed = None
    rows: list = []
    rows_total, truncated = 0, False
    if execute:
        if not high:
            warnings.append("--execute ignored: the plan is not high-confidence")
        elif chosen_view is None:
            warnings.append("--execute ignored: the route is not a saved view")
        elif len(raws) != 1:
            warnings.append(f"--execute ignored: the view has {len(raws)} parameter combinations (twin nodes)")
        else:
            try:
                envelope = views_runner.run_view(chosen_view.spec, selected, raws[0])
                rows = envelope.pop("rows", []) or []
                rows_total = envelope.pop("rows_total", len(rows))
                truncated = bool(envelope.pop("truncated", False))
                executed = {"cmd": steps[0]["cmd"], "status": envelope.get("status", "ok"), "error": None,
                            "result": envelope}
            except ViewError as exc:
                executed = {"cmd": steps[0]["cmd"], "status": "error", "error": str(exc), "result": None}

    resolved_classes = {ref.get("entity_class") for e in entities for ref in e["candidates"]}
    lexical_used = clf.backend == "lexical" or calls.fell_back
    return {
        "plan_version": PLAN_VERSION,
        "question": question,
        "domains": {"selected": selected, "source": source, "confidence": domain_conf,
                    "candidates": domain_candidates},
        "route": {
            "id": route_id, "confidence": route_conf,
            "alternatives": [{"id": k, "p": p} for k, p in _top(route_answer.probabilities, 2, exclude=(route_id,))],
            "triggered_by": lexical.triggered(route_obj, facts) if (route_obj and lexical_used) else [],
        },
        "view_match": view_match,
        "entities": entities,
        "store": candidates.store_facts(selected, question, resolved_classes) if selected else None,
        "confidence": {"level": level, "score": round(score, 4), "threshold": threshold, "weakest": weakest},
        "steps": steps,
        "suggested_steps": suggested,
        "fallbacks": fallbacks,
        "read": read,
        "classifier": {"backend": clf.backend, "model": clf.model, "calls": calls.calls,
                       "latency_ms": round(calls.latency_ms, 1), "fell_back": calls.fell_back,
                       "reason": calls.reason or auto_reason},
        "warnings": warnings,
        "executed": executed,
        "rows": rows,
        "rows_total": rows_total,
        "truncated": truncated,
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_planner.py -v`
Expected: all PASS. Two cases to check if one fails:
- `test_a_route_question_fills_the_route_templates`: "How often" is the only trigger hit, so aggregate scores 1.5 of a 2.0 denominator (p = 0.75 ≥ 0.7).
- `test_the_domain_is_chosen_when_not_given`: "journal" is shared with the domain name. Its p is 1/(1+0.5) ≈ 0.67, so the test asserts only the selection, not the level.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_plan/planner.py test/test_query_plan_planner.py
git commit -m "feat(plan): the planner, stages to payload, with --execute for one view

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: `artmind query plan` on the CLI, and its place in traces

**Files:**
- Modify: `artmind/cli.py` (new command after `query_views_run`; `COMMAND_GROUPS`; `query` group docstring)
- Modify: `artmind/traces/path.py` (`_EXACT`, `span_layer`, `views_check`)
- Modify: `artmind/traces/analysis.py` (`signals`)
- Test: `test/test_query_plan_cli.py` (create), `test/test_traces_path.py`, `test/test_traces_views_check.py`, `test/test_cli_guide.py` (run only)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_cli.py`:

```python
"""`artmind query plan` (spec 2026-10-22 §4.1)."""

import json

from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_plan import planner


def test_the_command_prints_the_plan(monkeypatch):
    seen = {}

    def fake_plan(question, domains, execute=False, classifier="auto"):
        seen.update(question=question, domains=domains, execute=execute, classifier=classifier)
        return {"plan_version": 1, "steps": []}

    monkeypatch.setattr(planner, "plan", fake_plan)
    result = CliRunner().invoke(cli, ["query", "plan", "--domain", "a,b", "--execute", "--classifier", "lexical",
                                      "--compact", "who leads?"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"plan_version": 1, "steps": []}
    assert seen == {"question": "who leads?", "domains": ["a", "b"], "execute": True, "classifier": "lexical"}


def test_domain_is_optional(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains, **kw: {"domains_seen": domains})
    result = CliRunner().invoke(cli, ["query", "plan", "--compact", "who leads?"])
    assert json.loads(result.output) == {"domains_seen": None}


def test_a_plan_error_is_a_click_error(monkeypatch):
    def boom(*a, **k):
        raise planner.PlanError("the question is empty")

    monkeypatch.setattr(planner, "plan", boom)
    result = CliRunner().invoke(cli, ["query", "plan", " "])
    assert result.exit_code != 0 and "the question is empty" in result.output
```

Append to `test/test_traces_path.py`:

```python
def test_a_plan_span_is_its_own_layer_and_an_executed_one_is_a_view():
    from artmind.traces import path

    plan_span = {"command": "query plan", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 0,
                 "started_at": 1.0, "evidence": {}}
    assert path.span_layer(plan_span) == "plan"
    assert path.derive([plan_span], answer=None)["graph_attempts"] == 0
    executed = {**plan_span, "rows": 2}
    assert path.span_layer(executed) == "view"
    assert path.derive([executed], answer=None)["graph_attempts"] == 1
```

Append to `test/test_traces_views_check.py`:

```python
def test_a_plan_counts_as_the_views_check():
    from artmind.traces.path import views_check

    spans = [
        {"command": "query plan", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 0, "started_at": 1.0},
        {"command": "query vector-text", "args": {"domain": ["b"]}, "exit_code": 0, "rows": 3, "started_at": 2.0},
    ]
    assert views_check(spans)["status"] == "first"
    no_domain = [{**spans[0], "args": {"domain": []}}, spans[1]]
    assert views_check(no_domain)["listed_all"] is True
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_cli.py test/test_traces_path.py test/test_traces_views_check.py -v -k "plan or command or domain_is_optional"`
Expected: FAIL (`No such command 'plan'`; layer `discovery`; views check `skipped`).

- [ ] **Step 3: Add the command to `artmind/cli.py`**

After `query_views_run`, add:

```python
@query.command("plan")
@click.option("--domain", "domain", multiple=True, help="Scope to these domains (repeatable; comma-splittable). Omit and the plan chooses")
@click.option("--execute", is_flag=True, help="Also run the step when the plan is one high-confidence saved view")
@click.option("--classifier", type=click.Choice(["auto", "lexical", "decision"]), default="auto", show_default=True,
              help="auto: the decision model when ARTMIND_DECISION_URL is set, else lexical rules")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def query_plan_command(domain: tuple, execute: bool, classifier: str, compact: bool, question: str) -> None:
    """Plan the retrieval for a question: domains, saved view, route and ready-to-run steps.

    Read-only and deterministic. Run `steps` when `confidence.level` is high; when it
    is low, `steps` is empty: judge `suggested_steps` and read the routing table.
    """
    from neo4j.exceptions import DriverError, Neo4jError

    from artmind.query_plan import planner

    domains = _parse_domains(domain) if domain else None
    try:
        result = planner.plan(question, domains, execute=execute, classifier=classifier)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except (DriverError, Neo4jError) as exc:
        raise click.ClickException(f"Neo4j is unreachable or failed: {exc}") from exc
    _echo_json(result, compact)
```

In `COMMAND_GROUPS`, in the `"artmind query"` list, insert as the first entry:

```python
        {"name": "Planning", "commands": ["plan"]},
```

In the `query` group's docstring (the function decorated `@cli.group(cls=TracedGroup)` near line 2579), add a sentence at the start of the groups paragraph: ``Start with `plan` (deterministic route, view match and ready steps for a question).``

- [ ] **Step 4: Teach `artmind/traces/path.py` about plans**

Add `"query plan": "plan",` to `_EXACT`. Replace `span_layer` with:

```python
def span_layer(span: dict) -> str:
    """`layer_of` the span's command, except: a dry run is discovery (it executed
    nothing), and a `query plan --execute` that returned rows is a view run."""
    if is_dry_run(span):
        return "discovery"
    command = span.get("command")
    if command == "query plan":
        return "view" if (span.get("rows") or 0) > 0 else "plan"
    return layer_of(command)
```

In `views_check`, immediately after `domains = span_domains(span)`, add:

```python
        if command == "query plan":
            # The plan matches the question against the view catalogue of its
            # domains (all of them when it chose the domain itself).
            if domains is None:
                listed_all = True
            else:
                listed |= domains
            if (span.get("rows") or 0) > 0:
                applicable = True
                ran = True
            continue
```

- [ ] **Step 5: Keep plan spans out of `empty_rows` in `artmind/traces/analysis.py`**

In `signals`, extend the `empty_rows` condition from Task 2 with `and s.get("command") != "query plan"`.

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_cli.py test/test_traces_path.py test/test_traces_views_check.py test/test_traces_analysis.py test/test_cli_guide.py -v`
Expected: all PASS (`test_every_command_is_routed_into_the_guide` passes because `plan` is in `COMMAND_GROUPS`).

- [ ] **Step 7: Live probe (needs Neo4j and a vault with data)**

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind query plan --domain personal_journal --classifier lexical "How many visits did I make to Lakeport in 2025?" | head -60
```

Expected: a JSON payload with every §4.2 key. `route.id` is `view` when the vault's view matches; otherwise a route with `steps` or `suggested_steps`. Paste nothing from this output into the checkout.

- [ ] **Step 8: Commit**

```bash
git add artmind/cli.py artmind/traces/path.py artmind/traces/analysis.py test/test_query_plan_cli.py test/test_traces_path.py test/test_traces_views_check.py
git commit -m "feat(plan): artmind query plan, traced as its own layer and as the views check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Phase B checkpoint:** `uv run --group dev pytest test/test_query_plan_*.py test/test_traces_*.py test/test_cli_guide.py -q` → all PASS.


---

# Phase C — The decision-model backend

### Task 16: Jev-API classifier, fallback and circuit breaker

**Files:**
- Create: `artmind/query_plan/classify/jev.py`
- Modify: `artmind/query_plan/classify/__init__.py` (`select_classifier`)
- Test: `test/test_query_plan_jev.py` (create); update `test/test_query_plan_lexical.py::test_auto_without_an_endpoint_is_lexical` only if it breaks

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_jev.py`:

```python
"""The Jev-API decision classifier (spec 2026-10-22 §4.4, §7). No network: urlopen is faked."""

import io
import json
import socket
import urllib.error

import pytest
from _fake_credentials import FAKE_CREDENTIAL

from artmind.query_plan import planner
from artmind.query_plan.classify import ClassifierUnavailable, Decision, select_classifier
from artmind.query_plan.classify import jev

DECISIONS = [
    Decision("view", "Which saved query?", {"visits_by_year": "Visits ...", "none": "No saved query"},
             {"visits_by_year": 5.0}, default="none", default_confidence=1.0, none_mass=1.0),
    Decision("route", "What kind of question?", {"aggregate": "A count", "search": "Anything else"},
             {"aggregate": 1.5}, default="search", default_confidence=0.5, none_mass=0.5),
]


@pytest.fixture(autouse=True)
def _fresh_breakers():
    jev._reset_breakers()
    yield
    jev._reset_breakers()


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, *, answers=None, error=None):
    sent = {}

    def fake_urlopen(request, timeout):
        sent.update(url=request.full_url, body=json.loads(request.data), headers=dict(request.headers),
                    timeout=timeout)
        if error is not None:
            raise error
        return _Response(json.dumps({"model": "nimble", "answers": answers}).encode())

    monkeypatch.setattr(jev.urllib.request, "urlopen", fake_urlopen)
    return sent


def _clf(**kw):
    return jev.JevClassifier("http://localhost:11434/v1/systemone", "nimble", **kw)


def test_the_request_has_the_jev_shape(monkeypatch):
    sent = _serve(monkeypatch, answers={
        "view": {"choice": "visits_by_year", "probabilities": {"visits_by_year": 0.9, "none": 0.1}, "confidence": 0.9},
        "route": {"choice": "aggregate", "probabilities": {"aggregate": 0.7, "search": 0.3}, "confidence": 0.7},
    })
    answers = _clf(api_key=FAKE_CREDENTIAL, timeout=2.5).decide("How many visits?", DECISIONS)
    assert sent["body"] == {
        "model": "nimble",
        "state": {"question": "How many visits?"},
        "questions": {
            "view": {"type": "choice", "instructions": "Which saved query?",
                     "criteria": {"visits_by_year": "Visits ...", "none": "No saved query"}},
            "route": {"type": "choice", "instructions": "What kind of question?",
                      "criteria": {"aggregate": "A count", "search": "Anything else"}},
        },
    }
    assert sent["headers"]["Authorization"] == f"Bearer {FAKE_CREDENTIAL}"
    assert sent["timeout"] == 2.5
    assert answers["view"].choice == "visits_by_year" and answers["view"].confidence == 0.9


def test_a_missing_choice_is_the_most_probable_option(monkeypatch):
    _serve(monkeypatch, answers={
        "view": {"probabilities": {"visits_by_year": 0.2, "none": 0.8}},
        "route": {"probabilities": {"aggregate": 0.6, "search": 0.4}},
    })
    answers = _clf().decide("q", DECISIONS)
    assert answers["view"].choice == "none" and answers["view"].confidence == 0.8


@pytest.mark.parametrize("answers", [
    {"view": {"choice": "elsewhere", "probabilities": {"none": 1.0}}, "route": {"probabilities": {"search": 1.0}}},
    {"view": {"probabilities": {"none": "high"}}, "route": {"probabilities": {"search": 1.0}}},
    {"route": {"probabilities": {"search": 1.0}}},
])
def test_an_unusable_answer_is_bad_answer(monkeypatch, answers):
    _serve(monkeypatch, answers=answers)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == "bad_answer"


@pytest.mark.parametrize("error, reason", [
    (socket.timeout("timed out"), "timeout"),
    (urllib.error.URLError(socket.timeout("timed out")), "timeout"),
    (urllib.error.URLError(ConnectionRefusedError()), "unreachable"),
    (urllib.error.HTTPError("http://x", 500, "boom", {}, None), "http_500"),
])
def test_transport_failures_have_a_reason(monkeypatch, error, reason):
    _serve(monkeypatch, error=error)
    with pytest.raises(ClassifierUnavailable) as caught:
        _clf().decide("q", DECISIONS)
    assert caught.value.reason == reason


def test_the_breaker_opens_after_three_failures_and_closes_after_the_cooldown(monkeypatch):
    now = [100.0]
    _serve(monkeypatch, error=socket.timeout("timed out"))
    clf = _clf(clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(ClassifierUnavailable):
            clf.decide("q", DECISIONS)
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "circuit_open"
    now[0] += jev.BREAKER_COOLDOWN_S + 1
    with pytest.raises(ClassifierUnavailable) as caught:
        clf.decide("q", DECISIONS)
    assert caught.value.reason == "timeout"


def test_the_planner_falls_back_to_lexical(monkeypatch):
    class Down:
        backend, model = "decision", "nimble"

        def decide(self, question, decisions):
            raise ClassifierUnavailable("timeout")

    warnings: list[str] = []
    calls = planner._Calls(Down(), warnings)
    answers = calls.decide("How often?", DECISIONS)
    assert answers["view"].choice == "visits_by_year"
    assert calls.fell_back and calls.reason == "timeout" and calls.calls == 1
    assert warnings == ["decision classifier unavailable (timeout); used the lexical classifier"]


def test_select_classifier_reads_the_endpoint_settings():
    env = {"ARTMIND_DECISION_URL": "http://localhost:11434/v1/systemone", "ARTMIND_DECISION_MODEL": "nimble",
           "ARTMIND_DECISION_TIMEOUT": "3"}
    clf, reason = select_classifier("auto", "auto", None, env=env)
    assert (clf.backend, clf.model, clf.timeout, reason) == ("decision", "nimble", 3.0, None)
    clf, _ = select_classifier("auto", "auto", "tev1", env=env)
    assert clf.model == "tev1"
    clf, reason = select_classifier("auto", "lexical", None, env=env)
    assert (clf.backend, reason) == ("lexical", "domain_config")
    with pytest.raises(ValueError, match="ARTMIND_DECISION_URL"):
        select_classifier("decision", "auto", None, env={})
    with pytest.raises(ValueError, match="ARTMIND_DECISION_MODEL"):
        select_classifier("auto", "auto", None, env={"ARTMIND_DECISION_URL": "http://x"})
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_jev.py -v`
Expected: FAIL with `ImportError: cannot import name 'jev'`.

- [ ] **Step 3: Write `artmind/query_plan/classify/jev.py`**

```python
"""Decision-model classifier over the Jev API (spec 2026-10-22 §4.4, §7).

One POST per batch of decisions to ARTMIND_DECISION_URL: Ollama's
`/v1/systemone` locally, or a Clef/Jev endpoint. A decision model returns a
probability for every option in one prefill pass, so the same input under a
pinned model gives the same answer. Any failure raises ClassifierUnavailable
and the planner redoes the call lexically. After BREAKER_FAILURES consecutive
failures the endpoint is skipped for BREAKER_COOLDOWN_S, per process, so a
down endpoint does not cost every plan a timeout.

Privacy: the request carries the question, option texts and candidate names.
With a local endpoint nothing leaves the machine; a hosted one receives them.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from artmind.query_plan.classify.base import Answer, ClassifierUnavailable, Decision

BREAKER_FAILURES = 3
BREAKER_COOLDOWN_S = 60.0
_breakers: dict[str, dict] = {}


def _reset_breakers() -> None:
    """Test isolation only."""
    _breakers.clear()


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse(payload: dict, decision: Decision) -> Answer:
    raw = payload["answers"][decision.name]
    probs = raw.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        raise ValueError(f"{decision.name}: no probabilities")
    for key, value in probs.items():
        if key not in decision.options or not _number(value):
            raise ValueError(f"{decision.name}: bad probability {key!r}={value!r}")
    keys = list(decision.options)
    probabilities = {k: round(float(probs.get(k, 0.0)), 4) for k in keys}
    choice = raw.get("choice") or raw.get("answer") or raw.get("label")
    if choice is None:
        choice = max(keys, key=lambda k: (probabilities[k], -keys.index(k)))
    if choice not in decision.options:
        raise ValueError(f"{decision.name}: choice {choice!r} is not an option")
    confidence = raw.get("confidence")
    confidence = round(float(confidence), 4) if _number(confidence) else probabilities[choice]
    return Answer(choice, probabilities, confidence)


class JevClassifier:
    backend = "decision"

    def __init__(self, url: str, model: str, *, api_key: str | None = None, timeout: float = 5.0,
                 clock=time.monotonic):
        self.url = url
        self.model = model
        self.api_key = api_key
        self.timeout = float(timeout)
        self.clock = clock

    def _breaker(self) -> dict:
        return _breakers.setdefault(self.url, {"failures": 0, "open_until": 0.0})

    def _failure(self, reason: str) -> ClassifierUnavailable:
        breaker = self._breaker()
        breaker["failures"] += 1
        if breaker["failures"] >= BREAKER_FAILURES:
            breaker["failures"] = 0
            breaker["open_until"] = self.clock() + BREAKER_COOLDOWN_S
        return ClassifierUnavailable(reason)

    def request_body(self, question: str, decisions: list[Decision]) -> dict:
        return {
            "model": self.model,
            "state": {"question": question},
            "questions": {
                d.name: {"type": d.type, "instructions": d.instructions, "criteria": dict(d.options)}
                for d in decisions
            },
        }

    def decide(self, question: str, decisions: list[Decision]) -> dict[str, Answer]:
        if self.clock() < self._breaker()["open_until"]:
            raise ClassifierUnavailable("circuit_open")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url, data=json.dumps(self.request_body(question, decisions)).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._failure(f"http_{exc.code}") from exc
        except urllib.error.URLError as exc:
            timed_out = isinstance(exc.reason, (TimeoutError, socket.timeout))
            raise self._failure("timeout" if timed_out else "unreachable") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise self._failure("timeout") from exc
        except OSError as exc:
            raise self._failure("unreachable") from exc
        except ValueError as exc:
            raise self._failure("bad_answer") from exc
        try:
            answers = {d.name: _parse(payload, d) for d in decisions}
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise self._failure("bad_answer") from exc
        self._breaker()["failures"] = 0
        return answers
```

- [ ] **Step 4: Replace `select_classifier` in `artmind/query_plan/classify/__init__.py`**

Replace the whole module with:

```python
"""Plan classifiers: lexical rules always; a decision model when configured (spec 2026-10-22 §4.4)."""

from __future__ import annotations

import os

from artmind.query_plan.classify.base import Answer, Classifier, ClassifierUnavailable, Decision
from artmind.query_plan.classify.lexical import LexicalClassifier

__all__ = ["Answer", "Classifier", "ClassifierUnavailable", "Decision", "LexicalClassifier", "select_classifier"]

DEFAULT_TIMEOUT_S = 5.0


def _timeout(env) -> float:
    try:
        value = float(env.get("ARTMIND_DECISION_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def select_classifier(option: str, domain_choice: str, model_override: str | None,
                      env=None) -> tuple[Classifier, str | None]:
    """(classifier, why it is lexical) for `--classifier` and the domain config.

    Settings are artmind-specific names (ARTMIND_DECISION_URL / _MODEL / _API_KEY /
    _TIMEOUT), so no variable another tool exports can redirect them (CLAUDE.md §5).
    """
    env = os.environ if env is None else env
    url = (env.get("ARTMIND_DECISION_URL") or "").strip()
    model = (model_override or env.get("ARTMIND_DECISION_MODEL") or "").strip()
    if option == "lexical":
        return LexicalClassifier(), None
    if option == "auto" and domain_choice == "lexical":
        return LexicalClassifier(), "domain_config"
    if not url:
        if option == "decision":
            raise ValueError("--classifier decision needs ARTMIND_DECISION_URL and ARTMIND_DECISION_MODEL")
        return LexicalClassifier(), "no_endpoint"
    if not model:
        raise ValueError("ARTMIND_DECISION_URL is set but ARTMIND_DECISION_MODEL is not")
    from artmind.query_plan.classify.jev import JevClassifier

    return JevClassifier(url, model, api_key=(env.get("ARTMIND_DECISION_API_KEY") or None),
                         timeout=_timeout(env)), None
```

- [ ] **Step 5: Run the classifier and planner tests**

Run: `uv run --group dev pytest test/test_query_plan_jev.py test/test_query_plan_lexical.py test/test_query_plan_planner.py -v`
Expected: all PASS. `test_auto_without_an_endpoint_is_lexical` still passes: it deletes `ARTMIND_DECISION_URL` from the real environment.

- [ ] **Step 6: Commit**

```bash
git add artmind/query_plan/classify test/test_query_plan_jev.py
git commit -m "feat(plan): decision-model classifier over the Jev API, with lexical fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

# Phase D — Labelled set and offline replay

### Task 17: The labelled set, `plan labels validate` and `plan config check`

**Files:**
- Create: `artmind/query_plan/labels.py`
- Modify: `artmind/cli.py` (new top-level `plan` group; `COMMAND_GROUPS`)
- Test: `test/test_query_plan_labels.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_labels.py`:

```python
"""The labelled set (spec 2026-10-22 §4.6). Synthetic labels only."""

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from artmind.cli import cli
from artmind.query_plan import labels

VIEWS = {"visits_by_year": {"person", "place", "year"}}


def _data(**over):
    label = {"id": "L001", "question": "How many visits did Ada make to Lakeport in 2025?", "source": "written",
             "domains": ["journal"], "route": "view", "view": "visits_by_year",
             "params": {"person": "Ada", "place": ["Lakeport", "Lakeport"], "year": 2025}}
    return {"version": 1, "labels": [{**label, **over}]}


def test_a_valid_label_parses():
    (label,) = labels.parse_labels(_data(), Path("."), view_params=VIEWS)
    assert (label.id, label.route, label.view, label.expect) == ("L001", "view", "visits_by_year", "high")
    assert label.domains == ("journal",)


@pytest.mark.parametrize("over, message", [
    ({"id": "7"}, "id must be L"),
    ({"route": "nowhere"}, "unknown route"),
    ({"view": "missing_view"}, "no saved view"),
    ({"params": {"colour": "red"}}, "has no param"),
    ({"source": "chat"}, "source must be"),
    ({"expect": "maybe"}, "expect must be"),
    ({"domains": []}, "domains must be"),
    ({"benchmark": "Q07"}, "exactly one of question / benchmark"),
    ({"route": "view", "view": None, "params": {}}, "route view needs a view"),
])
def test_each_rule_is_reported(over, message):
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(_data(**over), Path("."), view_params=VIEWS)
    assert any(message in e for e in caught.value.errors), caught.value.errors


def test_all_errors_are_collected_not_just_the_first():
    data = _data(route="nowhere", source="chat")
    with pytest.raises(labels.LabelError) as caught:
        labels.parse_labels(data, Path("."), view_params=VIEWS)
    assert len(caught.value.errors) == 2


def test_a_benchmark_reference_takes_its_question_text(tmp_path):
    (tmp_path / "benchmarking").mkdir()
    (tmp_path / "benchmarking" / "questions.md").write_text(
        "## Q07 — Rates\n\n**Question:** Which rate applied in January?\n\n**Evaluation comment:** x\n"
    )
    data = {"version": 1, "benchmark_file": "benchmarking/questions.md", "labels": [
        {"id": "L002", "benchmark": "Q07", "source": "benchmark", "domains": ["bank"], "route": "time_history",
         "view": None},
    ]}
    (label,) = labels.parse_labels(data, tmp_path, view_params={})
    assert label.question == "Which rate applied in January?" and label.benchmark == "Q07"


def test_the_cli_validates_a_file(tmp_path):
    path = tmp_path / "plan_labels.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "labels": [
        {"id": "L001", "question": "What did the team decide?", "source": "written", "domains": ["journal"],
         "route": "search", "view": None},
    ]}))
    result = CliRunner().invoke(cli, ["plan", "labels", "validate", "--file", str(path), "--compact"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"command": "plan labels validate", "file": str(path), "ok": True,
                                         "labels": 1, "errors": []}


def test_the_cli_fails_on_an_invalid_file(tmp_path):
    path = tmp_path / "plan_labels.yaml"
    path.write_text("version: 1\nlabels: [{id: bad}]\n")
    result = CliRunner().invoke(cli, ["plan", "labels", "validate", "--file", str(path), "--compact"])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_plan_config_check_reports_each_file(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_PLAN_DIR", tmp_path)
    (tmp_path / "journal.yaml").write_text("confidence_threshold: 0.8\n")
    (tmp_path / "bank.yaml").write_text("colour: blue\n")
    result = CliRunner().invoke(cli, ["plan", "config", "check", "--compact"])
    assert result.exit_code == 1
    by_domain = {r["domain"]: r for r in json.loads(result.output)["results"]}
    assert by_domain["journal"]["ok"] is True and by_domain["bank"]["ok"] is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_labels.py -v`
Expected: FAIL with `ImportError` / `No such command 'plan'`.

- [ ] **Step 3: Write `artmind/query_plan/labels.py`**

```python
"""The labelled set for `query plan` replay: <vault>/benchmarking/plan_labels.yaml
(spec 2026-10-22 §4.6). Private: it holds real questions, so it lives in the
vault and is committed only to the vault's own repository, never to the checkout.

Validation collects every error rather than stopping at the first, so one run
of `artmind plan labels validate` shows everything to fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from artmind.query_plan import routes as routes_mod

_ID_RE = re.compile(r"^L\d{3,}$")
_SOURCE_RE = re.compile(r"^(benchmark|written|trace:[0-9a-f]{8,})$")
_KEYS = frozenset({"id", "question", "benchmark", "source", "domains", "route", "view", "params", "expect",
                   "expected_documents", "notes"})
_TOP = frozenset({"version", "benchmark_file", "labels"})


class LabelError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Label:
    id: str
    question: str
    source: str
    domains: tuple[str, ...]
    route: str
    view: str | None
    params: dict
    expect: str = "high"
    expected_documents: tuple[str, ...] = ()
    notes: str = ""
    benchmark: str | None = None


def default_path() -> Path | None:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return Path(vault) / "benchmarking" / "plan_labels.yaml" if vault else None


def _view_params() -> dict[str, set[str]]:
    from artmind.kg_views import store

    loaded, _ = store.list_views(None)
    return {v.spec.name: set(v.spec.params) for v in loaded}


def _benchmark_questions(base_dir: Path, name: str, errors: list[str]) -> dict[str, str]:
    from artmind.benchmark import _parse_questions_file

    try:
        return {q["qid"]: q["question"] for q in _parse_questions_file(base_dir / name)}
    except OSError as exc:
        errors.append(f"benchmark_file: {exc}")
        return {}


def parse_labels(data, base_dir: Path, *, view_params: dict[str, set[str]] | None = None) -> list[Label]:
    """`base_dir` is the vault root: `benchmark_file` is relative to it."""
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("labels"), list):
        raise LabelError(["the labels file must be a mapping with `version: 1` and a `labels` list"])
    errors: list[str] = []
    extra = set(data) - _TOP
    if extra:
        errors.append(f"unknown top-level key(s) {sorted(extra)}")
    questions = _benchmark_questions(base_dir, data["benchmark_file"], errors) if data.get("benchmark_file") else {}
    route_ids = {r.id for r in routes_mod.load_routes()} | {"view"}
    view_params = _view_params() if view_params is None else view_params
    out: list[Label] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["labels"]):
        if not isinstance(raw, dict):
            errors.append(f"labels[{i}] must be a mapping")
            continue
        lid = raw.get("id")
        where = f"label {lid}" if isinstance(lid, str) else f"labels[{i}]"
        bad = set(raw) - _KEYS
        if bad:
            errors.append(f"{where}: unknown key(s) {sorted(bad)}")
        if not isinstance(lid, str) or not _ID_RE.match(lid):
            errors.append(f"{where}: id must be L followed by 3 or more digits")
        elif lid in seen:
            errors.append(f"duplicate label id {lid}")
        else:
            seen.add(lid)
        question = None
        if ("question" in raw) == ("benchmark" in raw):
            errors.append(f"{where}: give exactly one of question / benchmark")
        elif "question" in raw:
            if isinstance(raw["question"], str) and raw["question"].strip():
                question = raw["question"].strip()
            else:
                errors.append(f"{where}: question must be a non-empty string")
        else:
            question = questions.get(raw["benchmark"])
            if question is None:
                errors.append(f"{where}: benchmark {raw['benchmark']!r} is not in benchmark_file")
        source = raw.get("source")
        if not isinstance(source, str) or not _SOURCE_RE.match(source):
            errors.append(f"{where}: source must be benchmark, written or trace:<id>")
        domains = raw.get("domains")
        if not isinstance(domains, list) or not domains or not all(isinstance(d, str) and d for d in domains):
            errors.append(f"{where}: domains must be a non-empty list of names")
            domains = []
        route = raw.get("route")
        if route not in route_ids:
            errors.append(f"{where}: unknown route {route!r}")
        view = raw.get("view")
        params = raw.get("params") or {}
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be a mapping")
            params = {}
        if view is not None:
            if view not in view_params:
                errors.append(f"{where}: no saved view {view!r}")
            elif set(params) - view_params[view]:
                errors.append(f"{where}: view {view} has no param(s) {sorted(set(params) - view_params[view])}")
        elif params:
            errors.append(f"{where}: params need a view")
        if route == "view" and view is None:
            errors.append(f"{where}: route view needs a view")
        expect = raw.get("expect", "high")
        if expect not in ("high", "low"):
            errors.append(f"{where}: expect must be high or low")
        documents = raw.get("expected_documents") or []
        if not isinstance(documents, list) or not all(isinstance(d, str) for d in documents):
            errors.append(f"{where}: expected_documents must be a list of paths")
            documents = []
        out.append(Label(lid if isinstance(lid, str) else "", question or "", source or "", tuple(domains),
                         route or "", view, dict(params), expect, tuple(documents), str(raw.get("notes") or ""),
                         raw.get("benchmark")))
    if errors:
        raise LabelError(errors)
    return out


def load_labels(path: Path) -> list[Label]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LabelError([f"{path}: {exc}"]) from exc
    except yaml.YAMLError as exc:
        raise LabelError([f"{path} is not valid YAML: {exc}"]) from exc
    return parse_labels(data, path.resolve().parent.parent)


def check_entities(labels: list[Label]) -> list[str]:
    """Needs Neo4j: every entity param name resolves in its label's domains by name evidence."""
    from artmind import vector_query
    from artmind.kg_views import store

    errors = []
    for label in labels:
        if not label.view:
            continue
        spec = store.load_view(label.view).spec
        for name, expected in label.params.items():
            if spec.params[name].type != "entity":
                continue
            for value in (expected if isinstance(expected, list) else [expected]):
                rows = vector_query.entity_resolve(list(label.domains), str(value), 10).get("rows", [])
                if not any({"exact", "fulltext"} & set(r.get("matched_by") or []) for r in rows):
                    errors.append(f"label {label.id}: {name}={value!r} does not resolve in {list(label.domains)}")
    return errors
```

- [ ] **Step 4: Add the `plan` group to `artmind/cli.py`**

Near the other top-level groups (for example right after the `traces` group's commands), add:

```python
# ── artmind plan (spec 2026-10-22: config checks, labelled set, replay) ─────────


@cli.group("plan")
def plan_group() -> None:
    """Operate `query plan`: per-domain config checks, the labelled set, offline replay.

    Not proxied. The labels file and replay output hold real questions: they stay in the
    vault (`<vault>/benchmarking/`, `<vault>/.artmind/logs/`), never in a checkout.
    """


@plan_group.group("config")
def plan_config_group() -> None:
    """Per-domain plan config (`domains/plan/<domain>.yaml`): narrator, threshold, classifier."""


@plan_config_group.command("check")
@click.option("--domain", "domain", multiple=True, help="Only these domains (repeatable; comma-splittable). Default: every file")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def plan_config_check(domain: tuple, compact: bool) -> None:
    """Validate plan config files and, with Neo4j up, that each configured narrator exists."""
    import paths
    from artmind.query_plan import candidates as plan_candidates
    from artmind.query_plan import config as plan_config

    names = _parse_repeatable(domain) if domain else sorted(p.stem for p in Path(paths.DOMAIN_PLAN_DIR).glob("*.yaml"))
    results = []
    for name in names:
        path = plan_config.config_file(name)
        entry = {"domain": name, "file": str(path) if path else None, "ok": True, "errors": [], "narrator_found": None}
        try:
            cfg = plan_config.load_config(name)
        except plan_config.PlanConfigError as exc:
            entry.update(ok=False, errors=[str(exc)])
            results.append(entry)
            continue
        if cfg.narrator:
            try:
                entry["narrator_found"] = bool(plan_candidates.narrator_candidates(cfg.narrator, [name]))
            except Exception as exc:  # Neo4j down: report, do not fail the file
                entry["errors"].append(f"narrator not checked: {exc}")
            if entry["narrator_found"] is False:
                entry["ok"] = False
                entry["errors"].append(f"narrator {cfg.narrator.name!r} ({cfg.narrator.entity_class}) is not in {name}")
        results.append(entry)
    _echo_json({"command": "plan config check", "results": results}, compact)
    if not all(r["ok"] for r in results):
        raise SystemExit(1)


@plan_group.group("labels")
def plan_labels_group() -> None:
    """The labelled set (`<vault>/benchmarking/plan_labels.yaml`)."""


def _labels_path(file_path: Path | None) -> Path:
    from artmind.query_plan import labels as plan_labels

    path = file_path or plan_labels.default_path()
    if path is None:
        raise click.ClickException("not inside a vault: pass --file")
    return path


@plan_labels_group.command("validate")
@click.option("--file", "file_path", type=click.Path(path_type=Path), default=None,
              help="Labels file. Default: <vault>/benchmarking/plan_labels.yaml")
@click.option("--checkEntities", "check_entities", is_flag=True, help="Also check (needs Neo4j) that every entity param name resolves")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def plan_labels_validate(file_path: Path | None, check_entities: bool, compact: bool) -> None:
    """Check every rule of the labels file and report all problems at once."""
    from artmind.query_plan import labels as plan_labels

    path = _labels_path(file_path)
    try:
        loaded = plan_labels.load_labels(path)
    except plan_labels.LabelError as exc:
        _echo_json({"command": "plan labels validate", "file": str(path), "ok": False, "labels": 0,
                    "errors": exc.errors}, compact)
        raise SystemExit(1)
    errors = plan_labels.check_entities(loaded) if check_entities else []
    _echo_json({"command": "plan labels validate", "file": str(path), "ok": not errors, "labels": len(loaded),
                "errors": errors}, compact)
    if errors:
        raise SystemExit(1)
```

`test_the_cli_fails_on_an_invalid_file` reads `json.loads(result.output)["ok"]`; the payload above always carries `ok`.

In `COMMAND_GROUPS`, add `"plan"` to the `"artmind"` → `"Query"` commands list (after `"query"`), and add a new entry:

```python
    "artmind plan": [
        {"name": "Plan", "commands": ["config", "labels"]},
    ],
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_labels.py test/test_cli_guide.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/query_plan/labels.py artmind/cli.py test/test_query_plan_labels.py
git commit -m "feat(plan): the labelled set, plan labels validate and plan config check

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 18: Offline replay and `artmind plan replay`

**Files:**
- Create: `artmind/query_plan/replay.py`
- Modify: `artmind/cli.py` (`plan replay`; `COMMAND_GROUPS`)
- Modify: `justfile` (recipe `plan-replay`)
- Test: `test/test_query_plan_replay.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_replay.py`:

```python
"""Replay scoring (spec 2026-10-22 §4.6). Plans are synthetic dicts; no graph."""

import json

from artmind.query_plan import replay
from artmind.query_plan.labels import Label


def _label(**over):
    base = dict(id="L001", question="q", source="written", domains=("journal",), route="view",
                view="visits_by_year", params={"person": "Ada", "year": 2025})
    return Label(**{**base, **over})


def _plan(level="high", route="view", view="visits_by_year", person="Ada", year=2025, selected=("journal",),
          latency=12.0):
    return {
        "domains": {"selected": list(selected)},
        "route": {"id": route},
        "view_match": {"name": view, "params": {
            "person": {"type": "entity", "chosen": {"name": person}, "twins": False, "candidates": []},
            "year": {"type": "int", "value": year},
        }} if route == "view" else None,
        "confidence": {"level": level},
        "classifier": {"latency_ms": latency, "fell_back": False},
    }


def test_outcomes():
    assert replay.score(_label(), _plan())["outcome"] == "correct"
    wrong = replay.score(_label(), _plan(year=2024))
    assert (wrong["outcome"], wrong["mismatches"]) == ("wrong", ["param:year"])
    assert replay.score(_label(), _plan(level="low"))["outcome"] == "abstained"
    assert replay.score(_label(expect="low"), _plan(level="low"))["outcome"] == "correct_abstain"
    assert replay.score(_label(expect="low"), _plan())["mismatches"] == ["confidence"]


def test_a_parent_domain_covers_its_children():
    label = _label(domains=("bank.policy", "bank.cases"), route="search", view=None, params={})
    assert replay.score(label, _plan(route="search", view=None, selected=("bank",)))["outcome"] == "correct"
    assert "domains" in replay.score(label, _plan(route="search", view=None, selected=("journal",)))["mismatches"]


def test_twins_must_all_be_acceptable_names():
    label = _label(params={"person": ["Ada", "Ada"]})
    plan = _plan()
    plan["view_match"]["params"]["person"] = {"type": "entity", "chosen": None, "twins": True,
                                              "candidates": [{"name": "Ada"}, {"name": "ada"}]}
    assert replay.score(label, plan)["outcome"] == "correct"


def test_replay_finds_unstable_plans():
    plans = iter([_plan(), _plan(year=2024)])
    report = replay.replay([_label()], "lexical", repeat=2, plan_fn=lambda q, d, classifier: next(plans))
    assert report["unstable"] == ["L001"] and report["outcomes"]["correct"] == 1


def test_stable_plans_are_not_flagged():
    report = replay.replay([_label()], "lexical", repeat=3, plan_fn=lambda q, d, classifier: _plan())
    assert report["unstable"] == [] and report["gate"]["repeat"] == 3


def test_an_error_is_its_own_outcome():
    def boom(question, domains, classifier="lexical"):
        raise RuntimeError("neo4j down")

    report = replay.replay([_label()], "lexical", plan_fn=boom)
    assert report["outcomes"]["error"] == 1 and report["errors"][0]["error"] == "neo4j down"


def test_the_gate():
    report = replay.summarise([
        {"id": "L1", "outcome": "correct", "mismatches": [], "expected_route": "view", "latency_ms": 5.0},
        {"id": "L2", "outcome": "wrong", "mismatches": ["route"], "expected_route": "search", "latency_ms": 9.0},
    ], unstable=[], classifier="lexical", repeat=4)
    assert report["gate"] == {"view_wrong": 0, "wrong_rate": 0.5, "repeat": 4, "passed": False}
    assert report["latency_ms"] == {"p50": 9.0, "p95": 9.0}


def test_detail_goes_to_the_logs_dir(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    path = replay.write_detail({"classifier": "lexical", "results": []})
    assert path.parent == tmp_path and json.loads(path.read_text())["classifier"] == "lexical"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_replay.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Write `artmind/query_plan/replay.py`**

```python
"""Offline replay of `query plan` over the labelled set (spec 2026-10-22 §4.6).

No agent: each label's question is planned (against the live graph) and the plan
is scored against the label. `wrong` (a confident plan that is wrong) is the
number to drive to zero; `abstained` (a low-confidence plan) is reported
separately, since the agent then falls back to the routing table.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

OUTCOMES = ("correct", "abstained", "correct_abstain", "wrong", "error")
WRONG_RATE_GATE = 0.10
REPEAT_GATE = 4


def _names(value) -> set[str]:
    return {str(v).casefold() for v in (value if isinstance(value, list) else [value])}


def _domains_match(selected: list[str], expected) -> bool:
    if set(selected) == set(expected):
        return True
    return len(selected) == 1 and all(d == selected[0] or d.startswith(selected[0] + ".") for d in expected)


def _param_wrong(expected, entry: dict | None) -> bool:
    if entry is None:
        return True
    if entry.get("type") == "entity":
        refs = entry.get("candidates") if entry.get("twins") else ([entry["chosen"]] if entry.get("chosen") else [])
        got = {str(r.get("name") or "").casefold() for r in refs}
        return not got or not got <= _names(expected)
    return entry.get("value") != expected


def score(label, plan: dict) -> dict:
    mismatches = []
    if not _domains_match(plan["domains"]["selected"], label.domains):
        mismatches.append("domains")
    if plan["route"]["id"] != label.route:
        mismatches.append("route")
    got_view = (plan.get("view_match") or {}).get("name") if plan["route"]["id"] == "view" else None
    if got_view != label.view:
        mismatches.append("view")
    if label.view and got_view == label.view:
        params = plan["view_match"]["params"]
        mismatches += [f"param:{n}" for n, expected in label.params.items() if _param_wrong(expected, params.get(n))]
    level = plan["confidence"]["level"]
    if level == "low":
        outcome = "correct_abstain" if label.expect == "low" else "abstained"
    elif label.expect == "low":
        outcome, mismatches = "wrong", [*mismatches, "confidence"]
    else:
        outcome = "wrong" if mismatches else "correct"
    return {"id": label.id, "outcome": outcome, "mismatches": mismatches, "route": plan["route"]["id"],
            "expected_route": label.route, "view": got_view, "level": level,
            "latency_ms": plan["classifier"]["latency_ms"], "fell_back": plan["classifier"]["fell_back"]}


def _stable(plan: dict) -> str:
    copy = json.loads(json.dumps(plan, default=str))
    copy.get("classifier", {}).pop("latency_ms", None)
    return json.dumps(copy, sort_keys=True)


def _percentile(values: list[float], q: float) -> float | None:
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


def summarise(results: list[dict], unstable: list[str], classifier: str, repeat: int = 1) -> dict:
    counts = {o: sum(1 for r in results if r["outcome"] == o) for o in OUTCOMES}
    per_route: dict[str, dict[str, int]] = {}
    for r in results:
        per_route.setdefault(r["expected_route"], {o: 0 for o in OUTCOMES})[r["outcome"]] += 1
    latencies = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    view_wrong = sum(1 for r in results if r["expected_route"] == "view" and r["outcome"] == "wrong")
    wrong_rate = counts["wrong"] / len(results) if results else None
    passed = (view_wrong == 0 and wrong_rate is not None and wrong_rate < WRONG_RATE_GATE
              and not unstable and repeat >= REPEAT_GATE)
    return {
        "classifier": classifier,
        "labels": len(results),
        "outcomes": counts,
        "per_route": dict(sorted(per_route.items())),
        "wrong": [{"id": r["id"], "mismatches": r["mismatches"]} for r in results if r["outcome"] == "wrong"],
        "errors": [{"id": r["id"], "error": r.get("error")} for r in results if r["outcome"] == "error"],
        "unstable": unstable,
        "latency_ms": {"p50": _percentile(latencies, 0.5), "p95": _percentile(latencies, 0.95)},
        "gate": {"view_wrong": view_wrong, "wrong_rate": wrong_rate, "repeat": repeat, "passed": passed},
        "results": results,
    }


def replay(labels, classifier: str, repeat: int = 1, *, given_domains: bool = False, plan_fn=None) -> dict:
    if plan_fn is None:
        from artmind.query_plan import planner

        plan_fn = planner.plan
    results: list[dict] = []
    unstable: list[str] = []
    for label in labels:
        domains = list(label.domains) if given_domains else None
        try:
            runs = [plan_fn(label.question, domains, classifier=classifier) for _ in range(max(1, repeat))]
        except Exception as exc:  # one label failing must not stop the replay (spec §7)
            results.append({"id": label.id, "outcome": "error", "mismatches": [], "error": str(exc),
                            "route": None, "expected_route": label.route, "view": None, "level": None,
                            "latency_ms": None, "fell_back": None})
            continue
        if len({_stable(r) for r in runs}) > 1:
            unstable.append(label.id)
        results.append(score(label, runs[0]))
    return summarise(results, unstable, classifier, repeat)


def write_detail(report: dict) -> Path:
    """Per-label detail into <ARTMIND_HOME>/logs/ (gitignored, private), never the checkout."""
    import paths

    path = Path(paths.LOGS_DIR) / f"plan-replay-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return path
```

`replay` calls `plan_fn(question, domains, classifier=...)`, the same shape as `planner.plan`, so the tests pass lambdas of that shape.

- [ ] **Step 4: Add `plan replay` to `artmind/cli.py`**

After `plan_labels_validate`, add:

```python
@plan_group.command("replay")
@click.option("--file", "file_path", type=click.Path(path_type=Path), default=None,
              help="Labels file. Default: <vault>/benchmarking/plan_labels.yaml")
@click.option("--classifier", type=click.Choice(["lexical", "decision", "both"]), default="lexical", show_default=True)
@click.option("--repeat", type=click.IntRange(1, 10), default=1, show_default=True,
              help="Plan each label N times; labels whose plans differ are listed as unstable (the gate needs 4)")
@click.option("--givenDomains", "given_domains", is_flag=True, help="Pass each label's domains instead of letting the plan choose")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def plan_replay(file_path: Path | None, classifier: str, repeat: int, given_domains: bool, compact: bool) -> None:
    """Replay `query plan` over the labelled set and score it. Needs Neo4j; runs no agent.

    Prints the summary; per-label detail goes to <ARTMIND_HOME>/logs/plan-replay-*.json.
    """
    from artmind.query_plan import labels as plan_labels
    from artmind.query_plan import replay as plan_replay_mod
    from artmind.query_plan.classify import select_classifier

    path = _labels_path(file_path)
    try:
        loaded = plan_labels.load_labels(path)
    except plan_labels.LabelError as exc:
        raise click.ClickException("invalid labels file; run `artmind plan labels validate`: " + str(exc)) from exc
    backends = ["lexical", "decision"] if classifier == "both" else [classifier]
    if "decision" in backends:
        try:
            select_classifier("decision", "auto", None)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
    reports = {}
    for backend in backends:
        report = plan_replay_mod.replay(loaded, backend, repeat, given_domains=given_domains)
        detail = plan_replay_mod.write_detail(report)
        reports[backend] = {**{k: v for k, v in report.items() if k != "results"}, "detail_file": str(detail)}
    _echo_json({"command": "plan replay", "file": str(path), "repeat": repeat, "reports": reports}, compact)
```

In `COMMAND_GROUPS`, change the `"artmind plan"` entry to `{"name": "Plan", "commands": ["config", "labels", "replay"]}`.

- [ ] **Step 5: Add the justfile recipe**

In `justfile`, immediately before the line `# ── artmind traces ───…`, add:

```just
# ── artmind plan ─────────────────────────────────────────────────────────────

# score `query plan` against a vault's labelled set, offline, 4 repeats for the determinism gate  (usage: just plan-replay ~/artmind_vaults/<name> [lexical|decision|both])
plan-replay vault classifier="lexical":
    cd {{quote(vault)}} && ARTMIND_NO_PROXY=1 artmind plan replay --classifier {{ classifier }} --repeat 4

```

- [ ] **Step 6: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_replay.py test/test_query_plan_labels.py test/test_cli_guide.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/query_plan/replay.py artmind/cli.py justfile test/test_query_plan_replay.py
git commit -m "feat(plan): offline replay with the v1 gate, artmind plan replay, just plan-replay

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Phase C/D checkpoint:** `uv run --group dev pytest test/test_query_plan_*.py test/test_cli_guide.py -q` → all PASS.

---

# Phase E — Shadow mode

### Task 19: A background plan for every traced turn

**Files:**
- Create: `artmind/query_plan/shadow.py`
- Modify: `artmind/traces/store.py` (`count_session_turns`, `list_events_of_type`)
- Modify: `artmind/traces/recorder.py` (`__init__`, `start`, `finish`)
- Modify: `artmind/webui/app.py` (the `TraceRecorder.start` call), `artmind/benchmark.py` (the `TraceRecorder.start` call)
- Test: `test/test_query_plan_shadow.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_query_plan_shadow.py`:

```python
"""Shadow mode (spec 2026-10-22 §6): a plan beside each traced turn, never seen by the agent."""

import json
import time

import pytest

from artmind.query_plan import planner, shadow
from artmind.traces import store
from artmind.traces.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr("artmind.traces.recorder.graph_state", lambda domains: {})
    monkeypatch.delenv("ARTMIND_PLAN_SHADOW", raising=False)


def _shadow_events(trace_id):
    return [e for e in store.list_events(trace_id) if e["type"] == "plan_shadow"]


def test_a_finished_turn_carries_its_shadow_plan(monkeypatch):
    seen = {}

    def fake_plan(question, domains=None):
        seen.update(question=question, domains=domains)
        return {"confidence": {"level": "high"}, "steps": [{"n": 1, "cmd": "artmind query views run v --compact"}]}

    monkeypatch.setattr(planner, "plan", fake_plan)
    rec = TraceRecorder.start(source="admin", question="Who leads X?", session_id="s1",
                              shadow_question="Who leads X?", shadow_domains=["b"])
    rec.finish()
    (event,) = _shadow_events(rec.trace_id)
    payload = json.loads(event["payload"])
    assert payload["status"] == "ok" and payload["followup"] is False
    assert payload["plan"]["confidence"]["level"] == "high"
    assert seen == {"question": "Who leads X?", "domains": ["b"]}


def test_a_second_turn_in_a_session_is_a_followup(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    TraceRecorder.start(source="admin", question="first", session_id="s1").finish()
    rec = TraceRecorder.start(source="admin", question="yes", session_id="s1", shadow_question="yes")
    rec.finish()
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["followup"] is True


def test_a_failing_plan_is_recorded_as_an_error(monkeypatch):
    def boom(question, domains=None):
        raise RuntimeError("neo4j down")

    monkeypatch.setattr(planner, "plan", boom)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    payload = json.loads(_shadow_events(rec.trace_id)[0]["payload"])
    assert payload["status"] == "error" and "neo4j down" in payload["error"]


def test_a_slow_plan_times_out(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: time.sleep(1.0) or {})
    monkeypatch.setattr(shadow, "WAIT_S", 0.05)
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    assert json.loads(_shadow_events(rec.trace_id)[0]["payload"])["status"] == "timeout"


def test_the_kill_switch(monkeypatch):
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q", shadow_question="q")
    rec.finish()
    assert _shadow_events(rec.trace_id) == []


def test_no_shadow_question_means_no_shadow(monkeypatch):
    monkeypatch.setattr(planner, "plan", lambda question, domains=None: {})
    rec = TraceRecorder.start(source="admin", question="q")
    rec.finish()
    assert _shadow_events(rec.trace_id) == []


def test_list_events_of_type_batches_by_trace():
    a = TraceRecorder.start(source="admin", question="a")
    store.insert_events(a.trace_id, [{"seq": 0, "ts": 1.0, "type": "plan_shadow", "payload": "{}"}])
    b = TraceRecorder.start(source="admin", question="b")
    store.insert_events(b.trace_id, [{"seq": 0, "ts": 1.0, "type": "text", "payload": "x"}])
    found = store.list_events_of_type([a.trace_id, b.trace_id], "plan_shadow")
    assert list(found) == [a.trace_id] and found[a.trace_id][0]["payload"] == "{}"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_query_plan_shadow.py -v`
Expected: FAIL (`unexpected keyword argument 'shadow_question'`; no `shadow` module).

- [ ] **Step 3: Write `artmind/query_plan/shadow.py`**

```python
"""Shadow mode: a `query plan` for every traced turn, stored beside its trace (spec 2026-10-22 §6).

The plan runs in a worker thread alongside the agent, from the user's own text,
and is recorded as a `plan_shadow` trace event when the turn finishes. The agent
never sees it. It never delays or fails a turn: every failure becomes
`status: error`. Kill switch: ARTMIND_PLAN_SHADOW=0.
"""

from __future__ import annotations

import os
import threading

from loguru import logger

WAIT_S = 10.0


def enabled() -> bool:
    return (os.environ.get("ARTMIND_PLAN_SHADOW") or "1").strip().casefold() not in ("0", "false", "no", "off")


class ShadowPlan:
    def __init__(self, question: str, domains: list[str] | None, followup: bool):
        self.followup = followup
        self._plan: dict | None = None
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, args=(question, domains), name="plan-shadow", daemon=True)
        self._thread.start()

    def _run(self, question: str, domains: list[str] | None) -> None:
        try:
            from artmind.query_plan import planner

            self._plan = planner.plan(question, domains)
        except Exception as exc:
            logger.debug("shadow plan failed: {}", exc)
            self._error = f"{type(exc).__name__}: {exc}"

    def result(self, timeout: float | None = None) -> dict:
        self._thread.join(WAIT_S if timeout is None else timeout)
        if self._thread.is_alive():
            return {"status": "timeout", "followup": self.followup, "plan": None, "error": None}
        if self._error is not None:
            return {"status": "error", "followup": self.followup, "plan": None, "error": self._error}
        return {"status": "ok", "followup": self.followup, "plan": self._plan, "error": None}


def start(question: str | None, domains: list[str] | None = None, followup: bool = False) -> ShadowPlan | None:
    if not enabled() or not (question or "").strip():
        return None
    try:
        return ShadowPlan(question, domains, followup)
    except Exception as exc:  # a thread that cannot start must not break the turn
        logger.debug("shadow plan not started: {}", exc)
        return None
```

- [ ] **Step 4: Add the store helpers to `artmind/traces/store.py`**

After `list_events`, add:

```python
def count_session_turns(session_id: str) -> int:
    conn = _connect()
    try:
        return conn.execute("SELECT COUNT(*) FROM trace_turns WHERE session_id = ?", (session_id,)).fetchone()[0]
    finally:
        conn.close()


def list_events_of_type(trace_ids: list[str], type_: str) -> dict[str, list[dict]]:
    """`{trace_id: [events of type_]}` in one pass, chunked like list_spans_for."""
    if not trace_ids:
        return {}
    chunk_size = 500
    out: dict[str, list[dict]] = {}
    conn = _connect()
    try:
        for i in range(0, len(trace_ids), chunk_size):
            chunk = trace_ids[i : i + chunk_size]
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT * FROM trace_events WHERE type = ? AND trace_id IN ({marks}) ORDER BY trace_id, seq",
                (type_, *chunk),
            ):
                out.setdefault(row["trace_id"], []).append(dict(row))
    finally:
        conn.close()
    return out
```

- [ ] **Step 5: Wire the recorder (`artmind/traces/recorder.py`)**

Add `from artmind.query_plan import shadow` to the imports. In `__init__`, add `self._shadow = None` as its last line. Replace `start` with:

```python
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
```

In `finish`, immediately after `self._flush("text")`, add:

```python
            if self._shadow is not None:
                self._add("plan_shadow", self._shadow.result(), name="query plan")
```

- [ ] **Step 6: Pass the shadow question from the two traced surfaces**

In `artmind/webui/app.py`, in the `functools.partial(TraceRecorder.start, ...)` call, add the argument `shadow_question=payload.prompt,` (after `context_path=context_path,`). `payload.prompt` is the user's own text: the replayed conversation is prepended only to the local `prompt`.

In `artmind/benchmark.py`, in the `asyncio.to_thread(TraceRecorder.start, ...)` call, add:

```python
            shadow_question=question["question"],
            shadow_domains=[run["domain_hint"]] if run["domain_hint"] else None,
```

- [ ] **Step 7: Run the tests**

Run: `uv run --group dev pytest test/test_query_plan_shadow.py test/test_traces_recorder.py test/test_traces_app.py test/test_benchmark.py test/test_webui_benchmark_api.py -v`
Expected: all PASS. The older recorder and app tests pass no `shadow_question`, so they see no shadow.

- [ ] **Step 8: Commit**

```bash
git add artmind/query_plan/shadow.py artmind/traces/store.py artmind/traces/recorder.py artmind/webui/app.py artmind/benchmark.py test/test_query_plan_shadow.py
git commit -m "feat(plan): shadow mode, a background plan recorded beside each traced turn

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Reading shadow plans back — `traces show`, `stats`, `triage`

**Files:**
- Modify: `artmind/traces/analysis.py`
- Test: `test/test_traces_plan_shadow.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `test/test_traces_plan_shadow.py`:

```python
"""Shadow plans in traces show/stats/triage (spec 2026-10-22 §6)."""

import json

import pytest

from artmind.traces import analysis, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _shadow(level="high", cmd="artmind query views run visits --domain b --compact", followup=False):
    return {"status": "ok", "followup": followup,
            "plan": {"confidence": {"level": level}, "steps": [{"n": 1, "cmd": cmd}] if level == "high" else [],
                     "suggested_steps": [] if level == "high" else [{"n": 1, "cmd": cmd}]}}


def _span(command, name=None):
    args = {"domain": ["b"], **({"name": name} if name else {})}
    return {"command": command, "args": args, "exit_code": 0, "rows": 1}


def test_shadow_plan_reads_the_event_and_survives_a_truncated_payload():
    assert analysis.shadow_plan([{"type": "plan_shadow", "payload": json.dumps(_shadow())}])["status"] == "ok"
    assert analysis.shadow_plan([{"type": "plan_shadow", "payload": "{\"status\": \"ok\"…[truncated 9 bytes]"}]) == {
        "status": "truncated", "followup": None, "plan": None, "error": None}
    assert analysis.shadow_plan([{"type": "text", "payload": "x"}]) is None


def test_agreement_compares_the_first_step_with_the_agents_spans():
    assert analysis.agreement(_shadow(), [_span("query views run", "visits")]) is True
    assert analysis.agreement(_shadow(), [_span("query views run", "other")]) is False
    assert analysis.agreement(_shadow(cmd="artmind query graph pattern7 --domain b --compact"),
                              [_span("query graph pattern7")]) is True
    assert analysis.agreement({"status": "ok", "plan": {"steps": [], "suggested_steps": []}}, []) is None


def test_a_confident_plan_the_agent_ignored_is_a_triage_signal():
    sig = analysis.signals({"turns": 1}, [_span("query vector-text")], None, {"status": "n/a"}, shadow=_shadow())
    assert sig["plan_disagreed_high"] == 2
    sig = analysis.signals({"turns": 1}, [_span("query vector-text")], None, {"status": "n/a"},
                           shadow=_shadow(level="low"))
    assert "plan_disagreed_high" not in sig


def _turn(trace_id, session, followup_plan, agent_command, agent_name=None):
    store.insert_turn(trace_id, source="admin", question="q", session_id=session, started_at=1_700_000_000.0)
    store.update_turn(trace_id, status="done", domains=json.dumps(["b"]), completed_at=1_700_000_001.0)
    store.insert_span({"span_id": f"s-{trace_id}", "trace_id": trace_id, "command": agent_command,
                       "args": json.dumps({"domain": ["b"], **({"name": agent_name} if agent_name else {})}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": "{}", "started_at": 1_700_000_000.5}, [])
    store.insert_events(trace_id, [{"seq": 0, "ts": 1.0, "type": "plan_shadow",
                                    "payload": json.dumps(followup_plan)}])


def test_stats_report_shadow_agreement():
    _turn("t1", "s1", _shadow(), "query views run", "visits")
    _turn("t2", "s2", _shadow(), "query vector-text")
    _turn("t3", "s3", _shadow(followup=True), "query views run", "visits")
    (row,) = [r for r in analysis.stats(by="all") if r["domain"] == "b"]
    assert row["plan_shadow"] == {"plans": 3, "high_share": 1.0, "agreement_first": 0.5,
                                  "agreement_followup": 1.0, "agreement_by_rating": {"unrated": 2 / 3}}


def test_show_includes_the_shadow_plan():
    _turn("t1", "s1", _shadow(), "query views run", "visits")
    assert analysis.show("t1")["plan_shadow"]["status"] == "ok"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_traces_plan_shadow.py -v`
Expected: FAIL (`no attribute 'shadow_plan'`; `signals()` has no `shadow` argument).

- [ ] **Step 3: Implement in `artmind/traces/analysis.py`**

Add `import json` and `import shlex` to the imports. After the imports, add:

```python
def shadow_plan(events: list[dict]) -> dict | None:
    """The turn's `plan_shadow` payload (spec 2026-10-22 §6), or None. A payload
    cut by store.cap is no longer JSON: reported as `truncated`."""
    for event in events:
        if event.get("type") == "plan_shadow":
            try:
                value = json.loads(event.get("payload") or "null")
            except ValueError:
                return {"status": "truncated", "followup": None, "plan": None, "error": None}
            return value if isinstance(value, dict) else None
    return None


def _command_key(cmd: str) -> str | None:
    """`artmind query views run v --domain b` -> `query views run v` (words before the first option)."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if not tokens or tokens[0] != "artmind":
        return None
    words = []
    for token in tokens[1:]:
        if token.startswith("-"):
            break
        words.append(token)
    return " ".join(words) or None


def _span_key(span: dict) -> str:
    command = span.get("command") or ""
    args = span.get("args")
    if command == "query views run" and isinstance(args, dict) and args.get("name"):
        return f"{command} {args['name']}"
    return command


def agreement(shadow: dict | None, spans: list[dict]) -> bool | None:
    """Did the agent run the shadow plan's first step? None when the plan has no step."""
    plan = (shadow or {}).get("plan") or {}
    steps = plan.get("steps") or plan.get("suggested_steps") or []
    key = _command_key(steps[0].get("cmd") or "") if steps else None
    if key is None:
        return None
    return key in {_span_key(s) for s in spans if s.get("command") != "query plan"}


def _rate(values: list) -> float | None:
    known = [v for v in values if v is not None]
    return sum(1 for v in known if v) / len(known) if known else None


def _shadow_rates(turns: list[dict], shadows: dict[str, list[dict]], spans_by_trace: dict[str, list[dict]]) -> dict:
    rows = []
    for turn in turns:
        payload = shadow_plan(shadows.get(turn["trace_id"], []))
        if payload and payload.get("status") == "ok":
            rows.append((turn, payload, agreement(payload, spans_by_trace.get(turn["trace_id"], []))))
    if not rows:
        return {"plans": 0}
    by_rating: dict[str, list] = {}
    for turn, _, agreed in rows:
        by_rating.setdefault(turn.get("rating") or "unrated", []).append(agreed)
    high = sum(1 for _, p, _ in rows if ((p.get("plan") or {}).get("confidence") or {}).get("level") == "high")
    return {
        "plans": len(rows),
        "high_share": high / len(rows),
        "agreement_first": _rate([a for _, p, a in rows if not p.get("followup")]),
        "agreement_followup": _rate([a for _, p, a in rows if p.get("followup")]),
        "agreement_by_rating": {k: _rate(v) for k, v in sorted(by_rating.items())},
    }
```

Change the `signals` signature to `def signals(turn: dict, spans: list[dict], turns_p90: float | None, views: dict | None = None, shadow: dict | None = None) -> dict[str, int]:` and, before `return out`, add:

```python
    level = (((shadow or {}).get("plan") or {}).get("confidence") or {}).get("level")
    if (shadow or {}).get("status") == "ok" and level == "high" and agreement(shadow, spans) is False:
        out["plan_disagreed_high"] = 2
```

In `triage`, after `checks = views_checks(turns, spans_by_trace)`, add `shadows = store.list_events_of_type([t["trace_id"] for t in turns], "plan_shadow")`, and change the `signals(...)` call to pass `shadow=shadow_plan(shadows.get(turn["trace_id"], []))`.

In `stats`, replace

```python
    checks = views_checks(done, store.list_spans_for([t["trace_id"] for t in done]))
```

with

```python
    ids = [t["trace_id"] for t in done]
    spans_by_trace = store.list_spans_for(ids)
    checks = views_checks(done, spans_by_trace)
    shadows = store.list_events_of_type(ids, "plan_shadow")
```

and add `"plan_shadow": _shadow_rates(turns, shadows, spans_by_trace),` as the last entry of the per-bucket dict (after `**_views_rates(turns, checks),`).

In `show`, add `"plan_shadow": shadow_plan(events),` to the returned dict (after `"feedback": ...`).

- [ ] **Step 4: Run the trace tests**

Run: `uv run --group dev pytest test/test_traces_plan_shadow.py test/test_traces_analysis.py test/test_traces_cli.py test/test_traces_views_check.py -v`
Expected: all PASS. If an older `test_traces_cli.py` test compares a whole `stats` row, add `"plan_shadow": {"plans": 0}` to its expected dict.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/analysis.py test/test_traces_plan_shadow.py test/test_traces_cli.py
git commit -m "feat(traces): shadow plans in traces show, stats agreement and a triage signal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Phase E checkpoint:** `uv run --group dev pytest test/test_query_plan_*.py test/test_traces_*.py test/test_benchmark.py test/test_cli_guide.py -q` → all PASS.

---

# Phase F — Docs, live verification and the gate

### Task 21: Docs that change with the code (CLAUDE.md §4)

**Files:**
- Modify: `CLAUDE.md`, `docs/INSTALL.md`, `artmind/env.example`

- [ ] **Step 1: `CLAUDE.md` repo layout**

In the repo-layout table, after the `artmind/kg_views/` row, add:

```markdown
| `artmind/query_plan/` | `artmind query plan` (spec `docs/superpowers/specs/2026-10-22-query-plan-design.md`): deterministic routing in code. `routes.yaml` (the one routing table; tests check every template against the Click tree), `candidates` (mentions, domain options incl. family parents, entity resolution by name evidence, twins, document names, store facts), `classify/` (`lexical` always; `jev` = a decision model over the Jev API, `ARTMIND_DECISION_URL`/`_MODEL`, with lexical fallback and a circuit breaker), `fill` (scalar view params), `planner` (stages → payload; `--execute` runs one high-confidence view), `config` (`domains/plan/<domain>.yaml`: narrator, threshold; never seeded), `labels` + `replay` (`<vault>/benchmarking/plan_labels.yaml`, private; `artmind plan replay`), `shadow` (a plan beside every admin/benchmark trace as a `plan_shadow` event; `ARTMIND_PLAN_SHADOW=0` turns it off). |
```

- [ ] **Step 2: `CLAUDE.md` command routing quick reference**

In "Command routing quick reference", add `plan` to the `artmind query <cmd>` bullet's list (first), and add a bullet:

```markdown
- `artmind plan <cmd>` — `config check`, `labels validate`, `replay` (not proxied; labels and replay output hold real questions and stay in the vault)
```

- [ ] **Step 3: `docs/INSTALL.md`**

At the end of the `## Run` section (before `### Exposure of the web UIs`), add:

```markdown
### `query plan` settings

`artmind query plan` routes a question in code. With no settings it uses lexical
rules. To use a local decision model (Jev API), install Ollama ≥ 0.35, pull a model
yourself (`ollama pull nimble`; artmind never downloads models), and set in
`~/.artmind/config.env`:

    ARTMIND_DECISION_URL=http://localhost:11434/v1/systemone
    ARTMIND_DECISION_MODEL=nimble
    # ARTMIND_DECISION_TIMEOUT=5        # seconds per call
    # ARTMIND_DECISION_API_KEY=...      # hosted endpoints only

A hosted endpoint (for example Clef on Workers AI) receives the question and the
candidate entity names; a local one keeps them on the machine. Shadow mode (a plan
recorded beside every admin-console and benchmark trace) is on by default;
`ARTMIND_PLAN_SHADOW=0` turns it off.
```

- [ ] **Step 4: `artmind/env.example`**

After the `ARTMIND_SYNTHESIZE_TIMEOUT` block, add:

```
# ── query plan (spec 2026-10-22) ─────────────────────────────────────────────
# A decision model over the Jev API for `artmind query plan`; unset = lexical rules.
# Pull the model yourself first (e.g. `ollama pull nimble`).
# ARTMIND_DECISION_URL=http://localhost:11434/v1/systemone
# ARTMIND_DECISION_MODEL=nimble
# ARTMIND_DECISION_TIMEOUT=5
# Shadow plans beside admin-console/benchmark traces; 0 turns them off.
# ARTMIND_PLAN_SHADOW=1
```

- [ ] **Step 5: Check and commit**

Run: `just dev-cli-help | grep -n "plan"` → shows `query plan` and the `plan` group.

```bash
git add CLAUDE.md docs/INSTALL.md artmind/env.example
git commit -m "docs: query plan, the plan group and their settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 22: Live verification, the labelled set and the replay gate

This task touches real vaults and real questions. **Nothing from it goes into the checkout.** All output stays in the terminal, the scratchpad, or the vault.

- [ ] **Step 1: Full hermetic suite**

Run: `uv run --group dev pytest test/ -q -x` (allow it to run in the background; if the vault-radar hook stalls it, run `uv run --group dev pytest test/test_query_plan_*.py test/test_traces_*.py test/test_graph_query.py test/test_vector_query.py test/test_llm_providers.py test/test_text2cypher.py test/test_text2sql.py test/test_entity_document_history.py test/test_cli_guide.py -q`).
Expected: all PASS.

- [ ] **Step 2: Reinstall and restart daemons**

```bash
just dev-install
```

Then start `serve` again from inside a vault if it was running (`cd ~/artmind_vaults/brain01 && artmind serve`), and restart `admin-ui`. A stale daemon answers proxied `query` calls with the old code (CLAUDE.md §1).

- [ ] **Step 3: Honest-command probes (banking_corpus)**

```bash
cd ~/artmind_vaults/banking_corpus
ARTMIND_NO_PROXY=1 artmind query graph pattern10 --domain banking --documentName interest_rate_schedule_2026 --compact | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['rows']), d.get('history_matches'), d.get('next'))"
ARTMIND_NO_PROXY=1 artmind query document-history --domain banking --documentName policy_customer_identification --compact | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['rows']), d.get('matched_name'), d.get('closest'))"
ARTMIND_NO_PROXY=1 artmind query graph pattern7 --domain banking --searchTerm complaint --compact | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['rows_total'], d['truncated'], d.get('next'), d['rows'][0].get('documents'))"
```

Expected:
- `pattern10` on a retired-only name: 0 rows, a `history_matches` list and `next`.
- `document-history` without the suffix: rows plus `matched_name`, or `closest`.
- `pattern7`: `rows_total`, `truncated: true` with a `next`, and `documents` on each row.

(Use the public sample-corpus file names only; if a name differs, take one from `artmind query domains-overview`.)

- [ ] **Step 4: The P26 determinism probe (brain01)**

```bash
cd ~/artmind_vaults/brain01
for i in 1 2 3 4; do ARTMIND_NO_PROXY=1 artmind query plan --domain personal_journal --compact "<the P26 question, typed by the user>" | python3 -c "import json,sys,hashlib; d=json.load(sys.stdin); d['classifier'].pop('latency_ms'); print(hashlib.sha256(json.dumps(d,sort_keys=True).encode()).hexdigest()[:12], d['route']['id'], d['confidence']['level'], len(d['steps']))"; done
```

Expected: four identical hashes; route `view`; level `high`; two steps if the place still has two twin nodes. **Ask the user to type the question**; do not copy it from the dossier or traces into a file. For the narrator mapping to work, first create `~/artmind_vaults/brain01/.artmind/domains/plan/personal_journal.yaml` with `narrator: {entity_class: PERSON, name: Diarist}` (the public schema's narrator name) and run `ARTMIND_NO_PROXY=1 artmind plan config check` → `narrator_found: true`.

- [ ] **Step 5: Decision-model probe (operator step first)**

Ask the user to pull the models (`ollama pull nimble`, `ollama pull tev1`); do not run the pull yourself. Then verify the real response shape before trusting the parser:

```bash
curl -s http://localhost:11434/v1/systemone -d '{"model":"nimble","state":{"question":"How often did the team meet?"},"questions":{"route":{"type":"choice","instructions":"What kind of question is this?","criteria":{"aggregate":"A count or frequency","search":"Anything else"}}}}'
```

Expected: `answers.route` with per-option `probabilities` and a `confidence`. If the selected option's key is not `choice`, `answer` or `label`, add it to the tuple in `jev._parse` (one line) with a test, and commit. Then repeat Step 4 with `--classifier decision` and with `ARTMIND_DECISION_URL`/`ARTMIND_DECISION_MODEL` set in the shell.

- [ ] **Step 6: Draft the labelled sets (with the user)**

In a session run from the checkout with `ARTMIND_VAULT=<vault>`, draft `<vault>/benchmarking/plan_labels.yaml` for `brain01` and `banking_corpus`:
- Use `docs/query-routing-study/04-scenarios.md` for shapes and ideal routes, the 36 benchmark questions by `benchmark: Qnn`, and the recorded brain01 turns (`source: trace:<id>`).
- Aim for at least 40 labels across both vaults. Use `expect: low` for S22-type inference questions.
- Show each drafted label to the user and write it only once the user confirms it.
- Then run `artmind plan labels validate --checkEntities` inside each vault.

Labels never enter the checkout.

- [ ] **Step 7: Replay gate**

```bash
just plan-replay ~/artmind_vaults/brain01 both
just plan-replay ~/artmind_vaults/banking_corpus both
```

Expected per backend: `gate.view_wrong == 0`, `gate.wrong_rate < 0.10`, `unstable == []`, `gate.passed: true` for at least one backend. Report to the user, without question text:
- the outcome counts and gate fields per backend;
- the per-route table;
- latency p50/p95.

Recommend the default classifier: fewest `wrong`, then fewest `abstained`. If no backend passes, list the wrong labels' `mismatches` and stop; the next step is a route or trigger change in `routes.yaml`, decided with the user.

- [ ] **Step 8: Shadow mode on**

With the admin UI restarted (Step 2), shadow mode is on by default. After a week of normal use the user runs `artmind traces stats --by all --compact` and `artmind traces triage` inside the vault; `plan_shadow` and `plan_disagreed_high` feed the skill wire-in spec (study W3), which is out of scope here.

---

## Self-review notes (for the executor)

- Every spec section maps to a task:

  | Spec section | Task |
  |---|---|
  | §4.1 | 15 |
  | §4.2 | 12–14 |
  | §4.3 | 9 |
  | §4.4 | 11, 16 |
  | §4.5 | 10, 17 |
  | §4.6 | 17–18 |
  | §5.1 | 1–2 |
  | §5.2 | 3 |
  | §5.3 | 4 |
  | §5.4 | 5–7 |
  | §6 | 19–20 |
  | §7 | 14, 16, 19 (and the Step 4 failure cases throughout) |
  | §8 | every task's tests, plus Task 22 |
  | §9 | Task 21, Task 22 |
  | §10 (`noul` check) | `bool` params use defaults in Task 13; the response-shape check is in Task 22 Step 5 |

- Names used across tasks:
  - `routes.load_routes`, `routes.render`
  - `lexical.Facts`, `lexical.answer`, `lexical.route_scores`, `lexical.view_scores`, `lexical.domain_scores`, `lexical.value_scores`, `lexical.triggered`, `lexical.has_date`
  - `candidates.find_mentions`, `candidates.keywords`, `candidates.domain_options`, `candidates.resolve_mention`, `candidates.narrator_candidates`, `candidates.twin_groups`, `candidates.document_names`, `candidates.match_document`, `candidates.store_facts`
  - `fill.fill_scalar`, `fill.combinations`
  - `planner.plan`, `planner.PlanError`, `planner._Calls`
  - `config.load_config`, `config.merge`, `config.config_file`, `config.PlanConfigError`, `config.DEFAULT_FIRST_PERSON`
  - `select_classifier(option, domain_choice, model_override, env=None)`
  - `labels.parse_labels`, `labels.load_labels`, `labels.check_entities`, `labels.default_path`
  - `replay.score`, `replay.replay`, `replay.summarise`, `replay.write_detail`
  - `shadow.start`, `shadow.WAIT_S`
  - `store.count_session_turns`, `store.list_events_of_type`
  - `analysis.shadow_plan`, `analysis.agreement`
  - `path.span_layer`, `path.is_dry_run`
