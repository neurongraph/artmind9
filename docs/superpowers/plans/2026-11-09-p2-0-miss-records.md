# P2-0 Miss Records Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `artmind traces misses [--since] [--run] [--source]` derives one record per miss from stored traces (vocabulary, reformulation, identity, scope, structure, answer, user-stated), writes them to the vault's private `logs/misses/`, and prints counts only.

**Architecture:** A pure module `artmind/traces/misses.py` takes one turn's spans, answer, latest grade, feedback and the next turn of its conversation, and returns `Miss` records; a thin loader in the same module reads the registry, and a CLI command writes the JSONL and prints counts. Capture changes are limited to two fields on a span's evidence (`status`, `warnings`); the link from a cited source to the span that returned it is derived, not stored. No LLM call, no graph access, no planner import.

**Tech Stack:** Python 3.14, Click, sqlite3 (registry DB), pytest with `CliRunner`.

**Spec:** `docs/superpowers/specs/2026-11-08-classic-baseline-and-skill-simplification-design.md` §4.1 (and §7, which reads the records). Evidence that shaped the kinds: `docs/query-routing-study/20-classic-baseline.md`.

---

## Ground rules for every task

- Branch `baseline/v0.11-classic`, checkout `~/projects/artmind9`. Run tests with `uv run --group dev pytest ...`.
- **Never import `artmind.query_plan`** (`test/test_no_planner_entanglement.py` fails the suite).
- Miss records hold private corpus text (search phrases, names, must items). They go only to `<ARTMIND_HOME>/logs/misses/`. **stdout carries counts, kinds and ids only.** Tests use invented text.
- Commit after each task with the message given, ending with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

## Deviation from the spec, decided here

Spec §4.1 names two capture additions. (1) "each cited source is linked to the span that returned it": derived at read time from the answer's `### Sources` links and each span's `evidence.documents` / `evidence.provenance`, the way `traces/path.py` already credits citations; nothing new is stored. (2) "a follow-up that names a missed item stores the item": derived at read time from the next turn of the same conversation (`session_id`, `turn_index + 1`). The only stored additions are a span's `status` (`no_match` / `needs_disambiguation`) and its `warnings`, which the identity and scope kinds need and which spans drop today.

## File structure

| File | Change | Responsibility |
|---|---|---|
| `artmind/traces/evidence.py` | modify | add `status` and `warnings` to a span's evidence |
| `artmind/traces/misses.py` | create | `Miss`, citation linking, one detector per kind, `derive_turn`, `load_and_derive`, `summarise` |
| `artmind/cli.py` | modify | `traces misses` command beside `traces compare` |
| `test/test_traces_evidence.py` | modify | status / warnings capture |
| `test/test_traces_misses.py` | create | detectors on invented spans |
| `test/test_traces_misses_cli.py` | create | loader + CLI on an invented registry |
| `CLAUDE.md`, `artmind/skills/artmind-review/SKILL.md` | modify | document the command |

---

### Task 1: record `status` and `warnings` in span evidence

**Files:**
- Modify: `artmind/traces/evidence.py` (`_evidence`, near the end before `provenance = _provenance(payload)`)
- Test: `test/test_traces_evidence.py`

- [ ] **Step 1: Write the failing tests** (append to `test/test_traces_evidence.py`)

```python
def test_a_no_match_status_is_kept_in_evidence():
    from artmind.traces.evidence import summarize

    out = summarize({"query_type": "entity_resolve", "status": "no_match", "rows": []})
    assert out["rows"] == 0
    assert out["evidence"]["status"] == "no_match"


def test_needs_disambiguation_status_is_kept():
    from artmind.traces.evidence import summarize

    out = summarize({"query_type": "entity_resolve", "status": "needs_disambiguation", "rows": [{"x": 1}]})
    assert out["evidence"]["status"] == "needs_disambiguation"


def test_an_ordinary_status_is_not_recorded():
    from artmind.traces.evidence import summarize

    assert "status" not in summarize({"status": "ok", "rows": [{"x": 1}]})["evidence"]


def test_warnings_are_kept_capped_and_trimmed():
    from artmind.traces.evidence import summarize

    long = "unknown domain 'zz': " + "x" * 600
    out = summarize({"rows": [], "warnings": [long, 7, "second"] + ["w"] * 30})
    ws = out["evidence"]["warnings"]
    assert ws[0].startswith("unknown domain 'zz'") and len(ws[0]) <= 300
    assert "second" in ws and 7 not in ws
    assert len(ws) <= 10


def test_no_warnings_key_when_the_list_is_empty():
    from artmind.traces.evidence import summarize

    assert "warnings" not in summarize({"rows": [], "warnings": []})["evidence"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --group dev pytest test/test_traces_evidence.py -q`
Expected: the five new tests FAIL (`KeyError: 'status'` / `'warnings'`).

- [ ] **Step 3: Implement**

In `artmind/traces/evidence.py`, add constants under `_NOT_AN_ANSWER`:

```python
_WARNINGS_KEPT = 10
_WARNING_CHARS = 300
```

and in `_evidence`, just before `provenance = _provenance(payload)`:

```python
    if payload.get("status") in _NOT_AN_ANSWER:
        ev["status"] = payload["status"]
    warnings = [w[:_WARNING_CHARS] for w in payload.get("warnings") or [] if isinstance(w, str)]
    if warnings:
        ev["warnings"] = warnings[:_WARNINGS_KEPT]
```

(`_plan_evidence` calls `_evidence` on a rebuilt dict without `status`/`warnings`, so plan spans are unchanged.)

- [ ] **Step 4: Run the evidence and span tests**

Run: `uv run --group dev pytest test/test_traces_evidence.py test/test_traces_span.py test/test_traces_path.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/evidence.py test/test_traces_evidence.py
git commit -m "feat(traces): keep a command's no-match status and its warnings in span evidence

Miss records (spec 2026-11-08 §4.1) need entity-resolve's no_match /
needs_disambiguation and the unknown-domain warning, which spans dropped.
Warnings are capped at 10, 300 characters each."
```

