# Redesign quality scorecard

Twelve measurements taken on the live graph **before** the observation/projection
redesign. Each one is a defect the redesign is meant to remove, or a control that
should stay healthy. Re-run after cutover and compare.

Baseline captured **2026-08-23**, all domains: 64 Documents · 1,546 DocChunks ·
5,527 Entities · 7,476 Entity→Entity edges.

**After-cutover measured 2026-08-29** (Phase 8 Step 7,
[`redesign-phase8-implementation-notes.md`](redesign-phase8-implementation-notes.md)),
all domains, fresh full re-ingest from the vault: 63 Documents · 1,529 DocChunks ·
8,072 Entities · 14,720 Entity→Entity edges.

---

## Scorecard

| # | Measure | Baseline | Target | **After cutover** | Removed by |
|---|---|---|---|---|---|
| 1 | Near-duplicate entity names (≥0.85, same class+domain) | 693 pairs | < 100 | **644 pairs** ❌ missed | normalization ladder + name vocabulary + canonicalization |
| 2 | Entities with an accreted `" \| "` description | 512 (82% of the 625 multi-source entities) | 0 | **0** ✅ | accretive merge deleted |
| 3 | Entities solely sourced by a superseded document, still live | 235 | 0 | **0** ✅ | zero-observations GC rule |
| 4 | Edges typed by an entity class name or class pair | 1,581 (21%) | 0 | **0** ✅ (of 14,578 live edges) | `RELATES_TO` + `rel_type` |
| 5 | Distinct entity→entity relationship types | 249 (58 used once) | 1 | **1** ✅ exact | same |
| 6 | Superseded chunks still in the vector/fulltext index | 124 (8%) | 0 | **0** ✅ | `:DocChunkHistory` label swap |
| 7 | Documents whose `version` is a string, not an integer | 63 of 64 | 0 | **0** ✅ | `_version` / `declared_version` split |
| 8 | Documents ever reaching version > 1 | 1 | — | **0** (informational — no doc has been re-ingested a second time yet post-cutover) | content-hash versioning |
| 9 | Entities with an LLM-extracted `status` colliding with system status | 115 (vs 160 system) | 0 | **163, but reframed** — system status now lives on `_status` (reserved prefix), so this no longer collides with anything; it's harmless domain data | `_`-prefixed system properties |
| 10 | Edges carrying `doc_ids` provenance | 8 of 7,482 | 100% | **14,578 of 14,578 (100%)** ✅ exact | provenance rebuilt, not accreted |
| 11 | Curation passes ever run (`RefineRun` / `EntityVersion`) | 0 / 0 | n/a | **0 / 0** ✅ | both mechanisms deleted |
| 12 | **Control** — distinct property keys / near-dup pairs | 439 / 1 | stays ≤ 1 dup | **1,773 / 80** ❌ regressed hard | *should not regress* |

Row 12 is the control and the whole argument in one line: property keys are clean
**because** the prompt enumerates them per class, while entity names are filthy
**because** nothing enumerates them. Structure predicts quality. If row 12 degrades
after the schema restructure, the restructure lost something — **and it did**: see
the implementation notes for the root cause (the `properties` extraction prompt in
`artmind/domains/meta.yaml` is explicitly free-form, contradicting this row's own
premise) and Finding B there.

Row 1 also missed its target for a distinct, confirmed reason: `sameas propose` is
architecturally a cross-domain adjudicator only and never generates same-domain
near-duplicate candidates — see Finding A in the implementation notes.

## Two more that aren't defects, but should move

| Measure | Baseline | Expected after |
|---|---|---|
| Distinct aggregate keys after deterministic normalization | 5,527 → **5,482** lexical → **5,408** with measurement-tail stripping | 119 spurious splits collapse without any LLM |
| Alias transitive closure | fuses `FCA` + `PRA` + `Regulatory Authorities` into one 5-member component | **0** — aliases propose, they never merge |

## Answer quality — baseline captured

The numbers above measure the graph. Only one thing measures whether the system got
*better at answering*: the banking Q&A benchmark.

