import shutil
from pathlib import Path

from artmind.db import _init_db
from artmind.graph_query import neo4j_session
from artmind.schema_validate import validate_all_or_raise
from artmind.vault import VaultLayout, resolve_vault, write_gitignore
from paths import (
    ARTMIND_DATA_DIR,
    ARTMIND_HOME,
    DOMAIN_META_PATH,
    DOMAIN_SCHEMAS_DIR,
    GRAPH_SNAPSHOT_DIR,
    JOBS_DIR,
    KG_DIR,
    LOGS_DIR,
    MACHINE_CONFIG_DIR,
    MACHINE_CONFIG_ENV,
    MARKDOWNS_DIR,
    ORIGINALS_DIR,
    PACKAGE_ENV_EXAMPLE,
    PACKAGE_META_YAML,
    PACKAGE_OPENCODE_DIR,
    PACKAGE_SCHEMAS_DIR,
    PACKAGE_SKILLS_DIR,
    REFINE_DIR,
    STRUCTURED_DIR,
    STRUCTURED_SNAPSHOT_DIR,
)
from utils.functions import load_env


def _seed_tree(src, dest, *, overwrite: bool = False) -> int:
    """Copy each top-level entry of ``src`` into ``dest``. Returns entries written.

    Two policies, by what the tree holds:

    - ``overwrite=False`` — *user data* (``.env`` only). Existing entries are
      skipped so local edits survive re-running init.
    - ``overwrite=True`` — *package assets* (skills, opencode persona, domain
      schemas). These are shipped alongside the code with the package as their
      source of truth, so a reinstall must replace whatever the run folder
      holds; skipping them would freeze them at whatever version first seeded
      the run folder, and edits made in ``artmind/skills/`` or
      ``artmind/domains/schemas/`` would silently never reach the chat agent
      or the CLI.

    Entries are replaced wholesale, not merged, so a file deleted from a skill
    also disappears from the run folder. Names the package does not ship are left
    alone either way, so a domain added via `artmind domains add` (never
    committed to the package) is never pruned — but renaming or removing a
    schema in the package leaves its old run-folder copy in place too, since
    that name simply no longer appears in `src.iterdir()`.
    """
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in src.iterdir():
        if entry.name == ".DS_Store":
            continue
        target = dest / entry.name
        if target.exists():
            if not overwrite:
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
        written += 1
    return written


# Keys that belong to a VAULT's own config.env (Neo4j connection, and the
# legacy path/location overrides the vault model now derives from the vault
# itself), not the machine-wide one. Mirrors docs/vault.md's "Machine-level
# config" table. Matched by literal line-start, same as the manual
# `grep -vE` fix docs/vault.md documented before this was automated (a
# commented-out `# ARTMIND_DATA_DIR=...` line is deliberately left alone).
#
# `artmind/env.example` itself no longer carries any of these -- it is
# machine-scoped by construction now, so this filter is a no-op on the
# "seeded fresh" path below. It still earns its keep on the "migrate an
# older install's legacy .env" path, where that file predates the split and
# genuinely does mix both scopes together.
_VAULT_SCOPED_ENV_PREFIXES = (
    "ARTMIND_KG_NEO4J_",
    "ARTMIND_DATA_DIR",
    "ARTMIND_VAULT_DIR",
    "ARTMIND_ARCHIVE_DIR",
)


