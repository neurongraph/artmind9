# Query review loop — P4: shape-report, attach, regressions, verify — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the review loop. Aggregate shape findings across dossiers by schema element, so one systemic problem becomes one dossier with many symptoms. Sample a domain proactively, with no failing question needed. Assemble every dossier's regression question into a benchmark file. Verify a fix by re-running its regression question and comparing the new trace with the original.

**Architecture:**
- **`artmind/reviews/aggregate.py`**: parses each dossier's Shape audit table into `{element, check}` findings and groups them by element.
- **`scaffold.attach`**: adds a trace's symptom to an existing dossier.
- **`artmind/reviews/sample.py`**: picks random chunks from a domain's staging and flags two mechanical signals:
  - **lost at commit:** a staging observation with no graph entity for the chunk;
  - **isolated:** an observation that no staging relationship touches.
- **`artmind/reviews/verify.py`**:
  - parses the regression question;
  - assembles the regressions file;
  - runs a one-question benchmark through the existing runner (injectable for tests);
  - compares the original and new traces;
  - applies the graph-miss rule from spec §9;
  - logs the outcome in the dossier.

  It never sets `verified` itself: the reviewer grades against the evaluation comment and the human confirms.

**Tech Stack:** Python 3.14, click / rich-click, sqlite3, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-query-review-loop-design.md`. P4 covers §7.5, §9 and §10's P4 row.

**Branch:** `feat/query-review-p4`, already created from `master` @ 5e1ec2f.

---

## Ground rules for every task

- Run tests from the repo root with `uv run --group dev pytest <path> -v`. The suite is hermetic.
- Isolate the DB with `monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")`.
- Isolate dossiers with `monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")` and `paths.LOGS_DIR`.
- Isolate staging with `monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")`.
- Graph reads go through `graph_query.read_session` (module attribute); tests fake it.
- Use invented test data only.
- Commit after each task. End every message with a blank line, then exactly:

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/reviews/aggregate.py` | create | `findings(dossier)`, `shape_report(*, domain, statuses)` |
| `artmind/reviews/scaffold.py` | modify | `attach(dossier, trace_id)` |
| `artmind/reviews/sample.py` | create | `all_chunks(domain)`, `signals(staging, graph)`, `sample(domain, n, *, seed, with_graph)` |
| `artmind/reviews/verify.py` | create | `regression(d)`, `regressions_markdown(*, statuses)`, `summarize(shown)`, `compare(original, new)`, `verify(review_id, *, run_id, backend, runner)` |
| `artmind/cli.py` | modify | `review shape-report`, `review attach`, `review regressions`, `review verify` |
| skill, CLAUDE.md, justfile, spec | modify | docs |
| `test/test_reviews_aggregate.py`, `test/test_reviews_sample.py`, `test/test_reviews_verify.py`, `test/test_reviews_cli.py` | create / modify | tests |

---

### Task 1: Aggregate findings by schema element; attach a trace to a dossier

**Files:**
- Create: `artmind/reviews/aggregate.py`
- Modify: `artmind/reviews/scaffold.py` (add `attach` at the end)
- Test: `test/test_reviews_aggregate.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_aggregate.py`:

```python
"""Findings by schema element across dossiers; attaching a trace to a dossier."""

import json
import time
from datetime import date

import pytest

from artmind.reviews import aggregate, scaffold
from artmind.reviews import dossier as dz
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


AUDIT = ("| element | check | source sentence | why the question needs it |\n|---|---|---|---|\n"
         "| `j/PERSON-WENT_TO->` | coverage | I went to Lakeport with Mira. | count |\n"
         "| j/PERSON-WENT_TO-> | Reification | same | trip |\n"
         "| j/PERSON | identity | I went | narrator |\n")


def _dossier(rid, *, status="open", elements=(), audit="", traces=("t1",)):
    sections = {t: "" for t in dz.SECTIONS}
    sections["Shape audit"] = audit
    d = dz.Dossier(meta={"review_id": rid, "status": status, "stage": "extraction", "elements": list(elements),
                         "trace_ids": list(traces), "private_terms": []}, sections=sections)
    dz.save(d)
    return d


def test_findings_from_table_and_frontmatter():
    d = _dossier("rv-2031-01-01-aaaaaa", elements=["j/PERSON-WENT_TO->", "j/LOCATION"], audit=AUDIT)
    assert aggregate.findings(d) == [
        {"element": "j/PERSON-WENT_TO->", "check": "coverage"},
        {"element": "j/PERSON-WENT_TO->", "check": "reification"},
        {"element": "j/PERSON", "check": "identity"},
        {"element": "j/LOCATION", "check": None},
    ]


def test_shape_report_groups_and_ranks():
    _dossier("rv-2031-01-01-aaaaaa", elements=["j/PERSON-WENT_TO->"], audit=AUDIT, traces=("t1", "t2"))
    _dossier("rv-2031-01-02-bbbbbb", elements=["j/PERSON-WENT_TO->"], traces=("t3",))
    _dossier("rv-2031-01-03-cccccc", elements=["k/THING"], traces=("t4",))
    _dossier("rv-2031-01-04-dddddd", status="verified", elements=["j/PERSON-WENT_TO->"], traces=("t5",))
    rows = aggregate.shape_report()
    top = rows[0]
    assert top["element"] == "j/PERSON-WENT_TO->"
    assert top["traces"] == 3                       # t1, t2, t3 -- verified dossier excluded
    assert [d["review_id"] for d in top["dossiers"]] == ["rv-2031-01-01-aaaaaa", "rv-2031-01-02-bbbbbb"]
    assert top["checks"] == {"coverage": 1, "reification": 1}
    assert [r["element"] for r in aggregate.shape_report(domain="k")] == ["k/THING"]
    assert any(r["traces"] == 4 for r in aggregate.shape_report(statuses=dz.STATUSES))