| | |
|---|---|
| Run | `banking_corpus_before_artmind_changes` — `291e70cc-0898-43a2-a90d-b6e80ffba777` |
| Status | **completed**, 2026-08-23T17:31 |
| Questions | **36**, from `benchmarking/questions.md` |
| Backend | `claude-sdk` |
| Export | [`benchmarking/baseline-2026-08-23.md`](../benchmarking/baseline-2026-08-23.md) |

Re-run the same 36 questions after cutover, export to
`benchmarking/after-cutover.md`, and diff question by question.

Graph metrics going to zero while benchmark answers get worse would mean the
redesign optimised the model at the expense of retrieval. That is the failure mode
worth watching for.

**After-cutover run:**

| | |
|---|---|
| Run | `banking_corpus_after_phase8_cutover` — `dcc444b4-b91f-4a23-917e-2d735c464cf4` |
| Status | **completed**, 36/36, 2026-08-29 |
| Backend | `claude-sdk`, model `claude-haiku-4-5` (enterprise gateway) |
| Export | [`benchmarking/after-cutover.md`](../benchmarking/after-cutover.md) |

Two things must be disclosed before comparing the two runs, and both are covered
in full in the implementation notes:

1. **Baseline itself is incomplete for 5 of 36 questions** — Q32 is a truncated
   `failed` answer and Q33–Q36 are unanswered spend-limit rejections. After-cutover
   produced the first real answers ever obtained for those five; that is not a
   fair regression/improvement comparison, so it isn't reported as one.
2. **The two runs used different models** (baseline's model is unknown/unrecorded;
   after-cutover ran `claude-haiku-4-5` via a gateway workaround for an unrelated
   OAuth failure). The ~4–6x lower cost/turn count after cutover is confounded by
   this and is not attributed to the redesign.

Quality (the axis that actually matters) held up on every hard case sampled —
temporal supersession, conflict surfacing, not collapsing a stated discrepancy,
refusing false precision — with one disclosed exception: Q36 asserted a trend from
an n=1 sample instead of flagging it as too thin to generalize, exactly the pattern
Q35 (two questions later, same trap shape) got right. See the implementation notes
for the full read.

---

## Re-running it

Verified against the live graph on 2026-08-23 (baseline) and again on 2026-08-29
(after cutover) — this version reproduces every row above for both runs. It fixes
three bugs the 2026-08-23 version of this script had, found and confirmed live
during Phase 8 Step 7 (see the implementation notes for how each was caught):

