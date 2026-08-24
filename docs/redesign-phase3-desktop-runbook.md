# Phase 3 — desktop end-to-end runbook

The Phase 3 work was built and gated on a machine with **no reachable LLM and
no embedding service**. Three legs of the pipeline therefore have unit tests
but have never run against a live model:

1. **chunk extraction** — entities/properties/relationships from real text
2. **the name vocabulary's ANN** — embed the document, query `entity_embedding`,
   feed the result into the extraction prompt
3. **the embed sweep** — the actual embedding call after a commit

Everything else (the key function, canonicalization plumbing, date lifting,
observation writes, the projection rebuild, the conflict/temporal decision, the
GC, retire/restore) is already proven live against a real Neo4j 5.26 — see
[phase3 implementation notes](./redesign-phase3-implementation-notes.md).

This runbook closes those three legs. Budget ~30 minutes.

---

## Before you start

> **This replaces the three `interest_rate_schedule_*` Documents in your
> graph.** The script's cleanup is scoped to Phase 3 artifacts — it deletes
> entities only where `e.key IS NOT NULL`, so the pre-cutover entities your
> Phase 0 scorecard measures are left alone — but those three Documents, their
> chunks and their observations are replaced. Snapshot first if that matters.

```bash
artmind snapshot create     # or your usual backup route
```

---

## Step 0 — standing rule

```bash
cd ~/path/to/artmind9
git fetch origin
git checkout claude/artmind-phase3-observation-fxb0yj
just dev-stop-daemons && just dev-install
```

`dev-install` runs `artmind init`, which re-seeds the run folder. Both matter
here: a running `serve` daemon answers with the old build, and Phase 3 added a
`canonicalization` prompt template and `kind_naming_rules` to `meta.yaml` that
only reach the extractor **through the run folder**.

Confirm the seeding actually happened:

```bash
grep -c "canonicalization" ~/.artmind/domains/meta.yaml     # expect >= 1
grep -c "kind_naming_rules" ~/.artmind/domains/meta.yaml    # expect 1
```

If either is 0, `artmind init` did not overwrite — re-run it.

---

## Step 1 — hermetic suite

```bash
just dev-test
```

**Expect: 1155 passed, 14 skipped.** The 14 skips are the live projection
tests; Step 2 turns them on.

---

## Step 2 — live projection tests against your Neo4j

```bash
ARTMIND_TEST_NEO4J_URI=bolt://127.0.0.1:7687 \
  uv run --group dev pytest test/test_projection_live.py -v
```

**Expect: 14 passed.** These use a throwaway `test.projection` domain and clean
up after themselves, so they do not touch your corpus.

They cover the invariants that fail silently: transaction rollback, label
absence, `elementId` stability across rebuilds, the zero-observations GC, the
renamed-entity orphan case, and never-null-an-embedding.

---

## Step 3 — the deterministic gate (sanity check)

```bash
uv run python scripts/phase3_vertical_slice.py --fixtures
```

**Expect: `EXIT GATE PASSED`**, matching what I got here. This stubs the model
call only. If this fails on your machine but passed on mine, the difference is
environmental (run folder, schema, Neo4j version) and worth chasing before
Step 4 — a failure here is much easier to read than a failure with an LLM in
the loop.

---

## Step 4 — the full run **(this is the one I actually need)**

```bash
# ARTMIND_VAULT_DIR must be set in ~/.artmind/.env (the Phase 0 step).
grep ARTMIND_VAULT_DIR ~/.artmind/.env

uv run python scripts/phase3_vertical_slice.py \
  --full --vault "$ARTMIND_VAULT_DIR/banking/reference"
```

The `--vault` path must sit **inside** `ARTMIND_VAULT_DIR`; the script refuses
otherwise, because a `.md` outside the vault silently takes the pre-Phase-2
path-keyed ingest instead of the vault-native one, and nothing in the output
would tell you.

The script copies the three schedules into the vault, ingests each one
(extraction → canonicalization → observations → commit, rebuild deferred), then
runs one full rebuild plus the embed sweep, then asserts the gate.

**Pass looks like:**

```
Preflight: {'documents': N, 'phase3_entities': 0, 'legacy_entities': M, 'observations': 0}

  PASS  ingest interest_rate_schedule_2026.md
  PASS  extract+commit interest_rate_schedule_2026.md
  ... x3

Deferred full rebuild + embed sweep: {'rebuilt': ..., 'embedded': N>0}

  PASS  the embed sweep embedded at least one entity
  PASS  exactly ONE :Entity for the aggregate key
  PASS  rate_value is 4.50 (March, the latest valid_from)
  PASS  _temporal_props includes "rate_value"
  PASS  three :Observation nodes behind it via AGGREGATES
  PASS  no :Conflict (the three windows are disjoint)
  ...
EXIT GATE PASSED
```