---

### Task 2: the `Miss` record and citation linking

**Files:**
- Create: `artmind/traces/misses.py`
- Test: `test/test_traces_misses.py`

Span dicts are what `store.list_spans` returns: `span_id`, `command`, `args` (dict or None), `exit_code`, `rows`, `evidence` (dict), `started_at`. A cited source is a link under the answer's `### Sources` heading (`path.parse_sources`). A span "returned" a source when one of its `evidence.documents[].path` or `evidence.provenance.documents[].path/source_path/title` names the same document (`path._same_document` for paths; a title match is exact after casefolding).

- [ ] **Step 1: Write the failing tests** (`test/test_traces_misses.py`)

```python
"""Miss records (spec 2026-11-08 §4.1). Invented spans and text only."""

from artmind.traces import misses


def span(sid, command, *, args=None, rows=1, docs=(), prov=(), resolved=None, status=None, warnings=None, t=0.0):
    ev = {}
    if docs:
        ev["documents"] = [{"doc_id": f"id-{d}", "path": d} for d in docs]
    if prov:
        ev["provenance"] = {"documents": [{"doc_id": f"id-{p}", "path": None, "source_path": p, "title": None}
                                          for p in prov]}
    if resolved is not None:
        ev["resolved"] = resolved
    if status:
        ev["status"] = status
    if warnings:
        ev["warnings"] = warnings
    return {"span_id": sid, "command": command, "args": args or {}, "exit_code": 0, "rows": rows,
            "evidence": ev, "started_at": t}


ANSWER = "The fee is ten.\n\n### Sources\n- [fees](notes/fees.md)\n- [rates](<notes/rate sheet.md>)\n"


def test_cited_sources_link_to_the_first_span_that_returned_them():
    spans = [span("s1", "query vector-text", docs=["other.md"], t=1),
             span("s2", "query vector-text", docs=["notes/fees.md"], t=2),
             span("s3", "query survey", prov=["notes/rate sheet.md", "notes/fees.md"], t=3)]
    links = misses.cited_spans(ANSWER, spans)
    assert links == {"notes/fees.md": "s2", "notes/rate sheet.md": "s3"}


def test_a_citation_no_span_returned_maps_to_none():
    links = misses.cited_spans(ANSWER, [span("s1", "query vector-text", docs=["notes/fees.md"])])
    assert links["notes/rate sheet.md"] is None


def test_no_sources_section_means_no_links():
    assert misses.cited_spans("just text", [span("s1", "query vector-text", docs=["a.md"])]) == {}


def test_a_miss_serialises_with_its_kind_first():
    m = misses.Miss(kind="vocabulary", trace_id="t1", detail={"added_terms": ["fee"]}, span_id="s2")
    d = m.to_dict()
    assert list(d)[0] == "kind" and d["trace_id"] == "t1" and d["detail"] == {"added_terms": ["fee"]}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.traces.misses'`.

- [ ] **Step 3: Implement** (`artmind/traces/misses.py`)

```python
"""Miss records (spec 2026-11-08 §4.1): what knowledge a turn was missing, derived from stored traces.

One record per miss, kinds: vocabulary, reformulation, identity, scope, structure, answer, user-stated. Pure
detectors over span dicts (as `store.list_spans` returns them); `load_and_derive` reads the registry. No LLM call,
no graph. Records hold private corpus text: write them only under `<ARTMIND_HOME>/logs/misses/`, never to stdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from artmind.traces import path as path_mod

KINDS = ("vocabulary", "reformulation", "identity", "scope", "structure", "answer", "user-stated")
SEARCH_COMMANDS = frozenset({"query vector-text", "query survey"})


@dataclass(frozen=True)
class Miss:
    kind: str
    trace_id: str
    detail: dict = field(default_factory=dict)
    span_id: str | None = None
    run_id: str | None = None
    seq: int | None = None
    qid: str | None = None

    def to_dict(self) -> dict:
        return {"kind": self.kind, "trace_id": self.trace_id, "run_id": self.run_id, "seq": self.seq,
                "qid": self.qid, "span_id": self.span_id, "detail": self.detail}


def _span_documents(span: dict) -> list[tuple[str | None, str | None]]:
    """(path, title) pairs a span returned: evidence documents plus provenance documents."""
    ev = span.get("evidence") or {}
    out = [(d.get("path"), None) for d in ev.get("documents") or [] if isinstance(d, dict)]
    prov = ev.get("provenance") if isinstance(ev.get("provenance"), dict) else {}
    for d in prov.get("documents") or []:
        if isinstance(d, dict):
            out.append((d.get("source_path") or d.get("path"), d.get("title")))
    return out


def _returned(span: dict, cited: str) -> bool:
    for held, title in _span_documents(span):
        if isinstance(held, str) and held and path_mod._same_document(cited, held):
            return True
        if isinstance(title, str) and title and title.casefold() == cited.casefold():
            return True
    return False


def cited_spans(answer: str | None, spans: list[dict]) -> dict[str, str | None]:
    """{cited source: span_id of the first span (by start time) that returned it, or None}."""
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    links: dict[str, str | None] = {}
    for cited in path_mod.parse_sources(answer):
        links[cited] = next((s.get("span_id") for s in ordered if _returned(s, cited)), None)
    return links
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/misses.py test/test_traces_misses.py
git commit -m "feat(traces): miss record type and cited-source to span linking

Derived at read time from the answer's Sources links and each span's evidence
and provenance documents, the way path.py credits citations."
```

---

### Task 3: vocabulary and reformulation

**Files:**
- Modify: `artmind/traces/misses.py`
- Test: `test/test_traces_misses.py`

