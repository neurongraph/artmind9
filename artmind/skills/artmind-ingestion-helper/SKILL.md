---
name: artmind-ingestion-helper
description: Interactive guide for the `artmind ingest` pipeline. Helps users navigate ingestion stages, pick the right command, diagnose problems, and run entity resolution. Use when a user asks about ingesting documents, checking job status, re-running extraction, writing to graph, pulling KG from a repo, or fixing ingestion errors.
---

# artmind Ingestion Helper

You are a guided navigator for the `artmind ingest` pipeline. The user may not remember command names or the correct order of steps. Your job is to ask one clarifying question, identify their situation from the map below, then give them the exact command(s) to run — no more, no less.

## Step 0 — Orient the user

If the user hasn't stated their goal clearly, ask:

> "What are you trying to do? For example: ingest new documents, check the status of a running job, re-run extraction, write extracted JSON to Neo4j, pull KG from another repo, or clean up duplicate entities?"

Once you know the goal, go to the matching section below.

---

## Situation Map

### A. Ingest new documents for the first time

**Single file (recommended, blocking):**
```bash
artmind ingest sync path/to/document.pdf --domain YOUR_DOMAIN
```
- If `--domain` is omitted, the CLI prompts interactively.
- Runs the full pipeline: convert → chunk → LLM extract → write to Neo4j.
- Takes a few minutes per document. Watch the log output.

**Folder of files (blocking):**
```bash
artmind ingest sync path/to/folder/ --domain YOUR_DOMAIN
```

**Background / batch (non-blocking):**
```bash
artmind ingest async path/to/document.pdf --domain YOUR_DOMAIN
# returns a job_id immediately
```
Then track it with the admin UI's dashboard (`artmind admin-ui`, then open `/dashboard`) or `artmind ingest job-status JOB_ID`.

**A vault-native markdown file gets its identity seeded on first ingest**:
`_artmind_id` (a uuid7), `_version`, `_content_sha256`, and the rest of the
system frontmatter block are written into the file itself, and artmind makes
a git commit in the vault recording it. Re-ingesting the same file later
bumps `_version` only if the body changed; editing only frontmatter (tags,
title) takes a metadata-only fast path with no new version and no
re-extraction. If `--domain` is omitted, a file's own `_domain` frontmatter
wins over anything passed on the command line.

**A multi-file batch defers its projection rebuild to one pass at the end**
(true for both a folder `sync` and a multi-file `async` job) — every
document's observations get written, then one rebuild + one embed sweep
covers the whole batch. The deliberate next step after a bulk load is:
```bash
artmind projection synthesize --domain YOUR_DOMAIN --compact
```
This rewrites entity descriptions from their full observation set — the only
step in the pipeline that spends language-model budget without being asked
to, so it never runs automatically. See `/artmind-curate` for the full
`projection synthesize` reference.

**Which to use?**
- Single file or small batch → `sync` (simpler, log is right there)
- Large batch or want to keep working → `async`

**Batch concurrency limit — important:**
When running multiple background `sync` jobs in parallel, **cap at 5 concurrent jobs**. The LLM backend (Ollama cloud) has rate limiter constraints: running more than 5 simultaneous extraction jobs causes chunks to fail instantly with 0 entities extracted — the jobs appear to complete quickly but produce no useful output. Always wait for a batch of 5 to finish before launching the next batch.

**Need a domain first?** Ask the user: does a suitable domain already exist? Run:
```bash
artmind domains list
```
If not, point them at `/artmind-create-schema` to create one.

---

### B. Check the status of an async job

```bash
# List recent jobs:
artmind ingest jobs

# Status for a specific job:
artmind ingest job-status JOB_ID

# Detailed per-file results:
artmind ingest job-results JOB_ID

# List all recent jobs (optional status filter):
artmind ingest jobs
artmind ingest jobs --status failed
```

If files failed, go to **Situation E** (retry).

---

### C. Write extracted JSON to Neo4j (without re-running LLM)

Use this when Neo4j had a problem and you want to replay already-extracted data, or after a `pull-kg`.