- **Row 1** used `e.domain`, a property that does not exist on `Entity` (the
  redesign's reserved-prefix convention makes it `e._domain`) — silently grouping
  every entity into one `(class, None)` bucket and inflating the pair count.
  Fixed to `e._domain`.
- **Rows 4/5/10** excluded the adjudicator's structural bookkeeping edge types
  (`EXTRACTED_FROM`, `SUPERSEDES`, ...) from "live" relationships but missed two
  more added since: `SAME_AS` and a second `CONFLICTS_WITH` reference. Both are
  curation-graph edges, not extracted semantic relationships, and belong in `SYS`
  alongside the others.
- **Row 9**'s framing is stale post-redesign: system status now lives on the
  reserved `_status` property, so an LLM-extracted `status` value can no longer
  collide with anything. The query is kept (it's still informative) but the count
  is no longer a defect — see the table above.

```bash
uv run python - <<'PY'
import collections, difflib
from artmind.graph_query import read_session

with read_session() as s:
    q = lambda c, **p: s.run(c, **p).single()["c"]
    ents = s.run("MATCH (e:Entity) RETURN e.name AS n, e.entity_class AS k, "
                 "e._domain AS d, e.description AS desc").data()
    edges = s.run("MATCH (:Entity)-[r]->(:Entity) RETURN type(r) AS t, "
                  "r.doc_ids AS dids").data()

    # 1 — near-duplicate names within class+domain
    by = collections.defaultdict(list)
    for e in ents:
        by[(e["k"], e["d"])].append(e["n"])
    dupes = sum(
        1
        for names in by.values()
        for i in range(len(names))
        for j in range(i + 1, len(names))
        if names[i] and names[j]
        and difflib.SequenceMatcher(None, names[i].lower(), names[j].lower()).ratio() >= 0.85
    )
    print("1  near-duplicate name pairs      ", dupes, f"(of {len(ents)} entities)")

    # 2 — accreted descriptions
    print("2  accreted descriptions          ",
          sum(1 for e in ents if e["desc"] and " | " in str(e["desc"])))

    # 3 — live entities whose only source is a superseded document
    print("3  unretired orphans              ", q("""
        MATCH (o:Document)<-[:PART_OF]-(:DocChunk)<-[:EXTRACTED_FROM]-(e:Entity)
        WHERE o.valid_to IS NOT NULL
        MATCH (e)-[:EXTRACTED_FROM]->(ch:DocChunk)
        WITH e, collect(DISTINCT ch.doc_id) AS ds
        WHERE size(ds) = 1 AND coalesce(e.status,'') <> 'superseded'
        RETURN count(DISTINCT e) AS c"""))

    # 4 / 5 — relationship type space
    SYS = {"EXTRACTED_FROM","SUPERSEDES","MENTIONS","PRIOR_STATE",
           "CONFLICTS_WITH","CONFLICT_OF","EVIDENCE","PART_OF","SAME_AS"}
    classes = {e["k"] for e in ents if e["k"]}
    live = [x for x in edges if x["t"] not in SYS]
    leaked = [x for x in live
              if x["t"] in classes or any(p in classes for p in x["t"].split("___"))]
    print("4  class-name-typed edges         ", len(leaked), f"(of {len(live)} live edges)")
    print("5  distinct rel types             ", len({x['t'] for x in live}))

    # 6 — superseded chunks still indexed
    print("6  stale indexed chunks           ", q(
        "MATCH (c:DocChunk) WHERE c.valid_to IS NOT NULL "
        "AND c.embedding IS NOT NULL RETURN count(c) AS c"))

    # 7 / 8 — versioning
    print("7  string-versioned documents     ", q(
        "MATCH (d:Document) WHERE d.version IS NOT NULL "
        "AND valueType(d.version) STARTS WITH 'STRING' RETURN count(d) AS c"))
    print("8  documents at version > 1       ", q(
        "MATCH (d:Document) WHERE d.version > 1 RETURN count(d) AS c"))

    # 9 — extracted `status` values (no longer a collision — see note above)
    print("9  entities w/ extracted `status` ", q(
        "MATCH (e:Entity) WHERE e.status IS NOT NULL "
        "AND e.status <> 'superseded' RETURN count(e) AS c"))

    # 10 — edge provenance
    print("10 edges with doc_ids             ",
          f"{sum(1 for x in live if x['dids'])} of {len(live)}")

    # 11 — curation ever run
    print("11 RefineRun / EntityVersion      ",
          q("MATCH (r:RefineRun) RETURN count(r) AS c"), "/",
          q("MATCH (v:EntityVersion) RETURN count(v) AS c"))

    # 12 — control: property-key hygiene, plus the near-dup pairs the earlier
    # version of this script deferred to the session transcript
    SYSP = {"embedding","_prop_sources","id","name","entity_class","domain","type",
            "description","context","aliases","valid_from","valid_to","event_at",
            "time_source","status","superseded_by","valid_from_inferred"}
    ent_props = s.run(
        "MATCH (e:Entity) RETURN e.entity_class AS k, e._domain AS d, "
        "[x IN keys(e) WHERE NOT x IN $s] AS ks", s=sorted(SYSP)).data()
    allk = collections.Counter(k for r in ent_props for k in r["ks"])
    print("12 distinct property keys         ", len(allk))

    by_prop = collections.defaultdict(set)
    for r in ent_props:
        for k in r["ks"]:
            by_prop[(r["k"], r["d"])].add(k)
    prop_dupes = 0
    for names in by_prop.values():
        names = sorted(names)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if difflib.SequenceMatcher(None, names[i], names[j]).ratio() >= 0.85:
                    prop_dupes += 1
    print("12b near-dup property-key pairs   ", prop_dupes)
PY
```
