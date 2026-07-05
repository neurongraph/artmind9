---
name: artmind-query
description: The artmind system stores information from ingested documents. Using this skill respond to natural-language questions from the user given a particular domain. These questions would be in the form of Q&A between the user and artmind
---

# artmind Query

Use this skill to answer user questions over an artmind domain through the deterministic query CLI. The skill provides the reasoning layer; the CLI provides templated graph/vector retrieval and JSON output.

## Grounding Rule

Use only the structured KG data and chunk text returned by artmind query commands. If the data is insufficient, say so clearly. Do not invent entities, events, relationships, motivations, or source details not present in the returned data.

## Required Inputs

- `domain`: Ask for it if the user did not provide one.
- `question`: The natural-language question to answer. If the user asks multiple questions, break them down and answer each.

## Fixed Structural Schema

Four structural node types with fixed relationships, identical across domains:

- `(:DocChunk)-[:PART_OF]->(:Document)` — chunk belongs to a document
- `(:Entity)-[:EXTRACTED_FROM]->(:DocChunk)` — entity was extracted from a chunk
- `(:DocChunk)-[:MENTIONS]->(:Entity)` — chunk mentions an entity
- `(:UserChat)-[:MENTIONS]->(:Entity)` — user chat mentions an entity

Key properties: `Document` (id, name, path, domain), `DocChunk` (id, name, doc_id, text, domain), `UserChat` (id, raw_text, domain, session_id, created_by, created_at), `Entity` (id, name, entity_class, domain, description, type).

Every extracted entity carries the `:Entity` label plus a class label (e.g. `PERSON`). Entity-to-Entity relationship types are domain-specific — always check metadata. For document/chunk questions use `PART_OF` (not EXTRACTED_FROM); `pattern10` does this deterministically.

Add `--compact` to every command — it halves the JSON you must read.

## The Query Protocol: Route → Discover → Resolve → Retrieve → Ground → Adjudicate

### 0. Route — pick the domain set

Policies and the SOPs/matrices about the same subject live in DIFFERENT sibling
domains by design (e.g. `banking_policy` vs `banking_sop_guides`). Before answering:

```bash
uv run artmind query domains-overview --compact
```

