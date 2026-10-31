# Vector Retrieval on Passages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix vector-text's five defects (shallow fusion, post-filtered domains, mixed-scale scores, unprefixed whole-chunk embeddings, no diversity) by moving every text-retrieval vector onto ~1200-char `:DocPassage` nodes, and measure the change with a private retrieval eval.

**Architecture:** `:DocChunk` (6000 chars) stays the extraction/provenance unit and loses its vector. Passages are a pure function of chunk text, derived at commit and reconciled by the permanent embedding sweep; vectors come from a machine-local content-addressed cache or the embedding model, with task prefixes per model. `vector-text` searches passages (vector index filtered by domain inside `SEARCH`, plus BM25), fuses with RRF over deep pools, groups passages into parent chunks (MaxP), re-ranks for diversity (MMR) and returns parent chunks with the matching span.

**Tech Stack:** Python ≥3.14 (uv), Click, Neo4j ≥2026.02 (Cypher 25 `SEARCH`), Ollama embeddings (`nomic-embed-text`), SQLite (stdlib), pytest (hermetic, `CliRunner`, fake sessions).

**Spec:** `docs/superpowers/specs/2026-10-31-vector-retrieval-passages-design.md`. Read it first.

---

## Ground rules for every task

- Work on branch `feat/vector-retrieval-passages` in `/Users/surjitdas/projects/artmind9`. Never commit to `master`.
- Read `CLAUDE.md` once before Task 1. The parts that bite here: tests live in `test/` (singular); run with `uv run --group dev pytest ...`; a mocked Neo4j session accepts any Cypher, so **assert on the Cypher text and the parameters sent**, never on counts the code increments itself.
- Run a single test file while working: `uv run --group dev pytest test/<file>.py -q`. Run the full suite (`just dev-test`, ~3 minutes) at the end of every slice.
- Commit after each task with a conventional message ending in:
  ```
  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
  ```
  (use the model that actually wrote the code).
- Never copy vault content (labels, questions, chunk text) into the checkout. Tests use synthetic text only.
- **Model hint** on each task: `haiku` = mechanical (moves, renames, docs, index lists); `sonnet` = logic. The controller picks the subagent model from it.

## File map

| File | Status | Responsibility |
|---|---|---|
| `artmind/retrieval_eval/__init__.py` | create | package marker |
| `artmind/retrieval_eval/labels.py` | create | labels file: parse, validate, load, append, default path |
| `artmind/retrieval_eval/metrics.py` | create | recall@k, reciprocal rank, documents covered, summaries (pure) |
| `artmind/retrieval_eval/runner.py` | create | run labels through a search function; write and compare runs |
| `artmind/retrieval_eval/generate.py` | create | sample chunks, ask the LLM for one question each |
| `artmind/md_tables.py` | create | markdown table helpers moved out of `ingest.py` |
| `artmind/passages.py` | create | `split`, `Passage`, `split_key`, `passage_id`, `embedding_text`, `passage_rows` (pure) |
| `artmind/passage_store.py` | create | `write_chunk_passages(tx, chunk_id, rows)` (the one passage write) |
| `artmind/embedding_cache.py` | create | SQLite content-addressed vector cache |
| `artmind/retrieval_rank.py` | create | `Unit`, `passage_fuse`, `mmr_select` (pure) |
| `artmind/migrations/__init__.py`, `artmind/migrations/chunk_vectors_to_passages.py` | create | the temporary legacy cleanup |
| `artmind/extraction.py` | modify | prefix table, `embed_key`, `embed_document`, `embed_query` |
| `artmind/embed_sweep.py` | modify | `reconcile_passages` replaces the chunk sweep |
| `artmind/ingest.py` | modify | commit writes passages; chunk vectors and the sidecar go; entity sweep uses prefixes and `_embed_key` |
| `artmind/lifecycle.py` | modify | retire/restore swap passages too |
| `artmind/vector_query.py` | modify | passage-based `vector_text_search`; `embed_question` uses the query prefix |
| `artmind/graph_query.py` | modify | `entity-context` ranks chunks by passages (MaxP) |
| `artmind/setup.py` | modify | passage constraints/indexes; legacy indexes no longer created; migration call |
| `artmind/cli.py` | modify | `retrieval` group; `vector-text` options; `ingest embed-passages`; setup report line |
| `artmind/projection.py`, `artmind/table2graph.py`, `artmind/vault_sync.py`, `artmind/update.py`, `artmind/synthesize.py`, `artmind/canonicalize.py`, `artmind/graph_snapshot.py` | modify | callers of the renamed sweep, prefixes, schema lists |
| `obsidian/artmind-obsidian/src/types.ts`, `src/checklist.ts` | modify | renamed status fields |
| `artmind/skills/artmind-query/SKILL.md`, `CLAUDE.md`, `docs/vault.md`, `justfile` | modify | docs |
| `test/_neo4j_recorder.py` | create | shared fake session that records Cypher and params |

---

# Slice 1 — Retrieval eval (lands alone, for the baseline)

This slice must not import anything from later slices: the baseline is measured on the pre-change retrieval code.

### Task 1: Labels file (model: sonnet)

**Files:**
- Create: `artmind/retrieval_eval/__init__.py` (empty)
- Create: `artmind/retrieval_eval/labels.py`
- Test: `test/test_retrieval_labels.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_retrieval_labels.py
import pytest
import yaml

from artmind.retrieval_eval import labels as rl


def _doc(*entries):
    return {"labels": list(entries)}


NEEDLE = {"id": "r001", "question": "Which method?", "domains": ["papers"],
          "kind": "needle", "relevant": ["d1_001"], "source": "synthetic"}
SURVEY = {"id": "r002", "question": "What do they say about X?", "domains": ["papers"],
          "kind": "survey", "relevant_docs": ["d1", "d2"], "source": "hand"}


def test_parses_needle_and_survey():
    out = rl.parse_labels(_doc(NEEDLE, SURVEY))
    assert [l.id for l in out] == ["r001", "r002"]
    assert out[0].relevant == ("d1_001",) and out[0].kind == "needle"
    assert out[1].relevant_docs == ("d1", "d2") and out[1].source == "hand"


def test_reports_every_problem_at_once():
    bad = [
        {"id": "", "question": "q", "domains": ["d"], "kind": "needle", "relevant": ["x"]},
        {"id": "r1", "question": "", "domains": [], "kind": "other"},
        {"id": "r2", "question": "q", "domains": ["d"], "kind": "needle", "relevant": []},
        {"id": "r3", "question": "q", "domains": ["d"], "kind": "survey"},
        {"id": "r2", "question": "q", "domains": ["d"], "kind": "needle", "relevant": ["x"], "source": "bot"},
    ]
    with pytest.raises(rl.LabelError) as exc:
        rl.parse_labels(_doc(*bad))
    text = " | ".join(exc.value.errors)
    for needle in ("labels[0]: id", "labels[1]: question", "labels[1]: domains", "labels[1]: kind",
                   "labels[2]: needle", "labels[3]: survey", "labels[4]: duplicate id", "labels[4]: source"):
        assert needle in text


def test_top_level_must_hold_a_list():
    with pytest.raises(rl.LabelError):
        rl.parse_labels({"nope": 1})


def test_append_keeps_existing_and_numbers_new(tmp_path):
    path = tmp_path / "retrieval_labels.yaml"
    path.write_text(yaml.safe_dump(_doc(NEEDLE)), encoding="utf-8")
    new = [rl.RetrievalLabel(id="", question="Q2", domains=("papers",), kind="needle",
                             relevant=("d2_003",), source="synthetic")]
    added = rl.append_labels(path, new)
    assert [l.id for l in added] == ["r002"]
    assert [l.id for l in rl.load_labels(path)] == ["r001", "r002"]


def test_append_creates_the_file(tmp_path):
    path = tmp_path / "bench" / "retrieval_labels.yaml"
    new = [rl.RetrievalLabel(id="", question="Q", domains=("p",), kind="needle", relevant=("c",), source="synthetic")]
    rl.append_labels(path, new)
    assert [l.id for l in rl.load_labels(path)] == ["r001"]


def test_default_path_outside_a_vault(monkeypatch):
    monkeypatch.setattr(rl, "vault_dir", lambda: None)
    assert rl.default_path() is None
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_retrieval_labels.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'artmind.retrieval_eval'`.

- [ ] **Step 3: Implement**

```python
# artmind/retrieval_eval/labels.py
"""The retrieval eval's labelled set: <vault>/benchmarking/retrieval_labels.yaml
(spec 2026-10-31-vector-retrieval-passages-design.md §6.1).

Private: real questions and corpus ids. It stays in the vault, never in a checkout.
Labels name chunk ids (stable across the passage change), never passage ids.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

from artmind.query_plan.labels import vault_dir

KINDS = ("needle", "survey")
SOURCES = ("synthetic", "hand")


class LabelError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class RetrievalLabel:
    id: str
    question: str
    domains: tuple[str, ...]
    kind: str
    relevant: tuple[str, ...] = ()
    relevant_docs: tuple[str, ...] = ()
    source: str = "hand"


def default_path() -> Path | None:
    vault = vault_dir()
    return vault / "benchmarking" / "retrieval_labels.yaml" if vault else None


def _str_list(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        return None
    return [v.strip() for v in value]


def parse_labels(data) -> list[RetrievalLabel]:
    if not isinstance(data, dict) or not isinstance(data.get("labels"), list):
        raise LabelError(["top level must be a mapping with a `labels` list"])
    errors: list[str] = []
    out: list[RetrievalLabel] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["labels"]):
        where = f"labels[{i}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        lid = str(raw.get("id") or "").strip()
        question = str(raw.get("question") or "").strip()
        domains = _str_list(raw.get("domains"))
        kind = raw.get("kind")
        source = raw.get("source", "hand")
        relevant = _str_list(raw.get("relevant", [])) or []
        relevant_docs = _str_list(raw.get("relevant_docs", [])) or []
        problems = []
        if not lid:
            problems.append(f"{where}: id is required")
        elif lid in seen:
            problems.append(f"{where}: duplicate id {lid!r}")
        if not question:
            problems.append(f"{where}: question is required")
        if not domains:
            problems.append(f"{where}: domains must be a non-empty list of names")
        if kind not in KINDS:
            problems.append(f"{where}: kind must be one of {', '.join(KINDS)}")
        elif kind == "needle" and not relevant:
            problems.append(f"{where}: needle needs a non-empty `relevant` list of chunk ids")
        elif kind == "survey" and not relevant_docs:
            problems.append(f"{where}: survey needs a non-empty `relevant_docs` list of document ids")
        if source not in SOURCES:
            problems.append(f"{where}: source must be one of {', '.join(SOURCES)}")
        if lid:
            seen.add(lid)
        if problems:
            errors.extend(problems)
            continue
        out.append(RetrievalLabel(id=lid, question=question, domains=tuple(domains), kind=kind,
                                  relevant=tuple(relevant), relevant_docs=tuple(relevant_docs),
                                  source=source))
    if errors:
        raise LabelError(errors)
    return out


def load_labels(path: Path) -> list[RetrievalLabel]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LabelError([f"cannot read {path}: {exc}"]) from exc
    return parse_labels(data)


def _to_yaml_entry(label: RetrievalLabel) -> dict:
    entry = {k: v for k, v in asdict(label).items() if v not in ((), "")}
    for key in ("domains", "relevant", "relevant_docs"):
        if key in entry:
            entry[key] = list(entry[key])
    return entry


def _next_id(taken: set[str]) -> str:
    n = 1
    while f"r{n:03d}" in taken:
        n += 1
    return f"r{n:03d}"


def append_labels(path: Path, new: list[RetrievalLabel]) -> list[RetrievalLabel]:
    """Append `new` to the labels file (created if absent). A label with an empty id
    gets the next free `rNNN`. Written atomically. Returns the labels as appended."""
    path = Path(path)
    existing = load_labels(path) if path.exists() else []
    taken = {l.id for l in existing}
    added: list[RetrievalLabel] = []
    for label in new:
        if not label.id or label.id in taken:
            label = replace(label, id=_next_id(taken))
        taken.add(label.id)
        added.append(label)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"labels": [_to_yaml_entry(l) for l in [*existing, *added]]},
                          sort_keys=False, allow_unicode=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".retrieval_labels.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)
    return added
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_retrieval_labels.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/retrieval_eval/__init__.py artmind/retrieval_eval/labels.py test/test_retrieval_labels.py
git commit -m "feat(retrieval-eval): labels file for the retrieval eval"
```

### Task 2: Metrics (model: haiku)

**Files:**
- Create: `artmind/retrieval_eval/metrics.py`
- Test: `test/test_retrieval_metrics.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_retrieval_metrics.py
from artmind.retrieval_eval import metrics as m


def test_recall_at_k_is_hit_or_miss():
    assert m.recall_at_k(["a", "b", "c"], {"c"}, 3) == 1.0
    assert m.recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0


def test_reciprocal_rank_uses_first_hit():
    assert m.reciprocal_rank(["a", "b", "c"], {"b", "c"}) == 0.5
    assert m.reciprocal_rank(["a"], {"z"}) == 0.0


def test_docs_covered_is_a_fraction():
    assert m.docs_covered_at_k(["d1", "d1", "d3"], {"d1", "d2"}, 3) == 0.5
    assert m.docs_covered_at_k(["d1", "d2"], {"d1", "d2"}, 1) == 0.5


def test_summarise_by_kind():
    per = [
        {"kind": "needle", "latency_s": 1.0, "recall": {"5": 1.0, "20": 1.0}, "rr": 1.0},
        {"kind": "needle", "latency_s": 3.0, "recall": {"5": 0.0, "20": 1.0}, "rr": 0.25},
        {"kind": "survey", "latency_s": 2.0, "docs_covered": {"5": 0.5, "20": 1.0}},
    ]
    s = m.summarise(per, (5, 20))
    assert s["needle"] == {"questions": 2, "recall@5": 0.5, "recall@20": 1.0, "mrr": 0.625, "latency_s": 2.0}
    assert s["survey"] == {"questions": 1, "docs_covered@5": 0.5, "docs_covered@20": 1.0, "latency_s": 2.0}


def test_summarise_skips_absent_kinds():
    assert m.summarise([], (5,)) == {}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_retrieval_metrics.py -q` — Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# artmind/retrieval_eval/metrics.py
"""Retrieval metrics (spec 2026-10-31 §6.2). Pure functions over ranked ids."""
from __future__ import annotations

from collections.abc import Iterable, Sequence


