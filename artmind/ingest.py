import json
import re
import shutil
import time
import uuid
import datetime as _datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha1, sha256
from pathlib import Path

import yaml
from loguru import logger
from neo4j import GraphDatabase

from artmind.db import _get_db
from artmind.derived_markdown import (
    decide as _decide_promotion,
    derived_markdown_path,
    is_promoted as _is_promoted,
    markdown_was_edited as _markdown_was_edited,
)
from artmind.document_identity import (
    build_frontmatter,
    canonical_path,
    compute_content_sha256,
    decide_version,
    markdown_path_for,
    mint_artmind_id,
    resolve_canonical_path,
    resolve_identity,
    write_document,
)
from artmind.setup import _setup_neo4j
from artmind.vault_git import commit_paths as _vault_commit_paths
from artmind.vault_git import current_commit as _vault_current_commit
from artmind.vault_git import move_path as _vault_move_path
from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    embed_text as _embed_text,
    call_llm as _call_llm_text,
    extract_with_retry as _llm_extract_shared,
    parse_json_response as _parse_json_response,
    entities_list_text as _entities_list_text,
    ibm_ica_client_env as _ibm_ica_client_env,
)
from artmind.canonicalize import canonicalize_document
from artmind.llm_providers import describe_image_ollama, describe_image_openrouter
from artmind.jobs import _update_job_file_status, _update_job_status
from artmind.structured import STRUCTURED_EXTENSIONS
from paths import (
    ARTMIND_VAULT_DIR,
    DOMAIN_SCHEMAS_DIR,
    KG_DIR,
    MARKDOWNS_DIR,
    ORIGINALS_DIR,
    PROJECT_ROOT,
)
from utils.functions import load_env, log_llm_call, run_command

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
}


# What artmind can actually ingest. `ingest_file` routes every non-`.md` file
# to docling, so without this an Obsidian vault's `.canvas` files (JSON) are
# handed to a document converter that cannot read them, and its `.png`
# attachments run through image description at full LLM cost merely for being
# present. Unknown types are skipped by a directory walk and reported by the
# caller -- never silently attempted.
#
# Derived from the sets that already define what each pipeline handles, so a
# type added there cannot silently vanish from directory walks -- which is
# exactly what happened to `.xlsm`, declared in STRUCTURED_EXTENSIONS but
# missing from a hand-maintained copy of this list.
DOCLING_SUFFIXES = frozenset({".pdf", ".pptx", ".docx"})
SUPPORTED_SUFFIXES = (
    frozenset({".md"})                  # vault-native markdown
    | DOCLING_SUFFIXES                  # document conversion
    | frozenset(STRUCTURED_EXTENSIONS)  # the structured store
    | frozenset(IMAGE_EXTENSIONS)       # images, when a folder of them is mapped
)


def is_supported(path: Path) -> bool:
    """Can artmind ingest this file type at all?"""
    return Path(path).suffix.lower() in SUPPORTED_SUFFIXES


def collect_ingest_files(path: Path) -> list[Path]:
    """Resolve a file-or-directory ingest target to the sorted list of files to ingest.

    A single file ingests as itself, whatever its type -- naming it is an
    explicit request, and the caller reports an unsupported type rather than
    the walk silently dropping it. A directory is walked recursively, skipping
    any file under a dotfile/dot-directory (``.DS_Store``, ``.git/``,
    ``.artmind/``, ``.obsidian/``) and any file whose type artmind cannot
    ingest (see ``SUPPORTED_SUFFIXES``).
    """
    if path.is_dir():
        return sorted(
            f for f in path.rglob("*")
            if f.is_file()
            and not any(p.startswith(".") for p in f.relative_to(path).parts)
            and is_supported(f)
        )
    # A named file is an explicit request: return it and let the caller report
    # why it cannot be ingested, rather than silently pretending it was absent.
    return [path]


def _compute_sha256(file_path: Path) -> str:
    h = sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


# ── kg_chunk_status helpers ────────────────────────────────────────────────────


def _init_chunk_rows(doc_sha256: str, doc_id: str, chunk_count: int) -> None:
    """Insert pending rows for all chunks of a document (INSERT OR IGNORE — won't overwrite)."""
    conn = _get_db()
    try:
        now = datetime.now().isoformat()
        conn.executemany(
            "INSERT OR IGNORE INTO kg_chunk_status"
            " (doc_sha256, doc_id, chunk_seq, entities_status, properties_status,"
            "  relationships_status, updated_at)"
            " VALUES (?, ?, ?, 'pending', 'pending', 'pending', ?)",
            [(doc_sha256, doc_id, seq, now) for seq in range(1, chunk_count + 1)],
        )
        conn.commit()
    finally:
        conn.close()


def _get_chunk_statuses(doc_sha256: str) -> dict[int, dict]:
    """Return {chunk_seq: {doc_id, entities_status, properties_status, relationships_status}}."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT chunk_seq, doc_id, entities_status, properties_status, relationships_status"
            " FROM kg_chunk_status WHERE doc_sha256 = ? ORDER BY chunk_seq",
            (doc_sha256,),
        ).fetchall()
        return {
            row[0]: {
                "doc_id": row[1],
                "entities_status": row[2],
                "properties_status": row[3],
                "relationships_status": row[4],
            }
            for row in rows
        }
    finally:
        conn.close()


def _update_chunk_step(doc_sha256: str, chunk_seq: int, step: str, status: str) -> None:
    """Update one step's status for a chunk (step: 'entities'|'properties'|'relationships')."""
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE kg_chunk_status SET {step}_status = ?, updated_at = ?"
            " WHERE doc_sha256 = ? AND chunk_seq = ?",
            (status, datetime.now().isoformat(), doc_sha256, chunk_seq),
        )
        conn.commit()
    finally:
        conn.close()