- If the user names an exact single small domain, use it and skip to Discover.
- If the user names two or more SPECIFIC domains directly (e.g. "compare banking_policy
  and banking_sop_guides"), use exactly those domains and skip to Discover — no
  sub-agent needed, since routing is already resolved.
- If the user names an AREA ("banking", "our policies"), or it's unclear which
  domain(s) hold the answer, or listings look large, launch ONE sub-agent that runs
  `domains-overview` + per-domain `structural-metadata --compact` + `entity-resolve`,
  and returns ONLY a compact routing report:
  `{domains, resolved_entities:[{id,name,class,domain}], relevant_classes, relevant_rel_types}`.
  Main context never sees the raw listings.
- Pass `--domain` once per selected domain on every subsequent command; a single
  command call now spans all of them.

### 1. Discover — learn the domain's shape

Start every new domain/question session with:

```bash
uv run artmind query graph metadata --domain <domain> --compact
uv run artmind query graph entity-listing --domain <domain> --countAll --compact
```

For document/chunk/count questions, `structural-metadata` is the compact alternative (Document names + structural counts). From metadata identify: stored class labels (derived from `entity_class`, uppercased, non-alphanumerics → `_`), relationship types and directions, and whether the question needs graph facts, text evidence, or both.

If `total_entities` is large (> ~100), do not fetch the full listing. Narrow with `--nameFilter "<fragment>"`, or go straight to `pattern7`.

Document/chunk rows and `metadata` now carry `valid_from`/`valid_to`/`superseded_by` — use them to judge document currency.

### 2. Resolve — map question names to exact graph nodes

Most wrong answers come from name mismatch: the user says "Holmes", the graph has "Sherlock Holmes" AND "Mycroft Holmes", and substring matching silently merges them. If Route's sub-agent already returned `resolved_entities`, reuse those ids directly and skip straight to step 3 — don't re-resolve. Otherwise, before running retrieval patterns:

1. Resolve every entity reference in the question:

```bash
uv run artmind query entity-resolve --domain <domain> --topK 5 --compact "<name fragment or description>"
```

This combines Lucene full-text over entity names/descriptions with vector similarity over entity embeddings (RRF), so it handles both name fragments ("Holmes") and purely descriptive references ("the detective"). Each row returns the entity's `id`, `name`, `entity_class`, and `description`. (Alternatives: `entity-listing --nameFilter` for plain fragments, `pattern7 --searchTerm` for fulltext-only.)

2. Pick the canonical entity. If several are plausible, prefer the best name/description match and note the ambiguity in your answer; ask the user only if the choices change the answer materially.
3. Use the entity's exact `id` in retrieval via `--entityId` / `--entityId1` / `--entityId2` / `--entityIdList`. Ids never fan out; names can. Fall back to `--entityName` only when resolution was skipped because the name is unambiguous.

If entity-resolve returns nothing for an old graph, embeddings may be missing — `uv run artmind ingest embed-entities --domain <domain>` backfills them.

### 3. Retrieve — run the right pattern

| Question shape | Command |
|---|---|
| List entities of a class | `pattern1 --entityClass <LABEL> [--limit N]` |
| "Main / key / most important / top" entities | `pattern9 --entityClass <LABEL> --topN 5` (default ranks by entity-entity links; `--degreeMode mentions` ranks by how often sources mention it) |
| Facts/properties of named entities | `pattern2 --entityIdList <id>` (or `--entityNameList`) |
| Properties + relationship summary | `pattern3 --entityIdList <id>` |
| Full one-hop neighborhood / contextual role | `pattern4 --entityClass <LABEL> --entityId <id>` |
| Does a direct link exist between X and Y, and of what type | `pattern6 --entityId1 <id> --entityId2 <id>` |
| Nature/quality of a relationship, "how are X and Y related/connected" | `pattern5 --mode shortest --entityClass1/2 --entityId1/2` (paths traverse entity-entity edges only); `--mode all` for up to 3 paths |
| Search entities by name/description fragment | `pattern7 --searchTerm "<fragment>"` (Lucene-backed; punctuation is stripped automatically) |
| Entities of class X connected to entity Y | `pattern8 --entityClass <LABEL> --entityId <id>` |
| All chunks of / summarize a document | `pattern10 --documentName "<name>"` |
| Aggregations, custom filters, multi-hop combinations none of the above cover | `text2cypher "<question>"` — run `--dry-run` first to inspect the Cypher |

Routing notes:
- **pattern6 vs pattern5**: pattern6 answers "is there a direct relationship and what type". For the *nature or quality* of a relationship, use pattern5 — then ground with vector-text for narrative evidence. If pattern6 returns no rows, escalate to pattern5 `--mode shortest`.
- Patterns 2/3/4 return `doc_sources` and `chat_sources` — use these ids to know *where* a fact came from, and pull the actual text in the Ground step when needed.
- All commands accept repeatable `--domain` (comma-splittable) and roll sub-domains up.
  Rows carry `.domain` on chunks/documents — every fact you state must be attributed
  to BOTH its document name AND its domain.
- Add `--asOf today` for present-tense questions ("who can approve…"); drop it for
  historical ones. Untimed knowledge is always visible.

### 4. Ground — pull source text when narrative evidence is needed

```bash
uv run artmind query vector-text --domain <d1> --domain <d2> --topK 5 --compact "<question>"
```

Combines semantic (vector) and keyword (Lucene BM25 full-text) search via Reciprocal Rank Fusion; returns both document chunks and user chats. Use it for "where/when/how did X happen", motivations, quotes, or whenever graph output is too thin. In hybrid answers, take entity/relationship facts from the graph and narrative evidence from chunk text. When Route selected multiple domains, Ground should query all of them together in one call so results can be compared side by side.

### 5. Adjudicate — surface disagreements, never blend

First check for already-materialized conflicts on the resolved entity ids:

```bash
uv run artmind query graph conflicts --domain <d1> --domain <d2> --entityId <id> --compact
```

If a `Conflict` exists, surface its `claim_a`/`claim_b` with their EVIDENCE
provenance. Then independently compare the retrieved claims (below) to catch
conflicts introduced by new documents since the last detect-conflicts run.

After grounding, compare quantitative/authority claims across the retrieved
documents and domains (no extra LLM calls — the evidence is already in context).
When two sources disagree, surface BOTH claims with BOTH provenances in this format:

> Sources disagree: policy_complaints.md (banking_policy) says X; escalation_matrix.md
> (banking_sop_guides) says Y.

Never average, reconcile silently, or drop one side. If retrieval returned only one
side, re-run Ground with the sibling domains from Route before concluding.

Qualify claims by time: report present-tense answers "as of <date>, source A says X".
A claim whose document is superseded (has `superseded_by` / a `valid_to` in the past)
is HISTORY, not a live disagreement — say so. Before treating a materialized Conflict
as live, verify both documents' valid-time windows overlap and neither supersedes the
other — detect-conflicts' LLM adjudication tries to catch this at detection time, but
it isn't a structural guarantee, so re-check at query time using each side's
`valid_to`/`superseded_by`.

## Fallback Ladder

1. Thin results in the chosen domain → re-run with sibling domains from Route before concluding data is absent. (Same fix as Adjudicate's "only one side retrieved" case — apply it as soon as results look thin, not only after a disagreement surfaces.)
2. pattern6 empty → pattern5 `--mode shortest`.
3. Pattern output empty or too thin → vector-text.
4. text2cypher returns no rows but data should exist → run `structural-metadata`, then `text2cypher --dry-run` and compare relationship names; rephrase the question naming the correct relationship (e.g. "use PART_OF to connect DocChunk to Document").
5. text2cypher generates invalid Cypher → vector-text.
6. vector-text sparse or weak → state that the available artmind data does not answer the question.

## Answer Style

Answer directly and naturally. Keep provenance concise: say whether the answer comes from graph relationships, entity properties, chunk text, or a combination. Mention unresolved ambiguity when you picked between candidate entities. Do not expose raw JSON unless asked.
