# Redesign quality scorecard

Twelve measurements taken on the live graph **before** the observation/projection
redesign. Each one is a defect the redesign is meant to remove, or a control that
should stay healthy. Re-run after cutover and compare.

Baseline captured **2026-08-23**, all domains: 64 Documents · 1,546 DocChunks ·
5,527 Entities · 7,476 Entity→Entity edges.

---

## Scorecard

| # | Measure | Baseline | Target | Removed by |
|---|---|---|---|---|
| 1 | Near-duplicate entity names (≥0.85, same class+domain) | **693 pairs** | < 100 | normalization ladder + name vocabulary + canonicalization |
| 2 | Entities with an accreted `" \| "` description | **512** (82% of the 625 multi-source entities) | **0** | accretive merge deleted |
| 3 | Entities solely sourced by a superseded document, still live | **235** | **0** | zero-observations GC rule |
| 4 | Edges typed by an entity class name or class pair | **1,581** (21%) | **0** | `RELATES_TO` + `rel_type` |
| 5 | Distinct entity→entity relationship types | **249** (58 used once) | **1** | same |
| 6 | Superseded chunks still in the vector/fulltext index | **124** (8%) | **0** | `:DocChunkHistory` label swap |
| 7 | Documents whose `version` is a string, not an integer | **63 of 64** | **0** | `_version` / `declared_version` split |
| 8 | Documents ever reaching version > 1 | **1** | — | content-hash versioning |
| 9 | Entities with an LLM-extracted `status` colliding with system status | **115** (vs 160 system) | **0** | `_`-prefixed system properties |
| 10 | Edges carrying `doc_ids` provenance | **8 of 7,482** | **100%** | provenance rebuilt, not accreted |
| 11 | Curation passes ever run (`RefineRun` / `EntityVersion`) | **0 / 0** | n/a | both mechanisms deleted |
| 12 | **Control** — distinct property keys / real duplicates | **439 / 1** | stays ≤ 1 | *should not regress* |

Row 12 is the control and the whole argument in one line: property keys are clean
**because** the prompt enumerates them per class, while entity names are filthy
**because** nothing enumerates them. Structure predicts quality. If row 12 degrades
after the schema restructure, the restructure lost something.

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

---

## Re-running it

Verified against the live graph on 2026-08-23 — it reproduces every row above.

```bash
uv run python - <<'PY'
import re, collections, difflib
from artmind.graph_query import read_session

with read_session() as s:
    q = lambda c, **p: s.run(c, **p).single()["c"]
    ents = s.run("MATCH (e:Entity) RETURN e.name AS n, e.entity_class AS k, "
                 "e.domain AS d, e.description AS desc").data()
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
    print("1  near-duplicate name pairs      ", dupes)

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
           "CONFLICTS_WITH","CONFLICT_OF","EVIDENCE","PART_OF"}
    classes = {e["k"] for e in ents if e["k"]}
    live = [x for x in edges if x["t"] not in SYS]
    leaked = [x for x in live
              if x["t"] in classes or any(p in classes for p in x["t"].split("___"))]
    print("4  class-name-typed edges         ", len(leaked))
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

    # 9 — contested status key
    print("9  extracted-status collisions    ", q(
        "MATCH (e:Entity) WHERE e.status IS NOT NULL "
        "AND e.status <> 'superseded' RETURN count(e) AS c"))

    # 10 — edge provenance
    print("10 edges with doc_ids             ",
          f"{sum(1 for x in live if x['dids'])} of {len(live)}")

    # 11 — curation ever run
    print("11 RefineRun / EntityVersion      ",
          q("MATCH (r:RefineRun) RETURN count(r) AS c"), "/",
          q("MATCH (v:EntityVersion) RETURN count(v) AS c"))

    # 12 — control: property-key hygiene
    SYSP = {"embedding","_prop_sources","id","name","entity_class","domain","type",
            "description","context","aliases","valid_from","valid_to","event_at",
            "time_source","status","superseded_by","valid_from_inferred"}
    keys = s.run("MATCH (e:Entity) RETURN [k IN keys(e) WHERE NOT k IN $s] AS ks",
                 s=sorted(SYSP)).data()
    allk = collections.Counter(k for r in keys for k in r["ks"])
    print("12 distinct property keys         ", len(allk))
PY
```

Row 12's duplicate count needs a per-`(domain, class)` difflib pass — see the
session transcript for the fuller version; the headline is that only
`who_owns_or_uses` ~ `who_owns_or_uses_it` was a genuine duplicate.
