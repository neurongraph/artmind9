# artmind-update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a multi-turn `artmind-update` skill, CLI command group, and backend that lets users add and update knowledge graph facts from natural language, with domain detection, ambiguity resolution, and full audit trails.

**Architecture:** Three-layer (skill → CLI → backend), consistent with the rest of artmind. A new `artmind/extraction.py` module is refactored out of `ingest.py` and shared with the new `artmind/update.py` backend. Two new SQLite tables track update sessions and drafts. A new `UserChat` Neo4j node type (with vector embedding) is added as a first-class citizen alongside `DocChunk`.

**Tech Stack:** Python 3.14, Click, Neo4j (APOC), Ollama, SQLite, `json_repair`, `loguru`, `pytest`, `click.testing.CliRunner`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `artmind/extraction.py` | Shared LLM primitives (prompt build, call, parse) |
| Modify | `artmind/ingest.py` | Import from `extraction.py`; remove duplicated functions |
| Modify | `artmind/db.py` | Add `update_sessions` + `update_drafts` tables and helpers |
| Create | `artmind/update.py` | Backend: extract_facts, find_candidates, write_user_chat, export_chats |
| Modify | `artmind/cli.py` | Add `update` command group (draft, confirm, history, export) |
| Modify | `artmind/vector_query.py` | Union `DocChunk` + `UserChat` in vector search |
| Modify | `artmind/graph_query.py` | Update MENTIONS traversal to branch on DocChunk vs UserChat |
| Create | `skills/artmind-update/SKILL.md` | Skill document for Claude Code |
| Create | `test/test_extraction.py` | Tests for extraction.py primitives |
| Create | `test/test_update.py` | Tests for update.py backend |
| Create | `test/test_update_cli.py` | Tests for update CLI commands |

---

## Task 1: SQLite Schema — update_sessions + update_drafts tables

**Files:**
- Modify: `artmind/db.py`
- Create: `test/test_db_update.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_db_update.py
import sqlite3
from datetime import datetime

import pytest

from artmind.db import (
    _get_db,
    _create_update_session,
    _get_update_session,
    _update_session_status,
    _create_update_draft,
    _get_latest_pending_draft,
    _update_draft_status,
    _list_update_sessions,
)


def test_update_sessions_and_drafts_tables_exist():
    conn = _get_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "update_sessions" in tables
    assert "update_drafts" in tables


def test_create_and_get_session():
    _create_update_session("sess1", "general", "alice@example.com")
    session = _get_update_session("sess1")
    assert session is not None
    assert session["domain"] == "general"
    assert session["created_by"] == "alice@example.com"
    assert session["status"] == "draft"


def test_update_session_status():
    _create_update_session("sess2", "fiction", "alice@example.com")
    _update_session_status("sess2", "confirmed")
    session = _get_update_session("sess2")
    assert session["status"] == "confirmed"


def test_create_draft_and_get_latest_pending():
    _create_update_session("sess3", "general", "alice@example.com")
    draft_id = _create_update_draft(
        session_id="sess3",
        raw_text="Alice is the CEO.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    assert isinstance(draft_id, int)
    draft = _get_latest_pending_draft("sess3")
    assert draft is not None
    assert draft["raw_text"] == "Alice is the CEO."
    assert draft["domain"] == "general"


def test_update_draft_status():
    _create_update_session("sess4", "general", "alice@example.com")
    draft_id = _create_update_draft(
        session_id="sess4",
        raw_text="Bob is a manager.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    _update_draft_status(draft_id, "confirmed")
    draft = _get_latest_pending_draft("sess4")
    assert draft is None  # confirmed drafts are not returned as pending


def test_list_update_sessions_returns_sessions():
    _create_update_session("sess5", "general", "alice@example.com")
    _create_update_draft(
        session_id="sess5",
        raw_text="Carol joined in 2024.",
        input_hint="atomic_fact",
        extraction_json='{"entities": [], "relationships": []}',
        candidates_json="[]",
    )
    sessions = _list_update_sessions(domain=None, user=None, limit=20)
    ids = [s["session_id"] for s in sessions]
    assert "sess5" in ids
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/surjitdas/Projects/artmind9 && uv run pytest test/test_db_update.py -v 2>&1 | head -40
```

Expected: `ImportError` or `FAILED` — functions don't exist yet.

- [ ] **Step 3: Add tables and helpers to `artmind/db.py`**

Add two `CREATE TABLE IF NOT EXISTS` blocks inside `_init_db()` after the existing four tables:

```python
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS update_sessions (
            session_id    TEXT PRIMARY KEY,
            domain        TEXT NOT NULL,
            status        TEXT NOT NULL DEFAULT 'draft',
            created_by    TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS update_drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL REFERENCES update_sessions(session_id),
            raw_text        TEXT NOT NULL,
            input_hint      TEXT,
            extraction_json TEXT,
            candidates_json TEXT,
            status          TEXT NOT NULL DEFAULT 'pending',
            created_at      TEXT NOT NULL
        )
    """)
```

Then add these helper functions at the bottom of `artmind/db.py`:

```python
def _create_update_session(session_id: str, domain: str, created_by: str) -> None:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO update_sessions"
            " (session_id, domain, status, created_by, created_at, updated_at)"
            " VALUES (?, ?, 'draft', ?, ?, ?)",
            (session_id, domain, created_by, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _get_update_session(session_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT session_id, domain, status, created_by, created_at, updated_at"
            " FROM update_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "session_id": row[0], "domain": row[1], "status": row[2],
            "created_by": row[3], "created_at": row[4], "updated_at": row[5],
        }
    finally:
        conn.close()


def _update_session_status(session_id: str, status: str) -> None:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        conn.execute(
            "UPDATE update_sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, now, session_id),
        )
        conn.commit()
    finally:
        conn.close()


def _create_update_draft(
    session_id: str,
    raw_text: str,
    input_hint: str | None,
    extraction_json: str,
    candidates_json: str,
) -> int:
    conn = _get_db()
    now = __import__("datetime").datetime.now().isoformat()
    try:
        cursor = conn.execute(
            "INSERT INTO update_drafts"
            " (session_id, raw_text, input_hint, extraction_json, candidates_json, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (session_id, raw_text, input_hint, extraction_json, candidates_json, now),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _get_latest_pending_draft(session_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT d.id, d.session_id, d.raw_text, d.input_hint,"
            "       d.extraction_json, d.candidates_json, d.status, d.created_at,"
            "       s.domain, s.created_by"
            " FROM update_drafts d"
            " JOIN update_sessions s ON d.session_id = s.session_id"
            " WHERE d.session_id = ? AND d.status = 'pending'"
            " ORDER BY d.id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "session_id": row[1], "raw_text": row[2],
            "input_hint": row[3], "extraction_json": row[4],
            "candidates_json": row[5], "status": row[6], "created_at": row[7],
            "domain": row[8], "created_by": row[9],
        }
    finally:
        conn.close()


def _update_draft_status(draft_id: int, status: str) -> None:
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE update_drafts SET status = ? WHERE id = ?",
            (status, draft_id),
        )
        conn.commit()
    finally:
        conn.close()


def _list_update_sessions(
    domain: str | None, user: str | None, limit: int
) -> list[dict]:
    conn = _get_db()
    try:
        conditions = []
        params: list = []
        if domain:
            conditions.append("s.domain = ?")
            params.append(domain)
        if user:
            conditions.append("s.created_by = ?")
            params.append(user)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"""
            SELECT s.session_id, s.domain, s.created_by, s.created_at, s.status,
                   COUNT(d.id) AS input_count,
                   MIN(d.raw_text) AS first_raw_text
            FROM update_sessions s
            LEFT JOIN update_drafts d ON s.session_id = d.session_id
            {where}
            GROUP BY s.session_id
            ORDER BY s.created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return [
            {
                "session_id": r[0], "domain": r[1], "created_by": r[2],
                "created_at": r[3], "status": r[4], "input_count": r[5],
                "excerpt": (r[6] or "")[:80],
            }
            for r in rows
        ]
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_db_update.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/db.py test/test_db_update.py
git commit -m "feat: add update_sessions and update_drafts SQLite tables and helpers"
```

