# Phase 0–1: freeze and classic baseline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `baseline/v0.11-classic` (cut from `v0.11.0`) into the measured classic baseline of spec
`docs/superpowers/specs/2026-11-08-classic-baseline-and-skill-simplification-design.md` (§2, §3): the carry-over
fixes, a skill-variant switch, `traces compare` with pairwise and absolute judging, no new planner dependency,
15 new brain01 questions, tiered expected answers, and a recorded classic band.

**Architecture:** Small commits on one branch. Carried fixes come over by `git cherry-pick` from
`archive/p3-bundle`; the comparison tooling is rebuilt from the archive's modules, decoupled from
`artmind/query_plan/` through a new `artmind/benchmark_labels.py`. A test pins the planner seams so later work
cannot add to them. Measurement runs are started by the user in the admin console; judging is a CLI call.

**Tech Stack:** Python ≥ 3.14 with `uv`, Click, pytest (`just dev-test`), SQLite registry (`artmind/db.py`), the
configured LLM provider for judging (ibm_ica).

**Ground rules for every task**
- Work in `~/projects/artmind9` on branch `baseline/v0.11-classic`. Run tests with
  `uv run --group dev pytest <path> -v`. The full suite is `just dev-test`.
- `zsh` treats `name:a...` in `git show REV:path` as a modifier: always quote, e.g.
  `git show 'archive/p3-bundle:artmind/traces/judge.py'`.
- Never import from `artmind.query_plan` in new code (Task 12 enforces it).
- Privacy: this checkout is public. Test data is invented. Questions, answers, expected answers and judge reasons
  from vaults never enter the repo.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## File map

| File | Change | Responsibility |
|---|---|---|
| `artmind/graph_query.py`, `artmind/skills/artmind-query/SKILL.md` | cherry-pick B9 | `views` / `insights` per domain |
| `artmind/traces/span.py`, `analysis.py`, `path.py`, `cli.py` | cherry-pick B8, `96e28582` | `db` spans; SystemExit code |
| `artmind/db.py` | cherry-picks + Task 6 | migration lock; `contract` columns; `trace_judgements`, `trace_grades` |
| `artmind/webui/profiles.py`, `app.py`, `benchmark_routes.py`, `benchmark.py`, `static/benchmark.js`, `templates/dashboard.html`, `traces/recorder.py`, `traces/store.py` | cherry-pick S9 plumbing + Task 3 | skill-variant switch |
| `artmind/benchmark_labels.py` | create | label loader + document matching, planner-free |
| `artmind/retrieval_eval/labels.py` | modify | import `vault_dir` from `benchmark_labels` |
| `artmind/traces/compare.py` | create (from archive) | read runs, pair by question text, measures, answer reach |
| `artmind/traces/judge.py` | create (from archive) | pairwise judge, judging ceiling |
| `artmind/traces/tiers.py` | create | parse must / optional / must-not from an evaluation comment |
| `artmind/traces/grade.py` | create | absolute grade of one answer against its tiers, cached |
| `artmind/traces/gate.py` | create | band from baseline pairs; variant evaluation |
| `artmind/cli.py` | modify | `traces compare`; `survey --passages` cap |
| `artmind/query_plan/shadow.py` | modify | shadow off by default |
| `test/test_no_planner_entanglement.py` | create | pins planner import seams |
| `CLAUDE.md`, `artmind/env.example` | modify | document the above |

---

## Phase 0 (remaining)

Done already on 2026-11-08: tag `archive/p3-bundle` (local) on `c088af56`; main checkout switched to
`baseline/v0.11-classic`; both vaults' `config.env` set `ARTMIND_PLAN_SHADOW=0` and dropped
`ARTMIND_PLAN_SHADOW_RUN`.

### Task 0: finish the freeze

- [ ] **Step 1: Ask the user before pushing the tag.** Pushing `archive/p3-bundle` publishes the P3 commits
  (never pushed so far) to the public GitHub repo. With a yes:

```bash
git push origin archive/p3-bundle
```

Expected: `* [new tag] archive/p3-bundle -> archive/p3-bundle`.

- [ ] **Step 2: The user restarts both admin consoles from Obsidian.** The running `admin-ui` processes (ports
  8379, 8381) imported P3 code at start. Check afterwards:

```bash
pgrep -fl 'artmind (serve|admin-ui|chat-ui)'
```

Expected: new pids, started after the switch.

- [ ] **Step 3: Check one traced admin turn has no shadow plan.** After the user asks one question in each vault's
  admin console, take that turn's trace id from the console and run:

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind traces show <trace_id> --compact | grep -c plan_shadow
```

Expected: `0`.

---

## Phase 1: carry-over

### Task 1: cherry-pick B9, B8 and the SystemExit fix

**Files:** as in the commits (`graph_query.py`, the classic skill, `CLAUDE.md`, `traces/span.py`,
`traces/analysis.py`, `traces/path.py`, `cli.py`) and their tests.

- [ ] **Step 1: Cherry-pick**

```bash
git cherry-pick -x 5b3f7853 d5a6e7de 96e28582
```

Expected: three commits applied. On a conflict in `CLAUDE.md`, keep the `v0.11.0` text and add only the commit's
new sentence; `git cherry-pick --continue`.

- [ ] **Step 2: Run their tests**

```bash
uv run --group dev pytest test/test_domains_overview_views.py test/test_traces_analysis.py test/test_traces_path.py test/test_traces_plan_shadow.py test/test_traces_span.py -v
```

Expected: all pass.

### Task 2: cherry-pick the contract plumbing and the migration lock

**Files:** `artmind/benchmark.py`, `artmind/db.py`, `artmind/traces/recorder.py`, `artmind/traces/store.py`,
`artmind/webui/profiles.py`, `artmind/webui/app.py`, `artmind/webui/benchmark_routes.py`,
`artmind/webui/static/benchmark.js`, `artmind/webui/templates/dashboard.html`; tests
`test/test_agent_contract_store.py`, `test/test_agent_contract.py`, `test/test_agent_contract_surfaces.py`,
`test/test_webui_benchmark_api.py`, `test/test_db_init_concurrency.py`.

- [ ] **Step 1: Cherry-pick in this order** (the lock fix last, since its context includes the contract columns)

```bash
git cherry-pick -x c63afc7e f32001c9 41e478d9 ab1f0879
```

On a conflict in `artmind/db.py`: keep every column and table of both sides; the result must open the
transaction with `conn.execute("BEGIN IMMEDIATE")` around `_create_schema(conn.cursor())` exactly as `ab1f0879`
shows (`git show ab1f0879 -- artmind/db.py`). In `trace_spans`' column loop keep only `("payload_chars",
"INTEGER"), ("shown_chars", "INTEGER"), ("handle", "TEXT")`; the parent-span columns belong to the frozen executor.

- [ ] **Step 2: Run the tests**

```bash
uv run --group dev pytest test/test_agent_contract_store.py test/test_agent_contract.py test/test_agent_contract_surfaces.py test/test_webui_benchmark_api.py test/test_db_init_concurrency.py -v
```

Expected: pass, except tests asserting the bundle contract; Task 3 replaces those.

- [ ] **Step 3: Commit any conflict fixes** (only if Step 1 needed manual edits that `--continue` did not commit)

```bash
git commit -am "fix: resolve carry-over conflicts for the contract plumbing and migration lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: the contract becomes a skill variant

A variant `classic` uses `artmind-query`; any other variant `X` uses the skill folder `artmind-query-X`, which must
be installed where agents read skills. The DB column keeps the name `contract`.

**Files:**
- Modify: `artmind/webui/profiles.py` (the block added by `f32001c9`, from `CONTRACTS = ...` to the end)
- Modify: `artmind/webui/templates/dashboard.html` (the `benchmark-contract` select)
- Test: `test/test_agent_contract.py` (replace), `test/test_agent_contract_surfaces.py` (edit)

- [ ] **Step 1: Replace `test/test_agent_contract.py` with the variant tests**

