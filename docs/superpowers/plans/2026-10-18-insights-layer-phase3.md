# Insights layer — phase 3 (narration and circle naming) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At the end of `artmind insights refresh`, an LLM writes:
- one checked sentence for each top finding;
- a 1–3 word name for each community circle.

Both are cached across runs, and the run still succeeds if the LLM fails.

**Architecture:**
- **`artmind/insights/prompts.py`** holds versioned prompt templates and the post-checks:
  - a narration may name only its own subjects;
  - every number must appear in the finding's stats;
  - outcome-lift findings get no causal wording.
- **`artmind/insights/narrate.py`** does the work:
  - selects which findings to narrate;
  - reuses cached narrations from the previous snapshot by content hash;
  - batches the rest into JSON-returning LLM calls;
  - names circles, keeping a previous name while membership drifts under 30%.
- **The runner** calls it after the diff and before the snapshot is written.
- **The LLM** is passed in as a callable, so tests are hermetic. The default calls `extraction.call_llm` with the configured provider and logs to `llm_calls.log`.

**Tech Stack:** Python ≥3.14, existing `artmind.extraction.call_llm` / `parse_json_response`, `utils.functions.resolve_llm_model`, pytest.

Spec: `docs/superpowers/specs/2026-10-17-insights-layer-design.md` §11; phase 3 = §15 item 3.

**Decision recorded here (spec §11 is updated in Task 4).** "Top `narrate_top` plus every new finding" would narrate about 200 findings on a first run. The budget is therefore:
- the top `narrate_top` findings by score;
- plus up to `narrate_top` more new findings;
- uncached findings only;
- batched at 20 per LLM call.

---

## Conventions for every task

- Run only the targeted test files with `uv run --group dev pytest <files> -q`. Never run the full suite.
- Tests are hermetic: LLM calls are always stubbed, and all data is synthetic.
- Commit only your own paths: `git add <new files>`, then `git commit -m "<msg>" -- <paths>`. Never use `-a`/`-A`. Retry on `index.lock`.
- End every commit message with the Co-Authored-By trailer your system reminder specifies.
- Branch: `feat/insights-phase3`.

## File map

| File | Change |
|---|---|
| `artmind/insights/model.py` | `Finding.narration_key`, `Run.llm` |
| `artmind/insights/prompts.py` | new: `PROMPT_VERSION`, templates, `compact_stats`, `check_narration`, `check_circle_name`, `allowed_numbers` |
| `artmind/insights/narrate.py` | new: `narration_key`, `select_for_narration`, `narrate_findings`, `name_circles`, `default_llm` |
| `artmind/extraction.py` | `call_llm(..., call_type="chat")` |
| `artmind/insights/runner.py` | call narrate; `narrate` and `llm` params; fingerprint `narrated` |
| `artmind/cli.py` | `insights refresh --no-narrate` |
| tests | `test_insights_model.py`, `test_insights_prompts.py` (new), `test_insights_narrate.py` (new), `test_insights_runner.py`, `test_insights_cli.py` |

---

### Task 1: Model fields, prompts and post-checks

**Files:**
- Modify: `artmind/insights/model.py`
- Create: `artmind/insights/prompts.py`
- Test: `test/test_insights_model.py` (append), `test/test_insights_prompts.py` (new)

- [ ] **Step 1: Write the failing tests**

Append to `test/test_insights_model.py`:

```python
def test_narration_key_and_run_llm_round_trip():
    f = _finding(narration="Asha has gone quiet.", narration_key="abc")
    assert Finding.from_dict(f.to_dict()).narration_key == "abc"
    run = Run(domain="demo", created_at="t", as_of=None, fingerprint={}, anchors=[], profile={}, plan=[],
              findings=[f], circles=[], diff={}, llm={"calls": 1})
    assert Run.from_dict(run.to_dict()).llm == {"calls": 1}
    legacy = run.to_dict()
    del legacy["llm"]
    assert Run.from_dict(legacy).llm == {}
```

`test/test_insights_prompts.py`:

```python
from artmind.insights.model import Finding
from artmind.insights.prompts import (
    PROMPT_VERSION, allowed_numbers, check_circle_name, check_narration, compact_stats, finding_prompt, circle_prompt,
)


def finding(detector="dormancy", stats=None, subjects=None):
    return Finding(key="k", detector=detector, kind="corpus", score=0.8, title="Quiet: Asha",
                   subjects=subjects if subjects is not None else [{"_id": "p1", "name": "Asha Bose", "class": "PERSON"}],
                   stats=stats if stats is not None else {"last_seen": "2025-03-02", "cadence_days": 18.0,
                                                          "silence_days": 578, "support": 30})


KNOWN = {"asha bose", "ben okoro", "acme", "home"}


def test_allowed_numbers_include_rounded_forms_and_date_parts():
    nums = allowed_numbers({"share": 0.4567, "silence_days": 578, "last_seen": "2025-03-02",
                            "rates": {"2023": 12.25}, "list": [3, 4]})
    for n in ("0.46", "0.5", "46", "45.7", "578", "2025", "2023", "12.2", "12", "3", "4"):
        assert n in nums, n


def test_good_narration_passes():
    ok, reason = check_narration("Asha Bose has not appeared for 578 days, against a usual gap of 18 days.",
                                 finding(), KNOWN)
    assert ok, reason


def test_unknown_entity_number_and_causal_wording_are_rejected():
    assert check_narration("Asha Bose went quiet after meeting Ben Okoro.", finding(), KNOWN) == (False, "names Ben Okoro")
    assert check_narration("Asha Bose has been quiet for 600 days.", finding(), KNOWN)[1] == "number 600 not in stats"
    lift = finding("outcome_lift", stats={"negative_share": 0.7, "baseline_negative_share": 0.4, "z": 3.1})
    assert check_narration("Days with Asha Bose cause worse moods.", lift, KNOWN)[1] == "causal wording"
    assert check_narration("", finding(), KNOWN) == (False, "empty")
    assert check_narration("word " * 45, finding(), KNOWN) == (False, "too long")


def test_sentence_start_capital_is_not_an_entity():
    ok, _ = check_narration("Home is where Asha Bose spends 30 days.", finding(subjects=[
        {"_id": "p1", "name": "Asha Bose", "class": "PERSON"}, {"_id": "l1", "name": "Home", "class": "LOCATION"}]), KNOWN)
    assert ok


def test_circle_names():
    assert check_circle_name("Work team") == (True, "")
    assert check_circle_name("The extended family of the diarist")[0] is False
    assert check_circle_name("Team 2")[0] is False
    assert check_circle_name("")[0] is False


def test_compact_stats_truncates():
    s = compact_stats({"members": list(range(50)), "rates": {str(i): i for i in range(30)}, "x": 1})
    assert len(s["members"]) == 5 and len(s["rates"]) == 12 and s["x"] == 1


def test_prompts_carry_version_and_items():
    p = finding_prompt([finding()])
    assert '"key": "k"' in p and "JSON" in p
    c = circle_prompt([{"id": "PERSON:a", "members": [{"name": "A"}], "shared": ["Acme"]}])
    assert "PERSON:a" in c
    assert isinstance(PROMPT_VERSION, int)
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_model.py test/test_insights_prompts.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/model.py`:
- Add the field `narration_key: str | None = None` to `Finding`, after `narration`.
- Add the field `llm: dict = field(default_factory=dict)` to `Run`, after `diff`.
- In `Run.to_dict`, add `"llm": self.llm`.
- In `Run.from_dict`, add `llm=data.get("llm") or {}`.

`artmind/insights/prompts.py`:

```python
"""Narration prompts and their post-checks (spec 2026-10-17 §11).

Bump PROMPT_VERSION whenever a template or a check changes: it is part of
every cached narration's key, so old captions are rewritten under the new rules.
"""

from __future__ import annotations

import json
import re

PROMPT_VERSION = 1
MAX_WORDS = 40
CIRCLE_MAX_WORDS = 3
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_CAUSAL = re.compile(r"\b(cause[sd]?|causing|because|leads? to|led to|results? in|makes?|made|due to|drives?|so that)\b", re.I)
_CAPITALISED = re.compile(r"\b[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3}")

FINDING_TEMPLATE = """You write one-sentence captions for findings computed from a personal knowledge graph.

Rules:
- Use only the facts in each finding's JSON. Add no facts, causes, advice or judgement.
- For detector "outcome_lift" describe co-occurrence only. Never write "causes", "because", "leads to", "makes" or "due to".
- Name only the subjects listed in that finding.
- Every number you write must appear in that finding's stats; round to at most one decimal place.
- At most 30 words per caption. Plain, neutral language.

Return only JSON: an object mapping each finding's "key" to its caption, with exactly these keys.

Findings:
{items}
"""

CIRCLE_TEMPLATE = """Name each group of people from a personal knowledge graph with a short label of 1 to 3 words,
such as "Family", "Work team" or "College friends". Use only the information given. Do not use a member's
name. Do not use numbers.

Return only JSON: an object mapping each circle's "id" to its label.

Circles:
{items}
"""


def compact_stats(stats: dict, max_items: int = 5, max_keys: int = 12) -> dict:
    out = {}
    for k, v in stats.items():
        if isinstance(v, list):
            out[k] = v[:max_items]
        elif isinstance(v, dict):
            out[k] = dict(list(v.items())[:max_keys])
        else:
            out[k] = v
    return out


def finding_prompt(findings) -> str:
    items = [{"key": f.key, "detector": f.detector, "title": f.title,
              "subjects": [{"name": s.get("name"), "class": s.get("class")} for s in f.subjects[:10]],
              "stats": compact_stats(f.stats)} for f in findings]
    return FINDING_TEMPLATE.format(items=json.dumps(items, ensure_ascii=False, indent=1))


def circle_prompt(circles: list[dict]) -> str:
    return CIRCLE_TEMPLATE.format(items=json.dumps(circles, ensure_ascii=False, indent=1))


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def allowed_numbers(stats: dict) -> set[str]:
    """Every number a caption may use: each numeric stat with its rounded and percentage forms,
    plus digit runs inside strings (dates, periods)."""
    out: set[str] = set()
    for v in _walk(stats):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            for x in (v, v * 100) if 0 < abs(v) <= 1 else (v,):
                out.update({f"{x:g}", f"{round(x, 1):g}", f"{round(x, 2):g}", str(int(round(x))), str(int(x))})
        else:
            out.update(_NUMBER.findall(str(v)))
    return out


def check_narration(text: str, finding, known_names: set[str]) -> tuple[bool, str]:
    """(ok, reason). known_names are lowercased names of every entity in the domain."""
    text = (text or "").strip()
    if not text:
        return False, "empty"
    if len(text.split()) > MAX_WORDS:
        return False, "too long"
    if finding.detector == "outcome_lift" and _CAUSAL.search(text):
        return False, "causal wording"
    allowed = {str(s.get("name", "")).casefold() for s in finding.subjects}
    for m in _CAPITALISED.finditer(text):
        phrase = m.group(0)
        words = phrase.split()
        for i in range(len(words)):
            for j in range(len(words), i, -1):
                cand = " ".join(words[i:j]).strip(".,;:'’").casefold()
                if cand in known_names and cand not in allowed:
                    return False, f"names {' '.join(words[i:j]).strip('.,;:')}"
    numbers = allowed_numbers(finding.stats)
    for n in _NUMBER.findall(text):
        if n not in numbers:
            return False, f"number {n} not in stats"
    return True, ""


def check_circle_name(name: str) -> tuple[bool, str]:
    name = (name or "").strip().strip('"')
    if not name:
        return False, "empty"
    if len(name.split()) > CIRCLE_MAX_WORDS or len(name) > 30:
        return False, "too long"
    if any(ch.isdigit() for ch in name):
        return False, "has digits"
    return True, ""
```

- [ ] **Step 4: Run the tests and see them pass**

Same command as Step 2. Expected: all PASS.

`test_sentence_start_capital_is_not_an_entity` passes because "Home" is a listed subject. A capitalised word that is not a known entity name is never rejected, because the check only looks up phrases in `known_names`.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/prompts.py test/test_insights_prompts.py
git commit -m "feat(insights): narration prompts and post-checks" -- artmind/insights/model.py artmind/insights/prompts.py test/test_insights_model.py test/test_insights_prompts.py
```

---

### Task 2: Narration and circle naming

**Files:**
- Create: `artmind/insights/narrate.py`
- Test: `test/test_insights_narrate.py`

- [ ] **Step 1: Write the failing test**

```python
import json