def _trace(tid):
    store.insert_turn(tid, source="admin", question=f"Where did Person {tid} go?", started_at=time.time())
    store.update_turn(tid, answer="Lakeport.", status="done", retrieval_path="[]", domains='["j"]')
    store.insert_span({"span_id": f"s-{tid}", "trace_id": tid, "command": "query entity-resolve",
                       "args": json.dumps({"reference": f"Person {tid}"}), "exit_code": 0, "duration_ms": 1,
                       "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"resolved": [{"id": "e", "name": f"Person {tid}", "score": 1}]}),
                       "started_at": time.time()}, [])


def test_attach_adds_symptom_trace_terms_and_log():
    _trace("t9")
    d = _dossier("rv-2031-01-01-aaaaaa", traces=("t1",))
    d.sections["Symptom"] = "first symptom"
    scaffold.attach(d, "t9", today=date(2031, 1, 5))
    assert d.meta["trace_ids"] == ["t1", "t9"]
    assert "Person t9" in d.meta["private_terms"]
    assert d.sections["Symptom"].startswith("first symptom\n\n### Also seen in trace `t9`")
    assert "> Where did Person t9 go?" in d.sections["Symptom"]
    assert d.sections["Log"].endswith("- 2031-01-05 attached trace `t9`")
    reparsed = dz.parse(dz.render(d))           # the ### heading stays inside Symptom
    assert reparsed.sections["Symptom"] == d.sections["Symptom"]


def test_attach_refusals():
    _trace("t9")
    d = _dossier("rv-2031-01-01-aaaaaa", traces=("t9",))
    with pytest.raises(dz.DossierError, match="already"):
        scaffold.attach(d, "t9")
    closed = _dossier("rv-2031-01-02-bbbbbb", status="verified", traces=("t1",))
    with pytest.raises(dz.DossierError, match="reopen"):
        scaffold.attach(closed, "t9")
    with pytest.raises(dz.DossierError, match="no trace"):
        scaffold.attach(_dossier("rv-2031-01-03-cccccc"), "missing")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_aggregate.py -v`
Expected: FAIL with `ModuleNotFoundError: artmind.reviews.aggregate`.

- [ ] **Step 3: Implement `aggregate.py`**

```python
"""Shape findings across dossiers, by schema element (spec 2026-10-11 §7.5).
One systemic problem should be one dossier listing every symptom; this is
where the reviewer sees that an element already has one."""

from collections import Counter

from artmind.reviews.dossier import Dossier, iter_dossiers

ACTIVE = ("open", "in-progress")


def findings(dossier: Dossier) -> list[dict]:
    """Shape-audit table rows as {element, check}, then frontmatter elements
    the table does not mention (check None)."""
    out: list[dict] = []
    for line in (dossier.sections.get("Shape audit") or "").split("\n"):
        row = line.strip()
        if not row.startswith("|") or set(row) <= set("|-: "):
            continue
        cells = [c.strip().strip("`").strip() for c in row.strip("|").split("|")]
        if len(cells) < 2 or cells[0].lower() == "element" or not cells[0]:
            continue
        out.append({"element": cells[0], "check": cells[1].lower() or None})
    seen = {f["element"] for f in out}
    out += [{"element": str(e), "check": None} for e in dossier.meta.get("elements") or [] if str(e) not in seen]
    return out


def shape_report(*, domain: str | None = None, statuses=ACTIVE) -> list[dict]:
    groups: dict[str, dict] = {}
    for _, dossier, error in iter_dossiers():
        if error or dossier.meta.get("status") not in statuses:
            continue
        for finding in findings(dossier):
            element = finding["element"]
            if domain and not element.startswith(f"{domain}/"):
                continue
            group = groups.setdefault(element, {"dossiers": {}, "checks": Counter(), "trace_ids": set()})
            group["dossiers"][dossier.review_id] = {"review_id": dossier.review_id,
                                                     "status": dossier.meta.get("status"),
                                                     "stage": dossier.meta.get("stage")}
            if finding["check"]:
                group["checks"][finding["check"]] += 1
            group["trace_ids"].update(dossier.meta.get("trace_ids") or [])
    rows = [{"element": element, "traces": len(g["trace_ids"]), "dossiers": list(g["dossiers"].values()),
             "checks": dict(g["checks"])} for element, g in groups.items()]
    rows.sort(key=lambda r: (-r["traces"], -len(r["dossiers"]), r["element"]))
    return rows
