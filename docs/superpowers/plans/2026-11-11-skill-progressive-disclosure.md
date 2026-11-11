# P2b: artmind-query with references on demand — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the P2b variant of the query skill (`artmind-query-p2b`): a short SKILL.md plus references the agent
opens when a routing row or a `guide` key on a result points at them, with `commands.md` generated from the Click
tree. Then measure it as contract `p2b` and promote it if it passes.

**Architecture:** `artmind/guides.py` decides, from a command path and its payload, which reference keys a result
points at; `_echo_json` adds them as `guide` and trace evidence keeps them. `artmind/cli_guide.py` gains a markdown
renderer that reuses the admin console's CLI-guide walk to write `references/commands.md`. The skill text is split
from the current `artmind-query` SKILL.md by a one-off script, so every moved line lands verbatim in exactly one
file, and a test checks that.

**Tech Stack:** Python 3.14, Click (rich-click), pytest, `uv`, `just`, SQLite (trace tables).

**Spec:** `docs/superpowers/specs/2026-11-11-skill-progressive-disclosure-design.md`.

**Constraints for whoever runs this:**
- brain01 is a private journal: no names, places, question or answer text in code, tests, commits or docs. Counts
  and ids only. banking_corpus is public.
- Stage only files you changed (`git add <paths>`, never `git add -A`). Do not push.
- Do not edit `artmind/skills/artmind-query/` (the live skill) until Task 11 (promotion).
- `artmind/query_plan/` is frozen: no imports from it.
- Benchmark runs cost money: Task 10 asks the user before any run.

---

## File structure

| File | Responsibility |
|---|---|
| `artmind/guides.py` (new) | `GUIDES`, `reads_mart`, `guides_for`, `with_guide`. Stdlib only. |
| `artmind/cli.py` (modify `_echo_json`, `query_text2sql`) | add `guide` before the envelope; text2sql's mart test via `guides.reads_mart` |
| `artmind/hints.py` (modify `text2sql_mart`) | use `guides.reads_mart` |
| `artmind/traces/evidence.py` (modify `_evidence`) | keep `guide` and the first `next` on the span |
| `artmind/cli_guide.py` (modify) | `agent_command`, `render_markdown` |
| `justfile` (modify) | `dev-skill-commands` recipe |
| `artmind/skills/artmind-query-p2b/` (new) | SKILL.md, references (split + new + generated) |
| `scripts/split_p2b_skill.py` (new, one-off, kept for the record) | splits the live SKILL.md into the variant |
| `scripts/p2b_gate.py` (new) | the measurement in spec §7, counts only |
| `test/test_guides.py` (new) | `guides_for` per key, `_echo_json` wiring |
| `test/test_traces_evidence.py` (modify) | `guide`/`next` on evidence |
| `test/test_cli_guide.py` (modify) | `render_markdown` |
| `test/test_skill_commands_md.py` (new) | checked-in `commands.md` is current |
| `test/test_skill_references.py` (new) | guide keys ↔ files ↔ routing table |
| `test/test_query_skill_p2b.py` (new) | moved-text check, verbatim sections, command existence, size |

---

### Task 1: `artmind/guides.py`

**Files:**
- Create: `artmind/guides.py`
- Test: `test/test_guides.py`

- [ ] **Step 1: Write the failing tests**

```python
"""guide keys on results (spec 2026-11-11 skill progressive disclosure §5.1)."""
import pytest

from artmind import guides


def test_keys_are_the_reference_stems_in_precedence_order():
    assert guides.GUIDES == ("hubs", "resolve", "temporal", "composites", "conflicts", "graph-model", "survey")


@pytest.mark.parametrize("sql,expected", [
    ("SELECT * FROM j__mart_fact_happening", True),
    ("SELECT * FROM j__visits", False),
    (None, False),
])
def test_reads_mart(sql, expected):
    assert guides.reads_mart(sql) is expected


FIRES = [
    ("hubs", "query entity-context", {"rows": [{"entity": {}, "hub": True, "cut": {"connections": 3}}]}),
    ("resolve", "query entity-resolve", {"rows": [{"entity": {}}], "twins": [[{"_id": "a"}, {"_id": "b"}]]}),
    ("resolve", "query views run", {"status": "needs_disambiguation", "candidates": []}),
    ("resolve", "query entity-resolve", {"status": "no_match", "rows": []}),
    ("temporal", "query vector-text", {"rows": [{"doc": {"id": "d", "replaced": [{"name": "old"}]}}]}),
    ("temporal", "query survey", {"rows": [{"doc_id": "d", "replaced": [{"name": "old"}]}]}),
    ("temporal", "query chunks", {"rows": [{"doc": {"id": "d", "replaced": [{"name": "old"}]}}]}),
    ("composites", "query text2sql", {"rows": [{"n": 3}], "generated_sql": "SELECT count(*) FROM j__mart_x"}),
    ("conflicts", "query graph conflicts", {"rows": [{"id": "c1"}]}),
    ("graph-model", "query graph text2cypher", {"rows": [], "generated_cypher": "MATCH (n) RETURN n"}),
    ("graph-model", "query graph pattern8", {"rows": [{"entity": {}}]}),
    ("survey", "query survey", {"rows": [{"doc_id": "d"}], "coverage": {"matching_documents": 3, "shown": 3}}),
    ("survey", "query topics run", {"rows": [{"doc_id": "d"}]}),
]


@pytest.mark.parametrize("key,command,payload", FIRES)
def test_each_key_fires(key, command, payload):
    assert key in guides.guides_for(command, payload)


QUIET = [
    ("query entity-context", {"rows": [{"entity": {}}]}),
    ("query entity-resolve", {"rows": [{"entity": {}}]}),
    ("query views run", {"status": "ok", "rows": []}),
    ("query vector-text", {"rows": [{"doc": {"id": "d"}}]}),
    ("query text2sql", {"rows": [{"n": 3}], "generated_sql": "SELECT 1 FROM j__visits"}),
    ("query text2sql", {"rows": [], "generated_sql": "SELECT 1 FROM j__mart_x"}),
    ("query text2sql", {"rows": [{"n": 1}], "generated_sql": "SELECT 1 FROM j__mart_x", "dry_run": True}),
    ("query graph conflicts", {"rows": []}),
    ("query graph pattern8", {"rows": []}),
    ("query survey", {"rows": []}),
    ("query orient", {"domains": []}),
    ("ingest sync", {"rows": [{"hub": True}]}),
]


@pytest.mark.parametrize("command,payload", QUIET)
def test_nothing_fires(command, payload):
    assert guides.guides_for(command, payload) == []


def test_at_most_two_keys_in_precedence_order():
    payload = {"rows": [{"entity": {}, "hub": True, "doc": {"replaced": [{"name": "o"}]}}],
               "twins": [[{"_id": "a"}]]}
    # entity-context fires hubs; twins and replaced are only read for their own commands
    assert guides.guides_for("query entity-context", payload) == ["hubs"]
    many = {"rows": [{"doc": {"replaced": [{"name": "o"}]}}], "coverage": {"matching_documents": 1, "shown": 1}}
    assert guides.guides_for("query survey", many) == ["temporal", "survey"]


def test_with_guide_adds_only_when_something_fires_and_keeps_a_set_guide():
    assert "guide" not in guides.with_guide("query survey", {"rows": []})
    assert guides.with_guide("query survey", {"rows": [{"doc_id": "d"}]})["guide"] == ["survey"]
    own = {"rows": [{"doc_id": "d"}], "guide": ["hubs"]}
    assert guides.with_guide("query survey", own)["guide"] == ["hubs"]
    assert guides.with_guide(None, {"rows": [{"doc_id": "d"}]}) == {"rows": [{"doc_id": "d"}]}
    assert guides.with_guide("query survey", ["not", "a", "dict"]) == ["not", "a", "dict"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_guides.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.guides'`.

