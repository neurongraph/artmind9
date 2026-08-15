import json
import re
import shutil
import sqlite3
import time
import uuid
import datetime as _datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import sha1, sha256
from pathlib import Path

import yaml
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from loguru import logger
from neo4j import GraphDatabase

from artmind.db import _get_db
from artmind.setup import _setup_neo4j
from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    embed_text as _embed_text,
    call_llm as _call_llm_text,
    extract_with_retry as _llm_extract_shared,
    parse_json_response as _parse_json_response,
    entities_list_text as _entities_list_text,
)
from artmind.llm_providers import describe_image_ollama, describe_image_openrouter
from artmind.jobs import _update_job_file_status, _update_job_status
from paths import (
    ARTMIND_VAULT_DIR,
    DB_PATH,
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


def collect_ingest_files(path: Path) -> list[Path]:
    """Resolve a file-or-directory ingest target to the sorted list of files to ingest.

    A single file ingests as itself. A directory is walked recursively, skipping
    any file under a dotfile/dot-directory (``.DS_Store``, ``.git/``, ``.venv/``,
    etc.) — those are OS/tooling artifacts, never ingestion targets. Shared by
    every ingestion entry point (CLI sync/async, the admin dashboard's ingest
    endpoint) so they can't drift out of agreement on what "ingest a directory"
    means.
    """
    if path.is_dir():
        return sorted(
            f for f in path.rglob("*")
            if f.is_file()
            and not any(p.startswith(".") for p in f.relative_to(path).parts)
        )
    return [path]


def _compute_sha256(file_path: Path) -> str:
    h = sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


# ── document registry helpers ──────────────────────────────────────────────────


def _sha256_in_registry(file_sha256: str) -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM documents WHERE sha256 = ?", (file_sha256,))
    found = cursor.fetchone() is not None
    conn.close()
    return found


def _filename_in_registry(filename: str) -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM documents WHERE UPPER(filename) = ?", (filename.upper(),)
    )
    found = cursor.fetchone() is not None
    conn.close()
    return found


