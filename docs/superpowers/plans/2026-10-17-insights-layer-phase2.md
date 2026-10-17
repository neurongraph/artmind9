# Insights layer — phase 2 (outcome lift, identity, transitions, open loops, more graph health) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven detectors to `artmind insights`:
- corpus: `outcome_lift`, `transitions`, `open_loops`;
- identity: `same_as_candidates`, `split_signal`;
- graph health: `attribution_suspects`, `fragments`.

Also add `artmind insights same-as --export` and `artmind insights config check`.

**Architecture:** Same shape as phase 1. Three new read-only projections load:
- the anchor's dated relations;
- per-entity property values;
- the entity edge list.

The runner puts them on the `RunContext`. Detectors stay pure functions over the context. `split_signal` reads other detectors' findings from `ctx.memo["findings"]`, which the runner fills as detectors run, and same-as candidates from `ctx.memo["same_as"]`.

**Tech Stack:** Python ≥3.14, networkx (pure Python), PyYAML, Click, pytest.

Spec: `docs/superpowers/specs/2026-10-17-insights-layer-design.md` §6.5–§6.8 and §7, phase 2 = §15 item 2. Phase-1 plan for style: `docs/superpowers/plans/2026-10-17-insights-layer-phase1.md`.

---

## Conventions for every task

- Run only the targeted test files with `uv run --group dev pytest <files> -q`. Never run the full suite; it stalls on this Mac.
- Tests are hermetic and use **synthetic data only**. Never copy names or text from a real vault.
- Other agents may commit on this branch at the same time. Commit only your own paths: `git add <new files>` then `git commit -m "<msg>" -- <paths>`. Never use `-a` or `-A`. Retry on `index.lock`.
- End every commit message with the Co-Authored-By trailer your system reminder specifies.
- Branch: `feat/insights-phase2`.

## File map

| File | Change |
|---|---|
| `artmind/insights/stats.py` | + `period_start`, `two_proportion_z`, `levenshtein` |
| `artmind/insights/config.py` | Validate and normalise the `outcome` / `transitions` / `open_loops` blocks; `InsightsConfig.block()`; new detector setting keys; `aliases` setting |
| `artmind/insights/projections.py` | + `anchor_relations`, `entity_property`, `entity_edges` |
| `artmind/insights/profile.py` | `iso_days` made public; `Profile.entity_edges` (not serialised) and `Profile.components` |
| `artmind/insights/detectors/__init__.py` | `RunContext.entity_props`, `RunContext.anchor_relations`; registry extended |
| `artmind/insights/detectors/lift.py` | `outcome_lift` |
| `artmind/insights/detectors/transitions.py` | `transitions` |
| `artmind/insights/detectors/open_loops.py` | `open_loops` |
| `artmind/insights/detectors/same_as.py` | `same_as_candidates` |
| `artmind/insights/detectors/split.py` | `split_signal` |
| `artmind/insights/detectors/health.py` | + `attribution_suspects`, `fragments` |
| `artmind/insights/aliases.yaml` | Kinship / alias name groups |
| `artmind/insights/runner.py` | Load the context extras; fill `memo["findings"]`; `DEPENDS`; `DETECTORS_VERSION = 2` |
| `artmind/insights/config_check.py` | `check_config()` |
| `artmind/cli.py` | `insights same-as`, `insights config check` |
| `test/_insights_helpers.py` | `make_ctx(..., entity_props=, anchor_relations=, entity_edges=)` |
| `pyproject.toml` | Ship `artmind/insights/aliases.yaml` as package data (Task 6) |

---

### Task 1: Stats helpers, config blocks, projections

**Files:**
- Modify: `artmind/insights/stats.py`
- Modify: `artmind/insights/config.py`
- Modify: `artmind/insights/projections.py`
- Test: `test/test_insights_stats.py`, `test/test_insights_config.py`, `test/test_insights_projections.py`

- [ ] **Step 1: Write the failing tests** (append to each file)

`test/test_insights_stats.py`:

```python
def test_period_start():
    assert stats.period_start("2024") == "2024-01-01"
    assert stats.period_start("2024-Q3") == "2024-07-01"
    assert stats.period_start("2024-05") == "2024-05-01"


def test_two_proportion_z():
    z = stats.two_proportion_z(15, 17, 5, 23)
    assert 4.0 < z < 4.4
    assert stats.two_proportion_z(0, 0, 5, 10) is None
    assert stats.two_proportion_z(0, 10, 0, 10) is None   # pooled p = 0


def test_levenshtein():
    assert stats.levenshtein("jonathan", "jonathon") == 1
    assert stats.levenshtein("", "abc") == 3
    assert stats.levenshtein("same", "same") == 0
```

`test/test_insights_config.py`:

```python
def test_outcome_block_is_validated_and_normalised():
    c = parse_config({"outcome": {"class": "EMOTION", "positive": ["Joy", " pride"], "negative": ["Sadness"]}}, "demo")
    assert c.block("outcome") == {"class": "EMOTION", "property": "type", "positive": ["joy", "pride"], "negative": ["sadness"]}
    assert c.block("open_loops") is None


def test_open_loops_and_transitions_blocks():
    c = parse_config({
        "open_loops": {"class": "PLAN", "open": ["Firm intention"], "closed": ["done"]},
        "transitions": {"extra_verbs": ["promoted_to"], "classes": ["ORGANIZATION"]},
    }, "demo")
    assert c.block("open_loops") == {"class": "PLAN", "property": "status", "urgency_property": "urgency",
                                     "open": ["firm intention"], "closed": ["done"]}
    assert c.block("transitions") == {"extra_verbs": ["PROMOTED_TO"], "classes": ["ORGANIZATION"]}


@pytest.mark.parametrize("data, message", [
    ({"outcome": {"positive": ["joy"], "negative": ["sad"]}}, "outcome.class"),
    ({"outcome": {"class": "E", "positive": [], "negative": ["sad"]}}, "outcome.positive"),
    ({"outcome": {"class": "E", "positive": ["joy"], "negative": ["Joy"]}}, "both positive and negative"),
    ({"outcome": {"class": "E", "positive": ["joy"], "negative": ["sad"], "colour": 1}}, "unknown key"),
    ({"open_loops": {"class": "PLAN"}}, "open_loops.open"),
    ({"transitions": {"extra_verbs": "joined"}}, "transitions.extra_verbs"),
    ({"detectors": {"outcome_lift": {"min_z": 0}}}, "min_z"),
    ({"detectors": {"transitions": {"cluster_days": "x"}}}, "cluster_days"),
    ({"detectors": {"same_as_candidates": {"aliases": [["a"]]}}}, "aliases"),
])
def test_phase_two_blocks_are_refused_when_invalid(data, message):
    with pytest.raises(InsightsError, match=message):
        parse_config(data, "demo")


def test_same_as_aliases_setting_is_accepted():
    c = parse_config({"detectors": {"same_as_candidates": {"aliases": [["Nana", "Grandma"]]}}}, "demo")
    assert c.detector("same_as_candidates")["aliases"] == [["Nana", "Grandma"]]
```

Delete the phase-1 test `test_phase_two_blocks_are_kept_verbatim`. It asserted `c.raw` holds unvalidated blocks, which is no longer true.

`test/test_insights_projections.py`:

```python
def test_phase_two_projections_take_their_params():
    rec = Recorder(rows=[])
    p = Projector(["demo"], session_factory=rec.factory())
    p.rows("anchor_relations", anchors=["me"])
    p.rows("entity_property", cls="PLAN", prop="status")
    p.rows("entity_edges")
    params = [call[1] for call in rec.calls]
    assert params == [
        {"anchors": ["me"], "domains": ["demo"]},
        {"cls": "PLAN", "prop": "status", "domains": ["demo"]},
        {"domains": ["demo"]},
    ]
    assert "ASSERTS_RELATION" in PROJECTIONS["anchor_relations"] and "_doc_valid_from" in PROJECTIONS["anchor_relations"]
```

(The existing parametrised test already checks every projection is read-only and `$domains`-scoped.)

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_stats.py test/test_insights_config.py test/test_insights_projections.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/stats.py`. Add:

```python
def period_start(label: str) -> str:
    """'2024' -> '2024-01-01', '2024-Q3' -> '2024-07-01', '2024-05' -> '2024-05-01'."""
    if "-Q" in label:
        year, q = label.split("-Q")
        return f"{year}-{(int(q) - 1) * 3 + 1:02d}-01"
    if len(label) == 7:
        return f"{label}-01"
    return f"{label}-01-01"


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> float | None:
    """z for 'share x1/n1 differs from x2/n2'; None when undefined (an empty side, or a pooled share of 0 or 1)."""
    if n1 <= 0 or n2 <= 0:
        return None
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return None
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return (x1 / n1 - x2 / n2) / se


def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]
```

`artmind/insights/config.py`:

1. Extend the setting-key tuples:

```python
_POS_INT_KEYS = ("min_support", "window", "silence_multiple", "min_silence_days", "min_weight",
                 "min_circle_size", "min_period_edges", "min_count",
                 "cluster_days", "dominance_min_days", "stale_days", "min_targets", "min_size", "max_size", "min_days")
_POS_NUM_KEYS = ("resolution", "min_log_ratio", "min_jaccard",
                 "min_z", "min_score", "max_share", "focus_share", "dominance_share", "max_name_distance",
                 "circle_min_shift")
```

2. In the per-detector settings loop, after the `classes` check, add:

```python
            if key == "aliases" and (not isinstance(value, list) or not all(
                    isinstance(g, list) and len(g) >= 2 and all(isinstance(n, str) and n.strip() for n in g)
                    for g in value)):
                raise InsightsError(f"{where}: detector {name} aliases must be a list of name groups (2+ names each)")
```

3. Replace the `raw` block parsing (the `for key in _RAW_BLOCKS:` loop) with:

```python
    raw = {}
    for key in _RAW_BLOCKS:
        if key in data:
            raw[key] = _BLOCK_PARSERS[key](data[key], f"{where}: {key}")
```

Then add these module-level helpers above `parse_config`:

```python
def _strings(value, where: str, *, required: bool, lower: bool = True) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value) or (required and not value):
        raise InsightsError(f"{where} must be a {'non-empty ' if required else ''}list of strings")
    return [v.strip().casefold() if lower else v.strip() for v in value]