```

- [ ] **Step 4: Implement `scaffold.attach`**

Append to `artmind/reviews/scaffold.py`. Import `append_log` from `artmind.reviews.dossier` alongside the existing imports.

```python
def attach(dossier: Dossier, trace_id: str, *, today: date | None = None) -> Dossier:
    """Add another symptom trace to an open dossier (spec 2026-10-11 §7.5):
    its symptom block under Symptom, its id in trace_ids, its private terms."""
    from artmind.traces.analysis import show

    if trace_id in (dossier.meta.get("trace_ids") or []):
        raise DossierError(f"trace {trace_id} is already in {dossier.review_id}")
    if dossier.meta.get("status") not in _ACTIVE:
        raise DossierError(f"{dossier.review_id} is {dossier.meta.get('status')}; reopen it "
                           f"(`artmind review status {dossier.review_id} open`) before attaching a trace")
    shown = show(trace_id)
    if shown is None:
        raise DossierError(f"no trace {trace_id}")
    block = f"### Also seen in trace `{trace_id}`\n\n{_symptom(shown)}"
    dossier.sections["Symptom"] = f"{(dossier.sections.get('Symptom') or '').rstrip()}\n\n{block}"
    dossier.meta["trace_ids"] = [*(dossier.meta.get("trace_ids") or []), trace_id]
    terms = dict.fromkeys(dossier.meta.get("private_terms") or [])
    terms.update(dict.fromkeys(harvest_private_terms(shown)))
    dossier.meta["private_terms"] = list(terms)
    append_log(dossier, f"attached trace `{trace_id}`", today=today)
    return dossier
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_aggregate.py test/test_reviews_scaffold.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/reviews/aggregate.py artmind/reviews/scaffold.py test/test_reviews_aggregate.py
git commit -m "feat(reviews): shape findings by schema element; attach a trace to a dossier"
```

---

### Task 2: Proactive sampling — `artmind/reviews/sample.py`

**Files:**
- Create: `artmind/reviews/sample.py`
- Test: `test/test_reviews_sample.py`

Sampling never needs an LLM: it reads staging and one graph query per chunk. The two signals are mechanical hints; the reviewer grades the real shape with `review shape`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_sample.py`:

```python
"""Proactive shape sampling: random chunks of a domain, mechanical signals."""

import json
from contextlib import contextmanager

import pytest

from artmind.reviews import sample


@pytest.fixture(autouse=True)
def _staging(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "KG_DIR", tmp_path / "kg")
    for i in range(1, 4):
        folder = tmp_path / "kg" / "j" / f"2031-01-0{i}"
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"id": f"doc-{i}", "path": f"j/2031-01-0{i}.md"}))
        (folder / "chunks.json").write_text(json.dumps([{"id": f"doc-{i}_001", "text": "Mira met Arlo. They sailed."}]))
        (folder / "observations.json").write_text(json.dumps([
            {"name": "Mira", "canonical_name": "Mira Venn", "entity_class": "PERSON", "chunk_id": f"doc-{i}_001"},
            {"name": "Arlo", "entity_class": "PERSON", "chunk_id": f"doc-{i}_001"},
            {"name": "Harbour", "entity_class": "LOCATION", "chunk_id": f"doc-{i}_001"}]))
        (folder / "relationships.json").write_text(json.dumps([
            {"source_name": "Mira", "target_name": "Arlo", "rel_type": "meets", "chunk_id": f"doc-{i}_001"}]))


@pytest.fixture
def fake_graph(monkeypatch):
    from artmind import graph_query

    class _Result:
        def data(self):
            return [{"entity": {"_id": "e1", "name": "Mira Venn", "entity_class": "PERSON"}, "rels": []},
                    {"entity": {"_id": "e2", "name": "Harbour", "entity_class": "LOCATION"}, "rels": []}]

    class _Session:
        def run(self, cypher, **params):
            return _Result()

    @contextmanager
    def fake_read_session():
        yield _Session()

    monkeypatch.setattr(graph_query, "read_session", fake_read_session)


def test_all_chunks():
    assert [cid for cid, _ in sample.all_chunks("j")] == ["doc-1_001", "doc-2_001", "doc-3_001"]
    assert sample.all_chunks("missing") == []


def test_signals():
    staging = {"observations": [{"name": "Mira", "canonical_name": "Mira Venn", "entity_class": "PERSON"},
                                {"name": "Arlo", "entity_class": "PERSON"},
                                {"name": "Harbour", "entity_class": "LOCATION"}],
               "relationships": [{"source_name": "Mira", "target_name": "Arlo", "rel_type": "meets"}]}
    graph = {"nodes": [{"name": "Mira Venn", "extracted_here": True}, {"name": "Harbour", "extracted_here": True},
                       {"name": "Arlo", "extracted_here": False}]}
    assert sample.signals(staging, graph) == {"lost_at_commit": ["Arlo"], "isolated": ["Harbour"]}
    assert sample.signals(staging, None) == {"isolated": ["Harbour"]}


def test_sample_is_seeded_and_bounded(fake_graph):
    a = sample.sample("j", 2, seed=7)
    b = sample.sample("j", 2, seed=7)
    assert [s["chunk_id"] for s in a["sampled"]] == [s["chunk_id"] for s in b["sampled"]]
    assert a["population"] == 3 and len(a["sampled"]) == 2
    first = a["sampled"][0]
    assert first["observations"] == 3 and first["relationships"] == 1 and first["graph_entities"] == 2
    assert first["signals"] == {"lost_at_commit": ["Arlo"], "isolated": ["Harbour"]}
    assert first["next"].startswith("artmind review shape --domain j --chunk doc-")
    assert len(sample.sample("j", 10, seed=1)["sampled"]) == 3


def test_sample_without_graph():
    out = sample.sample("j", 1, seed=1, with_graph=False)
    assert out["sampled"][0]["graph_entities"] is None
    assert out["sampled"][0]["signals"] == {"isolated": ["Harbour"]}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_sample.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/sample.py`:

```python
"""Proactive shape sampling (spec 2026-10-11 §7.5 `--sample`): random chunks
of a domain, no failing question needed, with two mechanical signals --
`lost_at_commit` (a staging observation with no graph entity extracted from
the chunk) and `isolated` (an observation no staging relationship touches).
The reviewer grades the actual shape with `artmind review shape --chunk`."""

import random
from pathlib import Path

from artmind.reviews import shape


def _cf(value) -> str:
    return str(value or "").strip().casefold()


def all_chunks(domain: str) -> list[tuple[str, Path]]:
    import paths

    root = paths.KG_DIR / domain
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for chunk in shape._read_json(folder / "chunks.json", []):
            if chunk.get("id"):
                out.append((chunk["id"], folder))
    return out


def signals(staging: dict, graph: dict | None) -> dict:
    observations = staging["observations"]
    touched = {_cf(r.get("source_name")) for r in staging["relationships"]} | \
              {_cf(r.get("target_name")) for r in staging["relationships"]}
    isolated = sorted({o["name"] for o in observations
                       if _cf(o.get("name")) not in touched and _cf(o.get("canonical_name")) not in touched})
    if graph is None:
        return {"isolated": isolated}
    here = {_cf(n.get("name")) for n in graph["nodes"] if n.get("extracted_here")}
    lost = sorted({o["name"] for o in observations
                   if _cf(o.get("name")) not in here and _cf(o.get("canonical_name")) not in here})
    return {"lost_at_commit": lost, "isolated": isolated}


def sample(domain: str, n: int, *, seed: int | None = None, with_graph: bool = True) -> dict:
    population = all_chunks(domain)
    picked = random.Random(seed).sample(population, min(n, len(population)))
    out = []
    for chunk_id, folder in picked:
        staged = shape._staging_from_folder(folder, chunk_id)
        graph = shape.graph_for_chunks([domain], [chunk_id]) if with_graph else None
        out.append({
            "chunk_id": chunk_id,
            "document": staged["document"].get("path") or staged["document"].get("name"),
            "sentences": len(shape.sentences(staged["chunk"].get("text"))),
            "observations": len(staged["observations"]),
            "relationships": len(staged["relationships"]),
            "graph_entities": sum(1 for x in graph["nodes"] if x.get("extracted_here")) if graph else None,
            "signals": signals(staged, graph),
            "next": f"artmind review shape --domain {domain} --chunk {chunk_id}",
        })
    return {"domain": domain, "seed": seed, "population": len(population), "sampled": out}
```

`shape._staging_from_folder(folder, chunk_id)` was added in P3 (a refactor of `staging_for_chunk`). Read `artmind/reviews/shape.py` to confirm its name and return shape. `graph_for_chunks` makes one read query per sampled chunk.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_sample.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/sample.py test/test_reviews_sample.py
git commit -m "feat(reviews): proactive shape sampling with lost-at-commit and isolated signals"
```

---

### Task 3: Regressions and verification — `artmind/reviews/verify.py`

**Files:**
- Create: `artmind/reviews/verify.py`
- Test: `test/test_reviews_verify.py`

`verify` makes a one-question benchmark run from the dossier's regression question. It runs it with the existing runner (or takes `run_id` for a run already done), compares the new trace with the dossier's first trace, and logs the outcome in the dossier.

The mechanical checks are:
- `answered`: the new turn has an answer.
- For a dossier whose original turn was a **graph miss** (spec §9), `graph_answer`: the new turn did not fall through and its `answer_basis` is `graph` or `mixed`.

Grading against the evaluation comment is the reviewer's job. `verify` never sets `verified`.

- [ ] **Step 1: Write the failing tests**

Create `test/test_reviews_verify.py`:

```python
"""Regression questions out of dossiers; verifying a fix by re-running one."""

import json
import time

import pytest

from artmind import benchmark
from artmind.reviews import dossier as dz
from artmind.reviews import verify
from artmind.traces import store

REG = ("### R-rv-2031-01-01-aaaaaa — trips\n\n**Question:** How many trips did Mira take?\n\n"
       "**Evaluation comment:** Three, answered from the graph.")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def _turn(tid, *, answer, fell_through, basis, commands=("query graph pattern6",)):
    store.insert_turn(tid, source="admin", question="How many trips did Mira take?", started_at=time.time())
    store.update_turn(tid, answer=answer, status="done", fell_through=int(fell_through), answer_basis=basis,
                      graph_attempts=1, graph_productive=0 if fell_through else 1, domains='["j"]',
                      retrieval_path="[]")
    for i, cmd in enumerate(commands):
        store.insert_span({"span_id": f"{tid}-{i}", "trace_id": tid, "command": cmd, "args": "{}", "exit_code": 0,
                           "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0, "evidence": "{}",
                           "started_at": time.time()}, [])


def _dossier(rid="rv-2031-01-01-aaaaaa", traces=("t-orig",), status="fixed", reg=REG, elements=("j/PERSON-WENT_TO->",)):
    sections = {t: "" for t in dz.SECTIONS}
    sections["Regression question"] = reg
    d = dz.Dossier(meta={"review_id": rid, "status": status, "stage": "extraction", "elements": list(elements),
                         "trace_ids": list(traces), "private_terms": []}, sections=sections)
    dz.save(d)
    return d


def test_regression_parses_the_one_question():
    r = verify.regression(_dossier())
    assert r == {"review_id": "rv-2031-01-01-aaaaaa", "title": "trips", "question": "How many trips did Mira take?",
                 "eval_comment": "Three, answered from the graph."}
    with pytest.raises(dz.DossierError):
        verify.regression(_dossier(rid="rv-2031-01-02-bbbbbb", reg="<!-- nothing yet -->"))