from _insights_helpers import ent, make_ctx
from artmind.insights import narrate
from artmind.insights.model import Finding


def f(key, score, diff="same", name="Asha Bose", stats=None, detector="dormancy"):
    return Finding(key=key, detector=detector, kind="corpus", score=score, title=f"Quiet: {name}", diff=diff,
                   subjects=[{"_id": key, "name": name, "class": "PERSON"}],
                   stats=stats or {"silence_days": 578, "cadence_days": 18})


class StubLLM:
    def __init__(self, reply=None, raises=None):
        self.prompts, self.reply, self.raises = [], reply, raises

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        if self.reply is not None:
            return self.reply(prompt) if callable(self.reply) else self.reply
        keys = [item["key"] for item in json.loads(prompt.split("Findings:\n", 1)[1])]
        return json.dumps({k: "Asha Bose has not appeared for 578 days." for k in keys})


def ctx_for():
    return make_ctx([ent("p1", "PERSON", ["2024-01-01"], name="Asha Bose"),
                     ent("p2", "PERSON", ["2024-01-01"], name="Ben Okoro")])


def test_selection_budget_top_plus_new():
    fs = [f(f"k{i}", score=1 - i / 100, diff="new" if i % 2 else "same") for i in range(40)]
    fs.append(f("gone", 0.99, diff="gone"))
    chosen = narrate.select_for_narration(fs, top=5)
    keys = [x.key for x in chosen]
    assert keys[:5] == ["k0", "k1", "k2", "k3", "k4"] and len(keys) == 10 and "gone" not in keys
    assert all(int(k[1:]) % 2 for k in keys[5:])


def test_narrates_batches_checks_and_caches():
    ctx, llm = ctx_for(), StubLLM()
    fs = [f(f"k{i}", 0.9 - i / 100) for i in range(25)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx, llm=llm, top=25)
    assert len(llm.prompts) == 2                     # 20 + 5
    assert stats == {"narrated": 25, "cached": 0, "rejected": 0, "calls": 2, "errors": 0}
    assert all(x.narration and x.narration_key for x in fs)

    again = [f(f"k{i}", 0.9 - i / 100) for i in range(25)]
    llm2 = StubLLM()
    stats2 = narrate.narrate_findings(again, previous=fs, ctx=ctx, llm=llm2, top=25)
    assert llm2.prompts == [] and stats2["cached"] == 25 and again[0].narration == fs[0].narration


def test_changed_stats_invalidate_the_cache():
    ctx = ctx_for()
    first = [f("k0", 0.9)]
    narrate.narrate_findings(first, previous=None, ctx=ctx, llm=StubLLM(), top=5)
    changed = [f("k0", 0.9, stats={"silence_days": 600, "cadence_days": 18})]
    llm = StubLLM(reply=lambda p: json.dumps({"k0": "Asha Bose has not appeared for 600 days."}))
    narrate.narrate_findings(changed, previous=first, ctx=ctx, llm=llm, top=5)
    assert len(llm.prompts) == 1 and "600" in changed[0].narration


def test_rejected_and_failed_narrations_leave_none():
    ctx = ctx_for()
    bad = StubLLM(reply=json.dumps({"k0": "Asha Bose stopped seeing Ben Okoro.", "k1": ""}))
    fs = [f("k0", 0.9), f("k1", 0.8)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx, llm=bad, top=5)
    assert [x.narration for x in fs] == [None, None] and stats["rejected"] == 2
    boom = [f("k0", 0.9)]
    stats = narrate.narrate_findings(boom, previous=None, ctx=ctx, llm=StubLLM(raises=RuntimeError("down")), top=5)
    assert boom[0].narration is None and stats["errors"] == 1


def test_unparseable_reply_counts_as_error():
    fs = [f("k0", 0.9)]
    stats = narrate.narrate_findings(fs, previous=None, ctx=ctx_for(), llm=StubLLM(reply="not json at all"), top=5)
    assert fs[0].narration is None and stats["errors"] == 1


def circle(cid, members, name=None, source="template"):
    return {"id": cid, "class": "PERSON", "lead": members[0], "name": name or f"Circle around {members[0]}",
            "name_source": source, "members": members}