def _find_registered_documents(domain: str, document_name: str) -> list[dict]:
    """Return registry rows matching a domain and document filename."""
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT id, domain, filename, sha256, original_path, added_at
            FROM documents
            WHERE domain = ? AND UPPER(filename) = ?
            """,
            (domain, document_name.upper()),
        )
        rows = cursor.fetchall()
        return [
            {
                "id": row[0],
                "domain": row[1],
                "filename": row[2],
                "sha256": row[3],
                "original_path": row[4],
                "added_at": row[5],
            }
            for row in rows
        ]
    finally:
        conn.close()


def _delete_from_registry(domain: str, document_name: str) -> int:
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM documents WHERE domain = ? AND UPPER(filename) = ?",
            (domain, document_name.upper()),
        )
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _delete_chunk_status(doc_sha256: str) -> int:
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        if not _table_exists(conn, "kg_chunk_status"):
            return 0
        cursor = conn.cursor()
        cursor.execute("DELETE FROM kg_chunk_status WHERE doc_sha256 = ?", (doc_sha256,))
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def _delete_chunk_status_by_doc_id(doc_id: str) -> int:
    """Delete kg_chunk_status rows by doc_id — needed for force-ingested duplicates,
    whose extraction key differs from the registry's real sha256."""
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(DB_PATH)
    try:
        if not _table_exists(conn, "kg_chunk_status"):
            return 0
        cursor = conn.cursor()
        cursor.execute("DELETE FROM kg_chunk_status WHERE doc_id = ?", (doc_id,))
        deleted = cursor.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def _path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _delete_path(path: Path, expected_parent: Path) -> bool:
    """Delete a file or directory if it lives under the expected parent."""
    if not path.exists() or not _path_is_under(path, expected_parent):
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


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
    """Reconstruct file_result from the registry for CLI retry commands."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT filename, sha256, original_path, logical_id FROM documents"
            " WHERE UPPER(filename) = ? AND domain = ?",
            (document_name.upper(), domain),
        ).fetchone()
        if not row:
            # Try prefix match (user may omit extension)
            row = conn.execute(
                "SELECT filename, sha256, original_path, logical_id FROM documents"
                " WHERE UPPER(filename) LIKE ? AND domain = ? LIMIT 1",
                (document_name.upper() + "%", domain),
            ).fetchone()
        if not row:
            return None
        filename, doc_sha256, original_path, logical_id = row
        registered_path = Path(original_path)
        chunks_dir = MARKDOWNS_DIR / f"{registered_path.stem}_chunks"
        chunk_count = len(sorted(chunks_dir.glob("chunk_*.md"))) if chunks_dir.exists() else 0
        result = {
            "status": "ok",
            "filename": filename,
            "sha256": doc_sha256,
            "registered_path": original_path,
            "domain": domain,
            "chunks_dir": str(chunks_dir),
            "chunk_count": chunk_count,
        }
        if logical_id:
            result["logical_id"] = logical_id
        return result
    finally:
        conn.close()


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


def _register_document(domain: str, file_path: Path, logical_id: str) -> str:
    """Insert document into registry; return resolved path. Raises ValueError on duplicate."""
    filename = file_path.name
    file_sha256 = _compute_sha256(file_path)
    resolved_path = str(file_path.resolve())
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO documents (domain, filename, sha256, original_path, added_at, logical_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (domain, filename, file_sha256, resolved_path, datetime.now().isoformat(), logical_id),
        )
        conn.commit()
        return resolved_path
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            cursor.execute("SELECT 1 FROM documents WHERE sha256 = ?", (file_sha256,))
            if cursor.fetchone():
                raise ValueError(f"Duplicate: SHA256 {file_sha256} already registered")
            cursor.execute("SELECT 1 FROM documents WHERE filename = ?", (filename,))
            if cursor.fetchone():
                raise ValueError(f"Duplicate: filename '{filename}' already registered")
            raise ValueError(f"Duplicate: logical id '{logical_id}' already registered")
        raise
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
            else:
                description = describe_image_ollama(image, model, prompt, timeout)
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


def ingest_file(
    source: Path,
    image_model: str,
    domain: str = "general",
    job_id: str | None = None,
    chunk_size: int = 6000,
    force: bool = False,
):
    file_size_kb = source.stat().st_size / 1024
    logger.info(
        "── Ingest start: {} ({:.1f} KB, domain={})", source.name, file_size_kb, domain
    )
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    file_sha256 = _compute_sha256(source)
    logger.debug("SHA256: {}", file_sha256)
    is_duplicate = _sha256_in_registry(file_sha256)
    if is_duplicate and not force:
        logger.warning(
            "Skipping duplicate — SHA256 already registered: {}", source.name
        )
        file_result["error"] = "Duplicate SHA256 already registered"
        if job_id:
            _update_job_file_status(
                job_id,
                str(source.resolve()),
                status="skipped",
                error_message="Duplicate SHA256 already registered",
            )
        return file_result

    # A forced duplicate gets its own extraction identity (chunk cache, doc_id,
    # Neo4j document node) so it doesn't collide/merge with the original document
    # that shares this content hash. The registry still records the real sha256.
    extraction_sha256 = file_sha256
    if is_duplicate and force:
        extraction_sha256 = f"{file_sha256}-{uuid.uuid4().hex[:8]}"
        logger.info(
            "Forcing duplicate ingestion of {} — using independent extraction key",
            source.name,
        )

    # Stable path-based logical identity (A1c). Computed from the *source* path
    # (not the data-dir copy) so re-ingesting an edited file at the same Vault
    # path resolves to the same document. A forced duplicate gets an independent
    # logical id too, mirroring the independent extraction key above, so it never
    # collides with / bumps the version of the original document.
    logical_id = _logical_id(domain, _canonical_key(source, domain))
    if is_duplicate and force:
        logical_id = f"{logical_id}-{uuid.uuid4().hex[:8]}"

    dest_filename = source.name
    if _filename_in_registry(dest_filename):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_filename = f"{source.stem}_{timestamp}{source.suffix}"
        logger.info("Name collision — renamed to: {}", dest_filename)

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
                file_result["status"] = "skipped"
                file_result["error"] = "Oversized image: docling size limit exceeded"
                return file_result
            logger.error(
                "Docling failed for {} in {:.1f}s: {}",
                dest_filename,
                elapsed,
                stderr or stdout,
            )
            file_result["error"] = "Docling conversion failed"
            return file_result

        if not md_file.exists():
            logger.error("Expected markdown not created: {}", md_file)
            file_result["error"] = "Markdown file not created"
            return file_result

        md_size_kb = md_file.stat().st_size / 1024
        logger.info(
            "Docling conversion done in {:.1f}s — markdown: {:.1f} KB",
            elapsed,
            md_size_kb,
        )

        artifacts_dir = MARKDOWNS_DIR / f"{dest_path.stem}_artifacts"
        if artifacts_dir.exists():
            images = sorted(
                f
                for f in artifacts_dir.iterdir()
                if f.suffix.lower() in IMAGE_EXTENSIONS
            )
            if images:
                logger.info("Found {} image(s) to describe in artifacts", len(images))
                md_content = md_file.read_text(encoding="utf-8")
                for idx, image in enumerate(images, start=1):
                    logger.info("Image [{}/{}]: {}", idx, len(images), image.name)
                    description = _describe_image(image, image_model)
                    if description:
                        image.with_name(image.name + "_desc.md").write_text(
                            description, encoding="utf-8"
                        )
                        md_content = _replace_image_ref(
                            md_content, image.name, description
                        )
                    else:
                        logger.error(
                            "No description produced for image: {}", image.name
                        )
                md_file.write_text(md_content, encoding="utf-8")
                logger.debug(
                    "Markdown updated with {} image description(s)", len(images)
                )
            else:
                logger.debug("Artifacts dir exists but contains no images")
        else:
            logger.debug("No artifacts directory for {}", dest_filename)

    try:
        registered_path = _register_document(domain, dest_path, logical_id)
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
        file_result["sha256"] = extraction_sha256
        file_result["logical_id"] = logical_id
        file_result["registered_path"] = str(registered_path)
        file_result["chunks_dir"] = str(chunks_dir)
        file_result["chunk_count"] = len(chunks)
        return file_result
    except ValueError as e:
        logger.error("Registration failed for {}: {}", dest_filename, e)
        file_result["error"] = str(e)
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
# Entity<->Entity rel_type (it's structural, written only by this module's own
# Entity->DocChunk provenance code), so blocking it as an Entity->Entity type is
# defense-in-depth with no known cost.
#
# PRIOR_STATE is reserved on the same grounds: it links a live Entity to an
# :EntityVersion snapshot and is written only by artmind.entity_history. An
# LLM-minted one would imply history that no snapshot node backs.
#
# PART_OF is deliberately NOT reserved: multiple shipped schemas (general_schema,
# banking.organization_schema, sales_collateral_schema, project_governance_schema)
# list part_of as a legitimate LLM-extractable Entity->Entity relationship (e.g.
# "Branch X part_of Region Y"). The only structural PART_OF edge is the hardcoded
# DocChunk->Document edge written elsewhere in this module's own upsert code — a
# different code path from this Entity->Entity loop — so reserving PART_OF here
# would silently drop legitimate, schema-sanctioned extractions.
RESERVED_REL_TYPES = frozenset({"SUPERSEDES", "EXTRACTED_FROM", "PRIOR_STATE"})


def _neo4j_value(value):
    """Convert a value to a Neo4j-compatible type (no nested maps)."""
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return [
            json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v
            for v in value
        ]
    return value


def _flatten_props(props: dict) -> dict:
    """Flatten a props dict to Neo4j-compatible types, dropping empty values."""
    result = {}
    for k, v in props.items():
        if v is None or v == "" or v == []:
            continue
        result[k] = _neo4j_value(v)
    return result


def _merge_prop_value(existing, incoming):
    """Merge a single property value: union lists, append strings, keep existing scalars."""
    if existing is None:
        return incoming
    if isinstance(existing, list) and isinstance(incoming, list):
        result = list(existing)
        for item in incoming:
            if item not in result:
                result.append(item)
        return result
    if isinstance(existing, list):
        return existing if incoming in existing else existing + [incoming]
    if isinstance(incoming, list):
        return incoming if existing in incoming else [existing] + incoming
    if isinstance(existing, str) and isinstance(incoming, str):
        return (
            existing
            if (not incoming or incoming in existing)
            else f"{existing} | {incoming}"
        )
    return existing  # numbers, bools — keep existing


def _merge_props_dicts(existing: dict, incoming: dict) -> dict:
    result = dict(existing)
    for key, val in incoming.items():
        result[key] = _merge_prop_value(result.get(key), val)
    return result


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


def _parse_prop_sources(raw) -> dict:
    """Decode a node's ``_prop_sources`` JSON string into a ledger dict.

    Tolerates the property being absent (pre-A1e node) or malformed — either
    yields an empty ledger, so the entity is simply re-seeded from its current
    clean values on the next touch rather than erroring the whole write."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _ledger_upsert(ledger: dict, key: str, doc_id: str, value) -> None:
    """Record ``doc_id``'s contribution of ``value`` to property ``key``.

    Idempotent per document: re-ingesting the same doc replaces its prior
    contribution in place (so a re-feed does not double-append), otherwise the
    contribution is appended — preserving first-seen document order, which is
    the order ``_fold_ledger`` merges in."""
    contributions = ledger.setdefault(key, [])
    for entry in contributions:
        if entry[0] == doc_id:
            entry[1] = value
            return
    contributions.append([doc_id, value])