**Single document:**
```bash
artmind ingest write-to-graph DOCUMENT_NAME --domain YOUR_DOMAIN
```
`DOCUMENT_NAME` is the registered filename (e.g. `report.pdf`).

**All documents in a domain folder:**
```bash
artmind ingest write-to-graph --folder data/kg/YOUR_DOMAIN
```

**Folder that contains domain sub-folders (e.g. `data/kg`):**
```bash
artmind ingest write-to-graph --domain YOUR_DOMAIN --folder data/kg
```
The CLI will search recursively, show you the list of documents it found, and ask for confirmation before writing.

**Prerequisites:** The `document.json` must already exist in `data/kg/DOMAIN/DOCNAME/`. If it doesn't, run `extract-kg` first (Situation D).

**Chunk embeddings:** `write-to-graph` runs a resumable chunk-embedding sweep afterwards by
default — committed KG staging carries no vectors on purpose (see `docs/vault.md`,
"Embeddings"), so restoring from it (a fresh clone, a `pull-kg`, a wiped graph) always needs
this once. Pass `--noEmbed` to skip it and run it separately later:
```bash
artmind ingest embed-chunks
```

---

### D. Re-run LLM extraction on an already-ingested document

Use when extraction failed mid-way, or you updated the schema and want to re-extract.

```bash
artmind ingest extract-kg DOCUMENT_NAME --domain YOUR_DOMAIN
```

This skips chunks that already succeeded and only re-runs failed/missing ones. After it completes, write to Neo4j:

```bash
artmind ingest write-to-graph DOCUMENT_NAME --domain YOUR_DOMAIN
```

**If the document isn't registered at all** (no chunks exist), you need `sync` first (Situation A).

---

### D.1 — Extraction is stuck/crawling (e.g. repeated "Connection error") — halt and resume safely

Large documents (hundreds of chunks) can hit transient LLM-provider connection errors partway through. It is **safe to kill the worker at any time** — progress is durable per chunk *and per step* (`entities`/`properties`/`relationships`) in the `kg_chunk_status` table, not just per file. Killing it does not lose completed work.

1. **Find the stuck file and job:**
   ```bash
   artmind ingest jobs --status processing
   artmind ingest job-status JOB_ID
   ```
   Look at `chunk_progress` per file — a file with `entities_done` far behind `total_chunks`, or one that hasn't advanced in a while, is the culprit.

2. **Confirm the error pattern** (optional, for diagnosis):
   ```bash
   tail -200 logs/artmind_worker.log | grep -i "connection error"
   ```

3. **Kill the worker:**
   ```bash
   ps aux | grep worker.py   # find the PID (also cached in worker.pid at project root)
   kill PID
   ```
   The PID file is stale-safe — the next `_ensure_worker_running()` call (e.g. from `async` or `retry-job`) detects the dead PID and overwrites it automatically. No manual cleanup needed.

4. **Resume the specific stuck document with `extract-kg` — not `retry-job`:**
   ```bash
   artmind ingest extract-kg "DOCUMENT_NAME" --domain YOUR_DOMAIN
   ```
   This re-reads `kg_chunk_status` for the document and skips every chunk+step already marked `ok`, retrying only `failed`/not-yet-attempted ones. **Important:** `retry-job` only re-queues job-files with status `failed` (see Situation E) — a file killed mid-`processing` stays stuck at `processing` and `retry-job` will silently skip it. `extract-kg` works directly off `kg_chunk_status` and ignores the job-file status entirely, so it's the correct tool here.

5. **Large documents take hours — run it detached, not blocking:**
   ```bash
   nohup artmind ingest extract-kg "DOCUMENT_NAME" --domain YOUR_DOMAIN > /tmp/extract.log 2>&1 &
   ```
   Poll progress with `job-status` or by tailing `logs/artmind_ingestion.log`, rather than waiting on a blocking foreground call (which will time out in most shells/harnesses long before a 500+ chunk document finishes).