```python
"""Skill variants (spec 2026-11-08 §2.2): `classic` is artmind-query; variant X is artmind-query-X."""

import pytest

from artmind.webui import profiles
from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, skill_for, variant_from_env, with_variant


def test_classic_is_the_plain_skill():
    assert skill_for("classic") == "artmind-query"
    assert skill_for("p2-1") == "artmind-query-p2-1"


@pytest.mark.parametrize("bad", ["", "Bad", "a b", "../x", "-x", "x" * 41])
def test_variant_names_are_checked(bad):
    with pytest.raises(ValueError):
        skill_for(bad)


def test_with_variant_swaps_the_query_skill_and_records_the_variant():
    p = with_variant(BENCHMARK_PROFILE, "p2-1")
    assert "artmind-query-p2-1" in p.skills and "artmind-query" not in p.skills
    assert p.contract == "p2-1"
    assert "artmind-query-p2-1" in p.system_append


def test_with_variant_classic_is_identity():
    assert with_variant(ADMIN_PROFILE, "classic") is ADMIN_PROFILE


def test_variant_from_env(monkeypatch):
    monkeypatch.delenv(profiles.CONTRACT_ENV, raising=False)
    assert variant_from_env() == "classic"
    monkeypatch.setenv(profiles.CONTRACT_ENV, "p2-1")
    assert variant_from_env() == "p2-1"
    monkeypatch.setenv(profiles.CONTRACT_ENV, "Not Valid")
    with pytest.raises(ValueError):
        variant_from_env()


def test_require_skill_checks_the_variant_folder(tmp_path, monkeypatch):
    monkeypatch.setattr("paths.ARTMIND_AGENT_CWD", tmp_path)
    profiles.require_skill(with_variant(BENCHMARK_PROFILE, "classic"))  # classic never checked
    with pytest.raises(ValueError, match="artmind-query-p2-1"):
        profiles.require_skill(with_variant(BENCHMARK_PROFILE, "p2-1"))
    skill = tmp_path / ".claude" / "skills" / "artmind-query-p2-1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    profiles.require_skill(with_variant(BENCHMARK_PROFILE, "p2-1"))
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_agent_contract.py -v
```

Expected: FAIL, `ImportError: cannot import name 'skill_for'`.

- [ ] **Step 3: Replace the `f32001c9` block in `artmind/webui/profiles.py`** (from `CONTRACTS = ("classic",
  "bundle")` to the end of `require_skill`) with:

```python
CONTRACT_ENV = "ARTMIND_AGENT_CONTRACT"
CLASSIC = "classic"
_VARIANT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def skill_for(variant: str) -> str:
    """The query skill folder of a skill variant: classic is artmind-query, X is artmind-query-X."""
    if not isinstance(variant, str) or not _VARIANT_RE.fullmatch(variant):
        raise ValueError(f"a skill variant is lowercase letters, digits and dashes (max 40), got {variant!r}")
    return "artmind-query" if variant == CLASSIC else f"artmind-query-{variant}"


def variant_from_env() -> str:
    raw = (os.environ.get(CONTRACT_ENV) or "").strip()
    if not raw:
        return CLASSIC
    skill_for(raw)  # validates
    return raw


def with_variant(profile: AgentProfile, variant: str) -> AgentProfile:
    """`profile` answering with the query skill of `variant` (spec 2026-11-08 §2.2)."""
    new = skill_for(variant)
    if variant == profile.contract:
        return profile
    old = skill_for(profile.contract)
    skills = tuple(new if s == old else s for s in profile.skills)
    append = profile.system_append.replace(f"{old} for", f"{new} for")
    return dataclasses.replace(profile, skills=skills, system_append=append, contract=variant)


def require_skill(profile: AgentProfile) -> None:
    """Raise ValueError when a non-classic variant's skill is not installed where agents look for skills."""
    if profile.contract == CLASSIC:
        return
    import paths

    name = skill_for(profile.contract)
    if not (paths.ARTMIND_AGENT_CWD / ".claude" / "skills" / name / "SKILL.md").exists():
        raise ValueError(f"skill variant {profile.contract!r} needs the {name} skill in this vault's .claude/skills")


# Kept names for the cherry-picked callers in app.py and benchmark_routes.py.
contract_from_env = variant_from_env
with_contract = with_variant
```

Add `import re` beside `import os` at the top of the module.

In `artmind/benchmark.py`, `_create_benchmark_run` (from `c63afc7e`) validates with the removed `CONTRACTS`.
Replace these lines:

```python
    from artmind.webui.profiles import CONTRACTS

    if contract not in CONTRACTS:
        raise ValueError(f"contract must be one of {', '.join(CONTRACTS)}, got {contract!r}")
```

with:

```python
    from artmind.webui.profiles import skill_for

    skill_for(contract)  # raises ValueError for a name that is not a skill variant
```

and add `artmind/benchmark.py` to this task's `git add`.

- [ ] **Step 4: Make the benchmark form free-text.** In `artmind/webui/templates/dashboard.html`, replace the
  `<select id="benchmark-contract">…</select>` block with:

```html
          <input id="benchmark-contract" type="text" value="classic" pattern="[a-z0-9][a-z0-9-]{0,39}"
                 title="Skill variant: classic, or X for the artmind-query-X skill">
```

`static/benchmark.js` already reads `.value`, so it needs no change.

- [ ] **Step 5: Edit `test/test_agent_contract_surfaces.py`.** Replace every `"bundle"` with `"p2-1"`. In the
  test that expects a missing-skill refusal, create no skill folder (the refusal is still expected). In
  `test_the_run_route_refuses_an_unknown_contract`, change `"fast"` to `"Not Valid"`. Where a test expects the
  run to be accepted with `"p2-1"`, create the skill first:

```python
    skill = tmp_path / ".claude" / "skills" / "artmind-query-p2-1"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    monkeypatch.setattr("paths.ARTMIND_AGENT_CWD", tmp_path)
```

- [ ] **Step 6: Run the tests**

```bash
uv run --group dev pytest test/test_agent_contract.py test/test_agent_contract_surfaces.py test/test_agent_contract_store.py test/test_webui_benchmark_api.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add artmind/webui/profiles.py artmind/benchmark.py artmind/webui/templates/dashboard.html test/test_agent_contract.py test/test_agent_contract_surfaces.py
git commit -m "feat(webui): the agent contract is a skill variant (classic or artmind-query-<variant>)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: `artmind/benchmark_labels.py`

**Files:**
- Create: `artmind/benchmark_labels.py`
- Modify: `artmind/retrieval_eval/labels.py:16`
- Test: `test/test_benchmark_labels.py`

- [ ] **Step 1: Write the failing test**

```python
"""Benchmark labels are read without the planner: question + expected_documents only. Invented data."""

import pytest

from artmind import benchmark_labels as bl


def _write(tmp_path, text):
    p = tmp_path / "plan_labels.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_reads_question_and_expected_documents_and_ignores_planner_keys(tmp_path):
    p = _write(tmp_path, """
version: 2
labels:
  - id: L001
    question: Which invented policy applies?
    route: episode
    follows: none
    intent: retrieve
    expected_documents: [policies/alpha.md, beta.md]
  - id: L002
    question: No documents here?
""")
    labels = bl.load_labels(p)
    assert [(x.id, x.question, x.expected_documents) for x in labels] == [
        ("L001", "Which invented policy applies?", ("policies/alpha.md", "beta.md")),
        ("L002", "No documents here?", ())]


@pytest.mark.parametrize("text, msg", [
    ("labels: 3", "labels"),
    ("labels:\n  - id: L1\n", "no question"),
    ("labels:\n  - question: q\n    expected_documents: [1]\n", "expected_documents"),
    ("labels: [", "plan_labels.yaml"),
])
def test_bad_files_raise(tmp_path, text, msg):
    with pytest.raises(bl.LabelError, match=msg):
        bl.load_labels(_write(tmp_path, text))


def test_document_matching():
    assert bl.match_kind("policies/alpha.md", {"path": "vault/policies/alpha.md"}) == "path"
    assert bl.match_kind("x/alpha.md", {"path": "y/alpha.md"}) == "basename"
    assert bl.match_kind("x/alpha.md", {"title": "alpha"}) == "title"
    assert bl.match_kind("alpha.md", {"path": "beta.md"}) is None
    assert bl.reached_documents(["a.md", "b.md"], [{"path": "a.md"}]) == (["a.md"], ["b.md"])


def test_paths(monkeypatch, tmp_path):
    monkeypatch.setattr("paths.ARTMIND_VAULT_DIR", str(tmp_path), raising=False)
    assert bl.vault_dir() == tmp_path
    assert bl.default_path() == tmp_path / "benchmarking" / "plan_labels.yaml"
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_benchmark_labels.py -v
```

Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.benchmark_labels'`.

- [ ] **Step 3: Create `artmind/benchmark_labels.py`**

