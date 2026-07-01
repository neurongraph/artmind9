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

### E. Retry a failed async job

```bash
artmind ingest retry-job JOB_ID
```

This re-queues only the failed files and restarts the worker.

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
| Job stuck in `processing` | Worker crashed | Run `artmind ingest retry-job JOB_ID` |
| Empty graph after Neo4j restart | Neo4j was ephemeral and lost data | Run `artmind session initiate` to restore from snapshot, or `write-to-graph` if JSON exists |
| Duplicate entities after merging domains | Entity resolution not run | Run `refine-graph --dry-run` then apply |

---

## Getting Help

```bash
artmind ingest --help                  # list all ingest sub-commands
artmind ingest COMMAND --help          # help for a specific command
```