- [ ] **Step 3: Write `artmind/guides.py`**

```python
"""Which artmind-query reference a result points at (spec 2026-11-11 skill progressive disclosure §5.1).

A result that shows something the skill handles in a reference (a hub, twins, a replaced document, mart rows,
conflict rows, Cypher, a survey) carries `guide: [key, ...]`; the skill reads `references/<key>.md`. A key is the
reference file's stem, never a path: the agent may have loaded the skill from the vault, `<vault>/.artmind` or
`~/.artmind`. test/test_skill_references.py keeps keys and files in step. Stdlib only; never raises.
"""

from __future__ import annotations

from typing import Any, Callable

#: Every key, in precedence order (the first two that apply are kept).
GUIDES: tuple[str, ...] = ("hubs", "resolve", "temporal", "composites", "conflicts", "graph-model", "survey")
MAX_GUIDES = 2
_NOT_AN_ANSWER = ("needs_disambiguation", "no_match")


def reads_mart(sql: Any) -> bool:
    """True when generated SQL reads a mart table (`<domain>__mart_*`)."""
    return isinstance(sql, str) and "__mart_" in sql


def _rows(payload: dict) -> list[dict]:
    rows = payload.get("rows")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _hubs(command: str, payload: dict) -> bool:
    return command == "query entity-context" and any(r.get("hub") is True or r.get("cut") for r in _rows(payload))


def _resolve(command: str, payload: dict) -> bool:
    if command not in ("query entity-resolve", "query views run"):
        return False
    return bool(payload.get("twins")) or payload.get("status") in _NOT_AN_ANSWER


def _replaced(row: dict) -> bool:
    doc = row.get("doc")
    return bool(row.get("replaced")) or (isinstance(doc, dict) and bool(doc.get("replaced")))


def _temporal(command: str, payload: dict) -> bool:
    return command in ("query vector-text", "query survey", "query chunks") and any(
        _replaced(r) for r in _rows(payload))


def _composites(command: str, payload: dict) -> bool:
    return (command == "query text2sql" and not payload.get("dry_run") and bool(_rows(payload))
            and reads_mart(payload.get("generated_sql")))


def _conflicts(command: str, payload: dict) -> bool:
    return command == "query graph conflicts" and bool(_rows(payload))


def _graph_model(command: str, payload: dict) -> bool:
    if command == "query graph text2cypher":
        return isinstance(payload.get("generated_cypher"), str)
    return command.startswith("query graph pattern") and bool(_rows(payload))


def _survey(command: str, payload: dict) -> bool:
    return command in ("query survey", "query topics run") and bool(_rows(payload))


_RULES: dict[str, Callable[[str, dict], bool]] = {
    "hubs": _hubs, "resolve": _resolve, "temporal": _temporal, "composites": _composites,
    "conflicts": _conflicts, "graph-model": _graph_model, "survey": _survey,
}
assert tuple(_RULES) == GUIDES


def guides_for(command: str | None, payload: Any) -> list[str]:
    """The reference keys `payload` (printed by `command`, e.g. "query survey") points at; at most two."""
    if not isinstance(command, str) or not isinstance(payload, dict):
        return []
    try:
        return [k for k in GUIDES if _RULES[k](command, payload)][:MAX_GUIDES]
    except Exception:  # a guide is a hint; an odd payload never breaks the command
        return []


def with_guide(command: str | None, payload: Any) -> Any:
    """`payload` with `guide` added when a key applies; a `guide` the command set itself is kept."""
    if not isinstance(payload, dict) or "guide" in payload:
        return payload
    keys = guides_for(command, payload)
    return {**payload, "guide": keys} if keys else payload
```

Note on survey: the spec's `survey` row lists `coverage` partial, `periods`, or brief rows. The agent always passes
`--shape brief`, so in practice that is every survey with rows; the rule says so directly. Task 12 updates the spec
row to match.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_guides.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/guides.py test/test_guides.py
git commit -m "feat(guides): reference keys a result points at (P2b)"
```

---

### Task 2: `guide` on every printed result, and text2sql's mart test in one place

**Files:**
- Modify: `artmind/cli.py` (`_echo_json`, around line 490; `query_text2sql`, the `mart_next` block)
- Modify: `artmind/hints.py:92-96` (`text2sql_mart`)
- Test: `test/test_guides.py` (append)

- [ ] **Step 1: Append the failing tests**

```python
import json

import click
from click.testing import CliRunner


def _run_echo(command_name: str, payload: dict) -> dict:
    from artmind.cli import _echo_json

    @click.group()
    def root():
        pass

    @root.group(name="query")
    def query():
        pass

    @query.command(name=command_name)
    def cmd():
        _echo_json(payload, compact=True)

    result = CliRunner().invoke(root, ["query", command_name])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_echo_json_adds_guide_after_the_envelope():
    out = _run_echo("survey", {"rows": [{"doc_id": "d"}]})
    assert list(out)[:3] == ["command", "warnings", "next"]
    assert out["guide"] == ["survey"]
    assert set(out["guide"]) <= set(guides.GUIDES)


def test_echo_json_adds_no_guide_when_nothing_fires():
    assert "guide" not in _run_echo("survey", {"rows": []})