def _build_file_result_from_db(document_name: str, domain: str) -> dict | None:
    """Reconstruct file_result from the registry for CLI retry commands.

    Phase 5 dropped the registry's `filename` column (docs/redesign-phase-
    plan.md, "E") -- it was never anything but `Path(path).name`. Matched in
    Python rather than SQL (no portable basename function in plain sqlite3),
    against every row in the domain -- registries are small enough that this
    doesn't matter.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT path, content_sha256, artmind_id FROM documents WHERE domain = ?",
            (domain,),
        ).fetchall()
    finally:
        conn.close()
    target = document_name.upper()
    matches = [r for r in rows if Path(r[0]).name.upper() == target]
    if not matches:
        # Prefix match (user may omit extension)
        matches = [r for r in rows if Path(r[0]).name.upper().startswith(target)]
    if not matches:
        return None
    registered_path_str, doc_sha256, artmind_id = matches[0]
    # `canonical_path()` (document_identity.py) stores a *vault-relative*
    # string for anything inside the configured vault -- true of every
    # vault-native document, by definition of `_is_vault_native_markdown`.
    # Every other producer of `file_result["registered_path"]` in this module
    # (`_ingest_vault_native`, `_ingest_binary_derived`) hands back an
    # already-absolute path; resolve here too so this is the same shape,
    # not a value that only happens to work when cwd is the vault root.
    try:
        registered_path = resolve_canonical_path(registered_path_str)
    except ValueError:
        registered_path = Path(registered_path_str)
    chunks_dir = MARKDOWNS_DIR / f"{registered_path.stem}_chunks"
    chunk_count = len(sorted(chunks_dir.glob("chunk_*.md"))) if chunks_dir.exists() else 0
    result = {
        "status": "ok",
        "filename": registered_path.name,
        "sha256": doc_sha256,
        "registered_path": str(registered_path),
        "domain": domain,
        "chunks_dir": str(chunks_dir),
        "chunk_count": chunk_count,
    }
    if artmind_id:
        result["artmind_id"] = artmind_id
        # extract_kg (ingest.py) branches on "artmind_id" in file_result to
        # treat this as a vault-native markdown doc and reads file_result["version"]
        # unconditionally -- unlike _ingest_vault_native, this retry path never ran
        # decide_version(), so the registry alone can't supply it (the `documents`
        # table has no version column, docs/redesign-phase-plan.md "E"). Read it
        # back from the frontmatter that write_document() persisted, falling back
        # to 1 the same way the binary no_op path does if it's missing or unreadable.
        version = 1
        try:
            meta, _ = _parse_md_frontmatter(registered_path.read_text(encoding="utf-8"))
            version = int(meta.get("_version") or 1)
        except Exception as e:
            logger.warning(
                "Could not read _version from frontmatter for {}: {} (defaulting to 1)",
                registered_path, e,
            )
        result["version"] = version
    return result


def _canonical_key(source: Path, domain: str) -> str:
    """The stable identity key for a document, feeding ``_logical_id``.

    Prefer the source path *relative to the configured Vault root*
    (``ARTMIND_VAULT_DIR``) so a file keeps one identity across edits and
    re-ingest regardless of where a working copy sits. Fall back to the
    casefolded basename when no vault is configured or the file lives outside
    it — stable enough for ad-hoc single-file ingests. Case-folded so a rename
    that only changes case is still the same document on case-insensitive
    filesystems.
    """
    try:
        resolved = source.resolve()
    except Exception:
        resolved = source
    if ARTMIND_VAULT_DIR is not None:
        try:
            return resolved.relative_to(ARTMIND_VAULT_DIR).as_posix().casefold()
        except ValueError:
            pass
    return resolved.name.casefold()


def _logical_id(domain: str, canonical_key: str) -> str:
    """Deterministic, path-based document identity.

    ``sha1(domain \\x00 canonical_key)``. Unlike the physical ``Document.id``
    (a random uuid reused across versions), this is reproducible from the file's
    location, so re-ingesting an edited file resolves to the *same* document
    instead of minting a duplicate. Pure — same inputs always yield the same id.
    """
    return sha1(f"{domain}\x00{canonical_key}".encode("utf-8")).hexdigest()


def _resolve_doc_identity(
    domain: str, logical_id: str, resumed_doc_id: str | None = None
) -> tuple[str, int]:
    """Resolve the physical ``doc_id`` + ``version`` for a logical document.

    A document already in the graph (matched by ``logical_id`` + ``domain``)
    keeps its physical id and bumps ``version`` — this is how idempotent
    re-ingest (A1d) recognises an edited file as the same node. Otherwise mint a
    fresh identity at version 1, preferring a ``resumed_doc_id`` handed in from a
    prior partial extraction (chunk-status rows) so a resumed run keeps its id.
    A lookup failure (Neo4j unreachable) degrades to a fresh identity rather than
    aborting the extraction.
    """
    from artmind.graph_query import neo4j_session

    rec = None
    try:
        with neo4j_session() as session:
            rec = session.run(
                "MATCH (d:Document {logical_id: $lid, domain: $domain}) "
                "RETURN d.id AS id, d.version AS version "
                "ORDER BY coalesce(d.version, 1) DESC LIMIT 1",
                lid=logical_id,
                domain=domain,
            ).single()
    except Exception as e:
        logger.warning("logical_id lookup failed ({}); minting fresh identity", e)
        rec = None

    if rec and rec.get("id"):
        return rec["id"], int(rec.get("version") or 1) + 1
    return (resumed_doc_id or uuid.uuid4().hex), 1


def _register_document(
    domain: str,
    file_path: Path,
    artmind_id: str | None = None,
    *,
    content_sha256: str | None = None,
) -> str:
    """Record a document in the path <-> id cache; return the resolved path.

    Phase 2 (docs/document-identity.md): the registry is bookkeeping, not
    identity. For a plain binary/tabular source (``artmind_id=None``) real
    identity continuity still runs entirely through Neo4j's `logical_id`
    lookup (`_resolve_doc_identity`) — this row exists only so `retry-job`
    has something to look up by path. For a vault-native source, or a
    binary's derived/promoted markdown (`artmind/derived_markdown.py`,
    `_ingest_binary_derived` — which does NOT register the original binary
    itself, only its derived markdown; "has this binary been converted
    before" is answered by the filesystem, not this registry, see that
    function's docstring), ``artmind_id`` is the real identity and this row
    *is* the path <-> id cache the resolution table reads.

    ``content_sha256``, when given, is the caller's already-computed
    body-only hash (`decide_version`'s `_content_sha256` for a vault-native
    document, or the derived body's hash for a binary's derived markdown) —
    the registry stores exactly that number, not a second, disagreeing one.
    Omitted (a plain binary/tabular source with no separable body), this
    falls back to a whole-file hash.

    No duplicate guard: re-ingesting a known identity is always a replace
    now (`--replace`/`--force` are gone), and a copied template with an
    unedited body is a legitimate new document, not an error.
    """
    resolved_content_sha256 = content_sha256 if content_sha256 is not None else _compute_sha256(file_path)
    # `canonical_path`, not a raw `.resolve()` -- this MUST match exactly what
    # `resolve_identity`'s registry lookups key on (vault-relative when the
    # file is vault-native), or a `move`/`refuse` decision compares apples to
    # oranges (an absolute path stored here against a vault-relative path
    # looked up there) and every re-ingest of a known identity misreads as a
    # brand-new file at a colliding id.
    resolved_path = canonical_path(file_path)
    now = datetime.now().isoformat()
    conn = _get_db()
    try:
        if artmind_id is not None:
            conn.execute(
                """
                INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(artmind_id) DO UPDATE SET
                    domain = excluded.domain,
                    path = excluded.path,
                    content_sha256 = excluded.content_sha256,
                    last_ingested_at = excluded.last_ingested_at
                """,
                (artmind_id, domain, resolved_path, resolved_content_sha256, now),
            )
        else:
            # No `artmind_id` to `ON CONFLICT` against (a path-only row is a
            # plain binary original or a csv/xlsx — there's no UNIQUE(path)
            # either, since a vault-native path and a data-dir original path
            # live in disjoint namespaces and could otherwise collide under
            # one constraint). Delete-then-insert keeps re-ingesting an
            # unchanged file from accumulating a fresh duplicate row on every
            # run.
            #
            # Deliberately NOT scoped by `domain`: a path is already a
            # natural unique key (one file, one row) independent of which
            # domain it's currently tagged with — re-registering it under a
            # *different* domain (a re-home) must update that same row, not
            # leave a stale duplicate behind under the old domain.
            conn.execute(
                "DELETE FROM documents WHERE artmind_id IS NULL AND path = ?",
                (resolved_path,),
            )
            conn.execute(
                "INSERT INTO documents (artmind_id, domain, path, content_sha256, last_ingested_at)"
                " VALUES (NULL, ?, ?, ?, ?)",
                (domain, resolved_path, resolved_content_sha256, now),
            )
        conn.commit()
        return resolved_path
    finally:
        conn.close()


# ── ingest helpers ─────────────────────────────────────────────────────────────

_DESCRIBE_PROMPTS = [
    "If the picture is a logo or an icon, just reply logo or icon. "
    "If the picture is a table, reproduce it as a GitHub-flavored markdown table, "
    "preserving every row and column exactly as shown — do not summarize or paraphrase "
    "any values. Otherwise, describe what's in the picture ensuring all the words detected "
    "in the picture are included in your description",
    "Describe this image in detail. Include any visible text. If it contains a table, "
    "reproduce it as a markdown table preserving all rows and columns exactly.",
]


def _describe_image(image: Path, model: str) -> str | None:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
    logger.debug(
        "Describing image: {} (model={}, timeout={}s)", image.name, model, timeout
    )
    t0 = time.monotonic()
    for attempt, prompt in enumerate(_DESCRIBE_PROMPTS, start=1):
        logger.debug(
            "LLM PROMPT (image description, attempt {}):\n{}\nImage: {}",
            attempt,
            prompt,
            image.name,
        )
        try:
            if provider == "openrouter":
                description = describe_image_openrouter(image, model, prompt, timeout, env)
            elif provider == "ibm_ica":
                description = describe_image_openrouter(
                    image, model, prompt, timeout, _ibm_ica_client_env(env)
                )
            else:
                host = env.get("ARTMIND_KG_LLM_URL") or None
                description = describe_image_ollama(image, model, prompt, timeout, host=host)
            log_llm_call("chat", model, f"[IMAGE: {image.name}]\n{prompt}", description)
            logger.debug(
                "LLM RESPONSE (image description, attempt {}):\n{}",
                attempt,
                description,
            )
            if description:
                elapsed = time.monotonic() - t0
                logger.info(
                    "Image described in {:.1f}s (attempt {}): {} → {!r:.80}",
                    elapsed,
                    attempt,
                    image.name,
                    description,
                )
                return description
            logger.warning(
                "Empty response for {} on attempt {}, retrying...", image.name, attempt
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "Image description failed for {} in {:.1f}s (attempt {}): {}",
                image.name,
                elapsed,
                attempt,
                e,
            )
            return None
    elapsed = time.monotonic() - t0
    logger.error(
        "Image description empty after {} attempts in {:.1f}s: {}",
        len(_DESCRIBE_PROMPTS),
        elapsed,
        image.name,
    )
    return None


def _replace_image_ref(md_content: str, image_name: str, description: str) -> str:
    pattern = re.compile(r"!\[[^\]]*\]\([^)]*" + re.escape(image_name) + r"[^)]*\)")
    return pattern.sub(lambda _: description, md_content)


def _is_vault_native_markdown(source: Path) -> bool:
    """A vault-native markdown source: authored in the vault, identified by
    frontmatter, never copied (Q96 — docs/stores-and-repos.md)."""
    if source.suffix.lower() != ".md" or ARTMIND_VAULT_DIR is None:
        return False
    try:
        source.resolve().relative_to(ARTMIND_VAULT_DIR)
        return True
    except ValueError:
        return False


def _is_promotable_binary(source: Path) -> bool:
    """A true binary source (pdf/pptx/docx, ...) with a vault configured to
    mirror its derived markdown into — eligible for Phase 5 derived-markdown
    promotion (docs/document-identity.md, "Derived-markdown promotion"). An
    ad-hoc `.md` outside the vault is already markdown; there's no derived
    copy of it to promote, so it stays on the pre-Phase-2 path-keyed flow.
    Without a vault configured there's nowhere to mirror derived output into
    either, so binaries fall back to the same pre-Phase-2 flow in that case.
    """
    return source.suffix.lower() != ".md" and ARTMIND_VAULT_DIR is not None


def ingest_file(
    source: Path,
    image_model: str,
    domain: str | None = "general",
    job_id: str | None = None,
    chunk_size: int = 6000,
    *,
    set_domain: str | None = None,
    fork: bool = False,
    adopt: bool = False,
):
    """Ingest one file. Dispatches on source type (docs/document-identity.md):

    - vault-native markdown: identity is `_artmind_id`, resolved via the
      six-row resolution table; no copy into originals/markdowns.
    - everything else (binary, or an ad-hoc .md outside the vault): the
      pre-Phase-2 path-keyed flow, unchanged except `--force`/`--replace`
      are gone — re-ingesting a known identity is now always a replace, and
      a copied template with an unedited body is a legitimate new document
      rather than something to guard against.
    """
    if _is_vault_native_markdown(source):
        return _ingest_vault_native(
            source, domain=domain, job_id=job_id, chunk_size=chunk_size,
            set_domain=set_domain, fork=fork, adopt=adopt,
        )
    if _is_promotable_binary(source):
        return _ingest_binary_derived(
            source, image_model, domain or "general", job_id, chunk_size,
            set_domain=set_domain,
        )
    return _ingest_binary_or_adhoc(source, image_model, domain or "general", job_id, chunk_size)


def _ingest_vault_native(
    source: Path,
    *,
    domain: str | None,
    job_id: str | None,
    chunk_size: int,
    set_domain: str | None,
    fork: bool,
    adopt: bool,
) -> dict:
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    raw_text = source.read_text(encoding="utf-8")
    existing_meta, body = _parse_md_frontmatter(raw_text)

    prior_domain = existing_meta.get("_domain")
    effective_domain = set_domain or prior_domain or domain
    if not effective_domain:
        file_result["error"] = (
            f"{source}: no '_domain' in frontmatter and no --domain given"
        )
        logger.error(file_result["error"])
        return file_result
    domain_changed = prior_domain is not None and prior_domain != effective_domain

    try:
        resolution = resolve_identity(source, existing_meta.get("_artmind_id"), fork=fork, adopt=adopt)
    except Exception as e:  # IdentityConflict, most likely
        file_result["error"] = str(e)
        logger.error("Identity resolution failed for {}: {}", source, e)
        return file_result
    logger.info("Identity resolution for {}: {}", source.name, resolution.verdict)

    if resolution.verdict == "heal":
        # Frontmatter lost its id; the registry supplies it back — not a
        # content change, so it does not by itself affect versioning below.
        existing_meta = {**existing_meta, "_artmind_id": resolution.artmind_id}

    version_decision = decide_version(body, existing_meta)
    tier = "content" if domain_changed else version_decision.tier

    ingested_at = datetime.now(_datetime.timezone.utc).isoformat()
    new_meta = build_frontmatter(
        existing_meta,
        artmind_id=resolution.artmind_id,
        version=version_decision.version,
        content_sha256=version_decision.content_sha256,
        domain=effective_domain,
        source_commit=_vault_current_commit(),
        source_path=canonical_path(source),
        source_type="md",
        ingested_at=ingested_at,
        body=body,
    )
    write_document(source, new_meta, body)
    _register_document(
        effective_domain, source, resolution.artmind_id,
        content_sha256=version_decision.content_sha256,
    )

    if job_id:
        _update_job_file_status(
            job_id, str(source.resolve()), status="processing",
            current_step="ingest_file", started_at=ingested_at,
        )

    file_result.update({
        "status": "ok",
        "domain": effective_domain,
        "sha256": _compute_sha256(source),
        "artmind_id": resolution.artmind_id,
        "version": version_decision.version,
        "content_sha256": version_decision.content_sha256,
        "registered_path": str(source.resolve()),
        "resolution_verdict": resolution.verdict,
        "tier": tier,
        "touched_path": source,
    })

    if tier == "metadata_only":
        try:
            from artmind.delta import apply_metadata_only

            apply_metadata_only(
                doc_id=resolution.artmind_id,
                domain=effective_domain,
                metadata={
                    k: new_meta.get(k)
                    for k in ("title", "project", "area", "tags", "created_on", "modified_on")
                    if new_meta.get(k)
                },
            )
        except Exception as e:
            logger.warning("metadata-only graph update skipped for {}: {}", source.name, e)
        file_result["chunk_count"] = 0
        logger.info(
            "── Ingest done in {:.1f}s: {} (metadata_only, v{})",
            time.monotonic() - t_file_start, source.name, version_decision.version,
        )
        return file_result

    chunks = _split_markdown(body, chunk_size)
    chunks_dir = MARKDOWNS_DIR / f"{source.stem}_chunks"
    _persist_chunks(chunks, chunks_dir)
    file_result["chunks_dir"] = str(chunks_dir)
    file_result["chunk_count"] = len(chunks)

    logger.info(
        "── Ingest done in {:.1f}s: {} ({}, v{}) — {} chunk(s)",
        time.monotonic() - t_file_start, source.name, tier, version_decision.version, len(chunks),
    )
    return file_result


def _convert_binary_via_docling(dest_path: Path, image_model: str) -> tuple[str | None, dict]:
    """Run docling on `dest_path` (already copied into `documents/originals/`),
    describe any extracted images, and return the resulting markdown body.

    Writes the final body to `MARKDOWNS_DIR / f"{dest_path.stem}.md"` as a
    side effect (unchanged from the pre-Phase-5 shape other code may still
    read that path for) and also returns it directly, since Phase 5's
    derived-markdown promotion writes the same body into the vault instead of
    (or in addition to) that data-dir copy.

    Returns `(body, {})` on success, or `(None, {"status": ..., "error": ...})`
    on failure — the caller merges the error dict into its own `file_result`.
    """
    dest_filename = dest_path.name
    md_file = MARKDOWNS_DIR / f"{dest_path.stem}.md"

    logger.info("Converting to markdown via docling: {}", dest_filename)
    t0 = time.monotonic()
    cmd_str = f'uv run docling --to md --image-export-mode referenced --output "{MARKDOWNS_DIR}" "{dest_path}"'
    returncode, stdout, stderr = run_command(cmd_str, cwd=PROJECT_ROOT)
    elapsed = time.monotonic() - t0
    if returncode != 0:
        combined = (stderr or "") + (stdout or "")
        if "exceeds size limit" in combined or "max_image_decoded_size" in combined:
            logger.warning(
                "Skipping {} — docling rejected oversized image (file too large to convert)",
                dest_filename,
            )
            return None, {"status": "skipped", "error": "Oversized image: docling size limit exceeded"}
        logger.error(
            "Docling failed for {} in {:.1f}s: {}",
            dest_filename, elapsed, stderr or stdout,
        )
        return None, {"status": "failed", "error": "Docling conversion failed"}

    if not md_file.exists():
        logger.error("Expected markdown not created: {}", md_file)
        return None, {"status": "failed", "error": "Markdown file not created"}

    md_size_kb = md_file.stat().st_size / 1024
    logger.info(
        "Docling conversion done in {:.1f}s — markdown: {:.1f} KB", elapsed, md_size_kb,
    )

    md_content = md_file.read_text(encoding="utf-8")
    artifacts_dir = MARKDOWNS_DIR / f"{dest_path.stem}_artifacts"
    if artifacts_dir.exists():
        images = sorted(
            f for f in artifacts_dir.iterdir() if f.suffix.lower() in IMAGE_EXTENSIONS
        )
        if images:
            logger.info("Found {} image(s) to describe in artifacts", len(images))
            for idx, image in enumerate(images, start=1):
                logger.info("Image [{}/{}]: {}", idx, len(images), image.name)
                description = _describe_image(image, image_model)
                if description:
                    image.with_name(image.name + "_desc.md").write_text(
                        description, encoding="utf-8"
                    )
                    md_content = _replace_image_ref(md_content, image.name, description)
                else:
                    logger.error("No description produced for image: {}", image.name)
            md_file.write_text(md_content, encoding="utf-8")
            logger.debug("Markdown updated with {} image description(s)", len(images))
        else:
            logger.debug("Artifacts dir exists but contains no images")
    else:
        logger.debug("No artifacts directory for {}", dest_filename)

    return md_content, {}


def _ingest_binary_or_adhoc(
    source: Path,
    image_model: str,
    domain: str,
    job_id: str | None,
    chunk_size: int,
):
    file_size_kb = source.stat().st_size / 1024
    logger.info(
        "── Ingest start: {} ({:.1f} KB, domain={})", source.name, file_size_kb, domain
    )
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    logger.debug("SHA256: {}", _compute_sha256(source))

    # Stable path-based logical identity (A1c). Computed from the *source* path
    # (not the data-dir copy) so re-ingesting an edited file at the same Vault
    # path resolves to the same document. This is unaffected by Phase 2 —
    # binaries can't carry frontmatter, so they stay path-keyed
    # (docs/document-identity.md, "sources that cannot carry frontmatter").
    # Real identity continuity for this path runs through Neo4j's `logical_id`
    # lookup (`_resolve_doc_identity`); the SQLite row _register_document
    # writes below is bookkeeping, not identity.
    logical_id = _logical_id(domain, _canonical_key(source, domain))
    dest_filename = source.name

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = ORIGINALS_DIR / dest_filename
    shutil.copy2(source, dest_path)
    logger.debug("Copied original to: {}", dest_path)

    MARKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    if job_id:
        _update_job_file_status(
            job_id,
            str(source.resolve()),
            status="processing",
            current_step="ingest_file",
            started_at=datetime.now().isoformat(),
        )

    md_file = MARKDOWNS_DIR / f"{dest_path.stem}.md"

    if dest_path.suffix.lower() == ".md":
        shutil.copy2(dest_path, md_file)
        logger.info("Source is markdown — skipping docling conversion")
    else:
        body, err = _convert_binary_via_docling(dest_path, image_model)
        if body is None:
            file_result.update(err)
            return file_result

    registered_path = _register_document(domain, dest_path)
    elapsed_total = time.monotonic() - t_file_start
    logger.info(
        "── Ingest done in {:.1f}s: {} registered in domain '{}'",
        elapsed_total,
        dest_filename,
        domain,
    )

    # Split markdown into chunks and persist each chunk to disk
    raw_text = md_file.read_text(encoding="utf-8")
    _, body = _parse_md_frontmatter(raw_text)
    chunks = _split_markdown(body, chunk_size)
    chunks_dir = MARKDOWNS_DIR / f"{dest_path.stem}_chunks"
    _persist_chunks(chunks, chunks_dir)
    logger.info("Saved {} chunk(s) to {}_chunks/", len(chunks), dest_path.stem)

    file_result["status"] = "ok"
    file_result["domain"] = domain
    file_result["sha256"] = _compute_sha256(dest_path)
    file_result["logical_id"] = logical_id
    file_result["registered_path"] = str(registered_path)
    file_result["chunks_dir"] = str(chunks_dir)
    file_result["chunk_count"] = len(chunks)
    return file_result


def _ingest_binary_derived(
    source: Path,
    image_model: str,
    domain: str,
    job_id: str | None,
    chunk_size: int,
    *,
    set_domain: str | None = None,
) -> dict:
    """Ingest a true binary source (pdf/pptx/docx) whose derived markdown is
    mirrored into the vault (docs/document-identity.md, "Derived-markdown
    promotion"; docs/redesign-phase-plan.md, Phase 5 "D"). Extends
    `_artmind_id` to binary sources: the derived document IS the graph's
    `Document.id` from here on (`file_result["artmind_id"]`, read by the
    extraction entry point the same way a vault-native document already is)
    — one identity, not the old two-tier logical_id/physical-id scheme
    `_ingest_binary_or_adhoc` still uses for an ad-hoc `.md` or a vault-less
    install.

    Every ingest of the same original (matched by domain+filename, unchanged
    in spirit from Phase 2's `_canonical_key` — a binary source stays
    path-keyed, per docs/document-identity.md's "sources that cannot carry
    frontmatter" table) runs the 2x2 from the spec:

        markdown edited?  binary changed?  -> action
        no                no               -> no_op
        no                yes              -> convert (reconvert, safe)
        yes               no               -> promote (stop deriving it)
        yes               yes              -> collision (refuse, report both)

    A prior derived document that was already promoted refuses reconversion
    outright, before this 2x2 ever runs — see docs/document-identity.md's
    promote table, "re-converting the binary: refused".

    "Has this binary been converted before" is answered by the FILESYSTEM,
    at two deterministic, domain+stem-scoped locations — not the registry.
    Promotion's whole point is to move the file out of `_derived/`, so
    `_derived/<domain>/<stem>.md` existing means "not yet promoted" and NOT
    existing does not mean "never converted"; the second location,
    `<domain>/<stem>.md`, is where promotion moves it to (this module's own
    choice of target — docs/document-identity.md specifies the promotion but
    not a destination folder). Both are scoped by `domain`, so re-ingesting
    under a genuinely different domain without `--setDomain` reads as a
    fresh document, the same limitation `_ingest_vault_native` accepts for
    `--domain` before a file's own frontmatter can be consulted — except a
    binary has no frontmatter to consult until AFTER this lookup finds it.
    """
    file_size_kb = source.stat().st_size / 1024
    logger.info(
        "── Ingest start: {} ({:.1f} KB, domain={})", source.name, file_size_kb, domain
    )
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    stem = source.stem
    effective_domain = set_domain or domain
    dest_path = ORIGINALS_DIR / source.name
    orig_registry_path = canonical_path(dest_path)

    if job_id:
        _update_job_file_status(
            job_id, str(source.resolve()), status="processing",
            current_step="ingest_file", started_at=datetime.now().isoformat(),
        )

    # Compare against the PRIOR run's data-dir copy before it's overwritten --
    # `dest_path` already persists "the last original we saw" with no
    # registry round-trip needed. Absent (first-ever ingest of this
    # filename+domain) is not "changed", it's handled by `current_path is
    # None` below instead.
    binary_changed = dest_path.exists() and _compute_sha256(dest_path) != _compute_sha256(source)

    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest_path)
    logger.debug("Copied original to: {}", dest_path)

    derived_path = derived_markdown_path(ARTMIND_VAULT_DIR, effective_domain, stem)
    promoted_path_guess = ARTMIND_VAULT_DIR / effective_domain / f"{stem}.md"

    existing_meta: dict = {}
    existing_body = ""
    current_path: Path | None = None
    already_promoted = False
    if derived_path.exists():
        current_path = derived_path
        existing_meta, existing_body = _parse_md_frontmatter(derived_path.read_text(encoding="utf-8"))
    elif promoted_path_guess.exists():
        candidate_meta, candidate_body = _parse_md_frontmatter(
            promoted_path_guess.read_text(encoding="utf-8")
        )
        if _is_promoted(candidate_meta):
            current_path, existing_meta, existing_body = promoted_path_guess, candidate_meta, candidate_body
            already_promoted = True
        # else: an unrelated hand-authored document happens to occupy this
        # domain+stem path -- treated as "never converted" (a fresh
        # conversion below); a later promotion attempt would then fail loudly
        # (git mv refuses to overwrite a tracked file) rather than clobber it.

    if already_promoted:
        file_result["error"] = (
            f"{stem!r} was already promoted to vault-native at {current_path} "
            "-- reconverting the binary is refused (docs/document-identity.md, "
            '"Derived-markdown promotion"). Ingest that file directly instead.'
        )
        file_result["promoted_path"] = str(current_path)
        logger.warning(file_result["error"])
        return file_result

    if current_path is None:
        action = "convert"
    else:
        markdown_edited = _markdown_was_edited(existing_body, existing_meta.get("_derived_sha256"))
        action = _decide_promotion(
            markdown_edited=markdown_edited, binary_changed=binary_changed
        ).action

    if action == "collision":
        file_result["error"] = (
            f"{stem!r}: both the original binary and its derived markdown "
            f"({current_path}) changed since the last ingest -- "
            "artmind will not guess which side wins (docs/document-identity.md, "
            '"Derived-markdown promotion", collision). Resolve by hand: either '
            "revert the markdown edit and re-ingest the binary, or promote the "
            "markdown manually and discard the new binary."
        )
        logger.warning(file_result["error"])
        return file_result

    if action == "no_op":
        file_result["status"] = "ok"
        file_result["domain"] = effective_domain
        file_result["artmind_id"] = existing_meta.get("_artmind_id")
        file_result["version"] = int(existing_meta.get("_version") or 1)
        file_result["registered_path"] = str(current_path)
        file_result["tier"] = "no_op"
        file_result["chunk_count"] = 0
        logger.info(
            "── Ingest done in {:.1f}s: {} (no_op — neither the binary nor its "
            "derived markdown changed)", time.monotonic() - t_file_start, source.name,
        )
        return file_result

    promoted = False
    if action == "promote":
        promoted_path = promoted_path_guess
        if not _vault_move_path(current_path, promoted_path):
            file_result["error"] = (
                f"promotion of {current_path} to {promoted_path} failed "
                "(git mv) — see log"
            )
            return file_result

        version_decision = decide_version(existing_body, existing_meta)
        promoted_meta = build_frontmatter(
            existing_meta,
            artmind_id=existing_meta["_artmind_id"],
            version=version_decision.version,
            content_sha256=version_decision.content_sha256,
            domain=effective_domain,
            source_commit=_vault_current_commit(),
            source_path=existing_meta.get("_source_path") or orig_registry_path,
            source_type="md",
            ingested_at=datetime.now(_datetime.timezone.utc).isoformat(),
            body=existing_body,
        )
        promoted_meta.pop("_derived_sha256", None)
        write_document(promoted_path, promoted_meta, existing_body)
        _vault_commit_paths(
            [promoted_path],
            f"artmind: promote {stem} to vault-native "
            f"(was derived from {existing_meta.get('_source_type', 'a binary source')})",
        )
        logger.warning(
            "Promoted {} to vault-native at {} — a human edited the derived "
            "markdown; artmind will no longer reconvert the original binary "
            "for this document.", stem, promoted_path,
        )

        registered_path = promoted_path
        body = existing_body
        artmind_id = existing_meta["_artmind_id"]
        version = version_decision.version
        promoted = True
    else:  # "convert" — first-ever conversion, or the binary changed and the markdown didn't
        body, err = _convert_binary_via_docling(dest_path, image_model)
        if body is None:
            file_result.update(err)
            return file_result

        new_derived_sha256 = compute_content_sha256(body)
        artmind_id = existing_meta.get("_artmind_id") or mint_artmind_id()
        if current_path is None:
            version = 1
        elif new_derived_sha256 == existing_meta.get("_derived_sha256"):
            version = int(existing_meta.get("_version") or 1)
        else:
            version = int(existing_meta.get("_version") or 1) + 1

        registered_path = derived_path
        registered_path.parent.mkdir(parents=True, exist_ok=True)
        new_meta = build_frontmatter(
            existing_meta,
            artmind_id=artmind_id,
            version=version,
            content_sha256=compute_content_sha256(body),
            domain=effective_domain,
            source_commit=_vault_current_commit(),
            source_path=orig_registry_path,
            source_type=source.suffix.lstrip(".").lower(),
            ingested_at=datetime.now(_datetime.timezone.utc).isoformat(),
            body=body,
        )
        new_meta["_derived_sha256"] = new_derived_sha256
        write_document(registered_path, new_meta, body)
        _vault_commit_paths(
            [registered_path],
            f"artmind: {'convert' if current_path is None else 'reconvert'} "
            f"{stem} ({effective_domain})",
        )

    _register_document(effective_domain, registered_path, artmind_id, content_sha256=compute_content_sha256(body))

    elapsed_total = time.monotonic() - t_file_start
    chunks = _split_markdown(body, chunk_size)
    chunks_dir = MARKDOWNS_DIR / f"{stem}_chunks"
    _persist_chunks(chunks, chunks_dir)
    logger.info("Saved {} chunk(s) to {}_chunks/", len(chunks), stem)

    file_result["status"] = "ok"
    file_result["domain"] = effective_domain
    file_result["artmind_id"] = artmind_id
    file_result["version"] = version
    file_result["sha256"] = _compute_sha256(registered_path)
    file_result["registered_path"] = str(registered_path)
    file_result["chunks_dir"] = str(chunks_dir)
    file_result["chunk_count"] = len(chunks)
    if promoted:
        file_result["promoted"] = True
        file_result["promoted_to"] = str(registered_path)

    logger.info(
        "── Ingest done in {:.1f}s: {} ({}, v{}) — {} chunk(s)",
        elapsed_total, source.name, action, version, len(chunks),
    )
    return file_result


# ── knowledge graph helpers ────────────────────────────────────────────────────


def _parse_md_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata dict, body text). Metadata is empty if no YAML frontmatter."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    try:
        meta = yaml.safe_load(text[3:end]) or {}
    except Exception:
        meta = {}
    return meta, text[end + 4 :].lstrip("\n")


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1


def _is_table_separator_row(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and "-" in stripped and set(stripped) <= set("|:- ")


def _reattach_table_headers(sub_chunks: list[str]) -> list[str]:
    """Re-prepend the most recent markdown table header+separator row to any
    chunk that starts mid-table, so a table split across chunks doesn't hand
    the extraction LLM unlabeled rows with no column context."""
    result = []
    last_header: tuple[str, str] | None = None
    for chunk in sub_chunks:
        lines = chunk.split("\n")
        starts_mid_table = _is_table_row(lines[0]) and not (
            len(lines) > 1 and _is_table_separator_row(lines[1])
        )
        if starts_mid_table and last_header:
            chunk = "\n".join([*last_header, chunk])
            lines = chunk.split("\n")
        for i in range(len(lines) - 1):
            if _is_table_row(lines[i]) and _is_table_separator_row(lines[i + 1]):
                last_header = (lines[i], lines[i + 1])
        result.append(chunk)
    return result


def _header_path(metadata: dict) -> str:
    """Breadcrumb of the h1–h4 headers the splitter attached to a chunk, e.g.
    'Overview > Fees > Monthly'. Empty when the chunk sits under no heading."""
    parts = [metadata[k] for k in ("h1", "h2", "h3", "h4") if metadata.get(k)]
    return " > ".join(parts)


def _locate_chunk(text: str, piece: str, cursor: int) -> tuple[int, int, int]:
    """Locate a chunk's source span in `text`, at or after `cursor`.

    The markdown header splitter reformats whitespace (it appends trailing
    spaces to headings and collapses blank lines), so a chunk is *not* a verbatim
    substring of the source. We anchor instead on the chunk's first and last
    non-blank lines — which survive that reformatting — and return the span
    running from the first line's start to the last line's end. Returns
    (char_start, char_end, next_cursor); (-1, -1, cursor) when the anchor can't
    be found (e.g. a reattached table header)."""
    lines = [ln.strip() for ln in piece.split("\n") if ln.strip()]
    if not lines:
        return -1, -1, cursor
    first, last = lines[0], lines[-1]
    start = text.find(first, cursor)
    if start == -1:
        start = text.find(first)
    if start == -1:
        return -1, -1, cursor
    end_anchor = text.find(last, start)
    end = end_anchor + len(last) if end_anchor != -1 else start + len(first)
    return start, end, end


def _split_markdown(text: str, chunk_size: int) -> list[dict]:
    """Split markdown into chunks carrying block-level provenance.

    Returns dicts ``{text, char_start, char_end, header_path, block_hash}``.
    ``char_start``/``char_end`` mark the chunk's span in the source body,
    anchored on its first and last non-blank lines (the header splitter reformats
    whitespace, so the slice is line-accurate rather than byte-identical to
    ``text``; see ``_locate_chunk``). ``block_hash`` is a content hash over the
    final chunk text, stable across re-ingest — the key A4's delta classifier
    keys on.
    """
    # Imported lazily: langchain-text-splitters ships only in the optional
    # `[ingest]` extra, so a core-only install can still import artmind.ingest.
    from langchain_text_splitters import (
        MarkdownHeaderTextSplitter,
        RecursiveCharacterTextSplitter,
    )

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "h1"),
            ("##", "h2"),
            ("###", "h3"),
            ("####", "h4"),
        ],
        strip_headers=False,
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", " "],
    )
    chunks: list[dict] = []

    def _emit(pieces: list[str], header_path: str, cursor: int) -> int:
        # Offsets are located against the pre-reattachment pieces (each a
        # verbatim substring of `text`); reattachment only prepends to `text`.
        located = [(0, 0)] * len(pieces)
        for i, piece in enumerate(pieces):
            start, end, cursor = _locate_chunk(text, piece, cursor)
            located[i] = (start, end)
        for (start, end), final_text in zip(located, _reattach_table_headers(pieces)):
            chunks.append(
                {
                    "text": final_text,
                    "char_start": start,
                    "char_end": end,
                    "header_path": header_path,
                    "block_hash": sha1(final_text.encode("utf-8")).hexdigest(),
                }
            )
        return cursor

    header_docs = header_splitter.split_text(text)
    cursor = 0
    for doc in header_docs:
        content = doc.page_content.strip()
        if not content:
            continue
        header_path = _header_path(doc.metadata)
        if len(content) <= chunk_size:
            pieces = [content]
        else:
            pieces = [c.strip() for c in char_splitter.split_text(content) if c.strip()]
        cursor = _emit(pieces, header_path, cursor)
    if not chunks:
        pieces = [c.strip() for c in char_splitter.split_text(text) if c.strip()]
        _emit(pieces, "", 0)
    return chunks


def _persist_chunks(chunks: list[dict], chunks_dir: Path) -> None:
    """Write each chunk's text to ``chunk_NNN.md`` and a parallel
    ``chunks_meta.json`` sidecar holding block-level provenance (source offsets,
    header breadcrumb, content hash) keyed by zero-padded sequence."""
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for stale in chunks_dir.glob("chunk_*.md"):
        stale.unlink()
    meta: dict[str, dict] = {}
    for i, chunk in enumerate(chunks, start=1):
        (chunks_dir / f"chunk_{i:03d}.md").write_text(chunk["text"], encoding="utf-8")
        meta[f"{i:03d}"] = {
            "char_start": chunk["char_start"],
            "char_end": chunk["char_end"],
            "header_path": chunk["header_path"],
            "block_hash": chunk["block_hash"],
        }
    (chunks_dir / "chunks_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _llm_extract(step_name: str, model: str, prompt: str, debug_dir: Path) -> tuple[list, bool]:
    return _llm_extract_shared(step_name, model, prompt, debug_dir=debug_dir)


def _save_debug(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    logger.debug("Raw LLM response saved for debugging: {}", path.name)


def _filter_valid_items(
    items: list, seq: int, step_name: str, required_key: str | None = None
) -> list:
    """Drop non-dict items an LLM occasionally mixes into otherwise-valid JSON output."""

    def is_valid(item) -> bool:
        if not isinstance(item, dict):
            return False
        return required_key is None or required_key in item

    valid = [item for item in items if is_valid(item)]
    dropped = len(items) - len(valid)
    if dropped:
        logger.warning(
            "  Chunk {} — dropped {} malformed {} item(s) from LLM output", seq, dropped, step_name
        )
    return valid


def _rewrite_entity_ids(
    entities: list[dict], chunk_id: str
) -> tuple[list[dict], dict[str, str]]:
    """Prefix every entity id with chunk_id; return (rewritten_entities, old→new id map)."""
    id_map: dict[str, str] = {}
    rewritten = []
    for e in entities:
        old_id = e["id"]
        new_id = f"{chunk_id}_{old_id}"
        id_map[old_id] = new_id
        rewritten.append({**e, "id": new_id})
    return rewritten, id_map


def _rewrite_ref_ids(
    items: list[dict], id_map: dict[str, str], *fields: str
) -> list[dict]:
    """Rewrite id fields in a list of dicts using the given id_map."""
    result = []
    for item in items:
        item = dict(item)
        for field in fields:
            if field in item:
                item[field] = id_map.get(item[field], item[field])
        result.append(item)
    return result


# ── neo4j helpers ─────────────────────────────────────────────────────────────


def _sanitize_label(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", s.strip()).upper() or "UNKNOWN"


# SUPERSEDES is created ONLY by the audited temporal helpers (apply_supersession /
# apply_node_supersession), which stamp provenance (scope/detected_by/effective) and
# retire the older side. An LLM-extracted Entity->Entity relationship must never mint
# a bare SUPERSEDES edge with no provenance — that corrupts lineage silently.
#
# EXTRACTED_FROM is reserved too: no shipped domain schema lists it as a legitimate
# Entity<->Entity rel_type (it's structural, written only between an :Observation and
# its source :DocChunk — see observations.py — never between two Entities), so
# blocking it as an Entity->Entity type is defense-in-depth with no known cost.
#
# PRIOR_STATE, entity_history.py and the :EntityVersion zone it backed are gone
# (Phase 4) — nothing left links a live Entity to a history snapshot node, since
# observations now carry that role directly (see `query entity-history`). Not
# un-reserved: an LLM-minted PRIOR_STATE edge would still be confusing machinery-
# shaped noise even with nothing left to check it against.
#
# PART_OF is deliberately NOT reserved: multiple shipped schemas (general_schema,
# banking.organization_schema, sales_collateral_schema, project_governance_schema)
# list part_of as a legitimate LLM-extractable Entity->Entity relationship (e.g.
# "Branch X part_of Region Y"). The only structural PART_OF edge is the hardcoded
# DocChunk->Document edge written elsewhere in this module's own upsert code — a
# different code path from this Entity->Entity loop — so reserving PART_OF here
# would silently drop legitimate, schema-sanctioned extractions.
RESERVED_REL_TYPES = frozenset({
    "SUPERSEDES", "EXTRACTED_FROM", "PRIOR_STATE",
    # Phase 4: the collapsed relationship shape and the observation/entity
    # aggregation machinery that produces it. An extractor claiming one of
    # these as its own rel_type would be indistinguishable from the system's
    # own edges.
    "RELATES_TO", "ASSERTS_RELATION", "AGGREGATES",
})


def _neo4j_value(key, value):
    """Convert a value to a Neo4j-compatible type, or `None` to drop it.

    Nested objects are **dropped with a warning** rather than JSON-encoded —
    the dict->JSON branch this used to have is gone (Phase 4): it survived
    only for the old per-type relationship properties, which carried
    arbitrary extracted fields; `ASSERTS_RELATION`/`RELATES_TO` now carry a
    fixed, known-scalar property set (`rel_type`/`doc_id`/`chunk_id` and the
    aggregate's own `observation_count`/`chunk_ids`/`doc_ids`), so nothing
    left needs it. Same discipline as `observations.flatten_domain_props`:
    properties flatten or they don't exist, everywhere, not just on
    observations. A JSON blob is unqueryable, unmergeable by shape, and
    invisible to the property-key hygiene the scorecard tracks.
    """
    if isinstance(value, dict):
        logger.warning("Neo4j: dropped nested-object property {!r} (JSON blobs are forbidden)", key)
        return None
    if isinstance(value, list):
        flat = [v for v in value if not isinstance(v, (dict, list))]
        if len(flat) != len(value):
            logger.warning(
                "Neo4j: dropped {} nested item(s) from list property {!r}",
                len(value) - len(flat), key,
            )
        return flat
    return value


def _flatten_props(props: dict) -> dict:
    """Flatten a props dict to Neo4j-compatible types, dropping empty values."""
    result = {}
    for k, v in props.items():
        if v is None or v == "" or v == []:
            continue
        flattened = _neo4j_value(k, v)
        if flattened is None or flattened == []:
            continue
        result[k] = flattened
    return result


def strip_embeddings(chunk: dict) -> dict:
    """A copy of `chunk` with its vector removed, for persisting to disk.

    An embedding is a pure function of (text, embedding model) -- derived,
    deterministic, and free to recompute locally. KG staging is committed to
    git (docs/vault.md), and a vector changes completely when one word of its
    text changes, so it cannot be delta-compressed: ten versions of one
    chunks.json cost 60 KB of git objects with embeddings and 20 KB without.

    Returns a copy so the in-memory chunk keeps its vector -- the same run
    still writes it to the graph.
    """
    if "embedding" not in chunk:
        return chunk
    return {k: v for k, v in chunk.items() if k != "embedding"}


EMBEDDING_SIDECAR = "embeddings.json"


def write_embedding_sidecar(doc_kg_dir: Path, chunks: list[dict]) -> int:
    """Persist chunk vectors beside the staging, gitignored.

    Staging is committed and vectors cannot be delta-compressed, so they are
    stripped from it (see `strip_embeddings`) -- but discarding them outright
    would make every ingest embed twice, since the graph write re-reads the
    chunk JSON from disk. The sidecar keeps the local copy without putting it
    in git.
    """
    vectors = {c["chunk_id"]: c["embedding"] for c in chunks if c.get("embedding")}
    if not vectors:
        return 0
    (Path(doc_kg_dir) / EMBEDDING_SIDECAR).write_text(
        json.dumps(vectors), encoding="utf-8"
    )
    return len(vectors)


def read_embedding_sidecar(doc_kg_dir: Path) -> dict:
    """Locally-cached vectors, or `{}`. Absent is the normal state after a
    fresh clone, never an error."""
    try:
        return json.loads((Path(doc_kg_dir) / EMBEDDING_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}






# Properties never tracked in the per-document provenance ledger (A1e): fixed
# identity keys and internal machinery. Everything else an ingest asserts
# (description, type, aliases, context, and domain properties) is accretive and
# therefore attributable to the document(s) that contributed it.
_UNTRACKED_PROP_KEYS = frozenset(
    {"name", "entity_class", "domain", "id", "embedding", "_prop_sources"}
)
# Ledger key for a value that predates the ledger (or has no known source): its
# contribution can never be attributed to a document, so purge must never drop
# it. Seeded once, on the first ledgered touch of a pre-A1e entity.
_LEGACY_SOURCE = "__legacy__"








def _ensure_neo4j_schema(session, embedding_dim: int = 768) -> None:
    _setup_neo4j(session, embedding_dim)




def entity_embedding_text(name: str, description: str | None) -> str:
    """Text embedded for an entity — name plus description when available."""
    return f"{name}: {description}" if description else name


def embed_missing_entity_embeddings(
    session, domain: str, embed_model: str, keys: list | None = None
) -> int:
    """The post-commit embed sweep.

    Matches on ``embedding IS NULL OR embedding_stale`` and clears the flag as
    it writes. Runs **after** the commit, outside any transaction, because it
    calls the embedding service — which is exactly why the rebuild cannot do
    this itself and marks `embedding_stale` instead.

    **It never nulls an embedding.** A null embedding is absent from the
    `entity_embedding` vector index, which makes the entity invisible to
    `entity-resolve`'s vector leg rather than merely less accurate. A stale
    embedding still finds the entity; a null one deletes it from semantic
    search. So a failure here skips that entity and leaves the flag set for
    the next sweep.

    Scoped to `keys` when given — an incremental ingest sweeps only what it
    dirtied, not the whole domain.
    """
    scope = ""
    params: dict = {"domain": domain}
    if keys:
        scope = " AND e.key IN $keys"
        params["keys"] = [k if isinstance(k, str) else "|".join(k) for k in keys]

    rows = session.run(
        f"""
        MATCH (e:Entity)
        WHERE (e._domain = $domain OR e._domain STARTS WITH ($domain + '.'))
          AND e.name IS NOT NULL
          AND (e.embedding IS NULL OR e.embedding_stale){scope}
        RETURN e._id AS id, e.name AS name, e.description AS description
        """,
        **params,
    ).data()

    count, failed = 0, 0
    for row in rows:
        try:
            embedding = _embed_text(
                embed_model, entity_embedding_text(row["name"], row.get("description"))
            )
        except Exception as e:
            failed += 1
            logger.warning("Entity embedding failed for {!r}: {}", row["name"], e)
            continue
        # Written together with clearing the flag, so an entity is never left
        # claiming to be fresh while holding an old vector.
        session.run(
            "MATCH (e:Entity {_id: $id}) SET e.embedding = $embedding, e.embedding_stale = false",
            id=row["id"],
            embedding=embedding,
        )
        count += 1
    if count or failed:
        logger.info(
            "Embed sweep: {} entity node(s) embedded, {} skipped (still marked stale)",
            count, failed,
        )
    return count


def embed_entities_backfill(domain: str) -> dict:
    """Backfill embeddings for all entities in a domain that lack one or are stale."""
    from artmind.graph_query import neo4j_session

    env = load_env()
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    with neo4j_session() as session:
        embedded = embed_missing_entity_embeddings(session, domain, embed_model)
    return {"domain": domain, "entities_embedded": embedded}


def _retract_prior_version(tx, domain: str, doc_id: str) -> dict:
    """Move everything the previous version of ``doc_id`` asserted out of
    ``latest`` — the version/status transition that replaces the old
    hard-retraction.

    Assertion-time only, and carried entirely by a **label swap**
    (`:Observation` -> `:ObservationHistory`, `:DocChunk` -> `:DocChunkHistory`)
    rather than a status property: a demoted node keeps the valid-time window
    it always had, stays queryable by asking for it (see `entity_history`), and
    the label swap is what structurally drops it out of `chunk_text_ft` /
    `chunk_embedding` / every projection query — no predicate to forget, no
    index to filter. Nothing is deleted, so provenance survives a re-ingest.
    Chunks used to be `DETACH DELETE`d here; they are relabelled instead.

    **No entity GC happens here.** Which entities should now disappear is the
    projection's business, decided by the zero-latest-observations rule over
    the affected keys. Doing it here is how the old code ended up with three
    competing, silently-failing GC mechanisms.

    **No edge retraction happens here either.** `RELATES_TO` aggregate edges
    are entirely derived from `ASSERTS_RELATION` observation edges, which
    themselves live on `:Observation` nodes — relabelling a document's
    observations to `:ObservationHistory` makes their `ASSERTS_RELATION` edges
    structurally invisible to the next rebuild's aggregation query, and the
    affected-key rebuild (which already includes every key this document's
    observations touch) recomputes the aggregate edges from scratch. Nothing
    here has to know an edge existed.

    Runs inside the caller's transaction.
    """
    result = {"observations_demoted": 0, "chunks": 0}

    rec = tx.run(
        """
        MATCH (o:Observation {doc_id: $doc_id})
        REMOVE o:Observation SET o:ObservationHistory
        RETURN count(o) AS n
        """,
        doc_id=doc_id,
    ).single()
    result["observations_demoted"] = int(rec["n"]) if rec else 0

    rec = tx.run(
        """
        MATCH (c:DocChunk {doc_id: $doc_id})
        REMOVE c:DocChunk SET c:DocChunkHistory
        RETURN count(c) AS n
        """,
        doc_id=doc_id,
    ).single()
    result["chunks"] = int(rec["n"]) if rec else 0
    return result


def ingest_to_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
    chunk_size: int = 6000,
    stage_only: bool = False,
    defer_rebuild: bool = False,
) -> bool:
    """Orchestrate KG extraction and (unless stage_only) commit for one document.

    Re-ingesting a known identity is always a replace now (`--replace` is
    gone) — the commit always hard-retracts whatever the reused ``doc_id``'s
    prior graph contributions were before re-committing, which is a safe
    no-op for a document that's genuinely new (nothing to retract).

    This path only ever runs the three-tier delta classifier (A4, ADR 0006 (f))
    for a *binary-source* re-ingest — a vault-native document's metadata-only
    fast path is decided earlier, in `_ingest_vault_native`, and never reaches
    here at all (`"logical_id" in file_result` is precisely binary-only:
    vault-native carries `artmind_id`, never `logical_id`).
    """
    # A4: metadata-only fast path. Never for stage_only (that path is a
    # staging test that must produce doc_kg_dir).
    if not stage_only and "logical_id" in file_result:
        try:
            from artmind.delta import classify_reingest, apply_metadata_only

            registered_path = Path(file_result["registered_path"])
            md_file = MARKDOWNS_DIR / f"{registered_path.stem}.md"
            if md_file.exists():
                classification = classify_reingest(
                    md_file,
                    domain=domain,
                    logical_id=file_result["logical_id"],
                    chunk_size=chunk_size,
                )
                if classification.tier == "metadata_only" and classification.doc_id:
                    logger.info(
                        "A4 delta: {} — {} (fast path)",
                        classification.tier,
                        classification.reason,
                    )
                    summary = apply_metadata_only(
                        doc_id=classification.doc_id,
                        domain=domain,
                        metadata=classification.metadata,
                    )
                    logger.info(
                        "A4 metadata-only apply: doc_id={} applied={} removed={}",
                        summary["doc_id"], list(summary["applied"].keys()), summary["removed"],
                    )
                    return True
                logger.info(
                    "A4 delta: {} — {} (full pipeline)",
                    classification.tier,
                    classification.reason,
                )
        except Exception as e:
            logger.warning("A4 delta classifier skipped ({}); running full pipeline", e)

    # Back-compat: if ingest_file didn't split chunks yet, do it now.
    if "chunks_dir" not in file_result:
        registered_path = Path(file_result["registered_path"])
        # `markdown_path_for` exists precisely to replace hand-built
        # `MARKDOWNS_DIR / f"{stem}.md"` paths, which are wrong for a
        # vault-native document: Phase 2 stopped copying those into the data
        # dir, so the vault file IS the markdown. This call site was missed,
        # and it turned every metadata-only vault-native re-ingest that reached
        # here into "Markdown not found".
        source_type = file_result.get(
            "source_type", "md" if "artmind_id" in file_result else "other"
        )
        md_file = markdown_path_for(
            source_type, vault_path=registered_path, stem=registered_path.stem
        )
        if not md_file.exists():
            logger.error("Markdown not found: {}", md_file)
            return False
        _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
        chunks = _split_markdown(body, chunk_size)
        chunks_dir = MARKDOWNS_DIR / f"{registered_path.stem}_chunks"
        _persist_chunks(chunks, chunks_dir)
        file_result["chunks_dir"] = str(chunks_dir)
        file_result["chunk_count"] = len(chunks)
        file_result.setdefault("sha256", _compute_sha256(registered_path))

    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model)
    if doc_kg_dir is None:
        return False
    if stage_only:
        logger.info("Staged (not committed): {}", doc_kg_dir)
        return True
    return commit_to_graph(doc_kg_dir, domain, defer_rebuild=defer_rebuild)


def _resolve_ingest_workers(chunk_count: int, override: int | None = None) -> int:
    """Chunk-level fan-out width, clamped to [1, chunk_count].

    Chunks are independent units of work (each does its own embedding + 3 LLM
    calls and writes its own JSON/status row), so they parallelize cleanly. The
    win is real only when the LLM backend serves concurrent requests: OpenRouter
    does, local Ollama largely serializes on one GPU. So the default is
    provider-aware — 4 for openrouter, 1 (today's sequential behaviour) for
    ollama — and either can be overridden via ARTMIND_INGEST_MAX_WORKERS or the
    `override` argument (CLI/caller).
    """
    if override is None:
        env = load_env()
        raw = env.get("ARTMIND_INGEST_MAX_WORKERS")
        if raw is not None and str(raw).strip():
            try:
                override = int(raw)
            except ValueError:
                logger.warning("Invalid ARTMIND_INGEST_MAX_WORKERS={!r}; ignoring", raw)
        if override is None:
            provider = env.get("ARTMIND_KG_LLM_PROVIDER", "ollama")
            override = 4 if provider == "openrouter" else 1
    return max(1, min(override, chunk_count)) if chunk_count > 0 else 1


def _document_valid_time(md_file: Path, frontmatter: dict, schema: dict) -> dict:
    """The document's valid-time window, resolved at ingest.

    Frontmatter first (Phase 2's `_valid_from` / `_valid_to` are part of the
    system contract), then the document's own header table via the schema's
    `temporal.document` mapping, then the schema default.

    Emitted with `_`-prefixed names because artmind owns them — the underscore
    IS the rule, and an LLM-extracted `status` colliding with the system's own
    is exactly what row 9 of the scorecard counts.
    """
    from artmind.temporal import lift_document_dates

    out: dict = {}
    if frontmatter.get("_valid_from"):
        out["_valid_from"] = str(frontmatter["_valid_from"])
    if frontmatter.get("_valid_to"):
        out["_valid_to"] = str(frontmatter["_valid_to"])
    if frontmatter.get("_valid_time_source"):
        out["_valid_time_source"] = str(frontmatter["_valid_time_source"])
    if out.get("_valid_from"):
        out.setdefault("_valid_time_source", "frontmatter")
        return out

    temporal = schema.get("temporal") or {}
    mapping = temporal.get("document") or {}
    defaults = temporal.get("defaults") or {}
    body = ""
    if md_file.exists():
        try:
            _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read {} for valid-time lifting: {}", md_file, e)

    lifted = lift_document_dates(body, frontmatter, mapping, defaults) if mapping else {}
    if lifted.get("valid_from"):
        out["_valid_from"] = lifted["valid_from"]
    if lifted.get("valid_to"):
        out["_valid_to"] = lifted["valid_to"]
    if lifted.get("time_source"):
        out["_valid_time_source"] = lifted["time_source"]
    if lifted.get("version"):
        # The document's own "| Version | 2.1 |" header — a string with no
        # system meaning, kept apart from the integer `_version`.
        out["declared_version"] = lifted["version"]

    return out


def _build_observations(
    entities: list[dict],
    properties: list[dict],
    canonical_names: dict[str, str],
    schema: dict,
    document: dict,
) -> list[dict]:
    """Turn this document version's extraction output into Observation props.

    One observation per (chunk, extracted-entity-identity). The entity's own
    schema-declared date property (a property tagged `temporal: valid_from`)
    overrides the document's for the observation's **fact-level** valid time,
    while `_doc_valid_from` always carries the document's — the two axes the
    rebuild needs to tell a conflict from ordinary history.
    """
    from artmind.observations import build_observation, class_kind
    from artmind.temporal import _entity_temporal_mapping, parse_iso

    props_by_id = {p["id"]: p.get("properties", {}) for p in properties}
    entity_dates = _entity_temporal_mapping(schema)
    doc_valid_from = document.get("_valid_from")
    doc_valid_to = document.get("_valid_to")
    doc_id = document["id"]
    doc_version = document.get("version") or 1

    observations: list[dict] = []
    seen: set[str] = set()
    for entity in entities:
        chunk_id = entity.get("chunk_id") or ""
        raw_name = entity.get("name") or ""
        canonical = canonical_names.get(raw_name) or raw_name
        if not canonical or not chunk_id:
            continue

        domain_props = dict(props_by_id.get(entity.get("id"), {}))

        # A fact carrying its own dates overrides the document's.
        fact_valid_from, fact_valid_to = None, None
        for canon_key, prop_name in (entity_dates.get(entity.get("entity_class") or "") or {}).items():
            parsed = parse_iso(str(domain_props.get(prop_name))) if domain_props.get(prop_name) else None
            if not parsed:
                continue
            if canon_key == "valid_from":
                fact_valid_from = parsed
            elif canon_key == "valid_to":
                fact_valid_to = parsed

        observation = build_observation(
            entity,
            canonical_name=canonical,
            domain_props=domain_props,
            doc_id=doc_id,
            doc_version=int(doc_version) if str(doc_version).isdigit() else 1,
            chunk_id=chunk_id,
            kind=class_kind(schema, entity.get("entity_class") or ""),
            doc_valid_from=doc_valid_from,
            valid_from=fact_valid_from,
            valid_to=fact_valid_to or doc_valid_to,
            valid_time_source=("property" if fact_valid_from else document.get("_valid_time_source")),
        )
        # Two chunks asserting the same identity produce the same id only when
        # they ARE the same chunk; within one chunk, a repeated entity is one
        # observation, not several.
        if observation["id"] in seen:
            continue
        seen.add(observation["id"])
        observations.append(observation)
    return observations


def extract_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
    max_workers: int | None = None,
) -> Path | None:
    """Extract KG from persisted chunks and merge into document-level JSON files.

    Resumable: already-ok steps are skipped. Failed steps get a second attempt
    in the pre-merge retry pass before the merge proceeds.

    Chunks in the first pass are processed concurrently across a bounded thread
    pool (see _resolve_ingest_workers); the 3 extraction steps stay sequential
    *within* a chunk (properties/relationships depend on the entities output).
    Returns doc_kg_dir on success, None if prerequisites are missing.
    """
    doc_sha256 = file_result.get("sha256", "")
    chunks_dir = Path(file_result["chunks_dir"])
    registered_path = Path(file_result["registered_path"])
    source_type = file_result.get("source_type", "md" if "artmind_id" in file_result else "other")
    md_file = markdown_path_for(source_type, vault_path=registered_path, stem=registered_path.stem)

    # Read filing metadata from markdown frontmatter (ADR 0010)
    # Chunks get a denormalized copy so they can be filtered by filing taxonomy
    filing_metadata: dict = {}
    if md_file.exists():
        try:
            meta, _ = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
            if meta.get("project"):
                filing_metadata["project"] = str(meta["project"])
            if meta.get("area"):
                filing_metadata["area"] = str(meta["area"])
            if meta.get("tags"):
                tags = meta["tags"]
                if isinstance(tags, str):
                    filing_metadata["tags"] = [t.strip() for t in tags.split(",")]
                elif isinstance(tags, list):
                    filing_metadata["tags"] = [str(t) for t in tags]
        except Exception as e:
            logger.warning("Failed to read filing metadata from {}: {}", md_file, e)

    if not chunks_dir.exists():
        logger.error("Chunks directory not found: {}", chunks_dir)
        return None

    domain_schema_file = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not domain_schema_file.exists():
        logger.error("Domain schema not found: {}", domain_schema_file)
        return None
    schema = yaml.safe_load(domain_schema_file.read_text(encoding="utf-8"))

    doc_kg_dir = KG_DIR / domain / registered_path.stem
    doc_kg_dir.mkdir(parents=True, exist_ok=True)
    # Scoped by doc_sha256 (not just the file stem) so a genuine resume of an
    # interrupted run — same content, same sha256 — still hits this cache and
    # skips already-ok steps, while a re-ingest with *changed* content lands in
    # a fresh, empty directory instead of silently inheriting a prior version's
    # cached chunk text/embedding via the setdefault() calls below.
    chunk_data_dir = doc_kg_dir / "chunks" / doc_sha256
    chunk_data_dir.mkdir(parents=True, exist_ok=True)

    # Identity: a vault-native document's `_ingest_vault_native` already
    # resolved identity+version (docs/document-identity.md) and handed it
    # through `file_result["artmind_id"]`/`["version"]` — `doc_id` IS the
    # `_artmind_id`, one identity, not two parallel ones. A binary source has
    # no frontmatter to carry an id, so it keeps the pre-Phase-2 path: resolve
    # the physical doc_id + version by `logical_id` against the graph (a
    # document already there keeps its id and bumps version; otherwise a
    # fresh id at version 1). A resumed partial extraction (chunk-status rows
    # keyed by the extraction sha256) supplies its already-minted doc_id as
    # the fresh-mint fallback so a resume keeps its id.
    logical_id = None
    if "artmind_id" in file_result:
        doc_id = file_result["artmind_id"]
        version = file_result["version"]
    else:
        logical_id = file_result.get("logical_id") or _logical_id(
            domain, _canonical_key(registered_path, domain)
        )
        existing = _get_chunk_statuses(doc_sha256)
        resumed_doc_id = next(iter(existing.values()))["doc_id"] if existing else None
        doc_id, version = _resolve_doc_identity(domain, logical_id, resumed_doc_id)

    # ── name vocabulary, retrieved ONCE before any chunk extracts ─────────
    # Cross-document drift control: showing the extractor names already in use
    # stops a new document coining a fresh one for something the graph knows.
    # Restricted to recurrent classes — an occurrent entity is a completed
    # event, and offering existing event names invites folding two distinct
    # incidents into one. Never fatal: no vocabulary is the pre-redesign
    # behaviour, so a down embed service costs quality, not the ingest.
    vocabulary: list = []
    try:
        from artmind.canonicalize import retrieve_vocabulary
        from artmind.graph_query import read_session

        seed_text = "\n\n".join(
            f.read_text(encoding="utf-8")[:1500] for f in sorted(chunks_dir.glob("chunk_*.md"))[:3]
        )
        with read_session() as vocab_session:
            vocabulary = retrieve_vocabulary(
                vocab_session, domain=domain, schema=schema,
                seed_text=seed_text, embed_model=embed_model,
            )
    except Exception as e:
        logger.warning("Name vocabulary unavailable, extracting without it ({})", e)

    # ── property-key vocabulary, retrieved ONCE before any chunk extracts ──
    # Cross-document drift control for property KEYS, the same problem
    # `vocabulary` above solves for entity NAMES: a property key already
    # committed to the graph for this class family should be reused rather
    # than reinvented (`balance_minimum` vs. a fresh `balance_range_minimum`
    # for the same concept). No embedding needed here -- see
    # `retrieve_property_vocabulary`'s docstring. Never fatal, same fail-open
    # contract as the name vocabulary above.
    property_vocabulary: dict = {}
    try:
        from artmind.canonicalize import retrieve_property_vocabulary
        from artmind.graph_query import read_session

        with read_session() as prop_vocab_session:
            property_vocabulary = retrieve_property_vocabulary(
                prop_vocab_session, domain=domain, schema=schema,
            )
    except Exception as e:
        logger.warning("Property vocabulary unavailable, extracting without it ({})", e)

    chunk_files = sorted(chunks_dir.glob("chunk_*.md"))
    chunk_count = len(chunk_files)
    logger.info(
        "KG extraction: {} | {} chunk(s) | model={} | embed={}",
        registered_path.name,
        chunk_count,
        text_model,
        embed_model,
    )
    t0 = time.monotonic()

    _init_chunk_rows(doc_sha256, doc_id, chunk_count)

    # Embeddings never land in the per-chunk JSON on disk (see strip_embeddings
    # below) -- a sidecar, gitignored, is the only place a vector persists.
    # Read it once so a resumed run doesn't pay for a re-embed of a chunk it
    # already has a vector for, and accumulate this run's vectors here so
    # they can be written back out once, alongside chunks.json.
    embedding_sidecar = read_embedding_sidecar(doc_kg_dir)
    computed_embeddings: dict[str, list] = {}

    def _process_chunk(seq: int, chunk_file: Path, statuses: dict) -> None:
        chunk_text = chunk_file.read_text(encoding="utf-8")
        chunk_id = f"{doc_id}_{seq:03d}"
        chunk_json = chunk_data_dir / f"chunk_{seq:03d}.json"
        status = statuses.get(seq, {})

        # Load existing per-chunk data (enables skipping already-ok steps on resume)
        data: dict = {}
        if chunk_json.exists():
            try:
                data = json.loads(chunk_json.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Embedding — compute once and persist (to the sidecar, not the chunk
        # JSON: see strip_embeddings). A vector already in the sidecar from a
        # prior run of this same document is reused rather than recomputed.
        if "embedding" not in data and chunk_id in embedding_sidecar:
            data["embedding"] = embedding_sidecar[chunk_id]
        if "embedding" not in data:
            try:
                data["embedding"] = _embed_text(embed_model, chunk_text)
            except Exception as e:
                logger.error("  Embedding failed for chunk {}: {}", seq, e)
                data["embedding"] = []
        if data["embedding"]:
            computed_embeddings[chunk_id] = data["embedding"]

        data.setdefault("chunk_seq", seq)
        data.setdefault("chunk_id", chunk_id)
        data.setdefault("doc_id", doc_id)
        data.setdefault("text", chunk_text)
        data.setdefault("name", f"Chunk {seq}/{chunk_count}")
        data.setdefault("domain", domain)
        # Denormalized filing metadata (ADR 0010) for fast filtering
        for key, value in filing_metadata.items():
            data.setdefault(key, value)
        if seq > 1:
            data.setdefault("prev_chunk_id", f"{doc_id}_{seq - 1:03d}")
        if seq < chunk_count:
            data.setdefault("next_chunk_id", f"{doc_id}_{seq + 1:03d}")

        entities_status = status.get("entities_status", "pending")

        # ── entities ──────────────────────────────────────────────────────────
        if entities_status != "ok":
            logger.info("  Chunk {} — entities", seq)
            raw_entities, ok = _llm_extract(
                f"chunk_{seq:03d}_entities",
                text_model,
                build_entities_prompt(chunk_text, schema, vocabulary=vocabulary),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "entities", "ok" if ok else "failed")
            if ok:
                raw_entities = _filter_valid_items(raw_entities, seq, "entity", required_key="id")
                entities, id_map = _rewrite_entity_ids(raw_entities, chunk_id)
                data["raw_entities"] = raw_entities
                data["id_map"] = id_map
                data["entities"] = [
                    {**e, "chunk_id": chunk_id, "doc_id": doc_id, "domain": domain}
                    for e in entities
                ]
            else:
                data.setdefault("raw_entities", [])
                data.setdefault("id_map", {})
                data.setdefault("entities", [])
            entities_status = "ok" if ok else "failed"
        else:
            raw_entities = data.get("raw_entities", [])

        id_map = data.get("id_map", {})
        has_entities = bool(raw_entities)

        # ── properties ────────────────────────────────────────────────────────
        properties_status = status.get("properties_status", "pending")
        if has_entities and entities_status == "ok" and properties_status != "ok":
            logger.info("  Chunk {} — properties", seq)
            raw_props, ok = _llm_extract(
                f"chunk_{seq:03d}_properties",
                text_model,
                build_properties_prompt(
                    chunk_text, data.get("raw_entities", []), schema, vocabulary=property_vocabulary
                ),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "properties", "ok" if ok else "failed")
            if ok:
                raw_props = _filter_valid_items(raw_props, seq, "property")
                props = _rewrite_ref_ids(raw_props, id_map, "id")
                data["properties"] = [
                    {**p, "chunk_id": chunk_id, "doc_id": doc_id} for p in props
                ]
            else:
                data.setdefault("properties", [])
        elif not has_entities or entities_status != "ok":
            data.setdefault("properties", [])
            _update_chunk_step(doc_sha256, seq, "properties", "skipped")

        # ── relationships ─────────────────────────────────────────────────────
        relationships_status = status.get("relationships_status", "pending")
        if has_entities and entities_status == "ok" and relationships_status != "ok":
            logger.info("  Chunk {} — relationships", seq)
            raw_rels, ok = _llm_extract(
                f"chunk_{seq:03d}_relationships",
                text_model,
                build_relationships_prompt(chunk_text, data.get("raw_entities", []), schema),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "relationships", "ok" if ok else "failed")
            if ok:
                raw_rels = _filter_valid_items(raw_rels, seq, "relationship")
                rels = _rewrite_ref_ids(raw_rels, id_map, "source_id", "target_id")
                rels = [{**r, "chunk_id": chunk_id, "doc_id": doc_id} for r in rels]
                for e in data.get("entities", []):
                    rels.append({
                        "source_id": e["id"],
                        "source_name": e["name"],
                        "target_id": chunk_id,
                        "target_name": chunk_id,
                        "rel_type": "EXTRACTED_FROM",
                        "description": "Entity extracted from this document chunk",
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "domain": domain,
                    })
                data["relationships"] = rels
            else:
                data.setdefault("relationships", [])
        elif not has_entities or entities_status != "ok":
            data.setdefault("relationships", [])
            _update_chunk_step(doc_sha256, seq, "relationships", "skipped")

        chunk_json.write_text(
            json.dumps(strip_embeddings(data), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── first pass ────────────────────────────────────────────────────────────
    statuses = _get_chunk_statuses(doc_sha256)
    workers = _resolve_ingest_workers(chunk_count, max_workers)
    if workers <= 1:
        for seq, chunk_file in enumerate(chunk_files, start=1):
            logger.info("Chunk {}/{} ({} bytes)", seq, chunk_count, chunk_file.stat().st_size)
            _process_chunk(seq, chunk_file, statuses)
    else:
        # NOTE(429-backoff): this fan-out is exactly what can trip a provider's
        # rate limit — `workers` concurrent LLM requests per document. If you raise
        # ARTMIND_INGEST_MAX_WORKERS / --maxWorkers and start seeing HTTP 429s,
        # the fix lives in extract_with_retry (extraction.py), not here.
        logger.info("Extracting {} chunk(s) with {} workers", chunk_count, workers)
        # Fan out per-chunk. Each _process_chunk is self-contained: it reads its
        # own chunk file, writes chunk_{seq}.json to a unique path, and records
        # status in its own SQLite row (WAL + busy-timeout make concurrent writes
        # safe). Completion order is irrelevant — the merge pass below reads back
        # by seq. A worker that raises is logged; the first exception is re-raised
        # after the pool drains so an unexpected failure still surfaces (matching
        # the sequential path), while chunks that did finish stay on disk for
        # resume.
        first_exc: Exception | None = None
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_process_chunk, seq, chunk_file, statuses): seq
                for seq, chunk_file in enumerate(chunk_files, start=1)
            }
            for future in as_completed(futures):
                seq = futures[future]
                try:
                    future.result()
                except Exception as e:
                    logger.error("Chunk {} raised during extraction: {}", seq, e)
                    if first_exc is None:
                        first_exc = e
        if first_exc is not None:
            raise first_exc

    # ── pre-merge retry pass ───────────────────────────────────────────────────
    statuses = _get_chunk_statuses(doc_sha256)
    failed_seqs = [
        seq for seq, s in statuses.items()
        if "failed" in (s["entities_status"], s["properties_status"], s["relationships_status"])
    ]
    if failed_seqs:
        logger.info("Pre-merge retry: {} chunk(s) with failed steps — retrying", len(failed_seqs))
        for seq in failed_seqs:
            chunk_file = chunks_dir / f"chunk_{seq:03d}.md"
            _process_chunk(seq, chunk_file, statuses)

    # ── merge chunk JSONs into document-level files ────────────────────────────
    all_chunks: list[dict] = []
    all_entities: list[dict] = []
    all_properties: list[dict] = []
    all_relationships: list[dict] = []

    # Block-level provenance sidecar (A1a). Absent for pre-A1a ingests, in which
    # case chunks simply carry no offset/header/hash — downstream tolerates that.
    chunks_meta: dict = {}
    meta_path = chunks_dir / "chunks_meta.json"
    if meta_path.exists():
        try:
            chunks_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            chunks_meta = {}

    for seq in range(1, chunk_count + 1):
        chunk_json = chunk_data_dir / f"chunk_{seq:03d}.json"
        if not chunk_json.exists():
            logger.warning("Missing chunk JSON for seq {}, skipping in merge", seq)
            continue
        data = json.loads(chunk_json.read_text(encoding="utf-8"))
        chunk_node = {k: data[k] for k in ("name", "doc_id", "text", "embedding", "domain") if k in data}
        chunk_node["id"] = data["chunk_id"]
        # Include denormalized filing metadata (ADR 0010)
        for key in ("project", "area", "tags"):
            if key in data:
                chunk_node[key] = data[key]
        for link in ("prev_chunk_id", "next_chunk_id"):
            if link in data:
                chunk_node[link] = data[link]
        cmeta = chunks_meta.get(f"{seq:03d}")
        if cmeta:
            for key in ("char_start", "char_end", "header_path", "block_hash"):
                if cmeta.get(key) is not None:
                    chunk_node[key] = cmeta[key]
        all_chunks.append(chunk_node)
        all_entities.extend(data.get("entities", []))
        all_properties.extend(data.get("properties", []))
        all_relationships.extend(data.get("relationships", []))

    # Build document node from markdown frontmatter
    meta = {}
    if md_file.exists():
        meta, _ = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    document: dict = {
        "id": doc_id,
        "version": version,
        "name": registered_path.name,
        "path": str(registered_path),
        "domain": domain,
    }
    if logical_id is not None:
        document["logical_id"] = logical_id
    else:
        document["artmind_id"] = doc_id

    # Filing metadata (ADR 0010): project, area, tags, title, created_on, modified_on
    # All optional; sourced from YAML frontmatter, with sensible defaults for temporal fields.
    if meta.get("title"):
        document["title"] = str(meta["title"])
    if meta.get("project"):
        document["project"] = str(meta["project"])
    if meta.get("area"):
        document["area"] = str(meta["area"])
    if meta.get("tags"):
        tags = meta["tags"]
        if isinstance(tags, str):
            document["tags"] = [t.strip() for t in tags.split(",")]
        elif isinstance(tags, list):
            document["tags"] = [str(t) for t in tags]

    # Temporal metadata: prefer frontmatter, fall back to filesystem
    if meta.get("created_on"):
        document["created_on"] = str(meta["created_on"])
    elif registered_path.exists():
        ctime = registered_path.stat().st_birthtime if hasattr(registered_path.stat(), "st_birthtime") else registered_path.stat().st_ctime
        document["created_on"] = _datetime.datetime.fromtimestamp(ctime, tz=_datetime.timezone.utc).isoformat()

    if meta.get("modified_on"):
        document["modified_on"] = str(meta["modified_on"])
    elif registered_path.exists():
        mtime = registered_path.stat().st_mtime
        document["modified_on"] = _datetime.datetime.fromtimestamp(mtime, tz=_datetime.timezone.utc).isoformat()

    # Legacy support: last_modified is now modified_on
    if "modified_on" in document and "last_modified" not in document:
        document["last_modified"] = document["modified_on"]
    elif registered_path.exists() and "last_modified" not in document:
        mtime = registered_path.stat().st_mtime
        document["last_modified"] = _datetime.datetime.fromtimestamp(mtime, tz=_datetime.timezone.utc).isoformat()

    if meta.get("author"):
        document["author"] = str(meta["author"])
    if meta.get("date"):
        document["date"] = str(meta["date"])

    # ── valid time, derived HERE rather than by a post-write hook ──────────
    # The projection's winner rule is "latest source-document valid_from", so
    # every observation needs its document's valid_from at write time, inside
    # the commit transaction. Deriving it afterwards (as `normalize_time` did)
    # is too late: the rebuild has already run, and the hook that computed it
    # swallowed its own exceptions.
    document_dates = _document_valid_time(md_file, meta, schema)
    document.update(document_dates)

    # ── per-document canonicalization: ONE call, after ALL chunks ──────────
    # Intra-document drift control. Chunks extracted in parallel and could not
    # see each other, so the same thing may carry several names; this is the
    # first point at which anything can see the whole document's output.
    canonical_names = canonicalize_document(
        all_entities, schema=schema, vocabulary=vocabulary,
        model=text_model, debug_dir=doc_kg_dir,
    )

    all_observations = _build_observations(
        all_entities, all_properties, canonical_names, schema, document,
    )

    def _write_json(filename: str, obj: object) -> None:
        (doc_kg_dir / filename).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    _write_json("document.json", document)
    _write_json("chunks.json", [strip_embeddings(c) for c in all_chunks])
    _write_json("entities.json", all_entities)
    _write_json("properties.json", all_properties)
    _write_json("relationships.json", all_relationships)
    _write_json("observations.json", all_observations)

    # Vectors move to a gitignored sidecar rather than vanishing: chunks.json
    # above is what git tracks (and cannot delta a changed vector against),
    # while the sidecar keeps this run's embeddings available locally so the
    # graph write below doesn't have to recompute them.
    write_embedding_sidecar(
        doc_kg_dir,
        [{"chunk_id": cid, "embedding": emb} for cid, emb in computed_embeddings.items()],
    )

    elapsed = time.monotonic() - t0
    final_statuses = _get_chunk_statuses(doc_sha256)
    failed_count = sum(
        1 for s in final_statuses.values()
        if "failed" in (s["entities_status"], s["properties_status"], s["relationships_status"])
    )
    logger.info(
        "KG extraction done in {:.1f}s | chunks={} entities={} observations={} "
        "properties={} relationships={} | chunks_with_failures={}",
        elapsed,
        chunk_count,
        len(all_entities),
        len(all_observations),
        len(all_properties),
        len(all_relationships),
        failed_count,
    )
    return doc_kg_dir


def _merge_relabeled(
    tx, base_label: str, history_label: str, id_value: str, props: dict, *, replace: bool = True
) -> None:
    """`MERGE` a node by `id`, whichever of `base_label` / `history_label` it
    currently carries (or neither, if this id is new), and leave it under
    `base_label` holding `props`.

    Necessary because retraction is a **label swap** now (Phase 4's
    :DocumentHistory/:DocChunkHistory/:ObservationHistory), not a `_status`
    property set. Step 2 of `_commit_document_tx` (`_retract_prior_version`)
    relabels the PRIOR version's chunks/observations to their History
    counterpart before this step writes the new version — and content that is
    stable across versions (a chunk's id is `{doc_id}_{seq}`, deterministic
    regardless of edits; an observation's id is deterministic per chunk +
    canonical name + class + domain) reuses the exact same id both times. A
    plain `MERGE (n:{base_label} {{id: $id}})` would not find that id under
    `history_label` — MERGE's label is part of the match pattern — and would
    silently CREATE A SECOND node with the same id under `base_label`,
    breaking "entities are MERGEd on a deterministic id, never
    delete-and-recreate" one level down (a duplicate observation/chunk, not a
    duplicate entity, but the same defect: elementId churn, and a stale
    History twin left orphaned behind it). Both label branches below are
    indexed lookups (each label carries its own id uniqueness
    constraint/index), so this stays index-backed rather than falling back to
    an unlabelled scan across the whole graph.

    `replace` picks `SET n = $props` (full replace — an observation is a
    complete statement of what one chunk said, so a stale property from a
    previous write must not survive) vs `SET n += $props` (additive — a
    DocChunk's filing metadata / char offsets accrete the way they always
    have).
    """
    set_clause = "SET n = $props" if replace else "SET n += $props"
    tx.run(
        f"""
        OPTIONAL MATCH (o1:{base_label} {{id: $id}})
        OPTIONAL MATCH (o2:{history_label} {{id: $id}})
        WITH coalesce(o1, o2) AS existing
        FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
            CREATE (n:{base_label} {{id: $id}})
            {set_clause}
        )
        FOREACH (n IN CASE WHEN existing IS NOT NULL THEN [existing] ELSE [] END |
            {set_clause}
            SET n:{base_label}
            REMOVE n:{history_label}
        )
        """,
        id=id_value, props=props,
    )


def _write_observations(tx, observations: list[dict], doc_id: str) -> int:
    """Write this document version's observations.

    Immutable records: written whole, never merged into and never patched. The
    id is deterministic (chunk + canonical name + class + domain), so a
    re-write of the same content replaces in place rather than duplicating —
    see `_merge_relabeled` for why that requires matching across both the
    `:Observation` and `:ObservationHistory` labels, not just the former.

    Each observation is a complete statement of what one chunk said, replaced
    whole (never `+=`) — a `+=` would let a property from a previous version
    of the same chunk survive into this one.
    """
    for observation in observations:
        _merge_relabeled(tx, "Observation", "ObservationHistory", observation["id"], observation)
        chunk_id = observation.get("chunk_id")
        if chunk_id:
            tx.run(
                """
                MATCH (o:Observation {id: $id})
                MATCH (c:DocChunk {id: $chunk_id})
                MERGE (o)-[:EXTRACTED_FROM]->(c)
                """,
                id=observation["id"], chunk_id=chunk_id,
            )
    logger.debug("Neo4j: wrote {} observation(s) for {}", len(observations), doc_id)
    return len(observations)


def _observation_lookup(observations: list[dict]) -> tuple[dict, dict]:
    """Two ways to resolve a relationship endpoint's raw extracted name back to
    one of this document's own `:Observation` ids.

    `by_chunk` is the precise match: a relationship is itself chunk-scoped
    (extracted from that chunk's own raw entities), so its `source_name` /
    `target_name` should resolve to an observation from the *same* chunk.
    `by_name` is the doc-wide fallback (first occurrence wins) for the rare
    case a relationship's chunk_id doesn't line up exactly. Both raw `name`
    and `canonical_name` are indexed, since a relationship extractor may echo
    either back.
    """
    by_chunk: dict[tuple[str, str], str] = {}
    by_name: dict[str, str] = {}
    for o in observations:
        chunk_id = o.get("chunk_id")
        for field in ("name", "canonical_name"):
            value = o.get(field)
            if not value:
                continue
            if chunk_id:
                by_chunk.setdefault((chunk_id, value), o["id"])
            by_name.setdefault(value, o["id"])
    return by_chunk, by_name


def _observation_keys(observations: list[dict]) -> dict[str, str]:
    """Observation id -> its aggregate key string. Two *different* chunk-scoped
    observations (different ids) commonly share one key — canonicalization
    folding "Rate A" and "Rate A alias" onto the same entity, say — and a
    relationship between them is a self-loop at the aggregate the projection
    will actually build, even though their raw observation ids differ."""
    return {o["id"]: o.get("key") for o in observations if o.get("id")}


_RELATION_STRUCTURAL_KEYS = frozenset({
    "source_id", "source_name", "target_id", "target_name",
    "rel_type", "chunk_id", "doc_id", "bidirectional",
})


def _write_relation_observations(tx, relationships: list[dict], document: dict, observations: list[dict]) -> int:
    """The immutable, chunk-scoped record of one extracted relationship:
    `(:Observation)-[:ASSERTS_RELATION {rel_type, doc_id, chunk_id, ...}]->(:Observation)`.

    Never merged or patched — like every other observation, it is written
    whole and replaced in place by its deterministic id on a re-write. This is
    the raw layer the projection rebuild aggregates into `RELATES_TO` entity
    edges (see `projection.py`); nothing here touches `:Entity` directly.

    Whatever a relationship extraction carries beyond the structural fields
    (e.g. a schema's own `relates_to` declaration adding "role" or "since")
    flattens onto the edge via `_flatten_props` — nested objects dropped with
    a warning, never JSON-blobbed, same discipline as everywhere else. These
    per-instance properties live only here; the aggregate `RELATES_TO` edge
    carries just `rel_type` plus `observation_count`/`chunk_ids`/`doc_ids` —
    there is no merge policy for arbitrary per-instance properties across many
    contributing observations, the same reason a scalar Entity property is
    never unioned.

    The `relationships` prompt template asks the extractor for a nested
    `properties: object` field per relationship (rel_type-specific detail —
    same shape as the entities prompt's own `properties` step), so it must be
    unwrapped here before flattening, exactly like `_build_observations` does
    for entities (`props_by_id = {..., p.get("properties", {})}`). Passing
    `rel.items()` straight through without this unwrap silently drops the
    entire payload every time, since `_flatten_props`/`_neo4j_value` correctly
    refuses a nested dict — found live during the Phase 8 cutover re-ingest.

    Endpoints are resolved against **this document's own observations** —
    the same limitation the pre-Phase-4 writer always had: a relationship
    whose endpoint wasn't itself extracted as an entity in this document is
    silently dropped, because there is nothing here to resolve it against.
    """
    from artmind.observations import relation_observation_id

    by_chunk, by_name = _observation_lookup(observations)
    obs_keys = _observation_keys(observations)

    def _resolve(name: str | None, chunk_id: str | None) -> str | None:
        if not name:
            return None
        if chunk_id and (chunk_id, name) in by_chunk:
            return by_chunk[(chunk_id, name)]
        return by_name.get(name)

    written = 0
    for rel in relationships:
        rel_type = re.sub(r"[^A-Za-z0-9_]", "_", rel.get("rel_type", "")).upper()
        if not rel_type or rel_type == "EXTRACTED_FROM":
            # Entity->DocChunk provenance is carried by observations now.
            continue
        if rel_type in RESERVED_REL_TYPES:
            logger.warning(
                "Neo4j: reserved rel_type skipped ({} -[{}]-> {}); "
                "only artmind-owned machinery may assert this rel_type",
                rel.get("source_name"), rel_type, rel.get("target_name"),
            )
            continue

        chunk_id = rel.get("chunk_id")
        source_obs = _resolve(rel.get("source_name"), chunk_id)
        target_obs = _resolve(rel.get("target_name"), chunk_id)
        if not source_obs or not target_obs:
            continue
        # Self-loop check is at the AGGREGATE key, not the raw observation id:
        # two distinct chunk-scoped observations commonly canonicalize onto
        # the same entity, and the projection would build a genuine self-loop
        # from them even though source_obs != target_obs here.
        if source_obs == target_obs or obs_keys.get(source_obs) == obs_keys.get(target_obs):
            continue

        rel_doc_id = rel.get("doc_id") or document.get("id", "")
        nested_props = rel.get("properties")
        rel_props = _flatten_props({
            **{k: v for k, v in rel.items()
               if k not in _RELATION_STRUCTURAL_KEYS and k != "properties"},
            **(nested_props if isinstance(nested_props, dict) else {}),
        })
        pairs = [(source_obs, target_obs)]
        if rel.get("bidirectional"):
            pairs.append((target_obs, source_obs))

        for src_obs, tgt_obs in pairs:
            edge_id = relation_observation_id(chunk_id or "", src_obs, rel_type, tgt_obs)
            tx.run(
                """
                MATCH (s:Observation {id: $src})
                MATCH (t:Observation {id: $tgt})
                MERGE (s)-[r:ASSERTS_RELATION {id: $id}]->(t)
                SET r = $props
                SET r.id = $id, r.rel_type = $rel_type, r.doc_id = $doc_id, r.chunk_id = $chunk_id
                """,
                src=src_obs, tgt=tgt_obs, id=edge_id,
                rel_type=rel_type, doc_id=rel_doc_id, chunk_id=chunk_id or "",
                props=rel_props,
            )
            written += 1
    return written


def _commit_document_tx(tx, staged: dict, defer_rebuild: bool = False) -> dict:
    """Everything one document's commit does to the graph, in ONE transaction.

    Order matters: the prior version is demoted *before* the new observations
    land (so a re-ingest replaces rather than accretes), the affected key set
    is captured from **both** versions, and the projection rebuild runs last —
    inside this same transaction.

    **A failure anywhere here fails the whole commit**, deliberately. The
    pre-redesign temporal and supersession hooks caught their own exceptions
    and logged a warning, which meant a broken projection looked exactly like
    a healthy one from the outside. A silently-skipped projection is a
    silently-stale query layer.
    """
    from artmind import projection, same_as
    from artmind.observations import key_string

    document = staged["document"]
    domain = staged["domain"]
    doc_id = document["id"]
    summary: dict = {}

    # 1. The prior version's keys — set 2 of the affected-key union. Captured
    #    BEFORE anything changes, because a rename between versions strands the
    #    old key and nothing else would ever name it again.
    prior_keys = projection.keys_for_document(tx, doc_id)

    # 2. Demote the prior version.
    summary["retracted"] = _retract_prior_version(tx, domain, doc_id)

    # 3. Document + chunks. A re-ingest of a document `docs retire` had moved
    #    to :DocumentHistory must find and revive that same node, not create a
    #    duplicate under :Document — see _merge_relabeled.
    _merge_relabeled(tx, "Document", "DocumentHistory", doc_id, _flatten_props(document), replace=False)
    for chunk in staged["chunks"]:
        chunk_props = _flatten_props({k: v for k, v in chunk.items() if k != "embedding"})
        chunk_props["embedding"] = chunk.get("embedding", [])
        # additive (replace=False): a chunk's filing metadata / char offsets
        # accrete the way they always have — see _merge_relabeled for why the
        # label-pair match is needed at all (a same-doc_id re-ingest reuses
        # this same deterministic chunk id, and step 2 just relabelled the
        # prior version's chunk to :DocChunkHistory earlier in this same
        # transaction).
        _merge_relabeled(tx, "DocChunk", "DocChunkHistory", chunk["id"], chunk_props, replace=False)
        tx.run(
            """
            MATCH (c:DocChunk {id: $id})
            MATCH (d:Document {id: $doc_id})
            MERGE (c)-[:PART_OF]->(d)
            """,
            id=chunk["id"],
            doc_id=chunk["doc_id"],
        )
    summary["chunks"] = len(staged["chunks"])

    # 4. Observations.
    observations = staged["observations"]
    summary["observations"] = _write_observations(tx, observations, doc_id)

    incoming_keys = {
        (o["key"].split("|")[0], o["key"].split("|")[1], o["key"].split("|")[2])
        for o in observations if o.get("key") and o["key"].count("|") == 2
    }

    # 4b. Retractions — any observation just written that carries `_retracts`
    #     demotes its target to history (or deletes the target relationship
    #     edge) before the rebuild runs. The target's own key may differ from
    #     anything else this document touched, so it joins `incoming_keys` —
    #     `affected_keys` doesn't distinguish why a key is in scope, only that
    #     it is.
    retracted_keys = projection.apply_retractions(tx, observations)
    incoming_keys |= retracted_keys
    summary["retracted_observations"] = sorted(key_string(k) for k in retracted_keys)

    # 5. Relationship observations — raw, immutable ASSERTS_RELATION edges
    #    between this document's own Observation nodes. Written before the
    #    rebuild (not after, as the old direct-to-Entity writer required):
    #    the rebuild's relationship aggregation reads ASSERTS_RELATION, so it
    #    has to exist first, and both endpoints are already guaranteed to be
    #    in `incoming_keys` (a relationship's endpoints are always entities
    #    extracted in this same document) — no separate key-tracking needed.
    summary["relationships"] = _write_relation_observations(
        tx, staged["relationships"], document, observations
    )

    # 6. The affected-key union, then the rebuild — which now also aggregates
    #    RELATES_TO edges for every key it touches.
    keys = projection.affected_keys(
        incoming=list(incoming_keys),
        prior=list(prior_keys),
        same_as_groups=same_as.groups_touching(incoming_keys | prior_keys),
    )
    if defer_rebuild:
        # Directory ingest: one full rebuild at the end instead of N incremental
        # ones. The keys still have to come back to the caller, since the sweep
        # is scoped to them.
        summary["deferred_keys"] = sorted(keys)
        summary["projection"] = {"deferred": True}
    else:
        summary["projection"] = projection.rebuild(
            tx, keys, synthesis_loader=lambda k: projection.load_synthesis(tx, k)
        )
        summary["deferred_keys"] = []
    summary["affected_keys"] = sorted(keys)

    return summary


def _load_staged(doc_kg_dir: Path, domain: str) -> dict | None:
    """Read the staged JSON one document's commit needs.

    `chunks.json` carries no vectors (see `strip_embeddings`) -- the sidecar
    beside it, if present, supplies them here so the graph write below still
    gets a vector without recomputing it. A fresh clone has no sidecar, so
    its chunks come back with none; a later sweep (Task 2) fills those in.
    """
    def _load(name: str, default=None):
        path = doc_kg_dir / name
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        chunks = _load("chunks.json", [])
        sidecar = read_embedding_sidecar(doc_kg_dir)
        if sidecar:
            for chunk in chunks:
                if "embedding" not in chunk and chunk.get("id") in sidecar:
                    chunk["embedding"] = sidecar[chunk["id"]]
        return {
            "domain": domain,
            "document": _load("document.json"),
            "chunks": chunks,
            "observations": _load("observations.json", []),
            "relationships": _load("relationships.json", []),
        }
    except Exception as e:
        logger.error("Failed to load KG JSON files from {}: {}", doc_kg_dir, e)
        return None


def _write_to_neo4j(doc_kg_dir: Path, domain: str | None = None, defer_rebuild: bool = False) -> dict | None:
    """Commit one staged document to Neo4j in a single transaction.

    Returns the commit summary, or None if the staged files could not be read.
    A Neo4j failure **raises** — it is the caller's job to decide what a failed
    commit means, and the old `return False` let callers treat a failed graph
    write as a soft outcome.
    """
    env = load_env()
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))

    staged = _load_staged(doc_kg_dir, domain or "")
    if not staged or not staged.get("document"):
        return None
    staged["domain"] = domain or staged["document"].get("domain", "")

    from artmind.graph_query import neo4j_session

    with neo4j_session() as session:
        _ensure_neo4j_schema(session, embedding_dim)
        summary = session.execute_write(_commit_document_tx, staged, defer_rebuild)

    logger.info(
        "Neo4j commit: {} — {} chunk(s), {} observation(s), projection {}",
        staged["document"].get("name", doc_kg_dir.name),
        summary["chunks"], summary["observations"], summary["projection"],
    )
    return summary


def write_to_graph(doc_kg_dir: Path, domain: str | None = None) -> bool:
    """Write staged KG JSON to Neo4j. Safe to re-run after fixing Neo4j issues."""
    try:
        return _write_to_neo4j(doc_kg_dir, domain) is not None
    except Exception as e:
        logger.error("Neo4j write failed for {}: {}", doc_kg_dir, e)
        return False


def commit_to_graph(doc_kg_dir: Path, domain: str, defer_rebuild: bool = False) -> bool:
    """Commit one staged document: chunks, observations and the projection
    rebuild, in a single transaction, followed by the post-commit embed sweep.

    This is the single convergence point for all three ingestion sources
    (extract, pull-from-repo, import-bundle).

    **The rebuild is a step inside this commit, not a hook after it.** The
    pre-redesign version ran temporal normalization and supersession detection
    here as best-effort hooks that caught their own exceptions and logged a
    warning — so a broken projection was indistinguishable from a healthy one.
    Now a projection failure fails the commit and nothing lands.

    The **embed sweep** deliberately runs after the transaction has committed:
    it calls the embedding service, which a transaction cannot do. It is
    scoped to the keys this commit dirtied, and it never nulls an embedding —
    an entity it cannot embed keeps its old vector and stays flagged for the
    next sweep.

    `defer_rebuild` is the directory path: per-document observation writes,
    then one full rebuild at the end (see `rebuild_projection`).
    """
    try:
        summary = _write_to_neo4j(doc_kg_dir, domain, defer_rebuild=defer_rebuild)
    except Exception as e:
        logger.error("commit_to_graph: transaction failed for {}: {}", doc_kg_dir, e)
        return False
    if summary is None:
        return False

    if not defer_rebuild:
        _sweep_embeddings(domain, summary.get("affected_keys") or [])
    return True


def _sweep_embeddings(domain: str, keys: list) -> int:
    """Post-commit embed sweep, scoped to the affected keys.

    Never fatal: the commit already succeeded and the graph is correct. An
    entity the sweep could not embed keeps `embedding_stale = true` and is
    picked up next time — which is the whole point of flagging rather than
    nulling.
    """
    if not keys:
        return 0
    try:
        from artmind.graph_query import neo4j_session

        env = load_env()
        embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
        with neo4j_session() as session:
            return embed_missing_entity_embeddings(session, domain, embed_model, keys=keys)
    except Exception as e:
        logger.warning(
            "Embed sweep skipped for {} ({}); entities stay marked stale and will be "
            "picked up by the next sweep", domain, e,
        )
        return 0


def rebuild_projection(domain: str | None = None, keys: list | None = None) -> dict:
    """Rebuild the projection outside an ingest — the deferred directory path,
    and the recovery path for drift.

    A full rebuild when `keys` is omitted. The embed sweep follows, since a
    rebuild leaves everything it touched flagged stale.
    """
    from artmind import projection
    from artmind.graph_query import neo4j_session

    domains = [domain] if domain else None
    with neo4j_session() as session:
        if keys:
            summary = session.execute_write(
                lambda tx: projection.rebuild(
                    tx, keys, synthesis_loader=lambda k: projection.load_synthesis(tx, k)
                )
            )
            swept_keys = list(keys)
        else:
            summary = session.execute_write(
                lambda tx: projection.full_rebuild(
                    tx, domains, synthesis_loader=lambda k: projection.load_synthesis(tx, k)
                )
            )
            swept_keys = sorted(session.execute_read(lambda tx: projection.all_keys(tx, domains)))
    if domain:
        summary["embedded"] = _sweep_embeddings(domain, swept_keys)
    return summary