def recall_at_k(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return 1.0 if any(r in relevant for r in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for i, r in enumerate(ranked, start=1):
        if r in relevant:
            return 1.0 / i
    return 0.0


def docs_covered_at_k(ranked_docs: Sequence[str], relevant_docs: set[str], k: int) -> float:
    if not relevant_docs:
        return 0.0
    return len(set(ranked_docs[:k]) & relevant_docs) / len(relevant_docs)


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 4) if values else 0.0


def summarise(per_question: list[dict], ks: Sequence[int]) -> dict:
    out: dict = {}
    needles = [q for q in per_question if q["kind"] == "needle"]
    surveys = [q for q in per_question if q["kind"] == "survey"]
    if needles:
        s: dict = {"questions": len(needles)}
        for k in ks:
            s[f"recall@{k}"] = _mean(q["recall"][str(k)] for q in needles)
        s["mrr"] = _mean(q["rr"] for q in needles)
        s["latency_s"] = _mean(q["latency_s"] for q in needles)
        out["needle"] = s
    if surveys:
        s = {"questions": len(surveys)}
        for k in ks:
            s[f"docs_covered@{k}"] = _mean(q["docs_covered"][str(k)] for q in surveys)
        s["latency_s"] = _mean(q["latency_s"] for q in surveys)
        out["survey"] = s
    return out
```

- [ ] **Step 4: Run the tests** — `uv run --group dev pytest test/test_retrieval_metrics.py -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/retrieval_eval/metrics.py test/test_retrieval_metrics.py
git commit -m "feat(retrieval-eval): recall, MRR and documents-covered metrics"
```

### Task 3: Eval runner (model: sonnet)

**Files:**
- Create: `artmind/retrieval_eval/runner.py`
- Test: `test/test_retrieval_runner.py`

The runner must work against **both** the current `vector_text_search(domains, question, topK, as_of=None)` (no `diversity` argument) and the later one. So it passes `diversity` only when given.

- [ ] **Step 1: Write the failing tests**

```python
# test/test_retrieval_runner.py
import json

from artmind.retrieval_eval import runner
from artmind.retrieval_eval.labels import RetrievalLabel

LABELS = [
    RetrievalLabel(id="r001", question="q1", domains=("p",), kind="needle", relevant=("c2",)),
    RetrievalLabel(id="r002", question="q2", domains=("p",), kind="survey", relevant_docs=("d1", "d9")),
]


def fake_search(calls):
    def search(domains, question, top_k, diversity):
        calls.append((tuple(domains), question, top_k, diversity))
        return {"rows": [
            {"chat": {"id": "x"}},  # chat rows are ignored
            {"chunk": {"id": "c1", "doc_id": "d1"}},
            {"chunk": {"id": "c2", "doc_id": "d2"}},
        ]}
    return search


def test_run_eval_scores_each_kind():
    calls = []
    ticks = iter([0.0, 0.5, 1.0, 2.0])
    run = runner.run_eval(LABELS, ks=(1, 5), search=fake_search(calls), clock=lambda: next(ticks))
    assert calls == [(("p",), "q1", 5, None), (("p",), "q2", 5, None)]
    q1, q2 = run["questions"]
    assert q1["chunk_ids"] == ["c1", "c2"] and q1["recall"] == {"1": 0.0, "5": 1.0} and q1["rr"] == 0.5
    assert q2["docs_covered"] == {"1": 0.5, "5": 0.5}
    assert run["summary"]["needle"]["mrr"] == 0.5
    assert run["ks"] == [1, 5] and run["diversity"] is None and run["labels"] == 2


def test_default_search_omits_diversity_when_none(monkeypatch):
    seen = {}

    def vts(domains, question, top_k, **kwargs):
        seen.update(kwargs)
        return {"rows": []}

    from artmind import vector_query
    monkeypatch.setattr(vector_query, "vector_text_search", vts)
    runner._default_search(["p"], "q", 5, None)
    assert seen == {}
    runner._default_search(["p"], "q", 5, 0.3)
    assert seen == {"diversity": 0.3}


def test_write_and_compare(tmp_path):
    a = {"summary": {"needle": {"questions": 2, "recall@5": 0.5, "mrr": 0.4}}}
    b = {"summary": {"needle": {"questions": 2, "recall@5": 0.75, "mrr": 0.5}}}
    path = runner.write_run(a, tmp_path)
    assert json.loads(path.read_text())["summary"] == a["summary"]
    assert runner.compare_runs(a, b) == {"needle": {"recall@5": 0.25, "mrr": 0.1}}
```

- [ ] **Step 2: Run to see them fail** — `uv run --group dev pytest test/test_retrieval_runner.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# artmind/retrieval_eval/runner.py
"""Run the labelled set through vector-text and score it (spec 2026-10-31 §6.2).

Runs in-process (no `serve` proxy). Runs are written to
<vault>/.artmind/logs/retrieval-eval/<timestamp>.json: private, vault only.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from artmind.retrieval_eval import metrics
from artmind.retrieval_eval.labels import RetrievalLabel

Search = Callable[[list[str], str, int, float | None], dict]


def _default_search(domains: list[str], question: str, top_k: int, diversity: float | None) -> dict:
    from artmind import vector_query

    kwargs = {} if diversity is None else {"diversity": diversity}
    return vector_query.vector_text_search(domains, question, top_k, **kwargs)


def run_eval(
    labels: Sequence[RetrievalLabel],
    *,
    ks: Sequence[int] = (5, 20),
    diversity: float | None = None,
    search: Search | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> dict:
    search = search or _default_search
    ks = sorted(set(int(k) for k in ks))
    top = max(ks)
    per: list[dict] = []
    for label in labels:
        t0 = clock()
        result = search(list(label.domains), label.question, top, diversity)
        latency = clock() - t0
        chunks = [r["chunk"] for r in (result.get("rows") or []) if r.get("chunk")]
        chunk_ids = [c.get("id") for c in chunks][:top]
        doc_ids = [c.get("doc_id") for c in chunks][:top]
        entry: dict = {"id": label.id, "kind": label.kind, "latency_s": round(latency, 3), "chunk_ids": chunk_ids}
        if label.kind == "needle":
            relevant = set(label.relevant)
            entry["recall"] = {str(k): metrics.recall_at_k(chunk_ids, relevant, k) for k in ks}
            entry["rr"] = metrics.reciprocal_rank(chunk_ids, relevant)
        else:
            relevant_docs = set(label.relevant_docs)
            entry["docs_covered"] = {str(k): metrics.docs_covered_at_k(doc_ids, relevant_docs, k) for k in ks}
        per.append(entry)
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ks": ks,
        "diversity": diversity,
        "labels": len(labels),
        "summary": metrics.summarise(per, ks),
        "questions": per,
    }


def runs_dir() -> Path:
    import paths

    return Path(paths.LOGS_DIR) / "retrieval-eval"


def write_run(run: dict, directory: Path | None = None) -> Path:
    directory = Path(directory or runs_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(run, indent=2), encoding="utf-8")
    return path


def compare_runs(before: dict, after: dict) -> dict:
    """Per kind, per metric: after - before (questions and latency left out of the diff)."""
    out: dict = {}
    for kind, b_stats in (after.get("summary") or {}).items():
        a_stats = (before.get("summary") or {}).get(kind) or {}
        diff = {
            metric: round(value - a_stats[metric], 4)
            for metric, value in b_stats.items()
            if metric not in ("questions", "latency_s") and metric in a_stats
        }
        if diff:
            out[kind] = diff
    return out
```

- [ ] **Step 4: Run the tests** — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/retrieval_eval/runner.py test/test_retrieval_runner.py
git commit -m "feat(retrieval-eval): eval runner, run files and run comparison"
```

### Task 4: Synthetic label generation (model: sonnet)

**Files:**
- Create: `artmind/retrieval_eval/generate.py`
- Create: `test/_neo4j_recorder.py`
- Test: `test/test_retrieval_generate.py`

- [ ] **Step 1: Create the shared recorder** (used by later tasks too)

```python
# test/_neo4j_recorder.py
"""A fake Neo4j session/transaction that records every query.

Tests assert on `calls` (cypher, params): which query ran and what it was sent.
`respond(cypher, params)` returns the rows for one call (default: none).
"""


class FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class Recorder:
    def __init__(self, respond=None):
        self.calls: list[tuple[str, dict]] = []
        self._respond = respond or (lambda cypher, params: [])

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return FakeResult(self._respond(cypher, params))

    def queries_containing(self, fragment: str) -> list[tuple[str, dict]]:
        return [(c, p) for c, p in self.calls if fragment in c]

    # context-manager form so it can stand in for `neo4j_session()` / `read_session()`
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
```

- [ ] **Step 2: Write the failing tests**

```python
# test/test_retrieval_generate.py
from _neo4j_recorder import Recorder

from artmind.retrieval_eval import generate

CHUNKS = [{"id": f"d1_{i:03d}", "doc_id": "d1", "text": "x" * 300} for i in range(1, 6)]
CHUNKS.append({"id": "d1_999", "doc_id": "d1", "text": "short"})


def session():
    return Recorder(lambda cypher, params: CHUNKS if "MATCH (c:DocChunk)" in cypher else [])


def test_sample_is_seeded_and_skips_short_chunks():
    a = generate.sample_chunks(session(), "papers", 3, seed=7)
    b = generate.sample_chunks(session(), "papers", 3, seed=7)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert len(a) == 3 and "d1_999" not in [r["id"] for r in a]


def test_sample_scopes_the_domain_family():
    s = session()
    generate.sample_chunks(s, "papers", 2, seed=1)
    cypher, params = s.calls[0]
    assert "c._domain = $domain OR c._domain STARTS WITH ($domain + '.')" in cypher
    assert params["domain"] == "papers"


def test_generate_builds_needle_labels_and_reports_failures():
    answers = iter(['{"question": "What does it say?"}', "not json", '{"question": ""}'])
    labels, warnings = generate.generate_labels(session(), "papers", 3, seed=2, llm=lambda p: next(answers))
    assert len(labels) == 1
    label = labels[0]
    assert label.kind == "needle" and label.source == "synthetic" and label.domains == ("papers",)
    assert label.question == "What does it say?" and len(label.relevant) == 1
    assert len(warnings) == 2


def test_prompt_asks_for_paraphrase_and_carries_the_text():
    seen = []
    generate.generate_labels(session(), "papers", 1, seed=3, llm=lambda p: seen.append(p) or '{"question": "q?"}')
    assert "Paraphrase" in seen[0] and "x" * 300 in seen[0]
```

Note: `from _neo4j_recorder import Recorder` works because pytest puts `test/` on `sys.path` (rootdir-relative, no `__init__.py` in `test/`). If it does not, check `pyproject.toml` `[tool.pytest.ini_options]` for `pythonpath` and add `"test"` there.

- [ ] **Step 3: Run to see them fail** — `uv run --group dev pytest test/test_retrieval_generate.py -q` — Expected: FAIL.

- [ ] **Step 4: Implement**

```python
# artmind/retrieval_eval/generate.py
"""Synthetic retrieval labels (spec 2026-10-31 §6.2): sample chunks, ask the
configured LLM for one question each. The sampled text goes to the provider
(logged in llm_calls.log, like extraction). From a Claude Code shell, run with
`env -u ANTHROPIC_BASE_URL` (CLAUDE.md, Testing implications §5).
"""
from __future__ import annotations

import random
from collections.abc import Callable

from artmind.retrieval_eval.labels import RetrievalLabel

MIN_CHARS = 200
SAMPLE_CHARS = 3000

QUESTION_PROMPT = """You write one evaluation question for a document search system.

Below is a passage from a document. Write ONE question that a person could ask
and that this passage answers. Rules:
- Paraphrase: do not copy distinctive phrases, section titles or rare words from
  the passage unless the question cannot be asked without them (a person's,
  product's or organisation's name is fine).
- The question must make sense to someone who has not seen the passage.
- Answer with JSON only: {{"question": "..."}}

<passage>
{text}
</passage>
"""


def sample_chunks(session, domain: str, count: int, seed: int) -> list[dict]:
    rows = session.run(
        """
        MATCH (c:DocChunk)
        WHERE c._domain = $domain OR c._domain STARTS WITH ($domain + '.')
        RETURN c.id AS id, c.doc_id AS doc_id, c.text AS text
        ORDER BY c.id
        """,
        domain=domain,
    ).data()
    rows = [r for r in rows if len((r.get("text") or "").strip()) >= MIN_CHARS]
    return random.Random(seed).sample(rows, min(count, len(rows)))


def _default_llm() -> Callable[[str], str]:
    from artmind.extraction import call_llm
    from utils.functions import load_env, resolve_llm_model

    model = resolve_llm_model(load_env())
    return lambda prompt: call_llm(model, prompt, call_type="retrieval_label")


def generate_labels(
    session, domain: str, count: int, seed: int, *, llm: Callable[[str], str] | None = None
) -> tuple[list[RetrievalLabel], list[str]]:
    from artmind.extraction import parse_json_response

    llm = llm or _default_llm()
    labels: list[RetrievalLabel] = []
    warnings: list[str] = []
    for row in sample_chunks(session, domain, count, seed):
        prompt = QUESTION_PROMPT.format(text=row["text"][:SAMPLE_CHARS])
        try:
            parsed = parse_json_response(llm(prompt))
        except Exception as exc:  # provider error or unparseable answer
            warnings.append(f"{row['id']}: no question ({exc})")
            continue
        question = str((parsed or {}).get("question") or "").strip() if isinstance(parsed, dict) else ""
        if not question:
            warnings.append(f"{row['id']}: empty question")
            continue
        labels.append(RetrievalLabel(id="", question=question, domains=(domain,), kind="needle",
                                     relevant=(row["id"],), source="synthetic"))
    return labels, warnings
```

Check `artmind/extraction.py:147` `parse_json_response`: if it returns `None` on bad input instead of raising, the `isinstance` guard covers it; if it raises, the `except` does.

- [ ] **Step 5: Run the tests** — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/retrieval_eval/generate.py test/_neo4j_recorder.py test/test_retrieval_generate.py
git commit -m "feat(retrieval-eval): synthetic needle labels from sampled chunks"
```

### Task 5: `artmind retrieval` CLI group and docs (model: sonnet)

**Files:**
- Modify: `artmind/cli.py` (new group after the `plan` group, ~line 4400; `COMMAND_GROUPS` at ~line 259)
- Modify: `justfile`, `CLAUDE.md`
- Test: `test/test_retrieval_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_retrieval_cli.py
import json

import yaml
from click.testing import CliRunner

from artmind.cli import cli
from artmind.retrieval_eval import labels as rl
from artmind.retrieval_eval import runner


def _labels_file(tmp_path):
    path = tmp_path / "retrieval_labels.yaml"
    path.write_text(yaml.safe_dump({"labels": [
        {"id": "r001", "question": "q", "domains": ["p"], "kind": "needle", "relevant": ["c1"]}]}))
    return path


def test_validate_ok(tmp_path):
    res = CliRunner().invoke(cli, ["retrieval", "labels", "validate", "--file", str(_labels_file(tmp_path)), "--compact"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["ok"] is True


def test_validate_reports_errors_and_exits_1(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("labels: [{id: r1}]")
    res = CliRunner().invoke(cli, ["retrieval", "labels", "validate", "--file", str(path), "--compact"])
    assert res.exit_code == 1
    assert json.loads(res.output)["errors"]


def test_eval_writes_a_run_and_compares(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_default_search",
                        lambda d, q, k, div: {"rows": [{"chunk": {"id": "c1", "doc_id": "d1"}}]})
    monkeypatch.setattr(runner, "runs_dir", lambda: tmp_path / "runs")
    before = tmp_path / "before.json"
    before.write_text(json.dumps({"summary": {"needle": {"questions": 1, "recall@5": 0.0, "mrr": 0.0}}}))
    res = CliRunner().invoke(cli, ["retrieval", "eval", "--file", str(_labels_file(tmp_path)),
                                   "--topK", "5", "--compare", str(before), "--compact"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["summary"]["needle"]["recall@5"] == 1.0
    assert out["compare"] == {"needle": {"recall@5": 1.0, "mrr": 1.0}}
    assert list((tmp_path / "runs").glob("*.json"))


def test_generate_appends(tmp_path, monkeypatch):
    from artmind.retrieval_eval import generate
    monkeypatch.setattr(generate, "generate_labels", lambda s, d, c, seed: (
        [rl.RetrievalLabel(id="", question="q?", domains=(d,), kind="needle", relevant=("c9",), source="synthetic")],
        ["c8: empty question"]))
    import artmind.graph_query as gq
    from _neo4j_recorder import Recorder
    monkeypatch.setattr(gq, "read_session", lambda: Recorder())
    path = tmp_path / "l.yaml"
    res = CliRunner().invoke(cli, ["retrieval", "labels", "generate", "--domain", "p", "--count", "1",
                                   "--file", str(path), "--compact"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["added"] == ["r001"] and out["warnings"] == ["c8: empty question"]
```

- [ ] **Step 2: Run to see them fail** — `uv run --group dev pytest test/test_retrieval_cli.py -q` — Expected: FAIL (`No such command 'retrieval'`).

- [ ] **Step 3: Implement the group in `artmind/cli.py`** (place it right after the last `plan_group` command)

```python
@cli.group("retrieval")
def retrieval_group() -> None:
    """Measure vector-text: a private labelled set and eval runs (spec 2026-10-31 §6).

    Not proxied. Labels and runs hold real questions and corpus ids: they stay in the
    vault (`<vault>/benchmarking/retrieval_labels.yaml`,
    `<vault>/.artmind/logs/retrieval-eval/`), never in a checkout.
    """


@retrieval_group.group("labels")
def retrieval_labels_group() -> None:
    """The labelled set (`<vault>/benchmarking/retrieval_labels.yaml`)."""


def _retrieval_labels_path(file_path: Path | None) -> Path:
    from artmind.retrieval_eval import labels as rl

    path = file_path or rl.default_path()
    if path is None:
        raise click.ClickException("not inside a vault: pass --file")
    return path


@retrieval_labels_group.command("generate")
@click.option("--domain", required=True, help="Domain whose chunks to sample")
@click.option("--count", type=int, default=50, show_default=True, help="Chunks to sample (one question each)")
@click.option("--seed", type=int, default=0, show_default=True, help="Sampling seed (same seed, same chunks)")
@click.option("--file", "file_path", type=click.Path(path_type=Path), default=None,
              help="Labels file. Default: <vault>/benchmarking/retrieval_labels.yaml")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def retrieval_labels_generate(domain: str, count: int, seed: int, file_path: Path | None, compact: bool) -> None:
    """Sample chunks and have the configured LLM write one question each (appended as synthetic).

    Sends the sampled text to the configured provider. From a Claude Code shell run it
    with `env -u ANTHROPIC_BASE_URL` (CLAUDE.md §5).
    """
    from artmind import graph_query
    from artmind.retrieval_eval import generate
    from artmind.retrieval_eval import labels as rl

    path = _retrieval_labels_path(file_path)
    with graph_query.read_session() as session:
        new, warnings = generate.generate_labels(session, domain, count, seed)
    added = rl.append_labels(path, new)
    _echo_json({"command": "retrieval labels generate", "file": str(path),
                "added": [l.id for l in added], "warnings": warnings}, compact)


@retrieval_labels_group.command("validate")
@click.option("--file", "file_path", type=click.Path(path_type=Path), default=None,
              help="Labels file. Default: <vault>/benchmarking/retrieval_labels.yaml")
@click.option("--checkIds", "check_ids", is_flag=True, help="Also check (needs Neo4j) that every chunk and document id exists")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def retrieval_labels_validate(file_path: Path | None, check_ids: bool, compact: bool) -> None:
    """Check every rule of the labels file and report all problems at once (exit 1 on any)."""
    from artmind import graph_query
    from artmind.retrieval_eval import labels as rl

    path = _retrieval_labels_path(file_path)
    try:
        loaded = rl.load_labels(path)
    except rl.LabelError as exc:
        _echo_json({"command": "retrieval labels validate", "file": str(path), "ok": False, "errors": exc.errors}, compact)
        raise SystemExit(1)
    errors: list[str] = []
    if check_ids:
        chunk_ids = sorted({c for l in loaded for c in l.relevant})
        doc_ids = sorted({d for l in loaded for d in l.relevant_docs})
        with graph_query.read_session() as session:
            found_chunks = {r["id"] for r in session.run(
                "UNWIND $ids AS id MATCH (c:DocChunk {id: id}) RETURN id", ids=chunk_ids).data()}
            found_docs = {r["id"] for r in session.run(
                "UNWIND $ids AS id MATCH (d:Document {id: id}) RETURN id", ids=doc_ids).data()}
        errors += [f"chunk {c} not in the graph" for c in chunk_ids if c not in found_chunks]
        errors += [f"document {d} not in the graph" for d in doc_ids if d not in found_docs]
    _echo_json({"command": "retrieval labels validate", "file": str(path), "ok": not errors,
                "labels": len(loaded), "errors": errors}, compact)
    if errors:
        raise SystemExit(1)


@retrieval_group.command("eval")
@click.option("--file", "file_path", type=click.Path(path_type=Path), default=None,
              help="Labels file. Default: <vault>/benchmarking/retrieval_labels.yaml")
@click.option("--topK", "top_k", default="5,20", show_default=True, help="Cut-offs to score (comma-separated)")
@click.option("--diversity", type=float, default=None, help="Passed to vector-text (omit for its default)")
@click.option("--compare", "compare_path", type=click.Path(exists=True, path_type=Path), default=None,
              help="An earlier run file to diff against")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def retrieval_eval_cmd(file_path: Path | None, top_k: str, diversity: float | None,
                       compare_path: Path | None, compact: bool) -> None:
    """Run every label through vector-text in-process; report recall@K, MRR, documents covered."""
    from artmind.retrieval_eval import labels as rl
    from artmind.retrieval_eval import runner

    path = _retrieval_labels_path(file_path)
    try:
        loaded = rl.load_labels(path)
    except rl.LabelError as exc:
        raise click.ClickException("; ".join(exc.errors)) from exc
    try:
        ks = [int(k) for k in top_k.split(",") if k.strip()]
    except ValueError as exc:
        raise click.ClickException("--topK must be comma-separated integers") from exc
    run = runner.run_eval(loaded, ks=ks, diversity=diversity)
    run_path = runner.write_run(run)
    out = {"command": "retrieval eval", "run": str(run_path), "summary": run["summary"]}
    if compare_path:
        out["compare"] = runner.compare_runs(json.loads(compare_path.read_text(encoding="utf-8")), run)
    _echo_json(out, compact)
```

(`json` and `Path` are already imported at the top of `cli.py`; check, and add if not.)

- [ ] **Step 4: Route it in `COMMAND_GROUPS`** (`artmind/cli.py` ~line 259)

In the `"artmind"` list, the Query entry becomes:
```python
        {"name": "Query", "commands": ["query", "plan", "retrieval", "views", "insights", "traces", "review"]},
```
and add a key after `"artmind plan"`:
```python
    "artmind retrieval": [
        {"name": "Retrieval eval", "commands": ["labels", "eval"]},
    ],
```

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_retrieval_cli.py test/test_cli_guide.py -q`
Expected: PASS. If `test_cli_guide.py` names an unrouted command, route it the same way.

- [ ] **Step 6: Docs**

`justfile` — add next to the other CLI recipes:
```
# Retrieval eval against the current vault (labels in <vault>/benchmarking/)
retrieval-eval *args:
    uv run artmind retrieval eval {{args}}
```
`CLAUDE.md` — add a row to the repo-layout table after the `artmind/query_plan/` row:
```
| `artmind/retrieval_eval/` | Retrieval eval for `vector-text` (spec `docs/superpowers/specs/2026-10-31-vector-retrieval-passages-design.md` §6): `labels` (`<vault>/benchmarking/retrieval_labels.yaml`, private; needle labels name chunk ids, survey labels document ids), `generate` (samples chunks, one LLM question each, sends text to the configured provider), `metrics`, `runner` (in-process, runs in `<vault>/.artmind/logs/retrieval-eval/`). CLI: `artmind retrieval labels generate/validate`, `artmind retrieval eval [--compare RUN]` (not proxied). |
```
and in "Command routing quick reference" add:
```
- `artmind retrieval <cmd>` — `labels generate|validate`, `eval` (not proxied; labels and runs hold real questions and stay in the vault)
```

- [ ] **Step 7: Full suite, then commit**

Run: `just dev-test` — Expected: all pass.
```bash
git add artmind/cli.py justfile CLAUDE.md test/test_retrieval_cli.py
git commit -m "feat(retrieval-eval): artmind retrieval labels/eval commands"
```

## ⛔ CHECKPOINT A — baseline (stop and ask the user)

Report Slice 1 done and ask the user to measure the baseline **before** Slice 2 starts. The user runs, inside the vault (from a Claude Code shell, the first command needs `env -u ANTHROPIC_BASE_URL`):

```bash
env -u ANTHROPIC_BASE_URL ARTMIND_NO_PROXY=1 artmind retrieval labels generate --domain <domain> --count 50 --seed 1
```
```bash
artmind retrieval labels validate --checkIds
```
```bash
ARTMIND_NO_PROXY=1 artmind retrieval eval
```

They also add 10–20 hand-written labels (`source: hand`, including `kind: survey` ones) to `<vault>/benchmarking/retrieval_labels.yaml`, and keep the printed run path as the baseline. Do not continue until the user says the baseline exists.

---

# Slice 2 — Embedding foundations (pure code, nothing wired in yet)

### Task 6: Move markdown table helpers to `artmind/md_tables.py` (model: haiku)

**Files:**
- Create: `artmind/md_tables.py`
- Modify: `artmind/ingest.py` (`_is_table_row`, `_is_table_separator_row`, `_reattach_table_headers`, ~line 1680–1720)

- [ ] **Step 1:** In `artmind/ingest.py` find `def _is_table_row`, `def _is_table_separator_row` and `def _reattach_table_headers` (around line 1680–1720). Move all three, verbatim, into a new `artmind/md_tables.py` under these public names: `is_table_row`, `is_table_separator_row`, `reattach_table_headers` (rename inside the moved bodies too). Give the module the docstring `"""Markdown table helpers shared by chunk and passage splitting."""` and copy whatever imports the bodies need (likely `re`).
- [ ] **Step 2:** In `artmind/ingest.py` add `from artmind.md_tables import is_table_row as _is_table_row, is_table_separator_row as _is_table_separator_row, reattach_table_headers as _reattach_table_headers` with the other `artmind` imports, so every existing call site keeps working unchanged.
- [ ] **Step 3:** Run `uv run --group dev pytest test/ -q -k "table or split or chunk"` — Expected: PASS (pure move).
- [ ] **Step 4:** Commit: `git commit -am "refactor(ingest): move markdown table helpers to md_tables" && git add artmind/md_tables.py && git commit --amend --no-edit`

### Task 7: `artmind/passages.py` (model: sonnet)

**Files:**
- Create: `artmind/passages.py`
- Test: `test/test_passages.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_passages.py
import pytest

from artmind import passages as P


def para(n, word="alpha"):
    return " ".join([word] * n) + "."


def test_short_chunk_is_one_trimmed_passage():
    text = "\n  Hello world.  \n"
    out = P.split(text, 1200)
    assert len(out) == 1
    p = out[0]
    assert (p.seq, p.text) == (1, "Hello world.")
    assert text[p.char_start:p.char_end] == "Hello world."


def test_blank_chunk_has_no_passages():
    assert P.split("   \n\n ", 1200) == []


def test_offsets_slice_the_parent_and_cover_it():
    text = "\n\n".join(para(40, w) for w in ("alpha", "beta", "gamma", "delta", "eps", "zeta"))
    out = P.split(text, 500)
    assert len(out) > 2
    for p in out:
        assert text[p.char_start:p.char_end] == p.text
        assert len(p.text) <= 500
        assert not p.text[0].isspace() and not p.text[-1].isspace()
    assert out[0].char_start == 0 and out[-1].char_end == len(text)
    for a, b in zip(out, out[1:]):
        assert text[a.char_end:b.char_start].strip() == ""   # overlap, or a whitespace-only gap
        assert b.char_start > a.char_start                    # progress


def test_prefers_paragraph_boundaries():
    text = para(60, "alpha") + "\n\n" + para(60, "beta")
    first = P.split(text, 500)[0]
    assert first.text.endswith(".") and "beta" not in first.text


def test_never_cuts_mid_word():
    text = " ".join(f"word{i}" for i in range(400))
    for p in P.split(text, 300):
        assert not p.text.split()[0][0].isdigit()   # starts at a word start
        assert p.text.split()[-1].startswith("word")


def test_table_header_reattached_to_a_later_passage():
    rows = "\n".join(f"| r{i} | {'v' * 40} |" for i in range(40))
    text = "| col | val |\n| --- | --- |\n" + rows
    out = P.split(text, 400)
    assert len(out) > 1
    assert out[1].text.startswith("| col | val |\n| --- | --- |")


def test_deterministic():
    text = "\n\n".join(para(50, w) for w in ("a", "b", "c", "d"))
    assert P.split(text, 300) == P.split(text, 300)


def test_split_key_and_ids(monkeypatch):
    monkeypatch.setattr(P, "passage_chars", lambda: 900)
    assert P.split_key() == f"{P.SPLIT_VERSION}:900"
    assert P.passage_id("doc_003", 2) == "doc_003_p02"


def test_embedding_text_puts_context_first():
    assert P.embedding_text("Body.", "Paper > Methods") == "Paper > Methods\nBody."
    assert P.embedding_text("Body.", "") == "Body."


def test_passage_rows_carry_chunk_fields(monkeypatch):
    monkeypatch.setattr(P, "passage_chars", lambda: 1200)
    chunk = {"id": "d1_001", "doc_id": "d1", "_domain": "papers", "text": "Some text.",
             "header_path": "Intro", "valid_from": "2026-01-01"}
    rows = P.passage_rows(chunk, "My Paper")
    assert rows == [{
        "id": "d1_001_p01", "chunk_id": "d1_001",
        "props": {"id": "d1_001_p01", "chunk_id": "d1_001", "doc_id": "d1", "_domain": "papers",
                  "seq": 1, "char_start": 0, "char_end": 10, "text": "Some text.",
                  "context": "My Paper > Intro", "_split_key": f"{P.SPLIT_VERSION}:1200",
                  "valid_from": "2026-01-01"},
    }]
```

- [ ] **Step 2: Run to see them fail** — `uv run --group dev pytest test/test_passages.py -q` — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# artmind/passages.py
"""Passages: the retrieval unit under a DocChunk (spec 2026-10-31 §2.1).

A DocChunk (~6000 chars) is the extraction and provenance unit; its passages
(~1200 chars) carry every text-retrieval vector. `split` is a pure function of
the chunk text and `split_key()`, so every machine derives identical passages.
No I/O, no Neo4j, no embedding calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from artmind.md_tables import reattach_table_headers

#: Bump whenever the splitting rules change; passages cut under another
#: version are rebuilt by the sweep (`_split_key` on each passage).
SPLIT_VERSION = 1
DEFAULT_CHARS = 1200
OVERLAP_CHARS = 150

_SENTENCE_END = re.compile(r"[.!?][\"')\]]?\s")


@dataclass(frozen=True)
class Passage:
    seq: int          # 1-based
    char_start: int   # offset in the parent chunk's text
    char_end: int
    text: str         # chunk_text[char_start:char_end], with a table header reattached when cut mid-table


def passage_chars() -> int:
    from utils.functions import load_env

    try:
        value = int(load_env().get("ARTMIND_KG_PASSAGE_CHARS", DEFAULT_CHARS))
    except (TypeError, ValueError):
        return DEFAULT_CHARS
    return max(value, 2 * OVERLAP_CHARS)


def split_key() -> str:
    """What a passage was cut under: the rules' version and the target size."""
    return f"{SPLIT_VERSION}:{passage_chars()}"


def passage_id(chunk_id: str, seq: int) -> str:
    return f"{chunk_id}_p{seq:02d}"


def _cut(text: str, start: int, hard_end: int) -> int:
    """Best end in the second half of [start, hard_end): paragraph, line, sentence, word.
    A line break ranks above a sentence so table rows and list items stay whole."""
    lo = start + (hard_end - start) // 2
    window = text[lo:hard_end]
    i = window.rfind("\n\n")
    if i != -1:
        return lo + i + 2
    i = window.rfind("\n")
    if i != -1:
        return lo + i + 1
    last = None
    for m in _SENTENCE_END.finditer(window):
        last = m
    if last is not None:
        return lo + last.end()
    i = max(window.rfind(" "), window.rfind("\n"))
    if i != -1:
        return lo + i + 1
    return hard_end


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _next_start(text: str, start: int, end: int) -> int:
    """Start of the next passage, up to OVERLAP_CHARS before `end`: at a line start
    when the overlap holds one (a table row, so its header can be reattached), else
    at a word start."""
    s = max(end - OVERLAP_CHARS, start + 1)
    nl = text.find("\n", s, end)
    if nl != -1:
        return nl + 1
    while s < end and not text[s - 1].isspace():
        s += 1
    return s


def split(chunk_text: str, chars: int | None = None) -> list[Passage]:
    target = chars or passage_chars()
    n = len(chunk_text)
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        hard_end = min(start + target, n)
        end = n if hard_end == n else _cut(chunk_text, start, hard_end)
        s, e = _trim(chunk_text, start, end)
        if e > s:
            spans.append((s, e))
        if end >= n:
            break
        start = _next_start(chunk_text, start, end)
    texts = reattach_table_headers([chunk_text[s:e] for s, e in spans])
    return [Passage(i, s, e, t) for i, ((s, e), t) in enumerate(zip(spans, texts), start=1)]


def embedding_text(text: str, context: str | None) -> str:
    """The text a passage is embedded as (before the model's task prefix)."""
    return f"{context}\n{text}" if context else text


def passage_rows(chunk: dict, title: str | None) -> list[dict]:
    """One chunk's passages as write rows for `passage_store.write_chunk_passages`."""
    context = " > ".join(p for p in (title, chunk.get("header_path")) if p)
    key = split_key()
    rows = []
    for p in split(chunk.get("text") or ""):
        pid = passage_id(chunk["id"], p.seq)
        props = {
            "id": pid, "chunk_id": chunk["id"], "doc_id": chunk.get("doc_id"),
            "_domain": chunk.get("_domain"), "seq": p.seq,
            "char_start": p.char_start, "char_end": p.char_end, "text": p.text,
            "context": context, "_split_key": key,
        }
        for field in ("valid_from", "valid_to"):
            if chunk.get(field) is not None:
                props[field] = chunk[field]
        rows.append({"id": pid, "chunk_id": chunk["id"], "props": props})
    return rows
```

Note on the table test: when a passage starts with a reattached header, `p.text != chunk_text[s:e]`. `test_offsets_slice_the_parent_and_cover_it` uses no tables, so it holds; `Passage.text` documents the exception.

- [ ] **Step 4: Run the tests** — Expected: PASS. If `test_never_cuts_mid_word` fails at a hard cut (a single 300+ char token), that case is out of scope; the test text has short words.

- [ ] **Step 5: Commit**

```bash
git add artmind/passages.py test/test_passages.py
git commit -m "feat(passages): pure passage splitter with split key and write rows"
```

### Task 8: Task prefixes and the embed key (model: sonnet)

**Files:**
- Modify: `artmind/extraction.py` (beside `embed_text`, line 11)
- Modify: `artmind/vector_query.py:125-127` (`embed_question`)
- Test: `test/test_embed_prefixes.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_embed_prefixes.py
from artmind import extraction, vector_query


def test_prefixes_by_model_base_name():
    assert extraction.embed_prefixes("nomic-embed-text:latest") == ("search_document: ", "search_query: ")
    assert extraction.embed_prefixes("library/nomic-embed-text:v1.5") == ("search_document: ", "search_query: ")
    assert extraction.embed_prefixes("mxbai-embed-large") == ("", "")


def test_embed_key_depends_on_model_and_prefix_version(monkeypatch):
    a = extraction.embed_key("nomic-embed-text:latest")
    assert a == extraction.embed_key("nomic-embed-text:latest") and len(a) == 16
    assert a != extraction.embed_key("other-model")
    monkeypatch.setattr(extraction, "PREFIX_VERSION", extraction.PREFIX_VERSION + 1)
    assert a != extraction.embed_key("nomic-embed-text:latest")


def test_document_and_query_embeddings_get_their_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.0])
    extraction.embed_document("nomic-embed-text:latest", "Body")
    extraction.embed_query("nomic-embed-text:latest", "Question?")
    assert sent == ["search_document: Body", "search_query: Question?"]