def test_regressions_markdown_round_trips_through_the_benchmark_parser():
    _dossier()
    _dossier(rid="rv-2031-01-02-bbbbbb", status="open",
             reg="### R-rv-2031-01-02-bbbbbb — x\n\n**Question:** Q2?\n\n**Evaluation comment:** E2.")
    text = verify.regressions_markdown()
    assert [q["question"] for q in benchmark._parse_questions(text)] == ["How many trips did Mira take?", "Q2?"]
    only_fixed = verify.regressions_markdown(statuses=("fixed",))
    assert [q["question"] for q in benchmark._parse_questions(only_fixed)] == ["How many trips did Mira take?"]


def _runner_recording(new_trace_id, seen):
    def run(run_id):
        seen["run_id"] = run_id
        seen["run"] = benchmark._get_benchmark_run(run_id)
        benchmark._update_benchmark_question(run_id, 1, status="completed", answer_text="Three.", trace_id=new_trace_id)
    return run


def test_verify_graph_miss_dossier_passes_when_the_graph_answers():
    _turn("t-orig", answer="Two.", fell_through=True, basis="vector", commands=("query graph pattern6", "query vector-text"))
    _turn("t-new", answer="Three.", fell_through=False, basis="graph")
    _dossier()
    seen = {}
    result = verify.verify("rv-2031-01-01-aaaaaa", runner=_runner_recording("t-new", seen))
    assert seen["run"]["domain_hint"] == "j" and seen["run"]["name"] == "verify rv-2031-01-01-aaaaaa"
    assert result["question"] == "How many trips did Mira take?"
    assert result["original"]["fell_through"] is True and result["new"]["answer_basis"] == "graph"
    assert [(c["check"], c["ok"]) for c in result["checks"]] == [("answered", True), ("graph_answer", True)]
    assert result["mechanical_ok"] is True
    log = dz.load("rv-2031-01-01-aaaaaa").sections["Log"]
    assert f"verify run `{seen['run_id']}` → trace `t-new`: mechanical checks pass" in log
    assert dz.load("rv-2031-01-01-aaaaaa").meta["status"] == "fixed"   # never auto-verified


def test_verify_graph_miss_fails_if_it_still_falls_through():
    _turn("t-orig", answer="Two.", fell_through=True, basis="vector")
    _turn("t-new", answer="Three.", fell_through=True, basis="vector")
    _dossier()
    result = verify.verify("rv-2031-01-01-aaaaaa", runner=_runner_recording("t-new", {}))
    assert result["mechanical_ok"] is False
    assert dict((c["check"], c["ok"]) for c in result["checks"])["graph_answer"] is False


def test_verify_non_graph_miss_only_checks_answered_and_accepts_an_existing_run():
    _turn("t-orig", answer="Two.", fell_through=False, basis="graph")
    _turn("t-new", answer="Three.", fell_through=False, basis="graph")
    _dossier()
    run_id = benchmark._create_benchmark_run("x", [{"seq": 1, "qid": "R", "title": "t", "question": "q",
                                                    "eval_comment": None}])
    benchmark._update_benchmark_question(run_id, 1, status="completed", trace_id="t-new")

    def must_not_run(_):
        raise AssertionError("an existing run must not be re-run")

    result = verify.verify("rv-2031-01-01-aaaaaa", run_id=run_id, runner=must_not_run)
    assert [c["check"] for c in result["checks"]] == ["answered"]
    assert result["run_id"] == run_id


def test_verify_untraced_run_fails_cleanly():
    _turn("t-orig", answer="Two.", fell_through=False, basis="graph")
    _dossier()

    def run(run_id):
        benchmark._update_benchmark_question(run_id, 1, status="failed", error_message="agent crashed")

    result = verify.verify("rv-2031-01-01-aaaaaa", runner=run)
    assert result["mechanical_ok"] is False and result["error"] == "agent crashed"
    assert result["checks"][0]["check"] == "traced"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_verify.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `artmind/reviews/verify.py`:

```python
"""Verification (spec 2026-10-11 §9): every dossier carries one regression
question; `regressions_markdown` assembles them into a benchmark file and
`verify` re-runs one as a one-question benchmark, then sets the new trace
beside the original. Mechanical checks only -- grading against the evaluation
comment is the reviewer's, confirming `verified` is the human's."""

import asyncio
import re

from artmind.reviews.dossier import Dossier, DossierError, append_log, iter_dossiers, load, save
from artmind.reviews.validate import _PLACEHOLDER_RE

GRAPH_BASES = ("graph", "mixed")
ANSWER_EXCERPT = 1500
#: The `R-<review_id> — ` prefix of a regression heading; the benchmark parser keeps it in the title.
_R_PREFIX = re.compile(r"^R-\S+\s*[—:-]\s*")


def _regression_text(dossier: Dossier) -> str:
    return _PLACEHOLDER_RE.sub("", dossier.sections.get("Regression question") or "").strip()


def regression(dossier: Dossier) -> dict:
    from artmind.benchmark import _parse_questions

    questions = _parse_questions(_regression_text(dossier))
    if len(questions) != 1 or not questions[0].get("question"):
        raise DossierError(f"{dossier.review_id}: the Regression question section must hold exactly one question")
    q = questions[0]
    title = _R_PREFIX.sub("", q.get("title") or "").strip() or None
    return {"review_id": dossier.review_id, "title": title, "question": q["question"],
            "eval_comment": q.get("eval_comment")}


def regressions_markdown(*, statuses=None) -> str:
    blocks = []
    for _, dossier, error in iter_dossiers():
        if error or (statuses and dossier.meta.get("status") not in statuses):
            continue
        text = _regression_text(dossier)
        if text:
            blocks.append(text)
    return "# artmind review regressions\n\n" + "\n\n".join(blocks) + "\n"


def summarize(shown: dict) -> dict:
    from artmind.traces.path import layer_of, parse_sources

    turn = shown["turn"]
    spans = [s for e in shown["events"] for s in e.get("spans", [])] + list(shown.get("unattributed_spans") or [])
    return {
        "trace_id": turn["trace_id"],
        "answer": (turn.get("answer") or "")[:ANSWER_EXCERPT],
        "fell_through": bool(turn.get("fell_through")),
        "answer_basis": turn.get("answer_basis"),
        "commands": sorted({s["command"] for s in spans if layer_of(s.get("command")) != "discovery"}),
        "views": sorted({(s.get("evidence") or {}).get("view", {}).get("name")
                         for s in spans if (s.get("evidence") or {}).get("view")}),
        "sources": parse_sources(turn.get("answer")),
    }


def compare(original: dict | None, new: dict) -> dict:
    from artmind.traces.path import is_graph_miss

    new_summary = summarize(new)
    checks = [{"check": "answered", "ok": bool(new_summary["answer"])}]
    if original is not None and is_graph_miss(original["turn"]):
        checks.append({"check": "graph_answer",
                       "ok": not new_summary["fell_through"] and new_summary["answer_basis"] in GRAPH_BASES,
                       "why": "the original turn was a graph miss: the fix must let the graph answer"})
    return {"original": summarize(original) if original is not None else None, "new": new_summary,
            "checks": checks, "mechanical_ok": all(c["ok"] for c in checks)}


def _domain_of(dossier: Dossier, original: dict | None) -> str | None:
    for element in dossier.meta.get("elements") or []:
        if "/" in str(element):
            return str(element).split("/", 1)[0]
    domains = (original or {}).get("turn", {}).get("domains") or []
    return domains[0] if domains else None


def _default_runner(run_id: str) -> None:
    from artmind.benchmark import _run_benchmark

    asyncio.run(_run_benchmark(run_id))


def verify(review_id: str, *, run_id: str | None = None, backend: str = "claude-sdk", runner=None) -> dict:
    from artmind import benchmark
    from artmind.reviews.index import index_dossier
    from artmind.traces.analysis import show

    dossier = load(review_id)
    reg = regression(dossier)
    original_id = (dossier.meta.get("trace_ids") or [None])[0]
    original = show(original_id) if original_id else None
    if run_id is None:
        run_id = benchmark._create_benchmark_run(
            str(dossier.path),
            [{"seq": 1, "qid": f"R-{review_id}", "title": reg["title"], "question": reg["question"],
              "eval_comment": reg["eval_comment"]}],
            domain_hint=_domain_of(dossier, original), backend=backend, name=f"verify {review_id}",
        )
        (runner or _default_runner)(run_id)
    question = benchmark._get_benchmark_question(run_id, 1)
    if question is None:
        raise DossierError(f"benchmark run {run_id} has no question 1")
    new_id = question.get("trace_id")
    new = show(new_id) if new_id else None
    result = {"review_id": review_id, "run_id": run_id, "question": reg["question"],
              "eval_comment": reg["eval_comment"], "run_status": question.get("status"),
              "error": question.get("error_message")}
    if new is None:
        result.update({"original": summarize(original) if original else None, "new": None,
                       "checks": [{"check": "traced", "ok": False,
                                   "why": "the benchmark question recorded no trace (failed, or a stale serve daemon)"}],
                       "mechanical_ok": False})
    else:
        result.update(compare(original, new))
    verdict = "pass" if result["mechanical_ok"] else "fail"
    append_log(dossier, f"verify run `{run_id}` → trace `{new_id}`: mechanical checks {verdict} "
                        f"(grade against the evaluation comment, then `artmind review status {review_id} verified`)")
    path = save(dossier)
    try:
        index_dossier(dossier, path)
    except Exception:
        pass  # the file is the source of truth; `review reindex` repairs the index
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_verify.py -v`
Expected: PASS.

`_get_benchmark_run` returns `domain_hint` and `name`. Check `_row_to_run` in `artmind/benchmark.py`, and adapt only the test's key names if they differ.

- [ ] **Step 5: Commit**

```bash
git add artmind/reviews/verify.py test/test_reviews_verify.py
git commit -m "feat(reviews): regression questions from dossiers; verify a fix by re-running one"
```

---

### Task 4: CLI — `shape-report`, `attach`, `regressions`, `verify`

**Files:**
- Modify: `artmind/cli.py` (after `review reextract`)
- Test: `test/test_reviews_cli.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `test/test_reviews_cli.py`)

```python
def test_shape_report_cli(monkeypatch):
    from artmind.reviews import aggregate, sample

    monkeypatch.setattr(aggregate, "shape_report", lambda **kw: [{"element": "d/A", "kw": sorted(kw)}])
    monkeypatch.setattr(sample, "sample", lambda domain, n, **kw: {"domain": domain, "n": n, "seed": kw["seed"]})
    out = json.loads(_run("shape-report", "--domain", "d", "--sample", "3", "--seed", "5", "--compact").stdout)
    assert out["elements"] == [{"element": "d/A", "kw": ["domain", "statuses"]}]
    assert out["sample"] == {"domain": "d", "n": 3, "seed": 5}
    assert "sample" not in json.loads(_run("shape-report", "--compact").stdout)
    result = _run("shape-report", "--sample", "3", ok=False)
    assert result.exit_code != 0 and "--domain" in result.output