def _fold_ledger(contributions: list) -> object:
    """Recompute a property's clean materialized value from its ledger.

    Folds each document's contribution through ``_merge_prop_value`` in
    first-seen order — reproducing exactly the accretive result a plain ingest
    would have produced, so the stored clean value is unchanged on fresh ingest
    yet becomes an un-mergeable-by-hand quantity that purge can rebuild after
    dropping a source. Returns ``None`` for an empty ledger (property removed)."""
    result = None
    for _doc_id, value in contributions:
        result = _merge_prop_value(result, value)
    return result


def _ensure_neo4j_schema(session, embedding_dim: int = 768) -> None:
    _setup_neo4j(session, embedding_dim)


def _upsert_entity(
    session, entity: dict, extra_props: dict | None, doc_id: str | None = None
) -> None:
    """Merge Entity node by (entity_class, name, domain), maintaining a per-document
    property-provenance ledger (A1e).

    The clean merged value of each accretive property stays materialized on the
    node exactly as a plain ingest would leave it (``"A | B"``), so every value-
    parsing consumer — consolidate, entity_history, temporal, snapshots — keeps
    working. Alongside it, ``_prop_sources`` records which document contributed
    which raw value, so a later purge can drop one document's share and recompute
    the clean value (see ``_fold_ledger``). ``doc_id`` is the contributing
    document's physical id, threaded from ``_write_to_neo4j``; a falsy value maps
    to a distinct ``__unknown__`` source rather than the never-purged legacy one."""
    entity_class = entity["entity_class"]
    name = entity["name"]
    source = doc_id or "__unknown__"

    incoming = _flatten_props(
        {
            "name": name,
            "entity_class": entity_class,
            "domain": entity.get("domain"),
            "type": entity.get("type"),
            "description": entity.get("description"),
            "aliases": entity.get("aliases"),
            "context": entity.get("context"),
        }
    )
    if extra_props:
        incoming.update(_flatten_props(extra_props))

    domain = incoming.get("domain", "")
    tracked = {k: v for k, v in incoming.items() if k not in _UNTRACKED_PROP_KEYS}

    rec = session.run(
        "MATCH (n:Entity {name: $name, entity_class: $ec, domain: $domain}) RETURN properties(n) AS p",
        name=name,
        ec=entity_class,
        domain=domain,
    ).single()

    if rec:
        existing = dict(rec["p"])
        ledger = _parse_prop_sources(existing.get("_prop_sources"))
        for key, value in tracked.items():
            # Migrate a pre-ledger clean value as an un-attributable legacy base
            # the first time this property is ledgered, so today's accretive
            # result is preserved rather than silently replaced.
            if key not in ledger and existing.get(key) is not None:
                ledger[key] = [[_LEGACY_SOURCE, existing[key]]]
            _ledger_upsert(ledger, key, source, value)
        merged = existing
        for key, contributions in ledger.items():
            folded = _fold_ledger(contributions)
            if folded in (None, "", []):
                merged.pop(key, None)
            else:
                merged[key] = folded
        merged["_prop_sources"] = json.dumps(ledger, ensure_ascii=False)
        if "id" not in merged:
            merged["id"] = uuid.uuid4().hex
        session.run(
            "MATCH (n:Entity {name: $name, entity_class: $ec, domain: $domain}) SET n = $props",
            name=name,
            ec=entity_class,
            domain=domain,
            props=merged,
        )
    else:
        ledger: dict = {}
        for key, value in tracked.items():
            _ledger_upsert(ledger, key, source, value)
        incoming["_prop_sources"] = json.dumps(ledger, ensure_ascii=False)
        incoming["id"] = uuid.uuid4().hex
        label_str = f"{_sanitize_label(entity_class)}:Entity"
        session.run(
            f"CREATE (n:{label_str}) SET n = $props",
            props=incoming,
        )