def test_embed_question_uses_the_query_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(vector_query, "_embedding_model", lambda: "nomic-embed-text:latest")
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.0])
    vector_query.embed_question("Where?")
    assert sent == ["search_query: Where?"]
```

- [ ] **Step 2: Run to see them fail** — Expected: FAIL (`embed_prefixes` missing).

- [ ] **Step 3: Implement in `artmind/extraction.py`** (directly below `embed_text`)

```python
#: Bump when `_PREFIXES` changes: every stored vector's `_embed_key` then goes stale
#: and the sweeps re-embed it (spec 2026-10-31 §2.2, §2.4).
PREFIX_VERSION = 1

# Task prefixes an embedding model was trained with, keyed by its base name.
# (document prefix, query prefix). A model not listed gets none.
_PREFIXES = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
}


def _model_base(model: str) -> str:
    return model.split(":", 1)[0].rsplit("/", 1)[-1]


def embed_prefixes(model: str) -> tuple[str, str]:
    return _PREFIXES.get(_model_base(model), ("", ""))


def embed_key(model: str) -> str:
    """Identity of how a stored vector was made: model + prefix table version."""
    return sha1(f"{model}|{PREFIX_VERSION}".encode("utf-8")).hexdigest()[:16]


def embed_document(model: str, text: str) -> list[float]:
    """Embed stored text (passages, chats, entities) with the model's document prefix."""
    return embed_text(model, embed_prefixes(model)[0] + text)


