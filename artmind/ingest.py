import json
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import ollama
import yaml
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from loguru import logger

from paths import (
    DB_PATH,
    DOMAIN_SCHEMAS_DIR,
    KG_DIR,
    MARKDOWNS_DIR,
    ORIGINALS_DIR,
    PROJECT_ROOT,
)
from utils.functions import load_env, run_command

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
}


def _compute_sha256(file_path: Path) -> str:
    h = sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


# ── document registry helpers ──────────────────────────────────────────────────


def _init_db():
    if DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE documents (
            id           INTEGER PRIMARY KEY,
            domain       TEXT NOT NULL,
            filename     TEXT NOT NULL,
            sha256       TEXT NOT NULL,
            original_path TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            UNIQUE(filename),
            UNIQUE(sha256)
        )
    """)
    cursor.execute("""
        CREATE TABLE ingestion_jobs (
            job_id           TEXT PRIMARY KEY,
            status           TEXT NOT NULL,
            file_count       INTEGER NOT NULL,
            processed_count  INTEGER DEFAULT 0,
            queued_at        TEXT NOT NULL,
            started_at       TEXT,
            completed_at     TEXT,
            error_message    TEXT,
            results_json     TEXT,
            domain           TEXT DEFAULT 'general'
        )
    """)

    cursor.execute("""
        CREATE TABLE ingestion_job_files (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id           TEXT NOT NULL REFERENCES ingestion_jobs(job_id),
            status           TEXT NOT NULL,
            filename         TEXT NOT NULL,
            started_at       TEXT,
            completed_at     TEXT,
            error_message    TEXT
        )
    """)

    conn.commit()
    conn.close()


def _get_db():
    _init_db()
    return sqlite3.connect(DB_PATH)


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


# ── ingestion job helpers ──────────────────────────────────────────────────────


def _create_job(batch_files: list[str], domain: str = "general") -> str:
    """Create a new ingestion job with per-file rows; return job_id."""
    job_id = str(uuid.uuid4())
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO ingestion_jobs (job_id, status, file_count, queued_at, domain)"
            " VALUES (?, ?, ?, ?, ?)",
            (job_id, "queued", len(batch_files), datetime.now().isoformat(), domain),
        )
        cursor.executemany(
            "INSERT INTO ingestion_job_files (job_id, status, filename) VALUES (?, ?, ?)",
            [(job_id, "queued", f) for f in batch_files],
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def _update_job_status(
    job_id: str,
    status: str | None = None,
    processed_count: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
):
    """Update parent job status and metadata."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        updates, params = [], []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if processed_count is not None:
            updates.append("processed_count = ?")
            params.append(processed_count)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            return
        params.append(job_id)
        cursor.execute(
            f"UPDATE ingestion_jobs SET {', '.join(updates)} WHERE job_id = ?", params
        )
        conn.commit()
    finally:
        conn.close()


def _update_job_file_status(
    job_id: str,
    filename: str,
    status: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
):
    """Update per-file status in ingestion_job_files."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        updates, params = [], []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if not updates:
            return
        params.extend([job_id, filename])
        cursor.execute(
            f"UPDATE ingestion_job_files SET {', '.join(updates)} WHERE job_id = ? AND filename = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def _get_job_status(job_id: str) -> dict | None:
    """Retrieve job status; return None if not found."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT job_id, status, file_count, processed_count, queued_at, started_at,"
            " completed_at, error_message, domain FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute(
            "SELECT filename, status FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = [{"filename": r[0], "status": r[1]} for r in cursor.fetchall()]
        return {
            "job_id": row[0],
            "status": row[1],
            "file_count": row[2],
            "processed_count": row[3],
            "queued_at": row[4],
            "started_at": row[5],
            "completed_at": row[6],
            "error_message": row[7],
            "domain": row[8] or "general",
            "files": files,
        }
    finally:
        conn.close()