def entity_embedding_text(name: str, description: str | None) -> str:
    """Text embedded for an entity — name plus description when available."""
    return f"{name}: {description}" if description else name


def embed_missing_entity_embeddings(session, domain: str, embed_model: str) -> int:
    """Embed name+description for all entities in the domain that lack an embedding.

    Idempotent backfill — also picks up entities written before embeddings existed.
    Returns the number of entities embedded. Failures are logged and skipped so a
    down embedding service never blocks a graph write.
    """
    rows = session.run(
        """
        MATCH (e:Entity)
        WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
          AND e.embedding IS NULL AND e.name IS NOT NULL
        RETURN e.id AS id, e.name AS name, e.description AS description
        """,
        domain=domain,
    ).data()
    count = 0
    for row in rows:
        try:
            embedding = _embed_text(
                embed_model, entity_embedding_text(row["name"], row.get("description"))
            )
        except Exception as e:
            logger.warning("Entity embedding failed for {!r}: {}", row["name"], e)
            continue
        session.run(
            "MATCH (e:Entity {id: $id}) SET e.embedding = $embedding",
            id=row["id"],
            embedding=embedding,
        )
        count += 1
    if count:
        logger.debug("Neo4j: embedded {} entity node(s)", count)
    return count


def embed_entities_backfill(domain: str) -> dict:
    """Backfill embeddings for all entities in a domain that lack one."""
    from artmind.graph_query import neo4j_session

    env = load_env()
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    with neo4j_session() as session:
        embedded = embed_missing_entity_embeddings(session, domain, embed_model)
    return {"domain": domain, "entities_embedded": embedded}


