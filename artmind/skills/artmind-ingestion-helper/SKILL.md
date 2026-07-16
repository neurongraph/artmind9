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
Then track it with `artmind ingest dashboard` (live) or `artmind ingest job-status JOB_ID`.

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
# Live dashboard (all jobs):
artmind ingest dashboard

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
   sqlite3 data/document_registry.db \
     "SELECT chunk_seq, entities_status FROM kg_chunk_status WHERE doc_sha256='SHA256' ORDER BY chunk_seq LIMIT 60;"
   ```
   Get `SHA256` via `SELECT sha256 FROM documents WHERE filename = 'DOCUMENT_NAME';` in the same DB. Chunks already `ok` should stay `ok`; only chunks previously `failed`/unattempted should change. In the log itself, a genuine skip shows only the `Chunk N/743 (...)` header line with no `entities`/`properties`/`relationships` sub-lines — real work shows all three (or a silent `skipped` if the chunk legitimately had zero entities).

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

# 3. Optionally merge duplicate entities
artmind ingest refine-graph --domain YOUR_DOMAIN --dry-run
```

**Conflict:** If a document sub-folder already exists locally, `pull-kg` aborts and lists conflicts. Resolve by renaming or deleting the local copy, then re-run.

**Auth:** Uses your existing Git credentials (SSH keys / credential helpers). Set `GITHUB_TOKEN` for HTTPS fallback.

---

### G. Resolve duplicate / similar entities (graph refinement)

After ingesting several documents, entity names may duplicate (e.g. "Holmes", "Sherlock Holmes"). Fix with:

```bash
# Step 1 — dry-run: compute proposals, save for review
artmind ingest refine-graph --domain YOUR_DOMAIN --dry-run --output merges.json

# Step 2 — review merges.json; edit if needed

# Step 3 — apply the reviewed proposals
artmind ingest refine-graph --from-file merges.json
```

**Focused refinement** (only specific entities):
```bash
artmind ingest refine-graph --domain YOUR_DOMAIN \
  --filter "Holmes,Watson,Moriarty" \
  --dry-run --output merges.json
```

**Backfill missing embeddings** (needed for entity-resolve queries):
```bash
artmind ingest embed-entities --domain YOUR_DOMAIN
```

---

### H. Remove a document from the graph

```bash
artmind docs clean --domain YOUR_DOMAIN DOCUMENT_NAME
```

---

## Full Pipeline Reference (happy path)

```
1. artmind domains list                              # confirm domain exists
2. artmind ingest sync FILE --domain DOMAIN          # ingest (or async + dashboard)
3. artmind ingest refine-graph --domain DOMAIN \
     --dry-run --output merges.json                  # optional: merge duplicates
4. artmind ingest refine-graph --from-file merges.json
```

**Team / import workflow:**
```
1. artmind ingest pull-kg --repo ... --repo-path ... --domain DOMAIN
2. artmind ingest write-to-graph --folder data/kg/DOMAIN
3. artmind ingest refine-graph --domain DOMAIN --dry-run
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
| `No chunks found` | `sync` hasn't been run yet, or only `async` was submitted but not completed | Check with `artmind ingest dashboard`; if needed run `sync` |
| Extraction completes in seconds with 0 entities and all chunks failed | Too many concurrent jobs — Ollama cloud rate limiter rejected requests | Run max 5 jobs at a time; re-run failed docs with `extract-kg` |
| Job stuck in `processing`, or crawling with repeated `Connection error` on chunks | Worker crashed or hit transient LLM-provider connection errors on a large document | Kill the worker (safe — progress is per-chunk/per-step durable), then run `artmind ingest extract-kg DOC --domain DOMAIN` on the specific file — **not** `retry-job`, which ignores files stuck at `processing`. See Situation D.1. |
| Empty graph after Neo4j restart | Neo4j was ephemeral and lost data | Run `artmind session initiate` to restore from snapshot, or `write-to-graph` if JSON exists |
| Duplicate entities after merging domains | Entity resolution not run | Run `refine-graph --dry-run` then apply |

---

## Getting Help

```bash
artmind ingest --help                  # list all ingest sub-commands
artmind ingest COMMAND --help          # help for a specific command
```