def test_circle_naming_and_drift_reuse():
    ents = [ent(i, "PERSON", ["2024-01-01"], name=i.upper()) for i in "abcdefghij"]
    ents.append(ent("org", "ORGANIZATION", ["2024-01-01"], name="Acme"))
    ctx = make_ctx(ents, entity_props={("PERSON", "relationship_to_diarist"): {"a": "colleague"}})
    llm = StubLLM(reply=lambda p: json.dumps({"PERSON:a": "Work team", "PERSON:f": "Team 7"}))
    circles = [circle("PERSON:a", list("abcde")), circle("PERSON:f", list("fghij"))]
    stats = narrate.name_circles(circles, previous=None, ctx=ctx, llm=llm)
    assert circles[0]["name"] == "Work team" and circles[0]["name_source"] == "llm"
    assert circles[1]["name"] == "Circle around f" and circles[1]["name_source"] == "template"   # digits rejected
    assert '"colleague"' in llm.prompts[0] and "Acme" in llm.prompts[0]
    assert stats == {"named": 1, "kept": 0, "rejected": 1, "calls": 1, "errors": 0}

    drifted = [circle("PERSON:b", list("abcde") + ["z"]), circle("PERSON:f", list("fghij"))]
    llm2 = StubLLM(reply=lambda p: json.dumps({"PERSON:f": "Old friends"}))
    stats2 = narrate.name_circles(drifted, previous=circles, ctx=ctx, llm=llm2)
    assert drifted[0]["name"] == "Work team" and drifted[0]["name_source"] == "llm" and stats2["kept"] == 1
    assert drifted[1]["name"] == "Old friends" and "PERSON:b" not in llm2.prompts[0]
```

(`{a,b,c,d,e,z}` vs `{a,b,c,d,e}` has Jaccard 5/6, about 0.83, which is ≥ 0.7, so the name is kept. `PERSON:f`'s previous name was a template, so it is asked again.)

Also add:

```python
def test_circle_failure_keeps_template():
    ctx = make_ctx([ent("a", "PERSON", ["2024-01-01"])])
    cs = [circle("PERSON:a", ["a"])]
    stats = narrate.name_circles(cs, previous=None, ctx=ctx, llm=StubLLM(raises=RuntimeError("x")))
    assert cs[0]["name_source"] == "template" and stats["errors"] == 1