def _write_to_neo4j(doc_kg_dir: Path) -> bool:
    """Read the extracted JSON files and write/merge everything into Neo4j."""
    env = load_env()
    uri = env.get("ARTMIND_KG_NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = env.get("ARTMIND_KG_NEO4J_USERNAME", "neo4j")
    password = env.get("ARTMIND_KG_NEO4J_PASSWORD", "")
    database = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))

    def _load(name: str):
        return json.loads((doc_kg_dir / name).read_text(encoding="utf-8"))

    try:
        document = _load("document.json")
        chunks = _load("chunks.json")
        entities = _load("entities.json")
        properties_list = _load("properties.json")
        relationships = _load("relationships.json")
    except Exception as e:
        logger.error("Failed to load KG JSON files from {}: {}", doc_kg_dir, e)
        return False

    # Index: entity-scoped-id → extra properties dict (from properties.json)
    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            _ensure_neo4j_schema(session, embedding_dim)

            # ── Document ──────────────────────────────────────────────
            session.run(
                "MERGE (d:Document {id: $id}) SET d += $props",
                id=document["id"],
                props=_flatten_props(document),
            )

            # ── DocChunks + PART_OF Document ──────────────────────────
            for chunk in chunks:
                embedding = chunk.get("embedding", [])
                props = _flatten_props(
                    {k: v for k, v in chunk.items() if k != "embedding"}
                )
                session.run(
                    """
                    MERGE (c:DocChunk {id: $id})
                    SET c += $props, c.embedding = $embedding
                    WITH c
                    MATCH (d:Document {id: $doc_id})
                    MERGE (c)-[:PART_OF]->(d)
                    """,
                    id=chunk["id"],
                    props=props,
                    embedding=embedding,
                    doc_id=chunk["doc_id"],
                )
            logger.debug("Neo4j: upserted {} DocChunk(s)", len(chunks))

            # ── Entity nodes ──────────────────────────────────────────
            for entity in entities:
                _upsert_entity(
                    session,
                    entity,
                    props_by_id.get(entity["id"]),
                    doc_id=document["id"],
                )
            logger.debug("Neo4j: upserted {} entity node(s)", len(entities))

            # ── Relationships ─────────────────────────────────────────
            rel_count = 0
            for rel in relationships:
                rel_type = re.sub(r"[^A-Za-z0-9_]", "_", rel["rel_type"]).upper()
                source_name = rel.get("source_name", "")
                target_name = rel.get("target_name", "")
                target_id = rel.get("target_id", "")
                is_bidi = rel.get("bidirectional", False)
                rel_props = _flatten_props(
                    {
                        k: v
                        for k, v in rel.items()
                        if k
                        not in {
                            "source_id",
                            "source_name",
                            "target_id",
                            "target_name",
                            "rel_type",
                            "chunk_id",
                            "doc_id",
                            "bidirectional",
                        }
                    }
                )

                domain = rel.get("domain") or document.get("domain", "")
                # Provenance (A1b): accumulate which doc/chunk contributed this
                # edge. Passed as (possibly empty) lists so the Cypher union never
                # inserts a blank entry. chunk_id is "{doc_id}_{seq:03d}", the
                # prefix A1d's retraction filters on.
                rel_doc_id = rel.get("doc_id") or document.get("id", "")
                rel_doc_ids = [rel_doc_id] if rel_doc_id else []
                rel_chunk_ids = [rel["chunk_id"]] if rel.get("chunk_id") else []
                try:
                    if rel_type == "EXTRACTED_FROM":
                        # Entity → DocChunk (matched by chunk id + domain)
                        if source_name and target_id:
                            session.run(
                                """
                                MATCH (e:Entity {name: $src, domain: $domain})
                                MATCH (c:DocChunk {id: $tgt_id})
                                CALL apoc.merge.relationship(e, $type, {}, $props, c, {}) YIELD rel
                                RETURN rel
                                """,
                                src=source_name,
                                tgt_id=target_id,
                                type=rel_type,
                                props=rel_props,
                                domain=domain,
                            )
                            rel_count += 1
                    elif rel_type in RESERVED_REL_TYPES:
                        # System-managed edge type — only the audited temporal
                        # helpers may create these. An LLM-extracted relationship
                        # normalizing to one of these must never be written here.
                        logger.warning(
                            "Neo4j: reserved relationship type skipped ({} -[{}]-> {}); "
                            "only audited helpers may create this edge type",
                            source_name,
                            rel_type,
                            target_name,
                        )
                    else:
                        # Entity → Entity (matched by canonical name + domain)
                        if source_name and target_name:
                            session.run(
                                """
                                MATCH (src:Entity {name: $src, domain: $domain})
                                MATCH (tgt:Entity {name: $tgt, domain: $domain})
                                CALL apoc.merge.relationship(src, $type, {}, {}, tgt, {}) YIELD rel
                                SET rel += $props
                                SET rel.doc_ids   = apoc.coll.toSet(coalesce(rel.doc_ids, [])   + $doc_ids)
                                SET rel.chunk_ids = apoc.coll.toSet(coalesce(rel.chunk_ids, []) + $chunk_ids)
                                RETURN rel
                                """,
                                src=source_name,
                                tgt=target_name,
                                type=rel_type,
                                props=rel_props,
                                doc_ids=rel_doc_ids,
                                chunk_ids=rel_chunk_ids,
                                domain=domain,
                            )
                            rel_count += 1
                            if is_bidi:
                                session.run(
                                    """
                                    MATCH (src:Entity {name: $src, domain: $domain})
                                    MATCH (tgt:Entity {name: $tgt, domain: $domain})
                                    CALL apoc.merge.relationship(tgt, $type, {}, {}, src, {}) YIELD rel
                                    SET rel += $props
                                    SET rel.doc_ids   = apoc.coll.toSet(coalesce(rel.doc_ids, [])   + $doc_ids)
                                    SET rel.chunk_ids = apoc.coll.toSet(coalesce(rel.chunk_ids, []) + $chunk_ids)
                                    RETURN rel
                                    """,
                                    src=source_name,
                                    tgt=target_name,
                                    type=rel_type,
                                    props=rel_props,
                                    doc_ids=rel_doc_ids,
                                    chunk_ids=rel_chunk_ids,
                                    domain=domain,
                                )
                                rel_count += 1
                except Exception as e:
                    logger.warning(
                        "Neo4j: relationship skipped ({} -[{}]-> {}): {}",
                        source_name,
                        rel_type,
                        target_name or target_id,
                        e,
                    )

            logger.debug("Neo4j: created/merged {} relationship(s)", rel_count)

            # ── Entity embeddings (name + description, used by entity-resolve) ─
            embed_model = env.get(
                "ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest"
            )
            embed_missing_entity_embeddings(
                session, document.get("domain", ""), embed_model
            )

        logger.info(
            "Neo4j ingestion complete: {}", document.get("name", doc_kg_dir.name)
        )
        return True
    except Exception as e:
        logger.error("Neo4j ingestion failed: {}", e)
        return False
    finally:
        driver.close()


