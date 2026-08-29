"""Runtime paths for artmind, rooted at a config-driven run folder.

Two independent roots decouple artmind from the source checkout:

- ``ARTMIND_HOME`` (default ``~/.artmind``) — the *run folder*: config (.env),
  skills, domain schemas, and logs. Needed by every command (query/serve/web-ui).
- ``ARTMIND_DATA_DIR`` (default ``~/artmind_data``) — *ingestion-only* data
  (originals, markdowns, registry db, jobs, kg staging, snapshots). A pure
  query/serve host never touches it and may omit it entirely.

Which run folder is active comes from *workspace* resolution
(docs/workspaces.md): a workspace is one knowledge base and everything scoped to
it. ``ARTMIND_HOME`` remains the raw escape hatch that bypasses workspaces
entirely, and an install that has never created one keeps the pre-workspace
layout (``~/.artmind`` *is* the run folder) unchanged.

Config is loaded here at import, MOST SPECIFIC FIRST, so ``ARTMIND_DATA_DIR``
and the rest are visible when the constants below are computed. Real
environment variables are never overridden.
"""

import hashlib
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# ── the workspace container (NOT the run folder) ──────────────────────────────
_SELF_DIR = Path(__file__).resolve().parent  # repo root (dev) or site-packages (wheel)

ARTMIND_ROOT = Path(
    os.environ.get("ARTMIND_ROOT") or (Path.home() / ".artmind")
).expanduser().resolve()

WORKSPACES_DIR = ARTMIND_ROOT / "workspaces"
WORKSPACE_POINTER = ARTMIND_ROOT / "current"
WORKSPACE_REGISTRY = ARTMIND_ROOT / "workspaces.yaml"
SHARED_ENV_FILE = ARTMIND_ROOT / "config.env"

# A workspace name becomes a directory under WORKSPACES_DIR, so it is validated
# before it is ever joined onto a path — `..` must not be able to walk out.
WORKSPACE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def valid_workspace_name(name: str) -> bool:
    return bool(name) and ".." not in name and bool(WORKSPACE_NAME_RE.fullmatch(name))


def _resolve_run_folder() -> "tuple[Path, str | None]":
    """The active run folder and workspace name (docs/workspaces.md, "Resolution").

    Precedence, highest first:

    1. ``ARTMIND_HOME`` — raw escape hatch, bypasses workspaces entirely. This is
       what CLAUDE.md documents and what the test suite sets.
    2. ``ARTMIND_WORKSPACE`` — a name.
    3. The pointer file ``$ARTMIND_ROOT/current`` — plain text, one name.
       Deliberately not the YAML registry: resolution has to stay reproducible by
       ``artmind/_entry.py``, which is stdlib-only by design and must reach the
       same answer to tell a stale daemon from a live one.
    4. ``ARTMIND_ROOT`` itself — the pre-workspace layout, where ``~/.artmind``
       *is* the run folder. An install that never adopts a workspace never leaves
       this branch, so this whole mechanism is invisible until it is used.
    """
    home = os.environ.get("ARTMIND_HOME")
    if home:
        return Path(home).expanduser().resolve(), None

    source = "ARTMIND_WORKSPACE"
    name = (os.environ.get("ARTMIND_WORKSPACE") or "").strip()
    if not name:
        source = str(WORKSPACE_POINTER)
        try:
            name = WORKSPACE_POINTER.read_text(encoding="utf-8").strip()
        except OSError:
            name = ""

    if not name:
        return ARTMIND_ROOT, None
    if not valid_workspace_name(name):
        raise ValueError(
            f"Invalid workspace name {name!r} (from {source}). Names are "
            "alphanumeric with . _ - and are used directly as directory names."
        )
    return (WORKSPACES_DIR / name).resolve(), name


ARTMIND_HOME, ARTMIND_WORKSPACE = _resolve_run_folder()

# Load config MOST SPECIFIC FIRST. ``override=False`` means an already-set key
# wins, so reading the workspace's own .env *before* the shared config.env is
# what makes the workspace override the shared value rather than the reverse.
# Real environment variables were set before either and therefore beat both.
#
# There is deliberately NO implicit fallback to a checkout-local .env
# (docs/workspaces.md, guardrail 1): it silently loaded another workspace's
# config — credentials and graph included — whenever a run folder existed
# without one of its own, which is exactly the state a newly-created workspace
# is in. ARTMIND_ALLOW_REPO_ENV=1 opts back in, for a dev clone that has not run
# `artmind init` yet.
LOADED_ENV_FILES: "list[Path]" = []
_candidates = [ARTMIND_HOME / ".env", SHARED_ENV_FILE]
if os.environ.get("ARTMIND_ALLOW_REPO_ENV", "").strip().lower() in ("1", "true", "yes"):
    _candidates.append(_SELF_DIR / ".env")
for _candidate in _candidates:
    if _candidate.is_file() and _candidate not in LOADED_ENV_FILES:
        load_dotenv(_candidate, override=False)
        LOADED_ENV_FILES.append(_candidate)

# Retained for backward compatibility. Prefer LOADED_ENV_FILES, which reports
# every file actually read rather than just the first.
ENV_FILE = LOADED_ENV_FILES[0] if LOADED_ENV_FILES else ARTMIND_HOME / ".env"


def workspace_fingerprint() -> str:
    """Identity of the workspace this process is bound to.

    Run folder *and* graph database, because either can drift on its own: a
    different run folder is a different workspace, and the same run folder whose
    .env was repointed at another database is a different graph. `artmind serve`
    reports this and `artmind/_entry.py` recomputes it before proxying, so a
    daemon started in one workspace cannot silently answer for another
    (docs/workspaces.md, guardrail 2).

    ``artmind/_entry.py`` reimplements this in pure stdlib to keep the fast path
    fast; ``test/test_workspace.py`` asserts the two agree.
    """
    database = os.environ.get("ARTMIND_KG_NEO4J_DATABASE", "") or ""
    payload = f"{ARTMIND_HOME}\n{database}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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

# ── archive root (docs archive's ONLY output; the only copy of archived content) ─
# Deliberately its own root, NOT under ARTMIND_DATA_DIR: a data-dir wipe (a
# routine, low-stakes reset per docs/stores-and-repos.md) must not destroy
# archived documents, which have no other copy anywhere. Excluded from
# `artmind snapshot` on the same principle (see unified_snapshot.py).
ARTMIND_ARCHIVE_DIR = Path(
    os.environ.get("ARTMIND_ARCHIVE_DIR") or (Path.home() / "artmind_archive")
).expanduser().resolve()

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