def test_circle_findings_get_the_circle_name():
    cs = [circle("PERSON:a", list("abc"), name="Work team", source="llm")]
    fs = [Finding(key="communities:PERSON:circle:a", detector="communities", kind="corpus", score=0.5,
                  title="Circle around A (3)", stats={"members": list("abc")}),
          Finding(key="communities:PERSON:share:a", detector="communities", kind="corpus", score=0.5,
                  title="Circle around A: share of mentions", stats={"members": list("abc")})]
    narrate.label_circle_findings(fs, cs)
    assert all(x.stats["circle_name"] == "Work team" for x in fs)
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_narrate.py -q`. Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** `artmind/insights/narrate.py`

```python
"""LLM narration of findings and naming of circles (spec 2026-10-17 §11).

The LLM is a callable `prompt -> text`, injected so tests never reach a model;
`default_llm()` uses the configured provider. Every caption is checked by
prompts.check_narration; a rejected or failed one leaves `narration` None,
so the UI falls back to the template title. Nothing here can fail a run.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable

from loguru import logger

from artmind.insights.graphs import jaccard
from artmind.insights.prompts import (
    PROMPT_VERSION, check_circle_name, check_narration, circle_prompt, finding_prompt,
)

BATCH = 20
KEEP_NAME_JACCARD = 0.7
MAX_CIRCLE_MEMBERS = 10

LLM = Callable[[str], str]


def default_llm() -> tuple[LLM, str]:
    from artmind.extraction import call_llm
    from utils.functions import load_env, resolve_llm_model

    model = resolve_llm_model(load_env())
    return (lambda prompt: call_llm(model, prompt, call_type="insights")), model


def narration_key(f) -> str:
    payload = json.dumps({"v": PROMPT_VERSION, "d": f.detector, "t": f.title, "s": f.subjects, "x": f.stats},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def select_for_narration(findings, top: int) -> list:
    live = sorted((f for f in findings if f.diff != "gone"), key=lambda f: (-f.score, f.key))
    chosen = live[:top]
    seen = {f.key for f in chosen}
    chosen += [f for f in live if f.diff == "new" and f.key not in seen][:top]
    return chosen


def _parse(text: str) -> dict:
    from artmind.extraction import parse_json_response

    data = parse_json_response(text)
    if not isinstance(data, dict):
        raise ValueError("reply is not a JSON object")
    return data


def narrate_findings(findings, *, previous, ctx, llm: LLM, top: int) -> dict:
    """Mutates findings in place (narration, narration_key). Returns counts."""
    stats = Counter({"narrated": 0, "cached": 0, "rejected": 0, "calls": 0, "errors": 0})
    cache = {p.key: p for p in (previous or []) if p.narration and p.narration_key}
    for f in findings:
        if f.diff == "gone":
            continue
        k = narration_key(f)
        hit = cache.get(f.key)
        if hit is not None and hit.narration_key == k:
            f.narration, f.narration_key = hit.narration, k
            stats["cached"] += 1
    todo = [f for f in select_for_narration(findings, top) if f.narration is None]
    known = {e.name.casefold() for e in ctx.entities if e.name}
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        stats["calls"] += 1
        try:
            replies = _parse(llm(finding_prompt(batch)))
        except Exception as exc:  # any provider or parse failure: leave the template titles
            logger.warning("insights narration batch failed: {}", exc)
            stats["errors"] += 1
            continue
        for f in batch:
            text = str(replies.get(f.key, "") or "").strip()
            ok, reason = check_narration(text, f, known)
            if ok:
                f.narration, f.narration_key = text, narration_key(f)
                stats["narrated"] += 1
            else:
                logger.info("insights narration for {} rejected: {}", f.key, reason)
                stats["rejected"] += 1
    return dict(stats)


def _circle_input(c: dict, ctx) -> dict:
    by_id = {e._id: e for e in ctx.entities}
    rel_props = [p for (cls, p) in ctx.entity_props if cls == c["class"] and p.startswith("relationship_to")]
    members = []
    for m in c["members"][:MAX_CIRCLE_MEMBERS]:
        e = by_id.get(m)
        if e is None:
            continue
        info = {"name": e.name}
        if e.type:
            info["type"] = e.type
        for p in rel_props:
            v = ctx.entity_props[(c["class"], p)].get(m)
            if v:
                info["relationship"] = v
        members.append(info)
    days = {d for m in c["members"] if m in by_id for d in by_id[m].days}
    shared = Counter()
    for e in ctx.entities:
        if e.cls in ("ORGANIZATION", "LOCATION") and e._id not in ctx.anchors:
            n = len(days.intersection(e.days))
            if n:
                shared[e.name] = n
    return {"id": c["id"], "members": members, "shared": [n for n, _ in shared.most_common(5)]}


def name_circles(circles: list[dict], *, previous, ctx, llm: LLM) -> dict:
    """Mutates circles in place (name, name_source). Returns counts."""
    stats = Counter({"named": 0, "kept": 0, "rejected": 0, "calls": 0, "errors": 0})
    old = [p for p in (previous or []) if p.get("name_source") == "llm"]
    todo = []
    for c in circles:
        match = max(old, key=lambda p: jaccard(set(p["members"]), set(c["members"])), default=None)
        if match is not None and match.get("class") == c.get("class") and \
                jaccard(set(match["members"]), set(c["members"])) >= KEEP_NAME_JACCARD:
            c["name"], c["name_source"] = match["name"], "llm"
            stats["kept"] += 1
        else:
            todo.append(c)
    if not todo:
        return dict(stats)
    stats["calls"] += 1
    try:
        replies = _parse(llm(circle_prompt([_circle_input(c, ctx) for c in todo])))
    except Exception as exc:
        logger.warning("insights circle naming failed: {}", exc)
        stats["errors"] += 1
        return dict(stats)
    for c in todo:
        name = str(replies.get(c["id"], "") or "").strip().strip('"')
        ok, _reason = check_circle_name(name)
        if ok:
            c["name"], c["name_source"] = name, "llm"
            stats["named"] += 1
        else:
            stats["rejected"] += 1
    return dict(stats)


def label_circle_findings(findings, circles: list[dict]) -> None:
    """Put each circle's name on its circle/share findings (stats.circle_name), matched by members."""
    for f in findings:
        if f.detector != "communities" or "members" not in f.stats:
            continue
        best = max(circles, key=lambda c: jaccard(set(c["members"]), set(f.stats["members"])), default=None)
        if best is not None and jaccard(set(best["members"]), set(f.stats["members"])) >= 0.5:
            f.stats["circle_name"] = best["name"]