def _get_job_results(job_id: str) -> dict | None:
    """Retrieve detailed job results; return None if not found."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT status, file_count, error_message FROM ingestion_jobs WHERE job_id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        status, file_count, error_message = row
        cursor.execute(
            "SELECT filename, status, error_message, started_at, completed_at"
            " FROM ingestion_job_files WHERE job_id = ? ORDER BY id",
            (job_id,),
        )
        files = [
            {
                "filename": r[0],
                "status": r[1],
                "error_message": r[2],
                "started_at": r[3],
                "completed_at": r[4],
            }
            for r in cursor.fetchall()
        ]
        results = {
            "job_id": job_id,
            "status": status,
            "file_count": file_count,
            "files": files,
        }
        if error_message:
            results["error_message"] = error_message
        return results
    finally:
        conn.close()


def _list_jobs(status_filter: str | None = None) -> list[dict]:
    """List all jobs; optionally filter by status."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if status_filter:
            cursor.execute(
                "SELECT job_id, status, file_count, processed_count, queued_at FROM ingestion_jobs"
                " WHERE status = ? ORDER BY queued_at DESC LIMIT 50",
                (status_filter,),
            )
        else:
            cursor.execute(
                "SELECT job_id, status, file_count, processed_count, queued_at FROM ingestion_jobs"
                " ORDER BY queued_at DESC LIMIT 50"
            )
        rows = cursor.fetchall()
        return [
            {
                "job_id": row[0],
                "status": row[1],
                "file_count": row[2],
                "processed_count": row[3],
                "queued_at": row[4],
            }
            for row in rows
        ]
    finally:
        conn.close()


def _register_document(domain: str, file_path: Path) -> str:
    """Insert document into registry; return resolved path. Raises ValueError on duplicate."""
    filename = file_path.name
    file_sha256 = _compute_sha256(file_path)
    resolved_path = str(file_path.resolve())
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO documents (domain, filename, sha256, original_path, added_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (domain, filename, file_sha256, resolved_path, datetime.now().isoformat()),
        )
        conn.commit()
        return resolved_path
    except sqlite3.IntegrityError as e:
        if "UNIQUE constraint failed" in str(e):
            cursor.execute("SELECT 1 FROM documents WHERE sha256 = ?", (file_sha256,))
            if cursor.fetchone():
                raise ValueError(f"Duplicate: SHA256 {file_sha256} already registered")
            raise ValueError(f"Duplicate: filename '{filename}' already registered")
        raise
    finally:
        conn.close()


# ── ingest helpers ─────────────────────────────────────────────────────────────

_DESCRIBE_PROMPTS = [
    "If the picture is a logo or an icon, just reply logo or icon. Otherwise, describe what’s in the picture ensuring all the words detected in the picture are included in your description",
    "Describe this image in detail. Include any visible text.",
]


def _describe_image(image: Path, model: str) -> str | None:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
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
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt, "images": [str(image)]}],
            )
            description = (response.message.content or "").strip()
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
    source: Path, image_model: str, domain: str = "general", job_id: str | None = None
):
    file_size_kb = source.stat().st_size / 1024
    logger.info(
        "── Ingest start: {} ({:.1f} KB, domain={})", source.name, file_size_kb, domain
    )
    file_result = {"filename": source.name, "status": "failed"}
    t_file_start = time.monotonic()

    file_sha256 = _compute_sha256(source)
    logger.debug("SHA256: {}", file_sha256)
    if _sha256_in_registry(file_sha256):
        logger.warning(
            "Skipping duplicate — SHA256 already registered: {}", source.name
        )
        file_result["error"] = "Duplicate SHA256 already registered"
        if job_id:
            _update_job_file_status(
                job_id,
                source.name,
                status="skipped",
                error_message="Duplicate SHA256 already registered",
            )
        return file_result

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
            source.name,
            status="processing",
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
        registered_path = _register_document(domain, dest_path)
        elapsed_total = time.monotonic() - t_file_start
        logger.info(
            "── Ingest done in {:.1f}s: {} registered in domain '{}'",
            elapsed_total,
            dest_filename,
            domain,
        )
        file_result["status"] = "ok"
        file_result["domain"] = domain
        file_result["registered_path"] = str(registered_path)
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
    return meta, text[end + 4:].lstrip("\n")