def test_text2sql_mart_hint_uses_the_shared_mart_test(monkeypatch):
    from artmind import hints

    calls = []
    monkeypatch.setattr(guides, "reads_mart", lambda sql: calls.append(sql) or True)
    assert hints.text2sql_mart({"rows": [{"n": 1}], "generated_sql": "x"}, ["d"], "q")
    assert calls == ["x"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_guides.py -q -k "echo_json or shared_mart"`
Expected: FAIL (`guide` missing from the output; `calls == []`).

- [ ] **Step 3: Wire `_echo_json`**

In `artmind/cli.py`, `_echo_json`, replace

```python
    payload = envelope(payload, ctx)  # command / warnings / next (cli_conventions, spec C1+C4)
```

with

```python
    from artmind import guides

    payload = guides.with_guide(_command_path(ctx) if ctx is not None else None, payload)  # P2b §5.2
    payload = envelope(payload, ctx)  # command / warnings / next (cli_conventions, spec C1+C4)
```

(`_command_path` is already imported in `cli.py`; check with `grep -n "_command_path" artmind/cli.py | head -3`.
If it is not, add it to the existing `from artmind.cli_conventions import ...` line.)

- [ ] **Step 4: Use the shared mart test in `hints.text2sql_mart`**

In `artmind/hints.py`, replace

```python
    if payload.get("dry_run") or not payload.get("rows") or "__mart_" not in (payload.get("generated_sql") or ""):
        return None
```

with

```python
    from artmind import guides

    if payload.get("dry_run") or not payload.get("rows") or not guides.reads_mart(payload.get("generated_sql")):
        return None
```

- [ ] **Step 5: Run the guide, hints and conventions tests**

Run: `uv run --group dev pytest test/test_guides.py test/test_hints.py test/test_hints_cli.py test/test_cli_conventions.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py artmind/hints.py test/test_guides.py
git commit -m "feat(guides): _echo_json adds guide; text2sql's mart test lives in guides.reads_mart"
```

---

### Task 3: spans keep `guide` and the first `next`

`next` is kept too because the gate (Task 10) and E2 need to know what a result pointed at.

**Files:**
- Modify: `artmind/traces/evidence.py` (`_evidence`, after the `warnings` block near line 296)
- Test: `test/test_traces_evidence.py` (append)

- [ ] **Step 1: Append the failing test**

```python
def test_guide_and_first_next_are_kept():
    from artmind.traces import evidence

    ev = evidence.summarize({"rows": [{"doc_id": "d"}], "guide": ["survey"],
                             "next": "artmind query page h1 --from 10; artmind query survey 'x'"})["evidence"]
    assert ev["guide"] == ["survey"]
    assert ev["next"] == "artmind query page h1 --from 10"
    assert "guide" not in evidence.summarize({"rows": []})["evidence"]
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --group dev pytest test/test_traces_evidence.py -q -k guide_and_first_next`
Expected: FAIL with `KeyError: 'guide'`.

- [ ] **Step 3: Implement**

In `artmind/traces/evidence.py`, `_evidence`, directly after

```python
    if warnings:
        ev["warnings"] = warnings[:_WARNINGS_KEPT]
```

add

```python
    guide = payload.get("guide")
    if isinstance(guide, list) and guide:
        ev["guide"] = [g for g in guide if isinstance(g, str)][:2]
    nxt = payload.get("next")
    if isinstance(nxt, str) and nxt.strip():
        ev["next"] = nxt.split("; ")[0].strip()[:_WARNING_CHARS]
```

- [ ] **Step 4: Run the evidence tests**

Run: `uv run --group dev pytest test/test_traces_evidence.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/evidence.py test/test_traces_evidence.py
git commit -m "feat(traces): spans keep a result's guide and first next"
```

---

### Task 4: `render_markdown` in the CLI guide

**Files:**
- Modify: `artmind/cli_guide.py` (append after `render_fragment`)
- Test: `test/test_cli_guide.py` (append)

- [ ] **Step 1: Append the failing tests**

```python
def test_agent_command_scope():
    from artmind.cli_guide import agent_command

    assert agent_command("artmind query survey")
    assert agent_command("artmind query graph pattern8")
    assert agent_command("artmind db sql")
    assert not agent_command("artmind query plan")  # frozen planner
    assert not agent_command("artmind db bridge confirm")
    assert not agent_command("artmind ingest sync")


def test_render_markdown_lists_agent_commands_with_their_options():
    from artmind.cli_guide import render_markdown

    md = render_markdown()
    assert md.startswith("# artmind commands")
    assert "\n## artmind query survey\n" in md
    assert "\n## artmind db sql\n" in md
    assert "artmind ingest" not in md and "\n## artmind query plan\n" not in md
    survey = md.split("\n## artmind query survey\n", 1)[1].split("\n## ", 1)[0]
    assert "`--by`" in survey and "`--shape`" in survey
    assert render_markdown() == md  # deterministic
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_cli_guide.py -q -k "agent_command or render_markdown"`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement**

Append to `artmind/cli_guide.py`:

```python
# ---------------------------------------------------------------------------
# Markdown for the query skill's references/commands.md (P2b, spec 2026-11-11 §5.4).
# Same walk and parameter data as the HTML guide, so the two cannot disagree.
# ---------------------------------------------------------------------------

#: Read-only `db` commands the Q&A agent may run (the ones traces count as discovery or sql).
AGENT_DB_COMMANDS = frozenset({
    "artmind db bridge", "artmind db list", "artmind db schema", "artmind db sql", "artmind db timeline",
    "artmind db review",
})
#: `query` commands left out: the frozen planner (spec 2026-11-08).
AGENT_EXCLUDED = frozenset({"artmind query plan"})

COMMANDS_MD_HEADER = (
    "# artmind commands\n\n"
    "Generated from the CLI by `just dev-skill-commands`; do not edit. Every `query` command and the read-only\n"
    "`db` commands, with every option. Each also takes `--domain` (repeatable) and `--compact` where listed.\n")


def agent_command(path: str) -> bool:
    return (path.startswith("artmind query ") and path not in AGENT_EXCLUDED) or path in AGENT_DB_COMMANDS


def _md_cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def _md_command(path: str, cmd, examples_data: dict) -> str:
    params = collect_params(cmd)
    brief = cmd.short_help or (cmd.help or "").strip().split("\n")[0]
    args = " ".join(p["name"].upper() if p["required"] else f"[{p['name'].upper()}]"
                    for p in params if p["kind"] == "argument")
    out = [f"## {path}", "", _md_cell(brief), "", f"`{path}{' ' + args if args else ''} [OPTIONS]`", ""]
    if params:
        out += ["| Flag | Type | Default | Help |", "|---|---|---|---|"]
        for p in params:
            flag = p["flags"] if p["kind"] == "option" else p["name"].upper()
            flag = f"`{flag}`" + (" (required)" if p["required"] else "")
            default = f"`{p['default']}`" if p["default"] not in (None, "", "False") else ""
            out.append(f"| {flag} | {p['type']} | {default} | {_md_cell(p['help'])} |")
        out.append("")
    for snippet in examples_data.get(path, {}).get("examples", []):
        out += ["```bash", snippet, "```", ""]
    return "\n".join(out)


def render_markdown(include=agent_command, examples_path: Path | None = None) -> str:
    """commands.md for the query skill: the commands `include` keeps, in the CLI guide's order."""
    examples_data = load_examples(examples_path)
    seen: set[str] = set()
    parts = [COMMANDS_MD_HEADER]
    for section in build_sections():
        for _title, items in section["panels"]:
            for path, cmd in items:
                if include(path) and path not in seen:
                    seen.add(path)
                    parts.append(_md_command(path, cmd, examples_data))
    return "\n".join(parts).rstrip() + "\n"
```

- [ ] **Step 4: Run the CLI guide tests**

Run: `uv run --group dev pytest test/test_cli_guide.py -q`
Expected: all pass. If `--by` or `--shape` is missing from the survey section, print
`render_markdown()` and check that `collect_params` sees the option; fix the renderer, not the test.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli_guide.py test/test_cli_guide.py
git commit -m "feat(cli_guide): render the agent's commands as markdown (P2b commands.md)"
```

---

### Task 5: the `dev-skill-commands` recipe and the staleness test

**Files:**
- Modify: `justfile` (after the `dev-cli-help` recipe, line ~109)
- Create: `test/test_skill_commands_md.py`

- [ ] **Step 1: Write the failing test**

```python
"""Each query skill's references/commands.md matches the CLI (P2b, spec 2026-11-11 §5.4)."""
from pathlib import Path

import pytest

from artmind.cli_guide import render_markdown

SKILLS = Path(__file__).resolve().parent.parent / "artmind" / "skills"
TARGETS = sorted(SKILLS.glob("artmind-query*/references/commands.md"))


def test_some_skill_has_commands_md():
    assert TARGETS, "no artmind-query*/references/commands.md; run: just dev-skill-commands"


@pytest.mark.parametrize("path", TARGETS, ids=lambda p: p.parent.parent.name)
def test_commands_md_is_current(path):
    skill = path.parent.parent.name
    assert path.read_text(encoding="utf-8") == render_markdown(), (
        f"{skill}/references/commands.md is stale; run: just dev-skill-commands {skill}")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --group dev pytest test/test_skill_commands_md.py -q`
Expected: FAIL on `test_some_skill_has_commands_md`.

- [ ] **Step 3: Add the recipe**

In `justfile`, after the `dev-cli-help` recipe:

```just
# regenerate a query skill's references/commands.md from the CLI (P2b; test/test_skill_commands_md.py checks it)
dev-skill-commands skill="artmind-query-p2b":
    mkdir -p artmind/skills/{{skill}}/references
    uv run python -c "from artmind.cli_guide import render_markdown; import sys; sys.stdout.write(render_markdown())" > artmind/skills/{{skill}}/references/commands.md
```

- [ ] **Step 4: Generate and run the test**

Run: `just dev-skill-commands && uv run --group dev pytest test/test_skill_commands_md.py -q`
Expected: pass. `artmind/skills/artmind-query-p2b/references/commands.md` exists; `head -20` of it shows the header
and the first `## artmind query …` command.

If the test fails although the file was just written, a default differs between the two processes (an environment
variable read at import). Find it with `diff <(uv run python -c "...render_markdown()...") <file>`, and render that
default as Click declares it, not as the environment sets it.

- [ ] **Step 5: Commit**

```bash
git add justfile test/test_skill_commands_md.py artmind/skills/artmind-query-p2b/references/commands.md
git commit -m "feat(skills): generated commands.md for artmind-query-p2b, with a staleness test"
```

---

### Task 6: split the live skill into the variant

The script copies the live `artmind/skills/artmind-query/` text into `artmind/skills/artmind-query-p2b/`, moving each
section verbatim to the file the spec names, and writes the new SKILL.md parts. Run it once; it is kept in
`scripts/` as the record of how the variant was made.

What goes where (headings in the live SKILL.md, each section running to the next marker listed):

| Lines starting with | Up to (not including) | Goes to |
|---|---|---|
| `---` (top) | `## Fixed Structural Schema` | SKILL.md head (Every turn, Orient, Plan the parts, Read every result, Running commands, Grounding, Required Inputs) |
| `## Fixed Structural Schema` | `## Insights` | `graph-model.md` |
| `## Insights` | `## Survey` | `insights.md` (includes Catalogue and assets) |
| `## Survey` | `## The Query Protocol` | `survey.md` (includes Topic plans) |
| `## The Query Protocol` | `### 1. Views` | dropped (one heading line) |
| `### 1. Views` | `#### Store routing` | `views.md` |
| `#### Store routing` | `A card with \`mart\`` | appended to `structured-store.md` |
| `A card with \`mart\`` | `### 2. Discover` | `composites.md` |
| `### 2. Discover` | `### 3. Resolve` | `graph-model.md` |
| `### 3. Resolve` | `### 4. Retrieve` | `resolve.md` |
| `### 4. Retrieve` | `**Superseded history.**` | `graph-model.md` |
| `**Superseded history.**` | `#### Hub entities` | appended to `temporal.md` |
| `#### Hub entities` | `### 5. Ground` | `hubs.md` |
| `### 5. Ground` | `### 6. Adjudicate` | `hubs.md` |
| `### 6. Adjudicate` | `## Fallback Ladder` | appended to `conflicts.md` |
| `## Fallback Ladder` | end | SKILL.md tail (Fallback Ladder, Answer Style, Provenance) |

**Files:**
- Create: `scripts/split_p2b_skill.py`
- Create (by running it): `artmind/skills/artmind-query-p2b/SKILL.md` and `references/{graph-model,insights,survey,views,composites,resolve,hubs,structured-store,temporal,conflicts,hierarchy}.md`

- [ ] **Step 1: Write the script**

```python
"""One-off: build artmind/skills/artmind-query-p2b from the live artmind-query text (P2b, spec 2026-11-11).

Every moved line is copied verbatim; test/test_query_skill_p2b.py checks each lands in exactly one file.
Run from the checkout root: uv run python scripts/split_p2b_skill.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "artmind" / "skills" / "artmind-query"
DST = ROOT / "artmind" / "skills" / "artmind-query-p2b"

lines = (SRC / "SKILL.md").read_text(encoding="utf-8").splitlines()


def at(prefix: str) -> int:
    hits = [i for i, ln in enumerate(lines) if ln.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


def span(start: str, end: str | None) -> list[str]:
    return lines[at(start):(at(end) if end else len(lines))]


def body(chunk: list[str]) -> str:
    return "\n".join(chunk).strip("\n") + "\n"


HEAD = lines[:at("## Fixed Structural Schema")]
TAIL = span("## Fallback Ladder", None)

refs: dict[str, list[str]] = {
    "graph-model": ["# Graph model and the pattern commands",
                    "Read when a result carries `guide: graph-model` (any `pattern*`, `text2cypher`), when you write "
                    "Cypher, or when orient was cut.", "",
                    *span("## Fixed Structural Schema", "## Insights"), "",
                    *span("### 2. Discover", "### 3. Resolve"), "",
                    *span("### 4. Retrieve", "**Superseded history.**")],
    "insights": ["# Insights and the catalogue",
                 "Read for broad or retrospective questions, or to see what a domain already answers.", "",
                 *span("## Insights", "## Survey")],
    "survey": ["# Survey and topic plans",
               "Read when a result carries `guide: survey`, or before a question that spans documents.", "",
               *span("## Survey", "## The Query Protocol")],
    "views": ["# Saved views",
              "Read when orient lists a view whose `summary` or `examples` fit the question.", "",
              *span("### 1. Views", "#### Store routing")],
    "composites": ["# The mart: facts, dimensions and wholes",
                   "Read when a result carries `guide: composites` (a `text2sql` answer from `<d>__mart_*`), or when "
                   "orient's card lists `mart`.", "",
                   *span("A card with `mart`", "### 2. Discover")],
    "resolve": ["# Resolve names to entities",
                "Read when a result carries `guide: resolve` (`twins`, `needs_disambiguation`, `no_match`).", "",
                *span("### 3. Resolve", "### 4. Retrieve")],
    "hubs": ["# Hub entities and grounding",
             "Read when a result carries `guide: hubs` (`hub: true` or a `cut`), or before pulling source text.", "",
             *span("#### Hub entities", "### 5. Ground"), "",
             *span("### 5. Ground", "### 6. Adjudicate")],
}
appends = {
    "structured-store": span("#### Store routing", "A card with `mart`"),
    "temporal": span("**Superseded history.**", "#### Hub entities"),
    "conflicts": span("### 6. Adjudicate", "## Fallback Ladder"),
}

READ_EVERY_RESULT_END = "safe to skip when the question does not need what it offers."
GUIDE_LINES = [
    "",
    "`guide`, when present, names references of this skill for what the result shows: `guide: [\"hubs\"]` means",
    "read `references/hubs.md` before your next command, once per turn.",
]
RUNNING_COMMANDS_END = "A refused command is not a reason to stop: re-run it bare."
COMMANDS_LINES = [
    "",
    "`pattern*`, `text2cypher`, `timeline`, `conflicts`, `hierarchy`, `metadata`, `structural-metadata` and",
    "`entity-listing` sit under `artmind query graph`; every other query command under `artmind query`. Every option",
    "of every command is in `references/commands.md`.",
]

ROUTING = """## Views and routing — question shape → command → reference

Match every question against the saved views `orient --domain` listed first: a view that fits answers in one call.
Otherwise pick a row for each part. Before an `entity-context`, `entity-history` or `pattern*` call, resolve each
name with `artmind query entity-resolve --domain <d> --topK 5 --compact "<name>"` and use the chosen `_id`. Read a
row's reference before its first command this turn. Every command needs `--domain <d>` and `--compact`.

| Question shape | First command | Reference |
|---|---|---|
| A saved view's `summary` or `examples` describe it | `artmind query views show <name>`, then `artmind query views run <name> --param <p>=<value> --render markdown` | `views.md` |
| One entity: tell me about X, X's role, why X … | `artmind query entity-context --shape brief --entityId <id> --query "<question>"` | `hubs.md` |
| Text of chunk ids you already have | `artmind query chunks --shape brief --idList <id>` | `hubs.md` |
| Where, when or how something happened; quotes; no entity anchor | `artmind query vector-text --shape brief --topK 10 "<part>"` | `hubs.md` |
| Spans documents: every time, each year, a trend, across the notes | `artmind query survey --shape brief [--by year] "<part>"`, or `artmind query topics run <slug>` when orient lists a plan for it | `survey.md` |
| Counts, sums or groups over records, or a card lists `mart` | `artmind query text2sql "<question>"` | `structured-store.md`, `composites.md` |
| A code, id or key value held in a table | `artmind query resolve-key` | `structured-store.md` |
| Entities of a class; the main or top ones | `artmind query graph pattern1 --shape brief --entityClass <LABEL>`; `artmind query graph pattern9 --entityClass <LABEL> --topN 5` | `graph-model.md` |
| Which or how many X did Y … | `artmind query graph pattern8 --entityClass X --entityId <id> --via mentions --all` | `graph-model.md` |
| How X and Y are related | `artmind query graph pattern6 --entityId1 <id> --entityId2 <id>`, then `artmind query graph pattern5 --mode shortest` | `graph-model.md` |
| Entities by a name fragment | `artmind query graph pattern7 --shape brief --searchTerm "<fragment>"` | `graph-model.md` |
| All chunks of a document, or its summary | `artmind query graph pattern10 --documentName "<name>"` | `graph-model.md` |
| Aggregations or filters no row above covers | `artmind query graph text2cypher --dryRun "<question>"`, then without `--dryRun` | `graph-model.md` |
| Everyone under X, a reporting line, a chain of command | `artmind query graph hierarchy --entityId <id>` | `hierarchy.md` |
| X's value before it changed, or at a date | `artmind query entity-history --entityId <id> [--property P] [--asOf <date>]` | `temporal.md` |
| What changed when a document was edited; an earlier version | `artmind query document-history --documentName "<name>"` | `temporal.md` |
| A sequence of events across the domain, by date | `artmind query graph timeline` | `temporal.md` |
| Sources that may disagree; comparing claims | `artmind query graph conflicts` | `conflicts.md` |
| Broad or retrospective: what stands out, what changed over time | `artmind query insights list` | `insights.md` |
| What the corpus already answers | `artmind query assets`; `artmind query catalogue` | `insights.md` |
| Orient was cut and a `cuts` entry names a `rest` | the `rest` command | `graph-model.md` |
"""

LADDER_END = "4. vector-text sparse or weak → state that the available artmind data does not answer the question."
JUDGEMENT_LINES = [
    "",
    "Judgement, whatever the command:",
    "",
    "- A count, a list or a ranking needs the whole set: answer it from `complete: true`, or say N of M.",
    "- Never say a thing is absent from a graph result alone: search the text (`vector-text`, `survey`) first.",
    "- Retired documents never appear in search; a dated or 'what changed' question also reads `references/temporal.md`.",
    "- Two sources that disagree are both stated, each with its document; never blend them.",
    "- Say plainly which part the data does not answer, and why (empty, partial, not read).",
]


def insert_after(chunk: list[str], endswith: str, extra: list[str]) -> list[str]:
    idx = [i for i, ln in enumerate(chunk) if ln.rstrip().endswith(endswith)]
    assert len(idx) == 1, (endswith, idx)
    return chunk[: idx[0] + 1] + extra + chunk[idx[0] + 1:]


def main() -> None:
    (DST / "references").mkdir(parents=True, exist_ok=True)
    head = ["name: artmind-query-p2b" if ln == "name: artmind-query" else ln for ln in HEAD]
    head = insert_after(head, READ_EVERY_RESULT_END, GUIDE_LINES)
    head = insert_after(head, RUNNING_COMMANDS_END, COMMANDS_LINES)
    tail = insert_after(TAIL, LADDER_END, JUDGEMENT_LINES)
    skill = "\n".join(head).rstrip("\n") + "\n\n" + ROUTING + "\n" + "\n".join(tail).rstrip("\n") + "\n"
    (DST / "SKILL.md").write_text(skill, encoding="utf-8")
    for name, chunk in refs.items():
        (DST / "references" / f"{name}.md").write_text(body(chunk), encoding="utf-8")
    for name in ("structured-store", "temporal", "conflicts", "hierarchy"):
        text = (SRC / "references" / f"{name}.md").read_text(encoding="utf-8").rstrip("\n") + "\n"
        if name in appends:
            text += "\n" + body(appends[name])
        (DST / "references" / f"{name}.md").write_text(text, encoding="utf-8")
    print(f"SKILL.md {len(skill.splitlines())} lines; references: "
          + ", ".join(sorted(p.stem for p in (DST / "references").glob("*.md"))))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/split_p2b_skill.py`
Expected: `SKILL.md ~195 lines; references: commands, composites, conflicts, graph-model, hierarchy, hubs, insights,
resolve, structured-store, survey, temporal, views`. An `AssertionError` naming a marker means the live text
changed since this plan was written: fix the marker, not the text.

- [ ] **Step 3: Read the result**

Read `artmind/skills/artmind-query-p2b/SKILL.md` top to bottom. Check: the frontmatter says `name:
artmind-query-p2b`; the `guide` lines end "Read every result"; the routing section sits between Required Inputs
and Fallback Ladder; the judgement lines follow the ladder. Read the first 10 lines of each new reference.

- [ ] **Step 4: Commit**

```bash
git add scripts/split_p2b_skill.py artmind/skills/artmind-query-p2b/SKILL.md artmind/skills/artmind-query-p2b/references/
git commit -m "feat(skills): artmind-query-p2b, the orient text split into references on demand"
```

---

### Task 7: tests for the variant — moved text, verbatim sections, commands, size

**Files:**
- Create: `test/test_query_skill_p2b.py`

- [ ] **Step 1: Write the tests**

```python
"""artmind-query-p2b (spec 2026-11-11 skill progressive disclosure §3-§4, §6).

SOURCE is the text the variant was split from. Until promotion it is the live artmind-query skill; Task 11 points
it at the archived copy.
"""
import re
from collections import Counter
from pathlib import Path

import click

from artmind.cli import cli

ROOT = Path(__file__).resolve().parent.parent
VARIANT = ROOT / "artmind" / "skills" / "artmind-query-p2b"
SOURCE = ROOT / "artmind" / "skills" / "artmind-query" / "SKILL.md"
SOURCE_REFS = SOURCE.parent / "references"
SKILL = (VARIANT / "SKILL.md").read_text(encoding="utf-8")
FILES = {p.name: p.read_text(encoding="utf-8") for p in [VARIANT / "SKILL.md", *sorted((VARIANT / "references").glob("*.md"))]
         if p.name != "commands.md"}

VERBATIM = ("## Every turn, in this order", "## Orient — read the corpus first",
            "## Plan the parts — after orient, before any retrieval", "## Read every result before you answer")


def _significant(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if len(s) < 30 or s.startswith("#") or s.startswith("```") or re.fullmatch(r"\|[-| ]+\|", s):
            continue
        out.append(s)
    return out


def test_every_source_line_lands_in_exactly_one_file():
    source = SOURCE.read_text(encoding="utf-8")
    once = [s for s, n in Counter(_significant(source)).items() if n == 1]
    lines_by_file = {name: set(_significant(text)) for name, text in FILES.items()}
    lost, doubled = [], []
    for s in once:
        homes = [name for name, lines in lines_by_file.items() if s in lines]
        if not homes:
            lost.append(s[:80])
        elif len(homes) > 1:
            doubled.append((s[:80], homes))
    assert not lost, "lines dropped in the split:\n" + "\n".join(lost)
    assert not doubled, f"lines in more than one file: {doubled}"


def test_existing_references_keep_their_text():
    for name in ("structured-store.md", "temporal.md", "conflicts.md", "hierarchy.md"):
        old = (SOURCE_REFS / name).read_text(encoding="utf-8").rstrip("\n")
        assert FILES[name].startswith(old), name


def _section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_the_four_opening_sections_are_verbatim():
    source = SOURCE.read_text(encoding="utf-8")
    for heading in VERBATIM:
        old, new = _section(source, heading).rstrip(), _section(SKILL, heading).rstrip()
        assert new.startswith(old), heading  # Read every result gains the guide lines at its end


def test_skill_names_itself_and_says_how_to_read_guide():
    assert re.search(r"^name: artmind-query-p2b$", SKILL.split("---")[1], re.M)
    reading = _section(SKILL, "## Read every result before you answer")
    assert "`guide`" in reading and "references/hubs.md" in reading


def test_every_artmind_command_named_exists():
    def resolve(tokens):
        cmd = cli
        for t in tokens:
            if not isinstance(cmd, click.Group) or t not in cmd.commands:
                return False
            cmd = cmd.commands[t]
        return True

    for name, text in FILES.items():
        for match in re.finditer(r"artmind ((?:query|db|views|insights|topic)(?: [a-z0-9-]+){0,3})", text):
            words = match.group(1).split()
            assert any(resolve(words[:n]) for n in range(len(words), 0, -1)), (name, match.group(0))


def test_size():
    assert len(SKILL.splitlines()) < 210  # spec §3: about 170, a target not a cap
```

- [ ] **Step 2: Run them**

Run: `uv run --group dev pytest test/test_query_skill_p2b.py -q`
Expected: all pass. If `test_every_source_line_lands_in_exactly_one_file` reports a lost line, the split table
missed a section: fix `scripts/split_p2b_skill.py`, re-run it (Task 6 Step 2), and re-run this test. A doubled
line means two spans overlap; fix the markers the same way.

- [ ] **Step 3: Commit**

```bash
git add test/test_query_skill_p2b.py
git commit -m "test(skills): p2b split keeps every line, the four opening sections verbatim"
```

---

### Task 8: guide keys, reference files and the routing table stay in step

**Files:**
- Create: `test/test_skill_references.py`

- [ ] **Step 1: Write the tests**

```python
"""guide keys ↔ reference files ↔ routing table (spec 2026-11-11 skill progressive disclosure §5.1, §6)."""
import ast
import re
from pathlib import Path

from artmind import guides

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "artmind"
# The skill that ships the routing table and guide keys. Task 11 changes this to artmind-query.
SKILL_DIR = PACKAGE / "skills" / "artmind-query-p2b"
REFS = SKILL_DIR / "references"
SKILL = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _routing_refs() -> set[str]:
    table = SKILL.split("| Question shape | First command | Reference |", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"`([a-z-]+)\.md`", table))


def test_every_key_has_its_reference():
    missing = [k for k in guides.GUIDES if not (REFS / f"{k}.md").is_file()]
    assert not missing, missing


def test_every_routing_reference_exists():
    missing = sorted(r for r in _routing_refs() if not (REFS / f"{r}.md").is_file())
    assert not missing, missing


def test_every_reference_is_reached():
    reached = set(guides.GUIDES) | _routing_refs() | {"commands"}
    orphans = sorted(p.stem for p in REFS.glob("*.md") if p.stem not in reached)
    assert not orphans, orphans


# Files that write a `guide` key that is not a result's: span evidence records the result's guide.
ALLOWED = {"artmind/guides.py", "artmind/traces/evidence.py"}


def test_guide_is_set_only_in_guides_py():
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if str(path.relative_to(ROOT)) in ALLOWED or "skills" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                    and isinstance(node.slice, ast.Constant) and node.slice.value == "guide":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "guide" for k in node.keys):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "set `guide` only through artmind.guides: " + ", ".join(offenders)
```

- [ ] **Step 2: Run them**

Run: `uv run --group dev pytest test/test_skill_references.py -q`
Expected: all pass. If `test_guide_is_set_only_in_guides_py` flags an unrelated dict key named `guide` (for
example in the web UI), look at it: if it is not a result payload, add its path to an `ALLOWED` tuple in the test
with a one-line reason.

- [ ] **Step 3: Commit**

```bash
git add test/test_skill_references.py
git commit -m "test(skills): guide keys, reference files and routing rows stay in step"
```

---

### Task 9: full suite, plugin fixtures, docs

**Files:**
- Modify (only if regenerated): `obsidian/artmind-obsidian/test/fixtures/*.json`
- Modify: `CLAUDE.md` (Repo layout: add a `artmind/guides.py` row; `artmind/cli_guide.py` row mentions `render_markdown`)

- [ ] **Step 1: Run the whole suite**

Run: `just dev-test 2>&1 | tail -15`
Expected: all pass. A failure in `test/test_plugin_fixtures.py` means a fixture payload now carries `guide`:

```bash
ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q
```

```bash
just obsidian-plugin-test
```

Then `git diff --stat obsidian/` should show only added `guide` keys.

- [ ] **Step 2: Update CLAUDE.md**

Add after the `artmind/hints.py` row:

```markdown
| `artmind/guides.py` | `guide` keys (P2b, spec `docs/superpowers/specs/2026-11-11-skill-progressive-disclosure-design.md`): `guides_for(command, payload)` names the query skill's references a result points at (`hubs`, `resolve`, `temporal`, `composites`, `conflicts`, `graph-model`, `survey`; at most two), `_echo_json` adds them as `guide` (absent when none), spans keep them with the first `next`. A key is a reference file's stem; `test_skill_references.py` keeps keys, files and the routing table in step, and `guide` is set nowhere else. `reads_mart` is the one mart test. |
```

and in the `artmind/cli_guide.py` row, after "There is no checked-in HTML copy.", add: "`render_markdown()` renders
the agent's commands (every `query` command but the frozen `plan`, plus read-only `db`) as the query skill's
`references/commands.md`; `just dev-skill-commands [skill]` regenerates it and `test_skill_commands_md.py` fails when
it is stale."

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(CLAUDE.md): guides.py and the generated commands.md"
```

(Add `obsidian/artmind-obsidian/test/fixtures/` to that `git add` only if Step 1 regenerated them.)

---

### Task 10: measure P2b (two runs per vault)

**Files:**
- Create: `scripts/p2b_gate.py`

- [ ] **Step 1: Write the gate script**

```python
"""P2b gate measures (spec 2026-11-11 §7) from the registry's trace tables. Counts only, never question text.

uv run python scripts/p2b_gate.py <vault> <label>=<run8>[,<run8>...] [...] [--groups 1-14,15-29]
"""
import json
import sqlite3
import statistics
import sys
from pathlib import Path

PARTIAL_CLOSERS = ("query page", "query survey", "query topics run")


def _args(text):
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


def measure(db, run_prefixes, lo, hi):
    turns = [r for p in run_prefixes for r in db.execute(
        "select trace_id, benchmark_seq from trace_turns where benchmark_run_id like ? and benchmark_seq between ? and ?",
        (p + "%", lo, hi))]
    per_turn, partial, followed, surveyed = [], 0, 0, 0
    guide_seen, guide_read = {}, {}
    for trace_id, _seq in turns:
        spans = db.execute("select command, args, rows, rows_total, truncated, evidence from trace_spans "
                           "where trace_id = ? order by started_at", (trace_id,)).fetchall()
        spans = [s for s in spans if (s[0] or "").startswith(("query", "db"))]
        per_turn.append(len(spans))
        surveyed += any(s[0] in ("query survey", "query topics run") for s in spans)
        events = " ".join(p or "" for (p,) in db.execute("select payload from trace_events where trace_id = ?",
                                                         (trace_id,)))
        for i, (cmd, args, rows, total, trunc, ev) in enumerate(spans):
            if trunc or (isinstance(total, int) and isinstance(rows, int) and total > rows):
                partial += 1
                later = spans[i + 1:]
                if any(c in PARTIAL_CLOSERS or _args(a).get("all") is True or _args(a).get("offset")
                       for c, a, *_ in later):
                    followed += 1
            for key in (_args(ev).get("guide") or []):
                guide_seen[key] = guide_seen.get(key, 0) + 1
                if f"references/{key}.md" in events:
                    guide_read[key] = guide_read.get(key, 0) + 1
    return {"turns": len(turns), "median_commands": statistics.median(per_turn) if per_turn else None,
            "partial": partial, "partial_followed": followed,
            "survey_turns": surveyed, "guide_seen": guide_seen, "guide_read": guide_read}


def main(argv):
    vault, rest = Path(argv[0]).expanduser(), argv[1:]
    groups = [(1, 999)]
    if "--groups" in rest:
        i = rest.index("--groups")
        groups = [tuple(int(x) for x in g.split("-")) for g in rest[i + 1].split(",")]
        rest = rest[:i] + rest[i + 2:]
    db = sqlite3.connect(vault / ".artmind" / "data" / "document_registry.db")
    for spec in rest:
        label, ids = spec.split("=", 1)
        for lo, hi in groups:
            print(json.dumps({"label": label, "group": f"{lo}-{hi}", **measure(db, ids.split(","), lo, hi)}))


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 2: Baselines from L, M, N**

```bash
uv run python scripts/p2b_gate.py ~/artmind_vaults/banking_corpus M=9a027799,ca13f53b N=70560495
```

```bash
uv run python scripts/p2b_gate.py ~/artmind_vaults/brain01 L=c160f6ed,224f7daf M=c3ffa221,fc8862be N=d6744aa5 --groups 1-14,15-29
```

Expected: one JSON line per label and group. Run L covered brain01 Q15–Q29 only, so its 1-14 line has `turns: 0`.

- [ ] **Step 3: Link the variant into both vaults**

The benchmark checks `<vault>/.claude/skills/artmind-query-p2b/SKILL.md` (`profiles.require_skill`). In each vault:

```bash
cd ~/artmind_vaults/banking_corpus && artmind init
```

```bash
cd ~/artmind_vaults/brain01 && artmind init
```

Then `ls -l ~/artmind_vaults/*/.claude/skills/ | grep p2b` shows two symlinks into this checkout.

- [ ] **Step 4: Restart both admin consoles**

They import code once at start (CLAUDE.md, Testing implications §1). Stop and start the two background admin-ui
processes (banking on port 8381, brain01 on 8379) with the same commands they were started with, from inside each
vault, with `env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT`.

- [ ] **Step 5: Ask the user before spending**

Ask: "Ready to run P2b: two banking runs and two brain01 runs (contract `p2b`). Go?" Wait for a yes.

- [ ] **Step 6: Run and grade**

Use the run scripts from runs L/M/N (`run_m.sh` pattern): POST `/api/benchmark/runs` with
`{"path": <questions file>, "name": "P2b-<vault>-<n>", "contract": "p2b", "backend": "claude-sdk"}`, poll
`/api/benchmark/runs/<id>` until `completed`, then grade:

```bash
artmind traces compare --baseline 61cf661f,15837ded,f03056a8 --variant <id8> --questions benchmarking/questions.md --judge --grade --model claude-sonnet-5-5 --allowJudge --compact
```

(banking, from the vault; brain01 uses `--baseline 49bc9ef6,d6c41e2c,7a1d832d,a984341d` and
`benchmarking/Benchmarking_questions_v2.md`). Run every LLM-spending command with
`env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT`.

- [ ] **Step 7: Read the gate**

For each group, check spec §7's four rows against L/M/N: must-item score inside or above the band and at or above
the floor (banking 67 %, brain01 Q01–Q14 55 %, Q15–Q29 76 %); median commands no higher; partial-followed rate
(`partial_followed / partial`) no lower; `survey_turns / turns` no lower. Re-run Step 2's commands with the P2b ids
added (`P2b=<id8>,<id8>`).

- [ ] **Step 8: Record and commit**

Add a "Run P2b" section to `docs/query-routing-study/22-p2-1-orient.md` (or a new note 23): run ids, the gate table,
the guide seen/read counts per key. Counts and ids only.

```bash
git add scripts/p2b_gate.py docs/query-routing-study/22-p2-1-orient.md
git commit -m "docs(study 22): run P2b and its gate"
```

Report the result to the user and ask whether to promote (Task 11) or not.

---

### Task 11: promotion (only on the user's yes after a pass)

**Files:**
- Create: `docs/archive/artmind-query-orient/` (SKILL.md, references, README.md)
- Modify: `artmind/skills/artmind-query/` (replaced by the variant), `test/test_query_skill_p2b.py`,
  `test/test_skill_references.py`, `test/test_query_orient_skill.py`, `test/test_query_skill_views_step.py`,
  `test/test_create_view_skill.py`, `artmind/opencode/agent/artmind.md`, `CLAUDE.md`

- [ ] **Step 1: Archive the orient text**

```bash
mkdir -p docs/archive/artmind-query-orient
cp -R artmind/skills/artmind-query/SKILL.md artmind/skills/artmind-query/references docs/archive/artmind-query-orient/
```

Write `docs/archive/artmind-query-orient/README.md`: "The orient text of artmind-query, live 2026-11-11 until P2b
was promoted on <date>. Kept for comparison runs (a contract other than classic needs its own
artmind-query-<name> skill folder)."

- [ ] **Step 2: Move the variant in**

```bash
git rm -r -q artmind/skills/artmind-query
git mv artmind/skills/artmind-query-p2b artmind/skills/artmind-query
sed -i '' 's/^name: artmind-query-p2b$/name: artmind-query/' artmind/skills/artmind-query/SKILL.md
just dev-skill-commands artmind-query
```

- [ ] **Step 3: Point the tests at the promoted paths**

- `test/test_query_skill_p2b.py`: `VARIANT = ROOT / "artmind" / "skills" / "artmind-query"`,
  `SOURCE = ROOT / "docs" / "archive" / "artmind-query-orient" / "SKILL.md"`, and the name check
  `^name: artmind-query$`. Rename the file to `test/test_query_skill.py` with `git mv`.
- `test/test_skill_references.py`: `SKILL_DIR = PACKAGE / "skills" / "artmind-query"`.
- `test/test_query_orient_skill.py` and `test/test_query_skill_views_step.py`: they pin the orient text's prose. Point
  their `SKILL` paths at `docs/archive/artmind-query-orient/SKILL.md` where the check is about the archived text,
  and delete checks whose phrase now lives in a reference and is covered by `test_query_skill.py`'s moved-text
  test. Keep `test_every_artmind_command_in_the_skill_exists` and `test_named_options_exist` pointed at the live skill.
- `test/test_create_view_skill.py`: keep its persona check; it reads `artmind.md`, not the skill.

Run: `just dev-test 2>&1 | tail -15`. Expected: all pass.

- [ ] **Step 4: Prompts that describe the protocol**

`grep -n "Views\|Resolve\|Retrieve" artmind/opencode/agent/artmind.md artmind/webui/profiles.py`. Where a sentence
names the skill's sections ("Orient → Parts → Views → Resolve, Retrieve, Ground, Adjudicate"), change it to "Orient →
Parts → the routing table, with references opened when a row or a result's `guide` names them". Re-run
`uv run --group dev pytest test/test_create_view_skill.py -q`.

- [ ] **Step 5: CLAUDE.md**

In the `artmind/orient/` row, replace "the orient text, promoted 2026-11-11; the classic text is archived at
`docs/archive/artmind-query-classic/`" with "the P2b text, promoted <date>: a short SKILL.md with a routing table and
references opened by a row or a result's `guide`; the orient text is archived at `docs/archive/artmind-query-orient/`,
the classic text at `docs/archive/artmind-query-classic/`".

- [ ] **Step 6: Commit**

```bash
git add -A artmind/skills/artmind-query artmind/skills/artmind-query-p2b docs/archive/artmind-query-orient test/ artmind/opencode/agent/artmind.md artmind/webui/profiles.py CLAUDE.md
git status --short
git commit -m "feat(skills): promote P2b to artmind-query; archive the orient text"
```

(`git add -A <paths>` is scoped to the listed paths so the rename and deletions are staged; check `git status
--short` shows nothing outside them.)

- [ ] **Step 7: Vault links and the run folder (ask first)**

The vaults' `artmind-query-p2b` symlinks now dangle. Ask the user before removing them:
`rm ~/artmind_vaults/banking_corpus/.claude/skills/artmind-query-p2b ~/artmind_vaults/brain01/.claude/skills/artmind-query-p2b`.
Then `cd ~ && artmind setup` refreshes the `~/.artmind` copies (a removed skill's copy there is never pruned; the
p2b one was never copied, since setup was not run while it existed).

---

### Task 12: spec and work plan

**Files:**
- Modify: `docs/superpowers/specs/2026-11-11-skill-progressive-disclosure-design.md`
- Modify: `docs/superpowers/specs/2026-11-08-classic-baseline-and-skill-simplification-design.md` (row E)

- [ ] **Step 1: Spec corrections from building it**

- Status line: "built <date>; measured (run P2b, study note 22); promoted <date> / not promoted".
- §5.1 `survey` row: "any rows" (the agent always passes `--shape brief`, so the three listed conditions reduce to
  that).
- §5.2: the `_echo_json` wiring test lives in `test/test_guides.py`, not `test_cli_conventions.py`.
- §5.3: spans also keep the first `next` (the gate and E2 read it).
- §3: the measured SKILL.md length.

- [ ] **Step 2: Work plan row E**

Mark it done with the date and the gate result, as row C was.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-11-11-skill-progressive-disclosure-design.md docs/superpowers/specs/2026-11-08-classic-baseline-and-skill-simplification-design.md
git commit -m "docs(spec): P2b built and measured"
```
