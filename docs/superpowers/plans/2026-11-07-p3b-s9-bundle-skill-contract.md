# P3b slice S9: the bundle skill and the contract switch, implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second agent contract next to today's.
- `artmind-query-bundle`, about 150 lines, calls `artmind query run` and reads the bundle.
- It is chosen per surface by `ARTMIND_AGENT_CONTRACT` (admin console) or by a benchmark run's own `contract` field.
- Every trace turn records which contract ran it.
- Chat-ui stays classic.

**Architecture:**
- `AgentProfile` gains `contract` (`classic` | `bundle`).
- A helper `with_contract(profile, contract)` returns the profile with its query skill (`artmind-query` vs `artmind-query-bundle`) and its system-prompt sentence about the query skill swapped. Nothing in the CLI, planner or executor reads the contract.
- The admin UI builds its profile once at start, from `ARTMIND_AGENT_CONTRACT` (default `classic`).
- The benchmark driver builds the benchmark profile per run, from `benchmark_runs.contract`.
- `trace_turns.contract` is written by `TraceRecorder.start`.
- The skill's routing table and command list are generated from `routes.yaml` and the Click tree, and a test fails when they are stale.

**Tech stack:** Python 3.14, Click, FastAPI, pytest (hermetic), uv; markdown skills.

**Spec:** `docs/superpowers/specs/2026-11-07-p3-executor-design.md` §8.1 (where the contract is chosen, D5), §8.2 (the skill), §8.3 (mode), §8.4 (answer contract), §8.5 (previous), §9.2 (the S10 gate this enables), §13 Q8/Q9. Decided with the user on 2026-11-07: the contract applies to the benchmark runs and the admin console; chat-ui stays classic until S13.

**Inputs from the S6 shadow runs** (handover, "S6 live gate passed"), now improved by the survey leg (`1035203`): `search` bundles held 0.79–0.96 of what the agent read; other routes 0–0.3 before the survey leg. The skill must keep every bundle on the reasoning path, and must say that a `search` bundle is usually close to complete while other routes need their gaps checked.