6. **To verify it's truly resuming (not redoing) rather than trusting the log alone:**
   ```bash
   artmind ingest job-chunks JOB_ID "DOCUMENT_NAME" --compact
   ```
   Returns one row per chunk — `{"seq": 1, "e": "ok", "p": "ok", "r": "ok"}` — where `e`/`p`/`r` are the entities/properties/relationships steps. Chunks already `ok` should stay `ok`; only chunks previously `failed` or unattempted should change.

   **Don't query the registry DB directly for this.** The live registry lives under `$ARTMIND_DATA_DIR` (default `~/artmind_data`), not in the working directory — and a stale `data/document_registry.db` may well exist in a source checkout, so a relative path can read the wrong database and return confidently wrong answers at exactly the moment you are checking whether work is being redone.

   In the log itself, a genuine skip shows only the `Chunk N/743 (...)` header line with no `entities`/`properties`/`relationships` sub-lines — real work shows all three (or a silent `skipped` if the chunk legitimately had zero entities).

Once extraction finishes, write it to Neo4j as usual (Situation C).

---

### E. Retry a failed async job

```bash
artmind ingest retry-job JOB_ID
```

This re-queues only files with job-file status `failed` (and `skipped` if `--include-skipped` is passed) — **not** files stuck at `processing`. If a file was mid-`processing` when the worker died, use `extract-kg` directly on that document instead (Situation D.1); `retry-job` won't touch it.

To also force re-processing of files that were skipped as duplicates:
```bash
artmind ingest retry-job JOB_ID --include-skipped
```

---

### F. Pull KG JSON from an external / team repository

Useful when another team has already run extraction and shared the JSON in a Git repo — no need to re-extract.

```bash
# 1. Pull the KG JSON (sparse git checkout — only fetches the target path)
artmind ingest pull-kg \
  --repo git@github.com:ORG/REPO.git \
  --repo-path data/kg/DOMAIN_FOLDER \
  --domain YOUR_DOMAIN

# 2. Write the pulled documents to Neo4j
artmind ingest write-to-graph --folder data/kg/YOUR_DOMAIN

# 3. Optionally propose duplicate-entity merges (Situation G) for artmind-curate to review
artmind ingest refine-graph --domain YOUR_DOMAIN --dry-run
```

**Conflict:** If a document sub-folder already exists locally, `pull-kg` aborts and lists conflicts. Resolve by renaming or deleting the local copy, then re-run.

**Auth:** Uses your existing Git credentials (SSH keys / credential helpers). Set `GITHUB_TOKEN` for HTTPS fallback.

---

### G. Propose duplicate / similar entities for curation review

After ingesting several documents, entity names may duplicate (e.g. "Holmes", "Sherlock Holmes"). This command **proposes** same-as groups; it does not merge anything itself — hand off to `/artmind-curate` to review and approve:

```bash
# Step 1 — dry-run: compute proposals, save for review
artmind ingest refine-graph --domain YOUR_DOMAIN --dry-run --output merges.json

# Step 2 — review merges.json; edit if needed

# Step 3 — write the reviewed candidates as same-as proposals
artmind ingest refine-graph --from-file merges.json

# Step 4 — review and approve in the queue (artmind-curate's job)
artmind sameas list --status open --compact
artmind sameas approve <proposal_id>
```

**Focused proposing** (only specific entities):
```bash
artmind ingest refine-graph --domain YOUR_DOMAIN \
  --filter "Holmes,Watson,Moriarty" \
  --dry-run --output merges.json
```