def _delete_from_neo4j(domain: str, document_name: str) -> dict:
    """Delete matching Documents, their chunks, and orphan Entity nodes."""
    env = load_env()
    uri = env.get("ARTMIND_KG_NEO4J_URI", "neo4j://127.0.0.1:7687")
    user = env.get("ARTMIND_KG_NEO4J_USERNAME", "neo4j")
    password = env.get("ARTMIND_KG_NEO4J_PASSWORD", "")
    database = env.get("ARTMIND_KG_NEO4J_DATABASE", "neo4j")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            doc_result = session.run(
                """
                MATCH (d:Document {domain: $domain})
                WHERE toUpper(d.name) = toUpper($name)
                   OR toUpper(last(split(d.path, '/'))) = toUpper($name)
                WITH collect(d) AS docs
                UNWIND docs AS d
                OPTIONAL MATCH (c:DocChunk {doc_id: d.id})
                WITH collect(DISTINCT d) AS docs, collect(DISTINCT c) AS chunks
                FOREACH (c IN chunks | DETACH DELETE c)
                FOREACH (d IN docs | DETACH DELETE d)
                RETURN size(docs) AS deleted_documents, size(chunks) AS deleted_chunks
                """,
                domain=domain,
                name=document_name,
            ).single()
            orphan_result = session.run(
                """
                MATCH (e:Entity {domain: $domain})
                WHERE NOT (e)-[:EXTRACTED_FROM]->(:DocChunk)
                WITH collect(e) AS entities
                FOREACH (e IN entities | DETACH DELETE e)
                RETURN size(entities) AS deleted_orphan_entities
                """,
                domain=domain,
            ).single()
            return {
                "documents": int(doc_result["deleted_documents"]) if doc_result else 0,
                "chunks": int(doc_result["deleted_chunks"]) if doc_result else 0,
                "orphan_entities": (
                    int(orphan_result["deleted_orphan_entities"])
                    if orphan_result
                    else 0
                ),
            }
    finally:
        driver.close()


def clean_document(domain: str, document_name: str, delete_neo4j: bool = True) -> dict:
    """Remove an ingested document from local storage, registry, and Neo4j."""
    document_name = Path(document_name).name
    rows = _find_registered_documents(domain, document_name)
    if not rows:
        rows = [
            {
                "filename": document_name,
                "original_path": str(ORIGINALS_DIR / document_name),
            }
        ]

    result = {
        "domain": domain,
        "document_name": document_name,
        "registry_rows": 0,
        "originals": 0,
        "markdowns": 0,
        "markdown_artifacts": 0,
        "kg_dirs": 0,
        "chunk_status_rows": 0,
        "neo4j_documents": 0,
        "neo4j_chunks": 0,
        "neo4j_orphan_entities": 0,
        "neo4j_error": None,
    }

    for row in rows:
        original_path = Path(row["original_path"])
        if _delete_path(original_path, ORIGINALS_DIR):
            result["originals"] += 1

        stem = Path(row["filename"]).stem
        if _delete_path(MARKDOWNS_DIR / f"{stem}.md", MARKDOWNS_DIR):
            result["markdowns"] += 1
        if _delete_path(MARKDOWNS_DIR / f"{stem}_artifacts", MARKDOWNS_DIR):
            result["markdown_artifacts"] += 1

        # Read doc_id before deleting the KG dir — needed to find chunk-status rows
        # for force-ingested duplicates, whose extraction key differs from sha256.
        doc_kg_dir = KG_DIR / domain / stem
        doc_json = doc_kg_dir / "document.json"
        if doc_json.exists():
            try:
                doc_id = json.loads(doc_json.read_text(encoding="utf-8")).get("id")
            except Exception:
                doc_id = None
            if doc_id:
                result["chunk_status_rows"] += _delete_chunk_status_by_doc_id(doc_id)

        if _delete_path(doc_kg_dir, KG_DIR):
            result["kg_dirs"] += 1

        if row.get("sha256"):
            result["chunk_status_rows"] += _delete_chunk_status(row["sha256"])

    result["registry_rows"] = _delete_from_registry(domain, document_name)

    if delete_neo4j:
        try:
            graph_result = _delete_from_neo4j(domain, document_name)
            result["neo4j_documents"] = graph_result["documents"]
            result["neo4j_chunks"] = graph_result["chunks"]
            result["neo4j_orphan_entities"] = graph_result["orphan_entities"]
        except Exception as e:
            result["neo4j_error"] = str(e)

    return result