**Starting point:** branch `claude/p3-design-a9f633` at `34cf5a3` or later. Work in a worktree cut from it, never in that worktree itself (the user's installed `artmind` points at it).

---

## Before you start

- Read CLAUDE.md §2 "Skills reach the chat UI through the vault or the run folder":
  - a **new** skill reaches a vault only after `artmind init` runs inside it;
  - the checkout's `.claude/skills/` needs `just dev-refresh-skills`;
  - never run `artmind setup` or `init` from the checkout.
- Run tests with `uv run --group dev pytest <path> -q`, and the full suite at the end (`uv run --group dev pytest -q test/`, about 4 min).
- Privacy: test data is invented. The skill text names no vault content.
- Commit after every task with the given message plus `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the model you are). Do not push.

## Facts about the code you will need

- `artmind/webui/profiles.py`: `AgentProfile(name, skills, system_append, acp_mode, filesystem_access)`, a frozen dataclass. `QA_PROFILE` (skills `artmind-query`, `artmind-update`), `ADMIN_PROFILE` (six skills, with `artmind-query` first), `BENCHMARK_PROFILE` (`artmind-query` only) and `PROFILES`. Each `system_append` names `artmind-query` in its prose.
- `artmind/webui/agent.py` (~line 230) passes `skills=list(profile.skills)` and `system_prompt.append = profile.system_append` to the Claude SDK. The ACP backend gets `profile.system_append` as its preamble and `profile.acp_mode` as its opencode mode.
- `artmind/webui/app.py` `run_admin_ui` builds `backend_factory(ADMIN_PROFILE, trace=True)`. `stream()` calls `TraceRecorder.start(source=profile_name, ..., profile=profile_name, ...)`.
- `artmind/benchmark.py`:
  - `_create_benchmark_run(source_path, questions, domain_hint, backend, name)` inserts into `benchmark_runs`;
  - `_RUN_COLS` / `_row_to_run` read it;
  - `_run_benchmark(run_id)` uses `BENCHMARK_PROFILE` and `TraceRecorder.start(source="benchmark", ..., profile="benchmark", ...)`.
- `artmind/webui/benchmark_routes.py`: `BenchmarkRunRequest(path, domain_hint, backend, name)`, then `_create_benchmark_run`.
- `artmind/webui/templates/dashboard.html` (~line 197) has the `benchmark-backend` select. `artmind/webui/static/benchmark.js` (~line 54) posts `{path, domainHint, backend, name}`.
- `artmind/db.py`:
  - `benchmark_runs` is created around line 100 and has no migration loop yet; add one the way `trace_turns` does at ~line 466: `PRAGMA table_info`, then `ALTER TABLE ... ADD COLUMN`;
  - `trace_turns` columns are migrated at ~line 466.
- `artmind/traces/store.py` `insert_turn(trace_id, *, source, question, session_id, profile, backend, benchmark_run_id, benchmark_seq, started_at)`.
- `artmind/setup.py` `_symlink_skills(dest)` links every directory under `artmind/skills/`, so a new skill folder is picked up by `artmind init` without code changes. `test/test_webui_profiles.py` pins each profile's skill set.
- `artmind/webui/help.py` lists skill concepts from `ADMIN_PROFILE.skills`.
- The executor's bundle: `artmind/query_run/bundle.py` (`mode`, `plan`, `scope`, `path`, `evidence`, `documents`, `facts`, `guides`, `coverage`, `gaps`, `escalate{reason, suggested}`, `offer`, `timing`, `truncated`, `rows_total`, `handle`, `next`). Paging is `artmind query page <handle> --list evidence|documents|facts|guides`.
- Route ids and descriptions: `artmind.query_plan.routes.load_routes()` gives `Route.id`, `.description` and `.programs[*].kind` / `.program`; `program.run_nodes(...)` gives run templates.

---

### Task 1: The profile `contract` and `with_contract`

**Files:**
- Modify: `artmind/webui/profiles.py`
- Test: `test/test_agent_contract.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The agent contract (P3 spec §8.1, D5): a profile picks the classic or the bundle query skill."""

import pytest

from artmind.webui import profiles


def test_profiles_default_to_classic():
    for p in profiles.PROFILES.values():
        assert p.contract == "classic"
        assert "artmind-query" in p.skills and "artmind-query-bundle" not in p.skills


@pytest.mark.parametrize("base", [profiles.ADMIN_PROFILE, profiles.BENCHMARK_PROFILE, profiles.QA_PROFILE])
def test_with_contract_bundle_swaps_the_query_skill_in_place(base):
    p = profiles.with_contract(base, "bundle")
    assert p.contract == "bundle"
    assert "artmind-query-bundle" in p.skills and "artmind-query" not in p.skills
    assert p.skills.index("artmind-query-bundle") == base.skills.index("artmind-query")  # same position
    assert set(p.skills) - {"artmind-query-bundle"} == set(base.skills) - {"artmind-query"}
    assert "artmind-query-bundle" in p.system_append and "query run" in p.system_append
    assert (p.name, p.acp_mode, p.filesystem_access) == (base.name, base.acp_mode, base.filesystem_access)


def test_with_contract_classic_is_identity():
    assert profiles.with_contract(profiles.ADMIN_PROFILE, "classic") is profiles.ADMIN_PROFILE


def test_an_unknown_contract_is_refused():
    with pytest.raises(ValueError, match="classic, bundle"):
        profiles.with_contract(profiles.ADMIN_PROFILE, "fast")


@pytest.mark.parametrize("raw, expected", [(None, "classic"), ("", "classic"), ("classic", "classic"),
                                           ("bundle", "bundle"), (" Bundle ", "bundle")])
def test_contract_from_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ARTMIND_AGENT_CONTRACT", raising=False)
    else:
        monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", raw)
    assert profiles.contract_from_env() == expected


def test_a_bad_env_contract_is_refused(monkeypatch):
    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "fast")
    with pytest.raises(ValueError, match="ARTMIND_AGENT_CONTRACT"):
        profiles.contract_from_env()
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_agent_contract.py -q`
Expected: FAIL (`AgentProfile` has no `contract`; `with_contract` is missing).

- [ ] **Step 3: Implement in `profiles.py`**

Add `contract: str = "classic"` as the last field of `AgentProfile`. Then add:

```python
import dataclasses
import os

CONTRACTS = ("classic", "bundle")
CONTRACT_ENV = "ARTMIND_AGENT_CONTRACT"
QUERY_SKILL = {"classic": "artmind-query", "bundle": "artmind-query-bundle"}

#: Appended to a profile's system prompt under the bundle contract (P3 spec §8.1). The classic text keeps naming
#: `artmind-query` for the other skills' context; this sentence says which query skill answers questions here.
BUNDLE_SENTENCE = (
    "\nFor every question that needs a lookup, use the artmind-query-bundle skill: it calls "
    "`artmind query run \"<question>\" --compact` once and reads the evidence bundle; do not call "
    "`artmind query plan` yourself."
)


def contract_from_env() -> str:
    raw = (os.environ.get(CONTRACT_ENV) or "").strip().casefold()
    if not raw:
        return "classic"
    if raw not in CONTRACTS:
        raise ValueError(f"{CONTRACT_ENV} must be one of {', '.join(CONTRACTS)}, got {raw!r}")
    return raw


def with_contract(profile: AgentProfile, contract: str) -> AgentProfile:
    """`profile` under `contract`: the query skill swapped in place, the bundle sentence appended."""
    if contract not in CONTRACTS:
        raise ValueError(f"contract must be one of {', '.join(CONTRACTS)}, got {contract!r}")
    if contract == profile.contract:
        return profile
    old, new = QUERY_SKILL[profile.contract], QUERY_SKILL[contract]
    skills = tuple(new if s == old else s for s in profile.skills)
    append = profile.system_append
    if contract == "bundle":
        append = append.replace("artmind-query for", "artmind-query-bundle for") + BUNDLE_SENTENCE
    return dataclasses.replace(profile, skills=skills, system_append=append, contract=contract)
```

The `replace("artmind-query for", ...)` covers the QA text ("artmind-query for questions"). The admin text says "artmind-query for inspecting the graph" and the benchmark text says "the artmind-query skill's protocol". The appended sentence makes the bundle skill unambiguous in every profile. Switching back from bundle to classic is not needed and is not supported: it stays a no-op only when equal. Say so in the docstring.

- [ ] **Step 4: Run the tests and the existing profile tests**

Run: `uv run --group dev pytest test/test_agent_contract.py test/test_webui_profiles.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/profiles.py test/test_agent_contract.py
git commit -m "feat(webui): agent profiles carry a contract; with_contract swaps in the bundle query skill (P3 S9)"
```

### Task 2: `trace_turns.contract` and `benchmark_runs.contract`

**Files:**
- Modify: `artmind/db.py`, `artmind/traces/store.py` (`insert_turn`), `artmind/traces/recorder.py` (`TraceRecorder.start`), `artmind/benchmark.py` (`_create_benchmark_run`, `_RUN_COLS`, `_row_to_run`)
- Test: `test/test_agent_contract_store.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The contract is recorded on every trace turn and carried by every benchmark run (P3 spec §8.1)."""

import pytest

from artmind import benchmark
from artmind.traces import store
from artmind.traces.recorder import TraceRecorder


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")


def test_a_turn_records_its_contract():
    rec = TraceRecorder.start(source="admin", question="q", contract="bundle")
    assert store.get_turn(rec.trace_id)["contract"] == "bundle"


def test_the_default_contract_is_classic():
    rec = TraceRecorder.start(source="admin", question="q")
    assert store.get_turn(rec.trace_id)["contract"] == "classic"


def test_a_benchmark_run_carries_its_contract(tmp_path):
    questions = [{"seq": 1, "qid": "Q1", "title": "t", "question": "invented?", "eval_comment": None}]
    run_id = benchmark._create_benchmark_run(str(tmp_path / "q.md"), questions, contract="bundle")
    assert benchmark._get_benchmark_run(run_id)["contract"] == "bundle"
    other = benchmark._create_benchmark_run(str(tmp_path / "q.md"), questions)
    assert benchmark._get_benchmark_run(other)["contract"] == "classic"


def test_a_bad_contract_is_refused(tmp_path):
    with pytest.raises(ValueError):
        benchmark._create_benchmark_run(str(tmp_path / "q.md"), [], contract="fast")
```

`store.get_turn` selects `*` (checked), so the new column appears without a change there.

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_agent_contract_store.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`db.py`:
- in the `trace_turns` migration loop (~line 466), add `contract TEXT NOT NULL DEFAULT 'classic'`, following the existing `ALTER TABLE` pattern;
- after the `benchmark_runs` `CREATE TABLE`, add a migration:

```python
    run_cols = {row[1] for row in cursor.execute("PRAGMA table_info(benchmark_runs)")}
    if "contract" not in run_cols:
        cursor.execute("ALTER TABLE benchmark_runs ADD COLUMN contract TEXT NOT NULL DEFAULT 'classic'")
```

`store.insert_turn`: add a keyword `contract: str = "classic"` and include it in the INSERT.

`TraceRecorder.start`: add a keyword `contract: str = "classic"` and pass it to `insert_turn`.

`benchmark._create_benchmark_run`: add a keyword `contract: str = "classic"`. Validate it against `artmind.webui.profiles.CONTRACTS` with a lazy import, because `benchmark.py` must import without the web stack. Raise `ValueError` otherwise, and insert it. Add `contract` to `_RUN_COLS` (append it last) and to `_row_to_run` as `"contract": row[12]`.

- [ ] **Step 4: Run the tests and the trace and benchmark suites**

Run: `uv run --group dev pytest test/test_agent_contract_store.py test/test_traces_*.py test/test_benchmark*.py -q`
Expected: PASS. If a test pins the exact `trace_turns` column set or the run dict keys, add `contract` and list it in the commit.

- [ ] **Step 5: Commit**

```bash
git add artmind/db.py artmind/traces/store.py artmind/traces/recorder.py artmind/benchmark.py test/test_agent_contract_store.py
git commit -m "feat(traces): trace turns and benchmark runs record the agent contract (P3 S9)"
```

### Task 3: The admin console and the benchmark driver use the contract

**Files:**
- Modify: `artmind/webui/app.py` (`create_app` signature and `stream()`, `run_admin_ui`), `artmind/benchmark.py` (`_run_benchmark`), `artmind/webui/benchmark_routes.py`, `artmind/webui/templates/dashboard.html`, `artmind/webui/static/benchmark.js`
- Test: `test/test_agent_contract_surfaces.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The admin console takes its contract from ARTMIND_AGENT_CONTRACT; a benchmark run from its own field."""

import pytest

from artmind.webui import profiles


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "0")


def test_admin_profile_follows_the_env(monkeypatch):
    from artmind.webui import app as app_mod

    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "bundle")
    assert app_mod.admin_profile().contract == "bundle"
    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "classic")
    assert app_mod.admin_profile() is profiles.ADMIN_PROFILE


def test_chat_ui_ignores_the_env(monkeypatch):
    from artmind.webui import app as app_mod

    monkeypatch.setenv("ARTMIND_AGENT_CONTRACT", "bundle")
    assert app_mod.chat_profile() is profiles.QA_PROFILE


def test_a_bundle_benchmark_run_uses_the_bundle_profile_and_records_it(tmp_path, monkeypatch):
    import asyncio

    from artmind import benchmark
    from artmind.traces import store

    seen = {}

    class FakeBackend:
        trace_context_path = None

        def __init__(self, profile):
            seen["skills"] = profile.skills

        async def connect(self): pass
        async def query(self, prompt): pass
        async def receive_events(self):
            yield {"type": "turn_done", "result": "an invented answer", "turns": 1}
        async def disconnect(self): pass

    monkeypatch.setattr("artmind.webui.backends.create_backend",
                        lambda name, profile, **kw: FakeBackend(profile))
    q = [{"seq": 1, "qid": "Q1", "title": "t", "question": "invented?", "eval_comment": None}]
    run_id = benchmark._create_benchmark_run(str(tmp_path / "q.md"), q, contract="bundle")
    asyncio.run(benchmark._run_benchmark(run_id))
    assert "artmind-query-bundle" in seen["skills"]
    (turn,) = [t for t in store.list_turns() if t.get("benchmark_run_id") == run_id]
    assert turn["contract"] == "bundle"
```

The fake backend's method names and event shape must match what `_run_benchmark` calls. Read `_run_benchmark` and copy the protocol it uses: `connect` / `query` / `receive_events` / `disconnect`, or whatever it really calls. Also copy the event type that ends a turn. An existing benchmark test in `test/` may already fake a backend; reuse it if so.

Add a route test: POST `/api/benchmark/runs` with `{"path": ..., "contract": "bundle"}` via the admin app's TestClient (see `test/test_benchmark_routes*.py` or `test/test_webui*.py` for how they build it). Assert that `_create_benchmark_run` received `contract="bundle"` (monkeypatch it and capture the kwargs).

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_agent_contract_surfaces.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

`app.py`:

```python
def admin_profile():
    """The admin console's profile under ARTMIND_AGENT_CONTRACT (P3 S9; default classic)."""
    from artmind.webui.backends import ADMIN_PROFILE
    from artmind.webui.profiles import contract_from_env, with_contract

    return with_contract(ADMIN_PROFILE, contract_from_env())


def chat_profile():
    """The chat UI stays classic until the S10 gate passes (decided 2026-11-07)."""
    from artmind.webui.backends import QA_PROFILE

    return QA_PROFILE
```

In `run_admin_ui`, use `backend_factory(admin_profile(), trace=True)`. In `run_chat_ui`, use `chat_profile()`. A bad env value must stop admin-ui at start with a clear message: catch the `ValueError`, print it, and exit 1.

In `create_app`, take the profile's contract so `stream()` can record it. Find where `stream()` gets `profile_name`, and carry the profile itself (or its `contract`) alongside it into `create_app`, for example a `contract: str = "classic"` keyword stored on the app. `run_admin_ui` passes `admin_profile().contract`. `stream()` passes `contract=...` to `TraceRecorder.start`.

`benchmark._run_benchmark`: build `profile = with_contract(BENCHMARK_PROFILE, run.get("contract") or "classic")` once per run, pass it to `create_backend`, and pass `contract=profile.contract` to `TraceRecorder.start`.

`benchmark_routes.BenchmarkRunRequest`: add `contract: str = "classic"`, and pass it through to `_create_benchmark_run`. Map a `ValueError` to HTTP 400.

`dashboard.html`: after the backend select, add:

```html
        <label>
          Contract
          <select id="benchmark-contract">
            <option value="classic" selected>classic (artmind-query)</option>
            <option value="bundle">bundle (artmind-query-bundle)</option>
          </select>
        </label>
```

`benchmark.js`: read `document.getElementById("benchmark-contract").value` and add `contract` to the POST body.

Check whether the benchmark runs list in the dashboard shows run columns, by searching `benchmark.js` for `backend` in the rendering code. If it does, show `contract` beside `backend` the same way.

- [ ] **Step 4: Run the tests and the web suites**

Run: `uv run --group dev pytest test/test_agent_contract_surfaces.py test/test_webui*.py test/test_benchmark*.py test/test_traces_app.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/app.py artmind/benchmark.py artmind/webui/benchmark_routes.py artmind/webui/templates/dashboard.html artmind/webui/static/benchmark.js test/test_agent_contract_surfaces.py
git commit -m "feat(webui): admin console and benchmark runs choose the agent contract; chat-ui stays classic (P3 S9)"
```

### Task 4: Generated references `routes.md` and `commands.md`

**Files:**
- Create: `artmind/query_run/skill_refs.py`
- Create: `artmind/skills/artmind-query-bundle/references/routes.md`, `artmind/skills/artmind-query-bundle/references/commands.md` (generated)
- Modify: `justfile` (recipe `dev-gen-query-refs`)
- Test: `test/test_query_bundle_refs.py`

- [ ] **Step 1: Write the failing tests**

```python
"""The bundle skill's generated references stay in step with routes.yaml and the Click tree (P3 spec §8.2)."""

from pathlib import Path

from artmind.query_run import skill_refs

SKILL = Path(skill_refs.__file__).resolve().parents[1] / "skills" / "artmind-query-bundle"


def test_routes_md_is_current():
    assert (SKILL / "references" / "routes.md").read_text(encoding="utf-8") == skill_refs.routes_md(), \
        "stale: run `just dev-gen-query-refs`"


def test_commands_md_is_current():
    assert (SKILL / "references" / "commands.md").read_text(encoding="utf-8") == skill_refs.commands_md(), \
        "stale: run `just dev-gen-query-refs`"


def test_routes_md_names_every_route():
    from artmind.query_plan import routes

    text = skill_refs.routes_md()
    for route in routes.load_routes():
        assert f"## `{route.id}`" in text


def test_commands_md_lists_the_query_leaves_with_options():
    text = skill_refs.commands_md()
    for cmd in ("artmind query run", "artmind query page", "artmind query vector-text", "artmind query survey",
                "artmind query entity-context", "artmind query graph pattern7"):
        assert f"`{cmd}`" in text
    assert "--shape" in text and "--compact" in text
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_bundle_refs.py -q`
Expected: FAIL.

- [ ] **Step 3: Write `artmind/query_run/skill_refs.py`**

```python
"""Generated references for the artmind-query-bundle skill (P3 spec §8.2).

`routes.md` is the routing table from routes.yaml (description, program kinds, commands per program);
`commands.md` lists every `artmind query` leaf command (and the read-only `artmind db` steps) with its
options, from the Click tree. Both are written by `just dev-gen-query-refs`; a test fails when stale.
Deterministic output: sorted, no timestamps.
"""

from __future__ import annotations

import click

HEADER = "<!-- Generated by `just dev-gen-query-refs` from {src}. Do not edit by hand. -->\n\n"


def _cmd_words(template: str) -> str:
    words = []
    for token in template.split():
        if token.startswith("-"):
            break
        words.append(token)
    return " ".join(words)


def routes_md() -> str:
    from artmind.query_plan import program, routes

    out = [HEADER.format(src="artmind/query_plan/routes.yaml"), "# Routes\n\n",
           "What `query run` plans for each question shape, and the commands each program runs. "
           "Use it when a bundle escalates or misses part of the question.\n"]
    for route in routes.load_routes():
        out.append(f"\n## `{route.id}`\n\n{route.description}\n\n")
        for choice in route.programs:
            when = f" — when `{choice.when.text}`" if choice.when is not None else " — default"
            cmds = sorted({_cmd_words(n.run) for n in program.run_nodes(choice.program)})
            out.append(f"- **{choice.kind}**{when}: " + ", ".join(f"`{c}`" for c in cmds) + "\n")
        if route.variants:
            for kind, steps in sorted(route.variants.items()):
                out.append(f"- **{kind}** (store kind): " + ", ".join(f"`{_cmd_words(s)}`" for s in steps) + "\n")
        if route.read:
            out.append("\nRead: " + ", ".join(f"`{r}`" for r in route.read) + "\n")
    return "".join(out)


def _leaves(group: click.Group, prefix: list[str]):
    for name in sorted(group.commands):
        cmd = group.commands[name]
        if getattr(cmd, "hidden", False):
            continue
        path = [*prefix, name]
        if isinstance(cmd, click.Group):
            yield from _leaves(cmd, path)
        else:
            yield path, cmd


def _options(cmd: click.Command) -> str:
    opts = []
    for p in cmd.params:
        if isinstance(p, click.Option) and not p.hidden:
            long = next((o for o in p.opts if o.startswith("--")), p.opts[0])
            opts.append(long)
        elif isinstance(p, click.Argument):
            opts.append(f"<{p.name}>")
    return " ".join(opts)


def commands_md() -> str:
    from artmind.cli import cli
    from artmind.query_run.capture import DB_READ_ONLY

    out = [HEADER.format(src="the Click tree (artmind/cli.py)"), "# Commands\n\n",
           "Every `artmind query` command, and the read-only `artmind db` commands, with their options. "
           "Always pass `--compact`; pass `--shape brief` to the retrieval commands.\n\n"]
    for path, cmd in _leaves(cli.commands["query"], ["query"]):
        first = (cmd.help or "").strip().splitlines()[0] if cmd.help else ""
        out.append(f"- `artmind {' '.join(path)}` {_options(cmd)}  \n  {first}\n")
    out.append("\n## Read-only `artmind db`\n\n")
    for path, cmd in _leaves(cli.commands["db"], ["db"]):
        if " ".join(path) in DB_READ_ONLY:
            first = (cmd.help or "").strip().splitlines()[0] if cmd.help else ""
            out.append(f"- `artmind {' '.join(path)}` {_options(cmd)}  \n  {first}\n")
    return "".join(out)


def write_all(skill_dir) -> list:
    from pathlib import Path

    refs = Path(skill_dir) / "references"
    refs.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in (("routes.md", routes_md()), ("commands.md", commands_md())):
        (refs / name).write_text(text, encoding="utf-8")
        written.append(refs / name)
    return written
```

`DB_READ_ONLY` lives in `capture.py`. Check that `ProgramChoice.when` has a `.text`; it is a `Predicate`.

`justfile`: add next to `dev-refresh-skills`:

```
# regenerate the artmind-query-bundle skill's references from routes.yaml and the Click tree
dev-gen-query-refs:
    uv run python -c "from artmind.query_run import skill_refs; [print(p) for p in skill_refs.write_all('artmind/skills/artmind-query-bundle')]"
```

Run `just dev-gen-query-refs` once to write the two files.

The shared references (temporal, conflicts, hierarchy, structured-store) live under `artmind/skills/artmind-query/references/`. Link them into the new skill as relative symlinks:

```bash
cd artmind/skills/artmind-query-bundle/references && for f in temporal conflicts hierarchy structured-store; do ln -s ../../artmind-query/references/$f.md $f.md; done
```

Copy the four files rather than symlinking them: `pyproject.toml` ships `skills/**/*` as package-data, setuptools does not reliably follow file symlinks into a wheel, and `_symlink_skills` falls back to copying directories where symlinks are unavailable. `dev-gen-query-refs` copies them too (extend `write_all` to copy `artmind/skills/artmind-query/references/{temporal,conflicts,hierarchy,structured-store}.md`), and `test/test_query_bundle_refs.py` asserts each copy equals its source.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_bundle_refs.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/query_run/skill_refs.py artmind/skills/artmind-query-bundle/references justfile test/test_query_bundle_refs.py
git commit -m "feat(skills): generated routes and commands references for the bundle skill (P3 S9)"
```

### Task 5: The `artmind-query-bundle` skill

**Files:**
- Create: `artmind/skills/artmind-query-bundle/SKILL.md`
- Test: `test/test_query_bundle_skill.py`

- [ ] **Step 1: Write the failing tests** (they check the skill against data, not prose; spec §8.2)

```python
"""The bundle skill checked against data (P3 spec §8.2): commands exist, the shape is bounded, rules present."""

import re
import shlex
from pathlib import Path

import click

SKILL = Path(__file__).resolve().parents[1] / "artmind" / "skills" / "artmind-query-bundle" / "SKILL.md"


def _text():
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter():
    head = _text().split("---")[1]
    assert re.search(r"^name: artmind-query-bundle$", head, re.M)
    assert re.search(r"^description: .{40,}", head, re.M)


def test_about_150_lines():
    assert 100 <= len(_text().splitlines()) <= 190


def test_every_command_named_exists_in_the_click_tree():
    from artmind.cli import cli

    for cmd in sorted(set(re.findall(r"`(artmind [a-z][^`]*)`", _text()))):
        tokens = shlex.split(cmd.replace("<", "").replace(">", ""))
        node, i = cli, 1
        while i < len(tokens) and hasattr(node, "commands") and tokens[i] in node.commands:
            node = node.commands[tokens[i]]
            i += 1
        assert i > 2, cmd  # at least `artmind <group> <command>`
        assert not isinstance(node, click.Group), cmd  # a leaf command, not a bare group
        opts = {o for p in node.params for o in getattr(p, "opts", [])}
        for t in tokens[i:]:
            if t.startswith("--"):
                assert t.split("=")[0] in opts, (cmd, t)


def test_the_one_call_and_the_rules():
    text = _text()
    assert '`artmind query run "<question>" --compact`' in text
    assert "query plan" in text and "never" in text.lower()  # never call query plan (§13 Q9)
    for word in ("evidence", "documents", "facts", "guides", "escalate", "handle", "coverage", "mode"):
        assert word in text, word
    assert "references/routes.md" in text and "references/commands.md" in text


def test_routes_named_exist():
    from artmind.query_plan import routes

    ids = {r.id for r in routes.load_routes()}
    for rid in set(re.findall(r"`route: ([a-z_]+)`", _text())):
        assert rid in ids, rid
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --group dev pytest test/test_query_bundle_skill.py -q`
Expected: FAIL (no SKILL.md).

- [ ] **Step 3: Write the skill**

Write `artmind/skills/artmind-query-bundle/SKILL.md` with these sections, in this order. The line budget is a guide, and the total must stay between 100 and 190 lines. Use plain, short sentences.

1. **Frontmatter + purpose** (about 10 lines).
   - `name: artmind-query-bundle`.
   - The description: answer natural-language questions over artmind domains by calling `artmind query run` once per lookup turn and reading its evidence bundle; the bundle contract of P3, chosen by the admin console or a benchmark run.
   - The grounding rule: cite only bundle `evidence` and what commands you ran returned.
2. **Does this turn need a lookup?** (about 15 lines). From architecture spec §5.5 4a:
   - no lookup for a reshape, a deliverable (a table or file from the conversation), a curation hand-off (use the curate/update skills), a status check or an ops request;
   - a lookup for a new subject, "check again", or a follow-up that asks for more.
3. **The one call** (about 15 lines).
   - `artmind query run "<the user's question>" --compact`, with the user's own words.
   - Add `--domain <d>` only when the user named a domain.
   - Never pass `--previous`: the UI passes the previous turn through `ARTMIND_PREVIOUS_CONTEXT`.
   - Never call `artmind query plan` yourself.
4. **Reading a bundle** (about 30 lines). What each key means:
   - `evidence`: passages with `doc`, the only thing to cite;
   - `documents`: brief document rows, for coverage and depth;
   - `facts`: graph and SQL rows, linked to evidence by `evidence` ids;
   - `guides`: topic pages and insights; they steer, never cite them;
   - `coverage`: numbers to quote when incomplete;
   - `path`: what ran;
   - `escalate`: why it may not be enough, with `suggested` commands;
   - `truncated` / `handle` / `next`: page with `artmind query page <handle> --list evidence --compact`, or `--list documents` / `--list facts`.
5. **Mode** (about 20 lines). Every bundle says `mode: reasoning` today, because the fast path is off until a route passes its gate.
   - Treat the bundle as your starting evidence.
   - A bundle whose plan route is `search` or `survey` usually holds what the answer needs. Check that each part of the question is covered, then answer.
   - On other routes the bundle often misses documents. Before answering, check each named part of the question against `evidence` and `documents`. Fill gaps with the commands in `escalate.suggested` or the route's commands in `references/routes.md`, most often `artmind query survey --shape brief --domain <d> --compact "<question>"`.
   - On `mode: fast` (a later build): answer from the bundle. Run a command only if a named part of the question is visibly missing.
6. **Answer contract** (about 25 lines). Spec §8.4:
   - latest applicable source first;
   - incompleteness stated with `coverage` numbers, never "at least";
   - stated vs implied kept apart;
   - relations counted by direction;
   - widest granularity;
   - scope narrowing (`scope`) stated;
   - `gist_source` other than `passage` and `guides` never cited;
   - when your own commands changed the answer, ground it in what they returned;
   - when `escalate.reason` is `limit`, `deadline` or `step_error`, say what was not read.
7. **References** (about 15 lines). `references/routes.md`, `references/commands.md`, and `references/temporal.md`, `conflicts.md`, `hierarchy.md`, `structured-store.md`, each with when to read it.
8. **What not to do** (about 10 lines):
   - no `artmind query plan`;
   - no shell loops over commands (`artmind query run --each` exists for claim batches);
   - no `--previous`;
   - no Sources section (the UI adds provenance);
   - never cite guides.

Write every command in backticks starting with `artmind `, so the test checks it.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_query_bundle_skill.py test/test_query_bundle_refs.py -q`
Expected: PASS.

- [ ] **Step 5: Link the skill in the checkout and check profiles see it**

Run `just dev-refresh-skills`. It links new skills into the checkout's `.claude/` and `.pi/`, which are gitignored, so there is nothing to commit. Then run `uv run --group dev pytest test/test_webui_profiles.py test/test_agent_contract.py -q`.

- [ ] **Step 6: Commit**

```bash
git add artmind/skills/artmind-query-bundle/SKILL.md test/test_query_bundle_skill.py
git commit -m "feat(skills): artmind-query-bundle, the ~150-line bundle-first query skill (P3 S9)"
```

### Task 6: S9 gate and docs

- [ ] **Step 1: Full suite.** `uv run --group dev pytest -q test/`. Expected: all pass.
- [ ] **Step 2: Docs.**
  - **CLAUDE.md:** add a row for `artmind/skills/artmind-query-bundle/` (the bundle-contract query skill; generated references by `just dev-gen-query-refs`; chosen by `ARTMIND_AGENT_CONTRACT` in the admin console and by a benchmark run's `contract`; chat-ui stays classic; every trace turn records `contract`). In the "Skills reach the chat UI" section, say the new skill reaches a vault after `artmind init` there.
  - **Handover:** P3 entry gains "S9 built" plus the user steps from Step 3.
- [ ] **Step 3: User steps, for the user to run** (these touch the vaults; the implementer does not run them). List them in the handover:
  1. `cd ~/Projects/artmind9/.claude/worktrees/p3-design-a9f633 && just dev-install` (after merging S9).
  2. `cd ~/artmind_vaults/banking_corpus && artmind init`, and the same in brain01. This links `artmind-query-bundle` into each vault's `.claude/skills/`.
  3. For S10: two benchmark runs per vault from the admin console's benchmark form, one with **Contract: classic** and one with **Contract: bundle**. Same file, same build.
  4. To try the bundle contract by hand: start admin-ui with `ARTMIND_AGENT_CONTRACT=bundle`.
- [ ] **Step 4: Commit.** `docs: the bundle skill and the contract switch in CLAUDE.md; handover records S9 (P3 S9)`.

---

## Self-review notes

- **Spec coverage:**
  - §8.1: profile `contract` (Task 1); `ARTMIND_AGENT_CONTRACT` for the admin console and chat-ui staying classic (Task 3, decided with the user); `benchmark_runs.contract` and the form field (Tasks 2–3); `trace_turns.contract` (Task 2); why a new skill folder (Task 5); `artmind init` (Task 6 user steps).
  - §8.2: skill sections and their order (Task 5); references generated plus shared (Task 4); tests against data (Tasks 4–5).
  - §8.3: mode text (Task 5 §5).
  - §8.4: answer contract (Task 5 §6).
  - §8.5: no `--previous` from the agent (Task 5 §3).
  - §13 Q9: never `query plan` (Task 5 §3 and §8; test).
- **Out of scope for S9:** the paired comparison (`traces stats --compareRuns`, S10), the fast-path switch (S12), retiring a contract (S13), the opencode ACP persona text (`artmind/opencode/agent/*.md` keeps naming `artmind-query`; the ACP backend gets the swapped `system_append` as its preamble, which carries the bundle sentence).
- **Risk, guarded in Task 3:** skills are found under `paths.ARTMIND_AGENT_CWD / ".claude" / "skills"`. That is the vault root inside a vault (symlinks from `artmind init`), and `~/.artmind` outside one (copies from `artmind setup`). The SDK agent runs with cwd `RUN_FOLDER` = `ARTMIND_HOME` and still loads the vault-root skills (CLAUDE.md §2). Until `artmind init` runs in a vault, a `bundle` run there has no `artmind-query-bundle`. Add `profiles.require_skill(profile)`: when `profile.contract == "bundle"` and `ARTMIND_AGENT_CWD/.claude/skills/artmind-query-bundle/SKILL.md` does not exist, raise `ValueError("the bundle contract needs the artmind-query-bundle skill: run `artmind init` in this vault")`. `run_admin_ui` calls it at start (print, then exit 1). `_create_benchmark_run`, through `benchmark_routes` (HTTP 400), calls it for `contract="bundle"`. Test it with a tmp `ARTMIND_AGENT_CWD` (monkeypatch `paths.ARTMIND_AGENT_CWD` and `artmind.webui.profiles`' import of it).