def _mapping(value, where: str, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise InsightsError(f"{where} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise InsightsError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    return value


def _name(value, where: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise InsightsError(f"{where} must be a non-empty string")
    return value.strip()


def _parse_outcome(value, where: str) -> dict:
    v = _mapping(value, where, {"class", "property", "positive", "negative"})
    block = {
        "class": _name(v.get("class"), f"{where}.class"),
        "property": _name(v.get("property"), f"{where}.property", default="type"),
        "positive": _strings(v.get("positive"), f"{where}.positive", required=True),
        "negative": _strings(v.get("negative"), f"{where}.negative", required=True),
    }
    both = set(block["positive"]) & set(block["negative"])
    if both:
        raise InsightsError(f"{where}: {sorted(both)} listed as both positive and negative")
    return block


def _parse_open_loops(value, where: str) -> dict:
    v = _mapping(value, where, {"class", "property", "urgency_property", "open", "closed"})
    return {
        "class": _name(v.get("class"), f"{where}.class"),
        "property": _name(v.get("property"), f"{where}.property", default="status"),
        "urgency_property": _name(v.get("urgency_property"), f"{where}.urgency_property", default="urgency"),
        "open": _strings(v.get("open"), f"{where}.open", required=True),
        "closed": _strings(v.get("closed"), f"{where}.closed", required=False),
    }


def _parse_transitions(value, where: str) -> dict:
    v = _mapping(value, where, {"extra_verbs", "classes"})
    return {
        "extra_verbs": [s.upper() for s in _strings(v.get("extra_verbs"), f"{where}.extra_verbs", required=False, lower=False)],
        "classes": _strings(v.get("classes"), f"{where}.classes", required=False, lower=False),
    }


_BLOCK_PARSERS = {"outcome": _parse_outcome, "open_loops": _parse_open_loops, "transitions": _parse_transitions}
```

4. Add a method to `InsightsConfig`:

```python
    def block(self, name: str) -> dict | None:
        """A validated, normalised phase-2 block (outcome | transitions | open_loops), or None."""
        value = self.raw.get(name)
        return dict(value) if value is not None else None
```

`artmind/insights/projections.py`. Add to `PROJECTIONS`:

```python
    # The anchor's relations at observation level, so each target carries the days it was asserted.
    "anchor_relations": """
MATCH (e:Entity)-[:AGGREGATES]->(o:Observation)-[r:ASSERTS_RELATION]->(:Observation)<-[:AGGREGATES]-(t:Entity)
WHERE e._id IN $anchors AND e._domain IN $domains AND t._domain IN $domains
WITH r.rel_type AS rel, t, collect(DISTINCT o._doc_valid_from) AS days
RETURN rel AS rel, t._id AS _id, t.name AS name, t.entity_class AS cls, t.type AS type, days AS days
ORDER BY rel, _id
""",
    "entity_property": """
MATCH (e:Entity)
WHERE e._domain IN $domains AND e.entity_class = $cls AND e[$prop] IS NOT NULL
RETURN e._id AS _id, CASE WHEN valueType(e[$prop]) STARTS WITH 'LIST' THEN null ELSE toString(e[$prop]) END AS value
ORDER BY _id
""",
    "entity_edges": """
MATCH (a:Entity)-[:RELATES_TO]->(b:Entity)
WHERE a._domain IN $domains AND b._domain IN $domains AND a._id <> b._id
RETURN DISTINCT a._id AS a, b._id AS b
ORDER BY a, b
""",
```

- [ ] **Step 4: Run the tests and see them pass**

Same command as Step 2. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(insights): phase-2 config blocks, projections and stats helpers" -- artmind/insights/stats.py artmind/insights/config.py artmind/insights/projections.py test/test_insights_stats.py test/test_insights_config.py test/test_insights_projections.py
```

---

### Task 2: Profile edges/components, context extras, registry, helper

**Files:**
- Modify: `artmind/insights/profile.py`
- Modify: `artmind/insights/detectors/__init__.py`
- Create stubs: `artmind/insights/detectors/{lift,transitions,open_loops,same_as,split}.py`
- Modify: `artmind/insights/detectors/health.py` (stub detectors at the end)
- Modify: `test/_insights_helpers.py`
- Test: `test/test_insights_profile.py`, `test/test_insights_detectors_base.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_insights_profile.py`:

```python
def test_profile_components_and_edges():
    tables = _tables()
    tables["entity_edges"] = [{"a": "p1", "b": "e1"}, {"a": "e1", "b": "p1"}, {"a": "x1", "b": "x1"}]
    profile, _ = build_profile(FakeProjector(tables), SCHEMA, parse_config({}, "demo"))
    assert profile.entity_edges == [("e1", "p1")]
    assert profile.components == {"count": 2, "largest": 2, "isolated": 1}
    assert "entity_edges" not in profile.to_dict() and profile.to_dict()["components"]["count"] == 2
```

In `test/test_insights_detectors_base.py`, replace the expected registry list with:

```python
    assert names == [
        "salience_trend", "type_trend", "dormancy", "communities", "brokers",
        "outcome_lift", "transitions", "open_loops", "same_as_candidates", "split_signal",
        "kind_mismatch", "vocabulary_sprawl", "relationship_usage", "open_conflicts",
        "attribution_suspects", "fragments",
    ]
```

Append:

```python
def test_context_extras_default_empty():
    ctx = make_ctx([ent("a", "PERSON", ["2022-01-01"])])
    assert ctx.entity_props == {} and ctx.anchor_relations == [] and ctx.profile.entity_edges == []
    ctx2 = make_ctx([ent("a", "PERSON", ["2022-01-01"])], entity_props={("PERSON", "x"): {"a": "1"}},
                    anchor_relations=[{"rel": "R", "_id": "a"}], entity_edges=[("a", "b")])
    assert ctx2.entity_props[("PERSON", "x")] == {"a": "1"} and ctx2.profile.entity_edges == [("a", "b")]
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_profile.py test/test_insights_detectors_base.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/profile.py`:
- Rename `_iso_days` to `iso_days`, and update its one call site.
- Add `import networkx as nx` to the imports.
- Add two fields to `Profile`, after `documents`:

```python
    entity_edges: list = field(default_factory=list)  # sorted (a, b) _id pairs, a < b; never serialised
    components: dict = field(default_factory=dict)    # {"count", "largest", "isolated"}
```

In `to_dict`, add `"components": self.components,`. Do not serialise `entity_edges`.

In `build_profile`, before constructing `Profile`, add:

```python
    edges = sorted({
        (min(r["a"], r["b"]), max(r["a"], r["b"]))
        for r in projector.rows("entity_edges")
        if r.get("a") and r.get("b") and r["a"] != r["b"]
    })
    g = nx.Graph()
    g.add_nodes_from(e._id for e in entities)
    g.add_edges_from(edges)
    sizes = sorted((len(c) for c in nx.connected_components(g)), reverse=True)
    components = {"count": len(sizes), "largest": sizes[0] if sizes else 0, "isolated": sizes.count(1)}
```

Then pass `entity_edges=edges, components=components` to `Profile(...)`.

`artmind/insights/detectors/__init__.py`. Add to `RunContext`, after `memo`:

```python
    entity_props: dict = field(default_factory=dict)       # (cls, prop) -> {_id: value}
    anchor_relations: list = field(default_factory=list)   # {"rel","_id","name","cls","type","days"}
```

Replace `registry()` with:

```python
def registry() -> list[Detector]:
    """Every detector in run order. Readers follow what they read: brokers after communities;
    transitions after communities (circle shifts); split_signal after salience, lift and same-as."""
    from artmind.insights.detectors import (
        brokers, communities, dormancy, health, lift, open_loops, salience, same_as, split, transitions,
    )

    return [
        salience.SALIENCE_TREND, salience.TYPE_TREND, dormancy.DORMANCY,
        communities.COMMUNITIES, brokers.BROKERS,
        lift.OUTCOME_LIFT, transitions.TRANSITIONS, open_loops.OPEN_LOOPS,
        same_as.SAME_AS_CANDIDATES, split.SPLIT_SIGNAL,
        health.KIND_MISMATCH, health.VOCABULARY_SPRAWL, health.RELATIONSHIP_USAGE, health.OPEN_CONFLICTS,
        health.ATTRIBUTION_SUSPECTS, health.FRAGMENTS,
    ]
```

Stubs (Tasks 3–8 replace them). Each module contains:

```python
from artmind.insights.detectors import Detector, Skip

OUTCOME_LIFT = Detector("outcome_lift", "corpus", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

- `lift.py`: `OUTCOME_LIFT` / `"outcome_lift"` / corpus
- `transitions.py`: `TRANSITIONS` / `"transitions"` / corpus
- `open_loops.py`: `OPEN_LOOPS` / `"open_loops"` / corpus
- `same_as.py`: `SAME_AS_CANDIDATES` / `"same_as_candidates"` / health
- `split.py`: `SPLIT_SIGNAL` / `"split_signal"` / health

At the end of `health.py` add:

```python
ATTRIBUTION_SUSPECTS = Detector("attribution_suspects", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
FRAGMENTS = Detector("fragments", "health", lambda ctx: Skip("not implemented"), lambda ctx, t: [])
```

Also import `Skip` in `health.py`: `from artmind.insights.detectors import Detector, RunContext, Skip`.

`test/_insights_helpers.py`, `make_ctx`:
- add the keyword arguments `entity_props=None, anchor_relations=(), entity_edges=()`;
- pass `entity_edges=list(entity_edges)` to `Profile(...)`;
- pass `entity_props=dict(entity_props or {}), anchor_relations=list(anchor_relations)` to `RunContext(...)`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_*.py -q`. Expected: all PASS. The runner tests still pass, because the stubs Skip and carry nothing.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/detectors/lift.py artmind/insights/detectors/transitions.py artmind/insights/detectors/open_loops.py artmind/insights/detectors/same_as.py artmind/insights/detectors/split.py
git commit -m "feat(insights): profile edges and components, context extras, phase-2 registry" -- artmind/insights/profile.py artmind/insights/detectors test/_insights_helpers.py test/test_insights_profile.py test/test_insights_detectors_base.py
```

---

### Task 3: `outcome_lift`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/lift.py`
- Test: `test/test_insights_lift.py`

- [ ] **Step 1: Write the failing test**

```python
from datetime import date, timedelta

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.lift import CAPTION, OUTCOME_LIFT, day_polarity

DAYS = [(date(2023, 1, 1) + timedelta(days=i)).isoformat() for i in range(40)]
JOY, SAD = DAYS[:20], DAYS[20:]
OUTCOME = {"outcome": {"class": "EMOTION", "positive": ["joy"], "negative": ["sadness"]}}


def world():
    ents = [ent(f"j{i}", "EMOTION", [d], type="joy") for i, d in enumerate(JOY)]
    ents += [ent(f"s{i}", "EMOTION", [d], type="Sadness") for i, d in enumerate(SAD)]
    ents.append(ent("w", "PERSON", SAD[:15] + JOY[:2], name="Wren"))
    ents.append(ent("t", "PERSON", JOY[:15] + SAD[:2], name="Tess"))
    ents.append(ent("n", "PERSON", JOY[5:10] + SAD[5:10], name="Nell"))
    ents.append(ent("me", "PERSON", DAYS, name="Me"))
    ents += [ent(f"v{i}", "EVENT", [d], type="clash") for i, d in enumerate(SAD[:12])]
    return ents


def ctx_for(config=OUTCOME, **kw):
    return make_ctx(world(), config=config, anchors={"me"}, docs_per_period={"2023": 100},
                    effective={"EVENT": "occurrent"}, **kw)


def test_day_polarity_majority_and_ties():
    ctx = ctx_for()
    mood = day_polarity(ctx, ctx.config.block("outcome"))
    assert len(mood) == 40 and mood[JOY[0]] == 1 and mood[SAD[0]] == -1


def test_worse_and_better_subjects():
    ctx = ctx_for()
    assert OUTCOME_LIFT.plan(ctx) == ["all"]
    found = {f.key: f for f in OUTCOME_LIFT.run(ctx, "all")}
    worse, better = found["outcome_lift:PERSON:w"], found["outcome_lift:PERSON:t"]
    assert worse.stats["direction"] == "worse" and worse.title == "Worse days with Wren" and worse.stats["z"] > 2
    assert better.stats["direction"] == "better" and better.stats["z"] < -2
    assert worse.stats["baseline_negative_share"] == 0.5 and worse.card["caption"] == CAPTION
    assert "outcome_lift:PERSON:n" not in found          # no lift
    assert "outcome_lift:PERSON:me" not in found         # anchor
    assert not any(k.startswith("outcome_lift:EMOTION") for k in found)   # never the outcome class itself
    assert found["outcome_lift:EVENT:type:clash"].stats["direction"] == "worse"
    assert all(0 < f.score <= 1 for f in found.values())


def test_plan_skips():
    assert OUTCOME_LIFT.plan(ctx_for(config={})).reason == "not configured"
    other = {"outcome": {"class": "MOOD", "positive": ["joy"], "negative": ["sad"]}}
    assert isinstance(OUTCOME_LIFT.plan(ctx_for(config=other)), Skip)
    assert isinstance(OUTCOME_LIFT.plan(ctx_for(default_time_share=0.9)), Skip)


def test_non_type_property_uses_entity_props():
    cfg = {"outcome": {"class": "EMOTION", "property": "valence", "positive": ["good"], "negative": ["bad"]}}
    props = {("EMOTION", "valence"): {**{f"j{i}": "Good" for i in range(20)}, **{f"s{i}": "bad" for i in range(20)}}}
    found = {f.key for f in OUTCOME_LIFT.run(ctx_for(config=cfg, entity_props=props), "all")}
    assert "outcome_lift:PERSON:w" in found
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_lift.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/lift.py`)

```python
"""outcome_lift: entities and occurrent types that co-occur with better or worse days (spec §6.5).

A day's polarity is the majority of the configured outcome class's positive vs
negative values observed that day; ties are neutral and left out. Each subject's
negative share on its polar days is compared with every other polar day by a
two-proportion z-score. Co-occurrence, never cause: every card says so.
"""

from __future__ import annotations

from collections import defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import two_proportion_z

CAPTION = "Co-occurrence on the same day, not cause."


def _values(ctx: RunContext, block: dict) -> dict[str, str]:
    cls, prop = block["class"], block["property"]
    if prop == "type":
        return {e._id: e.type.strip().casefold() for e in ctx.entities if e.cls == cls and e.type and e.type.strip()}
    return {k: str(v).strip().casefold() for k, v in ctx.entity_props.get((cls, prop), {}).items() if v}


def day_polarity(ctx: RunContext, block: dict) -> dict[str, int]:
    """{day: +1 | -1} for days whose outcome values lean one way."""
    positive, negative = set(block["positive"]), set(block["negative"])
    values = _values(ctx, block)
    balance: dict[str, int] = defaultdict(int)
    for e in ctx.entities:
        if e.cls != block["class"] or e._id not in values:
            continue
        sign = 1 if values[e._id] in positive else -1 if values[e._id] in negative else 0
        if sign:
            for d in e.days:
                balance[d] += sign
    return {d: (1 if s > 0 else -1) for d, s in sorted(balance.items()) if s != 0}


def _plan(ctx: RunContext):
    block = ctx.config.block("outcome")
    if block is None:
        return Skip("not configured")
    if block["class"] not in ctx.profile.classes:
        return Skip(f"outcome class {block['class']} is not in the graph")
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    return ["all"]


def _subjects(ctx: RunContext, outcome_cls: str):
    for cls in ctx.classes_of_kind("recurrent"):
        if cls == outcome_cls:
            continue
        for e in ctx.members(cls):
            yield f"{cls}:{e._id}", e.name, [subject(e)], set(e.days), cls
    for cls in ctx.classes_of_kind("occurrent"):
        if cls == outcome_cls:
            continue
        by_type: dict[str, set[str]] = defaultdict(set)
        for e in ctx.entities:
            if e.cls == cls and e.type and e.type.strip() and e._id not in ctx.anchors:
                by_type[e.type.strip().lower()].update(e.days)
        for t in sorted(by_type):
            yield f"{cls}:type:{t}", f"{t} ({cls})", [], by_type[t], cls


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    block = ctx.config.block("outcome")
    cfg = ctx.config.detector("outcome_lift")
    min_support, min_z = cfg.get("min_support", 10), cfg.get("min_z", 2.0)
    mood = day_polarity(ctx, block)
    total = len(mood)
    total_negative = sum(1 for m in mood.values() if m < 0)
    if total == 0:
        return []
    baseline = total_negative / total
    out = []
    for suffix, name, subjects, days, cls in _subjects(ctx, block["class"]):
        hits = [mood[d] for d in days if d in mood]
        n1 = len(hits)
        if n1 < min_support:
            continue
        x1 = sum(1 for m in hits if m < 0)
        z = two_proportion_z(x1, n1, total_negative - x1, total - n1)
        if z is None or abs(z) < min_z:
            continue
        share = x1 / n1
        worse = z > 0
        out.append(Finding(
            key=f"outcome_lift:{suffix}", detector="outcome_lift", kind="corpus",
            score=round(min(1.0, abs(z) / 5), 4),
            title=f"{'Worse' if worse else 'Better'} days with {name}", subjects=subjects,
            stats={"direction": "worse" if worse else "better", "negative_share": round(share, 3),
                   "baseline_negative_share": round(baseline, 3), "lift": round(share - baseline, 3),
                   "z": round(z, 3), "support": n1, "class": cls, "headline": round(share - baseline, 3)},
            card={"type": "pairs", "subject": name, "share": round(share, 3), "baseline": round(baseline, 3),
                  "lift": round(share - baseline, 3), "caption": CAPTION},
        ))
    return out


OUTCOME_LIFT = Detector("outcome_lift", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test and see it pass**

Run: `uv run --group dev pytest test/test_insights_lift.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add test/test_insights_lift.py
git commit -m "feat(insights): outcome_lift detector" -- artmind/insights/detectors/lift.py test/test_insights_lift.py
```

---

### Task 4: `transitions`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/transitions.py`
- Test: `test/test_insights_transitions.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.transitions import TRANSITIONS
from artmind.insights.model import Finding

A_DAYS = tuple(f"2022-{m:02d}-10" for m in range(2, 12))
B_DAYS = tuple(f"2023-{m:02d}-05" for m in range(1, 11)) + tuple(f"2024-{m:02d}-05" for m in range(1, 11))


def rel(rel, _id, name, cls, days, type=None):
    return {"rel": rel, "_id": _id, "name": name, "cls": cls, "type": type, "days": tuple(days)}


def world():
    ents = [ent("me", "PERSON", A_DAYS + B_DAYS, name="Me", type="self"),
            ent("orgA", "ORGANIZATION", A_DAYS, name="Acme"), ent("orgB", "ORGANIZATION", B_DAYS, name="Bolt"),
            ent("act", "ACTIVITY", ["2024-03-03"], name="Pottery")]
    rels = [rel("WORKS_AT", "orgA", "Acme", "ORGANIZATION", A_DAYS),
            rel("WORKS_AT", "orgB", "Bolt", "ORGANIZATION", B_DAYS),
            rel("LEFT", "orgA", "Acme", "ORGANIZATION", ["2022-12-20"]),
            rel("JOINED", "orgB", "Bolt", "ORGANIZATION", ["2023-01-10"]),
            rel("STARTED", "act", "Pottery", "ACTIVITY", ["2024-03-03"])]
    return ents, rels


def ctx_for(**kw):
    ents, rels = world()
    kw.setdefault("anchor_relations", rels)
    return make_ctx(ents, anchors={"me"}, effective={"ACTIVITY": "occurrent"}, **kw)


def test_job_change_is_one_transition_with_two_sources():
    ctx = ctx_for()
    assert TRANSITIONS.plan(ctx) == ["anchor"]
    found = TRANSITIONS.run(ctx, "anchor")
    assert len(found) == 1
    f = found[0]
    assert f.key == "transitions:2022-12-20"
    assert set(f.stats["kinds"]) == {"explicit", "dominance"}
    labels = [e["label"] for e in f.card["events"]]
    assert "left Acme" in labels and "joined Bolt" in labels and "works_at: Acme -> Bolt" in labels
    assert {s["_id"] for s in f.subjects} == {"orgA", "orgB"}
    assert f.score >= 0.6


def test_occurrent_targets_are_not_transitions():
    keys = [e["label"] for f in TRANSITIONS.run(ctx_for(), "anchor") for e in f.card["events"]]
    assert not any("pottery" in k.lower() for k in keys)


def test_circle_shift_from_share_findings():
    ctx = ctx_for(anchor_relations=[])
    ctx.anchor_relations = [rel("WORKS_AT", "orgA", "Acme", "ORGANIZATION", ["2021-05-05"])]
    share = Finding(key="communities:PERSON:share:x", detector="communities", kind="corpus", score=0.5,
                    title="Circle around X: share of mentions",
                    stats={"members": ["x"], "rates": {"2021": 5.0, "2022": 6.0, "2023": 40.0, "2024": 42.0}})
    ctx.memo["findings"] = [share]
    found = TRANSITIONS.run(ctx, "anchor")
    circle = [f for f in found if "circle" in f.stats["kinds"]]
    assert circle and circle[0].stats["start"] == "2023-01-01"


def test_plan_skips_without_anchor_or_relations():
    ents, rels = world()
    assert isinstance(TRANSITIONS.plan(make_ctx(ents, anchor_relations=rels)), Skip)
    assert isinstance(TRANSITIONS.plan(make_ctx(ents, anchors={"me"})), Skip)
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_transitions.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/transitions.py`)

```python
"""transitions: moments when the anchor's life changed (spec §6.6).

Three sources:
- explicit transition verbs on the anchor's relations (joined, left, moved_to, ...);
- a change in the dominant target of one of the anchor's relations between periods;
- a sharp jump in a circle's share of mentions (from communities' share findings).

Events within `cluster_days` of each other form one transition; more
independent sources score higher.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, period_of, period_start

TRANSITION_VERBS = frozenset({
    "JOINED", "LEFT", "QUIT", "STARTED", "RESUMED", "STOPPED", "MOVED_TO", "MOVED_FROM",
    "MARRIED_TO", "DIVORCED", "ENROLLED_IN", "GRADUATED_FROM", "RETIRED_FROM", "PROMOTED_TO",
})


def _plan(ctx: RunContext):
    if not ctx.anchors:
        return Skip("no anchor entity")
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    if not ctx.anchor_relations:
        return Skip("the anchor has no dated relations")
    return ["anchor"]


def _classes(ctx: RunContext) -> set[str]:
    block = ctx.config.block("transitions") or {}
    return set(block.get("classes") or ctx.classes_of_kind("recurrent"))


def _explicit(ctx: RunContext, classes: set[str]) -> list[dict]:
    verbs = TRANSITION_VERBS | set((ctx.config.block("transitions") or {}).get("extra_verbs", []))
    return [
        {"date": r["days"][0], "kind": "explicit", "label": f"{r['rel'].lower()} {r['name']}", "_id": r["_id"]}
        for r in ctx.anchor_relations if r["rel"] in verbs and r["cls"] in classes and r["days"]
    ]


def _dominance(ctx: RunContext, classes: set[str], cfg: dict) -> list[dict]:
    min_days, min_share = cfg.get("dominance_min_days", 3), cfg.get("dominance_share", 0.4)
    period, periods = ctx.config.period, ctx.scored_periods()
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in ctx.anchor_relations:
        if r["cls"] in classes and r["days"] and r["rel"] not in TRANSITION_VERBS:
            by_rel[r["rel"]].append(r)
    events = []
    for rel, rows in sorted(by_rel.items()):
        rows = sorted(rows, key=lambda r: r["_id"])
        dominant = []
        for p in periods:
            counts = {r["_id"]: sum(1 for d in r["days"] if period_of(d, period) == p) for r in rows}
            total = sum(counts.values())
            best = max(counts, key=lambda k: counts[k]) if counts else None  # first max in _id order
            ok = best is not None and counts[best] >= min_days and counts[best] / total >= min_share
            dominant.append((p, best if ok else None))
        names = {r["_id"]: r["name"] for r in rows}
        days_of = {r["_id"]: r["days"] for r in rows}
        for (_p0, a), (p1, b) in zip(dominant, dominant[1:]):
            if a and b and a != b:
                first = min(d for d in days_of[b] if period_of(d, period) == p1)
                events.append({"date": first, "kind": "dominance",
                               "label": f"{rel.lower()}: {names[a]} -> {names[b]}", "_id": b})
    return events


def _circle_shifts(ctx: RunContext, cfg: dict) -> list[dict]:
    min_shift = cfg.get("circle_min_shift", 10)
    events = []
    for f in ctx.memo.get("findings", []):
        if f.detector != "communities" or ":share:" not in f.key:
            continue
        rates = sorted(f.stats.get("rates", {}).items())
        steps = [(p1, b - a) for (_p0, a), (p1, b) in zip(rates, rates[1:])]
        if len(steps) < 2:
            continue
        typical = statistics.median(abs(d) for _, d in steps)
        for p1, d in steps:
            if abs(d) >= min_shift and abs(d) > 2 * typical:
                verb = "rose" if d > 0 else "fell"
                events.append({"date": period_start(p1), "kind": "circle",
                               "label": f"{f.title.split(':')[0]} {verb} by {abs(d):.0f} per 100 documents", "_id": None})
    return events


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("transitions")
    classes = _classes(ctx)
    events = _explicit(ctx, classes) + _dominance(ctx, classes, cfg) + _circle_shifts(ctx, cfg)
    events.sort(key=lambda e: (e["date"], e["kind"], e["label"]))
    groups: list[list[dict]] = []
    for e in events:
        if groups and days_between(groups[-1][-1]["date"], e["date"]) <= cfg.get("cluster_days", 90):
            groups[-1].append(e)
        else:
            groups.append([e])
    targets = {r["_id"]: r for r in ctx.anchor_relations}
    out = []
    for g in groups:
        kinds = sorted({e["kind"] for e in g})
        ids = list(dict.fromkeys(e["_id"] for e in g if e["_id"]))
        out.append(Finding(
            key=f"transitions:{g[0]['date']}", detector="transitions", kind="corpus",
            score=round(min(1.0, 0.3 + 0.3 * (len(kinds) - 1) + 0.05 * (len(g) - 1)), 4),
            title=f"Change around {g[0]['date'][:7]}: " + "; ".join(e["label"] for e in g[:2]),
            subjects=[{"_id": i, "name": targets[i]["name"], "class": targets[i]["cls"]} for i in ids if i in targets],
            stats={"start": g[0]["date"], "end": g[-1]["date"], "kinds": kinds, "events": len(g),
                   "headline": len(kinds)},
            card={"type": "timeline", "events": g},
        ))
    return out


TRANSITIONS = Detector("transitions", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test and see it pass**

Run: `uv run --group dev pytest test/test_insights_transitions.py -q`. Expected: all PASS.

The job-change test relies on these facts:
- In 2022, Acme has 10 days and Bolt none.
- In 2023 and 2024, Bolt dominates.
- The dominance event is on 2023-01-05.
- The cluster starts at LEFT (2022-12-20), and JOINED (2023-01-10) falls within 90 days of it.

- [ ] **Step 5: Commit**

```bash
git add test/test_insights_transitions.py
git commit -m "feat(insights): transitions detector" -- artmind/insights/detectors/transitions.py test/test_insights_transitions.py
```

---

### Task 5: `open_loops`

**Files:**
- Modify (replace stub): `artmind/insights/detectors/open_loops.py`
- Test: `test/test_insights_open_loops.py`

- [ ] **Step 1: Write the failing test**

```python
from _insights_helpers import ent, make_ctx
from artmind.insights.detectors import Skip
from artmind.insights.detectors.open_loops import OPEN_LOOPS

CFG = {"open_loops": {"class": "PLAN", "open": ["firm intention", "tentative"], "closed": ["done"]}}


def ctx_for(config=CFG, props=None):
    ents = [ent("p1", "PLAN", ["2024-01-10"], name="Fix the roof"),
            ent("p2", "PLAN", ["2024-06-20"], name="Call the bank"),
            ent("p3", "PLAN", ["2024-01-05"], name="Paint the fence"),
            ent("p4", "PLAN", ["2024-02-01"], name="Learn guitar"),
            ent("x", "PERSON", ["2024-07-01"])]
    props = props if props is not None else {
        ("PLAN", "status"): {"p1": "Firm intention", "p2": "tentative", "p3": "done", "p4": "maybe later"},
        ("PLAN", "urgency"): {"p1": "High"},
    }
    return make_ctx(ents, config=config, entity_props=props, as_of="2024-07-01")


def test_stale_open_plans_and_unclassified_statuses():
    ctx = ctx_for()
    assert OPEN_LOOPS.plan(ctx) == ["PLAN"]
    found = {f.key: f for f in OPEN_LOOPS.run(ctx, "PLAN")}
    assert set(found) == {"open_loops:PLAN:p1", "open_loops:PLAN:unclassified"}
    f = found["open_loops:PLAN:p1"]
    assert f.title == "Still open: Fix the roof" and f.stats["age_days"] == 173 and f.stats["urgency"] == "high"
    u = found["open_loops:PLAN:unclassified"]
    assert u.kind == "health" and u.stats["top"] == {"maybe later": 1} and u.action == "config"


def test_urgency_weights_the_score():
    props = {("PLAN", "status"): {"p1": "firm intention", "p4": "firm intention"}}
    found = {f.key: f for f in OPEN_LOOPS.run(ctx_for(props=props), "PLAN")}
    high = {("PLAN", "status"): props[("PLAN", "status")], ("PLAN", "urgency"): {"p1": "urgent"}}
    boosted = {f.key: f for f in OPEN_LOOPS.run(ctx_for(props=high), "PLAN")}
    assert boosted["open_loops:PLAN:p1"].score > found["open_loops:PLAN:p1"].score


def test_plan_skips():
    assert OPEN_LOOPS.plan(ctx_for(config={})).reason == "not configured"
    other = {"open_loops": {"class": "TASK", "open": ["open"]}}
    assert isinstance(OPEN_LOOPS.plan(ctx_for(config=other)), Skip)
```

(`p1` last seen 2024-01-10 is 173 days before 2024-07-01, past the 60-day default. `p2` is open but only 11 days old. `p3` is closed. `p4`'s status is neither open nor closed.)

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_open_loops.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement** (replace `artmind/insights/detectors/open_loops.py`)

```python
"""open_loops: intentions with no follow-up (spec §6.7).

An entity of the configured class whose status is an `open` value and whose
last evidence day is more than `stale_days` before as_of. Statuses that are
neither open nor closed are reported once as a health finding: the config, or
the schema hint, is missing values.
"""

from __future__ import annotations

from collections import Counter

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between

URGENT = frozenset({"high", "urgent", "immediate", "critical"})


def _plan(ctx: RunContext):
    block = ctx.config.block("open_loops")
    if block is None:
        return Skip("not configured")
    if block["class"] not in ctx.profile.classes:
        return Skip(f"open_loops class {block['class']} is not in the graph")
    if ctx.as_of is None:
        return Skip("no document dates")
    return [block["class"]]


def _normalised(ctx: RunContext, cls: str, prop: str) -> dict[str, str]:
    return {k: str(v).strip().casefold() for k, v in ctx.entity_props.get((cls, prop), {}).items() if v}


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    block = ctx.config.block("open_loops")
    stale = ctx.config.detector("open_loops").get("stale_days", 60)
    status = _normalised(ctx, cls, block["property"])
    urgency = _normalised(ctx, cls, block["urgency_property"])
    open_values, closed_values = set(block["open"]), set(block["closed"])
    out, unclassified = [], Counter()
    for e in ctx.entities:
        if e.cls != cls or not e.days or e._id not in status:
            continue
        s = status[e._id]
        if s not in open_values:
            if s not in closed_values:
                unclassified[s] += 1
            continue
        age = days_between(e.days[-1], ctx.as_of)
        if age <= stale:
            continue
        weight = 1.0 if urgency.get(e._id) in URGENT else 0.8
        out.append(Finding(
            key=f"open_loops:{cls}:{e._id}", detector="open_loops", kind="corpus",
            score=round(min(1.0, age / (4 * stale)) * weight, 4), title=f"Still open: {e.name}",
            subjects=[subject(e)],
            stats={"status": s, "last_seen": e.days[-1], "age_days": age, "urgency": urgency.get(e._id),
                   "headline": age},
            card={"type": "ranking", "columns": ["status", "last_seen", "age_days"]},
        ))
    if unclassified:
        n = sum(unclassified.values())
        out.append(Finding(
            key=f"open_loops:{cls}:unclassified", detector="open_loops", kind="health",
            score=round(min(1.0, n / 50), 4),
            title=f"{n} {cls}.{block['property']} values are neither open nor closed",
            stats={"count": n, "distinct": len(unclassified),
                   "top": dict(sorted(unclassified.items(), key=lambda kv: (-kv[1], kv[0]))[:20]), "headline": n},
            card={"type": "stat"}, action="config",
        ))
    return out


OPEN_LOOPS = Detector("open_loops", "corpus", _plan, _run)
```

- [ ] **Step 4: Run the test and see it pass**

Run: `uv run --group dev pytest test/test_insights_open_loops.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add test/test_insights_open_loops.py
git commit -m "feat(insights): open_loops detector" -- artmind/insights/detectors/open_loops.py test/test_insights_open_loops.py
```

---

### Task 6: `same_as_candidates`, `split_signal`, kinship aliases

**Files:**
- Create: `artmind/insights/aliases.yaml`
- Modify (replace stubs): `artmind/insights/detectors/same_as.py`, `artmind/insights/detectors/split.py`
- Modify: `pyproject.toml` (package data, if needed; see Step 3)
- Test: `test/test_insights_same_as.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from _insights_helpers import ent, make_ctx
from artmind.insights.detectors.same_as import SAME_AS_CANDIDATES, candidate_pairs, load_alias_groups, name_match, norm
from artmind.insights.detectors.split import SPLIT_SIGNAL
from artmind.insights.model import Finding


def days(month, n, year=2023):
    return [f"{year}-{month:02d}-{d:02d}" for d in range(1, n + 1)]


def test_norm_and_name_rules():
    aliases = load_alias_groups()
    assert norm("  Sam  O'Neil ") == "sam o neil"
    assert name_match("dad", "daddy", aliases, 0.25)[0] == "kinship"
    assert name_match("ab", "asha bose", aliases, 0.25)[0] == "initials"
    assert name_match("sam", "sam rivera", aliases, 0.25)[0] == "token_subset"
    assert name_match("jonathan", "jonathon", aliases, 0.25)[0] == "edit"
    assert name_match("ben", "ken", aliases, 0.25) is None          # too short for edit distance
    assert name_match("sam rivera", "sam okoro", aliases, 0.25) is None


WORDS = iter(["Oak", "Elm", "Fir", "Yew", "Bay", "Ivy", "Rue", "Cob", "Hop", "Kit", "Lux", "Mug",
              "Nib", "Orb", "Pod", "Rib", "Tux", "Urn", "Vim", "Wax", "Zip", "Gum"])


def labelled_world():
    """Synthetic labelled pairs. Duplicates share a circle of neighbours and are never mentioned on the same day.
    Namesakes have disjoint circles, meet on two separate days, and disagree on type. Neighbour names are
    distinct 3-letter words, so they never name-match anything."""
    ents, positives, negatives = [], [], []

    def circle(month):
        return [ent(f"c{month}_{i}", "PERSON", days(month, 6), name=next(WORDS)) for i in range(3)]

    def same(a, b, name_a, name_b, month):
        ents.extend(circle(month))
        ents.append(ent(a, "PERSON", days(month, 6)[:3], name=name_a, type="parent"))
        ents.append(ent(b, "PERSON", days(month, 6)[3:], name=name_b, type="parent"))
        positives.append(tuple(sorted((a, b))))

    def namesakes(a, b, name_a, name_b, month):
        ents.extend(circle(month) + circle(month + 1))
        meet = days(12, 2)
        ents.append(ent(a, "PERSON", days(month, 4) + meet, name=name_a, type="friend"))
        ents.append(ent(b, "PERSON", days(month + 1, 4) + meet, name=name_b, type="colleague"))
        negatives.append(tuple(sorted((a, b))))

    same("dad", "daddy", "Dad", "Daddy", 1)
    same("ab", "asha", "AB", "Asha Bose", 3)
    same("jon1", "jon2", "Jonathan", "Jonathon", 5)
    namesakes("sam", "samo", "Sam", "Sam Okoro", 7)
    namesakes("lee", "leep", "Lee", "Lee Park", 9)
    return ents, positives, negatives


def test_labelled_fixture_separates_duplicates_from_namesakes():
    ents, positives, negatives = labelled_world()
    ctx = make_ctx(ents, docs_per_period={"2023": 100})
    found = SAME_AS_CANDIDATES.run(ctx, "PERSON")
    scored = {tuple(sorted((f.stats["canonical"]["_id"], f.stats["alias"]["_id"]))): f.score for f in found}
    for p in positives:
        assert scored.get(p, 0) >= 0.5, (p, scored.get(p))
    for n in negatives:
        assert scored.get(n, 0) < 0.5, (n, scored.get(n))
    assert {tuple(sorted((c["canonical"], c["alias"]))) for c in ctx.memo["same_as"]} == set(positives)
    f = next(f for f in found if f.stats["rule"] == "kinship")
    assert f.kind == "health" and f.action == "curate" and f.title.startswith("Possibly the same PERSON:")


def test_config_aliases_extend_the_package_list():
    ents = [ent("n1", "PERSON", days(1, 4), name="Nonno"), ent("g1", "PERSON", days(1, 8)[4:], name="Bapu")]
    plain = make_ctx(ents)
    assert not candidate_pairs(plain.entities, load_alias_groups(plain.config), 0.25)
    ctx = make_ctx(ents, config={"detectors": {"same_as_candidates": {"aliases": [["Nonno", "Bapu"]]}}})
    assert any(p[2] == "kinship" for p in candidate_pairs(ctx.entities, load_alias_groups(ctx.config), 0.25))


def test_split_signal_flags_opposite_trends():
    a = ent("t1", "LOCATION", days(1, 5), name="Tech Park One")
    b = ent("t2", "LOCATION", days(2, 5), name="TPO")
    ctx = make_ctx([a, b])
    ctx.memo["same_as"] = [{"class": "LOCATION", "canonical": "t1", "alias": "t2", "score": 0.7}]
    def trend(e, direction):
        return Finding(key=f"salience_trend:LOCATION:{e._id}", detector="salience_trend", kind="corpus", score=0.5,
                       title="x", subjects=[{"_id": e._id, "name": e.name, "class": "LOCATION"}],
                       stats={"direction": direction})
    ctx.memo["findings"] = [trend(a, "fading"), trend(b, "rising")]
    assert SPLIT_SIGNAL.plan(ctx) == ["all"]
    (f,) = SPLIT_SIGNAL.run(ctx, "all")
    assert f.key == "split_signal:LOCATION:t1:t2:salience_trend" and f.action == "curate"
    assert f.stats["directions"] == {"t1": "fading", "t2": "rising"}


def test_split_signal_skips_without_candidates():
    assert SPLIT_SIGNAL.plan(make_ctx([ent("a", "PERSON", days(1, 4))])).reason == "no same-as candidates this run"
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run --group dev pytest test/test_insights_same_as.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/aliases.yaml`:

```yaml
# Kinship and common alias names that denote the same person in personal notes.
# Each group is a set of interchangeable names (case-insensitive). Extend per
# domain in domains/insights/<domain>.yaml under detectors.same_as_candidates.aliases.
- [dad, daddy, papa, father, pa, baba, pop]
- [mom, mum, mummy, mommy, mother, ma, mama, maa]
- [grandma, granny, nana, grandmother]
- [grandpa, grandad, granddad, grandfather]
- [brother, bro]
- [sister, sis]
```

`artmind/insights/detectors/same_as.py`:

```python
"""same_as_candidates: probable duplicate entities within one class (spec §6.8).

Candidates come from names:
- kinship/alias groups;
- initials;
- a token subset;
- a small edit distance.

Each candidate is scored by a fixed logistic over:
- neighbourhood similarity in the co-mention graph;
- how often the pair is co-mentioned (duplicates rarely are);
- time-span overlap;
- agreement of `type` / `relationship_to_*`.

Nothing merges: findings feed `artmind insights same-as --export` and the curation review queue.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from pathlib import Path

import yaml

from artmind.insights import graphs
from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, levenshtein

ALIASES_FILE = Path(__file__).resolve().parent.parent / "aliases.yaml"
_PUNCT = re.compile(r"[^\w\s]")
MAX_TOKEN_BUCKET = 50   # a token shared by more entities than this ("office") is not a signal
MIN_EDIT_LEN = 4


def norm(name) -> str:
    return " ".join(_PUNCT.sub(" ", str(name).casefold()).split())


def load_alias_groups(config=None) -> list[frozenset[str]]:
    groups = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) or []
    if config is not None:
        groups = groups + (config.detector("same_as_candidates").get("aliases") or [])
    return [frozenset(norm(n) for n in g) for g in groups]


def _initials(n: str) -> str:
    tokens = n.split()
    return "".join(t[0] for t in tokens) if len(tokens) >= 2 else ""


def name_match(a: str, b: str, aliases: list[frozenset[str]], max_dist: float) -> tuple[str, float] | None:
    """(rule, strength) when two normalised names plausibly denote one thing."""
    if a == b:
        return ("exact", 1.0)
    if any(a in g and b in g for g in aliases):
        return ("kinship", 1.0)
    ta, tb = set(a.split()), set(b.split())
    for short, long_ in ((a, b), (b, a)):
        if " " not in short and 2 <= len(short) <= 4 and short == _initials(long_):
            return ("initials", 0.9)
    if ta < tb or tb < ta:
        return ("token_subset", 0.8)
    if min(len(a), len(b)) >= MIN_EDIT_LEN and a[0] == b[0]:
        d = levenshtein(a, b) / max(len(a), len(b))
        if d <= max_dist:
            return ("edit", round(0.7 * (1 - d / max_dist) + 0.2, 3))
    return None


def candidate_pairs(rows, aliases, max_dist) -> list[tuple[str, str, str, float]]:
    """[(id_a, id_b, rule, strength)] with id_a < id_b, from blocked comparisons only."""
    named = sorted(((e._id, norm(e.name)) for e in rows if norm(e.name)), key=lambda x: x[0])
    names = dict(named)
    buckets: list[list[str]] = []
    by_token, by_first, by_initials, by_alias = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for _id, n in named:
        for t in set(n.split()):
            by_token[t].append(_id)
        by_first[n[0]].append(_id)
        if _initials(n):
            by_initials[_initials(n)].append(_id)
        for i, g in enumerate(aliases):
            if n in g:
                by_alias[i].append(_id)
    buckets += [b for b in by_token.values() if len(b) <= MAX_TOKEN_BUCKET]
    buckets += list(by_alias.values())
    buckets += [b for b in by_first.values()]
    pairs: set[tuple[str, str]] = set()
    for b in buckets:
        pairs.update(tuple(sorted(p)) for p in itertools.combinations(b, 2))
    for _id, n in named:
        if " " not in n and n in by_initials:
            pairs.update(tuple(sorted((_id, other))) for other in by_initials[n] if other != _id)
    out = []
    for a, b in sorted(pairs):
        match = name_match(names[a], names[b], aliases, max_dist)
        if match:
            out.append((a, b, *match))
    return out


def _span_overlap(da: tuple[str, ...], db: tuple[str, ...]) -> float:
    start, end = max(da[0], db[0]), min(da[-1], db[-1])
    if end < start:
        return 0.0
    shorter = min(days_between(da[0], da[-1]), days_between(db[0], db[-1]))
    return 1.0 if shorter == 0 else min(1.0, days_between(start, end) / shorter)


def _plan(ctx: RunContext):
    classes = [c for c in ctx.classes_of_kind("recurrent") if len(ctx.members(c, floor=1)) >= 2]
    return classes or Skip("no recurrent class with two dated entities")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("same_as_candidates")
    min_score, max_dist = cfg.get("min_score", 0.5), cfg.get("max_name_distance", 0.25)
    rows = ctx.members(cls, floor=cfg.get("min_days", 1))
    by_id = {e._id: e for e in rows}
    pairs = candidate_pairs(rows, load_alias_groups(ctx.config), max_dist)
    if not pairs:
        return []
    try:
        g = graphs.co_mention_graph(rows, min_weight=1)
    except graphs.GraphTooLarge:
        g = None
    props = [p for p in sorted(ctx.profile.classes[cls].properties) if p.startswith("relationship_to")]
    out = []
    for a, b, rule, strength in pairs:
        ea, eb = by_id[a], by_id[b]
        nb = graphs.jaccard(set(g[a]) - {b}, set(g[b]) - {a}) if g is not None and a in g and b in g else 0.0
        co = len(set(ea.days) & set(eb.days))
        overlap = _span_overlap(ea.days, eb.days)
        agree = disagree = 0
        pairs_to_compare = [(ea.type, eb.type)] + [
            (ctx.entity_props.get((cls, p), {}).get(a), ctx.entity_props.get((cls, p), {}).get(b)) for p in props
        ]
        for va, vb in pairs_to_compare:
            if va and vb:
                if norm(va) == norm(vb):
                    agree += 1
                else:
                    disagree += 1
        z = (-2.0 + 2.5 * strength + 2.0 * nb - 1.5 * min(1.0, co / 2) + 0.5 * overlap
             + 0.6 * min(agree, 2) - 0.8 * min(disagree, 2))
        score = round(1 / (1 + math.exp(-z)), 4)
        if score < min_score:
            continue
        canonical, alias = sorted((ea, eb), key=lambda e: (-len(e.days), -len(e.name), e._id))
        ctx.memo.setdefault("same_as", []).append(
            {"class": cls, "canonical": canonical._id, "alias": alias._id, "score": score})
        out.append(Finding(
            key=f"same_as_candidates:{cls}:{a}:{b}", detector="same_as_candidates", kind="health", score=score,
            title=f"Possibly the same {cls}: {alias.name} / {canonical.name}",
            subjects=[subject(canonical), subject(alias)],
            stats={"canonical": subject(canonical), "alias": subject(alias), "rule": rule,
                   "name_strength": strength, "neighbour_jaccard": round(nb, 3), "co_mentions": co,
                   "time_overlap": round(overlap, 3), "agree": agree, "disagree": disagree, "headline": score},
            card={"type": "stat"}, action="curate",
        ))
    return out


SAME_AS_CANDIDATES = Detector("same_as_candidates", "health", _plan, _run)
```

`artmind/insights/detectors/split.py`:

```python
"""split_signal: a probable duplicate whose halves show opposite findings (spec §7).

When one real thing is extracted under two names, its signal splits. Then:
- one half "fades" while the other "rises";
- one half goes with better days while the other goes with worse ones.
Such pairs are the highest-priority curation.
"""

from __future__ import annotations

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding

SIGNALS = ("salience_trend", "outcome_lift")


def _plan(ctx: RunContext):
    return ["all"] if ctx.memo.get("same_as") else Skip("no same-as candidates this run")


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    by_subject = {
        (f.detector, f.subjects[0]["_id"]): f
        for f in ctx.memo.get("findings", []) if f.detector in SIGNALS and f.subjects
    }
    entities = {e._id: e for e in ctx.entities}
    out = []
    for c in ctx.memo.get("same_as", []):
        a, b = c["canonical"], c["alias"]
        if a not in entities or b not in entities:
            continue
        for signal in SIGNALS:
            fa, fb = by_subject.get((signal, a)), by_subject.get((signal, b))
            if not (fa and fb) or fa.stats.get("direction") == fb.stats.get("direction"):
                continue
            lo, hi = sorted((a, b))
            out.append(Finding(
                key=f"split_signal:{c['class']}:{lo}:{hi}:{signal}", detector="split_signal", kind="health",
                score=round(min(1.0, c["score"] * 1.2), 4),
                title=f"{entities[a].name} and {entities[b].name} look like one {c['class']} "
                      f"but show opposite {signal.replace('_', ' ')}",
                subjects=[subject(entities[a]), subject(entities[b])],
                stats={"canonical": a, "alias": b, "same_as_score": c["score"], "signal": signal,
                       "directions": {a: fa.stats["direction"], b: fb.stats["direction"]}, "headline": c["score"]},
                card={"type": "stat"}, action="curate",
            ))
    return out


SPLIT_SIGNAL = Detector("split_signal", "health", _plan, _run)
```

**Package data:** `aliases.yaml` must ship in the wheel.
- In `pyproject.toml` `[tool.setuptools.package-data]`, append `"insights/aliases.yaml"` to the `artmind = [...]` list.
- Then run `uv run python -c "from artmind.insights.detectors.same_as import load_alias_groups; print(len(load_alias_groups()))"`. Expected: `6`.

- [ ] **Step 4: Run the test and see it pass**

Run: `uv run --group dev pytest test/test_insights_same_as.py -q`. Expected: all PASS.

The labelled fixture is how the logistic weights are pinned. If a positive or negative lands on the wrong side of 0.5, report the per-pair signals (strength, neighbour Jaccard, co-mentions, overlap, agree/disagree). Do **not** loosen the test or retune the weights silently.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/aliases.yaml test/test_insights_same_as.py
git commit -m "feat(insights): same_as_candidates and split_signal" -- artmind/insights/aliases.yaml artmind/insights/detectors/same_as.py artmind/insights/detectors/split.py test/test_insights_same_as.py pyproject.toml
```

---

### Task 7: `attribution_suspects` and `fragments`

**Files:**
- Modify: `artmind/insights/detectors/health.py` (replace the two stubs)
- Test: `test/test_insights_health.py` (append)

- [ ] **Step 1: Write the failing tests** (append)

```python
from artmind.insights.detectors.health import ATTRIBUTION_SUSPECTS, FRAGMENTS


def _rel(i, cls, type):
    return {"rel": "WORKS_AT", "_id": f"t{i}", "name": f"T{i}", "cls": cls, "type": type, "days": ("2024-01-01",)}


def test_attribution_suspects_flags_rare_target_types_on_focused_relations():
    rels = [_rel(i, "ORGANIZATION", "employer") for i in range(15)]
    rels += [_rel(15 + i, "LOCATION", "workplace") for i in range(10)]
    rels += [_rel(40, "ORGANIZATION", "school")]
    rels += [{"rel": "VISITS", "_id": f"v{i}", "name": f"V{i}", "cls": "LOCATION", "type": f"kind{i}", "days": ("2024-01-01",)}
             for i in range(30)]   # unfocused relation: no suspects
    ctx = make_ctx([ent("me", "PERSON", ["2024-01-01"])], anchors={"me"}, anchor_relations=rels)
    assert ATTRIBUTION_SUSPECTS.plan(ctx) == ["anchor"]
    (f,) = ATTRIBUTION_SUSPECTS.run(ctx, "anchor")
    assert f.key == "attribution_suspects:WORKS_AT" and f.action == "reextract" and f.kind == "health"
    assert [s["_id"] for s in f.stats["suspects"]] == ["t40"]
    assert f.stats["typical"][0] == {"class": "ORGANIZATION", "type": "employer", "count": 15}


def test_fragments_reports_small_cut_off_groups():
    main = [("m0", f"m{i}") for i in range(1, 8)]
    islands = [("i1", "i2"), ("j1", "j2"), ("j2", "j3")]
    ents = [ent(x, "PERSON", ["2024-01-01"]) for x in {n for e in main + islands for n in e}]
    ctx = make_ctx(ents, entity_edges=sorted(main + islands))
    (f,) = FRAGMENTS.run(ctx, "all")
    assert f.key == "fragments:all" and f.stats["count"] == 2 and f.stats["main_component"] == 8
    assert f.stats["sizes"] == {"2": 1, "3": 1}
    assert FRAGMENTS.plan(make_ctx(ents)).reason == "no entity relations"
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_health.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement.** In `health.py`, add `from collections import Counter, defaultdict` and `import networkx as nx`. Then replace the two stubs with:

```python
# ── attribution_suspects ──────────────────────────────────────────────────────

def _plan_attribution(ctx: RunContext):
    return ["anchor"] if ctx.anchor_relations else Skip("no anchor relations")


def _run_attribution(ctx: RunContext, _target: str) -> list[Finding]:
    """On a focused anchor relation (its top-3 target (class, type) combos cover >= focus_share),
    targets whose (class, type) is rare (< max_share) were probably attributed to the wrong subject."""
    cfg = ctx.config.detector("attribution_suspects")
    min_targets, max_share, focus = cfg.get("min_targets", 20), cfg.get("max_share", 0.05), cfg.get("focus_share", 0.8)
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in ctx.anchor_relations:
        by_rel[r["rel"]].append(r)
    out = []
    for rel, rows in sorted(by_rel.items()):
        if len(rows) < min_targets:
            continue
        combo = {r["_id"]: (r["cls"], (r["type"] or "").strip().lower() or "(none)") for r in rows}
        counts = Counter(combo[r["_id"]] for r in sorted(rows, key=lambda r: r["_id"]))
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        n = len(rows)
        if sum(c for _, c in ranked[:3]) / n < focus:
            continue
        rare = {k for k, c in counts.items() if c / n < max_share}
        suspects = sorted((r for r in rows if combo[r["_id"]] in rare),
                          key=lambda r: (r["cls"], str(r["name"]), r["_id"]))
        if not suspects:
            continue
        out.append(Finding(
            key=f"attribution_suspects:{rel}", detector="attribution_suspects", kind="health",
            score=round(min(1.0, 5 * len(suspects) / n), 4),
            title=f"{len(suspects)} {rel} targets look misattributed",
            subjects=[{"_id": r["_id"], "name": r["name"], "class": r["cls"]} for r in suspects[:10]],
            stats={"targets": n,
                   "suspects": [{"_id": r["_id"], "name": r["name"], "class": r["cls"], "type": r["type"]}
                                for r in suspects[:20]],
                   "typical": [{"class": c, "type": t, "count": k} for (c, t), k in ranked[:3]],
                   "headline": len(suspects)},
            card={"type": "ranking"}, action="reextract",
        ))
    return out


# ── fragments ─────────────────────────────────────────────────────────────────

def _plan_fragments(ctx: RunContext):
    return ["all"] if ctx.profile.entity_edges else Skip("no entity relations")


def _run_fragments(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("fragments")
    lo, hi = cfg.get("min_size", 2), cfg.get("max_size", 5)
    g = nx.Graph()
    g.add_edges_from(ctx.profile.entity_edges)
    comps = sorted((sorted(c) for c in nx.connected_components(g)), key=lambda c: (-len(c), c[0]))
    small = [c for c in comps[1:] if lo <= len(c) <= hi]
    if not small:
        return []
    entities = {e._id: e for e in ctx.entities}
    def describe(i):
        e = entities.get(i)
        return {"_id": i, "name": e.name if e else i, "class": e.cls if e else None}
    return [Finding(
        key="fragments:all", detector="fragments", kind="health", score=round(min(1.0, len(small) / 50), 4),
        title=f"{len(small)} small groups of entities are cut off from the main graph",
        stats={"count": len(small), "main_component": len(comps[0]),
               "sizes": {str(k): v for k, v in sorted(Counter(len(c) for c in small).items())},
               "examples": [[describe(i) for i in c] for c in small[:20]], "headline": len(small)},
        card={"type": "ranking"}, action="curate",
    )]


ATTRIBUTION_SUSPECTS = Detector("attribution_suspects", "health", _plan_attribution, _run_attribution)
FRAGMENTS = Detector("fragments", "health", _plan_fragments, _run_fragments)
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_health.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(insights): attribution_suspects and fragments" -- artmind/insights/detectors/health.py test/test_insights_health.py
```

---

### Task 8: Runner wiring

**Files:**
- Modify: `artmind/insights/runner.py`
- Test: `test/test_insights_runner.py` (append)

- [ ] **Step 1: Write the failing tests** (append; `_tables`, `env`, `refresh` and `_refresh_tables` are already in the file)

```python
def test_context_extras_are_loaded_and_findings_shared(env, monkeypatch):
    from artmind.insights import runner as r

    seen = {}
    real = r.run_detectors
    def spy(ctx, selected):
        out = real(ctx, selected)
        seen["ctx"] = ctx
        return out
    monkeypatch.setattr(r, "run_detectors", spy)
    tables = _tables()
    tables["anchor_relations"] = [{"rel": "WORKS_AT", "_id": "a1", "name": "A1", "cls": "PERSON", "type": None,
                                   "days": ["2023-01-01", "bad"]}]
    _refresh_tables(tables, force=True)
    ctx = seen["ctx"]
    assert ctx.anchor_relations == [{"rel": "WORKS_AT", "_id": "a1", "name": "A1", "cls": "PERSON", "type": None,
                                     "days": ("2023-01-01",)}]
    assert {f.detector for f in ctx.memo["findings"]} >= {"communities", "brokers"}


def test_dependencies_for_phase_two_detectors():
    from artmind.insights.runner import DEPENDS, DETECTORS_VERSION
    assert DEPENDS["split_signal"] == {"same_as_candidates", "salience_trend", "outcome_lift"}
    assert DEPENDS["transitions"] == {"communities"}
    assert DETECTORS_VERSION == 2


def test_entity_props_loaded_for_configured_blocks(env):
    from artmind.insights.runner import load_entity_props
    from artmind.insights.config import parse_config
    from artmind.insights.profile import build_profile
    tables = _tables()
    tables["property_stats"] = [
        {"cls": "PERSON", "property": "relationship_to_diarist", "filled": 3, "distinct_values": 2, "sample": []},
        {"cls": "PERSON", "property": "mood", "filled": 3, "distinct_values": 2, "sample": []},
    ]
    tables["entity_property"] = [{"_id": "a1", "value": "friend"}]
    proj = FakeProjector(tables)
    cfg = parse_config({"outcome": {"class": "PERSON", "property": "mood", "positive": ["up"], "negative": ["down"]}}, "demo")
    profile, _ = build_profile(proj, SCHEMA, cfg)
    props = load_entity_props(proj, cfg, profile)
    assert set(props) == {("PERSON", "mood"), ("PERSON", "relationship_to_diarist")}
    assert ("entity_property", {"cls": "PERSON", "prop": "mood"}) in proj.calls
```

(If `_refresh_tables` doesn't accept `force`, use the file's existing helper signature. It was added in phase 1 and accepts `**kw` for `refresh`.)

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_runner.py -q`. Expected: the new tests FAIL.

- [ ] **Step 3: Implement** in `artmind/insights/runner.py`

1. Imports: add `iso_days` to the profile import: `from artmind.insights.profile import build_profile, infer_anchors, iso_days`.
2. Constants:

```python
DETECTORS_VERSION = 2
DEPENDS = {
    "brokers": {"communities"},
    "transitions": {"communities"},
    "split_signal": {"same_as_candidates", "salience_trend", "outcome_lift"},
}
```

3. In `run_detectors`, after `findings.extend(found)`, add `ctx.memo.setdefault("findings", []).extend(found)`.

4. Add these loaders:

```python
def load_entity_props(projector, config, profile) -> dict:
    """Per-entity values for the properties the configured detectors read:
    - the outcome property (unless `type`);
    - the open_loops status and urgency;
    - relationship_to_* on recurrent classes, for same-as agreement.
    Only properties the profile has seen are loaded."""
    wanted: set[tuple[str, str]] = set()
    outcome = config.block("outcome")
    if outcome and outcome["property"] != "type":
        wanted.add((outcome["class"], outcome["property"]))
    loops = config.block("open_loops")
    if loops:
        wanted |= {(loops["class"], loops["property"]), (loops["class"], loops["urgency_property"])}
    for cls, cp in profile.classes.items():
        if cp.effective_kind == "recurrent":
            wanted |= {(cls, p) for p in cp.properties if p.startswith("relationship_to")}
    out = {}
    for cls, prop in sorted(wanted):
        if cls in profile.classes and prop in profile.classes[cls].properties:
            out[(cls, prop)] = {r["_id"]: r["value"] for r in projector.rows("entity_property", cls=cls, prop=prop)
                                if r.get("_id") and r.get("value") is not None}
    return out


def load_anchor_relations(projector, anchors: list[dict]) -> list[dict]:
    if not anchors:
        return []
    out = []
    for r in projector.rows("anchor_relations", anchors=sorted(a["_id"] for a in anchors)):
        if not r.get("rel") or not r.get("_id"):
            continue
        out.append({"rel": str(r["rel"]), "_id": r["_id"], "name": r.get("name") or r["_id"],
                    "cls": r.get("cls") or "UNKNOWN", "type": r.get("type") if isinstance(r.get("type"), str) else None,
                    "days": iso_days(r.get("days"))})
    return out
```

5. In `_refresh`:
   - Move `anchors = infer_anchors(...)` inside the existing `try` that wraps `build_profile`, after it.
   - Also load the extras there:

```python
        anchors = infer_anchors(entities, config, profile.dated_days) if entities else []
        entity_props = load_entity_props(projector, config, profile)
        anchor_relations = load_anchor_relations(projector, anchors)
```

   - Keep the `if not entities: raise InsightsError(...)` check after the try.
   - Pass `entity_props=entity_props, anchor_relations=anchor_relations` to `RunContext(...)`.

- [ ] **Step 4: Run all insights tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_*.py -q`. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(insights): runner loads anchor relations and entity properties, shares findings" -- artmind/insights/runner.py test/test_insights_runner.py
```

---

### Task 9: `insights same-as` and `insights config check`

**Files:**
- Create: `artmind/insights/config_check.py`
- Modify: `artmind/cli.py` (the insights group, after `insights_runs`)
- Test: `test/test_insights_config_check.py`, `test/test_insights_cli.py` (append)

- [ ] **Step 1: Write the failing tests**

`test/test_insights_config_check.py`:

```python
import pytest

import paths
from _insights_helpers import FakeProjector
from artmind.insights import config_check

TABLES = {
    "entity_evidence": [
        {"_id": "e1", "name": "E1", "cls": "EMOTION", "type": "joy", "days": ["2024-01-01"], "docs": 1},
        {"_id": "p1", "name": "P1", "cls": "PLAN", "type": None, "days": ["2024-01-01"], "docs": 1},
    ],
    "documents": [{"id": "d", "valid_from": "2024-01-01", "time_source": "document_label"}],
    "property_stats": [{"cls": "PLAN", "property": "status", "filled": 1, "distinct_values": 1, "sample": ["open"]}],
    "entity_property": [{"_id": "p1", "value": "open"}],
}


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_INSIGHTS_DIR", tmp_path)
    monkeypatch.setattr(config_check, "load_schema", lambda d: {})
    return tmp_path


def test_valid_config_is_ok_with_warnings(cfg_dir):
    (cfg_dir / "demo.yaml").write_text(
        "outcome: {class: EMOTION, positive: [joy], negative: [sadness]}\n"
        "open_loops: {class: PLAN, open: [open], closed: [done]}\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is True and r["errors"] == []
    assert any("sadness" in w for w in r["warnings"]) and any("'done'" in w for w in r["warnings"])


def test_missing_classes_and_properties_are_errors(cfg_dir):
    (cfg_dir / "demo.yaml").write_text(
        "outcome: {class: MOOD, positive: [up], negative: [down]}\n"
        "open_loops: {class: PLAN, property: state, open: [open]}\n"
        "detectors: {communities: {classes: [TEAM]}}\nanchors: [Nobody]\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is False
    text = " ".join(r["errors"])
    for needle in ("MOOD", "PLAN.state", "TEAM", "Nobody"):
        assert needle in text


def test_invalid_config_is_reported_not_raised(cfg_dir):
    (cfg_dir / "demo.yaml").write_text("period: week\n")
    r = config_check.check_config("demo", projector=FakeProjector(TABLES))
    assert r["ok"] is False and "period" in r["errors"][0]
```

Append to `test/test_insights_cli.py`:

```python
def test_same_as_export_writes_refine_graph_input(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INSIGHTS_DIR", tmp_path / "insights")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    def cand(a, b, score):
        return Finding(key=f"same_as_candidates:PERSON:{a}:{b}", detector="same_as_candidates", kind="health",
                       score=score, title="t", action="curate",
                       stats={"canonical": {"_id": a, "name": a.title(), "class": "PERSON"},
                              "alias": {"_id": b, "name": b.title(), "class": "PERSON"}, "rule": "kinship"})
    run = Run(domain="demo", created_at="2026-10-17T09:00:00Z", as_of="2026-10-01", fingerprint={}, anchors=[],
              profile={}, plan=[], circles=[], diff={}, findings=[cand("dad", "daddy", 0.9), cand("sam", "sam r", 0.4)])
    store.write_run(run)
    out = tmp_path / "merges.json"
    result = invoke("same-as", "--domain", "demo", "--export", str(out))
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["proposed_merges"] == {"Daddy": "Dad"} and data["source"] == "artmind insights"
    assert "refine-graph --domain demo --from-file" in json.loads(result.output)["next"]


def test_config_check_exit_code(monkeypatch):
    from artmind.insights import config_check
    monkeypatch.setattr(config_check, "check_config", lambda d, projector=None: {"domain": d, "ok": False, "errors": ["x"], "warnings": []})
    result = CliRunner().invoke(cli, ["insights", "config", "check", "--domain", "demo", "--compact"])
    assert result.exit_code == 1 and json.loads(result.output)["errors"] == ["x"]
```

- [ ] **Step 2: Run them and see them fail**

Run: `uv run --group dev pytest test/test_insights_config_check.py test/test_insights_cli.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement**

`artmind/insights/config_check.py`:

```python
"""`artmind insights config check`: validate domains/insights/<domain>.yaml against the live graph.

Errors: invalid YAML or keys; configured classes or properties that are not in
the graph; anchors that resolve to nothing. Warnings: configured values that
never appear in the graph (a typo or a vocabulary drift).
"""

from __future__ import annotations

from artmind.graph_query import expand_domain_family
from artmind.insights.config import load_config
from artmind.insights.model import InsightsError
from artmind.insights.profile import build_profile, infer_anchors
from artmind.insights.projections import Projector
from artmind.temporal import load_schema


def _seen_values(projector, entities, profile, cls: str, prop: str) -> set[str] | None:
    if prop == "type":
        return {e.type.strip().casefold() for e in entities if e.cls == cls and e.type}
    if prop not in profile.classes[cls].properties:
        return None
    return {str(r["value"]).strip().casefold() for r in projector.rows("entity_property", cls=cls, prop=prop)
            if r.get("value") is not None}


def check_config(domain: str, projector=None) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        config = load_config(domain)
    except InsightsError as exc:
        return {"domain": domain, "ok": False, "errors": [str(exc)], "warnings": []}
    projector = projector or Projector(expand_domain_family(domain))
    profile, entities = build_profile(projector, load_schema(domain), config)
    classes = profile.classes

    def check_values(label: str, cls: str, prop: str, values: list[str]) -> None:
        if cls not in classes:
            errors.append(f"{label}: class {cls} is not in the graph")
            return
        seen = _seen_values(projector, entities, profile, cls, prop)
        if seen is None:
            errors.append(f"{label}: property {cls}.{prop} is not in the graph")
            return
        for v in values:
            if v not in seen:
                warnings.append(f"{label}: value {v!r} never appears in {cls}.{prop}")

    outcome = config.block("outcome")
    if outcome:
        check_values("outcome", outcome["class"], outcome["property"], outcome["positive"] + outcome["negative"])
    loops = config.block("open_loops")
    if loops:
        check_values("open_loops", loops["class"], loops["property"], loops["open"] + loops["closed"])
    for cls in config.detector("communities").get("classes") or []:
        if cls not in classes:
            errors.append(f"detectors.communities.classes: {cls} is not in the graph")
    for cls in (config.block("transitions") or {}).get("classes", []):
        if cls not in classes:
            errors.append(f"transitions.classes: {cls} is not in the graph")
    try:
        infer_anchors(entities, config, profile.dated_days)
    except InsightsError as exc:
        errors.append(f"anchors: {exc}")
    return {"domain": domain, "ok": not errors, "errors": errors, "warnings": warnings}
```

`artmind/cli.py`. After `insights_runs`, add:

```python
@insights_group.command("same-as")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--export", "export", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write the candidates as `ingest refine-graph --from-file` input")
@click.option("--min-score", "min_score", type=click.FloatRange(0, 1), default=0.5, show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_same_as(domain: tuple, export: Path | None, min_score: float, compact: bool) -> None:
    """Probable duplicates from the latest snapshot; --export feeds the same-as review queue. No Neo4j."""
    name = _one_domain(domain)
    run = _latest_insights(name)
    items = sorted((f for f in run.findings if f.detector == "same_as_candidates" and f.diff != "gone"
                    and f.score >= min_score), key=lambda f: (-f.score, f.key))
    merges: dict[str, str] = {}
    candidates = []
    for f in items:
        alias, canonical = f.stats["alias"]["name"], f.stats["canonical"]["name"]
        if alias == canonical or alias in merges:
            continue
        merges[alias] = canonical
        candidates.append({"alias": alias, "canonical": canonical, "class": f.stats["canonical"]["class"],
                           "score": f.score, "rule": f.stats.get("rule")})
    payload = {"source": "artmind insights", "domain": name, "created_at": run.created_at,
               "proposed_merges": merges, "candidates": candidates}
    result = {"domain": name, "candidates": len(candidates)}
    if export is not None:
        export.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        result.update(export=str(export),
                      next=f"artmind ingest refine-graph --domain {name} --from-file {export}")
    else:
        result["proposed_merges"] = merges
    _echo_json(result, compact)


@insights_group.group("config")
def insights_config_group() -> None:
    """Check domains/insights/<domain>.yaml against the live graph."""
    pass


@insights_config_group.command("check")
@click.option("--domain", "domain", required=True, multiple=True, help="The domain (one)")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def insights_config_check(domain: tuple, compact: bool) -> None:
    """Errors (exit 1): unknown classes or properties, unresolvable anchors. Warnings: values never seen. Needs Neo4j."""
    import yaml as _yaml
    from neo4j.exceptions import DriverError, Neo4jError

    from artmind.insights import config_check
    from artmind.insights.model import InsightsError

    name = _one_domain(domain)
    try:
        result = config_check.check_config(name)
    except (InsightsError, Neo4jError, DriverError, _yaml.YAMLError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
    if not result["ok"]:
        raise click.exceptions.Exit(1)
```

Make sure `json` and `Path` are imported at the top of `cli.py`; both already are. Also update the `insights_group` docstring to mention `same-as` and `config check`.

- [ ] **Step 4: Run the tests and see them pass**

Run: `uv run --group dev pytest test/test_insights_config_check.py test/test_insights_cli.py test/test_cli_guide.py -q`. Expected: all PASS.

Then smoke the real entry point: `uv run artmind insights --help` must list `same-as` and `config`.

- [ ] **Step 5: Commit**

```bash
git add artmind/insights/config_check.py test/test_insights_config_check.py
git commit -m "feat(insights): same-as export and config check" -- artmind/insights/config_check.py artmind/cli.py test/test_insights_config_check.py test/test_insights_cli.py
```

---

### Task 10: Docs and end-to-end (controller)

- **Spec.**
  - §6.8: note the `detectors.same_as_candidates.aliases` setting and the fixed logistic weights, which are pinned by the labelled fixture in `test/test_insights_same_as.py`.
  - §6.6: note the dominance share (0.4) and `transitions.classes`, which default to recurrent classes.
  - Fulltext-index hits are deferred.
- **CLAUDE.md:** extend the `artmind/insights/` row with the new detectors, `same-as`, `config check` and `aliases.yaml`. Add `same-as` and `config check` to the routing line.
- **justfile:** add an `insights-same-as domain path` recipe.
- **End-to-end on brain01:**
  - Write `~/artmind_vaults/brain01/.artmind/domains/insights/personal_journal.yaml` with an `outcome:` block (EMOTION types) and an `open_loops:` block (PLAN status).
  - `artmind insights config check --domain personal_journal`
  - `ARTMIND_NO_PROXY=1 artmind insights refresh --domain personal_journal`, then `list`.
  - Expect no unexpected skips, lift / transitions / same-as findings, and a split_signal if a known duplicate pair shows opposite trends.
  - Never paste vault content into the repo.