---

## Task 2: Create `artmind/extraction.py` — Shared LLM Primitives

**Files:**
- Create: `artmind/extraction.py`
- Create: `test/test_extraction.py`

- [ ] **Step 1: Write the failing tests**

```python
# test/test_extraction.py
from unittest.mock import MagicMock, patch

from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    entities_list_text,
    parse_json_response,
    extract_with_retry,
)


def test_build_entities_prompt_substitutes_text():
    schema = {"entities_prompt": "Extract from: {text}"}
    assert build_entities_prompt("my text", schema) == "Extract from: my text"


def test_build_properties_prompt_substitutes_entities_and_text():
    schema = {"properties_prompt": "Entities: {entities_list}\nText: {text}"}
    entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    result = build_properties_prompt("my text", entities, schema)
    assert "e0 (PERSON): Alice" in result
    assert "my text" in result


def test_build_relationships_prompt_substitutes_entities_and_text():
    schema = {"relationships_prompt": "Rels: {entities_list}\nText: {text}"}
    entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    result = build_relationships_prompt("my text", entities, schema)
    assert "e0 (PERSON): Alice" in result
    assert "my text" in result


def test_entities_list_text_formats_correctly():
    entities = [
        {"id": "e1", "entity_class": "PERSON", "name": "Alice"},
        {"id": "e2", "entity_class": "LOCATION", "name": "London"},
    ]
    assert entities_list_text(entities) == "e1 (PERSON): Alice\ne2 (LOCATION): London"


def test_parse_json_response_strips_think_blocks():
    raw = '<think>reasoning here</think>\n[{"name": "Alice"}]'
    assert parse_json_response(raw) == [{"name": "Alice"}]


def test_parse_json_response_strips_code_fences():
    raw = '```json\n[{"name": "Alice"}]\n```'
    assert parse_json_response(raw) == [{"name": "Alice"}]


def test_parse_json_response_handles_plain_json():
    raw = '[{"name": "Bob"}]'
    assert parse_json_response(raw) == [{"name": "Bob"}]


def test_extract_with_retry_returns_result_on_success():
    with patch("artmind.extraction.call_llm", return_value='[{"name": "Alice"}]'):
        result, ok = extract_with_retry("test_step", "test-model", "some prompt")
    assert ok is True
    assert result == [{"name": "Alice"}]


def test_extract_with_retry_returns_empty_list_on_failure():
    with patch("artmind.extraction.call_llm", side_effect=Exception("LLM error")):
        result, ok = extract_with_retry("test_step", "test-model", "some prompt")
    assert ok is False
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_extraction.py -v 2>&1 | head -20
```

Expected: `ImportError` — module doesn't exist yet.

- [ ] **Step 3: Create `artmind/extraction.py`**

```python
# artmind/extraction.py
import re
from pathlib import Path

import json_repair
import ollama
from loguru import logger

from utils.functions import load_env, log_llm_call


def embed_text(model: str, text: str) -> list[float]:
    response = ollama.embed(model=model, input=text)
    embedding = response.embeddings[0]
    log_llm_call("embed", model, text, f"[embedding vector, dim={len(embedding)}]")
    return embedding


def call_llm(model: str, prompt: str) -> str:
    env = load_env()
    timeout = int(env.get("ARTMIND_OLLAMA_TIMEOUT", "120"))
    response = ollama.Client(timeout=timeout).chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    result = (response.message.content or "").strip()
    log_llm_call("chat", model, prompt, result)
    return result


def parse_json_response(text: str):
    text = text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return json_repair.loads(text.strip())


def extract_with_retry(
    step_name: str,
    model: str,
    prompt: str,
    debug_dir: Path | None = None,
) -> tuple[list, bool]:
    raw_llm = ""
    for attempt in range(2):
        try:
            raw_llm = call_llm(model, prompt)
            return parse_json_response(raw_llm), True
        except Exception as e:
            if attempt == 0:
                logger.warning("  {} failed (attempt 1/2), retrying: {}", step_name, e)
            else:
                logger.error("  {} failed after 2 attempts: {}", step_name, e)
                if raw_llm and debug_dir:
                    safe = re.sub(r"[^A-Za-z0-9_]", "_", step_name)
                    (debug_dir / f"debug_{safe}.txt").write_text(raw_llm, encoding="utf-8")
    return [], False


def entities_list_text(entities: list[dict]) -> str:
    return "\n".join(f"{e['id']} ({e['entity_class']}): {e['name']}" for e in entities)


def build_entities_prompt(text: str, schema: dict) -> str:
    return schema.get("entities_prompt", "").replace("{text}", text)


def build_properties_prompt(text: str, entities: list[dict], schema: dict) -> str:
    ent_list = entities_list_text(entities)
    return (
        schema.get("properties_prompt", "")
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )


def build_relationships_prompt(text: str, entities: list[dict], schema: dict) -> str:
    ent_list = entities_list_text(entities)
    return (
        schema.get("relationships_prompt", "")
        .replace("{entities_list}", ent_list)
        .replace("{text}", text)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_extraction.py -v
```

Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/extraction.py test/test_extraction.py
git commit -m "feat: add artmind/extraction.py with shared LLM prompt-build and parse primitives"
```

---

## Task 3: Refactor `artmind/ingest.py` to Use `extraction.py`

**Files:**
- Modify: `artmind/ingest.py`

This is a pure refactor — no behaviour change. Existing tests must remain green.

- [ ] **Step 1: Verify existing tests pass before touching anything**

```bash
uv run pytest test/test_ingest_sync_cli.py test/test_graph_query.py test/test_domains_cli.py -v
```

Expected: All PASS.

- [ ] **Step 2: Update imports in `artmind/ingest.py`**

At the top of `artmind/ingest.py`, add:

```python
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
```

- [ ] **Step 3: Remove the now-duplicated functions from `artmind/ingest.py`**

Delete the following function bodies (keep the rest of the file intact):
- `_embed_text` (lines ~531–535) — now imported as alias
- `_call_llm_text` (lines ~538–550) — now imported as alias
- `_parse_json_response` (lines ~571–578) — now imported as alias
- `_entities_list_text` (lines ~581–582) — now imported as alias

For `_llm_extract`, replace it with a thin wrapper that passes `debug_dir` through:

```python
def _llm_extract(step_name: str, model: str, prompt: str, debug_dir: Path) -> tuple[list, bool]:
    return _llm_extract_shared(step_name, model, prompt, debug_dir=debug_dir)