def ensure_machine_config() -> dict:
    """Make sure ``~/.artmind/config.env`` -- the file every vault's
    machine-wide identity (LLM provider, API keys, embedding model) actually
    loads from (``paths.MACHINE_CONFIG_ENV``) -- exists and holds something.

    Deliberately keyed on ``paths.MACHINE_CONFIG_DIR`` (``Path.home() /
    ".artmind"``), NOT ``ARTMIND_HOME`` -- machine identity is the one thing
    shared by every vault on this machine, so this is safe and correct to call
    unconditionally, whether or not the caller happens to be vault-resident
    right now.

    Called from both ``scaffold_run_folder`` (``just dev-install`` /
    ``artmind setup``) and ``scaffold_vault`` (``artmind init``), so whichever
    of the two runs first is the one that actually creates it. Idempotent and
    never destructive -- three cases, in priority order:

    1. ``~/.artmind/config.env`` already exists -> left alone, action "ok".
    2. it's missing, but the legacy ``~/.artmind/.env`` (seeded by an older
       ``artmind9`` on this machine) holds real settings -> migrate them,
       stripping the vault-scoped keys above (their home is now a vault's own
       ``config.env``), action "migrated". Checked first so a returning
       user's actual settings always win over the bare template below.
    3. neither exists (a genuinely fresh machine) -> seed fresh from the
       package's ``env.example`` template, action "seeded" -- this is what
       makes a bare ``just dev-install`` produce a working, if default-filled,
       ``~/.artmind/config.env`` with no further step required.
    """
    if MACHINE_CONFIG_ENV.exists():
        return {"action": "ok", "path": str(MACHINE_CONFIG_ENV)}

    legacy_env = MACHINE_CONFIG_DIR / ".env"
    if legacy_env.is_file():
        source, action = legacy_env, "migrated"
    elif PACKAGE_ENV_EXAMPLE.is_file():
        source, action = PACKAGE_ENV_EXAMPLE, "seeded"
    else:
        return {"action": "missing", "path": str(MACHINE_CONFIG_ENV)}

    MACHINE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    filtered = "".join(
        line for line in source.read_text(encoding="utf-8").splitlines(keepends=True)
        if not line.startswith(_VAULT_SCOPED_ENV_PREFIXES)
    )
    MACHINE_CONFIG_ENV.write_text(filtered, encoding="utf-8")
    MACHINE_CONFIG_ENV.chmod(0o600)
    return {"action": action, "path": str(MACHINE_CONFIG_ENV), "source": str(source)}


def scaffold_run_folder() -> dict:
    """Create the run folder + data dirs and seed machine config, skills and schemas.

    Pure filesystem work — needs no Neo4j/config, so it is safe to run right
    after install, before the user has filled in ``~/.artmind/config.env``.

    Idempotent, but not inert: package assets (skills, opencode, domain schemas)
    are refreshed from the package on every run, while user data
    (``~/.artmind/config.env``, via ``ensure_machine_config``) is only seeded
    when absent. See ``_seed_tree``.

    The ``.claude/skills/`` and ``.opencode/`` seeds are skipped when
    ``ARTMIND_HOME`` is a *vault's* own ``.artmind/`` (i.e. this process is
    running inside a vault, not the machine home) — both have the same reason,
    not just belt-and-braces caution. Machine config is NOT gated this way:
    ``ensure_machine_config`` targets ``Path.home()`` directly regardless of
    ``ARTMIND_HOME``, so it is correct to call from inside a vault too (see its
    own docstring).

    - ``.claude/skills/`` and ``.opencode/agent/`` both live one level ABOVE
      ``ARTMIND_HOME`` when it is a vault's ``.artmind/`` — at
      ``<vault>/.claude/skills/`` and ``<vault>/.opencode/agent/``
      (``VaultLayout.skills_dir``/``opencode_agents_dir``, symlinked by
      ``scaffold_vault``/``init``, same as ``ARTMIND_AGENT_CWD`` in
      ``paths.py``), NOT ``<vault>/.artmind/.claude/skills/`` or
      ``<vault>/.artmind/.opencode/``, which is where this function would
      otherwise put them (``ARTMIND_HOME`` being the vault's ``.artmind/``).
      Seeding here would create a second, un-symlinked copy at the wrong path
      — one the vault's ``.gitignore`` (``.claude/skills/artmind-*`` and
      ``.opencode/agent/artmind*.md``, both written relative to the vault
      root) does not match, so it would get committed as vault content though
      it is a reproducible package asset. And a session run with cwd at that
      wrong path would never find either — see ``ARTMIND_AGENT_CWD``.

    See ``docs/vault.md``.
    """
    skills_dest = ARTMIND_HOME / ".claude" / "skills"
    opencode_dest = ARTMIND_HOME / ".opencode"

    vault_dir = resolve_vault()
    home_is_vault_resident = (
        vault_dir is not None and ARTMIND_HOME == VaultLayout(vault_dir).artmind_dir
    )

    directories = [
        ARTMIND_HOME,
        DOMAIN_SCHEMAS_DIR,
        LOGS_DIR,
        ARTMIND_DATA_DIR,
        ORIGINALS_DIR,
        MARKDOWNS_DIR,
        JOBS_DIR,
        KG_DIR,
        REFINE_DIR,
        GRAPH_SNAPSHOT_DIR,
        STRUCTURED_DIR,
        STRUCTURED_SNAPSHOT_DIR,
    ]
    if not home_is_vault_resident:
        directories += [skills_dest, opencode_dest]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    machine_config = ensure_machine_config()

    # Package assets: always refreshed — the package is their source of truth.
    # (Skills/opencode skipped entirely when vault-resident, per the docstring.)
    skills_refreshed = (
        0 if home_is_vault_resident else _seed_tree(PACKAGE_SKILLS_DIR, skills_dest, overwrite=True)
    )
    opencode_refreshed = (
        0 if home_is_vault_resident else _seed_tree(PACKAGE_OPENCODE_DIR, opencode_dest, overwrite=True)
    )
    schemas_copied = _seed_tree(PACKAGE_SCHEMAS_DIR, DOMAIN_SCHEMAS_DIR, overwrite=True)

    # meta.yaml is a single file, not a tree -- seed it the same "package asset,
    # always refreshed" way as skills/opencode/schemas above.
    meta_refreshed = 0
    if PACKAGE_META_YAML.is_file():
        shutil.copy2(PACKAGE_META_YAML, DOMAIN_META_PATH)
        meta_refreshed = 1

    # Fail loudly (Phase 1): a schema missing a mandatory `kind`, or still on
    # the pre-redesign entity_types list, must stop `init` here rather than
    # surface later as a confusing extraction failure.
    validate_all_or_raise()

    return {
        "run_folder": str(ARTMIND_HOME),
        "data_dir": str(ARTMIND_DATA_DIR),
        "machine_config": machine_config,
        "skills_refreshed": skills_refreshed,
        "schemas_copied": schemas_copied,
        "opencode_refreshed": opencode_refreshed,
        "meta_refreshed": meta_refreshed,
    }