Rules:
- **Search phrase** of a span: `args["question"]` (vector-text and survey both use it), else `args["query"]`; only spans whose `command` is in `SEARCH_COMMANDS`.
- **vocabulary**: for each search span that returned a cited source, the content words of its phrase that are not in the user's question. Words: lowercase, `[a-z0-9]+`, length ≥ 3, not in `STOPWORDS`; a word counts as present when the question contains it or its stem (strip a trailing `s`, `es`, `ing`, `ed`). One record per such span with at least one added term: `detail = {"phrase", "added_terms", "sources"}`.
- **reformulation**: the first search span that returned a cited source is the winner; if at least one search span ran before it, one record `detail = {"attempts": n_before + 1, "phrases": [...earlier phrases], "winning_phrase"}`, `span_id` = the winner.

- [ ] **Step 1: Write the failing tests** (append)

```python
Q = "What fee applies when a payment bounces?"


def test_vocabulary_lists_terms_the_question_lacked_from_a_cited_span():
    spans = [span("s1", "query vector-text", args={"question": "returned direct debit charge"},
                  docs=["notes/fees.md"])]
    out = misses.vocabulary(Q, ANSWER, spans)
    assert len(out) == 1 and out[0].kind == "vocabulary" and out[0].span_id == "s1"
    assert out[0].detail["added_terms"] == ["returned", "direct", "debit", "charge"]
    assert out[0].detail["sources"] == ["notes/fees.md"]


def test_vocabulary_ignores_stems_and_stopwords_of_question_words():
    spans = [span("s1", "query vector-text", args={"question": "the fees for bounced payments"},
                  docs=["notes/fees.md"])]
    assert misses.vocabulary(Q, ANSWER, spans) == []


def test_vocabulary_ignores_spans_whose_results_were_not_cited():
    spans = [span("s1", "query vector-text", args={"question": "unrelated words entirely"}, docs=["x.md"])]
    assert misses.vocabulary(Q, ANSWER, spans) == []


def test_reformulation_counts_attempts_before_the_winning_phrase():
    spans = [span("s1", "query vector-text", args={"question": "bounce fee"}, docs=["x.md"], t=1),
             span("s2", "query survey", args={"question": "payment failure charge"}, rows=0, t=2),
             span("s3", "query vector-text", args={"question": "returned payment fee"}, docs=["notes/fees.md"], t=3),
             span("s4", "query vector-text", args={"question": "later"}, docs=["notes/fees.md"], t=4)]
    out = misses.reformulation(Q, ANSWER, spans)
    assert len(out) == 1 and out[0].span_id == "s3"
    assert out[0].detail == {"attempts": 3, "phrases": ["bounce fee", "payment failure charge"],
                             "winning_phrase": "returned payment fee"}


def test_no_reformulation_when_the_first_search_won():
    spans = [span("s1", "query vector-text", args={"question": "fee"}, docs=["notes/fees.md"])]
    assert misses.reformulation(Q, ANSWER, spans) == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: the five new tests FAIL (`AttributeError: ... 'vocabulary'`).

- [ ] **Step 3: Implement** (append to `artmind/traces/misses.py`; add `import re` at the top)

```python
STOPWORDS = frozenset(
    "the and for with that this what when where which who whom whose why how are was were has have had not "
    "but from into onto over under about after before during each every any all some our your their his her "
    "its they them you can could would should will shall may might must did does doing done than then there "
    "these those also just only very more most such per via".split())
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str | None) -> list[str]:
    seen: dict[str, None] = {}
    for w in _WORD.findall((text or "").casefold()):
        if len(w) >= 3 and w not in STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def _present(word: str, question_words: set[str]) -> bool:
    stem = _stem(word)
    return word in question_words or any(stem == _stem(q) for q in question_words)


def search_phrase(span: dict) -> str | None:
    if span.get("command") not in SEARCH_COMMANDS:
        return None
    args = span.get("args") if isinstance(span.get("args"), dict) else {}
    phrase = args.get("question") or args.get("query")
    return phrase if isinstance(phrase, str) and phrase.strip() else None


def _search_spans(spans: list[dict]) -> list[dict]:
    return [s for s in sorted(spans, key=lambda s: s.get("started_at") or 0.0) if search_phrase(s)]


