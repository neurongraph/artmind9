# Pull KG from External Repos + Batch write_to_graph

## Problem Statement
The `write_to_graph` CLI command only accepts a single document name. There are two gaps:
1. No way to batch-write multiple documents' KG JSON to Neo4j in one command.
2. No way to import KG JSON that was extracted elsewhere (e.g. by another team or region) and checked into a GitHub repo.

The `--folder` option for `write_to_graph` was already implemented. This spec covers the remaining work: the `pull-kg` command, README updates, and justfile recipes.

## Current State
- `artmind ingest write_to_graph` now supports two modes:
  - Single document: `artmind ingest write_to_graph DOCUMENT_NAME --domain DOMAIN`
  - Batch from folder: `artmind ingest write_to_graph --folder PATH [--domain DOMAIN]`
- The KG directory layout is `data/kg/<domain>/<doc_stem>/` with each document sub-folder containing `document.json`, `chunks.json`, `entities.json`, `properties.json`, `relationships.json`.
- No mechanism exists to pull KG data from external sources.

## Design

### 1. New CLI command: `artmind ingest pull-kg`

**Signature:**
```
artmind ingest pull-kg --repo <github_url> --repo-path <path_in_repo> --domain <domain>
```

**Arguments and options:**
- `--repo` (required): Git-cloneable URL (SSH or HTTPS) of the external repository.
- `--repo-path` (required): Path within the repo to the folder containing document sub-folders (e.g. `data/kg/sales_collateral`).
- `--domain` (required): Target domain name. Documents are copied into `data/kg/<domain>/`.

**Behavior:**
1. Create a temporary directory via `tempfile.mkdtemp()`.
2. Run `git clone --no-checkout --depth=1 <repo>` into the temp dir.
3. Run `git sparse-checkout set <repo-path>` inside the cloned repo.
4. Run `git checkout` to materialize only the target folder.
5. Scan `<temp>/<repo-path>/` for immediate sub-directories containing `document.json`.
6. **Conflict check:** Compare sub-folder names against existing folders in `data/kg/<domain>/`. If any overlap, abort with an error listing all conflicting names.
7. Copy all non-conflicting document sub-folders into `data/kg/<domain>/` using `shutil.copytree`.
8. Clean up the temp directory.
9. Log a summary: number of documents pulled, domain, source repo.

**Authentication:**
- Primary: existing git credentials (SSH keys, credential helpers, `gh auth`).
- Fallback: if `GITHUB_TOKEN` env var is set, inject it as an HTTPS credential for the clone by rewriting the URL to `https://<token>@github.com/...`.

**Error handling:**
- Git not installed → clear error message.
- Clone fails (auth, network) → report the git error, clean up temp dir.
- No document sub-folders found at repo-path → error with the scanned path.
- Conflict detected → abort, list all conflicting folder names, clean up.

### 2. New module: `artmind/kg_pull.py`

Isolates the git sparse-checkout and copy logic from `cli.py`. Exports a single function:

```python
def pull_kg(repo_url: str, repo_path: str, domain: str) -> dict:
    """Pull KG JSON sub-folders from an external git repo into local data/kg/<domain>/.

    Returns a summary dict with keys: pulled_count, domain, repo_url, conflicts (empty list on success).
    Raises RuntimeError on git failures or conflicts.
    """
```

### 3. Justfile recipes

**New recipes:**
- `ingest-write-to-graph-folder folder domain=""`: Batch write from a folder. Domain defaults to folder name.
- `ingest-pull-kg repo repo_path domain`: Pull KG from external repo.

**Existing recipe unchanged:**
- `ingest-write-to-graph document domain`: Single-document write (no changes).

### 4. README updates

**Section: "Re-running extraction"** — expand the `write_to_graph` docs to show both modes (single document and `--folder`).

**New section: "Importing KG from external repositories"** (after "Re-running extraction") — explains:
- Background: KG JSON can be extracted elsewhere and checked into a GitHub repo as a shared artifact.
- Use case: a regional team extracts `sales_collateral` KG and pushes to their repo; you pull their domain folder into your local instance.
- Workflow: `pull-kg` → `write_to_graph --folder` → optionally `refine-graph`.
- Full example with commands.

**Section: "Justfile recipes"** — add the two new recipes to the list.

**Section: "Project layout"** — add `kg_pull.py` entry.

### 5. Typical workflow

```bash
# 1. Pull sales_collateral KG from the APAC team's repo
artmind ingest pull-kg \
  --repo git@github.com:acme/apac-kg-store.git \
  --repo-path data/kg/sales_collateral \
  --domain sales_collateral

# 2. Write all pulled documents to Neo4j
artmind ingest write_to_graph --folder data/kg/sales_collateral

# 3. Optionally resolve duplicate entities across the merged data
artmind ingest refine-graph --domain sales_collateral --dry-run
```