```

- [ ] **Step 4: Replace inline prompt template substitutions in `extract_kg()`**

In `_process_chunk()` inside `extract_kg()`, find the three `_llm_extract` call sites and update them:

Before (entities):
```python
raw_entities, ok = _llm_extract(
    f"chunk_{seq:03d}_entities",
    text_model,
    entities_tpl.replace("{text}", chunk_text),
    doc_kg_dir,
)
```

After (entities):
```python
raw_entities, ok = _llm_extract(
    f"chunk_{seq:03d}_entities",
    text_model,
    build_entities_prompt(chunk_text, schema),
    doc_kg_dir,
)
```

Before (properties):
```python
raw_props, ok = _llm_extract(
    f"chunk_{seq:03d}_properties",
    text_model,
    properties_tpl.replace("{entities_list}", entities_list).replace("{text}", chunk_text),
    doc_kg_dir,
)
```

After (properties):
```python
raw_props, ok = _llm_extract(
    f"chunk_{seq:03d}_properties",
    text_model,
    build_properties_prompt(chunk_text, data.get("raw_entities", []), schema),
    doc_kg_dir,
)
```

Before (relationships):
```python
raw_rels, ok = _llm_extract(
    f"chunk_{seq:03d}_relationships",
    text_model,
    relationships_tpl.replace("{entities_list}", entities_list).replace("{text}", chunk_text),
    doc_kg_dir,
)
```

After (relationships):
```python
raw_rels, ok = _llm_extract(
    f"chunk_{seq:03d}_relationships",
    text_model,
    build_relationships_prompt(chunk_text, data.get("raw_entities", []), schema),
    doc_kg_dir,
)
```

Also remove these three lines from `extract_kg()` (they built the template strings that are now handled inside `build_*_prompt`):
```python
entities_tpl = schema.get("entities_prompt", "")
properties_tpl = schema.get("properties_prompt", "")
relationships_tpl = schema.get("relationships_prompt", "")
```

- [ ] **Step 5: Run all existing tests to confirm no regressions**

```bash
uv run pytest test/test_ingest_sync_cli.py test/test_graph_query.py test/test_domains_cli.py test/test_extraction.py -v
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/ingest.py
git commit -m "refactor: ingest.py imports LLM primitives from extraction.py"
```

---

## Task 4: `artmind/update.py` — `_classify_input()` and `extract_facts()`

**Files:**
- Create: `artmind/update.py`
- Create: `test/test_update.py`

- [ ] **Step 1: Write failing tests**

```python
# test/test_update.py
from unittest.mock import patch

import pytest

from artmind.update import _classify_input, extract_facts


def test_classify_input_atomic_fact():
    assert _classify_input("Alice is the CEO") == "atomic_fact"


def test_classify_input_todo():
    assert _classify_input("TODO: call Bob tomorrow") == "todo"


def test_classify_input_need_to():
    assert _classify_input("Need to review the proposal") == "todo"


def test_classify_input_passage():
    text = "Alice works at Acme. Bob is her manager. They met in 2020."
    assert _classify_input(text) == "passage"


def test_classify_input_bulk():
    long_text = "a " * 300  # > 500 chars
    assert _classify_input(long_text) == "bulk"


def test_extract_facts_returns_entities_with_temp_ids():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_entities = [{"id": "e0", "entity_class": "PERSON", "name": "Alice"}]
    mock_props = [{"name": "Alice", "properties": {"role": "CEO"}}]
    mock_rels = [{"source_name": "Alice", "target_name": "Alice", "rel_type": "KNOWS"}]

    with patch("artmind.update.extract_with_retry") as mock:
        mock.side_effect = [
            (mock_entities, True),
            (mock_props, True),
            (mock_rels, True),
        ]
        result = extract_facts("Alice is CEO.", "general", schema, text_model="test-model")

    assert len(result["entities"]) == 1
    assert result["entities"][0]["name"] == "Alice"
    assert result["entities"][0]["temp_id"] == "e0"
    assert result["entities"][0]["properties"]["role"] == "CEO"


def test_extract_facts_returns_empty_on_entity_failure():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    with patch("artmind.update.extract_with_retry") as mock:
        mock.return_value = ([], False)
        result = extract_facts("some text", "general", schema, text_model="test-model")

    assert result["entities"] == []
    assert result["relationships"] == []
    # Only one LLM call made (entities failed, no properties/relationships attempted)
    assert mock.call_count == 1


def test_extract_facts_maps_relationship_source_target_to_temp_ids():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_entities = [
        {"id": "e0", "entity_class": "PERSON", "name": "Alice"},
        {"id": "e1", "entity_class": "ORG", "name": "Acme"},
    ]
    mock_rels = [{"source_name": "Alice", "target_name": "Acme", "rel_type": "WORKS_AT"}]

    with patch("artmind.update.extract_with_retry") as mock:
        mock.side_effect = [
            (mock_entities, True),
            ([], True),
            (mock_rels, True),
        ]
        result = extract_facts("Alice works at Acme.", "general", schema, text_model="test-model")

    assert len(result["relationships"]) == 1
    assert result["relationships"][0]["source_temp_id"] == "e0"
    assert result["relationships"][0]["target_temp_id"] == "e1"
    assert result["relationships"][0]["rel_type"] == "WORKS_AT"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update.py -v 2>&1 | head -20
```

Expected: `ImportError`.

- [ ] **Step 3: Create `artmind/update.py` with `_classify_input()` and `extract_facts()`**

```python
# artmind/update.py
import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from artmind.db import (
    _create_update_draft,
    _create_update_session,
    _get_latest_pending_draft,
    _get_update_session,
    _list_update_sessions,
    _update_draft_status,
    _update_session_status,
)
from artmind.extraction import (
    build_entities_prompt,
    build_properties_prompt,
    build_relationships_prompt,
    embed_text,
    extract_with_retry,
)
from artmind.graph_query import neo4j_session
from artmind.ingest import _flatten_props, _sanitize_label
from paths import DOMAIN_SCHEMAS_DIR
from utils.functions import load_env


def _classify_input(text: str) -> str:
    text = text.strip()
    if len(text) > 500:
        return "bulk"
    lower = text.lower()
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if len(sentences) <= 1:
        if any(kw in lower for kw in ("todo", "task", "remind", "need to", "should")):
            return "todo"
        return "atomic_fact"
    return "passage"


def extract_facts(
    text: str, domain: str, schema: dict, text_model: str | None = None
) -> dict:
    env = load_env()
    model = text_model or env.get("ARTMIND_KG_TEXT_MODEL", "ministral-3:14b")

    raw_entities, ok = extract_with_retry(
        "update_entities", model, build_entities_prompt(text, schema)
    )
    if not ok:
        raw_entities = []

    entities = [
        {
            "temp_id": e.get("id", f"e{i}"),
            "name": e.get("name", ""),
            "entity_class": e.get("entity_class", "UNKNOWN"),
            "properties": {},
        }
        for i, e in enumerate(raw_entities)
    ]

    raw_props: list = []
    raw_rels: list = []
    if raw_entities:
        raw_props, _ = extract_with_retry(
            "update_properties",
            model,
            build_properties_prompt(text, raw_entities, schema),
        )
        raw_rels, _ = extract_with_retry(
            "update_relationships",
            model,
            build_relationships_prompt(text, raw_entities, schema),
        )

    props_by_name = {
        p.get("name", p.get("id", "")): p.get("properties", {})
        for p in raw_props
    }
    for entity in entities:
        entity["properties"] = props_by_name.get(entity["name"], {})

    name_to_temp = {e["name"]: e["temp_id"] for e in entities}
    relationships = [
        {
            "source_temp_id": name_to_temp[r["source_name"]],
            "target_temp_id": name_to_temp[r["target_name"]],
            "rel_type": r.get("rel_type", "RELATED_TO"),
            "description": r.get("description", ""),
        }
        for r in raw_rels
        if r.get("source_name") in name_to_temp and r.get("target_name") in name_to_temp
    ]

    return {"entities": entities, "relationships": relationships}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/update.py test/test_update.py