def vocabulary(question: str, answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    links = cited_spans(answer, spans)
    by_span: dict[str, list[str]] = {}
    for cited, sid in links.items():
        if sid:
            by_span.setdefault(sid, []).append(cited)
    qwords = set(_words(question))
    out = []
    for s in _search_spans(spans):
        if s.get("span_id") not in by_span:
            continue
        added = [w for w in _words(search_phrase(s)) if not _present(w, qwords)]
        if added:
            out.append(Miss("vocabulary", trace_id, {"phrase": search_phrase(s), "added_terms": added,
                                                      "sources": by_span[s["span_id"]]}, span_id=s.get("span_id")))
    return out


def reformulation(question: str, answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    winners = {sid for sid in cited_spans(answer, spans).values() if sid}
    searches = _search_spans(spans)
    for i, s in enumerate(searches):
        if s.get("span_id") in winners:
            if i == 0:
                return []
            return [Miss("reformulation", trace_id, {"attempts": i + 1,
                                                     "phrases": [search_phrase(p) for p in searches[:i]],
                                                     "winning_phrase": search_phrase(s)},
                         span_id=s.get("span_id"))]
    return []
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/misses.py test/test_traces_misses.py
git commit -m "feat(traces): vocabulary and reformulation miss detectors"
```

---

### Task 4: identity and scope

**Files:**
- Modify: `artmind/traces/misses.py`
- Test: `test/test_traces_misses.py`

Rules:
- **identity**, per `query entity-resolve` span (and `query resolve-key`):
  - `evidence.status == "no_match"` → `detail {"reason": "no_match", "name": args.get("name") or args.get("question")}`;
  - `evidence.status == "needs_disambiguation"` → reason `ambiguous`;
  - else, with `evidence.resolved` non-empty: top score `< LOW_SCORE` (0.6) → reason `low_score` with the score; the top two scores within 10 % (reuse `analysis._ambiguous(span)`) → reason `ambiguous`; two resolved rows with the same casefolded name and different ids → reason `twins` with the ids. One record per span, first matching reason in that order (`twins` is checked before `low_score`, after the status reasons).
- **scope**:
  - each warning in a span's `evidence.warnings` that starts with `unknown domain` → `detail {"reason": "unknown_domain", "warning": ...}`;
  - a search or graph command re-run with the same command and a domain set that is a strict superset of an earlier run's (`path.span_domains`) → reason `widened`, `detail {"from": sorted(first), "to": sorted(later)}` (once per command);
  - a cited source whose span's domain set (`path.span_domains` of the span that returned it) is not covered by the first search span's domain set → reason `outside_first_scope`, with the source.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_identity_records_no_match_and_disambiguation():
    spans = [span("s1", "query entity-resolve", args={"name": "Person A"}, rows=0, status="no_match"),
             span("s2", "query entity-resolve", args={"name": "Person B"}, status="needs_disambiguation")]
    out = misses.identity(spans)
    assert [(m.span_id, m.detail["reason"]) for m in out] == [("s1", "no_match"), ("s2", "ambiguous")]
    assert out[0].detail["name"] == "Person A"


def test_identity_records_twins_before_a_low_score():
    resolved = [{"id": "e1", "name": "Org X", "entity_class": "ORG", "score": 0.5},
                {"id": "e2", "name": "org x", "entity_class": "ORG", "score": 0.2}]
    out = misses.identity([span("s1", "query entity-resolve", args={"name": "Org X"}, resolved=resolved)])
    assert out[0].detail == {"reason": "twins", "name": "Org X", "ids": ["e1", "e2"]}


def test_identity_records_a_low_top_score():
    resolved = [{"id": "e1", "name": "Place C", "score": 0.4}]
    out = misses.identity([span("s1", "query entity-resolve", args={"name": "Plac"}, resolved=resolved)])
    assert out[0].detail["reason"] == "low_score" and out[0].detail["score"] == 0.4


def test_a_confident_resolution_is_not_a_miss():
    resolved = [{"id": "e1", "name": "Place C", "score": 0.95}, {"id": "e2", "name": "Place D", "score": 0.3}]
    assert misses.identity([span("s1", "query entity-resolve", resolved=resolved)]) == []


def test_scope_records_an_unknown_domain_warning():
    out = misses.scope(None, [span("s1", "query vector-text", args={"domain": ["nope"]},
                                   warnings=["unknown domain 'nope': no schema"])])
    assert out[0].detail["reason"] == "unknown_domain"


def test_scope_records_a_widened_rerun():
    spans = [span("s1", "query vector-text", args={"domain": ["fam.a"], "question": "q"}, t=1),
             span("s2", "query vector-text", args={"domain": ["fam.a", "fam.b"], "question": "q"}, t=2)]
    out = misses.scope(None, spans)
    assert [m.detail for m in out] == [{"reason": "widened", "command": "query vector-text",
                                        "from": ["fam.a"], "to": ["fam.a", "fam.b"]}]


def test_scope_records_a_cited_source_outside_the_first_search_scope():
    spans = [span("s1", "query vector-text", args={"domain": ["fam.a"], "question": "q"}, docs=["x.md"], t=1),
             span("s2", "query graph pattern1", args={"domain": ["fam.b"]}, docs=["notes/fees.md"], t=2)]
    out = [m for m in misses.scope(ANSWER, spans) if m.detail["reason"] == "outside_first_scope"]
    assert out[0].detail["source"] == "notes/fees.md" and out[0].span_id == "s2"
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: the seven new tests FAIL.

- [ ] **Step 3: Implement** (append; add `from artmind.traces import analysis` to the imports)

```python
LOW_SCORE = 0.6
IDENTITY_COMMANDS = frozenset({"query entity-resolve", "query resolve-key"})


def _identity_reason(span: dict) -> dict | None:
    ev = span.get("evidence") or {}
    args = span.get("args") if isinstance(span.get("args"), dict) else {}
    name = args.get("name") or args.get("question")
    if ev.get("status") == "no_match":
        return {"reason": "no_match", "name": name}
    if ev.get("status") == "needs_disambiguation":
        return {"reason": "ambiguous", "name": name}
    resolved = [r for r in ev.get("resolved") or [] if isinstance(r, dict)]
    if not resolved:
        return None
    by_name: dict[str, list[str]] = {}
    for r in resolved:
        if isinstance(r.get("name"), str) and r.get("id"):
            by_name.setdefault(r["name"].casefold(), []).append(r["id"])
    twins = next((ids for ids in by_name.values() if len(set(ids)) > 1), None)
    if twins:
        return {"reason": "twins", "name": name, "ids": twins}
    top = resolved[0].get("score")
    if isinstance(top, (int, float)) and top < LOW_SCORE:
        return {"reason": "low_score", "name": name, "score": top}
    if analysis._ambiguous(span):
        return {"reason": "ambiguous", "name": name}
    return None


def identity(spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    out = []
    for s in sorted(spans, key=lambda s: s.get("started_at") or 0.0):
        if s.get("command") in IDENTITY_COMMANDS:
            reason = _identity_reason(s)
            if reason:
                out.append(Miss("identity", trace_id, reason, span_id=s.get("span_id")))
    return out


def scope(answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    ordered = sorted(spans, key=lambda s: s.get("started_at") or 0.0)
    out = []
    for s in ordered:
        for w in (s.get("evidence") or {}).get("warnings") or []:
            if isinstance(w, str) and w.startswith("unknown domain"):
                out.append(Miss("scope", trace_id, {"reason": "unknown_domain", "warning": w},
                                span_id=s.get("span_id")))
    first_by_command: dict[str, set[str]] = {}
    widened: set[str] = set()
    for s in ordered:
        domains = path_mod.span_domains(s)
        command = s.get("command") or ""
        if not domains or path_mod.layer_of(command) == "discovery":
            continue
        first = first_by_command.setdefault(command, domains)
        if domains > first and command not in widened:
            widened.add(command)
            out.append(Miss("scope", trace_id, {"reason": "widened", "command": command,
                                                "from": sorted(first), "to": sorted(domains)},
                            span_id=s.get("span_id")))
    searches = _search_spans(ordered)
    first_scope = path_mod.span_domains(searches[0]) if searches else None
    if first_scope:
        by_id = {s.get("span_id"): s for s in ordered}
        for cited, sid in cited_spans(answer, ordered).items():
            held = path_mod.span_domains(by_id[sid]) if sid else None
            if held and not any(path_mod._covers(f, d) for d in held for f in first_scope):
                out.append(Miss("scope", trace_id, {"reason": "outside_first_scope", "source": cited,
                                                    "first_scope": sorted(first_scope), "span_scope": sorted(held)},
                                span_id=sid))
    return out
```

Before implementing, read `path.span_domains` and `path._covers` (`artmind/traces/path.py:202-224`) and confirm `span_domains` returns a `set[str]` from `args["domain"]` (list) and `_covers(listed, domain)` is true when `domain` equals `listed` or sits under it (`listed.`-prefixed). If `span_domains` reads a different key, adapt the test fixtures' `args`, not the function.

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/misses.py test/test_traces_misses.py
git commit -m "feat(traces): identity and scope miss detectors

Identity: no match, ambiguity, twin nodes, a low top score. Scope: an unknown
domain, a re-run on a wider domain set, a cited source outside the first
search's scope."
```

---

### Task 5: structure, answer and user-stated

**Files:**
- Modify: `artmind/traces/misses.py`
- Test: `test/test_traces_misses.py`

Rules:
- **structure**: the turn's answer basis (`path.answer_basis(answer, spans)`) is `vector` (text only) while an identity span resolved an entity with no identity miss (a confident match), and no graph-layer span (`path.GRAPH_LAYERS`) ran → `detail {"reason": "text_despite_entity", "entities": [names]}`. Also any span whose evidence has `complete: false` → reason `incomplete_count` (the field arrives with P2-4; the detector is written now so P2-4 needs no change here).
- **answer**: from a grade detail dict (`{"grade", "must_missed", "must_not_hit", ...}`): one record per missed must item, `detail {"item", "grade"}`; one per must-not hit, `detail {"must_not": item, "grade"}`.
- **user-stated**: from feedback (`{"rating", "expected", "note"}`) when rating is `partial` or `wrong` and `expected` or `note` is set → `detail {"source": "feedback", "rating", "item": expected or note}`; from the next turn's question when it matches `FOLLOW_UP` → `detail {"source": "follow_up", "item": text after the matched phrase, stripped, ≤ 200 chars}`.

```python
FOLLOW_UP = re.compile(
    r"\b(?:you (?:missed|forgot|left out|did ?n[o']t (?:mention|include))|what about|"
    r"(?:also|please) (?:include|add|mention)|(?:is|was|are|were) missing)\b[:,]?\s*(.*)",
    re.IGNORECASE | re.DOTALL)
```

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_structure_flags_a_text_answer_when_an_entity_resolved():
    spans = [span("s1", "query entity-resolve", resolved=[{"id": "e1", "name": "Person A", "score": 0.97}], t=1),
             span("s2", "query vector-text", args={"question": "q"}, docs=["notes/fees.md"], t=2)]
    out = misses.structure(ANSWER, spans)
    assert out[0].detail == {"reason": "text_despite_entity", "entities": ["Person A"]}


def test_structure_is_quiet_when_the_graph_was_asked():
    spans = [span("s1", "query entity-resolve", resolved=[{"id": "e1", "name": "Person A", "score": 0.97}], t=1),
             span("s2", "query graph pattern3", docs=["notes/fees.md"], t=2)]
    assert misses.structure(ANSWER, spans) == []


def test_structure_flags_an_incomplete_count():
    s = span("s1", "query graph pattern1")
    s["evidence"]["complete"] = False
    assert misses.structure(None, [s])[0].detail["reason"] == "incomplete_count"


def test_answer_misses_come_from_the_grade():
    grade = {"grade": "partial", "must_missed": ["item one", "item two"], "must_not_hit": ["bad thing"]}
    out = misses.answer(grade)
    assert [m.detail for m in out] == [{"item": "item one", "grade": "partial"},
                                      {"item": "item two", "grade": "partial"},
                                      {"must_not": "bad thing", "grade": "partial"}]


def test_user_stated_from_feedback_and_a_follow_up():
    fb = {"rating": "partial", "expected": "should list the third option", "note": None}
    out = misses.user_stated(fb, "You missed: the weekend charge, and the cap.")
    assert out[0].detail == {"source": "feedback", "rating": "partial", "item": "should list the third option"}
    assert out[1].detail == {"source": "follow_up", "item": "the weekend charge, and the cap."}


def test_good_feedback_and_an_ordinary_follow_up_are_not_misses():
    assert misses.user_stated({"rating": "good", "expected": "x", "note": None}, "Thanks. And for 2025?") == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: the six new tests FAIL.

- [ ] **Step 3: Implement** (append)

```python
FOLLOW_UP = re.compile(
    r"\b(?:you (?:missed|forgot|left out|did ?n[o']t (?:mention|include))|what about|"
    r"(?:also|please) (?:include|add|mention)|(?:is|was|are|were) missing)\b[:,]?\s*(.*)",
    re.IGNORECASE | re.DOTALL)
ITEM_CHARS = 200


def structure(answer: str | None, spans: list[dict], *, trace_id: str = "") -> list[Miss]:
    out = []
    for s in spans:
        if (s.get("evidence") or {}).get("complete") is False:
            out.append(Miss("structure", trace_id, {"reason": "incomplete_count", "command": s.get("command")},
                            span_id=s.get("span_id")))
    graph_ran = any(path_mod.span_layer(s) in path_mod.GRAPH_LAYERS for s in spans)
    if not graph_ran and path_mod.answer_basis(answer, spans) == "vector":
        names = []
        for s in spans:
            if s.get("command") in IDENTITY_COMMANDS and _identity_reason(s) is None:
                top = ((s.get("evidence") or {}).get("resolved") or [{}])[0]
                if isinstance(top.get("name"), str):
                    names.append(top["name"])
        if names:
            out.append(Miss("structure", trace_id, {"reason": "text_despite_entity", "entities": names}))
    return out


def answer(grade: dict | None, *, trace_id: str = "") -> list[Miss]:
    if not isinstance(grade, dict):
        return []
    g = grade.get("grade")
    out = [Miss("answer", trace_id, {"item": item, "grade": g}) for item in grade.get("must_missed") or []]
    out += [Miss("answer", trace_id, {"must_not": item, "grade": g}) for item in grade.get("must_not_hit") or []]
    return out


def user_stated(feedback: dict | None, next_question: str | None, *, trace_id: str = "") -> list[Miss]:
    out = []
    if isinstance(feedback, dict) and feedback.get("rating") in ("partial", "wrong"):
        item = feedback.get("expected") or feedback.get("note")
        if item:
            out.append(Miss("user-stated", trace_id, {"source": "feedback", "rating": feedback["rating"],
                                                      "item": item}))
    match = FOLLOW_UP.search(next_question or "")
    if match and match.group(1).strip():
        out.append(Miss("user-stated", trace_id, {"source": "follow_up",
                                                  "item": match.group(1).strip()[:ITEM_CHARS]}))
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_traces_misses.py -q`
Expected: 22 passed.

- [ ] **Step 5: Commit**

```bash
git add artmind/traces/misses.py test/test_traces_misses.py
git commit -m "feat(traces): structure, answer and user-stated miss detectors

Structure also reads a span's complete: false, which P2-4 will add."
```

---

### Task 6: one turn, the loader and the summary

**Files:**
- Modify: `artmind/traces/misses.py`
- Test: `test/test_traces_misses_cli.py` (create; registry fixtures live here because they touch the DB)

`derive_turn` runs every detector on one turn and stamps `trace_id`, `run_id`, `seq`, `qid` on each record. `load_and_derive(*, since=None, run_refs=(), source=None)`:
- turns: `store.list_turns(source=source, since=since)` (no limit), filtered to `benchmark_run_id` in the resolved run ids when `run_refs` is given (resolve each ref with `compare._resolve`, which accepts an id or a unique prefix);
- spans: `store.list_spans_for(trace_ids)`;
- grade: the newest `trace_grades` row for `(benchmark_run_id, benchmark_seq)`, its `detail` JSON;
- qid: `benchmark_questions.qid` for `(run_id, seq)`;
- feedback: `store.latest_feedback(trace_id)`;
- next question: the turn with the same `session_id` and `turn_index + 1`.

`summarise(records, turns)` → `{"turns": n, "turns_with_misses": n, "misses": n, "by_kind": {kind: n for every kind in KINDS}, "by_reason": {"identity": {...}, "scope": {...}, "structure": {...}}}`. Counts only.

- [ ] **Step 1: Write the failing tests** (`test/test_traces_misses_cli.py`)

```python
"""traces misses: loader and CLI over an invented registry (spec 2026-11-08 §4.1)."""

import json

import pytest
from click.testing import CliRunner

from artmind.traces import misses, store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import artmind.cli as cli_mod

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path / "logs")


def _turn(tid, question, answer, *, session=None, run_id=None, seq=None, spans=(), started=1_700_000_000.0):
    store.insert_turn(tid, source="benchmark" if run_id else "admin", question=question, session_id=session,
                      benchmark_run_id=run_id, benchmark_seq=seq, started_at=started)
    store.update_turn(tid, status="done", answer=answer)
    for n, s in enumerate(spans):
        store.insert_span({"span_id": f"{tid}-s{n}", "trace_id": tid, "command": s["command"],
                           "args": json.dumps(s.get("args", {})), "exit_code": 0, "duration_ms": 1,
                           "rows": s.get("rows", 1), "rows_total": None, "truncated": 0,
                           "evidence": json.dumps(s.get("evidence", {})), "started_at": started + n + 0.5}, [])


ANSWER = "Ten.\n\n### Sources\n- [f](notes/fees.md)\n"


def test_load_and_derive_reads_spans_feedback_and_the_follow_up():
    _turn("t1", "What fee applies?", ANSWER, session="c1",
          spans=[{"command": "query vector-text", "args": {"question": "returned debit charge"},
                  "evidence": {"documents": [{"doc_id": "d1", "path": "notes/fees.md"}]}}])
    _turn("t2", "You missed the weekend surcharge", "ok", session="c1", started=1_700_000_100.0)
    store.add_feedback("t1", "partial", "the cap", None)
    records, turns = misses.load_and_derive()
    kinds = sorted(m.kind for m in records if m.trace_id == "t1")
    assert kinds == ["user-stated", "user-stated", "vocabulary"]
    summary = misses.summarise(records, turns)
    assert summary["turns"] == 2 and summary["by_kind"]["vocabulary"] == 1
    assert set(summary["by_kind"]) == set(misses.KINDS)


def test_answer_misses_come_from_the_latest_grade_of_a_benchmark_turn():
    from artmind import benchmark

    qs = [{"seq": 1, "qid": "Q07", "title": "t", "question": "invented?", "eval_comment": "Must: a; b"}]
    rid = benchmark._create_benchmark_run("/tmp/q.md", qs, contract="classic")
    _turn("b1", "invented?", "a only", run_id=rid, seq=1)
    conn = store._connect()
    conn.execute("INSERT INTO trace_grades (key, run_id, seq, model, grade, detail, created_at) VALUES"
                 " ('k1', ?, 1, 'm', 'partial', ?, 1)",
                 (rid, json.dumps({"grade": "partial", "must_found": ["a"], "must_missed": ["b"],
                                   "must_not_hit": [], "reason": "r"})))
    conn.commit()
    conn.close()
    records, _ = misses.load_and_derive(run_refs=[rid[:8]])
    ans = [m for m in records if m.kind == "answer"]
    assert [(m.qid, m.run_id, m.detail["item"]) for m in ans] == [("Q07", rid, "b")]


def test_cli_writes_records_and_prints_counts_only(tmp_path):
    from artmind.cli import cli

    _turn("t1", "A private question about Person Z?", ANSWER,
          spans=[{"command": "query vector-text", "args": {"question": "secret phrase words"},
                  "evidence": {"documents": [{"doc_id": "d1", "path": "notes/fees.md"}]}}])
    result = CliRunner().invoke(cli, ["traces", "misses", "--compact"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["command"] == "traces misses" and out["by_kind"]["vocabulary"] == 1
    for private in ("Person Z", "secret", "notes/fees.md"):
        assert private not in result.output
    written = list((tmp_path / "logs" / "misses").glob("misses-*.jsonl"))
    assert len(written) == 1 and out["records_file"] == str(written[0])
    lines = [json.loads(line) for line in written[0].read_text().splitlines()]
    assert lines[0]["kind"] == "vocabulary" and "secret" in lines[0]["detail"]["phrase"]


def test_cli_rejects_an_unknown_run():
    from artmind.cli import cli

    result = CliRunner().invoke(cli, ["traces", "misses", "--run", "nope"])
    assert result.exit_code == 1 and "no benchmark run" in result.output
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --group dev pytest test/test_traces_misses_cli.py -q`
Expected: FAIL (`AttributeError: ... 'load_and_derive'`; the CLI tests with "No such command 'misses'").

- [ ] **Step 3: Implement `derive_turn`, `load_and_derive`, `summarise`** (append to `artmind/traces/misses.py`; add `import json`)

```python
def derive_turn(turn: dict, spans: list[dict], *, grade: dict | None = None, feedback: dict | None = None,
                next_question: str | None = None, qid: str | None = None) -> list[Miss]:
    tid = turn["trace_id"]
    q, a = turn.get("question") or "", turn.get("answer")
    found = [*vocabulary(q, a, spans, trace_id=tid), *reformulation(q, a, spans, trace_id=tid),
             *identity(spans, trace_id=tid), *scope(a, spans, trace_id=tid), *structure(a, spans, trace_id=tid),
             *answer(grade, trace_id=tid), *user_stated(feedback, next_question, trace_id=tid)]
    stamp = {"run_id": turn.get("benchmark_run_id"), "seq": turn.get("benchmark_seq"), "qid": qid}
    return [Miss(m.kind, m.trace_id, m.detail, m.span_id, **stamp) for m in found]


def _latest_grade(conn, run_id: str | None, seq: int | None) -> dict | None:
    if not run_id or seq is None:
        return None
    row = conn.execute("SELECT detail FROM trace_grades WHERE run_id = ? AND seq = ? ORDER BY created_at DESC"
                       " LIMIT 1", (run_id, seq)).fetchone()
    return json.loads(row[0]) if row else None


def load_and_derive(*, since: float | None = None, run_refs=(), source: str | None = None
                    ) -> tuple[list[Miss], list[dict]]:
    from artmind.traces import compare, store

    conn = store._connect()
    try:
        run_ids = {compare._resolve(conn, ref) for ref in run_refs}
    finally:
        conn.close()
    turns = store.list_turns(source=source, since=since)
    if run_ids:
        turns = [t for t in turns if t.get("benchmark_run_id") in run_ids]
    spans_by = store.list_spans_for([t["trace_id"] for t in turns])
    records: list[Miss] = []
    conn = store._connect()
    try:
        for t in turns:
            nxt = None
            if t.get("session_id") and t.get("turn_index"):
                row = conn.execute("SELECT question FROM trace_turns WHERE session_id = ? AND turn_index = ?",
                                   (t["session_id"], t["turn_index"] + 1)).fetchone()
                nxt = row[0] if row else None
            qid = None
            if t.get("benchmark_run_id"):
                row = conn.execute("SELECT qid FROM benchmark_questions WHERE run_id = ? AND seq = ?",
                                   (t["benchmark_run_id"], t.get("benchmark_seq"))).fetchone()
                qid = row[0] if row else None
            records += derive_turn(t, spans_by.get(t["trace_id"], []),
                                   grade=_latest_grade(conn, t.get("benchmark_run_id"), t.get("benchmark_seq")),
                                   feedback=store.latest_feedback(t["trace_id"]), next_question=nxt, qid=qid)
    finally:
        conn.close()
    return records, turns


def summarise(records: list[Miss], turns: list[dict]) -> dict:
    by_kind = {k: 0 for k in KINDS}
    by_reason: dict[str, dict[str, int]] = {"identity": {}, "scope": {}, "structure": {}}
    for m in records:
        by_kind[m.kind] = by_kind.get(m.kind, 0) + 1
        if m.kind in by_reason:
            reason = m.detail.get("reason", "other")
            by_reason[m.kind][reason] = by_reason[m.kind].get(reason, 0) + 1
    return {"turns": len(turns), "turns_with_misses": len({m.trace_id for m in records}),
            "misses": len(records), "by_kind": by_kind, "by_reason": by_reason}
```

Check before wiring: `store.list_turns(source=..., since=..., limit=None)` returns turn dicts including `session_id`, `turn_index`, `benchmark_run_id`, `benchmark_seq` (it selects `*`; confirm at `artmind/traces/store.py:360`). `compare._resolve` raises `compare.CompareError("no benchmark run ...")`.

- [ ] **Step 4: Add the CLI command** (in `artmind/cli.py`, directly after `_write_compare_detail`)

```python
@traces_group.command("misses")
@_since_option
@click.option("--run", "run_refs", multiple=True,
              help="Only turns of this benchmark run (id or unique prefix; repeatable; comma-splittable)")
@click.option("--source", type=click.Choice(["admin", "benchmark"]), default=None)
@compact_option
def traces_misses(since, run_refs, source, compact) -> None:
    """Derive miss records from stored turns (spec 2026-11-08 §4.1): vocabulary, reformulation, identity, scope,
    structure, answer, user-stated. No LLM call. Records hold questions, phrases and names, so they go to
    <ARTMIND_HOME>/logs/misses/ (private); stdout carries counts only."""
    from datetime import datetime

    from artmind.traces import analysis, compare
    from artmind.traces import misses as misses_mod

    try:
        since_ts = analysis.parse_since(since)
        records, turns = misses_mod.load_and_derive(since=since_ts, run_refs=_parse_repeatable(run_refs),
                                                    source=source)
    except (ValueError, compare.CompareError) as exc:
        raise click.ClickException(str(exc)) from exc
    path = Path(LOGS_DIR) / "misses" / f"misses-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(m.to_dict(), ensure_ascii=False) + "\n" for m in records),
                    encoding="utf-8")
    _echo_json({**misses_mod.summarise(records, turns), "records_file": str(path)}, compact)
```

No `command` key: `_echo_json`'s envelope adds the canonical Click path (`traces misses`), as it does for `traces compare`.

- [ ] **Step 5: Run the tests**

Run: `uv run --group dev pytest test/test_traces_misses.py test/test_traces_misses_cli.py test/test_cli_conventions.py test/test_cli_guide.py test/test_no_planner_entanglement.py -q`
Expected: all pass. If `test_cli_conventions.py` flags `traces misses` as a write command (it writes a log file), add it to `_READ_ONLY` in that test with the reason "writes only its own private log under logs/misses, like traces compare", matching how `traces compare` is handled.

- [ ] **Step 6: Commit**

```bash
git add artmind/traces/misses.py artmind/cli.py test/test_traces_misses_cli.py test/test_cli_conventions.py
git commit -m "feat(traces): artmind traces misses derives miss records from stored turns

Reads spans, the latest grade, feedback and the next turn of the conversation;
writes one JSON line per miss to logs/misses/ (private) and prints counts by
kind and reason only. No LLM call."
```

---

### Task 7: docs

**Files:**
- Modify: `CLAUDE.md` (the `artmind/traces/` row and the command-routing line for `artmind traces <cmd>`)
- Modify: `artmind/skills/artmind-review/SKILL.md` (where it lists `traces` commands for triage)

- [ ] **Step 1: Edit `CLAUDE.md`**

In the `artmind/traces/` row, after the sentence about `compare`, add:

```
`misses [--since] [--run] [--source]`: miss records derived from stored turns (`misses.py`; kinds vocabulary, reformulation, identity, scope, structure, answer, user-stated; spec 2026-11-08 §4.1): spans, the latest `trace_grades` row, feedback and the next turn of the conversation; one JSON line per miss to `logs/misses/` (private), counts only on stdout, no LLM call. Spans keep a command's `no_match` / `needs_disambiguation` status and its warnings for it.
```

and in the command-routing list change the `artmind traces <cmd>` line to include `misses`:

```
- `artmind traces <cmd>` — recorded query traces: `list`, `show`, `triage`, `feedback`, `stats`, `compare`, `misses`, `prune` (not proxied)
```

- [ ] **Step 2: Edit the review skill**

Find the section of `artmind/skills/artmind-review/SKILL.md` that lists `artmind traces` commands and add one line:

```
- `artmind traces misses --since 7d` (or `--run <id>`): counts of miss records by kind; the records themselves are in the `records_file` it names (private, under logs/misses). Read them to see which vocabulary, names, scopes or must items the agent lacked.
```

- [ ] **Step 3: Run the doc-drift tests**

Run: `uv run --group dev pytest test/test_cli_guide.py test/test_cli_conventions.py -q`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md artmind/skills/artmind-review/SKILL.md
git commit -m "docs: traces misses in CLAUDE.md and the review skill"
```

---

### Task 8: gate — full suite and one live derivation per vault

- [ ] **Step 1: Full hermetic suite**

Run: `uv run --group dev pytest test/ -q`
Expected: all pass (≈ 6,420), 0 failed.

- [ ] **Step 2: Live derivation on banking** (read-only on the registry; writes one private log file)

```bash
cd ~/artmind_vaults/banking_corpus && artmind traces misses --run f03056a8 --compact
```

Expected: `turns: 36`; `by_kind.answer` equals the must items G missed plus its must-not hits (61 + 4 = 65 per note 20's grades for G: 221 − 160 missed, 4 must-not hits) — if not equal, the loader is not reading the newest grade; `records_file` exists. stdout contains no question text.

- [ ] **Step 3: Live derivation on brain01**

```bash
cd ~/artmind_vaults/brain01 && artmind traces misses --run 7a1d832d --compact
```

Expected: `turns: 29`; `by_kind.answer` = 192 − 120 missed + 12 must-not hits = 84; `identity` > 0 (twin and ambiguity cases exist in this vault per note 20). Open the `records_file` and check by eye that Q15's records show no `pattern8` path and Q19's show the twin organisation ids.

- [ ] **Step 4: Note the result** in `docs/query-routing-study/20-classic-baseline.md` under a new heading `## Miss records (P2-0)`: counts by kind for G and G1 only (no record text), and commit:

```bash
git add docs/query-routing-study/20-classic-baseline.md
git commit -m "docs(study): miss-record counts for runs G and G1"
```

---

## Self-review (done while writing)

- Spec §4.1 kinds: vocabulary (Task 3), reformulation (3), identity incl. twins and follow-up correction via user-stated (4, 5), scope incl. sibling re-run, unknown domain and out-of-scope citation (4), structure incl. `complete: false` (5), answer (5), user-stated (5). Capture additions: source→span by derivation (2), follow-up item by derivation (5, 6); stored additions limited to status/warnings (1). Output under `logs/misses/`, counts on stdout (6). No LLM call, not a measured variant; gate = suite + one live run per vault (8).
- Names used across tasks: `Miss`, `cited_spans`, `search_phrase`, `vocabulary`, `reformulation`, `identity`, `scope`, `structure`, `answer`, `user_stated`, `derive_turn`, `load_and_derive`, `summarise`, `KINDS`, `SEARCH_COMMANDS`, `IDENTITY_COMMANDS`, `LOW_SCORE`, `FOLLOW_UP` — each defined once.
- Brief-shape citations: provenance documents carry `source_path` or only a title; `_returned` checks both.