# Which schemas a NEW vault starts with. The `banking.*` family is a demo
# corpus's schemas, not a default every knowledge base should inherit
# (docs/vault.md, "Schemas").
STARTER_SCHEMAS = ("general", "personal_journal")

_STARTER_VAULT_YAML = """\
# artmind ingest manifest (docs/vault.md).
#
# `mappings` does two jobs: it says which domain governs a path's extraction,
# AND whether to ingest it at all. An unmapped path is never ingested -- so an
# attachments folder needs no ignore rule, and an unmapped Inbox/ is a drafting
# area where MOVING a note into a mapped folder is what says "this is ready".
#
# First match wins, so put a specific rule above a general one. Paths are globs
# relative to the vault root; `**` matches any depth.
#
# Domain precedence, highest first:
#   --setDomain  >  the file's own _domain frontmatter  >  a mapping  >  --domain
ingest:
  # manual | commit | schedule. Only `manual` acts today. Default manual:
  # nobody should discover automatic LLM spend by surprise.
  trigger: manual
  mappings: []
  #  - path: notes/**
  #    domain: personal_journal
  #  - path: scans/**
  #    domain: general
"""


# A vault's own config holds ONLY what is scoped to this knowledge base
# (docs/vault.md, "Machine-level config"). Deliberately NOT seeded from
# `env.example`: that is the machine-level template, and its uncommented
# `ARTMIND_DATA_DIR=~/artmind_data` would be loaded before paths.py's
# vault-relative default, sending every new vault's data to one shared
# directory -- precisely the coupling the vault model removes.
_STARTER_CONFIG_ENV = """\
# artmind — configuration for THIS vault. Gitignored: it holds a password.
#
# Shared identity (LLM provider, API keys, embedding + agent models) lives in
# ~/.artmind/config.env and is inherited by every vault. Anything set here
# overrides it, and a real environment variable overrides both.

# ── this vault's graph ────────────────────────────────────────────────────────
ARTMIND_KG_NEO4J_URI=neo4j://127.0.0.1:7687
ARTMIND_KG_NEO4J_USERNAME=neo4j
ARTMIND_KG_NEO4J_PASSWORD=
ARTMIND_KG_NEO4J_DATABASE=neo4j

# ── optional ──────────────────────────────────────────────────────────────────
# Push the vault's git repo after artmind commits frontmatter. Leave unset if
# something else (e.g. the Obsidian Git plugin) already owns pushing.
# ARTMIND_VAULT_GIT_PUSH=1

# Relocate derived data out of the vault. Only needed if the vault lives on a
# sync service that would choke on KG staging and snapshots; the default is
# <vault>/.artmind/data.
# ARTMIND_DATA_DIR=/somewhere/else
"""