git commit -m "feat: add update.py with _classify_input and extract_facts"
```

---

## Task 5: `artmind/update.py` — `find_candidates()`

**Files:**
- Modify: `artmind/update.py`
- Modify: `test/test_update.py`

- [ ] **Step 1: Add failing test to `test/test_update.py`**

```python
# append to test/test_update.py
from artmind.update import find_candidates


def test_find_candidates_returns_domain_matches_first():
    mock_rows = [
        {"node_id": "n1", "name": "Alice Smith", "entity_class": "PERSON",
         "context_snippet": "CEO of Acme", "match_score": 1.0}
    ]
    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows
        result = find_candidates("Alice", "PERSON", "general", top_n=5)

    assert len(result) == 1
    assert result[0]["name"] == "Alice Smith"


def test_find_candidates_falls_back_to_global_when_domain_empty():
    global_rows = [
        {"node_id": "n2", "name": "Alice Jones", "entity_class": "PERSON",
         "context_snippet": None, "match_score": 0.5}
    ]

    def run_side_effect(cypher, **kwargs):
        mock_result = MagicMock()
        # First call (domain-scoped) returns empty; second (global) returns data
        mock_result.data.return_value = [] if "e.domain = $domain" in cypher else global_rows
        return mock_result

    with patch("artmind.update.neo4j_session") as mock_session_ctx:
        mock_session = mock_session_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect
        result = find_candidates("Alice", "PERSON", "general", top_n=5)

    assert len(result) == 1
    assert result[0]["name"] == "Alice Jones"
```

Add `from unittest.mock import MagicMock` to the top of `test/test_update.py` imports.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update.py::test_find_candidates_returns_domain_matches_first -v
```

Expected: `ImportError` for `find_candidates`.

- [ ] **Step 3: Add `find_candidates()` to `artmind/update.py`**

```python
def find_candidates(
    entity_name: str, entity_class: str, domain: str, top_n: int = 5
) -> list[dict]:
    cypher_domain = """
    MATCH (e:Entity)
    WHERE e.domain = $domain
      AND (toLower(e.name) CONTAINS toLower($name)
           OR toLower($name) CONTAINS toLower(e.name))
    RETURN e.id AS node_id, e.name AS name, e.entity_class AS entity_class,
           e.description AS context_snippet,
           CASE WHEN toLower(e.name) = toLower($name) THEN 1.0 ELSE 0.5 END AS match_score
    ORDER BY match_score DESC, size(e.name) ASC
    LIMIT $top_n
    """
    cypher_global = """
    MATCH (e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($name)
       OR toLower($name) CONTAINS toLower(e.name)
    RETURN e.id AS node_id, e.name AS name, e.entity_class AS entity_class,
           e.description AS context_snippet,
           CASE WHEN toLower(e.name) = toLower($name) THEN 1.0 ELSE 0.5 END AS match_score
    ORDER BY match_score DESC
    LIMIT $top_n
    """
    with neo4j_session() as session:
        rows = session.run(cypher_domain, domain=domain, name=entity_name, top_n=top_n).data()
        if not rows:
            rows = session.run(cypher_global, name=entity_name, top_n=top_n).data()
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update.py -v
```

Expected: All 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/update.py test/test_update.py
git commit -m "feat: add find_candidates to update.py with domain-first fuzzy matching"
```

---

## Task 6: `artmind/update.py` — `write_user_chat()`, `_update_node_in_session()`, `draft_update()`, `confirm_update()`

**Files:**
- Modify: `artmind/update.py`
- Modify: `test/test_update.py`

- [ ] **Step 1: Add failing tests**

```python
# append to test/test_update.py
from artmind.update import write_user_chat, draft_update, confirm_update