```

- [ ] **Step 4: Run the test and see it pass**

Run: `uv run --group dev pytest test/test_insights_narrate.py test/test_insights_prompts.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/narrate.py test/test_insights_narrate.py
git commit -m "feat(insights): narration with caching and post-checks; circle naming" -- artmind/insights/narrate.py test/test_insights_narrate.py
```

---

### Task 3: Runner, CLI and the LLM call type

**Files:**
- Modify: `artmind/extraction.py` (`call_llm`)
- Modify: `artmind/insights/runner.py`
- Modify: `artmind/cli.py` (`insights_refresh`)
- Test: `test/test_insights_runner.py`, `test/test_insights_cli.py`

- [ ] **Step 1: Write the failing tests**

In `test/test_insights_runner.py`, change the `env` fixture's config line to:

```python
    (tmp_path / "cfg" / "demo.yaml").write_text("min_period_docs: 5\nnarrate: false\n")
```

The fixture keeps every existing runner test off the LLM. Check other tests in the file that rewrite `demo.yaml`, such as `test_retention_follows_config`, and add `narrate: false` to those too.

Append:

```python
def _stub_llm(prompt):
    import json as _json
    if "Findings:\n" in prompt:
        items = _json.loads(prompt.split("Findings:\n", 1)[1])
        return _json.dumps({i["key"]: i["title"].split(": ")[-1] + "." for i in items})
    items = _json.loads(prompt.split("Circles:\n", 1)[1])
    return _json.dumps({i["id"]: "Old friends" for i in items})


def test_narration_runs_after_diff_and_is_cached(env):
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\nnarrate_top: 3\n")
    calls = []
    def llm(p):
        calls.append(p)
        return _stub_llm(p)
    result = runner.refresh("demo", projector=FakeProjector(_tables()), now="2026-10-17T09:00:00Z", llm=llm)
    run = store.load_latest("demo")
    assert result["llm"]["calls"] >= 1 and run.llm["narrated"] >= 1
    assert any(c["name_source"] == "llm" and c["name"] == "Old friends" for c in run.circles)
    assert sum(1 for f in run.findings if f.narration) >= 1
    n = len(calls)
    runner.refresh("demo", projector=FakeProjector(_tables()), now="2026-10-17T10:00:00Z", llm=llm, force=True)
    assert len(calls) == n                       # everything cached: no new LLM calls
    assert store.load_latest("demo").llm["cached"] >= 1


def test_no_narrate_skips_llm_and_changes_fingerprint(env):
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\n")
    def boom(p):
        raise AssertionError("LLM must not be called")
    runner.refresh("demo", projector=FakeProjector(_tables()), now="2026-10-17T09:00:00Z", llm=boom, narrate=False)
    assert store.load_latest("demo").fingerprint["narrated"] is False
    second = runner.refresh("demo", projector=FakeProjector(_tables()), now="2026-10-17T10:00:00Z",
                            llm=lambda p: _stub_llm(p))
    assert second["status"] == "written"         # narration turned on: not up to date


def test_llm_failure_never_fails_the_run(env):
    (env / "cfg" / "demo.yaml").write_text("min_period_docs: 5\n")
    def down(p):
        raise RuntimeError("provider down")
    result = runner.refresh("demo", projector=FakeProjector(_tables()), now="2026-10-17T09:00:00Z", llm=down)
    assert result["status"] == "written" and result["llm"]["errors"] >= 1
    assert all(f.narration is None for f in store.load_latest("demo").findings)