**Backfill missing embeddings** (needed for entity-resolve queries and for
`sameas propose`'s candidate generation):
```bash
artmind ingest embed-entities --domain YOUR_DOMAIN
```

**Backfill missing chunk embeddings** (needed for vector/text search over chunks —
`write-to-graph` runs this automatically unless `--noEmbed` was passed):
```bash
artmind ingest embed-chunks
```

---

### H. Remove a document from the graph

**Retire** — moves the document and everything it asserted from `latest` to `history`: an
assertion-time act with no date semantics. Its observations stay in storage and stay reachable
by asking for them (`query entity-history`), but leave every index (vector/full-text search,
`chunks`, doc listings). Entities left with no `latest` observation anywhere are then removed by
the projection rebuild — not a guess, an arithmetic fact about what nothing asserts any more.
Reversible with `docs restore`:
```bash
artmind docs retire --domain YOUR_DOMAIN --documentName DOCUMENT_NAME
artmind docs restore --domain YOUR_DOMAIN --documentName DOCUMENT_NAME
```

**Archive** — the only actual removal artmind has (there is deliberately no
`purge`). Bundles the document (staged KG JSON, vault markdown, original
binary if any, a manifest) under `ARTMIND_ARCHIVE_DIR`, then removes it from
BOTH the graph and the vault (a real `git rm` + commit):
```bash
artmind docs archive --domain YOUR_DOMAIN --documentName DOCUMENT_NAME
artmind docs archived --domain YOUR_DOMAIN                    # list what's archived
artmind docs restore-from-archive --id ARTMIND_ID             # lands back as history, never latest
```
`restore-from-archive` deliberately does not promote the document to
`latest` — run `docs restore` afterward if that's really wanted. Deleting the
archive bundle itself is a filesystem act outside any artmind command, on
purpose, so archiving stays undoable.

**Re-ingest in place** — replace a document with an edited version idempotently (retracts the
prior version, then re-commits under the same identity — re-ingesting a known identity is
always a replace now, there is no `--replace` flag to pass):
```bash
artmind ingest sync EDITED_FILE --domain YOUR_DOMAIN
```

**Registry gone stale or wiped** (e.g. after restoring a graph-only snapshot,
or a doubt about whether path↔id lookups are current)? Rebuild it from vault
frontmatter rather than re-ingesting:
```bash
artmind docs reindex --compact
```
Safe to run any time — the registry is never authoritative, so this always
just re-derives it from what the vault actually says. csv/xlsx can't be
rebuilt this way (their identity is path-only); re-ingest them directly to
re-register.

---

### I. A structured table's classification is stuck, or you want to re-classify it

Structured files (csv/xlsx, ingested with the same `artmind ingest sync FILE --domain DOMAIN`)
register as **tables**, not documents. After registration each table gets classified in three
independent steps, each tracking its own status — `pending` | `ok` | `failed`:

| Step | Answers | Confirm with |
|---|---|---|
| `grain` | do these rows record facts (`instance`/`lookup`) or assert rules (`normative`)? | `db grain TABLE --set` |
| `bridge` | which columns' *values* are worth searching the graph for? | (CLI-only today) |
| `mapping` | which columns denote instances of which graph entity class? | `db mappings TABLE confirm ...` |

**Check the current status:**
```bash
artmind db schema TABLE --domain DOMAIN     # includes grain_status / bridge_status / mapping_status
artmind db bridge --domain DOMAIN           # same three, for every table at once
```

**Any step `failed`, or still `pending` on an older table — re-run just what's needed:**
```bash
artmind db propose TABLE --domain DOMAIN
```
By default this retries only steps that aren't already `ok`, leaving succeeded ones alone —
the same "resume only what's broken" shape `extract-kg` has for a document's chunks
(Situation D.1). It is safe to re-run; confirmed values are never overwritten.

**Re-run one specific step** (e.g. after editing the domain schema, which only affects mapping):
```bash
artmind db propose TABLE --domain DOMAIN --step mapping
```

**Force a step that already succeeded** (a second opinion, or post-schema-edit):
```bash
artmind db propose TABLE --domain DOMAIN --step mapping --redo
```
Without `--redo` an already-`ok` step is skipped, so this is the only way to re-ask it.

**Diagnosing a `failed` step:** no error text is stored — by design, matching the rest of the
pipeline. Check the logs:
```bash
tail -50 logs/artmind_ingestion.log | grep -i "propose_table_semantics"
```

Common causes:
- **`mapping` fails with "no schema file (or no entities_prompt)"** — the domain has no schema
  YAML, or it's a dotted sub-domain that hasn't been harmonized. Run `artmind domains list` to
  check; use `/artmind-create-schema` to create one, or `artmind domains harmonize`.
- **All three `failed`** — the LLM was unreachable at the time. The ingest hook is best-effort
  and only logs a warning rather than failing the load, so the table registered fine and just
  needs `db propose`.
- **All three still `pending` on a long-registered table** — it predates this pipeline. The
  migration defaults every existing table to `pending`; it cannot know whether a step had
  effectively already happened. `db propose` is the fix.

**Classify a whole domain at once:** the admin UI's *Structured data* tab has a
"Classify all unclassified" button (`artmind admin-ui`, then open `/dashboard`), which runs the
same code this command does. The three dots per table row are grain/bridge/mapping status.

**Once the steps report `ok`, the proposals still need a human.** Everything lands
*unconfirmed*. Judging whether a proposed grain or `column → entity_class` mapping is actually
*right* — and confirming or rejecting it — is `/artmind-curate`'s Workflow D, not this skill.
This skill gets a stuck step running again; that one adjudicates what it produced.

---

## Full Pipeline Reference (happy path)

```
1. artmind domains list                              # confirm domain exists
2. artmind ingest sync FOLDER --domain DOMAIN        # ingest (or async + dashboard)
3. artmind projection synthesize --domain DOMAIN     # deliberate second step of a bulk load
4. artmind ingest refine-graph --domain DOMAIN \
     --dry-run --output merges.json                  # optional: propose duplicate merges
5. artmind ingest refine-graph --from-file merges.json  # writes proposals, doesn't merge
6. artmind sameas approve <proposal_id>              # artmind-curate reviews, then approves
```

**Team / import workflow:**
```
1. artmind ingest pull-kg --repo ... --repo-path ... --domain DOMAIN
2. artmind ingest write-to-graph --folder data/kg/DOMAIN
3. artmind ingest refine-graph --domain DOMAIN --dry-run   # propose duplicate merges
```

**Re-run / repair workflow:**
```
1. artmind ingest extract-kg DOC --domain DOMAIN     # re-run LLM extraction
2. artmind ingest write-to-graph DOC --domain DOMAIN # push to Neo4j
```

---

## Common Gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `No document sub-folders with document.json found` | Passed a high-level folder (e.g. `data/kg`) instead of the domain folder | The CLI will now search recursively and confirm — just proceed, or pass `--folder data/kg/DOMAIN` directly |
| `Document not found in registry` | Document was never ingested with `sync`/`async` | Run `artmind ingest sync FILE --domain DOMAIN` first |
| `No chunks found` | `sync` hasn't been run yet, or only `async` was submitted but not completed | Check with `artmind ingest jobs`; if needed run `sync` |
| Extraction completes in seconds with 0 entities and all chunks failed | Too many concurrent jobs — Ollama cloud rate limiter rejected requests | Run max 5 jobs at a time; re-run failed docs with `extract-kg` |
| Job stuck in `processing`, or crawling with repeated `Connection error` on chunks | Worker crashed or hit transient LLM-provider connection errors on a large document | Kill the worker (safe — progress is per-chunk/per-step durable), then run `artmind ingest extract-kg DOC --domain DOMAIN` on the specific file — **not** `retry-job`, which ignores files stuck at `processing`. See Situation D.1. |
| Empty graph after Neo4j restart | Neo4j was ephemeral and lost data | Run `artmind session initiate` to restore from snapshot, or `write-to-graph` if JSON exists |
| Duplicate entities after merging domains | Same-as review not run | Run `refine-graph --dry-run` to propose, then `sameas approve` (see Situation G) |
| A structured table shows `mapping_status`/`bridge_status`/`grain_status` = `failed`, or a long-registered table is still all `pending` | Best-effort LLM call failed at ingest time (unreachable model), or the table predates the classification pipeline | `artmind db propose TABLE --domain DOMAIN` — retries only the steps not already `ok`. See Situation I. |
| `db propose` fails on the mapping step with "no schema file" | Domain has no schema YAML, or a dotted sub-domain was never harmonized | `artmind domains harmonize`, or create one via `/artmind-create-schema`. Grain and bridge still succeed independently. |

---

## Getting Help

```bash
artmind ingest --help                  # list all ingest sub-commands
artmind ingest COMMAND --help          # help for a specific command
```