def scaffold_vault(root: Path) -> dict:
    """Make `root` an artmind vault. Idempotent, and never destructive.

    Package assets are seeded ONLY when absent. This inverts
    `scaffold_run_folder`'s overwrite-always policy for schemas, which was safe
    when one run folder was reseeded from the package but would now clobber
    hand-authored vault schemas (docs/vault.md, "Schemas"). Skills keep their
    always-current property a different way -- they are symlinked to the
    installed copy rather than copied.
    """
    root = Path(root).expanduser().resolve()
    layout = VaultLayout(root)

    for directory in (
        layout.artmind_dir, layout.domains_dir, layout.schemas_dir,
        layout.data_dir, layout.kg_dir, layout.originals_dir, layout.chunks_dir,
        layout.structured_dir, layout.snapshots_dir, layout.jobs_dir,
        layout.refine_dir, layout.logs_dir, layout.skills_dir,
        layout.opencode_agents_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    seeded_schemas: list[str] = []
    for src in sorted(PACKAGE_SCHEMAS_DIR.glob("*_schema.yaml")):
        name = src.name.removesuffix("_schema.yaml")
        if name not in STARTER_SCHEMAS:
            continue
        dest = layout.schemas_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        seeded_schemas.append(name)

    if PACKAGE_META_YAML.is_file() and not layout.meta_yaml.exists():
        shutil.copy2(PACKAGE_META_YAML, layout.meta_yaml)

    if not layout.config_env.exists():
        layout.config_env.write_text(_STARTER_CONFIG_ENV, encoding="utf-8")
        layout.config_env.chmod(0o600)

    if not layout.vault_yaml.exists():
        layout.vault_yaml.write_text(_STARTER_VAULT_YAML, encoding="utf-8")

    linked = _symlink_skills(layout.skills_dir)
    linked_opencode = _symlink_opencode_agents(layout.opencode_agents_dir)
    gitignore_written = write_gitignore(root)
    machine_config = ensure_machine_config()

    return {
        "vault": str(root),
        "schemas": seeded_schemas,
        "skills": linked,
        "opencode_agents": linked_opencode,
        "gitignore": gitignore_written,
        "machine_config": machine_config,
    }


def _symlink_skills(dest: Path) -> list[str]:
    """Symlink each packaged skill into the vault's `.claude/skills/`.

    Symlinks rather than copies so an artmind upgrade reaches every vault with
    no re-seeding and no N-copies-to-update problem -- the same pattern the
    checkout already uses (see CLAUDE.md). Where symlinks are unavailable
    (Windows without privileges, some sync services) fall back to a copy; the
    cost is that upgrades then need an explicit refresh.
    """
    linked: list[str] = []
    for src in sorted(PACKAGE_SKILLS_DIR.iterdir()):
        if not src.is_dir():
            continue
        target = dest / src.name
        if target.is_symlink() or target.exists():
            if target.is_symlink() and target.resolve() == src.resolve():
                linked.append(src.name)
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        try:
            target.symlink_to(src, target_is_directory=True)
        except OSError:
            shutil.copytree(src, target)
        linked.append(src.name)
    return linked


def _symlink_opencode_agents(dest: Path) -> list[str]:
    """Symlink each packaged opencode agent (persona) `.md` file into the
    vault's `.opencode/agent/`.

    Same rationale as ``_symlink_skills``: a symlink rather than a copy means
    an artmind upgrade reaches every vault with no re-seeding. Files, not
    directories, since ``PACKAGE_OPENCODE_DIR / "agent"`` holds flat `.md`
    personas (``artmind.md``, ``artmind-admin.md``), unlike the per-skill
    subdirectories under ``PACKAGE_SKILLS_DIR``.
    """
    src_dir = PACKAGE_OPENCODE_DIR / "agent"
    linked: list[str] = []
    if not src_dir.is_dir():
        return linked
    for src in sorted(src_dir.glob("*.md")):
        target = dest / src.name
        if target.is_symlink() or target.exists():
            if target.is_symlink() and target.resolve() == src.resolve():
                linked.append(src.name)
                continue
            target.unlink()
        try:
            target.symlink_to(src)
        except OSError:
            shutil.copy2(src, target)
        linked.append(src.name)
    return linked


def _setup_neo4j(session, embedding_dim: int) -> None:
    # ── Uniqueness constraints ────────────────────────────────────────────────
    session.run(
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT user_chat_id IF NOT EXISTS FOR (n:UserChat) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT conflict_id IF NOT EXISTS FOR (n:Conflict) REQUIRE n.id IS UNIQUE"
    )

    # ── History labels (Phase 4) ──────────────────────────────────────────────
    # A document, its chunks, and its observations carry these labels — instead
    # of :Document/:DocChunk/:Observation — for exactly as long as they are
    # `history` rather than `latest`. There is no `_status` property backing
    # this; the label swap (docs retire/restore, and re-ingest superseding a
    # prior version) IS the state. Same id shape as the base label, so the same
    # uniqueness constraint applies — a node only ever carries one of the pair.
    session.run(
        "CREATE CONSTRAINT document_history_id IF NOT EXISTS FOR (n:DocumentHistory) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chunk_history_id IF NOT EXISTS FOR (n:DocChunkHistory) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT observation_history_id IF NOT EXISTS FOR (n:ObservationHistory) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX document_history_domain IF NOT EXISTS FOR (n:DocumentHistory) ON (n.domain)"
    )
    session.run(
        "CREATE INDEX chunk_history_doc_id IF NOT EXISTS FOR (n:DocChunkHistory) ON (n.doc_id)"
    )
    session.run(
        "CREATE INDEX chunk_history_domain IF NOT EXISTS FOR (n:DocChunkHistory) ON (n.domain)"
    )

    # ── The observation zone ──────────────────────────────────────────────────
    # :Observation is the immutable record of what one chunk of one document
    # version asserted. It carries NO :Entity label and NO class label, which is
    # what keeps it structurally out of entity_embedding and entity_name_ft --
    # no predicate to forget, no index to filter. Its id is a hash of
    # (chunk, canonical name, class, domain), so the constraint below also
    # guarantees a re-write cannot duplicate.
    session.run(
        "CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (n:Observation) REQUIRE n.id IS UNIQUE"
    )
    # The projection rebuild reads observations by aggregate key; the label
    # alone means latest now, so there is no status column to compose with —
    # see `projection.read_latest_observations`. The version transition and
    # affected-key capture read them by doc_id. `entity-history` (Phase 4) is
    # the one reader that spans both labels, hence the ObservationHistory
    # mirrors below.
    session.run(
        "CREATE INDEX observation_key IF NOT EXISTS FOR (n:Observation) ON (n.key)"
    )
    session.run(
        "CREATE INDEX observation_doc_id IF NOT EXISTS FOR (n:Observation) ON (n.doc_id)"
    )
    session.run(
        "CREATE INDEX observation_domain IF NOT EXISTS FOR (n:Observation) ON (n.domain)"
    )
    session.run(
        "CREATE INDEX observation_history_key IF NOT EXISTS FOR (n:ObservationHistory) ON (n.key)"
    )
    session.run(
        "CREATE INDEX observation_history_doc_id IF NOT EXISTS FOR (n:ObservationHistory) ON (n.doc_id)"
    )
    session.run(
        "CREATE INDEX observation_history_domain IF NOT EXISTS FOR (n:ObservationHistory) ON (n.domain)"
    )
    # Entity.key backs the full-rebuild sweep and the scoped embed sweep.
    session.run(
        "CREATE INDEX entity_key IF NOT EXISTS FOR (n:Entity) ON (n.key)"
    )
    # The embed sweep matches on `embedding IS NULL OR embedding_stale`.
    session.run(
        "CREATE INDEX entity_embedding_stale IF NOT EXISTS FOR (n:Entity) ON (n.embedding_stale)"
    )

    # ── Entity._id: unique constraint, or a plain index as fallback ───────────
    # Exact-id lookup is the query layer's primary retrieval path, so this must
    # be backed by an index either way. Since Phase 3 an Entity's id is
    # sha256(canonical_name | entity_class | domain) and the projection MERGEs
    # on it, so duplicates cannot arise from a fresh graph — but a graph
    # carrying pre-Phase-3 random uuids may still hold them, and there the
    # constraint cannot be created. The plain index keeps lookups fast until a
    # full rebuild replaces those ids. Prefixed `_id` (Phase 4) — see
    # `_domain` below for why: both are artmind-owned, unlike the
    # extraction-contract fields (name/description/entity_class/type/context/
    # aliases), which stay unprefixed because the whole query layer reads them.
    try:
        session.run(
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n._id IS UNIQUE"
        )
        entity_id_schema = "entity_id (unique)"
    except Exception:
        session.run("CREATE INDEX entity_id_idx IF NOT EXISTS FOR (n:Entity) ON (n._id)")
        entity_id_schema = "entity_id_idx (fallback index; duplicate Entity._id values exist)"

    # ── Composite index for exact 3-field entity upserts ──────────────────────
    session.run(
        "CREATE INDEX entity_lookup IF NOT EXISTS FOR (n:Entity) ON (n.name, n.entity_class, n._domain)"
    )

    # ── Single-property indexes for domain filtering (used by nearly every query) ─
    session.run(
        "CREATE INDEX entity_domain IF NOT EXISTS FOR (n:Entity) ON (n._domain)"
    )
    session.run(
        "CREATE INDEX document_domain IF NOT EXISTS FOR (n:Document) ON (n.domain)"
    )
    # Path-based logical identity (A1c): the lookup re-ingest uses to reuse a
    # document's physical id and bump its version instead of minting a duplicate.
    session.run(
        "CREATE INDEX document_logical_id IF NOT EXISTS FOR (n:Document) ON (n.logical_id)"
    )
    session.run(
        "CREATE INDEX chunk_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.domain)"
    )
    # Content-addressed block hash (A1a); the signal a later delta classifier (A4)
    # keys on to tell changed blocks from unchanged ones across re-ingest.
    session.run(
        "CREATE INDEX chunk_block_hash IF NOT EXISTS FOR (n:DocChunk) ON (n.block_hash)"
    )
    session.run(
        "CREATE INDEX user_chat_domain IF NOT EXISTS FOR (n:UserChat) ON (n.domain)"
    )

    # ── Temporal range indexes (canonical valid-time) ─────────────────────────
    # event_at (+ its index) is gone — for an occurrent entity valid_from IS
    # the event date, so a second axis was redundant; `timeline` (Phase 4) is
    # re-specified as a preset over entity_listing ordered by valid_from.
    session.run("CREATE INDEX entity_valid_from IF NOT EXISTS FOR (n:Entity) ON (n.valid_from)")
    session.run("CREATE INDEX entity_valid_to IF NOT EXISTS FOR (n:Entity) ON (n.valid_to)")
    session.run("CREATE INDEX chunk_valid_from IF NOT EXISTS FOR (n:DocChunk) ON (n.valid_from)")
    session.run("CREATE INDEX chunk_valid_to IF NOT EXISTS FOR (n:DocChunk) ON (n.valid_to)")
    session.run("CREATE INDEX document_valid_from IF NOT EXISTS FOR (n:Document) ON (n.valid_from)")
    session.run("CREATE INDEX document_valid_to IF NOT EXISTS FOR (n:Document) ON (n.valid_to)")
    session.run("CREATE INDEX conflict_status IF NOT EXISTS FOR (n:Conflict) ON (n.status)")

    # ── 2-field composite for name+domain entity lookups (ingest/update writes) ─
    session.run(
        "CREATE INDEX entity_name_domain IF NOT EXISTS FOR (n:Entity) ON (n.name, n._domain)"
    )

    # ── DocChunk.doc_id for chunk-to-document joins and deletes ───────────────
    session.run(
        "CREATE INDEX chunk_doc_id IF NOT EXISTS FOR (n:DocChunk) ON (n.doc_id)"
    )

    # ── Filing taxonomy (ADR 0010): project, area filtering on Document and DocChunk ─
    session.run(
        "CREATE INDEX document_project IF NOT EXISTS FOR (n:Document) ON (n.project)"
    )
    session.run(
        "CREATE INDEX document_area IF NOT EXISTS FOR (n:Document) ON (n.area)"
    )
    session.run(
        "CREATE INDEX chunk_project IF NOT EXISTS FOR (n:DocChunk) ON (n.project)"
    )
    session.run(
        "CREATE INDEX chunk_area IF NOT EXISTS FOR (n:DocChunk) ON (n.area)"
    )

    # ── A3: Graph indices for scoped graph-view re-queries (ADRs 0004/0008) ──
    # graph-view Cards are always filtered subviews. The Card's re-query after
    # a change event repeats the same filter, so composite indexes on the
    # most-frequent (label, filter-property) combos keep re-queries fast at scale.

    # Entity: (entity_class, domain) — pattern1/4/8/9 filter by class + domain;
    # entity_class alone shows up in entity_context / refine passes. The existing
    # entity_lookup composite is (name, entity_class, domain) which can't be
    # used when name is unknown, so this leading-column composite fills the gap.
    session.run(
        "CREATE INDEX entity_class_domain IF NOT EXISTS FOR (n:Entity) ON (n.entity_class, n._domain)"
    )
    session.run(
        "CREATE INDEX entity_class IF NOT EXISTS FOR (n:Entity) ON (n.entity_class)"
    )

    # Document: (project, domain) composite — filing_listing scoped by both;
    # (area, domain) likewise. Also plain area for area-only filters.
    session.run(
        "CREATE INDEX document_project_domain IF NOT EXISTS FOR (n:Document) ON (n.project, n.domain)"
    )
    session.run(
        "CREATE INDEX document_area_domain IF NOT EXISTS FOR (n:Document) ON (n.area, n.domain)"
    )

    # DocChunk: (project, domain) composite — vector-text / chunk queries scoped
    # to a project stay index-backed instead of falling to a domain scan + filter.
    session.run(
        "CREATE INDEX chunk_project_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.project, n.domain)"
    )
    session.run(
        "CREATE INDEX chunk_area_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.area, n.domain)"
    )

    # DocChunk: (doc_id, id) composite — chunks_by_id's ±N neighbor window
    # fetches all siblings of doc_id then ORDER BY id. The composite lets the
    # planner index-scan in reading order without a separate sort.
    session.run(
        "CREATE INDEX chunk_doc_id_id IF NOT EXISTS FOR (n:DocChunk) ON (n.doc_id, n.id)"
    )

    # Document: plain name index — pattern10 does CONTAINS on name, which the
    # name fulltext (document_name_ft below) is better for; a plain name index
    # is cheap and helps exact-name lookups elsewhere (docs commands, provenance).
    session.run(
        "CREATE INDEX document_name IF NOT EXISTS FOR (n:Document) ON (n.name)"
    )

    # ── Vector indexes ────────────────────────────────────────────────────────
    session.run(
        f"CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS "
        f"FOR (c:DocChunk) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )
    session.run(
        f"CREATE VECTOR INDEX user_chat_embedding IF NOT EXISTS "
        f"FOR (c:UserChat) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )
    session.run(
        f"CREATE VECTOR INDEX entity_embedding IF NOT EXISTS "
        f"FOR (e:Entity) ON (e.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )

    # ── Fulltext indexes ──────────────────────────────────────────────────────
    try:
        session.run(
            "CREATE FULLTEXT INDEX chunk_text_ft IF NOT EXISTS FOR (c:DocChunk) ON EACH [c.text]"
        )
        session.run(
            "CREATE FULLTEXT INDEX user_chat_text_ft IF NOT EXISTS FOR (c:UserChat) ON EACH [c.raw_text]"
        )
        session.run(
            "CREATE FULLTEXT INDEX entity_name_ft IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.description]"
        )
        # A3: Document.name + .title fulltext — pattern10's CONTAINS query and any
        # canvas graph-view / provenance Card that resolves a document by name benefit
        # from a fulltext index over a filter scan on a growing document set.
        session.run(
            "CREATE FULLTEXT INDEX document_name_ft IF NOT EXISTS FOR (d:Document) ON EACH [d.name, d.title]"
        )
    except Exception:
        pass

    # ── Relationship property index (Phase 4) ─────────────────────────────────
    # 249 free-text Entity-Entity relationship types collapsed to one
    # RELATES_TO {rel_type}: this is what keeps rel_type lookups index-backed
    # without needing one Neo4j type per relationship kind.
    session.run(
        "CREATE INDEX relates_to_type IF NOT EXISTS FOR ()-[r:RELATES_TO]-() ON (r.rel_type)"
    )

    # ── Structured-store catalogue (Table/TableColumn/EntityClass) ────────────
    # MERGE keys are synthetic composite `key` props (not node-key constraints,
    # which are Enterprise-only). These labels are distinct from :Entity by
    # design — never carry :Entity — so they stay out of query graph
    # pattern*/vector search/metadata.
    session.run(
        "CREATE CONSTRAINT cat_table_key IF NOT EXISTS FOR (n:Table) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT cat_column_key IF NOT EXISTS FOR (n:TableColumn) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT cat_entityclass_key IF NOT EXISTS FOR (n:EntityClass) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE INDEX cat_table_domain IF NOT EXISTS FOR (n:Table) ON (n.domain)"
    )

    # ── Curation (Phase 6) ──────────────────────────────────────────────────
    # :Synthesis is keyed on the ENTITY's own deterministic id (see
    # projection.load_synthesis) — a sibling node, not an Entity subtype, so
    # it survives a rebuild's MERGE + property-clear untouched.
    session.run(
        "CREATE CONSTRAINT synthesis_id IF NOT EXISTS FOR (n:Synthesis) REQUIRE n.id IS UNIQUE"
    )
    # :SameAsProposal — the review queue `sameas propose/list/approve/reject`
    # operates over. id is deterministic (canonical + sorted members), so
    # re-proposing an identical group MERGEs rather than duplicating.
    session.run(
        "CREATE CONSTRAINT sameas_proposal_id IF NOT EXISTS FOR (n:SameAsProposal) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX sameas_proposal_status IF NOT EXISTS FOR (n:SameAsProposal) ON (n.status)"
    )
    # :ProjectionState — a singleton (id='singleton') recording same_as.yaml's
    # hash, the schema-set hash, and the last FULL rebuild time, for
    # `projection status`'s drift check. No uniqueness constraint needed (a
    # single MERGE key), but an index keeps the lookup index-backed.
    session.run(
        "CREATE INDEX projection_state_id IF NOT EXISTS FOR (n:ProjectionState) ON (n.id)"
    )

    # One-time backfill: every :Conflict written before this phase by the
    # pairwise adjudicator carries no `_source` at all (the rebuild's own
    # conflicts have always been tagged `_source: 'projection'` since Phase
    # 3). Now that new adjudicator conflicts are tagged `_source:
    # 'adjudicator'` too, an untagged Conflict can only be a pre-Phase-6
    # adjudicator node — "unify on one :Conflict producer" means every
    # :Conflict has a recognized `_source`, not that old ones get deleted.
    session.run(
        "MATCH (c:Conflict) WHERE c._source IS NULL SET c._source = 'adjudicator'"
    )

    return {"entity_id_schema": entity_id_schema}


def setup_all() -> dict:
    """Initialize SQLite tables and Neo4j constraints/indexes.

    Safe to call at any time — all operations are idempotent (IF NOT EXISTS).
    Returns a summary of what was set up.
    """
    scaffold = scaffold_run_folder()
    env = load_env()
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))

    _init_db()

    with neo4j_session() as session:
        neo4j_notes = _setup_neo4j(session, embedding_dim)

    return {
        "scaffold": scaffold,
        "sqlite": "ok",
        "neo4j_constraints": [
            "document_id",
            "chunk_id",
            "user_chat_id",
            "conflict_id",
            "document_history_id",
            "chunk_history_id",
            "observation_history_id",
            "observation_id",
            neo4j_notes["entity_id_schema"],
            "cat_table_key",
            "cat_column_key",
            "cat_entityclass_key",
            "synthesis_id",
            "sameas_proposal_id",
        ],
        "neo4j_indexes": [
            "entity_lookup",
            "entity_domain",
            "entity_name_domain",
            "entity_class",
            "entity_class_domain",
            "entity_key",
            "entity_embedding_stale",
            "document_domain",
            "document_logical_id",
            "document_project",
            "document_area",
            "document_project_domain",
            "document_area_domain",
            "document_name",
            "document_history_domain",
            "chunk_domain",
            "chunk_block_hash",
            "chunk_doc_id",
            "chunk_doc_id_id",
            "chunk_project",
            "chunk_area",
            "chunk_project_domain",
            "chunk_area_domain",
            "chunk_history_doc_id",
            "chunk_history_domain",
            "user_chat_domain",
            "observation_key",
            "observation_doc_id",
            "observation_domain",
            "observation_history_key",
            "observation_history_doc_id",
            "observation_history_domain",
            "entity_valid_from",
            "entity_valid_to",
            "chunk_valid_from",
            "chunk_valid_to",
            "document_valid_from",
            "document_valid_to",
            "conflict_status",
            "cat_table_domain",
            "relates_to_type",
            "sameas_proposal_status",
            "projection_state_id",
        ],
        "neo4j_vector_indexes": [
            f"chunk_embedding (dim={embedding_dim})",
            f"user_chat_embedding (dim={embedding_dim})",
            f"entity_embedding (dim={embedding_dim})",
        ],
        "neo4j_fulltext_indexes": ["chunk_text_ft", "user_chat_text_ft", "entity_name_ft", "document_name_ft"],
    }