def test_attach_cli():
    rid = json.loads(_run("new", "t1", "--compact").stdout)["review_id"]
    store.insert_turn("t2", source="admin", question="Again?", started_at=time.time())
    store.update_turn("t2", answer="x", status="done", retrieval_path="[]", domains='["d"]')
    _run("attach", rid, "t2")
    assert dz.load(rid).meta["trace_ids"] == ["t1", "t2"]
    result = _run("attach", rid, "t2", ok=False)
    assert result.exit_code != 0 and "already" in result.output


def test_regressions_cli():
    rid = json.loads(_run("new", "t1", "--compact").stdout)["review_id"]
    text = _run("regressions").stdout
    assert text.startswith("# artmind review regressions") and f"R-{rid}" in text


def test_verify_cli(monkeypatch):
    from artmind.reviews import verify

    seen = {}
    monkeypatch.setattr(verify, "verify", lambda rid, **kw: seen.update(rid=rid, **kw) or {"mechanical_ok": True})
    out = json.loads(_run("verify", "rv-2031-01-01-aaaaaa", "--runId", "r1", "--compact").stdout)
    assert out == {"mechanical_ok": True} and seen == {"rid": "rv-2031-01-01-aaaaaa", "run_id": "r1", "backend": "claude-sdk"}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --group dev pytest test/test_reviews_cli.py -v`
Expected: the new tests FAIL with `No such command`.

- [ ] **Step 3: Implement** (in `artmind/cli.py`, after `review_reextract`)

```python
@review_group.command("shape-report")
@click.option("--domain", default=None, help="Only elements of this domain (required with --sample)")
@click.option("--all", "all_statuses", is_flag=True, help="Include fixed, verified and dismissed dossiers")
@click.option("--sample", "sample_n", type=int, default=None, help="Also sample N random chunks of --domain")
@click.option("--seed", type=int, default=None, help="Random seed for --sample (repeatable samples)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_shape_report(domain, all_statuses, sample_n, seed, compact) -> None:
    """Shape findings of open dossiers grouped by schema element, most-seen first --
    one systemic problem should be one dossier. With --sample, random chunks of the
    domain with mechanical signals (lost at commit, isolated) to audit proactively."""
    from artmind.reviews import aggregate, sample
    from artmind.reviews.dossier import STATUSES as ALL

    if sample_n is not None and not domain:
        raise click.ClickException("--sample needs --domain")
    payload = {"elements": aggregate.shape_report(domain=domain, statuses=ALL if all_statuses else aggregate.ACTIVE)}
    if sample_n is not None:
        payload["sample"] = sample.sample(domain, sample_n, seed=seed)
    _echo_json(payload, compact)


@review_group.command("attach")
@click.argument("review_id")
@click.argument("trace_id")
def review_attach(review_id, trace_id) -> None:
    """Add another symptom trace to an open dossier (one problem, one dossier)."""
    from artmind.reviews import dossier as dz
    from artmind.reviews import index, scaffold

    d = _load_dossier(review_id)
    try:
        scaffold.attach(d, trace_id)
    except dz.DossierError as exc:
        raise click.ClickException(str(exc)) from exc
    path = dz.save(d)
    index.index_dossier(d, path)
    click.echo(f"{review_id}: attached trace {trace_id} ({len(d.meta['trace_ids'])} traces)")


@review_group.command("regressions")
@click.option("--status", "statuses", multiple=True, type=click.Choice(STATUSES),
              help="Only dossiers with this status (repeatable); default: all")
def review_regressions(statuses) -> None:
    """Every dossier's regression question as one benchmark-format markdown file (stdout)."""
    from artmind.reviews.verify import regressions_markdown

    click.echo(regressions_markdown(statuses=statuses or None), nl=False)


@review_group.command("verify")
@click.argument("review_id")
@click.option("--runId", "run_id", default=None, help="Compare an existing one-question benchmark run instead of running one")
@click.option("--backend", type=click.Choice(["claude-sdk", "acp"]), default="claude-sdk", show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def review_verify(review_id, run_id, backend, compact) -> None:
    """Re-run the dossier's regression question as a one-question benchmark and set the
    new trace beside the original. Mechanical checks only; grade the answer against the
    evaluation comment, then `review status <id> verified`. Runs the agent: costs a turn."""
    from artmind.reviews import dossier as dz
    from artmind.reviews import verify

    try:
        result = verify.verify(review_id, run_id=run_id, backend=backend)
    except dz.DossierError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_cli.py test/test_cli_guide.py -v`, then `uv run artmind review --help`.
Expected: PASS, with 13 commands listed.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_reviews_cli.py
git commit -m "feat(reviews): artmind review shape-report, attach, regressions, verify"
```

---

### Task 5: Skill, docs, spec

**Files:**
- Modify: `artmind/skills/artmind-review/SKILL.md`, `references/stage-walk.md`
- Modify: `CLAUDE.md`, `justfile`, the spec
- Test: `test/test_reviews_skill.py` (append)

- [ ] **Step 1: Write the failing test** (append to `test/test_reviews_skill.py`)

```python
def test_skill_covers_aggregation_and_verification():
    skill = (SKILL / "SKILL.md").read_text()
    for command in ("review shape-report", "review attach", "review verify", "review regressions"):
        assert command in skill
    assert "## Verify a fix" in skill
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run --group dev pytest test/test_reviews_skill.py -v`
Expected: FAIL.

- [ ] **Step 3: Update `SKILL.md`**

1. Replace Ground rule 5 with:

   ```markdown
   5. **One problem, one dossier.** Before opening a dossier run
      `artmind review shape-report --domain <d>`; if an open dossier already holds
      your schema element, `artmind review attach <review_id> <trace_id>` instead
      of opening another.
   ```

2. Add these rows to the Commands table after `review reextract`:

   ```markdown
   | `artmind review shape-report [--domain D] [--sample N --seed S]` | findings by schema element across open dossiers; random chunks to audit proactively |
   | `artmind review attach <review_id> <trace_id>` | add another symptom trace to an open dossier |
   | `artmind review regressions [--status fixed]` | every regression question as one benchmark file |
   | `artmind review verify <review_id> [--runId …]` | re-run the regression question; the new trace beside the original |
   ```

3. Add a section after `## Flow`:

   ```markdown
   ## Verify a fix

   After a coding session (or the operator) applied a dossier's fix — and, for a
   schema or prompt change, re-extracted the affected documents:

   1. `artmind review status <review_id> fixed --note "<what changed>"`.
   2. `artmind review verify <review_id>` — runs the regression question as a
      one-question benchmark (an agent turn: real cost) and prints the original and
      new traces side by side with mechanical checks: `answered`, and for a graph
      miss `graph_answer` (no fall-through, basis graph or mixed).
   3. Grade the new answer against the evaluation comment yourself. Pass →
      `artmind review status <review_id> verified --note "<run id, why it passes>"`.
      Fail → `artmind review status <review_id> open --note "<what is still wrong>"`
      and continue the diagnosis with the new trace (`review attach`).

   Never mark `verified` on mechanical checks alone.

   ## Proactive audits

   `artmind review shape-report --domain <d> --sample 5 --seed <n>` picks random
   chunks with no failing question. `lost_at_commit` lists staging observations
   with no graph entity for the chunk; `isolated` lists observations no staging
   relationship touches. Audit interesting chunks with
   `artmind review shape --domain <d> --chunk <id>`. Open a dossier only for a
   finding a real question would hit; otherwise note it in the report to the user.
   ```

- [ ] **Step 4: Update `references/stage-walk.md`**

In `### missing-view`, after "(`artmind traces list` shows similar questions)", add "— and `artmind review shape-report` shows whether other dossiers already name the element".

- [ ] **Step 5: Update the docs**

1. **`CLAUDE.md`**, in the `artmind/reviews/` row:
   - add these modules: "`aggregate` (findings by schema element), `sample` (random chunks with lost-at-commit / isolated signals), `verify` (regressions file; re-run a regression question as a one-question benchmark and compare traces — never auto-verifies)";
   - extend the CLI list with `shape-report/attach/regressions/verify`.

   In the routing bullet, add the same four commands.

2. **`justfile`**: add these after `review-reextract`:

   ```just
   # findings by schema element across open dossiers  (usage: just review-shape-report [domain])
   review-shape-report domain="":
       uv run artmind review shape-report {{ if domain != "" { "--domain " + domain } else { "" } }}

   # re-run a dossier's regression question and compare traces  (usage: just review-verify <review_id>)
   review-verify review_id:
       uv run artmind review verify {{ review_id }}
   ```

3. **Spec:**
   - §7.5: after the `shape-report` bullet, add: "Findings are the Shape audit table rows (`element | check | …`) plus frontmatter `elements`; `artmind review attach <review_id> <trace_id>` appends a trace (symptom block, trace id, private terms, log line) to an open dossier." Change the `--sample` bullet to say it reports two mechanical signals (`lost_at_commit`, `isolated`) per sampled chunk and points at `review shape --chunk` for the graded audit.
   - §9: add: "`verify` takes `--runId` to compare an existing run instead of starting one, logs the outcome in the dossier, and never sets `verified` itself."

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --group dev pytest test/test_reviews_skill.py test/test_cli_guide.py -q`, then `just --list | grep review`.

- [ ] **Step 7: Commit**

```bash
git add artmind/skills/artmind-review test/test_reviews_skill.py CLAUDE.md justfile docs/superpowers/specs/2026-10-11-query-review-loop-design.md
git commit -m "docs(reviews): skill covers aggregation, proactive audits and verify"
```

---

### Task 6: Full suite and live check

- [ ] **Step 1:** `just dev-test`. Everything passes.
- [ ] **Step 2:** Restart `serve` from the vault.
- [ ] **Step 3:** Run `artmind review shape-report --domain personal_journal --sample 3 --seed 1`. Check that the dossier's elements are listed and that the samples carry signals.
- [ ] **Step 4:** Run `artmind review regressions | head`. Check that it parses with `benchmark._parse_questions`.
- [ ] **Step 5:** Run `artmind review verify rv-2026-10-11-e9a14f`. This is a real agent turn. The narrator fix is **not** applied yet, so the new answer should still undercount. Record that the loop measured it.
- [ ] **Step 6:** Report.

---

## Self-review

**Spec coverage (P4 row of §10):**

| Spec item | Task |
|---|---|
| `shape-report` and appending to dossiers (§7.5) | 1, 4 |
| `--sample` (§7.5) | 2, 4 |
| `regressions` and `verify` (§9) | 3, 4 |
| skill and docs | 5 |
| live check | 6 |

Nothing is deferred: this completes the spec's phases.