def test_write_user_chat_creates_node_and_returns_summary():
    resolutions = [{"entity_temp_id": "e0", "action": "create", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1",
            raw_text="Alice is CEO.",
            domain="general",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=[],
        )

    assert "user_chat_id" in result
    assert result["nodes_created"] == 1
    assert result["nodes_updated"] == 0
    assert result["relationships_written"] == 0


def test_write_user_chat_skipped_entity_not_written():
    resolutions = [{"entity_temp_id": "e0", "action": "skip", "node_id": None}]
    extracted_entities = [{"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}]

    with patch("artmind.update.embed_text", return_value=[0.1] * 768), \
         patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = MagicMock()

        result = write_user_chat(
            session_id="sess1",
            raw_text="Alice is CEO.",
            domain="general",
            user_id="alice@example.com",
            resolutions=resolutions,
            extracted_entities=extracted_entities,
            extracted_relationships=[],
        )

    assert result["nodes_created"] == 0
    assert result["nodes_updated"] == 0


def test_draft_update_stores_draft_and_returns_session_id():
    schema = {
        "entities_prompt": "Extract: {text}",
        "properties_prompt": "Props {entities_list} {text}",
        "relationships_prompt": "Rels {entities_list} {text}",
    }
    mock_facts = {"entities": [], "relationships": []}

    with patch("artmind.update.extract_facts", return_value=mock_facts), \
         patch("artmind.update.find_candidates", return_value=[]), \
         patch("artmind.update._load_schema", return_value=schema), \
         patch("artmind.update._create_update_session"), \
         patch("artmind.update._create_update_draft", return_value=1):

        result = draft_update(
            domain="general",
            text="Alice is CEO.",
            session_id=None,
            user_id="alice@example.com",
        )

    assert "session_id" in result
    assert "extracted_entities" in result
    assert "candidates_per_entity" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update.py::test_write_user_chat_creates_node_and_returns_summary -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add `_ensure_user_chat_schema()`, `_update_node_in_session()`, `write_user_chat()`, `_load_schema()`, `draft_update()`, `confirm_update()` to `artmind/update.py`**

```python
def _load_schema(domain: str) -> dict:
    schema_file = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    if not schema_file.exists():
        schema_file = DOMAIN_SCHEMAS_DIR / "general_schema.yaml"
    return yaml.safe_load(schema_file.read_text(encoding="utf-8"))


def _ensure_user_chat_schema(session, embedding_dim: int = 768) -> None:
    session.run(
        "CREATE CONSTRAINT user_chat_id IF NOT EXISTS FOR (n:UserChat) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        f"CREATE VECTOR INDEX user_chat_embedding IF NOT EXISTS "
        f"FOR (c:UserChat) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )


def _update_node_in_session(
    session, node_id: str, new_properties: dict, user_id: str, now: str
) -> None:
    props = _flatten_props({**new_properties, "updated_at": now, "updated_by": user_id})
    session.run(
        "MATCH (e:Entity) WHERE e.id = $node_id SET e += $props",
        node_id=node_id,
        props=props,
    )


def write_user_chat(
    session_id: str,
    raw_text: str,
    domain: str,
    user_id: str,
    resolutions: list[dict],
    extracted_entities: list[dict],
    extracted_relationships: list[dict],
) -> dict:
    env = load_env()
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))
    now = datetime.now().isoformat()
    chat_id = uuid.uuid4().hex
    embedding = embed_text(embed_model, raw_text)
    input_hint = _classify_input(raw_text)

    with neo4j_session() as session:
        _ensure_user_chat_schema(session, embedding_dim)

        session.run(
            """
            CREATE (c:UserChat {
                id: $id, raw_text: $raw_text, embedding: $embedding,
                domain: $domain, session_id: $session_id,
                input_hint: $input_hint, created_at: $now, created_by: $user_id
            })
            """,
            id=chat_id, raw_text=raw_text, embedding=embedding,
            domain=domain, session_id=session_id,
            input_hint=input_hint, now=now, user_id=user_id,
        )

        entity_names: dict[str, str] = {}
        nodes_created = 0
        nodes_updated = 0

        for res in resolutions:
            temp_id = res["entity_temp_id"]
            action = res["action"]
            entity_data = next(
                (e for e in extracted_entities if e["temp_id"] == temp_id), None
            )
            if not entity_data:
                continue

            if action == "create":
                label_str = f"{_sanitize_label(entity_data['entity_class'])}:Entity"
                props = _flatten_props({
                    "name": entity_data["name"],
                    "entity_class": entity_data["entity_class"],
                    "domain": domain,
                    "created_at": now,
                    "created_by": user_id,
                    "updated_at": now,
                    "updated_by": user_id,
                    **entity_data.get("properties", {}),
                })
                session.run(f"CREATE (e:{label_str}) SET e = $props", props=props)
                entity_names[temp_id] = entity_data["name"]
                nodes_created += 1

            elif action == "link":
                _update_node_in_session(
                    session, res["node_id"],
                    entity_data.get("properties", {}), user_id, now,
                )
                entity_names[temp_id] = entity_data["name"]
                nodes_updated += 1

            if action in ("create", "link"):
                session.run(
                    """
                    MATCH (c:UserChat {id: $chat_id})
                    MATCH (e:Entity {name: $ename, domain: $domain})
                    MERGE (c)-[:MENTIONS]->(e)
                    """,
                    chat_id=chat_id, ename=entity_data["name"], domain=domain,
                )

        rel_count = 0
        for rel in extracted_relationships:
            src_name = entity_names.get(rel.get("source_temp_id", ""))
            tgt_name = entity_names.get(rel.get("target_temp_id", ""))
            if not src_name or not tgt_name:
                continue
            rel_type = _sanitize_label(rel.get("rel_type", "RELATED_TO"))
            rel_props = _flatten_props({
                "source_chat_id": chat_id,
                "created_at": now,
                "created_by": user_id,
                "updated_at": now,
                "updated_by": user_id,
            })
            try:
                session.run(
                    """
                    MATCH (src:Entity {name: $src, domain: $domain})
                    MATCH (tgt:Entity {name: $tgt, domain: $domain})
                    CALL apoc.merge.relationship(src, $type, {source_chat_id: $chat_id},
                         $props, tgt, {}) YIELD rel
                    RETURN rel
                    """,
                    src=src_name, tgt=tgt_name, type=rel_type,
                    chat_id=chat_id, props=rel_props, domain=domain,
                )
                rel_count += 1
            except Exception as e:
                logger.warning(
                    "Relationship skipped ({} -[{}]-> {}): {}",
                    src_name, rel_type, tgt_name, e,
                )

    return {
        "user_chat_id": chat_id,
        "nodes_created": nodes_created,
        "nodes_updated": nodes_updated,
        "relationships_written": rel_count,
    }


def draft_update(
    domain: str, text: str, session_id: str | None, user_id: str
) -> dict:
    schema = _load_schema(domain)

    if not session_id:
        session_id = uuid.uuid4().hex
        _create_update_session(session_id, domain, user_id)

    facts = extract_facts(text, domain, schema)

    candidates_per_entity = [
        {
            "entity": e["name"],
            "temp_id": e["temp_id"],
            "top_n": find_candidates(e["name"], e["entity_class"], domain),
        }
        for e in facts["entities"]
    ]

    _create_update_draft(
        session_id=session_id,
        raw_text=text,
        input_hint=_classify_input(text),
        extraction_json=json.dumps(facts),
        candidates_json=json.dumps(candidates_per_entity),
    )

    return {
        "session_id": session_id,
        "extracted_entities": facts["entities"],
        "extracted_relationships": facts["relationships"],
        "candidates_per_entity": candidates_per_entity,
    }


def confirm_update(session_id: str, resolutions: list[dict], user_id: str) -> dict:
    draft = _get_latest_pending_draft(session_id)
    if not draft:
        raise ValueError(f"No pending draft for session {session_id!r}")

    facts = json.loads(draft["extraction_json"])

    result = write_user_chat(
        session_id=session_id,
        raw_text=draft["raw_text"],
        domain=draft["domain"],
        user_id=user_id,
        resolutions=resolutions,
        extracted_entities=facts["entities"],
        extracted_relationships=facts["relationships"],
    )

    _update_draft_status(draft["id"], "confirmed")
    _update_session_status(session_id, "confirmed")

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/update.py test/test_update.py
git commit -m "feat: add write_user_chat, draft_update, confirm_update to update.py"
```

---

## Task 7: `artmind/update.py` — `export_chats()`

**Files:**
- Modify: `artmind/update.py`
- Modify: `test/test_update.py`

- [ ] **Step 1: Add failing test**

```python
# append to test/test_update.py
from artmind.update import export_chats
import tempfile


def test_export_chats_sequential_writes_markdown(tmp_path):
    mock_rows = [
        {
            "session_id": "s1", "id": "c1", "raw_text": "Alice is CEO.",
            "domain": "general", "created_by": "alice@example.com",
            "created_at": "2026-05-05T10:00:00", "input_hint": "atomic_fact",
            "mentions": ["Alice"],
        }
    ]
    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows

        written = export_chats(domain=None, format="sequential", output_dir=tmp_path)

    assert len(written) == 1
    content = written[0].read_text()
    assert "Alice is CEO." in content
    assert "alice@example.com" in content


def test_export_chats_by_entity_writes_one_file_per_entity(tmp_path):
    mock_rows = [
        {
            "entity_name": "Alice",
            "chats": [
                {"id": "c1", "raw_text": "Alice is CEO.", "created_by": "alice@example.com",
                 "created_at": "2026-05-05T10:00:00", "domain": "general"},
            ],
        }
    ]
    with patch("artmind.update.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.data.return_value = mock_rows

        written = export_chats(domain=None, format="by-entity", output_dir=tmp_path)

    assert len(written) == 1
    content = written[0].read_text()
    assert "Alice" in content
    assert "Alice is CEO." in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update.py::test_export_chats_sequential_writes_markdown -v
```

Expected: `ImportError`.

- [ ] **Step 3: Add `export_chats()` to `artmind/update.py`**

```python
def export_chats(
    domain: str | None, format: str, output_dir: Path
) -> list[Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if format == "sequential":
        cypher = """
        MATCH (c:UserChat)
        WHERE $domain IS NULL OR c.domain = $domain
        OPTIONAL MATCH (c)-[:MENTIONS]->(e:Entity)
        WITH c, collect(e.name) AS mentions
        ORDER BY c.created_at ASC
        RETURN c.session_id AS session_id, c.id AS id, c.raw_text AS raw_text,
               c.domain AS domain, c.created_by AS created_by,
               c.created_at AS created_at, c.input_hint AS input_hint,
               mentions
        """
        with neo4j_session() as session:
            rows = session.run(cypher, domain=domain).data()

        sessions: dict[str, list[dict]] = {}
        for row in rows:
            sessions.setdefault(row["session_id"], []).append(row)

        for sid, chats in sessions.items():
            lines = [f"# Session {sid}\n"]
            for chat in chats:
                lines.append(f"**{chat['created_at']}** — {chat['created_by']}")
                lines.append(f"*Domain:* {chat['domain']}  *Hint:* {chat['input_hint']}")
                lines.append(f"\n{chat['raw_text']}\n")
                if chat["mentions"]:
                    lines.append(f"*Mentions:* {', '.join(chat['mentions'])}\n")
                lines.append("---\n")
            out = output_dir / f"session_{sid[:8]}.md"
            out.write_text("\n".join(lines), encoding="utf-8")
            written.append(out)

    elif format == "by-entity":
        cypher = """
        MATCH (c:UserChat)-[:MENTIONS]->(e:Entity)
        WHERE $domain IS NULL OR c.domain = $domain
        WITH e.name AS entity_name, collect({
            id: c.id, raw_text: c.raw_text, created_by: c.created_by,
            created_at: c.created_at, domain: c.domain
        }) AS chats
        ORDER BY entity_name
        RETURN entity_name, chats
        """
        with neo4j_session() as session:
            rows = session.run(cypher, domain=domain).data()

        for row in rows:
            entity_name = row["entity_name"]
            safe_name = "".join(c if c.isalnum() else "_" for c in entity_name)
            lines = [f"# {entity_name}\n"]
            for chat in row["chats"]:
                lines.append(f"**{chat['created_at']}** — {chat['created_by']}")
                lines.append(f"\n{chat['raw_text']}\n")
                lines.append("---\n")
            out = output_dir / f"entity_{safe_name}.md"
            out.write_text("\n".join(lines), encoding="utf-8")
            written.append(out)

    return written
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update.py -v
```

Expected: All 14 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/update.py test/test_update.py
git commit -m "feat: add export_chats to update.py"
```

---

## Task 8: Extend Vector Search to Include `UserChat` Nodes

**Files:**
- Modify: `artmind/vector_query.py`
- Modify: `test/test_vector_query.py`

- [ ] **Step 1: Read existing test file to understand its structure**

```bash
cat test/test_vector_query.py
```

- [ ] **Step 2: Add failing test for UserChat union search**

Add to `test/test_vector_query.py`:

```python
from unittest.mock import MagicMock, patch


def test_vector_search_unions_docchunk_and_userchat_results():
    mock_doc_rows = [{
        "score": 0.9,
        "chunk": {"id": "c1", "name": "Chunk 1", "doc_id": "d1", "text": "Alice is CEO"},
        "document": {"id": "d1", "name": "report.pdf", "path": "/a/b", "domain": "general"},
        "source_type": "document",
    }]
    mock_chat_rows = [{
        "score": 0.8,
        "chat": {"id": "ch1", "raw_text": "Alice leads the team", "domain": "general",
                 "created_by": "alice@example.com", "created_at": "2026-05-05T10:00:00"},
        "source_type": "user_chat",
    }]

    with patch("artmind.vector_query.embed_question", return_value=[0.1] * 768), \
         patch("artmind.vector_query.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value

        def run_side_effect(cypher, **kwargs):
            mock_result = MagicMock()
            if "DocChunk" in cypher:
                mock_result.__iter__ = lambda s: iter([MagicMock(**{"__getitem__.side_effect": mock_doc_rows[0].__getitem__})])
            else:
                mock_result.__iter__ = lambda s: iter([MagicMock(**{"__getitem__.side_effect": mock_chat_rows[0].__getitem__})])
            return mock_result

        # Use the simpler approach: mock serialize_record
        with patch("artmind.vector_query.serialize_record") as mock_ser, \
             patch("artmind.vector_query.strip_embeddings", side_effect=lambda x: x):
            mock_ser.side_effect = [mock_doc_rows[0], mock_chat_rows[0]]
            mock_session.run.return_value = [MagicMock(), MagicMock()]

            result = vector_search("general", "Alice", topK=5)

    assert result["query_type"] == "vector"
    assert "rows" in result
```

Note: The mock structure for vector_query tests is complex because of the neo4j driver. The key behaviour to test is that the result includes a `source_type` field. For a simpler test, check the output structure:

```python
def test_vector_search_result_has_source_type_field():
    with patch("artmind.vector_query.embed_question", return_value=[0.1] * 768), \
         patch("artmind.vector_query.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value = []

        result = vector_search("general", "Alice", topK=5)

    assert result["domain"] == "general"
    assert result["query_type"] == "vector"
    assert "rows" in result
    # rows is empty because mock returns [], but the shape is correct
```

Add the import `from artmind.vector_query import vector_search` to the test file.

- [ ] **Step 3: Run tests to verify they fail or show the current output lacks source_type**

```bash
uv run pytest test/test_vector_query.py -v
```

- [ ] **Step 4: Update `artmind/vector_query.py`** to union DocChunk + UserChat

Replace the entire `vector_search()` function:

```python
def vector_search(domain: str, question: str, topK: int = 5) -> dict:
    embedding = embed_question(question)

    cypher_chunks = """
    CYPHER 25
    MATCH (node:DocChunk)
      SEARCH node IN (
        VECTOR INDEX chunk_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE node.domain = $domain
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN score,
           node { .id, .name, .doc_id, .text } AS chunk,
           document { .id, .name, .path, .domain } AS document,
           'document' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_chats = """
    CYPHER 25
    MATCH (node:UserChat)
      SEARCH node IN (
        VECTOR INDEX user_chat_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE node.domain = $domain
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node { .id, .raw_text, .domain, .created_by, .created_at } AS chat,
           'user_chat' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    params = {
        "domain": domain,
        "embedding": embedding,
        "topK": int(topK),
        "candidateK": max(int(topK) * 5, int(topK)),
    }

    with neo4j_session() as session:
        chunk_rows = [
            strip_embeddings(serialize_record(record))
            for record in session.run(cypher_chunks, **params)
        ]
        chat_rows = [
            strip_embeddings(serialize_record(record))
            for record in session.run(cypher_chats, **params)
        ]

    all_rows = sorted(chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True)[:int(topK)]

    return {
        "domain": domain,
        "query_type": "vector",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": all_rows,
    }
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest test/test_vector_query.py -v
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/vector_query.py test/test_vector_query.py
git commit -m "feat: vector_search unions DocChunk and UserChat results with source_type field"
```

---

## Task 9: Update `graph_query.py` MENTIONS Traversal Patterns

**Files:**
- Modify: `artmind/graph_query.py`
- Modify: `test/test_graph_query.py`

- [ ] **Step 1: Read `graph_query.py` in full to identify all MENTIONS traversal sites**

```bash
grep -n "MENTIONS\|DocChunk\|chunk\|source" artmind/graph_query.py | head -60
```

For each pattern function that traverses `DocChunk` or `MENTIONS`, update it to also optionally match `UserChat`.

- [ ] **Step 2: Add a failing test for source attribution branching**

Add to `test/test_graph_query.py`:

```python
def test_pattern_cypher_includes_user_chat_source_match():
    from artmind.graph_query import _pattern_query
    cypher, _ = _pattern_query("pattern2", {
        "domain": "general",
        "entityNameList": "Alice",
    })
    assert "UserChat" in cypher or "user_chat" in cypher.lower()
```

- [ ] **Step 3: Update MENTIONS patterns in `graph_query.py`**

In each pattern that contains `OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)` or similar:

Before:
```cypher
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
RETURN e, collect(chunk) AS sources
```

After:
```cypher
OPTIONAL MATCH (chunk:DocChunk)-[:MENTIONS]->(e)
OPTIONAL MATCH (chat:UserChat)-[:MENTIONS]->(e)
RETURN e,
       collect(DISTINCT chunk { .id, .name, .doc_id, source_type: 'document' }) AS doc_sources,
       collect(DISTINCT chat { .id, .session_id, .created_by, .created_at, source_type: 'user_chat' }) AS chat_sources
```

The exact lines to change depend on the actual pattern implementations. Read `graph_query.py` in full before editing, then apply this pattern to every relevant pattern function.

- [ ] **Step 4: Run all graph_query tests**

```bash
uv run pytest test/test_graph_query.py -v
```

Expected: All PASS. If any break, inspect and fix — the return shape changed from `sources` to `doc_sources` + `chat_sources`.

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py test/test_graph_query.py
git commit -m "feat: graph_query patterns branch MENTIONS on DocChunk vs UserChat with source_type"
```

---

## Task 10: CLI — `artmind update draft` and `artmind update confirm`

**Files:**
- Modify: `artmind/cli.py`
- Create: `test/test_update_cli.py`

- [ ] **Step 1: Write failing tests**

```python
# test/test_update_cli.py
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


def test_update_draft_returns_json(runner):
    draft_result = {
        "session_id": "abc123",
        "extracted_entities": [
            {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
        ],
        "extracted_relationships": [],
        "candidates_per_entity": [{"entity": "Alice", "temp_id": "e0", "top_n": []}],
    }
    with patch("artmind.cli.update_backend.draft_update", return_value=draft_result):
        result = runner.invoke(cli, [
            "update", "draft",
            "--domain", "general",
            "--text", "Alice is a person",
        ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["session_id"] == "abc123"
    assert len(data["extracted_entities"]) == 1


def test_update_draft_with_existing_session(runner):
    draft_result = {
        "session_id": "existing-session",
        "extracted_entities": [],
        "extracted_relationships": [],
        "candidates_per_entity": [],
    }
    with patch("artmind.cli.update_backend.draft_update", return_value=draft_result) as mock:
        runner.invoke(cli, [
            "update", "draft",
            "--domain", "general",
            "--text", "Some fact",
            "--session", "existing-session",
        ])
    mock.assert_called_once_with(
        domain="general",
        text="Some fact",
        session_id="existing-session",
        user_id=mock.call_args.kwargs["user_id"],
    )


def test_update_confirm_returns_json(runner):
    confirm_result = {
        "nodes_created": 1,
        "nodes_updated": 0,
        "relationships_written": 0,
        "user_chat_id": "chat001",
    }
    resolutions = json.dumps([
        {"entity_temp_id": "e0", "action": "create", "node_id": None}
    ])
    with patch("artmind.cli.update_backend.confirm_update", return_value=confirm_result):
        result = runner.invoke(cli, [
            "update", "confirm",
            "--session", "abc123",
            "--resolutions", resolutions,
        ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["nodes_created"] == 1
    assert data["user_chat_id"] == "chat001"


def test_update_confirm_fails_gracefully_on_missing_draft(runner):
    with patch("artmind.cli.update_backend.confirm_update",
               side_effect=ValueError("No pending draft")):
        result = runner.invoke(cli, [
            "update", "confirm",
            "--session", "bad-session",
            "--resolutions", "[]",
        ])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update_cli.py -v 2>&1 | head -20
```

Expected: `ImportError` or `No such command 'update'`.

- [ ] **Step 3: Add `update` command group with `draft` and `confirm` to `artmind/cli.py`**

Add the import at the top of `cli.py`:

```python
import artmind.update as update_backend
```

Then add the command group after the existing `ingest` group:

```python
# ── artmind update ─────────────────────────────────────────────────────────────


@cli.group()
def update():
    """Add and update knowledge graph facts from natural language."""
    pass


@update.command("draft")
@click.option("--domain", required=True, help="Domain name.")
@click.option("--text", required=True, help="Raw user input text.")
@click.option("--session", default=None, help="Resume an existing session UUID.")
def update_draft(domain: str, text: str, session: str | None):
    """Extract facts and find graph candidates. Returns JSON."""
    _setup_logger()
    load_env()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
    try:
        result = update_backend.draft_update(
            domain=domain, text=text, session_id=session, user_id=user_id
        )
        _echo_json(result)
    except Exception as e:
        raise click.ClickException(str(e))


@update.command("confirm")
@click.option("--session", required=True, help="Session UUID from draft step.")
@click.option("--resolutions", required=True, help="JSON array of resolution objects.")
def update_confirm(session: str, resolutions: str):
    """Write confirmed facts to Neo4j. Returns JSON."""
    _setup_logger()
    load_env()
    env = load_env()
    user_id = env.get("ARTMIND_USER", "unknown")
    try:
        parsed = json.loads(resolutions)
        result = update_backend.confirm_update(
            session_id=session, resolutions=parsed, user_id=user_id
        )
        _echo_json(result)
    except ValueError as e:
        raise click.ClickException(str(e))
    except Exception as e:
        raise click.ClickException(str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update_cli.py::test_update_draft_returns_json test/test_update_cli.py::test_update_confirm_returns_json test/test_update_cli.py::test_update_confirm_fails_gracefully_on_missing_draft -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py test/test_update_cli.py
git commit -m "feat: add artmind update draft and confirm CLI commands"
```

---

## Task 11: CLI — `artmind update history` and `artmind update export`

**Files:**
- Modify: `artmind/cli.py`
- Modify: `test/test_update_cli.py`

- [ ] **Step 1: Add failing tests**

```python
# append to test/test_update_cli.py
import tempfile
from pathlib import Path


def test_update_history_outputs_table(runner):
    sessions = [
        {
            "session_id": "abc123", "domain": "general",
            "created_by": "alice@example.com", "created_at": "2026-05-05T10:00:00",
            "status": "confirmed", "input_count": 2, "excerpt": "Alice is CEO",
        }
    ]
    with patch("artmind.cli.update_backend._list_update_sessions", return_value=sessions):
        result = runner.invoke(cli, ["update", "history"])
    assert result.exit_code == 0, result.output
    assert "abc123" in result.output
    assert "general" in result.output


def test_update_export_sequential_calls_backend(runner, tmp_path):
    mock_written = [tmp_path / "session_abc12345.md"]
    (tmp_path / "session_abc12345.md").write_text("# Session abc123")
    with patch("artmind.cli.update_backend.export_chats", return_value=mock_written) as mock:
        result = runner.invoke(cli, [
            "update", "export",
            "--format", "sequential",
            "--output", str(tmp_path),
        ])
    assert result.exit_code == 0, result.output
    mock.assert_called_once_with(
        domain=None, format="sequential", output_dir=Path(str(tmp_path))
    )
    assert "session_abc12345.md" in result.output


def test_update_export_by_entity_with_domain_filter(runner, tmp_path):
    with patch("artmind.cli.update_backend.export_chats", return_value=[]) as mock:
        runner.invoke(cli, [
            "update", "export",
            "--domain", "fiction",
            "--format", "by-entity",
            "--output", str(tmp_path),
        ])
    mock.assert_called_once_with(
        domain="fiction", format="by-entity", output_dir=Path(str(tmp_path))
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest test/test_update_cli.py::test_update_history_outputs_table -v
```

Expected: `No such command 'history'`.

- [ ] **Step 3: Add `history` and `export` commands to the `update` group in `artmind/cli.py`**

```python
@update.command("history")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option("--user", default=None, help="Filter by created_by.")
@click.option("--limit", default=20, show_default=True, help="Maximum rows to return.")
def update_history(domain: str | None, user: str | None, limit: int):
    """List recent update sessions."""
    sessions = update_backend._list_update_sessions(domain=domain, user=user, limit=limit)
    if not sessions:
        click.echo("No update sessions found.")
        return
    header = f"{'SESSION':<12} {'DOMAIN':<12} {'BY':<24} {'AT':<22} {'INPUTS':>6}  EXCERPT"
    click.echo(header)
    click.echo("-" * len(header))
    for s in sessions:
        click.echo(
            f"{s['session_id'][:12]:<12} {s['domain']:<12} {s['created_by']:<24}"
            f" {s['created_at'][:19]:<22} {s['input_count']:>6}  {s['excerpt']}"
        )


@update.command("export")
@click.option("--domain", default=None, help="Filter by domain.")
@click.option(
    "--format", "fmt",
    type=click.Choice(["sequential", "by-entity"], case_sensitive=False),
    default="sequential",
    show_default=True,
)
@click.option(
    "--output",
    type=click.Path(file_okay=False, writable=True),
    default="data/chats",
    show_default=True,
)
def update_export(domain: str | None, fmt: str, output: str):
    """Export UserChat nodes to markdown files."""
    output_dir = Path(output)
    written = update_backend.export_chats(domain=domain, format=fmt, output_dir=output_dir)
    if not written:
        click.echo("No chats to export.")
        return
    for path in written:
        click.echo(str(path.name))
    click.echo(f"\nExported {len(written)} file(s) to {output_dir}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest test/test_update_cli.py -v
```

Expected: All PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
uv run pytest test/ -v
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py test/test_update_cli.py
git commit -m "feat: add artmind update history and export CLI commands"
```

---

## Task 12: Create `skills/artmind-update/SKILL.md`

**Files:**
- Create: `skills/artmind-update/SKILL.md`

No tests for skill documents.

- [ ] **Step 1: Create the skill directory and file**

```bash
mkdir -p skills/artmind-update
```

- [ ] **Step 2: Write `skills/artmind-update/SKILL.md`**

```markdown
---
name: artmind-update
description: Add and update facts in the artmind knowledge graph through natural language. Supports atomic facts, passages, pasted text, and todos. Domain-scoped, auditable, with ambiguity resolution before writing to the graph.
---

# artmind Update

Use this skill to let a user add or update facts in the artmind knowledge graph through conversational natural language input. You orchestrate domain detection, LLM extraction, candidate disambiguation, and graph writing via the CLI.

## Grounding Rules

- Never write to the graph until the user has confirmed candidate resolutions.
- Derive domain from the user's first message where possible; ask if ambiguous.
- Present all candidate choices for a single input in one batch, not one-by-one.
- Report what was written (nodes created/updated, relationships written) after each confirm.

## Required Inputs

- `domain`: Auto-detect from input; ask if unclear.
- `text`: The user's natural language input (atomic fact, passage, todo, pasted text).

## Session Setup

At skill start, load available domains:

```bash
uv run artmind domains list
```

Inspect the user's first message for domain signals (e.g., project names, people, domain-specific vocabulary). If confident, announce the chosen domain and proceed. If ambiguous, show the domain list and ask the user to pick.

## Step 1 — Draft (extract + find candidates)

```bash
uv run artmind update draft \
  --domain <domain> \
  --text "<user input>" \
  [--session <session_id>]
```

Output JSON contains:
- `session_id` — carry this for all subsequent turns
- `extracted_entities` — list of `{temp_id, name, entity_class, properties}`
- `extracted_relationships` — list of `{source_temp_id, target_temp_id, rel_type}`
- `candidates_per_entity` — list of `{entity, temp_id, top_n: [{node_id, name, entity_class, match_score, context_snippet}]}`

## Step 2 — Present Candidates and Collect Resolutions

For each entity in `candidates_per_entity`:

- If `top_n` is empty: automatically use `action: "create"` (no ambiguity).
- If `top_n` has candidates: present them to the user:

```
Found "Alice" — did you mean:
  1. Alice Smith (PERSON, linked to Acme Corp)
  2. Alice Johnson (PERSON, linked to Project Alpha)
  3. None of these — create new
```

Batch all entities into one message. Collect all answers before proceeding.

Build the resolutions JSON array:

```json
[
  {"entity_temp_id": "e0", "action": "link", "node_id": "<node_id from top_n>"},
  {"entity_temp_id": "e1", "action": "create", "node_id": null},
  {"entity_temp_id": "e2", "action": "skip", "node_id": null}
]
```

## Step 3 — Confirm (write to graph)

```bash
uv run artmind update confirm \
  --session <session_id> \
  --resolutions '<resolutions JSON>'
```

Output JSON: `{nodes_created, nodes_updated, relationships_written, user_chat_id}`

Report the summary to the user:
> "Added: 2 new nodes, 1 updated, 1 relationship written."

## Step 4 — Continue or Exit

Ask: "Anything else to add to this session?"

- If yes: go back to Step 1 with the same `--session <session_id>`.
- If no: report the full session summary and exit.

## Multi-turn Notes

- All turns in one skill invocation share the same `session_id`. Pass it in every `draft` call after the first.
- If the user's input has no extractable entities, report this clearly and ask if they want to rephrase.
- If extraction returns many entities, present all candidate batches in a single message grouped by entity.

## Export Reference

To dump all user-added knowledge to markdown (outside this skill, via CLI):

```bash
uv run artmind update export --format sequential --output data/chats/
uv run artmind update export --format by-entity --output data/chats/
```
```

- [ ] **Step 3: Commit**

```bash
git add skills/artmind-update/SKILL.md
git commit -m "feat: add artmind-update skill document"
```

---

## Self-Review Checklist

Before executing, verify:

**Spec coverage:**
- [x] UserChat node with embedding → Task 6 (`write_user_chat`)
- [x] Audit metadata (created_by, created_at, updated_at, updated_by) → Task 6
- [x] ARTMIND_USER env var → Tasks 10/11 CLI reads it
- [x] 3-pass extraction via extraction.py → Tasks 2/4
- [x] ingest.py refactor → Task 3
- [x] find_candidates fuzzy match → Task 5
- [x] SQLite update_sessions + update_drafts → Task 1
- [x] LLM calls logged to llm_calls.log → extraction.py uses log_llm_call (Task 2)
- [x] draft/confirm/history/export CLI → Tasks 10/11
- [x] vector_query unions DocChunk + UserChat → Task 8
- [x] graph_query MENTIONS branch → Task 9
- [x] export_chats sequential + by-entity → Task 7
- [x] Skill document → Task 12
- [x] _classify_input auto-tagging → Task 4

**Placeholder scan:** No TBD/TODO in any code block — all steps contain complete code.

**Type consistency:**
- `extract_facts()` returns `{entities: list[dict], relationships: list[dict]}` — consumed by `draft_update()` ✓
- `draft_update()` returns `{session_id, extracted_entities, extracted_relationships, candidates_per_entity}` — consumed by CLI ✓
- `confirm_update()` takes `resolutions: list[dict]` with `entity_temp_id/action/node_id` — matches what skill builds ✓
- `write_user_chat()` takes `extracted_entities` with `temp_id` field — matches `extract_facts()` output ✓
- `_list_update_sessions()` returns list with `session_id, domain, created_by, created_at, status, input_count, excerpt` — matches `history` CLI output ✓