def ingest_to_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
    chunk_size: int = 6000,
    stage_only: bool = False,
) -> bool:
    """Orchestrate KG extraction and (unless stage_only) commit for one document."""
    # Back-compat: if ingest_file didn't split chunks yet, do it now.
    if "chunks_dir" not in file_result:
        registered_path = Path(file_result["registered_path"])
        md_file = MARKDOWNS_DIR / f"{registered_path.stem}.md"
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
    return commit_to_graph(doc_kg_dir, domain)


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
    chunk_data_dir = doc_kg_dir / "chunks"
    chunk_data_dir.mkdir(parents=True, exist_ok=True)

    # Stable identity (A1c): resolve the physical doc_id + version by logical id.
    # A document already in the graph keeps its id and bumps version (idempotent
    # re-ingest); otherwise a fresh id at version 1. A resumed partial extraction
    # (chunk-status rows keyed by the extraction sha256) supplies its already-
    # minted doc_id as the fresh-mint fallback so a resume keeps its id. The
    # logical_id normally arrives via file_result; recompute from the basename as
    # a back-compat fallback for pre-A1c callers.
    logical_id = file_result.get("logical_id") or _logical_id(
        domain, _canonical_key(registered_path, domain)
    )
    existing = _get_chunk_statuses(doc_sha256)
    resumed_doc_id = next(iter(existing.values()))["doc_id"] if existing else None
    doc_id, version = _resolve_doc_identity(domain, logical_id, resumed_doc_id)

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

        # Embedding — compute once and persist
        if "embedding" not in data:
            try:
                data["embedding"] = _embed_text(embed_model, chunk_text)
            except Exception as e:
                logger.error("  Embedding failed for chunk {}: {}", seq, e)
                data["embedding"] = []

        data.setdefault("chunk_seq", seq)
        data.setdefault("chunk_id", chunk_id)
        data.setdefault("doc_id", doc_id)
        data.setdefault("text", chunk_text)
        data.setdefault("name", f"Chunk {seq}/{chunk_count}")
        data.setdefault("domain", domain)
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
                build_entities_prompt(chunk_text, schema),
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
                build_properties_prompt(chunk_text, data.get("raw_entities", []), schema),
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

        chunk_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

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
    md_file = MARKDOWNS_DIR / f"{registered_path.stem}.md"
    meta = {}
    if md_file.exists():
        meta, _ = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    document: dict = {
        "id": doc_id,
        "logical_id": logical_id,
        "version": version,
        "name": registered_path.name,
        "path": str(registered_path),
        "domain": domain,
    }
    if registered_path.exists():
        mtime = registered_path.stat().st_mtime
        document["last_modified"] = _datetime.datetime.fromtimestamp(mtime, tz=_datetime.timezone.utc).isoformat()
    if meta.get("author"):
        document["author"] = str(meta["author"])
    if meta.get("date"):
        document["date"] = str(meta["date"])

    def _write_json(filename: str, obj: object) -> None:
        (doc_kg_dir / filename).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

    _write_json("document.json", document)
    _write_json("chunks.json", all_chunks)
    _write_json("entities.json", all_entities)
    _write_json("properties.json", all_properties)
    _write_json("relationships.json", all_relationships)

    elapsed = time.monotonic() - t0
    final_statuses = _get_chunk_statuses(doc_sha256)
    failed_count = sum(
        1 for s in final_statuses.values()
        if "failed" in (s["entities_status"], s["properties_status"], s["relationships_status"])
    )
    logger.info(
        "KG extraction done in {:.1f}s | chunks={} entities={} properties={} relationships={} | chunks_with_failures={}",
        elapsed,
        chunk_count,
        len(all_entities),
        len(all_properties),
        len(all_relationships),
        failed_count,
    )
    return doc_kg_dir


def write_to_graph(doc_kg_dir: Path) -> bool:
    """Write merged KG JSON files to Neo4j. Safe to re-run after fixing Neo4j issues."""
    return _write_to_neo4j(doc_kg_dir)