```python
"""Benchmark labels: the expected documents of benchmark questions (spec 2026-11-08 §2.3).

Read from <vault>/benchmarking/plan_labels.yaml. Only `id`, `question` and `expected_documents` are read; every
other key (written by the frozen planner's tooling) is ignored, so this module never imports artmind.query_plan.
Private: the file holds real questions and stays in the vault.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

import yaml


class LabelError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkLabel:
    id: str
    question: str
    expected_documents: tuple[str, ...] = ()


def vault_dir() -> Path | None:
    import paths

    vault = getattr(paths, "ARTMIND_VAULT_DIR", None)
    return Path(vault) if vault else None


def default_path() -> Path | None:
    vault = vault_dir()
    return vault / "benchmarking" / "plan_labels.yaml" if vault else None


def load_labels(path: Path) -> list[BenchmarkLabel]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise LabelError(f"{path}: {exc}") from exc
    rows = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise LabelError(f"{path}: no `labels:` list")
    out = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise LabelError(f"{path}: label {i} is not a mapping")
        lid = str(row.get("id") or f"#{i}")
        question = str(row.get("question") or "").strip()
        if not question:
            raise LabelError(f"{path}: label {lid} has no question")
        docs = row.get("expected_documents") or []
        if not isinstance(docs, list) or not all(isinstance(d, str) for d in docs):
            raise LabelError(f"{path}: label {lid}: expected_documents must be a list of paths")
        out.append(BenchmarkLabel(lid, question, tuple(docs)))
    return out


def _norm(value) -> str:
    text = str(value or "").replace("\\", "/").strip().casefold()
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _stem(name: str) -> str:
    return posixpath.splitext(posixpath.basename(name))[0]


def match_kind(expected: str, doc: dict) -> str | None:
    """How `doc` matches the expected entry: "path" (equal, or one a trailing path of the other, in
    `path` / `source_path` / `name` / `id`), "basename" (same file name), "title" (the title equals the file
    name without its extension; brief rows carry only that), or None."""
    want = _norm(expected)
    if not want:
        return None
    paths = [_norm(doc.get(k)) for k in ("path", "source_path", "name", "id") if doc.get(k)]
    for have in paths:
        if have == want or have.endswith("/" + want) or (have.count("/") and want.endswith("/" + have)):
            return "path"
    base = posixpath.basename(want)
    if any(posixpath.basename(have) == base for have in paths):
        return "basename"
    title = _norm(doc.get("title"))
    if title and title in (_stem(want), base):
        return "title"
    return None


def reached_documents(expected, docs: list[dict]) -> tuple[list[str], list[str]]:
    """(matched, missed) of the expected entries against the documents."""
    matched, missed = [], []
    for want in expected:
        (matched if any(match_kind(want, d) for d in docs) else missed).append(want)
    return matched, missed
```

- [ ] **Step 4: Point `retrieval_eval` at it.** In `artmind/retrieval_eval/labels.py` replace
  `from artmind.query_plan.labels import vault_dir` with `from artmind.benchmark_labels import vault_dir`.

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_benchmark_labels.py test/ -k "retrieval" -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add artmind/benchmark_labels.py artmind/retrieval_eval/labels.py test/test_benchmark_labels.py
git commit -m "feat: benchmark labels and document matching without the planner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: `artmind/traces/compare.py` (from the archive, decoupled, paired by question text)

**Files:**
- Create: `artmind/traces/compare.py`
- Test: `test/test_traces_compare.py`

- [ ] **Step 1: Bring the archive's module and tests**

```bash
git show 'archive/p3-bundle:artmind/traces/compare.py' > artmind/traces/compare.py
git show 'archive/p3-bundle:test/test_traces_compare.py' > test/test_traces_compare.py
```

- [ ] **Step 2: Add the new behaviour's tests** to the end of `test/test_traces_compare.py`, and delete its
  `test_runs_of_different_question_files_do_not_pair`:

```python
def test_runs_pair_on_shared_question_text_and_report_the_rest():
    from artmind import benchmark

    a = _run("a", "classic", ["a1", "a2"])
    qs = [{"seq": 1, "qid": "N1", "title": "t", "question": "a new invented question?", "eval_comment": None},
          {"seq": 2, "qid": "Q02", "title": "t", "question": "Invented  question 2?", "eval_comment": "note 2"}]
    b = benchmark._create_benchmark_run("/tmp/q2.md", qs)
    pairs, only_left, only_right = compare.pair_by_text(*compare.load_runs([a, b]))
    assert [(p.seq, p.qid) for p in pairs] == [(2, "Q02")]
    assert only_left == ["Q01"] and only_right == ["N1"]


def test_expected_answers_can_come_from_the_current_questions_file():
    a = _run("a", "classic", ["a1"])
    b = _run("b", "classic", ["b1"])
    left, right = compare.load_runs([a, b])
    pairs, _, _ = compare.pair_by_text(left, right, expected={"invented question 1?": "Must: fact one"})
    assert pairs[0].expected == "Must: fact one"


def test_no_parent_span_column_is_needed():
    a = _run("a", "classic", ["x"], spans={1: [["d1"], ["d2"]]})
    assert compare.load_runs([a])[0].answers[1].commands == 2
```

In `test_runs_pair_by_seq_and_carry_their_contract`, change `"bundle"` to `"p2-1"` and `compare.pair(` to
`compare.pair_by_text(` with `pairs, _, _ =`.

- [ ] **Step 3: Run to see the new tests fail**

```bash
uv run --group dev pytest test/test_traces_compare.py -v
```

Expected: FAIL on `pair_by_text` missing and on the `parent_span_id` column (`sqlite3.OperationalError: no such
column`).

- [ ] **Step 4: Edit `artmind/traces/compare.py`**
  1. Module docstring first line becomes `"""Comparing benchmark runs (spec 2026-11-08 §3).`.
  2. In `load_runs`, replace `"SELECT evidence FROM trace_spans WHERE trace_id = ? AND parent_span_id IS NULL"` with
     `"SELECT evidence FROM trace_spans WHERE trace_id = ?"`.
  3. Replace the whole `pair` function with:

```python
def _norm_q(text) -> str:
    return " ".join(str(text or "").casefold().split())


def pair_by_text(left: Run, right: Run, *, expected: dict[str, str] | None = None
                 ) -> tuple[list[Pair], list[str], list[str]]:
    """Pairs of answers to the same question text (whitespace and case folded), in `left`'s order, and the qids
    asked in only one run. `expected` maps a folded question text to an expected-answer note that overrides the
    note stored with `left`'s run (the current questions file, spec §2.5)."""
    expected = {_norm_q(k): v for k, v in (expected or {}).items()}
    right_by = {_norm_q(q["question"]): s for s, q in right.questions.items()}
    pairs, only_left = [], []
    for s in sorted(left.questions):
        q = left.questions[s]
        key = _norm_q(q["question"])
        rs = right_by.pop(key, None)
        if rs is None:
            only_left.append(q["qid"] or str(s))
            continue
        pairs.append(Pair(s, q["qid"], q["question"], expected.get(key, q["eval_comment"]),
                          left.answers[s], right.answers[rs]))
    only_right = [right.questions[s]["qid"] or str(s) for s in sorted(right_by.values())]
    return pairs, only_left, only_right
```

  4. In `answer_reach`, replace `from artmind.query_plan import reach as reach_mod` with
     `from artmind import benchmark_labels as reach_mod`.

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_traces_compare.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/compare.py test/test_traces_compare.py
git commit -m "feat(traces): compare benchmark runs paired by question text, without the planner

Carried from archive/p3-bundle (d0c85b21, 9187448f, 21c46a04) and decoupled.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: the pairwise judge with a judging ceiling

**Files:**
- Create: `artmind/traces/judge.py`
- Modify: `artmind/db.py` (the `trace_judgements` table; no carried commit brings it)
- Test: `test/test_traces_judge.py`

- [ ] **Step 0: Add the table** in `artmind/db.py`'s `_create_schema`, directly after the `trace_llm_calls`
  `CREATE TABLE` block:

```python
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_judgements (
            key          TEXT PRIMARY KEY,
            left_run     TEXT NOT NULL,
            right_run    TEXT NOT NULL,
            seq          INTEGER NOT NULL,
            model        TEXT,
            verdict      TEXT NOT NULL,
            better       TEXT,
            reason       TEXT,
            created_at   REAL NOT NULL
        )
    """)
```

Vault registries written by the archive build already hold this table; `IF NOT EXISTS` keeps their cached verdicts.

- [ ] **Step 1: Bring the archive's module and tests**

```bash
git show 'archive/p3-bundle:artmind/traces/judge.py' > artmind/traces/judge.py
git show 'archive/p3-bundle:test/test_traces_judge.py' > test/test_traces_judge.py
```

- [ ] **Step 2: Add the ceiling tests** to `test/test_traces_judge.py`:

```python
def test_long_answers_are_sent_whole():
    long = "x" * 20_000
    text = judge.prompt("q?", None, long, "short")
    assert long in text


def test_an_answer_over_the_ceiling_is_unjudged_never_cut():
    calls = []
    v = judge.judge_pair("q?", None, "y" * (judge.ANSWER_CEILING + 1), "short",
                         llm=lambda p: calls.append(p) or "{}", model="m")
    assert v.verdict == "unjudged" and "ceiling" in v.reason and calls == []
```