def _split_markdown(text: str, chunk_size: int) -> list[str]:
    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[
            ("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4"),
        ],
        strip_headers=False,
    )
    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", " "],
    )
    header_docs = header_splitter.split_text(text)
    chunks: list[str] = []
    for doc in header_docs:
        content = doc.page_content.strip()
        if not content:
            continue
        if len(content) <= chunk_size:
            chunks.append(content)
        else:
            chunks.extend(c.strip() for c in char_splitter.split_text(content) if c.strip())
    if not chunks:
        chunks = [c.strip() for c in char_splitter.split_text(text) if c.strip()]
    return chunks


def _embed_text(model: str, text: str) -> list[float]:
    response = ollama.embed(model=model, input=text)
    return response.embeddings[0]


def _call_llm_text(model: str, prompt: str) -> str:
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    return (response.message.content or "").strip()


def _parse_json_response(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json.loads(text.strip())


def _entities_list_text(entities: list[dict]) -> str:
    return "\n".join(f"{e['id']} ({e['entity_class']}): {e['name']}" for e in entities)


def _save_debug(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    logger.debug("Raw LLM response saved for debugging: {}", path.name)


def _rewrite_entity_ids(entities: list[dict], chunk_id: str) -> tuple[list[dict], dict[str, str]]:
    """Prefix every entity id with chunk_id; return (rewritten_entities, old→new id map)."""
    id_map: dict[str, str] = {}
    rewritten = []
    for e in entities:
        old_id = e["id"]
        new_id = f"{chunk_id}_{old_id}"
        id_map[old_id] = new_id
        rewritten.append({**e, "id": new_id})
    return rewritten, id_map


def _rewrite_ref_ids(items: list[dict], id_map: dict[str, str], *fields: str) -> list[dict]:
    """Rewrite id fields in a list of dicts using the given id_map."""
    result = []
    for item in items:
        item = dict(item)
        for field in fields:
            if field in item:
                item[field] = id_map.get(item[field], item[field])
        result.append(item)
    return result


def ingest_to_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
    chunk_size: int = 6000,
) -> bool:
    registered_path = Path(file_result["registered_path"])
    md_file = MARKDOWNS_DIR / f"{registered_path.stem}.md"

    if not md_file.exists():
        logger.error("Markdown not found for KG ingestion: {}", md_file)
        return False

    raw_text = md_file.read_text(encoding="utf-8")
    meta, body = _parse_md_frontmatter(raw_text)

    domain_schema_file = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not domain_schema_file.exists():
        logger.error("Domain schema not found: {}", domain_schema_file)
        return False

    with open(domain_schema_file) as f:
        schema = yaml.safe_load(f)

    entities_prompt_tpl = schema.get("entities_prompt", "")
    properties_prompt_tpl = schema.get("properties_prompt", "")
    relationships_prompt_tpl = schema.get("relationships_prompt", "")

    doc_kg_dir = KG_DIR / domain / registered_path.stem
    doc_kg_dir.mkdir(parents=True, exist_ok=True)

    doc_id = uuid.uuid4().hex
    document: dict = {
        "id": doc_id,
        "name": registered_path.name,
        "path": str(registered_path),
        "domain": domain,
    }
    if meta.get("author"):
        document["author"] = str(meta["author"])
    if meta.get("date"):
        document["date"] = str(meta["date"])

    logger.info(
        "KG extraction start: {} | model={} | embed={} | chunk_size={}",
        registered_path.name, text_model, embed_model, chunk_size,
    )
    t0 = time.monotonic()

    chunks = _split_markdown(body, chunk_size)
    logger.info("Split into {} chunk(s)", len(chunks))

    all_chunks: list[dict] = []
    all_entities: list[dict] = []
    all_properties: list[dict] = []
    all_relationships: list[dict] = []

    for seq, chunk_text in enumerate(chunks, start=1):
        chunk_id = f"{doc_id}_{seq:03d}"
        logger.info("Chunk {}/{} ({} chars)", seq, len(chunks), len(chunk_text))

        try:
            embedding = _embed_text(embed_model, chunk_text)
        except Exception as e:
            logger.error("  Embedding failed for chunk {}: {}", seq, e)
            embedding = []

        chunk_node: dict = {
            "id": chunk_id,
            "doc_id": doc_id,
            "text": chunk_text,
            "embedding": embedding,
            "domain": domain,
        }
        if seq > 1:
            chunk_node["prev_chunk_id"] = f"{doc_id}_{seq - 1:03d}"
        if seq < len(chunks):
            chunk_node["next_chunk_id"] = f"{doc_id}_{seq + 1:03d}"
        all_chunks.append(chunk_node)

        # ── entities ──────────────────────────────────────────────────
        logger.info("  Chunk {} — entities", seq)
        raw_llm = ""
        try:
            raw_llm = _call_llm_text(text_model, entities_prompt_tpl.replace("{text}", chunk_text))
            raw_entities = _parse_json_response(raw_llm)
        except Exception as e:
            logger.error("  Entity extraction failed for chunk {}: {}", seq, e)
            if raw_llm:
                _save_debug(doc_kg_dir / f"debug_chunk{seq:03d}_entities.txt", raw_llm)
            raw_entities = []

        # Build entities_list with original (short) IDs for the LLM prompts below
        entities_list = _entities_list_text(raw_entities)

        # ── properties ────────────────────────────────────────────────
        raw_properties: list[dict] = []
        if raw_entities:
            logger.info("  Chunk {} — properties", seq)
            raw_llm = ""
            try:
                raw_llm = _call_llm_text(
                    text_model,
                    properties_prompt_tpl
                    .replace("{entities_list}", entities_list)
                    .replace("{text}", chunk_text),
                )
                raw_properties = _parse_json_response(raw_llm)
            except Exception as e:
                logger.error("  Properties extraction failed for chunk {}: {}", seq, e)
                if raw_llm:
                    _save_debug(doc_kg_dir / f"debug_chunk{seq:03d}_properties.txt", raw_llm)

        # ── relationships ─────────────────────────────────────────────
        raw_relationships: list[dict] = []
        if raw_entities:
            logger.info("  Chunk {} — relationships", seq)
            raw_llm = ""
            try:
                raw_llm = _call_llm_text(
                    text_model,
                    relationships_prompt_tpl
                    .replace("{entities_list}", entities_list)
                    .replace("{text}", chunk_text),
                )
                raw_relationships = _parse_json_response(raw_llm)
            except Exception as e:
                logger.error("  Relationships extraction failed for chunk {}: {}", seq, e)
                if raw_llm:
                    _save_debug(doc_kg_dir / f"debug_chunk{seq:03d}_relationships.txt", raw_llm)

        # ── rewrite IDs now that all LLM calls for this chunk are done ─
        entities, id_map = _rewrite_entity_ids(raw_entities, chunk_id)
        properties = _rewrite_ref_ids(raw_properties, id_map, "id")
        relationships = _rewrite_ref_ids(raw_relationships, id_map, "source_id", "target_id")

        for e in entities:
            e["chunk_id"] = chunk_id
            e["doc_id"] = doc_id
            e["domain"] = domain

        for p in properties:
            p["chunk_id"] = chunk_id
            p["doc_id"] = doc_id

        for r in relationships:
            r["chunk_id"] = chunk_id
            r["doc_id"] = doc_id

        # ── entity → DocChunk relationships ───────────────────────────
        for e in entities:
            relationships.append({
                "source_id": e["id"],
                "source_name": e["name"],
                "target_id": chunk_id,
                "target_name": chunk_id,
                "rel_type": "EXTRACTED_FROM",
                "description": f"Entity extracted from this document chunk",
                "chunk_id": chunk_id,
                "doc_id": doc_id,
            })

        all_entities.extend(entities)
        all_properties.extend(properties)
        all_relationships.extend(relationships)

    # ── write output files ─────────────────────────────────────────────────────
    def _write(filename: str, data: object) -> None:
        p = doc_kg_dir / filename
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    _write("document.json", document)
    _write("chunks.json", all_chunks)
    _write("entities.json", all_entities)
    _write("properties.json", all_properties)
    _write("relationships.json", all_relationships)

    logger.info(
        "KG extraction done in {:.1f}s | chunks={} entities={} properties={} relationships={}",
        time.monotonic() - t0,
        len(all_chunks),
        len(all_entities),
        len(all_properties),
        len(all_relationships),
    )
    return True
