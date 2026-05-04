import json
import re
import shutil
import sqlite3
import time
import uuid
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import json_repair
import ollama
import yaml
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
from loguru import logger
from neo4j import GraphDatabase

from artmind.db import _get_db
from artmind.jobs import _update_job_file_status, _update_job_status
from paths import (
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
            "SELECT filename, sha256, original_path FROM documents"
            " WHERE UPPER(filename) = ? AND domain = ?",
            (document_name.upper(), domain),
        ).fetchone()
        if not row:
            # Try prefix match (user may omit extension)
            row = conn.execute(
                "SELECT filename, sha256, original_path FROM documents"
                " WHERE UPPER(filename) LIKE ? AND domain = ? LIMIT 1",
                (document_name.upper() + "%", domain),
            ).fetchone()
        if not row:
            return None
        filename, doc_sha256, original_path = row
        registered_path = Path(original_path)
        chunks_dir = MARKDOWNS_DIR / f"{registered_path.stem}_chunks"
        chunk_count = len(sorted(chunks_dir.glob("chunk_*.md"))) if chunks_dir.exists() else 0
        return {
            "status": "ok",
            "filename": filename,
            "sha256": doc_sha256,
            "registered_path": original_path,
            "domain": domain,
            "chunks_dir": str(chunks_dir),
            "chunk_count": chunk_count,
        }
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
            response = ollama.Client(timeout=timeout).chat(
                model=model,
                messages=[{"role": "user", "content": prompt, "images": [str(image)]}],
            )
            description = (response.message.content or "").strip()
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
                str(source.resolve()),
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

        # Split markdown into chunks and persist each chunk to disk
        raw_text = md_file.read_text(encoding="utf-8")
        _, body = _parse_md_frontmatter(raw_text)
        chunks = _split_markdown(body, chunk_size)
        chunks_dir = MARKDOWNS_DIR / f"{dest_path.stem}_chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for i, chunk_text in enumerate(chunks, start=1):
            (chunks_dir / f"chunk_{i:03d}.md").write_text(chunk_text, encoding="utf-8")
        logger.info("Saved {} chunk(s) to {}_chunks/", len(chunks), dest_path.stem)

        file_result["status"] = "ok"
        file_result["domain"] = domain
        file_result["sha256"] = file_sha256
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


def _split_markdown(text: str, chunk_size: int) -> list[str]:
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
    header_docs = header_splitter.split_text(text)
    chunks: list[str] = []
    for doc in header_docs:
        content = doc.page_content.strip()
        if not content:
            continue
        if len(content) <= chunk_size:
            chunks.append(content)
        else:
            chunks.extend(
                c.strip() for c in char_splitter.split_text(content) if c.strip()
            )
    if not chunks:
        chunks = [c.strip() for c in char_splitter.split_text(text) if c.strip()]
    return chunks


def _embed_text(model: str, text: str) -> list[float]:
    response = ollama.embed(model=model, input=text)
    embedding = response.embeddings[0]
    log_llm_call("embed", model, text, f"[embedding vector, dim={len(embedding)}]")
    return embedding


def _call_llm_text(model: str, prompt: str) -> str:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    response = ollama.Client(timeout=timeout).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        # format="json", # Adding this parameter is making the model fail
        # think=False, # Adding this parameter as well is making the model go into an infinite loop
        options={"temperature": 0},
    )
    result = (response.message.content or "").strip()
    log_llm_call("chat", model, prompt, result)
    return result


def _llm_extract(step_name: str, model: str, prompt: str, debug_dir: Path) -> tuple[list, bool]:
    """Call LLM, parse JSON response, retry once on any failure. Returns (result, ok)."""
    raw_llm = ""
    for attempt in range(2):
        try:
            raw_llm = _call_llm_text(model, prompt)
            return _parse_json_response(raw_llm), True
        except Exception as e:
            if attempt == 0:
                logger.warning("  {} failed (attempt 1/2), retrying: {}", step_name, e)
            else:
                logger.error("  {} failed after 2 attempts: {}", step_name, e)
                if raw_llm:
                    safe = re.sub(r"[^A-Za-z0-9_]", "_", step_name)
                    _save_debug(debug_dir / f"debug_{safe}.txt", raw_llm)
    return [], False


def _parse_json_response(text: str):
    text = text.strip()
    # Strip <think>…</think> blocks produced by reasoning models (e.g. Qwen3)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json_repair.loads(text.strip())


def _entities_list_text(entities: list[dict]) -> str:
    return "\n".join(f"{e['id']} ({e['entity_class']}): {e['name']}" for e in entities)