- [ ] **Step 3: Run to see them fail**

```bash
uv run --group dev pytest test/test_traces_judge.py -v
```

Expected: FAIL (`ANSWER_CEILING` missing; the 20,000-character answer is cut).

- [ ] **Step 4: Edit `artmind/traces/judge.py`**
  1. Docstring first line: `"""The pairwise answer judge (spec 2026-11-08 §3.2).`
  2. Replace `PROMPT_VERSION = 1` with `PROMPT_VERSION = 2` and `ANSWER_CHARS = 8000` with
     `ANSWER_CEILING = 40_000  # answers are judged whole; one over this is unjudged, never cut`.
  3. Replace `prompt` with:

```python
def prompt(question: str, expected: str | None, a: str, b: str) -> str:
    return TEMPLATE.format(question=question, expected=expected or "(none)", a=a or "", b=b or "")
```

  4. In `judge_pair`, directly after the empty-answer checks, insert:

```python
    if len(left) > ANSWER_CEILING or len(right) > ANSWER_CEILING:
        return Verdict("unjudged", None, f"an answer is over the judging ceiling of {ANSWER_CEILING} characters")
```

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_traces_judge.py -v
```

Expected: all pass. If a carried test asserts the 8,000 cut, delete that test.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/judge.py test/test_traces_judge.py artmind/db.py
git commit -m "feat(traces): pairwise answer judge; answers judged whole up to a 40,000-character ceiling

Carried from archive/p3-bundle (fa934078).

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 7: tiered expected answers

Format inside a question's `**Evaluation comment:**` (one item per `;`, or one per `- ` line under a tier):

```
Must: fact one; fact two
Optional: fact three
Must-not: a wrong claim
Notes: free text for the grader
```

**Files:**
- Create: `artmind/traces/tiers.py`
- Test: `test/test_traces_tiers.py`

- [ ] **Step 1: Write the failing test**

```python
from artmind.traces import tiers


def test_inline_and_listed_items():
    t = tiers.parse("Must: a; b\nOptional: c\nMust-not: d\n- e\nNotes: be strict\nalso this")
    assert t.must == ("a", "b") and t.optional == ("c",) and t.must_not == ("d", "e")
    assert t.notes == "be strict\nalso this" and t.tiered


def test_spellings():
    t = tiers.parse("**Must:** x\nmust not: y\nMUST_NOT: z")
    assert t.must == ("x",) and t.must_not == ("y", "z")


def test_untiered_text_is_notes_only():
    t = tiers.parse("expect the 2024 figure")
    assert not t.tiered and t.notes == "expect the 2024 figure"
    assert not tiers.parse(None).tiered
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_traces_tiers.py -v
```

Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Create `artmind/traces/tiers.py`**

```python
"""Tiered expected answers (spec 2026-11-08 §2.5): must / optional / must-not items in a question's evaluation
comment. Untiered text is kept as notes, so old comments still read."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEAD = re.compile(r"^\s*(?:\*\*)?\s*(must[\s_-]*not|must|optional|notes?)\s*:?\s*(?:\*\*)?\s*:?\s*(.*)$",
                   re.IGNORECASE)
_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")


@dataclass(frozen=True)
class Tiers:
    must: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()
    notes: str = ""

    @property
    def tiered(self) -> bool:
        return bool(self.must or self.optional or self.must_not)


def _key(word: str) -> str:
    w = re.sub(r"[\s_-]+", "", word.casefold())
    return {"must": "must", "mustnot": "must_not", "optional": "optional"}.get(w, "notes")


def _split(text: str) -> list[str]:
    return [p.strip() for p in text.split(";") if p.strip()]


def parse(text: str | None) -> Tiers:
    found = {"must": [], "optional": [], "must_not": [], "notes": []}
    current = "notes"
    for line in (text or "").splitlines():
        head = _HEAD.match(line)
        if head:
            current = _key(head.group(1))
            rest = head.group(2).strip()
            if rest:
                found[current].extend([rest] if current == "notes" else _split(rest))
            continue
        item = _ITEM.match(line)
        if item and current != "notes":
            found[current].extend(_split(item.group(1)))
        elif line.strip():
            found["notes"].append(line.strip())
    return Tiers(tuple(found["must"]), tuple(found["optional"]), tuple(found["must_not"]),
                 "\n".join(found["notes"]))
```

- [ ] **Step 4: Run the tests**

```bash
uv run --group dev pytest test/test_traces_tiers.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/tiers.py test/test_traces_tiers.py
git commit -m "feat(traces): parse tiered expected answers from evaluation comments

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: the absolute grade

**Files:**
- Create: `artmind/traces/grade.py`
- Modify: `artmind/db.py` (new table, next to `trace_judgements`)
- Test: `test/test_traces_grade.py`

- [ ] **Step 1: Write the failing test**

```python
import json

import pytest

from artmind.traces import grade
from artmind.traces.tiers import Tiers

T = Tiers(must=("fact one", "fact two"), optional=("fact three",), must_not=("wrong claim",))


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _llm(payload):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return json.dumps(payload)
    return llm, calls


def test_grade_reads_items_by_position():
    llm, _ = _llm({"must": [True, False], "must_not": [False], "grade": "partial", "reason": "r"})
    g = grade.grade_answer("q?", T, "an answer", llm=llm)
    assert g.grade == "partial" and g.must_found == ("fact one",) and g.must_missed == ("fact two",)
    assert g.must_not_hit == ()


def test_a_must_not_hit_is_never_correct():
    llm, _ = _llm({"must": [True, True], "must_not": [True], "grade": "correct", "reason": "r"})
    assert grade.grade_answer("q?", T, "a", llm=llm).grade == "partial"


def test_untiered_empty_and_over_ceiling():
    llm, calls = _llm({})
    assert grade.grade_answer("q?", Tiers(notes="x"), "a", llm=llm).grade == "ungraded"
    empty = grade.grade_answer("q?", T, "  ", llm=llm)
    assert empty.grade == "wrong" and empty.must_missed == T.must
    assert grade.grade_answer("q?", T, "y" * (grade.ANSWER_CEILING + 1), llm=llm).grade == "ungraded"
    assert calls == []


def test_bad_judge_output_is_ungraded():
    llm, _ = _llm({"must": [True], "grade": "excellent"})
    assert grade.grade_answer("q?", T, "a", llm=llm).grade == "ungraded"


def test_cached_grade_calls_once():
    llm, calls = _llm({"must": [True, True], "must_not": [False], "grade": "correct", "reason": "r"})
    g1 = grade.cached_grade("run1", 1, "q?", T, "a", llm=llm, model="m")
    g2 = grade.cached_grade("run1", 1, "q?", T, "a", llm=llm, model="m")
    assert g1 == g2 and g1.grade == "correct" and len(calls) == 1
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_traces_grade.py -v
```

Expected: FAIL, `ModuleNotFoundError`.

- [ ] **Step 3: Add the table in `artmind/db.py`**, directly after the `trace_judgements` `CREATE TABLE`:

```python
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trace_grades (
            key          TEXT PRIMARY KEY,
            run_id       TEXT NOT NULL,
            seq          INTEGER NOT NULL,
            model        TEXT,
            grade        TEXT NOT NULL,
            detail       TEXT NOT NULL,
            created_at   REAL NOT NULL
        )
    """)
```

- [ ] **Step 4: Create `artmind/traces/grade.py`**

```python
"""The absolute grade (spec 2026-11-08 §3.2): one LLM call per answer against its tiered expected answer.

Each must item found or missing, each must-not item present or absent, and correct | partial | wrong. Cached in
`trace_grades` by a hash of (model, PROMPT_VERSION, question, tiers, answer). Sends the question, the tiers and
the answer to the configured LLM provider.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

from artmind.traces.tiers import Tiers

PROMPT_VERSION = 1
ANSWER_CEILING = 40_000
GRADES = ("correct", "partial", "wrong")

TEMPLATE = """You grade one answer from a question-answering system over a document corpus.

Question:
{question}

Items a correct answer MUST contain (numbered):
{must}

Items that may appear (no credit needed):
{optional}

Items the answer must NOT contain (numbered):
{must_not}

Grader's notes:
{notes}

Answer:
{answer}

