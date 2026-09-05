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

from dotenv import dotenv_values, load_dotenv

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

# ARTMIND_AGENT_CWD: the directory an agent process (claude-sdk, ACP) should be
# run in so it resolves `.claude/skills/` and `.opencode/agent/` correctly.
# NOT the same as ARTMIND_HOME once a vault is in play: `.claude/skills/` and
# `.opencode/agent/` are symlinked into the *vault root* (VaultLayout.skills_dir,
# VaultLayout.opencode_agents_dir), one level above `ARTMIND_HOME` (the vault's
# `.artmind/`) -- see docs/vault.md, "Skills and agent modes". Outside a vault,
# both live directly under the machine home, so ARTMIND_HOME is already correct.
ARTMIND_AGENT_CWD = _LAYOUT.root if _LAYOUT is not None else ARTMIND_HOME

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

# Keys that belong to a VAULT's own config.env, never the machine-wide one
# (docs/vault.md, "Machine-level config") -- once a real vault is in play,
# these "disappear as concepts... every one of them is now a position inside
# the vault." The load-order comment above promises the vault's config.env
# overrides the machine's, but that only holds for a key BOTH files set; a
# vault's own config.env leaves ARTMIND_DATA_DIR/VAULT_DIR/ARCHIVE_DIR
# commented out by default (correctly deferring to the vault-relative
# default), so a machine config.env carrying one anyway -- e.g. copied
# wholesale from an old pre-vault .env, exactly what `just dev-install`
# seeded before this file existed -- silently wins with no vault-side value
# to beat it: ingestion writes outside the vault, `git add` then fails
# because the file landed outside the repo, with no diagnostic pointing at
# the actual cause. Filtered out when loading MACHINE_CONFIG_ENV specifically
# (never the vault's own config.env, which is exactly where these belong).
_VAULT_ONLY_ENV_KEYS = (
    "ARTMIND_KG_NEO4J_URI", "ARTMIND_KG_NEO4J_USERNAME",
    "ARTMIND_KG_NEO4J_PASSWORD", "ARTMIND_KG_NEO4J_DATABASE",
    "ARTMIND_DATA_DIR", "ARTMIND_VAULT_DIR", "ARTMIND_ARCHIVE_DIR",
)

LOADED_ENV_FILES: "list[Path]" = []
_candidates = [
    ARTMIND_HOME / "config.env",   # this vault
    ARTMIND_HOME / ".env",         # legacy run folder, still honoured
    MACHINE_CONFIG_ENV,            # machine-wide identity
]
if os.environ.get("ARTMIND_ALLOW_REPO_ENV", "").strip().lower() in ("1", "true", "yes"):
    _candidates.append(_SELF_DIR / ".env")
for _candidate in _candidates:
    if not _candidate.is_file() or _candidate in LOADED_ENV_FILES:
        continue
    if _LAYOUT is not None and _candidate == MACHINE_CONFIG_ENV:
        _values = dotenv_values(_candidate)
        _leaked = sorted(
            k for k in _VAULT_ONLY_ENV_KEYS if _values.get(k) and k not in os.environ
        )
        if _leaked:
            print(
                f"artmind: ignoring vault-scoped key(s) in machine-wide {_candidate} "
                f"while inside a vault: {', '.join(_leaked)} -- these belong in this "
                f"vault's own .artmind/config.env instead (docs/vault.md, "
                f"\"Machine-level config\").",
                file=sys.stderr,
            )
        for _k, _v in _values.items():
            if _k not in _VAULT_ONLY_ENV_KEYS and _v is not None and _k not in os.environ:
                os.environ[_k] = _v
    else:
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
# relative to its cwd, i.e. ARTMIND_AGENT_CWD -- the run folder outside a
# vault, the vault root (symlinked in by scaffold_vault) inside one.
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