```

Append to `test/test_insights_cli.py`:

```python
def test_refresh_no_narrate_flag(monkeypatch):
    from artmind.insights import runner

    seen = {}
    monkeypatch.setattr(runner, "refresh", lambda domain, **kw: seen.update(kw) or {"status": "written"})
    result = invoke("refresh", "--domain", "demo", "--no-narrate")
    assert result.exit_code == 0, result.output and seen["narrate"] is False
```

Update the existing `test_refresh_passes_options_through` expectation to include `"narrate": True`.

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_runner.py test/test_insights_cli.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

`artmind/extraction.py`: change the signature to `def call_llm(model: str, prompt: str, call_type: str = "chat") -> str:` and log with `log_llm_call(call_type, model, prompt, result)`.

`artmind/insights/runner.py`:

1. Change `refresh` and `_refresh` to accept `narrate: bool | None = None, llm=None`, and thread them through.
2. In `_refresh`, compute `do_narrate = config.narrate if narrate is None else narrate`. Add `"narrated": bool(do_narrate)` to the `fingerprint` dict (with the other fields, before the up-to-date check).
3. After the `circles = ...` block and before `run = Run(...)`, insert:

```python
    llm_stats: dict = {}
    if do_narrate:
        from artmind.insights import narrate as narrate_mod
        model = None
        if llm is None:
            try:
                llm, model = narrate_mod.default_llm()
            except Exception as exc:  # no provider configured: run without narration
                llm_stats = {"errors": 1, "error": str(exc)}
        if llm is not None:
            prev_circles = previous.circles if previous is not None else None
            c_stats = narrate_mod.name_circles(circles, previous=prev_circles, ctx=ctx, llm=llm)
            live = [f for f in findings + carried if f.diff != "gone"]
            narrate_mod.label_circle_findings(live, circles)
            f_stats = narrate_mod.narrate_findings(live, previous=previous.findings if previous else None,
                                                   ctx=ctx, llm=llm, top=config.narrate_top)
            llm_stats = {"model": model, "prompt_version": narrate_mod.PROMPT_VERSION,
                         "calls": c_stats["calls"] + f_stats["calls"],
                         "narrated": f_stats["narrated"], "cached": f_stats["cached"],
                         "rejected": f_stats["rejected"] + c_stats["rejected"],
                         "errors": f_stats["errors"] + c_stats["errors"],
                         "circles_named": c_stats["named"], "circles_kept": c_stats["kept"]}
```

   Also import `PROMPT_VERSION` in narrate.py, which re-exports it from prompts, so `narrate_mod.PROMPT_VERSION` works.
4. Pass `llm=llm_stats` to `Run(...)`, and add `"llm": llm_stats` to the returned dict.
5. Carried findings keep their earlier narration, because `replace()` copies it. When they are re-narrated, their key is checked against the cache like any other finding.

`artmind/cli.py` `insights_refresh`:
- add `@click.option("--no-narrate", "no_narrate", is_flag=True, help="Skip the LLM captions and circle names this run")`;
- add the `no_narrate: bool` parameter;
- call `insights_runner.refresh(name, force=force, only=selected, narrate=not no_narrate)`;
- update the docstring to mention narration.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_*.py test/test_cli_guide.py -q`. Expected: all PASS.

Also run `uv run --group dev pytest test/test_extraction*.py -q`, if such files exist, to confirm the `call_llm` signature change breaks nothing.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(insights): narrate after diff, --no-narrate, cached captions and circle names" -- artmind/extraction.py artmind/insights/runner.py artmind/cli.py test/test_insights_runner.py test/test_insights_cli.py
```

---

### Task 4: Docs and end-to-end (controller)

- **Spec §11:**
  - record the budget decision: top `narrate_top` plus up to `narrate_top` new findings, uncached only, batched at 20;
  - the `narrated` fingerprint field;
  - `stats.circle_name` on circle findings;
  - the `--force` hint: after a `--no-narrate` run, a plain refresh re-narrates because the fingerprint differs.
- **CLAUDE.md:** add `narrate`/`prompts` and `--no-narrate` to the insights row.
- **End-to-end on brain01:**
  - run `ARTMIND_NO_PROXY=1 artmind insights refresh --domain personal_journal --force`;
  - check `llm` stats, the circle names and a sample of narrations;
  - re-run to confirm the cache (no LLM calls).
  - Never paste vault content into the repo.
