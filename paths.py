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
import sys
from pathlib import Path

from dotenv import load_dotenv

# ── run folder root ────────────────────────────────────────────────────────────
_SELF_DIR = Path(__file__).resolve().parent  # repo root (dev) or site-packages (wheel)

from artmind.vault import VaultLayout, resolve_vault

# ── vault discovery ───────────────────────────────────────────────────────────
# A vault is a directory containing `.artmind/` (docs/vault.md). When we are
# inside one, every path below is a position inside it. When we are not, we fall
# back to the pre-vault layout unchanged, so existing installs and the test
# suite keep working while the migration proceeds file by file.
ARTMIND_VAULT_DIR = resolve_vault()

# ARTMIND_VAULT_DIR used to SELECT the vault; it is now an output of discovery.
# Existing .env files still set it, where it is read by nobody -- a silent no-op
# is the worst shape for a config change, so say so once, on stderr (stdout
# carries --compact JSON that callers parse).
if os.environ.get("ARTMIND_VAULT_DIR"):
    print(
        "artmind: ARTMIND_VAULT_DIR is no longer read -- the vault is found by "
        "walking up from the current directory for .artmind/vault.yaml. "
        "Use ARTMIND_VAULT (or --vault) to point elsewhere, and remove "
        "ARTMIND_VAULT_DIR from your config.",
        file=sys.stderr,
    )
_LAYOUT = VaultLayout(ARTMIND_VAULT_DIR) if ARTMIND_VAULT_DIR else None

# ARTMIND_HOME remains the raw escape hatch, above vault discovery: CLAUDE.md
# documents it and test/conftest.py repoints it at a temp dir for the whole
# suite, which must keep working whether or not a vault is in play.
if os.environ.get("ARTMIND_HOME"):
    ARTMIND_HOME = Path(os.environ["ARTMIND_HOME"]).expanduser().resolve()
elif _LAYOUT is not None:
    ARTMIND_HOME = _LAYOUT.artmind_dir
else:
    ARTMIND_HOME = (Path.home() / ".artmind").resolve()

# ── config, loaded MOST SPECIFIC FIRST ────────────────────────────────────────
# `override=False` means an already-set key wins, so reading the vault's own
# config before the machine's is what makes the vault override the machine
# rather than the reverse. Real environment variables were set before either and
# therefore beat both.
#
# Secrets stay machine-wide because a vault is a repo you may push: the line is
# "secrets and models belong to the machine; knowledge belongs to the vault"
# (docs/vault.md).
#
# There is deliberately NO implicit fallback to a checkout-local .env: it
# silently loaded another knowledge base's config -- credentials and graph
# included -- whenever a run folder had none of its own.
MACHINE_CONFIG_DIR = (Path.home() / ".artmind").resolve()
MACHINE_CONFIG_ENV = MACHINE_CONFIG_DIR / "config.env"

LOADED_ENV_FILES: "list[Path]" = []
_candidates = [
    ARTMIND_HOME / "config.env",   # this vault
    ARTMIND_HOME / ".env",         # legacy run folder, still honoured
    MACHINE_CONFIG_ENV,            # machine-wide identity
]
if os.environ.get("ARTMIND_ALLOW_REPO_ENV", "").strip().lower() in ("1", "true", "yes"):
    _candidates.append(_SELF_DIR / ".env")
for _candidate in _candidates:
    if _candidate.is_file() and _candidate not in LOADED_ENV_FILES:
        load_dotenv(_candidate, override=False)
        LOADED_ENV_FILES.append(_candidate)

# Retained for backward compatibility. Prefer LOADED_ENV_FILES, which reports
# every file read rather than just the first.
ENV_FILE = LOADED_ENV_FILES[0] if LOADED_ENV_FILES else ARTMIND_HOME / ".env"

# ── ingestion data root (query never touches this) ─────────────────────────────
if os.environ.get("ARTMIND_DATA_DIR"):
    ARTMIND_DATA_DIR = Path(os.environ["ARTMIND_DATA_DIR"]).expanduser().resolve()
elif _LAYOUT is not None:
    ARTMIND_DATA_DIR = _LAYOUT.data_dir
else:
    ARTMIND_DATA_DIR = (Path.home() / "artmind_data").resolve()

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
