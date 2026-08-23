"""Runtime paths for artmind, rooted at a config-driven run folder.

Two independent roots decouple artmind from the source checkout:

- ``ARTMIND_HOME`` (default ``~/.artmind``) — the *run folder*: config (.env),
  skills, domain schemas, and logs. Needed by every command (query/serve/web-ui).
- ``ARTMIND_DATA_DIR`` (default ``~/artmind_data``) — *ingestion-only* data
  (originals, markdowns, registry db, jobs, kg staging, snapshots). A pure
  query/serve host never touches it and may omit it entirely.

``ARTMIND_HOME`` cannot itself live in ``.env`` (it is how we *find* ``.env``),
so it comes from the real environment or the default. The run-folder ``.env``
is loaded here at import so ``ARTMIND_DATA_DIR`` and other config are visible
when the constants below are computed; already-set environment variables win.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ── run folder root ────────────────────────────────────────────────────────────
_SELF_DIR = Path(__file__).resolve().parent  # repo root (dev) or site-packages (wheel)

ARTMIND_HOME = Path(
    os.environ.get("ARTMIND_HOME") or (Path.home() / ".artmind")
).expanduser().resolve()

# Load .env early: prefer the run folder; fall back to a repo-local .env so the
# editable/dev checkout keeps working without exporting ARTMIND_HOME. Real
# environment variables are never overridden.
ENV_FILE = ARTMIND_HOME / ".env"
for _candidate in (ARTMIND_HOME / ".env", _SELF_DIR / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)
        ENV_FILE = _candidate
        break

# ── ingestion data root (query never touches this) ─────────────────────────────
ARTMIND_DATA_DIR = Path(
    os.environ.get("ARTMIND_DATA_DIR") or (Path.home() / "artmind_data")
).expanduser().resolve()

# ── vault root (authoritative markdown; source of stable document identity) ─────
# The externally-editable markdown tree the canvas UX watches. Optional: when set,
# a document's identity (``logical_id``) keys off its path *relative to this root*
# so an edited/re-ingested file is recognised as the same document. When unset,
# identity falls back to the casefolded basename. Never itself lives in ``.env``
# only — a real env var wins, matching ARTMIND_HOME/ARTMIND_DATA_DIR.
_vault = os.environ.get("ARTMIND_VAULT_DIR")
ARTMIND_VAULT_DIR = Path(_vault).expanduser().resolve() if _vault else None

# ── package-shipped seed defaults (read-only; copied into the run folder) ──────
PACKAGE_SKILLS_DIR = _SELF_DIR / "artmind" / "skills"
PACKAGE_SCHEMAS_DIR = _SELF_DIR / "artmind" / "domains" / "schemas"
# One level ABOVE domains/schemas/ -- deliberately outside every `*_schema.yaml`
# glob (cli.py's `_get_available_domains`, harmonizer.py's `harmonize_all`,
# schema_reference.py's `list_schema_families`/`find_family_schemas`), so it is
# never enumerated as if it were a domain named "_meta".
PACKAGE_META_YAML = _SELF_DIR / "artmind" / "domains" / "meta.yaml"
PACKAGE_ENV_EXAMPLE = _SELF_DIR / "artmind" / "env.example"
# opencode/ACP persona; opencode reads .opencode/agent/ (and .claude/skills/)
# relative to its cwd, which is the run folder.
PACKAGE_OPENCODE_DIR = _SELF_DIR / "artmind" / "opencode"

# ``PROJECT_ROOT`` retained for backward compatibility (worker.py, ingest.py).
PROJECT_ROOT = _SELF_DIR

# ── config / query side (under ARTMIND_HOME) ───────────────────────────────────
DOMAIN_SCHEMAS_DIR = ARTMIND_HOME / "domains" / "schemas"
DOMAIN_META_PATH = ARTMIND_HOME / "domains" / "meta.yaml"
LOGS_DIR = ARTMIND_HOME / "logs"
INGEST_LOG_FILE = LOGS_DIR / "artmind_ingestion.log"
QUERY_LOG_FILE = LOGS_DIR / "artmind_query.log"
LLM_CALLS_LOG_FILE = LOGS_DIR / "llm_calls.log"
WORKER_LOG = LOGS_DIR / "artmind_worker.log"

# ── ingestion side (under ARTMIND_DATA_DIR) ────────────────────────────────────
DATA_DIR = ARTMIND_DATA_DIR
DOCUMENTS_DIR = DATA_DIR / "documents"
ORIGINALS_DIR = DOCUMENTS_DIR / "originals"
MARKDOWNS_DIR = DOCUMENTS_DIR / "markdowns"
DB_PATH = DATA_DIR / "document_registry.db"
JOBS_DIR = DATA_DIR / "ingestion_jobs"
KG_DIR = DATA_DIR / "kg"
REFINE_DIR = DATA_DIR / "refine"
GRAPH_SNAPSHOT_DIR = DATA_DIR / "graph_snapshot"
WORKER_PID_FILE = DATA_DIR / "worker.pid"
STRUCTURED_DIR = DATA_DIR / "structured"   # DuckDB catalog + <domain>/<table>.parquet
STRUCTURED_SNAPSHOT_DIR = DATA_DIR / "structured_snapshot"   # db backup/restore .tar.gz files