def _save_debug(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    logger.debug("Raw LLM response saved for debugging: {}", path.name)


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


def _ensure_neo4j_schema(session, embedding_dim: int = 768) -> None:
    session.run(
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX entity_lookup IF NOT EXISTS FOR (n:Entity) ON (n.name, n.entity_class, n.domain)"
    )
    session.run(
        f"CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS "
        f"FOR (c:DocChunk) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )


def _upsert_entity(session, entity: dict, extra_props: dict | None) -> None:
    """Merge Entity node by (entity_class, name), intelligently merging all properties."""
    entity_class = entity["entity_class"]
    name = entity["name"]

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

    rec = session.run(
        "MATCH (n:Entity {name: $name, entity_class: $ec, domain: $domain}) RETURN properties(n) AS p",
        name=name,
        ec=entity_class,
        domain=domain,
    ).single()

    if rec:
        merged = _merge_props_dicts(dict(rec["p"]), incoming)
        session.run(
            "MATCH (n:Entity {name: $name, entity_class: $ec, domain: $domain}) SET n = $props",
            name=name,
            ec=entity_class,
            domain=domain,
            props=merged,
        )
    else:
        label_str = f"{_sanitize_label(entity_class)}:Entity"
        session.run(
            f"CREATE (n:{label_str}) SET n = $props",
            props=incoming,
        )


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
                _upsert_entity(session, entity, props_by_id.get(entity["id"]))
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
                    else:
                        # Entity → Entity (matched by canonical name + domain)
                        if source_name and target_name:
                            session.run(
                                """
                                MATCH (src:Entity {name: $src, domain: $domain})
                                MATCH (tgt:Entity {name: $tgt, domain: $domain})
                                CALL apoc.merge.relationship(src, $type, {}, $props, tgt, {}) YIELD rel
                                RETURN rel
                                """,
                                src=source_name,
                                tgt=target_name,
                                type=rel_type,
                                props=rel_props,
                                domain=domain,
                            )
                            rel_count += 1
                            if is_bidi:
                                session.run(
                                    """
                                    MATCH (src:Entity {name: $src, domain: $domain})
                                    MATCH (tgt:Entity {name: $tgt, domain: $domain})
                                    CALL apoc.merge.relationship(tgt, $type, {}, $props, src, {}) YIELD rel
                                    RETURN rel
                                    """,
                                    src=source_name,
                                    tgt=target_name,
                                    type=rel_type,
                                    props=rel_props,
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
        if _delete_path(KG_DIR / domain / stem, KG_DIR):
            result["kg_dirs"] += 1

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
) -> bool:
    """Orchestrate KG extraction and Neo4j write for a single document."""
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
        chunks_dir.mkdir(parents=True, exist_ok=True)
        for i, chunk_text in enumerate(chunks, start=1):
            (chunks_dir / f"chunk_{i:03d}.md").write_text(chunk_text, encoding="utf-8")
        file_result["chunks_dir"] = str(chunks_dir)
        file_result["chunk_count"] = len(chunks)
        file_result.setdefault("sha256", _compute_sha256(registered_path))

    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model)
    if doc_kg_dir is None:
        return False
    return write_to_graph(doc_kg_dir)


def extract_kg(
    file_result: dict,
    domain: str,
    text_model: str = "ministral-3:14b",
    embed_model: str = "nomic-embed-text:latest",
) -> Path | None:
    """Extract KG from persisted chunks and merge into document-level JSON files.

    Resumable: already-ok steps are skipped. Failed steps get a second attempt
    in the pre-merge retry pass before the merge proceeds.
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
    entities_tpl = schema.get("entities_prompt", "")
    properties_tpl = schema.get("properties_prompt", "")
    relationships_tpl = schema.get("relationships_prompt", "")

    doc_kg_dir = KG_DIR / domain / registered_path.stem
    doc_kg_dir.mkdir(parents=True, exist_ok=True)
    chunk_data_dir = doc_kg_dir / "chunks"
    chunk_data_dir.mkdir(parents=True, exist_ok=True)

    # Stable doc_id: reuse from DB if this document was partially extracted before.
    existing = _get_chunk_statuses(doc_sha256)
    doc_id = next(iter(existing.values()))["doc_id"] if existing else uuid.uuid4().hex

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
                entities_tpl.replace("{text}", chunk_text),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "entities", "ok" if ok else "failed")
            if ok:
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

        entities_list = _entities_list_text(data.get("raw_entities", []))
        id_map = data.get("id_map", {})
        has_entities = bool(raw_entities)

        # ── properties ────────────────────────────────────────────────────────
        properties_status = status.get("properties_status", "pending")
        if has_entities and entities_status == "ok" and properties_status != "ok":
            logger.info("  Chunk {} — properties", seq)
            raw_props, ok = _llm_extract(
                f"chunk_{seq:03d}_properties",
                text_model,
                properties_tpl.replace("{entities_list}", entities_list).replace("{text}", chunk_text),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "properties", "ok" if ok else "failed")
            if ok:
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
                relationships_tpl.replace("{entities_list}", entities_list).replace("{text}", chunk_text),
                doc_kg_dir,
            )
            _update_chunk_step(doc_sha256, seq, "relationships", "ok" if ok else "failed")
            if ok:
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
    for seq, chunk_file in enumerate(chunk_files, start=1):
        logger.info("Chunk {}/{} ({} bytes)", seq, chunk_count, chunk_file.stat().st_size)
        _process_chunk(seq, chunk_file, statuses)

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
        "name": registered_path.name,
        "path": str(registered_path),
        "domain": domain,
    }
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