def _reassert_superseding_properties(doc_kg_dir: Path, domain: str) -> dict:
    # NOTE: unchanged behaviour — history capture is handled by the caller
    # (commit_to_graph) via artmind.entity_history, so this function keeps its
    # single job of re-asserting the superseding document's own values.
    """Overwrite merged entity properties with the superseding document's own values.

    _upsert_entity's merge is accretive — strings become "old | new" and
    numbers/booleans keep the existing value — which is right for peer documents
    but wrong once THIS document is known to supersede a contributor of those
    values. Called from commit_to_graph only after detect_supersession applied a
    SUPERSEDES edge for this document. Scoped to the domain properties this
    document itself asserts (properties.json); name/description/aliases/context
    live in entities.json and keep their accretive behaviour (consolidation's
    job). Matches by (name, entity_class, domain), the same key _upsert_entity
    merges on. Idempotent.
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8")) if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("reassert_superseding_properties: could not load staged JSON: {}", e)
        return {"entities_reasserted": 0}

    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    from artmind.graph_query import neo4j_session

    reasserted = 0
    with neo4j_session() as session:
        for e in entities:
            props = _flatten_props(props_by_id.get(e["id"], {}))
            if not props:
                continue
            rec = session.run(
                "MATCH (n:Entity {name:$name, entity_class:$ec, domain:$domain}) "
                "SET n += $props RETURN count(n) AS matched",
                name=e["name"],
                ec=e["entity_class"],
                domain=e.get("domain") or domain,
                props=props,
            ).single()
            reasserted += rec["matched"] if rec else 0
    if reasserted:
        logger.info(
            "reassert_superseding_properties: {} entity node(s) updated from {}",
            reasserted, doc_kg_dir.name,
        )
    return {"entities_reasserted": reasserted}


def _incoming_property_values(doc_kg_dir: Path, domain: str) -> dict:
    """The property values this document asserts, keyed by entity identity.

    The comparison side of the history snapshot: capture_prior_values supplies
    what the graph held, this supplies what the document says, and only the
    differences become :EntityVersion nodes.

    entities.json ids are chunk-scoped — the same logical entity mentioned in
    multiple chunks appears as multiple entries sharing one (name, entity_class,
    domain) identity but different chunk-scoped ids and different property
    subsets. Merge (not overwrite) property dicts across those entries so no
    chunk's asserted keys are dropped; a literal key collision lets the later
    chunk win, mirroring _reassert_superseding_properties' own behaviour — it
    issues one unconditional `SET n += $props` per entities.json entry, so the
    last-processed chunk's value for a given key is what actually lands on the
    node (unlike _upsert_entity's merge, which keeps the existing scalar).
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8"))
            if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("_incoming_property_values: could not load staged JSON: {}", e)
        return {}
    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    out: dict = {}
    for e in entities:
        props = _flatten_props(props_by_id.get(e["id"], {}))
        if not props:
            continue
        identity = (e["name"], e["entity_class"], e.get("domain") or domain)
        out.setdefault(identity, {}).update(props)
    return out


def commit_to_graph(doc_kg_dir: Path, domain: str) -> bool:
    """Complete commit of staged KG JSON to Neo4j: capture prior values, write,
    then the per-document self-asserted-truth hooks (temporal normalization,
    then supersession).

    This is the single convergence point for all three ingestion sources
    (extract, pull-from-repo, import-bundle). Cross-document judgment steps
    (merge/conflicts/consolidate) are deliberately NOT run here — see
    artmind.refine_pipeline. Hooks are best-effort: a down hook logs a warning
    but does not fail the commit, since the graph write already succeeded.
    """
    from artmind import entity_history

    # 0. Prior-value capture, for the entity history zone. Must precede the
    #    write: _upsert_entity's merge is accretive, so afterwards the live
    #    node holds "old | new" rather than the clean prior value. Gated on a
    #    pure-local check so documents that declare no supersession — the
    #    overwhelming majority — pay no Neo4j read at all.
    prior: dict = {}
    try:
        document_name = json.loads(
            (doc_kg_dir / "document.json").read_text(encoding="utf-8")
        ).get("name")
        if document_name and entity_history.supersession_possible(document_name, domain):
            prior = entity_history.capture_prior_values(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: prior-value capture failed for {}: {}", doc_kg_dir, e)

    ok = write_to_graph(doc_kg_dir)
    if not ok:
        return False

    # 1. Temporal normalization (additive, idempotent, per-document).
    try:
        from artmind.temporal import normalize_ingested_document
        normalize_ingested_document(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: temporal hook failed for {}: {}", doc_kg_dir, e)

    # 2. Supersession from this document's own declaration (must follow temporal
    #    so canonical dates/version exist). Scoped to just this document. When a
    #    SUPERSEDES edge was applied, snapshot the prior values this document
    #    overwrites, then re-assert its own values over the accretive merge —
    #    the superseding version's values win (see _reassert_superseding_properties).
    try:
        from artmind.temporal import detect_supersession
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        sup_report = detect_supersession(domain, only_doc_name=document.get("name"))
        applied = (sup_report or {}).get("applied")
        if applied:
            if prior:
                try:
                    entity_history.snapshot_changed_values(
                        prior,
                        _incoming_property_values(doc_kg_dir, domain),
                        applied[0].get("effective"),
                        document.get("id"),
                    )
                except Exception as e:
                    logger.warning("commit_to_graph: history snapshot failed for {}: {}", doc_kg_dir, e)
            _reassert_superseding_properties(doc_kg_dir, domain)
    except Exception as e:
        logger.warning("commit_to_graph: supersession hook failed for {}: {}", doc_kg_dir, e)

    return True