For each MUST item say whether the answer states it (same meaning counts; wording may differ). For each MUST NOT
item say whether the answer states it. Then grade: "correct" (every must item, no must-not item), "partial"
(some must items), "wrong" (none, or the answer's main claim is false).

Return only JSON: {{"must": [true|false, ...], "must_not": [true|false, ...], "grade": "correct|partial|wrong",
"reason": "<one sentence>"}}"""


@dataclass(frozen=True)
class Grade:
    grade: str                      # correct | partial | wrong | ungraded
    must_found: tuple[str, ...]
    must_missed: tuple[str, ...]
    must_not_hit: tuple[str, ...]
    reason: str


def _numbered(items) -> str:
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1)) or "(none)"


def prompt(question: str, tiers: Tiers, answer: str) -> str:
    return TEMPLATE.format(question=question, must=_numbered(tiers.must), optional=_numbered(tiers.optional),
                           must_not=_numbered(tiers.must_not), notes=tiers.notes or "(none)", answer=answer)


def _ungraded(tiers: Tiers, reason: str) -> Grade:
    return Grade("ungraded", (), (), (), reason)


def grade_answer(question: str, tiers: Tiers, answer: str | None, *, llm) -> Grade:
    if not tiers.must:
        return _ungraded(tiers, "no tiered expected answer with must items")
    answer = answer or ""
    if not answer.strip():
        return Grade("wrong", (), tiers.must, (), "the answer is empty")
    if len(answer) > ANSWER_CEILING:
        return _ungraded(tiers, f"the answer is over the judging ceiling of {ANSWER_CEILING} characters")
    try:
        from artmind.extraction import parse_json_response

        data = parse_json_response(llm(prompt(question, tiers, answer)))
    except Exception:
        return _ungraded(tiers, "the grader call failed")
    must = data.get("must") if isinstance(data, dict) else None
    must_not = data.get("must_not") if isinstance(data, dict) else None
    g = str((data or {}).get("grade") or "").strip().casefold() if isinstance(data, dict) else ""
    if (not isinstance(must, list) or len(must) != len(tiers.must) or g not in GRADES
            or not isinstance(must_not or [], list) or len(must_not or []) != len(tiers.must_not)):
        return _ungraded(tiers, "the grader's answer was not in the expected form")
    found = tuple(x for x, ok in zip(tiers.must, must) if ok is True)
    missed = tuple(x for x, ok in zip(tiers.must, must) if ok is not True)
    hit = tuple(x for x, ok in zip(tiers.must_not, must_not or []) if ok is True)
    if g == "correct" and (missed or hit):
        g = "partial"
    return Grade(g, found, missed, hit, str(data.get("reason") or "")[:500])


def _key(model, question, tiers: Tiers, answer) -> str:
    raw = json.dumps([PROMPT_VERSION, model, question, tiers.must, tiers.optional, tiers.must_not, tiers.notes,
                      answer], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached_grade(run_id: str, seq: int, question: str, tiers: Tiers, answer: str | None, *, llm, model: str) -> Grade:
    from artmind.traces import store

    key = _key(model, question, tiers, answer)
    conn = store._connect()
    try:
        row = conn.execute("SELECT detail FROM trace_grades WHERE key = ?", (key,)).fetchone()
        if row:
            d = json.loads(row[0])
            return Grade(d["grade"], tuple(d["must_found"]), tuple(d["must_missed"]), tuple(d["must_not_hit"]),
                         d["reason"])
        g = grade_answer(question, tiers, answer, llm=llm)
        if g.grade != "ungraded":
            conn.execute("INSERT OR REPLACE INTO trace_grades (key, run_id, seq, model, grade, detail, created_at)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (key, run_id, seq, model, g.grade, json.dumps({
                             "grade": g.grade, "must_found": g.must_found, "must_missed": g.must_missed,
                             "must_not_hit": g.must_not_hit, "reason": g.reason}), time.time()))
            conn.commit()
        return g
    finally:
        conn.close()
```

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_traces_grade.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/grade.py artmind/db.py test/test_traces_grade.py
git commit -m "feat(traces): absolute grade of an answer against its tiered expected answer, cached

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9: the band and the variant gate

Verdict dicts use the judge's orientation: `left` = baseline run, `right` = the other run.

**Files:**
- Create: `artmind/traces/gate.py`
- Test: `test/test_traces_gate.py`

- [ ] **Step 1: Write the failing test**

```python
from artmind.traces import gate


def V(verdict, better):
    return {"verdict": verdict, "better": better}


def test_balance():
    assert gate.balance({1: V("material", "left"), 2: V("minor", "right"), 3: V("same", "equal"),
                         4: V("unjudged", None)}) == 0


def test_band_is_the_widest_pair():
    pairs = {("A", "D"): {1: V("material", "left"), 2: V("material", "left")},
             ("A", "E"): {1: V("minor", "right")}}
    assert gate.band(pairs) == {"balance": 2, "material": 2}


def test_variant_inside_the_band_passes():
    b = {"balance": 2, "material": 3}
    vs = {"A": {1: V("material", "left"), 2: V("same", "equal")},
          "D": {1: V("minor", "right"), 2: V("same", "equal")}}
    out = gate.evaluate_variant(b, vs, baseline_found={"A": {1: {"x"}}, "D": {1: {"x"}}}, variant_found={1: {"x"}})
    assert out["passed"] and not out["above_band"]


def test_lost_must_and_worse_than_all_fail():
    b = {"balance": 5, "material": 5}
    vs = {"A": {1: V("material", "left")}, "D": {1: V("material", "left")}}
    out = gate.evaluate_variant(b, vs, baseline_found={"A": {1: {"x", "y"}}, "D": {1: {"x"}}},
                                variant_found={1: set()})
    assert not out["passed"]
    assert out["worse_than_all"] == [1] and out["lost_must"] == {1: ["x"]}


def test_above_band():
    b = {"balance": 1, "material": 1}
    vs = {"A": {1: V("material", "right"), 2: V("material", "right")},
          "D": {1: V("material", "right"), 2: V("minor", "right")}}
    assert gate.evaluate_variant(b, vs, baseline_found={}, variant_found={})["above_band"] is True
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_traces_gate.py -v
```

Expected: FAIL, `ModuleNotFoundError` (or wrong API if an old `gate.py` exists; this task replaces it).

- [ ] **Step 3: Create `artmind/traces/gate.py`**

```python
"""The classic band and the variant gate (spec 2026-11-08 §3.3-§3.4).

Verdicts are {seq: {"verdict", "better"}} with left = a baseline run, right = the other run. Cost and latency
are reported elsewhere and never gate.
"""

from __future__ import annotations


def balance(judgements: dict) -> int:
    """left-better minus right-better over judged questions (> 0: the baseline side better more often)."""
    js = [j for j in judgements.values() if j.get("verdict") not in (None, "unjudged")]
    return sum(1 for j in js if j.get("better") == "left") - sum(1 for j in js if j.get("better") == "right")


def _material(judgements: dict) -> int:
    return sum(1 for j in judgements.values() if j.get("verdict") == "material")


def band(baseline_pairs: dict) -> dict:
    """The widest |balance| and the highest material count over every pair of baseline runs."""
    if not baseline_pairs:
        return {"balance": 0, "material": 0}
    return {"balance": max(abs(balance(v)) for v in baseline_pairs.values()),
            "material": max(_material(v) for v in baseline_pairs.values())}


def evaluate_variant(band_: dict, variant_pairs: dict, *, baseline_found: dict, variant_found: dict) -> dict:
    """`variant_pairs`: baseline run id -> verdicts (left = that baseline run, right = the variant).
    `baseline_found`: run id -> {seq: set of must items found}; `variant_found`: {seq: set}."""
    b = band_["balance"]
    balances = {rid: balance(v) for rid, v in variant_pairs.items()}
    worse_sets = [{s for s, j in v.items() if j.get("verdict") == "material" and j.get("better") == "left"}
                  for v in variant_pairs.values()]
    worse_than_all = sorted(set.intersection(*worse_sets)) if worse_sets else []
    lost: dict[int, list[str]] = {}
    seqs = set().union(*(f.keys() for f in baseline_found.values())) if baseline_found else set()
    for seq in sorted(seqs):
        everyone = set.intersection(*(baseline_found[r].get(seq, set()) for r in baseline_found))
        missing = sorted(everyone - variant_found.get(seq, set()))
        if missing:
            lost[seq] = missing
    rows = [
        {"row": "within_band", "ok": all(x <= b for x in balances.values()),
         "detail": f"balance against each baseline run {balances}, band {b}"},
        {"row": "no_lost_must", "ok": not lost, "detail": f"must items every baseline run found, lost: {lost}"},
        {"row": "no_question_worse_than_all", "ok": not worse_than_all,
         "detail": f"materially worse than every baseline run: {worse_than_all}"},
    ]
    return {"passed": all(r["ok"] for r in rows), "above_band": bool(balances) and all(x < -b for x in balances.values()),
            "balances": balances, "worse_than_all": worse_than_all, "lost_must": lost, "rows": rows}
```

- [ ] **Step 4: Run the tests**

```bash
uv run --group dev pytest test/test_traces_gate.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/gate.py test/test_traces_gate.py
git commit -m "feat(traces): the classic band and the variant gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 10: `artmind traces compare`

```
artmind traces compare --baseline A,D[,E...] [--variant V] [--questions FILE]
                       [--judge] [--grade] [--allowJudge] [--model M] [--labels FILE] [--compact]
```

Stdout: run ids, measures (incl. answer reach), per-run grade summary, band, the variant gate, and the flagged qids.
Questions, answers and reasons go only to `<ARTMIND_HOME>/logs/compare-*.json`.

**Files:**
- Modify: `artmind/cli.py` (the `traces_group`; add the command after `traces stats`)
- Modify: `artmind/env.example` (document `ARTMIND_COMPARE_JUDGE`)
- Test: `test/test_traces_compare_cli.py`

- [ ] **Step 1: Write the failing test** `test/test_traces_compare_cli.py`

```python
"""`artmind traces compare` (spec 2026-11-08 §3). Invented runs; judge and grader are faked."""

import json

import pytest
from click.testing import CliRunner

from artmind.cli import cli
from test_traces_compare import _isolated_db, _run  # noqa: F401


@pytest.fixture
def runs():
    a = _run("a", "classic", ["ans one", "ans two"])
    d = _run("d", "classic", ["ans one", "ans two d"])
    v = _run("v", "p2-1", ["ans one", "ans two v"])
    return a, d, v


def _fake_llm(monkeypatch):
    from artmind.traces import judge

    def llm(prompt):
        if "MUST contain" in prompt:
            return json.dumps({"must": [True], "must_not": [], "grade": "correct", "reason": "r"})
        return json.dumps({"verdict": "minor", "better": "equal", "reason": "secret reason"})
    monkeypatch.setattr(judge, "default_llm", lambda model=None: (llm, "fake-model"))


def test_measures_without_judge_print_no_text(runs, tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    a, d, _ = runs
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{a},{d}", "--compact"])
    assert out.exit_code == 0, out.output
    p = json.loads(out.stdout)
    assert len(p["measures"]) == 2 and p["band"] is None and p["variant"] is None
    assert "invented question" not in out.stdout and "ans two" not in out.stdout


def test_needs_two_baseline_runs(runs):
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", runs[0]])
    assert out.exit_code == 1 and "two" in out.output


def test_judge_needs_consent(runs, monkeypatch):
    monkeypatch.delenv("ARTMIND_COMPARE_JUDGE", raising=False)
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{runs[0]},{runs[1]}", "--judge"])
    assert out.exit_code == 1 and "ARTMIND_COMPARE_JUDGE" in out.output


def test_judged_and_graded_with_variant(runs, tmp_path, monkeypatch):
    monkeypatch.setattr("artmind.cli.LOGS_DIR", tmp_path / "logs")
    _fake_llm(monkeypatch)
    qfile = tmp_path / "questions.md"
    qfile.write_text("## Q01\n**Question:** invented question 1?\n**Evaluation comment:** Must: fact one\n\n"
                     "## Q02\n**Question:** invented question 2?\n**Evaluation comment:** Must: fact two\n")
    a, d, v = runs
    out = CliRunner().invoke(cli, ["traces", "compare", "--baseline", f"{a},{d}", "--variant", v,
                                   "--questions", str(qfile), "--judge", "--grade", "--allowJudge", "--compact"])
    assert out.exit_code == 0, out.output
    p = json.loads(out.stdout)
    assert p["band"] == {"balance": 0, "material": 0}
    assert p["variant"]["gate"]["passed"] is True
    assert all(g["must"] == {"found": 2, "total": 2} for g in p["grades"].values())
    assert "secret reason" not in out.stdout
    detail = json.loads(open(p["detail_file"]).read())
    assert "secret reason" in json.dumps(detail)
```

- [ ] **Step 2: Run it to see it fail**

```bash
uv run --group dev pytest test/test_traces_compare_cli.py -v
```

Expected: FAIL, `No such command 'compare'`.

- [ ] **Step 3: Add the command to `artmind/cli.py`** after the `traces stats` command:

```python
@traces_group.command("compare")
@click.option("--baseline", "baseline", required=True,
              help="Two or more classic benchmark run ids (or unique prefixes), comma-separated")
@click.option("--variant", "variant", default=None, help="A run to judge against every baseline run")
@click.option("--questions", "questions_file", type=click.Path(path_type=Path, exists=True), default=None,
              help="Questions file whose evaluation comments (tiered expected answers) replace the ones stored with the runs")
@click.option("--judge", "use_judge", is_flag=True, help="Judge answers pairwise with the LLM (needs ARTMIND_COMPARE_JUDGE=1 or --allowJudge)")
@click.option("--grade", "use_grade", is_flag=True, help="Grade every answer against its tiered expected answer with the LLM (same consent)")
@click.option("--allowJudge", "allow_judge", is_flag=True, help="Allow judge and grader for this call: they send questions and answers to the configured LLM provider")
@click.option("--model", "model", default=None, help="Judge model (default: ARTMIND_KG_LLM_MODEL)")
@click.option("--labels", "labels_file", type=click.Path(path_type=Path), default=None,
              help="Labels file for answer reach (default: <vault>/benchmarking/plan_labels.yaml when present)")
@compact_option
def traces_compare(baseline, variant, questions_file, use_judge, use_grade, allow_judge, model, labels_file,
                   compact) -> None:
    """Compare classic baseline runs, and optionally a variant, on the same questions (spec 2026-11-08 §3).

    Prints measures, the band, grades and the variant gate. Questions, answers and reasons never reach stdout;
    they go to <ARTMIND_HOME>/logs/compare-*.json (private).
    """
    from artmind import benchmark
    from artmind.traces import compare as cmp
    from artmind.traces import gate as gate_mod
    from artmind.traces import tiers as tiers_mod

    refs = [r.strip() for r in baseline.split(",") if r.strip()]
    if len(refs) < 2:
        raise click.ClickException("--baseline takes at least two run ids")
    if (use_judge or use_grade) and not (allow_judge or os.environ.get("ARTMIND_COMPARE_JUDGE") == "1"):
        raise click.ClickException("--judge and --grade send questions and answers to the LLM provider: set "
                                   "ARTMIND_COMPARE_JUDGE=1 in this vault's config, or pass --allowJudge")
    expected = None
    if questions_file is not None:
        expected = {q["question"]: q["eval_comment"] for q in benchmark._parse_questions_file(questions_file)
                    if q.get("eval_comment")}
    try:
        runs = cmp.load_runs(refs + ([variant] if variant else []))
    except cmp.CompareError as exc:
        raise click.ClickException(str(exc)) from exc
    base, var = runs[:len(refs)], (runs[-1] if variant else None)
    labels = _compare_labels(labels_file)
    measures = [{**cmp.measures(r), "answer_reach": cmp.answer_reach(r, labels) if labels else None} for r in runs]
    out = {"runs": [r.run_id for r in runs], "measures": measures, "band": None, "grades": None, "variant": None,
           "flagged": None, "model": None, "detail_file": None}
    detail: dict = {"runs": out["runs"], "pairs": {}, "grades": {}}
    llm = chosen = None
    if use_judge or use_grade:
        from artmind.traces import judge

        llm, chosen = judge.default_llm(model)
        out["model"] = chosen

    def judged(left, right):
        pairs, only_l, only_r = cmp.pair_by_text(left, right, expected=expected)
        verdicts = {p.seq: judge.cached_judge(left.run_id, right.run_id, p.seq, p.question, p.expected,
                                              p.left.answer, p.right.answer, llm=llm, model=chosen) for p in pairs}
        detail["pairs"][f"{left.run_id}__{right.run_id}"] = {
            "only_left": only_l, "only_right": only_r,
            "questions": [{"seq": p.seq, "qid": p.qid, "verdict": verdicts[p.seq].verdict,
                           "better": verdicts[p.seq].better, "reason": verdicts[p.seq].reason} for p in pairs]}
        return pairs, {s: {"verdict": v.verdict, "better": v.better} for s, v in verdicts.items()}

    found: dict[str, dict[int, set]] = {}
    if use_grade:
        from artmind.traces import grade as grade_mod

        out["grades"] = {}
        anchor = base[0]
        for run in runs:
            pairs, _, _ = cmp.pair_by_text(anchor, run, expected=expected)
            counts = {g: 0 for g in ("correct", "partial", "wrong", "ungraded")}
            got = total = 0
            found[run.run_id] = {}
            for p in pairs:
                t = tiers_mod.parse(p.expected)
                g = grade_mod.cached_grade(run.run_id, p.seq, p.question, t, p.right.answer, llm=llm, model=chosen)
                counts[g.grade] += 1
                if g.grade != "ungraded":
                    got, total = got + len(g.must_found), total + len(t.must)
                found[run.run_id][p.seq] = set(g.must_found)
                detail["grades"].setdefault(run.run_id, []).append(
                    {"seq": p.seq, "qid": p.qid, "grade": g.grade, "must_missed": list(g.must_missed),
                     "must_not_hit": list(g.must_not_hit), "reason": g.reason})
            out["grades"][run.run_id] = {"counts": counts, "must": {"found": got, "total": total}}
    flagged: set = set()
    if use_judge:
        baseline_pairs = {}
        for i, left in enumerate(base):
            for right in base[i + 1:]:
                pairs, verdicts = judged(left, right)
                baseline_pairs[(left.run_id, right.run_id)] = verdicts
                flagged |= {p.qid for p in pairs if verdicts[p.seq]["verdict"] == "material"}
        out["band"] = gate_mod.band(baseline_pairs)
        if var is not None:
            vpairs, qid_of = {}, {}
            for left in base:
                pairs, verdicts = judged(left, var)
                vpairs[left.run_id] = verdicts
                qid_of.update({p.seq: p.qid for p in pairs})
            g = gate_mod.evaluate_variant(out["band"], vpairs,
                                          baseline_found={r.run_id: found.get(r.run_id, {}) for r in base},
                                          variant_found=found.get(var.run_id, {}))
            flagged |= {qid_of.get(s) for s in g["worse_than_all"]} | {qid_of.get(s) for s in g["lost_must"]}
            out["variant"] = {"run": var.run_id, "contract": var.contract, "gate": g}
    if use_grade:
        flagged |= {q["qid"] for rows in detail["grades"].values() for q in rows if q["grade"] == "wrong"}
    if use_judge or use_grade:
        out["flagged"] = sorted(q for q in flagged if q)
        out["detail_file"] = str(_write_compare_detail(detail))
    _echo_json(out, compact)


def _compare_labels(labels_file):
    from artmind import benchmark_labels

    if labels_file is not None and not labels_file.is_file():
        raise click.ClickException(f"labels file not found: {labels_file}")
    path = labels_file or benchmark_labels.default_path()
    if path is None or not path.is_file():
        return []
    try:
        return benchmark_labels.load_labels(path)
    except benchmark_labels.LabelError as exc:
        raise click.ClickException(f"invalid labels file {path}: {exc}") from exc


def _write_compare_detail(report: dict) -> Path:
    from datetime import datetime

    path = Path(LOGS_DIR) / f"compare-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    return path
```

Check `os`, `json`, `Path` and `LOGS_DIR` are already imported at the top of `cli.py` (`grep -n "^import os\|^import json\|LOGS_DIR" artmind/cli.py`); add any that are missing.

- [ ] **Step 4: Document the consent switch** in `artmind/env.example`, beside the other traces settings:

```
# `artmind traces compare --judge/--grade` sends questions and answers to the configured LLM provider.
# 1 allows it in this vault without --allowJudge.
# ARTMIND_COMPARE_JUDGE=1
```

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_traces_compare_cli.py test/test_cli_conventions.py test/test_cli_guide.py -v
```

Expected: PASS. If `test_cli_guide.py` reports `traces compare` unrouted, add `"compare"` next to `"stats"` in the
traces entry of `COMMAND_GROUPS` in `cli.py`.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py artmind/env.example test/test_traces_compare_cli.py
git commit -m "feat(traces): traces compare judges baseline runs, grades answers and gates a variant

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 11: `survey --passages` up to 8, and shadow planning off by default

**Files:**
- Modify: `artmind/cli.py` (the `query survey` `--passages` option), `artmind/query_plan/shadow.py:37-38`
- Test: `test/test_survey_cli.py`, `test/test_query_plan_shadow.py`

- [ ] **Step 1: Write the failing tests.** Append to `test/test_survey_cli.py`:

```python
def test_passages_accepts_up_to_eight():
    from artmind.cli import cli

    opt = next(p for p in cli.commands["query"].commands["survey"].params if p.name == "passages")
    assert opt.type.max == 8
```

Append to `test/test_query_plan_shadow.py`:

```python
def test_shadow_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("ARTMIND_PLAN_SHADOW", raising=False)
    assert shadow.enabled() is False
    monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "1")
    assert shadow.enabled() is True
```

- [ ] **Step 2: Run them to see them fail**

```bash
uv run --group dev pytest test/test_survey_cli.py::test_passages_accepts_up_to_eight test/test_query_plan_shadow.py::test_shadow_is_off_unless_asked -v
```

Expected: both FAIL.

- [ ] **Step 3: Implement.** In `artmind/cli.py` change the survey option
  `@click.option("--passages", type=click.IntRange(1, 3), ...` to `click.IntRange(1, 8)`. In
  `artmind/query_plan/shadow.py` replace `enabled` with:

```python
def enabled() -> bool:
    """Off unless ARTMIND_PLAN_SHADOW is 1/true/yes/on: the planner is frozen (spec 2026-11-08 §2.3)."""
    return (os.environ.get("ARTMIND_PLAN_SHADOW") or "0").strip().casefold() in ("1", "true", "yes", "on")
```

Update the module docstring's sentence `ARTMIND_PLAN_SHADOW=0 stops it.` to
`It runs only with ARTMIND_PLAN_SHADOW=1.`

- [ ] **Step 4: Keep the shadow tests that expect it on.** In `test/test_query_plan_shadow.py`'s `_isolated_db`
  fixture, replace `monkeypatch.delenv("ARTMIND_PLAN_SHADOW", raising=False)` with
  `monkeypatch.setenv("ARTMIND_PLAN_SHADOW", "1")`. Do the same in `test/test_traces_plan_shadow.py` if it deletes
  the variable.

- [ ] **Step 5: Run the tests**

```bash
uv run --group dev pytest test/test_survey_cli.py test/test_query_plan_shadow.py test/test_traces_plan_shadow.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py artmind/query_plan/shadow.py test/test_survey_cli.py test/test_query_plan_shadow.py test/test_traces_plan_shadow.py
git commit -m "feat: survey --passages up to 8; shadow planning only with ARTMIND_PLAN_SHADOW=1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 12: the entanglement test

**Files:**
- Create: `test/test_no_planner_entanglement.py`

- [ ] **Step 1: Write the test**

```python
"""No new code depends on the frozen planner (spec 2026-11-08 §2.3, D2). The seams below existed at v0.11.0;
phase 3 removes them. This list may only shrink."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "artmind"
SEAMS = {
    "artmind/cli.py",
    "artmind/server.py",
    "artmind/webui/app.py",
    "artmind/traces/recorder.py",
    "artmind/catalogue/cache.py",
}


def _imports_planner(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("artmind.query_plan"):
            return True
        if isinstance(node, ast.Import) and any(a.name.startswith("artmind.query_plan") for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "artmind" and any(
                a.name == "query_plan" for a in node.names):
            return True
    return False


def test_only_the_pinned_seams_import_the_planner():
    found = {str(p.relative_to(ROOT.parent)) for p in ROOT.rglob("*.py")
             if "query_plan" not in p.relative_to(ROOT).parts and _imports_planner(p)}
    assert found <= SEAMS, f"new planner imports: {sorted(found - SEAMS)}"


def test_the_seam_list_only_shrinks():
    found = {str(p.relative_to(ROOT.parent)) for p in ROOT.rglob("*.py")
             if "query_plan" not in p.relative_to(ROOT).parts and _imports_planner(p)}
    assert SEAMS <= found, f"remove these from SEAMS, they no longer import the planner: {sorted(SEAMS - found)}"
```

`artmind/retrieval_eval/labels.py` is not in the list: Task 4 removed its import.

- [ ] **Step 2: Run it**

```bash
uv run --group dev pytest test/test_no_planner_entanglement.py -v
```

Expected: PASS. A failure of the first test names a new import: move the needed code out of `query_plan` (as
Task 4 did) rather than adding a seam.

- [ ] **Step 3: Commit**

```bash
git add test/test_no_planner_entanglement.py
git commit -m "test: pin the planner import seams so no new code depends on the frozen planner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 13: docs and the full suite

**Files:**
- Modify: `CLAUDE.md` (repo-layout table rows for `artmind/traces/`, a new row for `artmind/benchmark_labels.py`;
  the `ARTMIND_PLAN_SHADOW` default in the `query_plan/` row; "Command routing quick reference" line for
  `artmind traces <cmd>`)

- [ ] **Step 1: Edit `CLAUDE.md`.**
  - `artmind/traces/` row: replace any `compare --runs A,B,BUNDLE` text with: `` `compare --baseline A,D[,…] [--variant V] [--questions FILE] [--judge] [--grade]`: pairs runs by question text, judges pairs (`judge.py`, whole answers to a 40,000 ceiling), grades each answer against tiered expected answers (`tiers.py`, `grade.py`, cached in `trace_grades`), prints the classic band and the variant gate (`gate.py`); questions, answers and reasons only in `logs/compare-*.json`; needs `ARTMIND_COMPARE_JUDGE=1` or `--allowJudge` ``.
  - New row: `` | `artmind/benchmark_labels.py` | Benchmark labels (`question`, `expected_documents` from `<vault>/benchmarking/plan_labels.yaml`) and document matching, with no planner import. | ``
  - `artmind/query_plan/` row: add at its start: `**Frozen 2026-11-08** (archive/p3-bundle, spec 2026-11-08): no new dependency (test_no_planner_entanglement.py); shadow planning only with ARTMIND_PLAN_SHADOW=1.`
  - The `artmind traces <cmd>` routing line: add `compare`.

- [ ] **Step 2: Run the full suite**

```bash
just dev-test
```

Expected: all pass (about 5,000 tests). Fix any failure inside the task that caused it before going on.

- [ ] **Step 3: Live smoke check** (the editable install already runs this branch):

```bash
cd ~/artmind_vaults/banking_corpus && ARTMIND_NO_PROXY=1 artmind traces compare --baseline 61cf661f,15837ded --compact
```

Expected: two measures rows, `band: null`, no question text on stdout.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md for the baseline branch (traces compare, benchmark labels, frozen planner)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Phase 1: questions, expected answers and the baseline

These tasks write only to the vaults' private `benchmarking/` folders. Nothing from them enters this checkout.

### Task 14: draft 15 new brain01 questions

- [ ] **Step 1: Read the shape of the graph** from inside the vault (artmind's own config reaches its Neo4j):

```bash
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind query domains-overview --compact
cd ~/artmind_vaults/brain01 && ARTMIND_NO_PROXY=1 artmind query graph entity-listing --domain personal_journal --countAll --compact
```

- [ ] **Step 2: Pick candidates per shape** and check each against the graph with `pattern8`, `pattern9`,
  `entity-context --query`, `text2cypher` (after `--dryRun`) and `vector-text`, all with `ARTMIND_NO_PROXY=1`:
  - 2+ for `pattern8` (entities of class X connected to entity Y);
  - 2+ for `pattern9` (top N of a class by connections, `--degreeMode relations` and `mentions`);
  - the rest from: multi-part questions, counts and histories, event windows, a follow-up pair, first-person.
  Avoid the 14 existing questions' subjects.

- [ ] **Step 3: Write them** to `~/artmind_vaults/brain01/benchmarking/Benchmarking_questions_v2.md`: the 14
  existing questions first, unchanged and in order (copy from `Benchmarking_questions.md`), then the 15 new ones,
  each as:

```markdown
## Q15 — <short title>
**Question:** <question>
**Evaluation comment:**
Must: <item>; <item>
Optional: <item>
Must-not: <item>
Notes: <what was checked, with which command>
```

- [ ] **Step 4: The user reviews and confirms** the new questions and their tiers (edits in place). Do not start
  Task 16 before the user says the file is confirmed.

### Task 15: tier the existing expected answers

- [ ] **Step 1: List untiered comments** in both question files:

```bash
cd ~/projects/artmind9 && uv run python - <<'PY'
from artmind import benchmark
from artmind.traces import tiers
for f in ["~/artmind_vaults/banking_corpus/benchmarking/questions.md",
          "~/artmind_vaults/brain01/benchmarking/Benchmarking_questions_v2.md"]:
    import os
    qs = benchmark._parse_questions_file(os.path.expanduser(f))
    print(f, [q["qid"] for q in qs if not tiers.parse(q["eval_comment"]).must])
PY
```

Expected: a list of qids per file (ids only; nothing private printed).

- [ ] **Step 2: Draft tiers** for each listed question from its existing comment and the vault data, in place.
- [ ] **Step 3: The user confirms** both files.

### Task 16: baseline runs (the user starts them)

- [ ] **Step 1: Before the runs:** `git -C ~/projects/artmind9 log -1 --oneline` shows the last commit of Task 13;
  both admin consoles were restarted after it (`pgrep -fl 'artmind admin-ui'` shows pids started after the commit).
- [ ] **Step 2: The user starts**, from each vault's admin console benchmark form with skill variant `classic`:
  - banking_corpus: one run of `benchmarking/questions.md`;
  - brain01: two runs of `benchmarking/Benchmarking_questions_v2.md`, started at least an hour apart (the gateway
    replays identical requests close together; handover 17).
- [ ] **Step 3: Record the run ids** in the baseline note (Task 18).

### Task 17: judge and grade the baseline

- [ ] **Step 1: banking**

```bash
cd ~/artmind_vaults/banking_corpus && env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT ARTMIND_NO_PROXY=1 artmind traces compare --baseline 61cf661f,15837ded,<E> --questions benchmarking/questions.md --judge --grade --allowJudge --model claude-sonnet-5-5 --compact
```

- [ ] **Step 2: brain01** (A and D answer the original 14; the two fresh runs answer all 29)

```bash
cd ~/artmind_vaults/brain01 && env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT ARTMIND_NO_PROXY=1 artmind traces compare --baseline 49bc9ef6,d6c41e2c,<F1>,<F2> --questions benchmarking/Benchmarking_questions_v2.md --judge --grade --allowJudge --model claude-sonnet-5-5 --compact
cd ~/artmind_vaults/brain01 && env -u ANTHROPIC_BASE_URL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT ARTMIND_NO_PROXY=1 artmind traces compare --baseline <F1>,<F2> --questions benchmarking/Benchmarking_questions_v2.md --judge --grade --allowJudge --model claude-sonnet-5-5 --compact
```

The first call's band covers the original 14 (pairs with A and D) plus the fresh pair; the second gives the
new-15 band alone (read its pair's `only_left` / `only_right` in the detail file to confirm all 29 paired).

- [ ] **Step 3: The user reads the flagged answers.** Hand the user the `flagged` qids and the `detail_file`
  path of each call. The user's ratings override the judge; where they disagree, note it for a judge prompt
  change (a `PROMPT_VERSION` bump) before phase 2.

### Task 18: record the baseline

- [ ] **Step 1: Write** `docs/query-routing-study/20-classic-baseline.md` with counts and ids only: run ids;
  bands per vault (balance, material; brain01 original-14 and new-15); grade counts and must-item coverage per
  run; answer reach on banking; median commands, latency, cost; the flagged counts and how many the user
  overrode; the facts of spec §3.1 (A/D build). Add its row to `docs/query-routing-study/README.md`.
- [ ] **Step 2: Grep the note for private names** seen in brain01 before committing (none may appear).
- [ ] **Step 3: Commit**

```bash
git add docs/query-routing-study/20-classic-baseline.md docs/query-routing-study/README.md
git commit -m "docs(query-routing-study): note 20, the measured classic baseline

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Roadmap: phases 2–5 (each gets its own plan when it starts)

| Order | Slice | Spec | Measured how |
|---|---|---|---|
| 1 | P2-0 miss records: `artmind traces misses`, cited-source → span link, named-item capture | §4.1 | hermetic + one live derivation per vault |
| 2 | P2-1 `artmind query orient` (question-free; `is_domain_name` → `artmind/domain_config.py`, seam list shrinks) | §4.2 | variant `p2-1` vs baseline |
| 3 | P2-2 `next` hints replacing ladder rungs 2, 3, 6, 7, 9, 10, 11 | §4.3 | variant, paired with P2-3 if the user wants fewer runs |
| 4 | P2-3 narrator and twins in `entity-resolve` (config loader → `domain_config.py`) | §4.3 | variant |
| 5 | P2-4 `--all` + `complete` on `pattern7`, `pattern8`, `entity-listing` | §4.3 | variant, paired with P2-5 optionally |
| 6 | P2-5 `entity-context` ranks by question by default; hub cut reported | §4.3 | variant |
| 7 | Composites, episode kind first (assembly-pass spec §3–§6, §9–§10; §7 replaced) | §4.4 | data change applied to the vault, one classic run per vault |
| 8 | P2b skill restructure (~150 lines + references, generated `commands.md`) | §4.5 | variant; fewer commands per turn |
| 9 | Phase 3 planner removal | §5 | full suite + live trace + one classic run per vault |
| 10 | Phase 4 audit → schema and guidance → second composite kind | §6 | graph-held must items up; benchmark inside or above band |
| 11 | Phase 5 DSL design note | §7 | the user reads it |

The survey default (50 documents) is reconsidered inside P2-1's plan, as a measured decision.