def embed_query(model: str, text: str) -> list[float]:
    """Embed a search query with the model's query prefix."""
    return embed_text(model, embed_prefixes(model)[1] + text)
```
Add `from hashlib import sha1` to the imports at the top of `extraction.py` if absent.

`embed_document`/`embed_query` call `embed_text` through the module global so tests can monkeypatch `extraction.embed_text`.

- [ ] **Step 4: Change `embed_question` in `artmind/vector_query.py`**

Replace lines 4 and 125–127:
```python
from artmind import extraction
```
```python
def embed_question(question: str, model: str | None = None) -> list[float]:
    return extraction.embed_query(model or _embedding_model(), question)
```
Remove the now-unused `from artmind.extraction import embed_text as _embed_text` import.

- [ ] **Step 5: Run tests**

Run: `uv run --group dev pytest test/test_embed_prefixes.py test/test_vector_query.py -q`
Expected: PASS (existing tests monkeypatch `embed_question` itself).

- [ ] **Step 6: Commit**

```bash
git add artmind/extraction.py artmind/vector_query.py test/test_embed_prefixes.py
git commit -m "feat(embeddings): per-model task prefixes and embed key; queries use the query prefix"
```

### Task 9: Content-addressed embedding cache (model: sonnet)

**Files:**
- Create: `artmind/embedding_cache.py`
- Test: `test/test_embedding_cache.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_embedding_cache.py
from artmind.embedding_cache import EmbeddingCache, cached_embed


def test_roundtrip_and_miss(tmp_path):
    cache = EmbeddingCache(tmp_path / "c.sqlite")
    k = EmbeddingCache.key("ek1", "text")
    assert cache.get(k) is None
    cache.put(k, [0.25, -1.5])
    assert EmbeddingCache(tmp_path / "c.sqlite").get(k) == [0.25, -1.5]


def test_key_depends_on_embed_key_and_text():
    assert EmbeddingCache.key("a", "t") != EmbeddingCache.key("b", "t")
    assert EmbeddingCache.key("a", "t") != EmbeddingCache.key("a", "u")


def test_cached_embed_calls_the_model_once(tmp_path):
    cache = EmbeddingCache(tmp_path / "c.sqlite")
    calls = []
    embed = lambda text: calls.append(text) or [1.0]
    assert cached_embed(cache, "ek", "hello", embed) == [1.0]
    assert cached_embed(cache, "ek", "hello", embed) == [1.0]
    assert calls == ["hello"]