---

## Step 5 — check the two LLM legs actually did something

The gate can pass while the vocabulary leg silently degrades to empty (it is
designed to fail soft), so check the log directly:

```bash
grep -E "Name vocabulary|Canonicalization" ~/.artmind/logs/artmind_ingestion.log | tail -20
```

**What to look for:**

| Line | Healthy | Means |
|---|---|---|
| `Name vocabulary: N existing name(s) across M recurrent class(es)` | N > 0 on documents 2 and 3 | the ANN found names the first document created — **this is the leg with the least coverage; if N is 0 every time, tell me** |
| `Name vocabulary: embedding failed` / `ANN query failed` | absent | it degraded to empty and the vocabulary leg did not run at all |
| `Canonicalization: X name(s) -> Y canonical (Z rewritten)` | one line **per document**, Y < X | the pass ran once per document and actually collapsed names |

Three `Canonicalization:` lines total, not one per chunk. More than three means
trap 10 regressed.

---

## Step 6 — eyeball the result

```bash
ARTMIND_NO_PROXY=1 artmind query graph entity-listing \
  --domain banking.reference --nameFilter "SmartSaver"
```

`ARTMIND_NO_PROXY=1` matters — a running `serve` daemon would answer from the
old build.

Then the projected entity itself — `pattern4` takes a class plus a name and
returns the node with its properties and neighbours:

```bash
ARTMIND_NO_PROXY=1 artmind query graph pattern4 \
  --domain banking.reference \
  --entityClass RATE_ENTRY \
  --entityName "SmartSaver Account Tier 2 Rate"
```

Check `rate_value` is `4.50` and `_temporal_props` contains `rate_value` —
the same two assertions the gate makes, but read through the query layer a
user would actually go through.

**Judgement call, not an assertion:** look at the entity names the real
extractor produced. `banking.reference`'s RATE_ENTRY guidance still tells it to
put the rate value and effective date *in* the name, contradicting the
meta-schema's recurrent naming rule (open question 2 in the notes). The key
function and canonicalization repair it, but if names are still arriving
measurement-laden, that is the schema guidance fighting the prompt and worth
fixing in Phase 7.

---

## Step 7 — retire/restore on real data

The one lifecycle behaviour Phase 3 pulled forward from Phase 5. Verified here
on fixtures; worth one pass on real extraction output.

```bash
ARTMIND_NO_PROXY=1 artmind docs retire \
  --domain banking.reference --documentName interest_rate_schedule_2026_03.md

ARTMIND_NO_PROXY=1 artmind query graph pattern4 \
  --domain banking.reference --entityClass RATE_ENTRY \
  --entityName "SmartSaver Account Tier 2 Rate"
# expect rate_value 4.60 — February now wins

ARTMIND_NO_PROXY=1 artmind docs restore \
  --domain banking.reference --documentName interest_rate_schedule_2026_03.md
# expect 4.50 again, and the SAME entity id as before the retire
```

Retiring all three would delete the entity outright (zero `latest`
observations) — that is the GC rule, and it is already covered live in
`test_projection_live.py`, so one retire/restore cycle is enough here.

---

## What to send back

Whatever happens, these four:

1. the full output of Step 4
2. the `grep` output from Step 5
3. the entity names from Step 6
4. `just dev-test` result from Step 1

If Step 4 fails, also `tail -100 ~/.artmind/logs/artmind_ingestion.log` — the failure will
almost certainly be in extraction or the vocabulary ANN, and the staged JSON
under `~/artmind_data/kg/banking.reference/<stem>/` (`observations.json`
especially) tells the rest of the story.

---

## Known-shaky spots, in likelihood order

1. **The vocabulary ANN returns nothing.** It queries the `entity_embedding`
   vector index, which only has content once the sweep has embedded something —
   so the first document legitimately gets an empty vocabulary. Documents 2 and
   3 should get names. If all three are empty, the sweep is not writing
   embeddings and Step 5's log will say so.
2. **The canonicalization model returns a shape the parser rejects.** It
   accepts both a JSON array of `{name, canonical_name}` and a flat
   `{name: canonical}` object, and falls back to identity mapping on anything
   else — which degrades quality without failing. Step 5's `Z rewritten` count
   is how you tell: a `0 rewritten` on every document means the mapping was
   discarded.
3. **A local model ignoring the recurrent naming rule.** Recoverable (the key
   function strips measurement tails), but it will show up as noisier names in
   Step 6.