def test_unusable_location_disables_the_cache(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    cache = EmbeddingCache(blocker / "sub" / "c.sqlite")   # parent is a file
    cache.put("k", [1.0])
    assert cache.get("k") is None
```

- [ ] **Step 2: Run to see them fail** — Expected: FAIL.

- [ ] **Step 3: Implement**

```python
# artmind/embedding_cache.py
"""Machine-local, content-addressed embedding cache (spec 2026-10-31 §3.1).

Key: sha1(embed_key, embedded text). A vector is a pure function of that pair, so
any hit is valid on this machine: a re-commit, a new document version with
unchanged passages, or a rebuilt graph never asks the model again. Lives in the
data dir (`<data>/embedding_cache.sqlite`), never committed. Unusable location ->
the cache is off (logged once) and every call goes to the model.
"""
from __future__ import annotations

import array
import hashlib
import sqlite3
from collections.abc import Callable
from pathlib import Path

from loguru import logger


def cache_path() -> Path:
    import paths

    return Path(paths.ARTMIND_DATA_DIR) / "embedding_cache.sqlite"


class EmbeddingCache:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else cache_path()
        self._conn: sqlite3.Connection | None = None
        self._disabled = False

    @staticmethod
    def key(embed_key: str, text: str) -> str:
        return hashlib.sha1(f"{embed_key}\0{text}".encode("utf-8")).hexdigest()

    def _db(self) -> sqlite3.Connection | None:
        if self._disabled:
            return None
        if self._conn is None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self.path)
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
                )
            except (OSError, sqlite3.Error) as exc:
                logger.warning("Embedding cache off ({}): {}", self.path, exc)
                self._disabled = True
                return None
        return self._conn

    def get(self, key: str) -> list[float] | None:
        db = self._db()
        if db is None:
            return None
        row = db.execute("SELECT vector FROM vectors WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        vec = array.array("d")
        vec.frombytes(row[0])
        return vec.tolist()

    def put(self, key: str, vector: list[float]) -> None:
        db = self._db()
        if db is None:
            return
        db.execute("INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                   (key, array.array("d", vector).tobytes()))
        db.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def cached_embed(cache: EmbeddingCache | None, embed_key: str, text: str,
                 embed: Callable[[str], list[float]]) -> list[float]:
    if cache is None:
        return embed(text)
    key = EmbeddingCache.key(embed_key, text)
    hit = cache.get(key)
    if hit is not None:
        return hit
    vector = embed(text)
    if vector:
        cache.put(key, vector)
    return vector
```

- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Commit**

```bash
git add artmind/embedding_cache.py test/test_embedding_cache.py
git commit -m "feat(embeddings): machine-local content-addressed vector cache"
```

Slice 2 ends: run `just dev-test` (expected: all pass).

---

# Slice 3 — Graph writes

### Task 10: Passage constraints and indexes in setup (model: haiku)

**Files:**
- Modify: `artmind/setup.py` (`_setup_neo4j` ~line 683–960; summary dict ~line 1030–1102)
- Test: `test/test_setup_passage_indexes.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_setup_passage_indexes.py
from _neo4j_recorder import Recorder

from artmind import setup


def test_passage_schema_created_and_legacy_chunk_indexes_not():
    s = Recorder()
    setup._setup_neo4j(s, 768)
    text = "\n".join(c for c, _ in s.calls)
    assert "CREATE CONSTRAINT passage_id IF NOT EXISTS FOR (n:DocPassage) REQUIRE n.id IS UNIQUE" in text
    assert "CREATE CONSTRAINT passage_history_id IF NOT EXISTS FOR (n:DocPassageHistory) REQUIRE n.id IS UNIQUE" in text
    for idx in ("passage_chunk_id", "passage_doc_id", "passage_domain", "passage_history_doc_id"):
        assert f"CREATE INDEX {idx} IF NOT EXISTS" in text
    assert "CREATE VECTOR INDEX passage_embedding IF NOT EXISTS" in text
    assert "ON p.embedding WITH [p._domain]" in text
    assert "`vector.dimensions`: 768" in text
    assert "CREATE FULLTEXT INDEX passage_text_ft IF NOT EXISTS FOR (p:DocPassage) ON EACH [p.text]" in text
    assert "chunk_embedding" not in text and "chunk_text_ft" not in text
```

- [ ] **Step 2: Run to see it fail.**
- [ ] **Step 3: Edit `_setup_neo4j`.** Next to the `chunk_id` / `chunk_history_id` constraints add:
```python
    session.run(
        "CREATE CONSTRAINT passage_id IF NOT EXISTS FOR (n:DocPassage) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT passage_history_id IF NOT EXISTS FOR (n:DocPassageHistory) REQUIRE n.id IS UNIQUE"
    )
```
Next to the DocChunk indexes add:
```python
    # DocPassage (spec 2026-10-31 §3.4): chunk_id for MaxP / re-split, doc_id for the
    # lifecycle label swaps, _domain for the sweep and the fulltext leg's filter.
    session.run("CREATE INDEX passage_chunk_id IF NOT EXISTS FOR (n:DocPassage) ON (n.chunk_id)")
    session.run("CREATE INDEX passage_doc_id IF NOT EXISTS FOR (n:DocPassage) ON (n.doc_id)")
    session.run("CREATE INDEX passage_domain IF NOT EXISTS FOR (n:DocPassage) ON (n._domain)")
    session.run("CREATE INDEX passage_history_doc_id IF NOT EXISTS FOR (n:DocPassageHistory) ON (n.doc_id)")
```
Replace the `chunk_embedding` vector index block (line ~906) with:
```python
    # Filter property `_domain` lets vector-text filter inside SEARCH (Neo4j >= 2026.02).
    session.run(
        f"CREATE VECTOR INDEX passage_embedding IF NOT EXISTS "
        f"FOR (p:DocPassage) ON p.embedding WITH [p._domain] "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )
```
Replace the `chunk_text_ft` fulltext line with:
```python
        session.run(
            "CREATE FULLTEXT INDEX passage_text_ft IF NOT EXISTS FOR (p:DocPassage) ON EACH [p.text]"
        )
```
Update the comment above `chunk_doc_id_id` only if it mentions vectors.
- [ ] **Step 4: Edit the summary in `setup_all`:** add `"passage_id"`, `"passage_history_id"` to `neo4j_constraints`; add the four passage indexes to `neo4j_indexes`; in `neo4j_vector_indexes` replace `chunk_embedding (dim=...)` with `f"passage_embedding (dim={embedding_dim}, filter: _domain)"`; in `neo4j_fulltext_indexes` replace `"chunk_text_ft"` with `"passage_text_ft"`.
- [ ] **Step 5:** `uv run --group dev pytest test/test_setup_passage_indexes.py test/ -q -k setup` — Expected: PASS (fix any existing setup test that listed `chunk_embedding`).
- [ ] **Step 6: Commit** — `git commit -am "feat(setup): passage constraints and indexes; stop creating chunk vector and fulltext indexes"` (and `git add` the new test first).

### Task 11: The passage write, used at commit (model: sonnet)

**Files:**
- Create: `artmind/passage_store.py`
- Modify: `artmind/ingest.py` — `_commit_document_tx` chunk loop (~line 4149–4184), `_retract_prior_version` (~line 2526–2546), merge step `chunk_node` (line 3612)
- Test: `test/test_passage_commit.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_passage_commit.py
from _neo4j_recorder import Recorder

from artmind import ingest, passage_store


ROWS = [{"id": "d1_001_p01", "chunk_id": "d1_001", "props": {"id": "d1_001_p01", "text": "t"}}]


def test_write_chunk_passages_retires_old_then_merges_new():
    tx = Recorder()
    passage_store.write_chunk_passages(tx, "d1_001", ROWS)
    (retire, p1), (merge, p2) = tx.calls
    assert "MATCH (p:DocPassage {chunk_id: $chunk_id})" in retire and "SET p:DocPassageHistory" in retire
    assert p1 == {"chunk_id": "d1_001"}
    assert "UNWIND $rows AS row" in merge and "OPTIONAL MATCH (o2:DocPassageHistory {id: row.id})" in merge
    assert "SET n = row.props" in merge and "MERGE (n)-[:PART_OF]->(c)" in merge
    assert p2 == {"rows": ROWS}


def test_write_chunk_passages_with_no_rows_only_retires():
    tx = Recorder()
    passage_store.write_chunk_passages(tx, "d1_001", [])
    assert len(tx.calls) == 1


def test_retract_prior_version_moves_passages_to_history():
    tx = Recorder(lambda c, p: [{"n": 0}])
    ingest._retract_prior_version(tx, "papers", "d1")
    swaps = tx.queries_containing("MATCH (p:DocPassage {doc_id: $doc_id})")
    assert swaps and "REMOVE p:DocPassage SET p:DocPassageHistory" in swaps[0][0]
    assert swaps[0][1] == {"doc_id": "d1"}
```

Then add, in the same file, a commit-level test. First read the existing tests of `_commit_document_tx` (`grep -rn "_commit_document_tx" test/ | head`) and copy the smallest one's setup (its `staged` dict and fake tx). Assert, using the parameters sent:
- no `tx.run` call for the `DocChunk` merge carries an `embedding` key in its props, even when the staged chunk has one;
- `passage_store.write_chunk_passages` is called once per staged chunk with `chunk_id` equal to that chunk's `id` and rows from `passages.passage_rows(chunk, title)` (monkeypatch `passage_store.write_chunk_passages` with a recorder);
- `title` is the staged document's `title`, falling back to its `name`;
- the summary's `passage_chunk_ids` lists every staged chunk id.

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement `artmind/passage_store.py`**

```python
# artmind/passage_store.py
"""The one write of a chunk's passages (spec 2026-10-31 §3.1, §3.3).

Retires the chunk's current passages to :DocPassageHistory, then MERGEs the new
rows under whichever label each id carries (`ingest._merge_relabeled`'s rule:
passage ids are deterministic, so a stable passage reuses its node) and leaves
them under :DocPassage holding exactly `row.props` -- no `embedding`: the sweep
fills it from the embedding cache or the model. Runs in the caller's transaction.
"""
from __future__ import annotations

RETIRE_CHUNK_PASSAGES = """
MATCH (p:DocPassage {chunk_id: $chunk_id})
REMOVE p:DocPassage SET p:DocPassageHistory
"""

MERGE_PASSAGES = """
UNWIND $rows AS row
OPTIONAL MATCH (o1:DocPassage {id: row.id})
OPTIONAL MATCH (o2:DocPassageHistory {id: row.id})
WITH row, coalesce(o1, o2) AS existing
FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
    CREATE (n:DocPassage) SET n = row.props)
FOREACH (_ IN CASE WHEN existing IS NULL THEN [] ELSE [1] END |
    REMOVE existing:DocPassageHistory SET existing:DocPassage SET existing = row.props)
WITH row
MATCH (n:DocPassage {id: row.id})
MATCH (c:DocChunk {id: row.chunk_id})
MERGE (n)-[:PART_OF]->(c)
"""


def write_chunk_passages(tx, chunk_id: str, rows: list[dict]) -> int:
    tx.run(RETIRE_CHUNK_PASSAGES, chunk_id=chunk_id)
    if rows:
        tx.run(MERGE_PASSAGES, rows=rows)
    return len(rows)
```

- [ ] **Step 4: Wire it into `_commit_document_tx`** (`artmind/ingest.py` ~line 4149). Replace the chunk loop body and the summary lines after it with:

```python
    title = document.get("title") or document.get("name")
    for chunk in staged["chunks"]:
        # Chunks carry no vector any more (spec 2026-10-31 §1): every text-retrieval
        # vector lives on the chunk's passages, written right below without one and
        # filled by the post-commit passage sweep.
        chunk_props = _flatten_props({k: v for k, v in chunk.items() if k != "embedding"})
        _merge_relabeled(tx, "DocChunk", "DocChunkHistory", chunk["id"], chunk_props, replace=False)
        tx.run(
            """
            MATCH (c:DocChunk {id: $id})
            MATCH (d:Document {id: $doc_id})
            MERGE (c)-[:PART_OF]->(d)
            """,
            id=chunk["id"],
            doc_id=chunk["doc_id"],
        )
        passage_store.write_chunk_passages(tx, chunk["id"], passages.passage_rows(chunk, title))
    summary["chunks"] = len(staged["chunks"])
    summary["passage_chunk_ids"] = [chunk["id"] for chunk in staged["chunks"]]
```
Keep the existing `_merge_relabeled` comment block above, but delete the long comment about `"embedding": []` (it describes chunk vectors). Check what the local variable holding the staged document is called in `_commit_document_tx` (the code calls `_flatten_props(document)` a few lines above, at ~4141): use that name. Add `from artmind import passage_store, passages` to the module imports (no cycle: neither imports `ingest`).

Note `_merge_relabeled(..., replace=False)` is additive, so a `DocChunk.embedding` already in the graph survives this write; the legacy migration (Task 19) removes those.

- [ ] **Step 5: Replace every reader of `summary["unembedded_chunk_ids"]`** (`grep -rn unembedded_chunk_ids artmind`): `ingest.py:4381` and `table2graph.py:1387` become `summary.get("passage_chunk_ids") or []` (they are rewired to the passage sweep in Task 13; leave the function names alone here).

- [ ] **Step 6: `_retract_prior_version`** (~line 2538): after the DocChunk relabel, add:
```python
    rec = tx.run(
        """
        MATCH (p:DocPassage {doc_id: $doc_id})
        REMOVE p:DocPassage SET p:DocPassageHistory
        RETURN count(p) AS n
        """,
        doc_id=doc_id,
    ).single()
    result["passages"] = int(rec["n"]) if rec else 0
```
and initialise `result = {"observations_demoted": 0, "chunks": 0, "passages": 0}`. Update its docstring's index names: "`passage_text_ft` / `passage_embedding`" instead of "`chunk_text_ft` / `chunk_embedding`".

- [ ] **Step 7: Stop staging chunk vectors** (line 3612): remove `"embedding"` from the tuple:
```python
        chunk_node = {k: data[k] for k in ("name", "doc_id", "text", "_domain") if k in data}
```

- [ ] **Step 8: Run** `uv run --group dev pytest test/test_passage_commit.py test/ -q -k "commit or retract or staged"` — fix existing tests that asserted a chunk embedding was written (they now assert it is not). Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add artmind/passage_store.py artmind/ingest.py artmind/table2graph.py test/test_passage_commit.py
git commit -m "feat(ingest): commit writes passages; chunks no longer carry vectors"
```

### Task 12: Lifecycle swaps passages (model: haiku)

**Files:**
- Modify: `artmind/lifecycle.py:31-76` (`_transition`)
- Test: `test/test_lifecycle.py` (add tests)

- [ ] **Step 1: Add failing tests to `test/test_lifecycle.py`**

```python
def test_transition_swaps_passages_both_ways():
    from _neo4j_recorder import Recorder
    from artmind import lifecycle, projection

    for to_history, frm, to in ((True, "DocPassage", "DocPassageHistory"), (False, "DocPassageHistory", "DocPassage")):
        tx = Recorder(lambda c, p: [{"n": 0}])
        orig = projection.keys_for_document
        projection.keys_for_document = lambda tx, doc_id: set()
        try:
            lifecycle._transition(tx, "d1", to_history=to_history, rebuild=False)
        finally:
            projection.keys_for_document = orig
        swaps = tx.queries_containing(f"MATCH (p:{frm} {{doc_id: $doc_id}})")
        assert swaps and f"REMOVE p:{frm} SET p:{to}" in swaps[0][0]
        assert swaps[0][1] == {"doc_id": "d1"}
```
(If `test_lifecycle.py` already has a fixture that patches `keys_for_document`, use it instead of the manual patch.)

- [ ] **Step 2: Run to see it fail.**
- [ ] **Step 3: Edit `_transition`.** Add `from_passage, to_passage = "DocPassage", "DocPassageHistory"` in the `to_history` branch and the reverse in the `else` branch; after the chunk swap add:
```python
    tx.run(
        f"MATCH (p:{from_passage} {{doc_id: $doc_id}}) REMOVE p:{from_passage} SET p:{to_passage}",
        doc_id=doc_id,
    )
```
In the docstring replace "`chunk_text_ft` and `chunk_embedding`, both of which are defined only `FOR (c:DocChunk)`" with "`passage_text_ft` and `passage_embedding`, both defined only `FOR (p:DocPassage)`", and "a document's chunks" with "a document's chunks and passages". A passage keeps its vector under the History label (outside every index), so a restore needs no sweep.
- [ ] **Step 4: Run** `uv run --group dev pytest test/test_lifecycle.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -am "feat(lifecycle): retire and restore move passages with their chunks"`

### Task 13: `reconcile_passages` replaces the chunk sweep (model: sonnet)

**Files:**
- Modify: `artmind/embed_sweep.py` (rewrite)
- Modify: `artmind/ingest.py` (`_sweep_chunk_embeddings` → `_sweep_passages`, `_sweep_domain`, line 4381)
- Modify: `artmind/cli.py` (imports line 31; `ingest embed-chunks` line 1292 → `embed-passages`; `_run_chunk_embed_sweep` line 1393; `COMMAND_GROUPS` ingest entry line 282; text at 4627)
- Modify: `artmind/table2graph.py:1387`, `artmind/vault_sync.py:1646`, `artmind/projection.py:1650-1690`
- Modify: `obsidian/artmind-obsidian/src/types.ts:190,207,223`, `src/checklist.ts:171`
- Test: `test/test_embed_sweep.py` (rewrite for the new function)

- [ ] **Step 1: Write the failing tests** (replace the chunk-sweep tests in `test/test_embed_sweep.py`; keep any test of unrelated helpers in that file)

```python
# test/test_embed_sweep.py
from _neo4j_recorder import Recorder

from artmind import embed_sweep, passages

KEY = "ek"


def _respond(build_rows=(), embed_rows=(), chat_rows=()):
    state = {"build": list(build_rows), "embed": list(embed_rows), "chat": list(chat_rows)}

    def respond(cypher, params):
        if "AS chunk_text" in cypher:               # chunks needing passages
            rows, state["build"] = state["build"], []
            return rows
        if "MATCH (p:DocPassage)" in cypher and "RETURN p.id AS id" in cypher:
            rows, state["embed"] = state["embed"], []
            return rows
        if "MATCH (c:UserChat)" in cypher and "RETURN c.id AS id" in cypher:
            rows, state["chat"] = state["chat"], []
            return rows
        return []
    return respond


def test_builds_passages_for_chunks_without_them(monkeypatch):
    calls = []
    monkeypatch.setattr(embed_sweep.passage_store, "write_chunk_passages",
                        lambda tx, cid, rows: calls.append((cid, rows)) or len(rows))
    s = Recorder(_respond(build_rows=[{"id": "d1_001", "doc_id": "d1", "_domain": "p",
                                       "chunk_text": "Some text.", "header_path": "Intro", "title": "Doc"}]))
    out = embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key=KEY, cache=None)
    (cypher, params), = s.queries_containing("AS chunk_text")
    assert "NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }" in cypher
    assert "p._split_key <> $split_key" in cypher and "trim(coalesce(c.text, '')) <> ''" in cypher
    assert params["split_key"] == passages.split_key()
    assert calls[0][0] == "d1_001" and calls[0][1][0]["props"]["context"] == "Doc > Intro"
    assert out["chunks_split"] == 1


def test_embeds_stale_passages_with_context_and_key():
    s = Recorder(_respond(embed_rows=[{"id": "d1_001_p01", "text": "Body.", "context": "Doc > Intro"}]))
    seen = []
    out = embed_sweep.reconcile_passages(s, embed=lambda t: seen.append(t) or [0.5], embed_key=KEY, cache=None)
    (select, sp), = s.queries_containing("RETURN p.id AS id")
    assert "p.embedding IS NULL OR p._embed_key IS NULL OR p._embed_key <> $embed_key" in select
    assert sp["embed_key"] == KEY
    assert seen == ["Doc > Intro\nBody."]
    (write, wp), = s.queries_containing("SET p.embedding = $embedding, p._embed_key = $embed_key")
    assert wp == {"id": "d1_001_p01", "embedding": [0.5], "embed_key": KEY}
    assert out["embedded"] == 1 and out["remaining"] == 0


def test_a_failed_embedding_is_skipped_and_the_loop_ends():
    rows = [{"id": f"p{i}", "text": "t", "context": ""} for i in range(3)]
    s = Recorder(_respond(embed_rows=rows))

    def boom(t):
        raise RuntimeError("down")
    out = embed_sweep.reconcile_passages(s, embed=boom, embed_key=KEY, cache=None, batch_size=3)
    assert out == {"chunks_split": 0, "embedded": 0, "remaining": 3, "chats_embedded": 0}
    assert not s.queries_containing("SET p.embedding")


def test_scopes_reach_both_steps():
    s = Recorder()
    embed_sweep.reconcile_passages(s, chunk_ids=["d1_001"], embed=lambda t: [1.0], embed_key=KEY, cache=None)
    for cypher, params in s.calls:
        if "AS chunk_text" in cypher:
            assert "c.id IN $chunk_ids" in cypher
        if "RETURN p.id AS id" in cypher:
            assert "p.chunk_id IN $chunk_ids" in cypher
    assert not s.queries_containing("MATCH (c:UserChat)")   # chats only on unscoped/domain runs


def test_empty_scope_is_a_no_op():
    s = Recorder()
    assert embed_sweep.reconcile_passages(s, chunk_ids=[], embed_key=KEY) == \
        {"chunks_split": 0, "embedded": 0, "remaining": 0, "chats_embedded": 0}
    assert s.calls == []


def test_unscoped_run_reembeds_stale_chats():
    s = Recorder(_respond(chat_rows=[{"id": "u1", "raw_text": "hi"}]))
    out = embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key=KEY, cache=None)
    (select, _), = s.queries_containing("MATCH (c:UserChat)")
    assert "c._embed_key <> $embed_key" in select
    assert out["chats_embedded"] == 1
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Rewrite `artmind/embed_sweep.py`**

```python
"""The passage sweep (spec 2026-10-31 §3.2): the permanent reconciliation of
`:DocPassage` with the graph's chunks, and of every stored text vector with the
current embedding key.

1. Build passages for every `:DocChunk` with none, or cut under another
   `split_key()` (a passage-size or rules change rolls out through here).
2. Embed passages with no vector or a stale `_embed_key` (model / prefix change),
   from the machine-local cache first (`embedding_cache`), else the model.
3. Unscoped or domain-scoped runs: re-embed `:UserChat` nodes with a stale key.

Resumable by construction, like the chunk sweep it replaces: every vector is
written as soon as it exists, and a run's own failures are excluded from its
later pages (`skip_ids`) so a dead embedding service cannot loop it.
"""
from __future__ import annotations

from loguru import logger

from artmind import passage_store, passages
from artmind.embedding_cache import EmbeddingCache, cached_embed
from artmind.extraction import embed_document, embed_key as _embed_key
from utils.functions import load_env


def _model() -> str:
    return load_env().get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")


_CHUNKS_NEEDING_PASSAGES = """
MATCH (c:DocChunk)
WHERE trim(coalesce(c.text, '')) <> ''
  AND (NOT EXISTS {{ MATCH (:DocPassage)-[:PART_OF]->(c) }}
       OR EXISTS {{ MATCH (p:DocPassage)-[:PART_OF]->(c) WHERE p._split_key <> $split_key }})
  AND NOT c.id IN $skip_ids{scope}
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
RETURN c.id AS id, c.doc_id AS doc_id, c._domain AS _domain, c.text AS chunk_text,
       c.header_path AS header_path, c.valid_from AS valid_from, c.valid_to AS valid_to,
       coalesce(d.title, d.name) AS title
LIMIT $batch_size
"""

_PASSAGES_NEEDING_VECTORS = """
MATCH (p:DocPassage)
WHERE (p.embedding IS NULL OR p._embed_key IS NULL OR p._embed_key <> $embed_key)
  AND NOT p.id IN $skip_ids{scope}
RETURN p.id AS id, p.text AS text, p.context AS context
LIMIT $batch_size
"""

_CHATS_NEEDING_VECTORS = """
MATCH (c:UserChat)
WHERE (c.embedding IS NULL OR c._embed_key IS NULL OR c._embed_key <> $embed_key)
  AND NOT c.id IN $skip_ids{scope}
RETURN c.id AS id, c.raw_text AS raw_text
LIMIT $batch_size
"""


def count_passage_gaps(tx) -> int:
    """Passages still missing a current vector (the CLI's heads-up, projection status)."""
    return tx.run(
        "MATCH (p:DocPassage) WHERE p.embedding IS NULL OR p._embed_key IS NULL "
        "OR p._embed_key <> $embed_key RETURN count(p) AS n",
        embed_key=_embed_key(_model()),
    ).single()["n"]


def _scope(var: str, id_field: str, chunk_ids, domain, doc_ids) -> tuple[str, dict]:
    clauses, params = [], {}
    if chunk_ids is not None:
        clauses.append(f"{var}.{id_field} IN $chunk_ids")
        params["chunk_ids"] = list(chunk_ids)
    if domain is not None:
        clauses.append(f"{var}._domain = $domain")
        params["domain"] = domain
    if doc_ids is not None:
        clauses.append(f"{var}.doc_id IN $doc_ids")
        params["doc_ids"] = list(doc_ids)
    return "".join(f"\n  AND {c}" for c in clauses), params


def reconcile_passages(
    session,
    *,
    chunk_ids: list | None = None,
    domain: str | None = None,
    doc_ids: list | None = None,
    embed=None,
    embed_key: str | None = None,
    cache: EmbeddingCache | None | bool = True,
    batch_size: int = 100,
    progress_every: int = 50,
    on_progress=None,
) -> dict:
    """See the module docstring. `chunk_ids=[]` / `doc_ids=[]` are scoped to
    nothing and return at once. `cache=True` opens the default cache; `None`
    disables it (tests). Returns {chunks_split, embedded, remaining, chats_embedded}."""
    result = {"chunks_split": 0, "embedded": 0, "remaining": 0, "chats_embedded": 0}
    if (chunk_ids is not None and not chunk_ids) or (doc_ids is not None and not doc_ids):
        return result
    model = _model()
    embed_key = embed_key or _embed_key(model)
    embed = embed or (lambda text: embed_document(model, text))
    if cache is True:
        cache = EmbeddingCache()

    # 1. passages for chunks that have none, or were cut under another split key
    scope, params = _scope("c", "id", chunk_ids, domain, doc_ids)
    split_key = passages.split_key()
    skipped: set[str] = set()
    while True:
        rows = session.run(_CHUNKS_NEEDING_PASSAGES.format(scope=scope), split_key=split_key,
                           skip_ids=list(skipped), batch_size=batch_size, **params).data()
        if not rows:
            break
        for row in rows:
            chunk = {"id": row["id"], "doc_id": row.get("doc_id"), "_domain": row.get("_domain"),
                     "text": row.get("chunk_text") or "", "header_path": row.get("header_path"),
                     "valid_from": row.get("valid_from"), "valid_to": row.get("valid_to")}
            built = passages.passage_rows(chunk, row.get("title"))
            if not built:          # nothing to split (cannot happen past the trim filter)
                skipped.add(row["id"])
                continue
            passage_store.write_chunk_passages(session, row["id"], built)
            result["chunks_split"] += 1
        if len(rows) < batch_size:
            break

    # 2. vectors for passages with none or a stale key
    scope, params = _scope("p", "chunk_id", chunk_ids, domain, doc_ids)
    failed: set[str] = set()
    while True:
        rows = session.run(_PASSAGES_NEEDING_VECTORS.format(scope=scope), embed_key=embed_key,
                           skip_ids=list(failed), batch_size=batch_size, **params).data()
        if not rows:
            break
        for row in rows:
            text = passages.embedding_text(row.get("text") or "", row.get("context"))
            try:
                vector = cached_embed(cache, embed_key, text, embed)
            except Exception as exc:
                failed.add(row["id"])
                logger.warning("Passage embedding failed for {!r}: {}", row["id"], exc)
                continue
            if not vector:
                failed.add(row["id"])
                continue
            session.run("MATCH (p:DocPassage {id: $id}) SET p.embedding = $embedding, p._embed_key = $embed_key",
                        id=row["id"], embedding=vector, embed_key=embed_key)
            result["embedded"] += 1
            if result["embedded"] % progress_every == 0:
                logger.info("Passage sweep: {} passage(s) embedded so far…", result["embedded"])
                if on_progress:
                    on_progress(result["embedded"], None)
        if len(rows) < batch_size:
            break
    result["remaining"] = len(failed)

    # 3. chats (their own vectors; no passages) on unscoped or domain-scoped runs only
    if chunk_ids is None and doc_ids is None:
        scope = "\n  AND c._domain = $domain" if domain is not None else ""
        extra = {"domain": domain} if domain is not None else {}
        chat_failed: set[str] = set()
        while True:
            rows = session.run(_CHATS_NEEDING_VECTORS.format(scope=scope), embed_key=embed_key,
                               skip_ids=list(chat_failed), batch_size=batch_size, **extra).data()
            if not rows:
                break
            for row in rows:
                try:
                    vector = cached_embed(cache, embed_key, row.get("raw_text") or "", embed)
                except Exception as exc:
                    chat_failed.add(row["id"])
                    logger.warning("Chat embedding failed for {!r}: {}", row["id"], exc)
                    continue
                session.run("MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding, c._embed_key = $embed_key",
                            id=row["id"], embedding=vector, embed_key=embed_key)
                result["chats_embedded"] += 1
            if len(rows) < batch_size:
                break

    if any(result.values()):
        logger.info("Passage sweep: {} chunk(s) split, {} passage(s) embedded, {} skipped, {} chat(s) embedded",
                    result["chunks_split"], result["embedded"], result["remaining"], result["chats_embedded"])
    return result
```

Note: `_CHUNKS_NEEDING_PASSAGES` is formatted with `.format`, hence the doubled `{{ }}` around the `EXISTS` bodies. The tests check the rendered Cypher.

- [ ] **Step 4: Rewire `artmind/ingest.py`.** Rename `_sweep_chunk_embeddings` to `_sweep_passages(chunk_ids=None, domain=None, doc_ids=None, *, strict=False) -> int` with the same body shape, calling `reconcile_passages(session, chunk_ids=chunk_ids, domain=domain, doc_ids=doc_ids)["embedded"]`; the no-op guard becomes `if not chunk_ids and not domain and not doc_ids: return 0`; log text says "Passage sweep". In `_sweep_domain` rename the output key `chunks_embedded` → `passages_embedded`. Line 4381: `_sweep_passages(summary.get("passage_chunk_ids") or [])`. Update the docstrings that say `ingest embed-chunks` to `ingest embed-passages`.

- [ ] **Step 5: Rewire the other callers.**
  - `artmind/table2graph.py:1387`: `report["commit"]["passages_embedded"] = ingest._sweep_passages(summary.get("passage_chunk_ids") or [])`.
  - `artmind/vault_sync.py:1646`: `ingest._sweep_passages(domain=d)`.
  - `artmind/projection.py`: replace `_UNEMBEDDED_CHUNKS_BY_DOMAIN` with
    ```python
    _UNEMBEDDED_PASSAGES_BY_DOMAIN = """
    MATCH (p:DocPassage) WHERE p.embedding IS NULL OR p._embed_key IS NULL OR p._embed_key <> $embed_key
    RETURN p._domain AS domain, count(p) AS n
    """
    ```
    pass `embed_key=extraction.embed_key(<model from load_env>)` where it is run, and rename the field `unembedded_chunks` → `unembedded_passages` in `_GAP_FIELDS`, the docstrings and every consumer (`grep -rn unembedded_chunks artmind test`).
  - `artmind/cli.py`: import `count_passage_gaps, reconcile_passages` instead (line 31); rename command `embed-chunks` → `embed-passages`:
    ```python
    @ingest.command("embed-passages")
    @click.option("--domain", default=None, help="Only this domain (exact name)")
    @click.option("--compact", is_flag=True, help="Emit compact JSON")
    def ingest_embed_passages(domain: str | None, compact: bool) -> None:
        """Build missing or outdated passages and embed every passage and chat whose vector is missing or stale.

        Committed KG staging carries no vectors (docs/vault.md, "Embeddings"); a fresh
        clone, a passage-size change or an embedding-model/prefix change all roll out
        through this sweep. Resumable: interrupt it and re-run.
        """
        _setup_logger()
        from artmind.graph_query import neo4j_session
        with neo4j_session() as session:
            result = reconcile_passages(session, domain=domain)
        _echo_json(result, compact)
    ```
    `_run_chunk_embed_sweep` → `_run_passage_sweep` using `count_passage_gaps(session)` and `reconcile_passages(session)`; message: `f"\nEmbedding {remaining:,} passage(s) — committed KG staging carries no vectors by design (docs/vault.md). Local model, no API cost.\n"` (also call it when `remaining == 0`, because chunks may still need passages: drop the early return and only print the heads-up when `remaining`). Update its callers. In `COMMAND_GROUPS` replace `"embed-chunks"` with `"embed-passages"`. At ~line 4627 rename `unembedded_chunks` in the help text.
  - Plugin: in `obsidian/artmind-obsidian/src/types.ts` rename `unembedded_chunks` → `unembedded_passages` (lines 190, 207) and `chunks_embedded` → `passages_embedded` (223); in `src/checklist.ts:171` use `p.unembedded_passages` and `countOf(p.unembedded_passages, "passage", "passages")`. Search `src/` and `test/` for any other use.

- [ ] **Step 6: Run** `uv run --group dev pytest test/ -q` then `just obsidian-plugin-test`. Fix renamed-field assertions. Plugin fixtures are regenerated in Task 20.
- [ ] **Step 7: Commit** — `git add -A artmind obsidian/artmind-obsidian/src test && git commit -m "feat(embeddings): passage sweep replaces the chunk embed sweep (ingest embed-passages)"`

### Task 14: Remove chunk embedding from extraction and the sidecar (model: sonnet)

**Files:**
- Modify: `artmind/ingest.py` (lines ~2283–2347 sidecar helpers, ~3306–3345 extract_kg embedding, ~3808 sidecar write, ~4276–4289 `_load_staged` merge)
- Modify: `artmind/vault_sync.py:1606`
- Modify tests: `test/test_ingest_staging_atomic.py`, `test/test_vault_sync.py`, `test/test_embed_sweep.py`, `test/test_vault_scaffold.py`, `test/test_chunks_only_extract.py`

- [ ] **Step 1:** In `extract_kg` (~line 3306–3345) delete `embedding_sidecar = read_embedding_sidecar(doc_kg_dir)`, `computed_embeddings`, and the whole "Embedding — compute once and persist" block (through `computed_embeddings[chunk_id] = data["embedding"]`). Also delete `embed_model` from `extract_kg`'s signature **only if** nothing else in the function uses it (`grep -n embed_model` inside the function first; the vocabulary step in `canonicalize` uses it — then keep it).
- [ ] **Step 2:** Delete the `write_embedding_sidecar(...)` call at ~3808 and the `sidecar_written` value (and any log/summary field that reported it; grep `sidecar_written`).
- [ ] **Step 3:** In `_load_staged` delete the sidecar merge block (~4277–4288) and the sentence of its docstring about the sidecar.
- [ ] **Step 4:** Delete `EMBEDDING_SIDECAR`, `write_embedding_sidecar`, `read_embedding_sidecar`, `carry_embedding_sidecar` (keep `strip_embeddings`: old chunk JSON caches may still hold a vector). In `artmind/vault_sync.py:1606` delete the `ingest.carry_embedding_sidecar(...)` call.
- [ ] **Step 5:** Keep the `.artmind/data/kg/**/embeddings.json` line in `artmind/vault.py`'s gitignore block (old sidecars stay on disk and must not be committed); change its comment to: `# Pre-2026-11 chunk-vector sidecars: no longer written, still never committed.`
- [ ] **Step 6:** Run `uv run --group dev pytest test/ -q`. Update the listed tests: delete tests that only exercised the removed helpers; where a test asserted that a vector reached the graph through the sidecar, assert instead that the commit wrote no chunk `embedding` and listed the chunk in `passage_chunk_ids`. Expected: all pass.
- [ ] **Step 7: Commit** — `git commit -am "refactor(ingest): chunks are no longer embedded; drop the chunk vector sidecar"`

### Task 15: Entity and chat vectors use the document prefix and `_embed_key` (model: sonnet)

**Files:**
- Modify: `artmind/ingest.py` (`embed_missing_entity_embeddings` ~line 2402–2470)
- Modify: `artmind/synthesize.py:204` and its write of `embedding`
- Modify: `artmind/update.py:337` (+ the query that stores the chat's embedding), `:505-528` (backfill)
- Modify: `artmind/canonicalize.py:84`
- Modify: `artmind/projection.py` (the unembedded-entities count, `grep -n "_UNEMBEDDED_ENTITIES\|unembedded_entities" artmind/projection.py`)
- Test: `test/test_entity_embed_key.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_entity_embed_key.py
from _neo4j_recorder import Recorder

from artmind import extraction, ingest


def test_entity_sweep_selects_stale_keys_and_writes_the_key(monkeypatch):
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: [0.1])
    s = Recorder(lambda c, p: [{"id": "e1", "name": "Ada", "description": "a person"}] if "RETURN e._id AS id" in c else [])
    ingest.embed_missing_entity_embeddings(s, "people", "nomic-embed-text:latest")
    (select, sp), = s.queries_containing("RETURN e._id AS id")
    assert "e._embed_key IS NULL OR e._embed_key <> $embed_key" in select
    assert sp["embed_key"] == extraction.embed_key("nomic-embed-text:latest")
    (write, wp), = s.queries_containing("SET e.embedding = $embedding")
    assert "e._embed_key = $embed_key" in write and wp["embed_key"] == sp["embed_key"]


def test_entity_text_gets_the_document_prefix(monkeypatch):
    sent = []
    monkeypatch.setattr(extraction, "embed_text", lambda model, text: sent.append(text) or [0.1])
    s = Recorder(lambda c, p: [{"id": "e1", "name": "Ada", "description": None}] if "RETURN e._id AS id" in c else [])
    ingest.embed_missing_entity_embeddings(s, "people", "nomic-embed-text:latest")
    assert sent == ["search_document: Ada"]
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Edit `embed_missing_entity_embeddings`:**
  - selection `AND (e.embedding IS NULL OR e.embedding_stale)` → `AND (e.embedding IS NULL OR e.embedding_stale OR e._embed_key IS NULL OR e._embed_key <> $embed_key)`; add `params["embed_key"] = extraction.embed_key(embed_model)` (import `from artmind import extraction` or use the module already imported);
  - `_embed_text(embed_model, entity_embedding_text(...))` → `extraction.embed_document(embed_model, entity_embedding_text(...))`;
  - write: `"MATCH (e:Entity {_id: $id}) SET e.embedding = $embedding, e.embedding_stale = false, e._embed_key = $embed_key"` with `embed_key=params["embed_key"]`.
  - Update the docstring's "Matches on ..." sentence.
- [ ] **Step 4:** `artmind/synthesize.py:204`: `embedding = embed_document(embed_model, text)` (import `embed_document`, `embed_key` from `artmind.extraction`); where it writes `e.embedding` (grep in the file), also set `_embed_key` to `embed_key(embed_model)` in the same SET.
- [ ] **Step 5:** `artmind/update.py:337`: `embedding = embed_document(embed_model, raw_text)`; in the Cypher that creates the `:UserChat` with `embedding` (grep `embedding` in `update.py`), set `_embed_key` too. In the backfill at ~505–528: select `c.embedding IS NULL OR c._embed_key IS NULL OR c._embed_key <> $embed_key`, embed with `embed_document`, write `_embed_key`. Import from `artmind.extraction`.
- [ ] **Step 6:** `artmind/canonicalize.py:84`: the seed text is a query against entity vectors → `query_vector = embed_query(embed_model, seed_text[:4000])` (import `embed_query`).
- [ ] **Step 7:** `artmind/projection.py`: the unembedded-entities count must match the sweep's selection: add `OR e._embed_key IS NULL OR e._embed_key <> $embed_key` and pass `embed_key`.
- [ ] **Step 8:** Run `uv run --group dev pytest test/ -q` — fix tests that asserted the old entity Cypher or raw entity text (the text is now prefixed for nomic). Expected: PASS.
- [ ] **Step 9: Commit** — `git add -A artmind test && git commit -m "feat(embeddings): entity and chat vectors carry the document prefix and an embed key"`

Slice 3 ends: `just dev-test` — all pass.

---

# Slice 4 — Query path

### Task 16: Fusion and diversity helpers (model: sonnet)

**Files:**
- Create: `artmind/retrieval_rank.py`
- Test: `test/test_retrieval_rank.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_retrieval_rank.py
import pytest

from artmind import retrieval_rank as R


def p(pid, chunk, doc, cosine=None, vec=None):
    return {"kind": "passage", "id": pid, "chunk_id": chunk, "doc_id": doc,
            **({"cosine": cosine} if cosine is not None else {}),
            **({"embedding": vec} if vec is not None else {})}


def test_rrf_then_maxp_groups_passages_into_chunks():
    vec = [p("c1_p01", "c1", "d1", 0.9, [1, 0]), p("c1_p02", "c1", "d1", 0.7, [1, 0]), p("c2_p01", "c2", "d2", 0.6, [0, 1])]
    text = [p("c1_p02", "c1", "d1"), p("c2_p01", "c2", "d2")]
    units = R.passage_fuse(vec, text)
    assert [u.key for u in units] == ["chunk:c1", "chunk:c2"]
    c1, c2 = units
    assert c1.passages_matched == 2 and c1.matched_by == ["vector", "fulltext"]
    assert c1.relevance == 0.9                      # best cosine among its passages
    assert c1.best["id"] == "c1_p02"                # best fused passage: vector rank 2 + text rank 1
    assert c1.score == pytest.approx(1 / 62 + 1 / 61)
    assert c2.score == pytest.approx(1 / 63 + 1 / 62)
    assert c2.matched_by == ["vector", "fulltext"] and c2.relevance == 0.6


def test_fulltext_only_unit_has_no_relevance():
    units = R.passage_fuse([], [p("c3_p01", "c3", "d3")])
    assert units[0].relevance is None and units[0].matched_by == ["fulltext"] and units[0].vector is None


def test_chats_are_their_own_units():
    chat = {"kind": "chat", "id": "u1", "cosine": 0.8, "embedding": [1, 0], "chat": {"id": "u1"}}
    units = R.passage_fuse([chat], [])
    assert units[0].key == "chat:u1" and units[0].kind == "chat"


def _unit(key, score, doc, vec):
    return R.Unit(key=key, kind="chunk", score=score, relevance=None, matched_by=["vector"],
                  best={}, doc_id=doc, vector=vec, passages_matched=1)


def test_mmr_zero_diversity_is_relevance_order():
    units = [_unit("a", 0.3, "d1", [1, 0]), _unit("b", 0.2, "d1", [1, 0]), _unit("c", 0.1, "d2", [0, 1])]
    assert [u.key for u in R.mmr_select(units, 2, 0.0)] == ["a", "b"]


def test_mmr_demotes_a_second_chunk_of_the_same_document():
    units = [_unit("a", 0.30, "d1", [1, 0]), _unit("b", 0.29, "d1", [0.9, 0.1]), _unit("c", 0.25, "d2", [0, 1])]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "c"]


def test_mmr_same_document_floor_applies_without_vectors():
    units = [_unit("a", 0.30, "d1", None), _unit("b", 0.29, "d1", None), _unit("c", 0.25, "d2", None)]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "c"]


def test_mmr_ties_break_on_score_then_key():
    units = [_unit("b", 0.2, "d1", None), _unit("a", 0.2, "d2", None)]
    assert [u.key for u in R.mmr_select(units, 2, 0.3)] == ["a", "b"]
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

```python
# artmind/retrieval_rank.py
"""Ranking for vector-text (spec 2026-10-31 §4.2 steps 3-5). Pure Python.

`passage_fuse`: Reciprocal Rank Fusion per passage (and per chat) over the vector
and fulltext rankings, then MaxP -- a chunk scores its best passage's fused score.
`mmr_select`: maximal marginal relevance over the fused units, with a
same-document redundancy floor so one document cannot fill the top K.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RRF_K = 60
SAME_DOC_SIMILARITY = 0.85
_LEG_ORDER = ("vector", "fulltext")


@dataclass
class Unit:
    key: str                     # "chunk:<id>" | "chat:<id>"
    kind: str                    # "chunk" | "chat"
    score: float                 # fused RRF score (MaxP over passages for a chunk)
    relevance: float | None      # best cosine among its passages; None if only fulltext found it
    matched_by: list[str]
    best: dict                   # best passage row (chunk) or the chat row
    doc_id: str | None
    vector: list[float] | None   # vector of the best passage when the vector leg returned one
    passages_matched: int = 1


def _legs(legs) -> list[str]:
    return [leg for leg in _LEG_ORDER if leg in legs]


def passage_fuse(vector_rows: list[dict], text_rows: list[dict], k: int = RRF_K) -> list[Unit]:
    per: dict[tuple[str, str], dict] = {}
    for leg, rows in (("vector", vector_rows), ("fulltext", text_rows)):
        for rank, row in enumerate(rows, start=1):
            e = per.setdefault((row["kind"], row["id"]),
                               {"row": row, "score": 0.0, "legs": set(), "cosine": None, "vector": None})
            e["score"] += 1.0 / (k + rank)
            e["legs"].add(leg)
            if row.get("cosine") is not None:
                e["cosine"] = row["cosine"]
            if row.get("embedding"):
                e["vector"] = row["embedding"]
    units: dict[str, Unit] = {}
    for (kind, rid), e in per.items():
        if kind == "chat":
            key = f"chat:{rid}"
            units[key] = Unit(key, "chat", e["score"], e["cosine"], _legs(e["legs"]), e["row"], None, e["vector"])
            continue
        key = f"chunk:{e['row']['chunk_id']}"
        u = units.get(key)
        if u is None:
            units[key] = Unit(key, "chunk", e["score"], e["cosine"], _legs(e["legs"]), e["row"],
                              e["row"].get("doc_id"), e["vector"])
            continue
        u.passages_matched += 1
        u.matched_by = _legs(set(u.matched_by) | e["legs"])
        if e["cosine"] is not None and (u.relevance is None or e["cosine"] > u.relevance):
            u.relevance = e["cosine"]
        if e["score"] > u.score:
            u.score, u.best = e["score"], e["row"]
            u.vector = e["vector"] or u.vector
    return sorted(units.values(), key=lambda u: (-u.score, u.key))


def _cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _similarity(a: Unit, b: Unit) -> float:
    sim = _cosine(a.vector, b.vector)
    if a.doc_id and a.doc_id == b.doc_id:
        sim = max(sim, SAME_DOC_SIMILARITY)
    return sim


def mmr_select(units: list[Unit], top_k: int, diversity: float) -> list[Unit]:
    """Pick `top_k` from the best `3 * top_k`: at each step the unit maximising
    (1 - d) * rel - d * redundancy. d = 0 is plain relevance order."""
    ordered = sorted(units, key=lambda u: (-u.score, u.key))
    if diversity <= 0 or len(ordered) <= 1:
        return ordered[:top_k]
    pool = ordered[: 3 * top_k]
    top = pool[0].score or 1.0
    picked: list[Unit] = []
    rest = list(pool)
    while rest and len(picked) < top_k:
        def value(u: Unit) -> float:
            redundancy = max((_similarity(u, p) for p in picked), default=0.0)
            return (1 - diversity) * (u.score / top) - diversity * redundancy
        best = min(rest, key=lambda u: (-value(u), -u.score, u.key))
        picked.append(best)
        rest.remove(best)
    return picked
```

Check `test_mmr_ties_break_on_score_then_key`: both score 0.2, no shared doc → equal value and score, key `a` < `b` → `a` first. Check `test_mmr_demotes...`: after `a`, `b` value = 0.7·(0.29/0.30) − 0.3·0.85 ≈ 0.4217; `c` value = 0.7·(0.25/0.30) − 0.3·0 ≈ 0.5833 → `c`.

- [ ] **Step 4: Run the tests** — Expected: PASS.
- [ ] **Step 5: Commit** — `git add artmind/retrieval_rank.py test/test_retrieval_rank.py && git commit -m "feat(retrieval): RRF+MaxP passage fusion and MMR diversity selection"`

### Task 17: Passage-based `vector_text_search` and CLI options (model: sonnet)

**Files:**
- Modify: `artmind/vector_query.py` (replace `vector_search`, `full_text_search`, `vector_text_search`, lines 130–292 and 475–503; keep `_rrf_combine`, `_put_exact_first`, `_get_result_id` — `entity_resolve` uses them)
- Modify: `artmind/cli.py:3159-3172` (`vector-text`)
- Test: `test/test_vector_query.py` (replace the `vector_search` / `full_text_search` / `vector_text_search` tests; keep the `entity_resolve` / `entity_name_candidates` ones)

- [ ] **Step 1: Write the failing tests** (append to `test/test_vector_query.py`, after deleting the tests of the three replaced functions)

```python
from _neo4j_recorder import Recorder

from artmind import graph_query

PASSAGES_VEC = [
    {"row": {"kind": "passage", "id": "c1_p01", "chunk_id": "c1", "doc_id": "d1", "char_start": 0,
             "char_end": 9, "text": "first one", "cosine": 0.82, "embedding": [1.0, 0.0]}},
    {"row": {"kind": "passage", "id": "c2_p01", "chunk_id": "c2", "doc_id": "d2", "char_start": 5,
             "char_end": 20, "text": "second", "cosine": 0.75, "embedding": [0.0, 1.0]}},
]
PASSAGES_FT = [{"row": {"kind": "passage", "id": "c2_p01", "chunk_id": "c2", "doc_id": "d2",
                        "char_start": 5, "char_end": 20, "text": "second"}}]
CHUNKS = [
    {"chunk": {"id": "c1", "name": "C1", "doc_id": "d1", "header_path": "A", "text": "first one ..."},
     "document": {"id": "d1", "name": "Doc 1"}},
    {"chunk": {"id": "c2", "name": "C2", "doc_id": "d2", "header_path": "B", "text": "xxxxx second ..."},
     "document": {"id": "d2", "name": "Doc 2"}},
]


def respond(cypher, params):
    if "VECTOR INDEX passage_embedding" in cypher:
        return PASSAGES_VEC
    if "'passage_text_ft'" in cypher:
        return PASSAGES_FT
    if "UNWIND $chunkIds AS cid" in cypher:
        return [c for c in CHUNKS if c["chunk"]["id"] in params["chunkIds"]]
    return []


def _patch(monkeypatch, session, embed=lambda q: [1.0, 0.0]):
    monkeypatch.setattr(vector_query, "embed_question", embed)
    monkeypatch.setattr(vector_query, "read_session", lambda: session)
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d, f"{d}.child"])


def test_vector_text_filters_inside_search_with_a_deep_pool(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    vector_query.vector_text_search(["papers"], "Which method?", 5)
    (vec, vp), = s.queries_containing("VECTOR INDEX passage_embedding")
    assert "WHERE p._domain IN $domains" in vec.split("LIMIT $pool")[0]   # inside SEARCH
    assert vp["domains"] == ["papers", "papers.child"] and vp["pool"] == 50
    (ft, fp), = s.queries_containing("'passage_text_ft'")
    assert "p._domain IN $domains" in ft and fp["pool"] == 50


def test_vector_text_returns_parent_chunks_with_spans_and_fused_scores(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    out = vector_query.vector_text_search(["papers"], "q", 2, diversity=0.0)
    assert out["parameters"] == {"topK": 2, "pool": 50, "diversity": 0.0, "context": "chunk"}
    assert out["warnings"] == []
    first, second = out["rows"]
    assert first["chunk"]["id"] == "c2"                     # found by both legs
    assert first["matched_by"] == ["vector", "fulltext"] and first["relevance"] == 0.75
    assert first["passage"] == {"id": "c2_p01", "char_start": 5, "char_end": 20}
    assert first["chunk"]["text"] == "xxxxx second ..." and first["document"]["id"] == "d2"
    assert first["source_type"] == "document" and first["passages_matched"] == 1
    assert second["chunk"]["id"] == "c1" and second["matched_by"] == ["vector"]
    assert 0 < second["score"] < first["score"]


def test_context_passage_returns_the_passage_text(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    out = vector_query.vector_text_search(["papers"], "q", 1, diversity=0.0, context="passage")
    assert out["rows"][0]["chunk"]["text"] == "second"


def test_embedding_down_falls_back_to_keywords_with_a_warning(monkeypatch):
    s = Recorder(respond)

    def down(q):
        raise RuntimeError("ollama unreachable")
    _patch(monkeypatch, s, embed=down)
    out = vector_query.vector_text_search(["papers"], "q", 2)
    assert out["warnings"] and out["warnings"][0].startswith("vector leg unavailable")
    assert [r["chunk"]["id"] for r in out["rows"]] == ["c2"] and out["rows"][0]["relevance"] is None
    assert not s.queries_containing("VECTOR INDEX passage_embedding")


def test_missing_passage_index_names_the_fix(monkeypatch):
    from neo4j.exceptions import ClientError

    def respond_missing(cypher, params):
        if "passage_embedding" in cypher:
            raise ClientError("There is no such vector schema index: passage_embedding")
        return []
    _patch(monkeypatch, Recorder(respond_missing))
    with pytest.raises(ValueError, match="embed-passages"):
        vector_query.vector_text_search(["papers"], "q", 2)


@pytest.mark.parametrize("kwargs", [{"topK": 0}, {"topK": 51}, {"diversity": 1.5}, {"context": "page"}])
def test_bad_arguments_raise(monkeypatch, kwargs):
    _patch(monkeypatch, Recorder(respond))
    args = {"topK": 5, **kwargs}
    top_k = args.pop("topK")
    with pytest.raises(ValueError):
        vector_query.vector_text_search(["papers"], "q", top_k, **args)
```

(`pytest` and `vector_query` are already imported at the top of the file.) Construct `ClientError` the way existing tests do (`grep -rn "ClientError(" test/ | head -3`): the neo4j driver's constructor signature varies by version, and some tests build it with a message argument, others set `.message` after `ClientError()`.

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement in `artmind/vector_query.py`.** Delete `vector_search`, `full_text_search` and `vector_text_search`; add:

```python
from artmind import retrieval_rank

POOL_MIN = 50
MAX_TOP_K = 50
DEFAULT_DIVERSITY = 0.3
CONTEXTS = ("chunk", "passage")
MISSING_INDEX = (
    "the passage index is missing: this graph has not been migrated to passages. Run "
    "`artmind ingest embed-passages` (or `artmind vault sync`) inside the vault, then `artmind setup`."
)

_PASSAGE_ROW = ("kind: 'passage', id: p.id, chunk_id: p.chunk_id, doc_id: p.doc_id, "
                "char_start: p.char_start, char_end: p.char_end, text: p.text")
_CHAT_ROW = "kind: 'chat', id: c.id, chat: c { .id, .raw_text, ._domain, .created_by, .created_at }"


def _pool(top_k: int) -> int:
    return max(POOL_MIN, 10 * top_k)


def _expand(domains: list[str]) -> list[str]:
    from artmind.graph_query import expand_domain_family

    out: list[str] = []
    for d in domains:
        for member in expand_domain_family(d):
            if member not in out:
                out.append(member)
    return out


def _passage_vector_cypher(as_of) -> str:
    from artmind.graph_query import asof_predicate

    asof = f"\n    WHERE {asof_predicate('p')}" if as_of else ""
    return f"""
    CYPHER 25
    MATCH (p:DocPassage)
      SEARCH p IN (
        VECTOR INDEX passage_embedding
        FOR $embedding
        WHERE p._domain IN $domains
        LIMIT $pool
      )
    WITH p, vector.similarity.cosine(p.embedding, $embedding) AS score{asof}
    RETURN p {{ {_PASSAGE_ROW}, cosine: score, embedding: p.embedding }} AS row
    ORDER BY score DESC
    """


def _passage_text_cypher(as_of) -> str:
    from artmind.graph_query import asof_predicate

    asof = f" AND {asof_predicate('p')}" if as_of else ""
    return f"""
    CALL db.index.fulltext.queryNodes('passage_text_ft', $ft_query)
    YIELD node AS p, score
    WHERE p._domain IN $domains{asof}
    RETURN p {{ {_PASSAGE_ROW} }} AS row
    ORDER BY score DESC
    LIMIT $pool
    """


def _chat_vector_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    CYPHER 25
    MATCH (c:UserChat)
      SEARCH c IN (VECTOR INDEX user_chat_embedding FOR $embedding LIMIT $pool)
    WITH c, vector.similarity.cosine(c.embedding, $embedding) AS score
    WHERE {domain_predicate("c")}
    RETURN c {{ {_CHAT_ROW}, cosine: score, embedding: c.embedding }} AS row
    ORDER BY score DESC
    """


def _chat_text_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    CALL db.index.fulltext.queryNodes('user_chat_text_ft', $ft_query)
    YIELD node AS c, score
    WHERE {domain_predicate("c")}
    RETURN c {{ {_CHAT_ROW} }} AS row
    ORDER BY score DESC
    LIMIT $pool
    """


def _chunk_fetch_cypher() -> str:
    return f"""
    UNWIND $chunkIds AS cid
    MATCH (c:DocChunk {{id: cid}})
    OPTIONAL MATCH (c)-[:PART_OF]->(document:Document)
    RETURN c {{ .id, .name, .doc_id, .header_path, .text }} AS chunk,
           document {{ .id, .name, .path, .source_path, ._domain, {supersedes_field("document")} }} AS document
    """


def _rows(session, cypher: str, params: dict) -> list[dict]:
    return [strip_internal_props(serialize_record(r))["row"] for r in session.run(cypher, **params)]


def _optional_rows(session, cypher: str, params: dict) -> list[dict]:
    """A chat leg: its index may be absent on an old graph (no chats ever)."""
    try:
        return _rows(session, cypher, params)
    except ClientError as e:
        if "index" in str(e).lower():
            return []
        raise


def _is_missing_index(e: ClientError) -> bool:
    text = str(e).lower()
    return "passage_embedding" in text or "passage_text_ft" in text or ("no such" in text and "index" in text)


def vector_text_search(
    domains,
    question: str,
    topK: int = 5,
    as_of: str | None = None,
    *,
    diversity: float = DEFAULT_DIVERSITY,
    context: str = "chunk",
) -> dict:
    """Hybrid search over passages (spec 2026-10-31 §4.2): vector (domain-filtered
    inside SEARCH) + BM25, each over a deep pool; RRF per passage; MaxP into parent
    chunks; MMR diversity; the top K parent chunks (or passages) with the matching span."""
    from artmind.graph_query import _domain_output, normalize_domains

    top_k = int(topK)
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"--topK must be between 1 and {MAX_TOP_K}")
    if not 0.0 <= float(diversity) <= 1.0:
        raise ValueError("--diversity must be between 0 and 1")
    if context not in CONTEXTS:
        raise ValueError(f"--context must be one of {', '.join(CONTEXTS)}")
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    pool = _pool(top_k)
    warnings: list[str] = []
    ft_query = sanitize_lucene_query(question)

    embedding = None
    try:
        embedding = embed_question(question)
    except Exception as e:
        warnings.append(f"vector leg unavailable: {e}")

    params = {"domains": _expand(domains), "pool": pool, **({"asOf": as_of} if as_of else {})}
    chat_params = {"domains": domains, "pool": pool}
    vector_rows: list[dict] = []
    text_rows: list[dict] = []
    with read_session() as session:
        try:
            if embedding is not None:
                vector_rows = _rows(session, _passage_vector_cypher(as_of), {**params, "embedding": embedding})
                vector_rows += _optional_rows(session, _chat_vector_cypher(), {**chat_params, "embedding": embedding})
            if ft_query:
                text_rows = _rows(session, _passage_text_cypher(as_of), {**params, "ft_query": ft_query})
                text_rows += _optional_rows(session, _chat_text_cypher(), {**chat_params, "ft_query": ft_query})
        except ClientError as e:
            if _is_missing_index(e):
                raise ValueError(MISSING_INDEX) from e
            raise
        # A chat's rows were appended after the passage rows; rank each leg by its own score.
        vector_rows.sort(key=lambda r: -(r.get("cosine") or 0.0))
        units = retrieval_rank.passage_fuse(vector_rows, text_rows)
        picked = retrieval_rank.mmr_select(units, top_k, float(diversity))
        chunk_ids = [u.best["chunk_id"] for u in picked if u.kind == "chunk"]
        fetched = {}
        if chunk_ids:
            for r in session.run(_chunk_fetch_cypher(), chunkIds=chunk_ids):
                rec = strip_internal_props(serialize_record(r))
                fetched[rec["chunk"]["id"]] = rec

    rows = []
    for u in picked:
        common = {"score": round(u.score, 6), "relevance": None if u.relevance is None else round(u.relevance, 4),
                  "matched_by": u.matched_by}
        if u.kind == "chat":
            rows.append({"chat": u.best["chat"], "source_type": "user_chat", **common})
            continue
        rec = fetched.get(u.best["chunk_id"])
        if rec is None:            # chunk vanished between the two reads
            continue
        chunk = dict(rec["chunk"])
        if context == "passage":
            chunk["text"] = u.best.get("text")
        rows.append({
            "chunk": chunk, "document": rec.get("document"), "source_type": "document", **common,
            "passage": {"id": u.best["id"], "char_start": u.best.get("char_start"), "char_end": u.best.get("char_end")},
            "passages_matched": u.passages_matched,
        })

    return {
        **_domain_output(domains),
        "query_type": "vector_text",
        "question": question,
        "parameters": {"topK": top_k, "pool": pool, "diversity": float(diversity), "context": context,
                       **({"asOf": as_of} if as_of else {})},
        "warnings": warnings,
        "rows": rows,
    }
```

In the test `respond` the recorder returns dicts shaped `{"row": {...}}`, matching what `_rows` reads. Check `serialize_record` accepts a plain dict (look at `graph_query.serialize_record`); if it requires a `neo4j.Record`, make `_rows` tolerate both: `rec = r if isinstance(r, dict) else serialize_record(r)`, and do the same in the chunk fetch.

- [ ] **Step 4: Update the CLI** (`artmind/cli.py:3159`)

```python
@query.command("vector-text")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
@click.option("--topK", "top_k", type=int, default=5, show_default=True, help="Results to return (1-50)")
@click.option("--diversity", type=float, default=0.3, show_default=True,
              help="0-1: how strongly to prefer chunks unlike those already picked (0 = pure relevance order)")
@click.option("--context", type=click.Choice(["chunk", "passage"]), default="chunk", show_default=True,
              help="Return each hit's parent chunk text, or only the matching passage")
@click.option("--asOf", "as_of", default=None, help="Only rows in force by this date (valid_from <= asOf; valid_to is rarely set, so this is a floor, not a point-in-time snapshot). ISO date or 'today'; nodes without valid-time always shown")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
@click.argument("question")
def vector_text(domain: tuple, top_k: int, diversity: float, context: str, as_of: str | None,
                compact: bool, question: str) -> None:
    """Hybrid search over passages: vector + keyword (RRF), grouped into parent chunks, diversified (MMR)."""
    domains = _parse_domains(domain)
    try:
        result = vector_query.vector_text_search(domains, question, top_k, as_of=as_of,
                                                 diversity=diversity, context=context)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

- [ ] **Step 5: Check the other readers of the payload.** `grep -rn "vector_text\|vector-text" artmind/traces artmind/query_plan artmind/webui` — `traces/path.py` reads evidence from `rows[].chunk.id` (unchanged). Make sure nothing reads `rows[].score` as a cosine. Run `uv run --group dev pytest test/ -q -k "trace or plan or vector"`.

- [ ] **Step 6: Run** `uv run --group dev pytest test/test_vector_query.py -q` — Expected: PASS.
- [ ] **Step 7: Commit** — `git commit -am "feat(query): vector-text searches passages with in-index domain filter, MaxP and MMR"`

### Task 18: `entity-context` ranks chunks by their passages (model: sonnet)

**Files:**
- Modify: `artmind/graph_query.py:1302-1317` (`chunk_refs`, `chunk_keyword` in `_entity_context_queries`)
- Test: `test/test_entity_context_passages.py`

- [ ] **Step 1: Write the failing test**

```python
# test/test_entity_context_passages.py
from artmind import graph_query


def test_chunk_refs_score_is_maxp_over_passages():
    q = graph_query._entity_context_queries()["chunk_refs"]
    assert "OPTIONAL MATCH (p:DocPassage)-[:PART_OF]->(c)" in q
    assert "max(CASE WHEN $queryVector IS NULL OR p.embedding IS NULL THEN null" in q
    assert "c.embedding" not in q


def test_chunk_keyword_uses_passages_best_per_chunk():
    q = graph_query._entity_context_queries()["chunk_keyword"]
    assert "'passage_text_ft'" in q and "node.chunk_id IN $chunkIds" in q
    assert "WITH node.chunk_id AS id, max(score) AS score" in q
```

- [ ] **Step 2: Run to see it fail.**
- [ ] **Step 3: Edit the two queries**

```python
        "chunk_refs": f"""
    {anchor}
    MATCH (e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)
    WITH DISTINCT c
    OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
    OPTIONAL MATCH (p:DocPassage)-[:PART_OF]->(c)
    WITH c, d, max({_query_score("p")}) AS score
    RETURN c.id AS id, c.name AS name, c.doc_id AS doc_id,
           c.valid_to IS NULL AS current, d.valid_from AS doc_valid_from,
           score
    """,
        "chunk_keyword": """
    CALL db.index.fulltext.queryNodes('passage_text_ft', $ftQuery)
    YIELD node, score
    WHERE node.chunk_id IN $chunkIds
    WITH node.chunk_id AS id, max(score) AS score
    RETURN id, score
    ORDER BY score DESC
    """,
```
Update the `_entity_context_queries` docstring: "`chunk_keyword` is the BM25 leg of `--query` ranking over the entity's chunks' passages (best passage per chunk); `chunk_refs` scores a chunk by its best passage's similarity (MaxP)." Update `_rank_chunk_refs`'s docstring "embedding similarity" → "best-passage similarity".
- [ ] **Step 4: Run** `uv run --group dev pytest test/ -q -k "entity_context or entity-context"` — Expected: PASS (existing tests mock rows; fix any that matched the old query text).
- [ ] **Step 5: Commit** — `git add -A artmind test && git commit -m "feat(query): entity-context ranks source chunks by their passages (MaxP)"`

Slice 4 ends: `just dev-test` — all pass.

---

# Slice 5 — Legacy cleanup

### Task 19: One-file migration run by `artmind setup` (model: sonnet)

**Files:**
- Create: `artmind/migrations/__init__.py` (empty), `artmind/migrations/chunk_vectors_to_passages.py`
- Modify: `artmind/setup.py` (`setup_all`, inside its `with neo4j_session()` block, ~line 1027)
- Modify: `artmind/cli.py:5689-5701` (`setup` prints the report line)
- Test: `test/test_migration_chunk_vectors.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_migration_chunk_vectors.py
from _neo4j_recorder import Recorder

from artmind.migrations import chunk_vectors_to_passages as m


def session(indexes=(), lacking=(), vectors=(0,)):
    left = list(vectors)

    def respond(cypher, params):
        if "SHOW INDEXES" in cypher:
            return [{"name": n} for n in indexes]
        if "NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }" in cypher:
            return [{"domain": d, "n": n} for d, n in lacking]
        if "c.embedding IS NOT NULL" in cypher and "REMOVE c.embedding" in cypher:
            return [{"n": left.pop(0) if left else 0}]
        if "c.embedding IS NOT NULL" in cypher:
            return [{"n": sum(left)}]
        return []
    return Recorder(respond)


def test_nothing_left_reports_none():
    s = session()
    out = m.run(s)
    assert out["status"] == "none" and out["report"] == "legacy chunk vectors: none"
    assert not s.queries_containing("DROP INDEX")


def test_waits_while_chunks_lack_passages():
    s = session(indexes=("chunk_embedding",), lacking=[("papers", 3)])
    out = m.run(s)
    assert out["status"] == "waiting" and out["domains_without_passages"] == {"papers": 3}
    assert "artmind ingest embed-passages" in out["report"]
    assert not s.queries_containing("DROP INDEX") and not s.queries_containing("REMOVE c.embedding")


def test_cleans_up_when_every_chunk_has_passages():
    s = session(indexes=("chunk_embedding", "chunk_text_ft"), vectors=(1000, 7))
    out = m.run(s)
    drops = [c for c, _ in s.queries_containing("DROP INDEX")]
    assert drops == ["DROP INDEX chunk_embedding IF EXISTS", "DROP INDEX chunk_text_ft IF EXISTS"]
    (lack, _), = s.queries_containing("NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }")
    assert "trim(coalesce(c.text, '')) <> ''" in lack
    removes = s.queries_containing("REMOVE c.embedding")
    assert "(c:DocChunk OR c:DocChunkHistory)" in removes[0][0]
    assert out == {"status": "cleaned", "dropped_indexes": ["chunk_embedding", "chunk_text_ft"],
                   "vectors_removed": 1007, "report": "legacy chunk vectors: none"}
```

- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement**

```python
# artmind/migrations/chunk_vectors_to_passages.py
"""One-off cleanup: chunk vectors -> passages (spec 2026-10-31 §3.6).

REMOVE THIS MODULE, its call in `setup.setup_all` and
`test/test_migration_chunk_vectors.py` once every graph in use prints
`legacy chunk vectors: none` from `artmind setup` (tracked in the GitHub issue
filed when this landed). A fresh graph never needs it: setup no longer creates
the legacy indexes.

Drops `chunk_embedding` and `chunk_text_ft` and removes `embedding` from
`:DocChunk`/`:DocChunkHistory` -- but only once every non-blank `:DocChunk` has
passages, so vector-text never loses its index before passages exist.
"""
from __future__ import annotations

LEGACY_INDEXES = ("chunk_embedding", "chunk_text_ft")
BATCH = 1000
NONE = "legacy chunk vectors: none"


def run(session) -> dict:
    present = [r["name"] for r in session.run(
        "SHOW INDEXES YIELD name WHERE name IN $names RETURN name", names=list(LEGACY_INDEXES)).data()]
    present = [n for n in LEGACY_INDEXES if n in present]
    leftover = session.run(
        "MATCH (c) WHERE (c:DocChunk OR c:DocChunkHistory) AND c.embedding IS NOT NULL RETURN count(c) AS n"
    ).single()["n"]
    if not present and not leftover:
        return {"status": "none", "report": NONE}

    lacking = session.run(
        """
        MATCH (c:DocChunk)
        WHERE trim(coalesce(c.text, '')) <> ''
          AND NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }
        RETURN c._domain AS domain, count(c) AS n
        ORDER BY domain
        """
    ).data()
    if lacking:
        by_domain = {r["domain"]: r["n"] for r in lacking}
        total = sum(by_domain.values())
        return {
            "status": "waiting",
            "domains_without_passages": by_domain,
            "report": (f"legacy chunk vectors: kept — {total} chunk(s) in {', '.join(map(str, by_domain))} "
                       "have no passages yet; run `artmind ingest embed-passages` (or `artmind vault sync`) "
                       "inside the vault, then `artmind setup` again"),
        }

    for name in present:
        session.run(f"DROP INDEX {name} IF EXISTS")
    removed = 0
    while True:
        n = session.run(
            f"""
            MATCH (c) WHERE (c:DocChunk OR c:DocChunkHistory) AND c.embedding IS NOT NULL
            WITH c LIMIT {BATCH}
            REMOVE c.embedding
            RETURN count(c) AS n
            """
        ).single()["n"]
        removed += n
        if n < BATCH:
            break
    return {"status": "cleaned", "dropped_indexes": present, "vectors_removed": removed, "report": NONE}
```

Check the third test against this: the respond stub returns `sum(left)` for the leftover count (1007) and pops 1000 then 7 for the batched removes → `removed == 1007`, the loop ends because 7 < 1000.

- [ ] **Step 4: Call it from `setup_all`.** Inside the existing `with neo4j_session() as session:` block, after `_setup_neo4j(...)`:
```python
        # Temporary (spec 2026-10-31 §3.6): delete with artmind/migrations/chunk_vectors_to_passages.py.
        from artmind.migrations import chunk_vectors_to_passages

        legacy = chunk_vectors_to_passages.run(session)
```
and add `"legacy_chunk_vectors": legacy` to the returned dict.

- [ ] **Step 5: Print it** in the `setup` command (`artmind/cli.py:5698`), after the fulltext line:
```python
        if result.get("legacy_chunk_vectors"):
            click.echo("Legacy chunk vectors: " + result["legacy_chunk_vectors"]["report"].removeprefix("legacy chunk vectors: "))
```
- [ ] **Step 6: Run** `uv run --group dev pytest test/test_migration_chunk_vectors.py test/ -q -k setup` — Expected: PASS (existing `setup_all` tests may need the migration patched: `monkeypatch.setattr("artmind.migrations.chunk_vectors_to_passages.run", lambda s: {"status": "none", "report": "legacy chunk vectors: none"})`).
- [ ] **Step 7: Commit** — `git add -A artmind test && git commit -m "feat(setup): one-off cleanup of legacy chunk vectors once passages exist"`

---

# Slice 6 — Docs, fixtures, verification

### Task 20: Docs, skill, snapshot list, plugin fixtures (model: haiku)

**Files:**
- Modify: `artmind/graph_snapshot.py:194-195`
- Modify: `artmind/skills/artmind-query/SKILL.md` (the vector-text paragraph ~line 427–452 and the chunks-only section ~line 27–46)
- Modify: `CLAUDE.md`, `docs/vault.md` ("Embeddings"), `justfile`
- Regenerate: `obsidian/artmind-obsidian/test/fixtures/*.json`

- [ ] **Step 1:** `artmind/graph_snapshot.py`: `"vector_indexes": ["passage_embedding", "user_chat_embedding"]`, `"fulltext_indexes": ["passage_text_ft", "user_chat_text_ft"]`. Snapshots already leave `DocPassage` out (it is not in `BASE_LABELS`); add one sentence to the `BASE_LABELS` comment: "`DocPassage`/`DocPassageHistory` are not exported: passages are derived from chunk text, and the restore's passage sweep rebuilds them." Confirm the restore path runs the passage sweep (`write-to-graph` calls `_run_passage_sweep`; grep `snapshot restore` in `cli.py` and make sure it reaches a `reconcile_passages` call — if it does not, add `_run_passage_sweep()` after the restore).
- [ ] **Step 2:** `SKILL.md` — replace the vector-text paragraph after the `artmind query vector-text ... --topK 5 --compact "<question>"` block with:
```
vector-text searches passages (~1200-char spans of the source text) by meaning and by keyword, fuses both rankings, groups hits into their parent chunk and spreads the top results across documents. Each row is a parent chunk: `chunk.text` (the whole chunk), `passage` (`char_start`/`char_end` of the span that matched, inside `chunk.text` — quote from there), `relevance` (0-1, how close the best passage is to the question; comparable across questions; `null` when only keywords matched), `matched_by` (`vector`, `fulltext` or both) and `score` (rank order only — never compare it across calls). `--diversity 0` gives pure relevance order (use it when you want several chunks of one document); `--context passage` returns only the matching passages (smaller, for scanning many hits). Use it for "where/when/how did X happen", motivations, quotes, or whenever graph output is too thin. In hybrid answers, take entity/relationship facts from the graph and narrative evidence from chunk text. When Route selected multiple domains, Ground should query all of them together in one call so results can be compared side by side.
```
and change rule 7 of the fallback list to: `7. vector-text sparse or weak (few rows, every \`relevance\` low, or rows matched by \`fulltext\` alone on a conceptual question) → state that the available artmind data does not answer the question.`
- [ ] **Step 3:** `CLAUDE.md`: in the repo-layout table add rows for `artmind/passages.py` + `passage_store.py` ("passages: ~1200-char retrieval units under each DocChunk; derived at commit, reconciled by `embed_sweep.reconcile_passages` (`ingest embed-passages`); carry every text vector; `_split_key`, `_embed_key`"), `artmind/embedding_cache.py`, `artmind/retrieval_rank.py`, `artmind/migrations/` ("temporary; delete when every graph's `setup` reports `legacy chunk vectors: none`"). In the routing reference change `vector-text` to mention `--diversity`, `--context`. Replace any `embed-chunks` with `embed-passages`.
- [ ] **Step 4:** `docs/vault.md` "Embeddings": replace chunk-vector wording with: vectors live on `:DocPassage` (and `:UserChat`, `:Entity`), never in committed staging; the passage sweep (`artmind ingest embed-passages`, run by `write-to-graph` and `vault sync`) builds passages from chunk text and embeds them, reading `<data>/embedding_cache.sqlite` first; a stale `_embed_key` (model or prefix change) or `_split_key` (passage size change) is re-done by the same sweep.
- [ ] **Step 5:** `justfile`: rename any `embed-chunks` recipe/command to `embed-passages`.
- [ ] **Step 6: Regenerate plugin fixtures**
```bash
ARTMIND_REGEN_PLUGIN_FIXTURES=1 uv run --group dev pytest test/test_plugin_fixtures.py -q
```
```bash
just obsidian-plugin-test
```
Expected: both pass. Review `git diff obsidian/artmind-obsidian/test/fixtures` — only the renamed fields and new vector-text fields should change; no corpus text (fixtures come from synthetic data).
- [ ] **Step 7: Full suite** — `just dev-test` — Expected: all pass.
- [ ] **Step 8: Commit** — `git add -A && git commit -m "docs: passages, embed-passages, vector-text fields; regenerate plugin fixtures"`

### Task 21: Live verification (model: sonnet; ⛔ CHECKPOINT B — ask the user first)

Hermetic tests accept any Cypher, so the `SEARCH ... WHERE` syntax, the `WITH [p._domain]` index option and the migration are proven only here. **Ask the user** which vault to run this on (recommended: the contracts eval vault on :7691) and wait for a yes. Then, inside that vault, with daemons stopped (`just dev-stop-daemons`):

- [ ] **Step 1:** `just dev-install` (editable install of this branch).
- [ ] **Step 2:** `artmind setup` — Expected: passage indexes created; `Legacy chunk vectors: kept — ... have no passages yet ...`. If `CREATE VECTOR INDEX ... WITH [p._domain]` fails, read the Neo4j Cypher manual's `SEARCH` / vector index page for the server version (`CALL dbms.components()`), fix the syntax in `setup.py` and `vector_query.py`, add a unit test for the corrected text, and re-run.
- [ ] **Step 3:** `artmind ingest embed-passages` — Expected: `chunks_split` equals the number of non-blank chunks; `embedded` > 0; `remaining` 0. Then `artmind ingest embed-entities --domain <d>` for each graph domain (re-embeds entities under the document prefix).
- [ ] **Step 4:** `artmind setup` again — Expected: `Legacy chunk vectors: none`.
- [ ] **Step 5:** `ARTMIND_NO_PROXY=1 artmind query vector-text --domain <d> --compact "<a question you know the answer to>"` — Expected: rows with `passage`, `relevance`, `matched_by`; no error. Repeat for a chunks-only domain if the vault has one, and with `--domain` set to a small domain to confirm it returns `topK` rows.
- [ ] **Step 6:** `ARTMIND_NO_PROXY=1 artmind query entity-context --domain <d> --entityId <id> --query "<question>" --compact` — Expected: chunks ranked; no error.
- [ ] **Step 7:** Restore check: `artmind docs retire --documentName <doc>` then `vector-text` no longer returns that document; `artmind docs restore --documentName <doc>` brings it back. (Check the exact `docs` subcommand names with `artmind docs --help`.)
- [ ] **Step 8:** Report results to the user, including the exact commands and outputs. The user then runs `ARTMIND_NO_PROXY=1 artmind retrieval eval --compare <baseline run>` in the baseline vault and decides on the reranker (spec §6.4).
- [ ] **Step 9:** After merge, file a GitHub issue "Remove chunk_vectors_to_passages migration" with the removal condition from the module docstring (ask the user before creating it).

---

## Self-review notes (for the plan author; kept for the executor's context)

- Spec §2.4 vs code: `_embed_key` is model + prefix version for **all** stored vectors; passages carry `_split_key` separately (spec amended).
- Spec §3.1: the vector cache is one machine-wide SQLite file instead of the per-document `embeddings.json` (spec amended); the old sidecar code is removed (Task 14).
- Spec §3.3: History passages keep their vector; the label swap alone takes them out of the index (spec amended) — restore needs no sweep.
- Spec §4.3: `vector_search` / `full_text_search` are replaced by private legs (spec amended); `relevance` is the chunk's best passage cosine.
- `header_path` already reached `DocChunk` (staged from `chunks_meta.json`); no change needed there (spec amended).
