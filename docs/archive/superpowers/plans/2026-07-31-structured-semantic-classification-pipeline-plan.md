# Structured Semantic Classification Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a table's three semantic properties (`grain`, `bridge_columns`, `column → entity_class` mappings) into three independent, resumable, per-step-tracked classification steps — replacing the deterministic difflib/live-KG mapping matcher with a schema-driven LLM call, mirroring the `kg_chunk_status` (entities/properties/relationships) pattern KG extraction already uses.

**Architecture:** Three status columns (`grain_status`/`bridge_status`/`mapping_status`, each `'pending'|'ok'|'failed'`) land on `tables` via an idempotent `ADD COLUMN` migration. A new `propose_mapping()` function in `artmind/structured/semantics.py` replaces `artmind/structured/mappings.py`'s difflib matcher, reading the domain schema (`schema_reference.parse_entities`) instead of live graph entities. A new orchestrator, `propose_table_semantics()`, becomes the single re-entry point every caller uses — CLI (`db propose --step/--redo`), the ingest pipeline's first-registration and new-column-on-refresh auto-runs, and two new admin-ui routes — exactly mirroring `extract_kg`'s role for KG extraction.

**Tech Stack:** Python 3.14 / Click / SQLite (registry) / DuckDB (structured store) / FastAPI (admin-ui) / vanilla JS (dashboard.js) / pytest (hermetic, `call_llm` stubbed).

**Source spec:** `docs/superpowers/specs/2026-07-31-structured-semantic-classification-pipeline-design.md` — §9 is a resolved-decisions log, don't re-litigate it. Two implementation-level design choices made while writing this plan (not spec deviations, just filling gaps the spec left to the implementer):

- **Grain/bridge stay one LLM call** (`propose_semantics`, unchanged in shape). `propose_table_semantics` gates whether grain is *persisted* purely by whether `"grain"` is in the requested/needed step set for this run — there's no way to ask the model just one of the two questions, so requesting `--step bridge` alone still asks about grain but never writes it.
- **The new-column-on-refresh trigger (§5) calls `propose_table_semantics(..., redo=True)` internally.** The normal skip-if-already-`'ok'` logic would otherwise suppress bridge/mapping entirely on a table whose prior run already succeeded — but a schema-drift event is exactly the case where re-attempting is correct despite the stale `'ok'` status.

---

## File-by-file summary

| File | Change |
|---|---|
| `artmind/db.py` | Add `grain_status`/`bridge_status`/`mapping_status` to the `tables` CREATE TABLE literal + idempotent `ADD COLUMN` migration. |
| `artmind/structured/registry.py` | Add `set_step_status()`; add the three status keys to `routing_surface()`'s per-table dict. |
| `artmind/structured/semantics.py` | Add `propose_mapping()` (new mapping-step function), `propose_table_semantics()` (orchestrator); extend `propose_semantics()` with `write_grain`/`only_columns` kwargs. |
| `artmind/structured/mappings.py` | **Deleted.** |
| `artmind/structured/pipeline.py` | Rewrite `_register_columns_and_mappings()` to call `propose_table_semantics()` instead of `propose_mappings`/`propose_semantics` directly; add the new-column-on-replace-refresh trigger. |
| `artmind/cli.py` | `db propose` gains `--step`/`--redo`, loses `--skipSemantics`. |
| `justfile` | Update `db-propose` recipe's usage comment. |
| `test/test_structured_mappings.py` | **Deleted** — edge cases re-homed into `test_structured_semantics.py`. |
| `test/test_structured_semantics.py` | New tests for `propose_mapping` and `propose_table_semantics`. |
| `test/test_structured_registry.py` | New tests for the migration + `set_step_status`. |
| `test/test_db_cli.py` | New tests for `db propose --step`/`--redo`. |
| `artmind/webui/dashboard_routes.py` | Fix missing `bridge_columns` in the schema endpoint; add propose + propose-all + progress routes. Tested in `test/test_webui_admin_api.py` (existing file, already covers this route family). |
| `artmind/webui/templates/dashboard.html` | Classify column header, classify-all button + domain markup. |
| `artmind/webui/static/dashboard.js` | Render grain/bridge/mapping pips per row; classify/redo button in row-expand; bulk classify-all with progress polling. |
| `artmind/skills/artmind-ingestion-helper/SKILL.md` | New "structured tables" situation + gotchas row. |
| `docs/CAPABILITIES.md` | Rows 5.4/5.5 wording + grounding notes; new 10.5-adjacent row for the admin-ui widget. **Last task — do not touch until everything above is built and verified.** |

---

### Task 1: Migration — `grain_status`/`bridge_status`/`mapping_status` columns

**Files:**
- Modify: `artmind/db.py:135-154` (CREATE TABLE literal), `artmind/db.py:278-290` (idempotent ADD COLUMN block)
- Modify: `artmind/structured/registry.py` (add `set_step_status`, extend `routing_surface`)
- Test: `test/test_structured_registry.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_structured_registry.py` (mirrors `test_init_db_adds_grain_to_pre_grain_schema` at line 440 and `test_routing_surface_shape_and_class_filter` at line 409):

```python
def test_init_db_adds_step_status_columns_to_pre_existing_schema(tmp_path, monkeypatch):
    """A registry seeded before this design shipped must gain the three status
    columns, defaulted to 'pending', without disturbing existing rows."""
    import sqlite3

    import artmind.db as db

    db_path = tmp_path / "reg.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    # Simulate a pre-existing DB that already has grain/grain_confirmed but not
    # the new status columns, by creating the schema then dropping them.
    db._init_db()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO datasources (name, type, path_or_dsn, created_at)"
        " VALUES ('default', 'duckdb', '/tmp/x', 'now')"
    )
    conn.execute(
        'INSERT INTO "tables" (datasource, table_name, domain, parquet_path,'
        " version, refresh_mode, grain, ingested_at)"
        " VALUES ('default', 'legacy_table', 'banking', '/tmp/l.parquet', 1,"
        " 'replace', 'instance', 'now')"
    )
    conn.commit()
    conn.close()

    # Re-run _init_db (idempotent) -- this is the real-world path: an existing
    # DB file, code upgraded, next command run re-triggers _init_db via _get_db.
    db._init_db()

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute('PRAGMA table_info("tables")')}
    assert {"grain_status", "bridge_status", "mapping_status"} <= cols
    row = conn.execute(
        'SELECT grain_status, bridge_status, mapping_status, table_name'
        ' FROM "tables" WHERE table_name = ?', ("legacy_table",)
    ).fetchone()
    conn.close()
    assert row == ("pending", "pending", "pending", "legacy_table")


def test_register_table_defaults_step_statuses_to_pending(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()

    registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )
    row = registry.get_table("customers", domain="banking")
    assert row["grain_status"] == "pending"
    assert row["bridge_status"] == "pending"
    assert row["mapping_status"] == "pending"


def test_set_step_status_validates_and_persists(tmp_path, monkeypatch):
    import pytest

    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    table_id = registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )

    registry.set_step_status(table_id, "mapping", "ok")
    assert registry.get_table_by_id(table_id)["mapping_status"] == "ok"

    with pytest.raises(ValueError, match="step must be one of"):
        registry.set_step_status(table_id, "not_a_step", "ok")
    with pytest.raises(ValueError, match="status must be one of"):
        registry.set_step_status(table_id, "grain", "not_a_status")


def test_routing_surface_includes_step_statuses(tmp_path, monkeypatch):
    import artmind.db as db
    from artmind.structured import registry

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    table_id = registry.register_table(
        "default", "customers", "banking", parquet_path="/tmp/c.parquet"
    )
    registry.set_step_status(table_id, "grain", "ok")

    surface = registry.routing_surface(["banking"])
    assert surface[0]["grain_status"] == "ok"
    assert surface[0]["bridge_status"] == "pending"
    assert surface[0]["mapping_status"] == "pending"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_registry.py -k "step_status" -v`
Expected: FAIL — `grain_status` column / `set_step_status` attribute doesn't exist yet.

- [ ] **Step 3: Add the columns to the CREATE TABLE literal**

Edit `artmind/db.py:148-149`, right after `grain_confirmed`:

```python
            grain                  TEXT NOT NULL DEFAULT 'instance',  -- 'instance' | 'lookup' | 'normative'
            grain_confirmed        INTEGER NOT NULL DEFAULT 0,        -- 0 = proposed/default, 1 = operator-confirmed
            grain_status           TEXT NOT NULL DEFAULT 'pending',   -- 'pending' | 'ok' | 'failed' -- did the LLM attempt+succeed since the last profile change
            bridge_status          TEXT NOT NULL DEFAULT 'pending',
            mapping_status         TEXT NOT NULL DEFAULT 'pending',
            ingested_at            TEXT NOT NULL,
```

- [ ] **Step 4: Add the idempotent ADD COLUMN migration**

Edit `artmind/db.py:278-290`, appending after the `grain_confirmed` block, still inside the same `existing` snapshot:

```python
    existing = {row[1] for row in cursor.execute('PRAGMA table_info("tables")')}
    if "grain" not in existing:
        cursor.execute(
            'ALTER TABLE "tables" ADD COLUMN grain TEXT NOT NULL DEFAULT \'instance\''
        )
    if "grain_confirmed" not in existing:
        cursor.execute(
            'ALTER TABLE "tables" ADD COLUMN grain_confirmed INTEGER NOT NULL DEFAULT 0'
        )
    # Same additive-with-default shape as grain/grain_confirmed above --
    # per-step run status (docs/superpowers/specs/2026-07-31-structured-
    # semantic-classification-pipeline-design.md §4), not a persisted error
    # message: a step's failure is only ever visible via loguru, matching the
    # rest of the codebase's best-effort hooks.
    for step in ("grain", "bridge", "mapping"):
        col = f"{step}_status"
        if col not in existing:
            cursor.execute(
                f'ALTER TABLE "tables" ADD COLUMN {col} TEXT NOT NULL DEFAULT \'pending\''
            )
```

- [ ] **Step 5: Add `set_step_status` and the status constants to registry.py**

Edit `artmind/structured/registry.py`, right after the `GRAINS` constant (line 24):

```python
GRAINS = ("instance", "lookup", "normative")

#: The three independently-resumable classification steps (see the design
#: doc's §2/§4 kg_chunk_status parallel) and the run-status vocabulary each
#: tracks on its own {step}_status column.
SEMANTIC_STEPS = ("grain", "bridge", "mapping")
STEP_STATUSES = ("pending", "ok", "failed")
```

Then add, right after `set_grain` (after line 319):

```python
def set_step_status(table_id: int, step: str, status: str) -> int:
    """Record whether ``step``'s LLM call was attempted and succeeded since the
    table's last profile change. Returns rows affected (0 = unknown table_id).

    No persisted error text, by design -- a step's failure is only ever
    visible via loguru, mirroring every other best-effort hook in this
    codebase (see ``registry.py``'s module docstring convention).
    """
    if step not in SEMANTIC_STEPS:
        raise ValueError(f"step must be one of {SEMANTIC_STEPS}, got {step!r}")
    if status not in STEP_STATUSES:
        raise ValueError(f"status must be one of {STEP_STATUSES}, got {status!r}")
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f'UPDATE "tables" SET {step}_status = ? WHERE id = ?', (status, table_id))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
```

(`step` is validated against the `SEMANTIC_STEPS` whitelist before it ever reaches the f-string, so this isn't a SQL-injection surface despite the interpolated column name.)

- [ ] **Step 6: Add the status keys to `routing_surface`**

Edit `artmind/structured/registry.py:244-250` (inside the `surface.append({...})` block in `routing_surface`):

```python
        surface.append({
            "table": table["table_name"],
            "domain": table["domain"],
            "grain": table.get("grain") or "instance",
            "grain_confirmed": bool(table.get("grain_confirmed")),
            "grain_status": table.get("grain_status", "pending"),
            "bridge_status": table.get("bridge_status", "pending"),
            "mapping_status": table.get("mapping_status", "pending"),
            "refresh_mode": table.get("refresh_mode"),
            "row_count": table.get("row_count"),
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_registry.py -k "step_status" -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Run the full registry + existing CLI suites to confirm no regression**

Run: `uv run --group dev pytest test/test_structured_registry.py test/test_db_cli.py -v`
Expected: PASS — in particular `test_db_bridge_returns_routing_surface` (test/test_db_cli.py:47) must still pass with the three new keys present.

- [ ] **Step 9: Commit**

```bash
git add artmind/db.py artmind/structured/registry.py test/test_structured_registry.py
git commit -m "feat(structured): add grain/bridge/mapping run-status columns to tables"
```

---

### Task 2: Mapping step — `propose_mapping()` in `semantics.py`

**Files:**
- Modify: `artmind/structured/semantics.py` (add `propose_mapping`, `_MAPPING_PROMPT`, `_class_lines`, `build_mapping_prompt`)
- Test: `test/test_structured_semantics.py`

This replaces `mappings.py`'s difflib/live-KG matcher with a schema-driven LLM call (spec §3): same confidence floor, same confirm-gate, but the prompt now carries `{class, description, types}` from `schema_reference.parse_entities(load_schema(domain)["entities_prompt"])` instead of live entity names from `graph_query.entity_listing()`.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_structured_semantics.py`, after the existing imports/helpers (after `_stub_llm`, line 55):

```python
_MAPPING_SCHEMA_ENTITIES_PROMPT = """Some preamble text a real schema file would have here.

ENTITY TYPES YOU MUST EXTRACT:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRODUCT
  A banking product a customer holds, such as a savings account or credit card.
  example type values: savings_account | credit_card

BRANCH
  A physical bank branch location.
  example type values: branch
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTRACTION RULES:
Some trailing rules text a real schema file would have here.
"""


def _stub_schema(monkeypatch, entities_prompt=_MAPPING_SCHEMA_ENTITIES_PROMPT):
    """Stub the schema lookup at the point semantics.py imports it -- same
    lazy-import-patching approach as _stub_llm."""
    import artmind.temporal as temporal

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {"entity_types": ["PRODUCT", "BRANCH"], "entities_prompt": entities_prompt},
    )


def test_mapping_prompt_includes_schema_classes_and_columns(tmp_path, monkeypatch):
    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    seen = _stub_llm(monkeypatch, {"mappings": []})

    semantics.propose_mapping(table_id, "banking")

    prompt = seen["prompt"]
    assert "vulnerable_customers" in prompt
    assert "PRODUCT" in prompt and "savings account or credit card" in prompt
    assert "BRANCH" in prompt
    # No live-KG dependency: nothing about entity_listing/graph names in the prompt.
    assert "vulnerability_driver" in prompt  # still carries column samples


def test_mapping_persists_proposals_above_floor(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == [
        {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9}
    ]
    persisted = registry.list_mappings(table_id)
    assert len(persisted) == 1
    assert persisted[0]["column"] == "vulnerability_driver"
    assert persisted[0]["entity_class"] == "PRODUCT"
    assert persisted[0]["confirmed"] == 0


def test_mapping_below_floor_unmapped(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [{"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.1}],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == []
    assert registry.list_mappings(table_id) == []


def test_mapping_ignores_hallucinated_column_or_class(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "not_a_real_column", "entity_class": "PRODUCT", "confidence": 0.99},
            {"column": "vulnerability_driver", "entity_class": "NOT_A_REAL_CLASS", "confidence": 0.99},
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.7},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == [
        {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.7}
    ]


def test_mapping_does_not_overwrite_confirmed(tmp_path, monkeypatch):
    """Same guarantee propose_semantics gives bridge columns: a re-proposal must
    never silently un-confirm an operator's review."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.upsert_mapping(table_id, "vulnerability_driver", "PRODUCT", 1.0, confirmed=True)
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [{"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.3}],
    })

    proposals = semantics.propose_mapping(table_id, "banking")

    assert proposals == []
    row = registry.list_mappings(table_id)[0]
    assert row["confirmed"] == 1
    assert row["confidence"] == 1.0


def test_mapping_only_columns_restricts_persistence(tmp_path, monkeypatch):
    """The replace-refresh new-column trigger (Task 5) needs to classify only
    genuinely new columns, leaving an existing (already-classified-or-not)
    column's mapping state untouched even if the model proposes for it too."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "mappings": [
            {"column": "vulnerability_driver", "entity_class": "PRODUCT", "confidence": 0.9},
            {"column": "support_needed", "entity_class": "BRANCH", "confidence": 0.9},
        ],
    })

    proposals = semantics.propose_mapping(table_id, "banking", only_columns={"support_needed"})

    assert proposals == [{"column": "support_needed", "entity_class": "BRANCH", "confidence": 0.9}]
    persisted_columns = {m["column"] for m in registry.list_mappings(table_id)}
    assert persisted_columns == {"support_needed"}


def test_mapping_fails_clearly_when_domain_has_no_schema(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    import artmind.temporal as temporal

    monkeypatch.setattr(temporal, "load_schema", lambda domain: {})

    with pytest.raises(ValueError, match="no schema file"):
        semantics.propose_mapping(table_id, "banking")


def test_mapping_no_entity_classes_in_schema_returns_empty_not_error(tmp_path, monkeypatch):
    """A schema file that exists but whose entities_prompt has no parseable
    classes is a valid (if unusual) state -- distinct from no schema at all."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    import artmind.temporal as temporal

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {"entity_types": [], "entities_prompt": "no banner here at all"},
    )

    assert semantics.propose_mapping(table_id, "banking") == []
    assert registry.list_mappings(table_id) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_semantics.py -k mapping -v`
Expected: FAIL — `semantics.propose_mapping` doesn't exist yet.

- [ ] **Step 3: Implement `propose_mapping` in `semantics.py`**

Edit `artmind/structured/semantics.py`, appending after `propose_semantics` (after line 205):

```python
_MAPPING_PROMPT = """You are matching a structured table's columns to entity classes from a knowledge-graph domain schema.

For each column, judge whether its SAMPLED VALUES look like instances of one of the listed
entity classes below — based on the class's description, not by matching column/class names
literally. Judge the values the same way you would for bridge columns: a column full of a
bank's marketing names denotes the PRODUCT class if its values read like product names, even
if none of them has been seen in any ingested document yet.

A column can legitimately map to more than one class (e.g. a `category` column on a complaints
table might describe both a PRODUCT and an ISSUE_TYPE). It is fine — and expected — for a
column with no plausible class (an id, a date, a raw numeric measure) to be omitted entirely.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TABLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

name: {table_name}
domain: {domain}
row_count: {row_count}
refresh_mode: {refresh_mode}

columns:
{columns}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CANDIDATE ENTITY CLASSES (from the domain schema)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{classes}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return only this JSON object. No preamble, no explanation, no markdown fences.

{{
  "mappings": [
    {{"column": string, "entity_class": string, "confidence": number between 0 and 1}}
  ]
}}
"""


def _class_lines(classes: list[dict]) -> str:
    lines = []
    for c in classes:
        types = ", ".join(c.get("types") or [])
        suffix = f" (e.g. {types})" if types else ""
        lines.append(f"  - {c['class']}: {c['description']}{suffix}")
    return "\n".join(lines) if lines else "  (no entity classes found in schema)"


def build_mapping_prompt(table: dict, table_id: int, classes: list[dict]) -> str:
    return _MAPPING_PROMPT.format(
        table_name=table["table_name"],
        domain=table["domain"],
        row_count=table.get("row_count"),
        refresh_mode=table.get("refresh_mode"),
        columns=_column_lines(table_id),
        classes=_class_lines(classes),
    )


def propose_mapping(
    table_id: int, domain: str, model: str | None = None, *, only_columns: set[str] | None = None
) -> list[dict]:
    """Propose ``column -> entity_class`` mappings for ``table_id`` from
    ``domain``'s schema (no live-KG dependency — see design doc §3). Persists
    via the same confidence floor and never-overwrite-confirmed guarantee
    ``propose_semantics`` gives bridge columns.

    Raises ``ValueError`` if ``domain`` has no schema file (or no
    ``entities_prompt``) — a distinct, clearly-reported failure from "schema
    exists but has zero parseable classes," which returns ``[]`` instead.

    ``only_columns``, when given, additionally restricts *persistence* to that
    column-name set — used by the replace-mode-refresh new-column trigger
    (design doc §5) so an existing, unrelated column's mapping state is never
    touched just because the whole table was re-profiled.
    """
    from artmind.extraction import call_llm, parse_json_response
    from artmind.schema_reference import parse_entities
    from artmind.temporal import load_schema
    from utils.functions import load_env, resolve_llm_model

    table = registry.get_table_by_id(table_id)
    if table is None:
        raise ValueError(f"no registered table with id {table_id}")

    schema = load_schema(domain)
    entities_prompt = schema.get("entities_prompt")
    if not entities_prompt:
        raise ValueError(
            f"domain '{domain}' has no schema file (or no entities_prompt) — the mapping"
            " step needs the domain's entity schema to judge column classes against. Check"
            " domains/schemas/, or run 'artmind domains harmonize' if this is a dotted"
            " sub-domain."
        )
    classes = parse_entities(entities_prompt)
    if not classes:
        return []

    resolved_model = resolve_llm_model(load_env(), model)
    raw = call_llm(resolved_model, build_mapping_prompt(table, table_id, classes))
    parsed = parse_json_response(raw) or {}

    known_columns = {c["name"] for c in registry.get_columns(table_id)}
    known_classes = {c["class"] for c in classes}
    already_confirmed = {
        (m["column"], m["entity_class"])
        for m in registry.list_mappings(table_id)
        if m.get("confirmed")
    }

    persisted = []
    for entry in parsed.get("mappings") or []:
        if not isinstance(entry, dict):
            continue
        column = entry.get("column")
        entity_class = entry.get("entity_class")
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        # Hallucination guard, mirrors propose_semantics's bridge_columns check.
        if column not in known_columns or entity_class not in known_classes:
            continue
        if only_columns is not None and column not in only_columns:
            continue
        if confidence < CONFIDENCE_FLOOR:
            continue
        if (column, entity_class) in already_confirmed:
            continue
        registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=False)
        persisted.append({"column": column, "entity_class": entity_class, "confidence": confidence})

    return persisted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_semantics.py -k mapping -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/structured/semantics.py test/test_structured_semantics.py
git commit -m "feat(structured): add schema-driven propose_mapping, replacing the difflib matcher's LLM gap"
```

---

### Task 3: Orchestrator — `propose_table_semantics()`

**Files:**
- Modify: `artmind/structured/semantics.py` (extend `propose_semantics`, add `propose_table_semantics`)
- Test: `test/test_structured_semantics.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_structured_semantics.py`:

```python
def test_propose_semantics_write_grain_false_suppresses_persistence(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table(grain="instance")
    _stub_llm(monkeypatch, {"grain": "lookup", "bridge_columns": []})

    result = semantics.propose_semantics(table_id, write_grain=False)

    assert result["grain_written"] is False
    assert result["grain"] == "instance"  # unchanged -- not asked to act on it this run
    assert registry.get_table_by_id(table_id)["grain"] == "instance"


def test_propose_semantics_only_columns_restricts_bridge_persistence(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_llm(monkeypatch, {
        "grain": "instance",
        "bridge_columns": [
            {"column": "vulnerability_driver", "confidence": 0.9},
            {"column": "support_needed", "confidence": 0.9},
        ],
    })

    semantics.propose_semantics(table_id, only_columns={"support_needed"})

    assert [r["column"] for r in registry.list_column_roles(table_id)] == ["support_needed"]


def test_propose_table_semantics_runs_all_three_steps_by_default(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    _stub_schema(monkeypatch)
    _stub_llm(monkeypatch, {
        "grain": "instance", "grain_reason": "records", "bridge_columns": [],
        "mappings": [],
    })

    result = semantics.propose_table_semantics(table_id, "banking")

    row = registry.get_table_by_id(table_id)
    assert row["grain_status"] == "ok"
    assert row["bridge_status"] == "ok"
    assert row["mapping_status"] == "ok"
    assert result["grain_status"] == "ok"
    assert result["mapping_status"] == "ok"


def test_propose_table_semantics_resumes_only_failed_step(tmp_path, monkeypatch):
    """Mirrors kg_chunk_status's resumability: a relationships-step failure
    doesn't force re-running an already-ok entities step."""
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.set_step_status(table_id, "grain", "ok")
    registry.set_step_status(table_id, "bridge", "ok")
    registry.set_step_status(table_id, "mapping", "failed")

    calls = {"semantics": 0, "mapping": 0}
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: calls.__setitem__("semantics", calls["semantics"] + 1) or {"grain": "instance", "bridge_columns": []},
    )
    monkeypatch.setattr(
        semantics, "propose_mapping",
        lambda *a, **k: calls.__setitem__("mapping", calls["mapping"] + 1) or [],
    )

    semantics.propose_table_semantics(table_id, "banking")

    assert calls == {"semantics": 0, "mapping": 1}
    assert registry.get_table_by_id(table_id)["mapping_status"] == "ok"


def test_propose_table_semantics_step_flag_targets_one_step(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()

    calls = {"semantics": 0, "mapping": 0}
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: calls.__setitem__("semantics", calls["semantics"] + 1) or {"grain": "instance", "bridge_columns": []},
    )
    monkeypatch.setattr(
        semantics, "propose_mapping",
        lambda *a, **k: calls.__setitem__("mapping", calls["mapping"] + 1) or [],
    )

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"])

    assert calls == {"semantics": 0, "mapping": 1}
    assert registry.get_table_by_id(table_id)["grain_status"] == "pending"


def test_propose_table_semantics_redo_reruns_ok_step(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()
    registry.set_step_status(table_id, "mapping", "ok")

    calls = []
    monkeypatch.setattr(semantics, "propose_mapping", lambda *a, **k: calls.append(1) or [])
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: {"grain": "instance", "bridge_columns": []},
    )

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"])
    assert calls == []  # already ok, no redo -> skipped

    semantics.propose_table_semantics(table_id, "banking", steps=["mapping"], redo=True)
    assert calls == [1]


def test_propose_table_semantics_records_failed_step_without_raising(tmp_path, monkeypatch):
    from artmind.structured import registry, semantics

    _patch_db(tmp_path, monkeypatch)
    table_id = _seed_table()

    def _boom(*a, **k):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(semantics, "propose_mapping", _boom)
    monkeypatch.setattr(
        semantics, "propose_semantics",
        lambda *a, **k: {"grain": "instance", "bridge_columns": []},
    )

    result = semantics.propose_table_semantics(table_id, "banking")  # must not raise

    assert result["mapping_status"] == "failed"
    assert "mapping_error" in result
    assert registry.get_table_by_id(table_id)["mapping_status"] == "failed"
    # grain/bridge succeeded independently of mapping's failure.
    assert registry.get_table_by_id(table_id)["grain_status"] == "ok"


def test_propose_table_semantics_unknown_table_raises(tmp_path, monkeypatch):
    import pytest

    from artmind.structured import semantics

    _patch_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="no registered table"):
        semantics.propose_table_semantics(99999, "banking")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_semantics.py -k "propose_table_semantics or write_grain or only_columns_restricts_bridge" -v`
Expected: FAIL — `propose_table_semantics` doesn't exist yet; `propose_semantics` doesn't accept `write_grain`/`only_columns` yet.

- [ ] **Step 3: Extend `propose_semantics` with `write_grain`/`only_columns`**

Edit `artmind/structured/semantics.py:145-205`, changing the signature and the grain/bridge-persistence logic:

```python
def propose_semantics(
    table_id: int,
    model: str | None = None,
    *,
    write_grain: bool = True,
    only_columns: set[str] | None = None,
) -> dict:
    """Propose ``grain`` and bridge columns for ``table_id``. Persists both,
    subject to ``write_grain``/``only_columns`` below.

    Returns ``{"grain", "grain_reason", "grain_written", "bridge_columns"}``.
    ``grain_written`` is False when an operator has already confirmed a grain,
    in which case the proposal is reported but not applied.

    ``write_grain=False`` computes but never persists grain — used by
    ``propose_table_semantics`` when only the bridge step was requested: grain
    and bridge share one LLM call (there's no way to ask the model just one of
    the two questions), but the caller can still refuse to act on the grain
    half of the answer. ``only_columns``, when given, restricts bridge-column
    *persistence* to that column-name set (design doc §5's new-column-only
    refresh trigger).
    """
    from artmind.extraction import call_llm, parse_json_response
    from utils.functions import load_env, resolve_llm_model

    table = registry.get_table_by_id(table_id)
    if table is None:
        raise ValueError(f"no registered table with id {table_id}")

    resolved_model = resolve_llm_model(load_env(), model)
    raw = call_llm(resolved_model, build_prompt(table, table_id))
    parsed = parse_json_response(raw) or {}

    grain = parsed.get("grain")
    grain_reason = parsed.get("grain_reason") or ""
    grain_written = False
    if write_grain:
        if grain in registry.GRAINS:
            if table.get("grain_confirmed"):
                # An operator already ruled on this; report but do not overwrite.
                grain = table["grain"]
            else:
                registry.set_grain(table_id, grain, confirmed=False)
                grain_written = True
        else:
            grain = table["grain"]
    else:
        # Not asked to act on grain this run -- report the existing value,
        # same shape as the already-confirmed branch above.
        grain = table["grain"]

    known_columns = {c["name"] for c in registry.get_columns(table_id)}
    already_confirmed = {
        role["column"] for role in registry.list_column_roles(table_id) if role.get("confirmed")
    }

    persisted = []
    for entry in parsed.get("bridge_columns") or []:
        if not isinstance(entry, dict):
            continue
        column = entry.get("column")
        try:
            confidence = float(entry.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        # Guard against a hallucinated column name reaching the registry.
        if column not in known_columns:
            continue
        if only_columns is not None and column not in only_columns:
            continue
        if confidence < CONFIDENCE_FLOOR:
            continue
        if column in already_confirmed:
            continue
        registry.upsert_column_role(table_id, column, BRIDGE_TERM, confidence, confirmed=False)
        persisted.append({"column": column, "bridge_role": BRIDGE_TERM, "confidence": confidence})

    return {
        "grain": grain,
        "grain_reason": grain_reason,
        "grain_written": grain_written,
        "bridge_columns": persisted,
    }
```

- [ ] **Step 4: Add `propose_table_semantics`**

Append to `artmind/structured/semantics.py`, after `propose_mapping` (from Task 2):

```python
def propose_table_semantics(
    table_id: int,
    domain: str,
    *,
    steps: list[str] | None = None,
    redo: bool = False,
    model: str | None = None,
    only_columns: set[str] | None = None,
) -> dict:
    """Run whichever of {grain, bridge, mapping} ``steps`` need attention for
    ``table_id``, recording per-step run status, and return a combined result.

    This is the one function every caller — ``db propose``, the ingest
    pipeline's first-registration/new-column auto-run, the admin-ui's
    propose/propose-all routes — goes through, mirroring ``extract_kg``'s role
    as the single re-entry point ``ingest sync`` also calls inline
    (docs/CAPABILITIES.md 3.1).

    ``steps`` defaults to all three. A step already ``'ok'`` is skipped unless
    ``redo``. Grain and bridge share one LLM call (``propose_semantics``);
    requesting only one of the two still makes that call, but grain is only
    *persisted* when ``"grain"`` is actually in the step set this run needs.

    Failures are caught per step, logged via loguru, and recorded as
    ``'failed'`` — never raised — mirroring ``kg_chunk_status``'s best-effort
    convention: the whole point of tracking run status is so a partial
    failure resumes on the next call rather than crashing the caller.

    ``only_columns``, forwarded to both underlying steps, restricts
    *persistence* to that column-name set (design doc §5's new-column-only
    refresh trigger) — it does not affect which steps are considered.
    """
    from loguru import logger

    table = registry.get_table_by_id(table_id)
    if table is None:
        raise ValueError(f"no registered table with id {table_id}")

    requested = set(steps) if steps else set(registry.SEMANTIC_STEPS)
    to_run = {
        step for step in requested
        if redo or table.get(f"{step}_status", "pending") != "ok"
    }

    result = {"table": table["table_name"], "domain": domain}

    if to_run & {"grain", "bridge"}:
        run_grain = "grain" in to_run
        run_bridge = "bridge" in to_run
        try:
            semantics_result = propose_semantics(
                table_id, model=model, write_grain=run_grain, only_columns=only_columns
            )
            result["semantics"] = semantics_result
            if run_grain:
                registry.set_step_status(table_id, "grain", "ok")
            if run_bridge:
                registry.set_step_status(table_id, "bridge", "ok")
        except Exception as exc:
            logger.warning(
                "propose_table_semantics: grain/bridge step failed for {}: {}",
                table["table_name"], exc,
            )
            result["semantics_error"] = str(exc)
            if run_grain:
                registry.set_step_status(table_id, "grain", "failed")
            if run_bridge:
                registry.set_step_status(table_id, "bridge", "failed")

    if "mapping" in to_run:
        try:
            result["mappings"] = propose_mapping(
                table_id, domain, model=model, only_columns=only_columns
            )
            registry.set_step_status(table_id, "mapping", "ok")
        except Exception as exc:
            logger.warning(
                "propose_table_semantics: mapping step failed for {}: {}",
                table["table_name"], exc,
            )
            result["mapping_error"] = str(exc)
            registry.set_step_status(table_id, "mapping", "failed")

    fresh = registry.get_table_by_id(table_id)
    result["grain_status"] = fresh["grain_status"]
    result["bridge_status"] = fresh["bridge_status"]
    result["mapping_status"] = fresh["mapping_status"]
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_semantics.py -v`
Expected: PASS — full file (existing grain/bridge tests + new mapping + orchestrator tests).

- [ ] **Step 6: Commit**

```bash
git add artmind/structured/semantics.py test/test_structured_semantics.py
git commit -m "feat(structured): add propose_table_semantics orchestrator with per-step run status"
```

---

### Task 4: CLI — `db propose --step`/`--redo`, retire `--skipSemantics`

**Files:**
- Modify: `artmind/cli.py:1461-1494`
- Modify: `justfile:245`
- Test: `test/test_db_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `test/test_db_cli.py`, after `test_db_grain_shows_and_confirms` (line 96):

```python
def test_db_propose_runs_all_steps_by_default(ingested, monkeypatch):
    import artmind.cli as cli
    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append((table_id, domain, kw)) or {
            "table": "products", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    result = CliRunner().invoke(cli.cli, ["db", "propose", "products", "--domain", "banking"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    _, domain, kwargs = calls[0]
    assert domain == "banking"
    assert kwargs["steps"] is None
    assert kwargs["redo"] is False
    payload = json.loads(result.output)
    assert payload["mapping_status"] == "ok"


def test_db_propose_step_flag_repeatable(ingested, monkeypatch):
    import artmind.cli as cli
    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(kw) or {
            "table": "products", "domain": domain,
            "grain_status": "pending", "bridge_status": "pending", "mapping_status": "ok",
        },
    )

    result = CliRunner().invoke(
        cli.cli,
        ["db", "propose", "products", "--domain", "banking", "--step", "mapping", "--redo"],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["steps"] == ["mapping"]
    assert calls[0]["redo"] is True


def test_db_propose_skip_semantics_flag_removed(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli, ["db", "propose", "products", "--domain", "banking", "--skipSemantics"]
    )
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


def test_db_propose_rejects_unknown_step(ingested):
    import artmind.cli as cli

    result = CliRunner().invoke(
        cli.cli, ["db", "propose", "products", "--domain", "banking", "--step", "nonsense"]
    )
    assert result.exit_code != 0
```

`json`/`CliRunner` are already imported at the top of `test/test_db_cli.py` (used by the other tests in that file); `ingested` is the fixture defined at line 22.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_db_cli.py -k db_propose -v`
Expected: FAIL — `--step`/`--redo` options don't exist; `--skipSemantics` still accepted.

- [ ] **Step 3: Rewrite `db_propose`**

Edit `artmind/cli.py:1461-1494`:

```python
@db.command("propose")
@click.argument("table")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope table resolution (repeatable; comma-splittable).")
@click.option("--step", "steps", multiple=True, type=click.Choice(["grain", "bridge", "mapping"]), help="Restrict to specific step(s) (repeatable). Default: all three, each skipped if already 'ok' unless --redo.")
@click.option("--redo", is_flag=True, help="Re-run a step even though its status is already 'ok'.")
@click.option("--model", default=None, help="Override the LLM model used for grain/bridge/mapping proposal.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_propose(table, domain, steps, redo, model, compact):
    """Re-run classification for TABLE: grain, bridge columns, and column-to-entityClass mappings.

    Each step tracks its own run status (grain_status/bridge_status/mapping_status)
    on the table row. By default only steps not already 'ok' are attempted, so a
    partial failure at ingest time resumes here automatically — the same role
    `extract-kg` plays for a partially-failed document. `--redo` forces a step to
    run again even though it already succeeded (e.g. after editing the domain
    schema). Confirmed values (grain_confirmed, column_roles/column_mappings
    .confirmed) are never overwritten by a re-proposal.
    """
    from artmind.structured.semantics import propose_table_semantics

    row = _resolve_table_row(table, domain)
    result = propose_table_semantics(
        row["id"], row["domain"], steps=list(steps) or None, redo=redo, model=model
    )
    _echo_json(result, compact)
```

- [ ] **Step 4: Update the justfile comment**

Edit `justfile:245`:

```
# re-run structured classification (grain, bridge columns, mappings) for a table  (usage: just db-propose <table> ["--step mapping --redo"])
db-propose table flags="":
    uv run artmind db propose {{ table }} {{ flags }}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_db_cli.py -v`
Expected: PASS — full file.

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py justfile test/test_db_cli.py
git commit -m "feat(cli): db propose gains --step/--redo, retires --skipSemantics"
```

---

### Task 5: Pipeline wiring — first-registration + new-column-on-refresh auto-run

**Files:**
- Modify: `artmind/structured/pipeline.py:168-221` (`_register_columns_and_mappings`)
- Test: `test/test_structured_semantics.py` (rewrite `test_pipeline_proposes_semantics_only_on_first_registration`), `test/test_structured_refresh_cli.py`

- [ ] **Step 1: Update the existing first-registration test**

The current `test_pipeline_proposes_semantics_only_on_first_registration` (`test/test_structured_semantics.py:186-228`) monkeypatches `semantics.propose_semantics` directly and asserts it's called once on first registration, never again on a same-column refresh. Since the pipeline will now call `propose_table_semantics` (not `propose_semantics`/`propose_mappings` directly), replace this test:

```python
def test_pipeline_proposes_all_three_steps_only_on_first_registration(tmp_path, monkeypatch):
    """First registration auto-runs grain+bridge+mapping once; a same-column
    replace refresh must not re-propose anything (mapping used to run
    unconditionally on every write before this design — that's the regression
    this guards against)."""
    import csv

    import artmind.db as db
    import paths
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()

    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append((table_id, domain, kw)) or {
            "table": "widgets", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    csv_path = tmp_path / "widgets.csv"

    def write_rows(rows):
        with open(csv_path, "w", newline="") as f:
            csv.writer(f).writerows([["id", "name"], *rows])

    write_rows([[1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(csv_path, "banking")
    assert len(calls) == 1, "first registration should classify"
    assert set(calls[0][2]["steps"]) == {"grain", "bridge", "mapping"}

    # Same columns, different content -- must not re-propose anything (no new
    # columns, replace-mode, version > 1).
    write_rows([[1, "Widget"], [2, "Gadget"], [3, "Doohickey"]])
    result = ingest_structured_file(csv_path, "banking")

    from artmind.structured import registry

    assert result["status"] == "ok", result
    assert registry.get_table("widgets", domain="banking")["version"] == 2
    assert len(calls) == 1, "a same-column refresh must not re-classify"


def test_pipeline_replace_refresh_new_column_triggers_bridge_and_mapping_for_new_column_only(
    tmp_path, monkeypatch
):
    """A replace-mode refresh that adds a genuinely new column proposes
    bridge/mapping for that column only -- grain stays untouched, and existing
    columns' mapping/bridge state is untouched too (design doc §5)."""
    import csv

    import artmind.db as db
    import paths
    from artmind.structured.pipeline import ingest_structured_file

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()

    import artmind.structured.semantics as semantics

    calls = []
    monkeypatch.setattr(
        semantics, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(kw) or {
            "table": "widgets", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    csv_path = tmp_path / "widgets.csv"
    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows([["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")
    assert len(calls) == 1  # first-registration run

    with open(csv_path, "w", newline="") as f:
        csv.writer(f).writerows([["id", "name", "category"], [1, "Widget", "Tools"]])
    ingest_structured_file(csv_path, "banking", force=True)

    assert len(calls) == 2
    new_column_call = calls[1]
    assert set(new_column_call["steps"]) == {"bridge", "mapping"}
    assert new_column_call["only_columns"] == {"category"}
    assert new_column_call["redo"] is True
```

Delete the old `test_pipeline_proposes_semantics_only_on_first_registration` function body (it's superseded by the two tests above).

- [ ] **Step 2: Run to verify failure**

Run: `uv run --group dev pytest test/test_structured_semantics.py -k "pipeline" -v`
Expected: FAIL — pipeline still calls `propose_semantics`/`propose_mappings` directly, not `propose_table_semantics`.

- [ ] **Step 3: Rewrite `_register_columns_and_mappings`**

Edit `artmind/structured/pipeline.py:168-221`:

```python
def _register_columns_and_mappings(
    ds: DuckDBDatasource, table_id: int, table_name: str, domain: str, *, exclude_system_cols: bool
) -> None:
    """Introspect + profile ``table_name``'s current schema, persist it to the
    registry's ``columns`` table, and drive classification through
    ``propose_table_semantics`` (the same function ``db propose`` and the
    admin-ui call). Shared by the initial-ingest (``_write_table``) and
    temporal-refresh (``_refresh_temporal_table``) paths so this bookkeeping
    can't drift between them.

    Classification cadence (design doc §5):
    - First registration (``version == 1``): all three steps run once.
    - A ``replace``-mode refresh whose column set grew: bridge + mapping run
      again, but scoped to the *new* columns only (``only_columns``) and
      forced past their already-'ok' status (``redo=True``) — an existing
      column's classification state is untouched. Grain stays untouched too:
      what a table means doesn't change because a column arrived.
    - Anything else (same-column refresh, or a temporal-mode batch — which
      never reaches this branch with a changed column set at all, since
      ``_validate_temporal_incoming_columns`` hard-errors on drift before this
      function runs): no classification call at all.

    ``exclude_system_cols`` must be true whenever ``table_name`` carries the
    SCD-2 system columns (``_valid_from``/``_valid_to``/``_is_current``) --
    they're internal bookkeeping, not real data columns and shouldn't be
    profiled at all. Any *real* data column can still have a DATE/DECIMAL/etc
    ``distinct_sample`` -- ``default=str`` below handles those.
    """
    old_columns = {c["name"] for c in registry.get_columns(table_id)}

    profiles = ds.profile_columns(table_name)
    columns = [
        {
            "name": c.name,
            "dtype": c.dtype,
            "profile_json": json.dumps(dataclasses.asdict(profiles[c.name]), default=str)
            if c.name in profiles
            else None,
        }
        for c in ds.introspect_schema(table_name)
        if not (exclude_system_cols and c.name in SYSTEM_COLUMNS)
    ]
    registry.replace_columns(table_id, columns)

    table = registry.get_table_by_id(table_id)
    if table is None:
        return

    from artmind.structured.semantics import propose_table_semantics

    if table.get("version") == 1:
        try:
            propose_table_semantics(table_id, domain, steps=["grain", "bridge", "mapping"])
        except Exception as e:
            # propose_table_semantics already catches+logs per-step; this is a
            # defensive backstop, mirroring commit_to_graph's hook guarding in
            # artmind/ingest.py -- an unreachable model must not fail the load.
            logger.warning(
                "structured pipeline: first-registration classification failed for {}: {}",
                table_name, e,
            )
    elif table.get("refresh_mode") == "replace":
        new_columns = {c["name"] for c in columns}
        added_columns = new_columns - old_columns
        if added_columns:
            try:
                propose_table_semantics(
                    table_id, domain, steps=["bridge", "mapping"],
                    only_columns=added_columns, redo=True,
                )
            except Exception as e:
                logger.warning(
                    "structured pipeline: new-column classification failed for {}: {}",
                    table_name, e,
                )
```

Remove the now-unused `from artmind.structured.mappings import propose_mappings` and `from artmind.structured.semantics import propose_semantics` inline imports that were in the old version — they're superseded by the single `propose_table_semantics` import above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_semantics.py test/test_structured_refresh_cli.py -v`
Expected: PASS — full files. In particular confirm `test_db_refresh_temporal_schema_drift_raises_clean_error` (`test/test_structured_refresh_cli.py:291`) still passes unchanged — temporal drift must still hard-error before `_register_columns_and_mappings` is ever reached.

- [ ] **Step 5: Run the full structured test suite as a regression check**

Run: `uv run --group dev pytest test/test_structured_*.py test/test_db_cli.py test/test_db_mappings_cli.py -v`
Expected: PASS (except `test_structured_mappings.py`, which Task 6 retires next — some of its tests may now fail since `mappings.py` is still present but no longer wired into the pipeline; that's expected and resolved in Task 6).

- [ ] **Step 6: Commit**

```bash
git add artmind/structured/pipeline.py test/test_structured_semantics.py
git commit -m "feat(structured): wire propose_table_semantics into first-registration and new-column refresh"
```

---

### Task 6: Retire `mappings.py` and its test file

**Files:**
- Delete: `artmind/structured/mappings.py`
- Delete: `test/test_structured_mappings.py`
- Modify: `test/test_structured_semantics.py` (confirm re-homed coverage from Task 2 already supersedes it — no new code needed here, this task is verification + deletion)

Per spec §7: the deterministic matcher is a full replacement, not an "old path" to keep testing. Task 2 already re-homed the edge cases (confidence floor → `test_mapping_below_floor_unmapped`; never-overwrite-confirmed → `test_mapping_does_not_overwrite_confirmed`; multiple-classes-per-column is inherently exercised by `test_mapping_persists_proposals_above_floor`'s shape). `test_propose_mappings_fuzzy_match` and `test_propose_mappings_skips_entity_listing_when_no_categorical_columns` have no equivalent — difflib fuzzy matching and the categorical-column pre-filter are both retired mechanisms (schema-driven mapping doesn't pre-filter by column `kind`, since the LLM judges values directly), so there is nothing to re-home for those two.

- [ ] **Step 1: Confirm no other code imports `mappings.py`**

Run:
```bash
grep -rn "structured.mappings\|structured import mappings\|from artmind.structured.mappings" --include="*.py" .
```
Expected: only `artmind/structured/pipeline.py` (already removed in Task 5) and `artmind/cli.py` (already removed in Task 4) show up in git history, not in the current tree. If anything else appears, stop and investigate before deleting.

- [ ] **Step 2: Delete the files**

```bash
git rm artmind/structured/mappings.py test/test_structured_mappings.py
```

- [ ] **Step 3: Run the full suite**

Run: `uv run --group dev pytest test/ -v`
Expected: PASS. No collection errors from the deleted files (confirms nothing else imports them), and `test_structured_semantics.py`'s Task 2/3 tests cover the retired file's edge cases.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(structured): retire the deterministic mappings.py matcher

Fully replaced by semantics.propose_mapping (schema-driven LLM call, no live-KG
dependency) — edge-case coverage re-homed into test_structured_semantics.py."
```

---

### Task 7: Admin-ui backend — bridge_columns fix, propose + propose-all routes

**Files:**
- Modify: `artmind/webui/dashboard_routes.py:515-522` (schema endpoint), add new routes after it
- Test: `test/test_webui_admin_api.py` — the existing Lane B route-test file already covers `/api/structured/tables/...` (see `test_structured_table_schema_found` at line 805, `test_structured_tables_lists_all` at line 780); add the new tests alongside those, using the file's own `_client()` helper (line 12: `TestClient(create_app(admin_routes=True))`) and its established style of `monkeypatch.setattr(dashboard_routes.<name>, ...)` — never a real registry DB.

- [ ] **Step 1: Write the failing tests**

Add to `test/test_webui_admin_api.py`, directly after `test_structured_table_schema_404` (line 823-825):

```python
def test_structured_schema_endpoint_includes_bridge_columns(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "get_table",
        lambda table, domain=None: {"id": 1, "table_name": "products", "domain": "banking"},
    )
    monkeypatch.setattr(dashboard_routes.structured_registry, "get_columns", lambda table_id: [])
    monkeypatch.setattr(dashboard_routes.structured_registry, "list_mappings", lambda table_id: [])
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "list_column_roles",
        lambda table_id: [{"column": "vulnerability_driver", "bridge_role": "term", "confirmed": 0, "confidence": 0.9}],
    )
    response = _client().get("/api/structured/tables/products/schema?domain=banking")
    assert response.status_code == 200
    body = response.json()
    assert "bridgeColumns" in body
    assert body["bridgeColumns"][0]["column"] == "vulnerability_driver"


def test_structured_propose_endpoint_calls_shared_function(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "get_table",
        lambda table, domain=None: {"id": 1, "table_name": "products", "domain": "banking"},
    )
    calls = []
    monkeypatch.setattr(
        dashboard_routes, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append((table_id, domain, kw)) or {
            "table": "products", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    response = _client().post(
        "/api/structured/tables/products/propose",
        json={"domain": "banking", "steps": ["mapping"], "redo": True},
    )
    assert response.status_code == 200, response.text
    assert calls == [(1, "banking", {"steps": ["mapping"], "redo": True, "model": None})]
    assert response.json()["mappingStatus"] == "ok"


def test_structured_propose_endpoint_404s_unknown_table(monkeypatch):
    monkeypatch.setattr(dashboard_routes.structured_registry, "get_table", lambda table, domain=None: None)
    response = _client().post(
        "/api/structured/tables/nope/propose", json={"domain": "banking"}
    )
    assert response.status_code == 404


def test_structured_propose_all_endpoint_runs_unclassified_tables(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "list_tables",
        lambda domains: [{
            "id": 1, "table_name": "products", "domain": "banking",
            "grain_status": "pending", "bridge_status": "pending", "mapping_status": "pending",
        }],
    )
    calls = []
    monkeypatch.setattr(
        dashboard_routes, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(table_id) or {
            "table": "products", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    response = _client().post("/api/structured/propose-all", json={"domain": "banking"})
    assert response.status_code == 200, response.text
    assert calls == [1]
    assert response.json()["results"][0]["table"] == "products"


def test_structured_propose_all_skips_already_ok_tables_unless_redo(monkeypatch):
    monkeypatch.setattr(
        dashboard_routes.structured_registry, "list_tables",
        lambda domains: [{
            "id": 1, "table_name": "fully_classified", "domain": "banking",
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        }],
    )
    calls = []
    monkeypatch.setattr(
        dashboard_routes, "propose_table_semantics",
        lambda table_id, domain, **kw: calls.append(table_id) or {
            "table": "fully_classified", "domain": domain,
            "grain_status": "ok", "bridge_status": "ok", "mapping_status": "ok",
        },
    )

    response = _client().post("/api/structured/propose-all", json={"domain": "banking"})
    assert response.status_code == 200, response.text
    assert calls == []
    assert response.json()["results"] == []


def test_structured_propose_all_progress_endpoint_defaults_to_zero():
    response = _client().get("/api/structured/propose-all/progress?domain=banking")
    assert response.status_code == 200
    assert response.json() == {"done": 0, "total": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -k "propose or bridge_columns" -v`
Expected: FAIL — routes don't exist yet, `bridgeColumns` missing from schema response.

- [ ] **Step 3: Fix the schema endpoint**

Edit `artmind/webui/dashboard_routes.py:515-522`:

```python
    @app.get("/api/structured/tables/{table}/schema")
    async def api_structured_table_schema(table: str, domain: str | None = None):
        row = structured_registry.get_table(table, domain=domain)
        if row is None:
            raise HTTPException(status_code=404, detail=f"table '{table}' not found")
        columns = structured_registry.get_columns(row["id"])
        mappings = structured_registry.list_mappings(row["id"])
        bridge_columns = structured_registry.list_column_roles(row["id"])
        return _camelize({**row, "columns": columns, "mappings": mappings, "bridge_columns": bridge_columns})
```

- [ ] **Step 4: Add the propose + propose-all + progress routes**

Add the import at the top of `artmind/webui/dashboard_routes.py`, alongside the other `artmind.structured` imports (near line 42-44):

```python
from artmind.structured.pipeline import ingest_structured_file
from artmind.structured.semantics import propose_table_semantics
```

Add the request models near the other `BaseModel` definitions in this file (search for where `ResumeExtractRequest`/`IngestRequest` are defined and place these alongside them):

```python
class StructuredProposeRequest(BaseModel):
    domain: str
    steps: list[str] = Field(default_factory=lambda: ["grain", "bridge", "mapping"])
    redo: bool = False
    model: str | None = None


class StructuredProposeAllRequest(BaseModel):
    domain: str
    redo: bool = False
    model: str | None = None
```

Add the routes right after the schema endpoint from Step 3:

```python
    _bulk_classify_progress: dict[str, dict] = {}

    @app.post("/api/structured/tables/{table}/propose")
    async def api_structured_propose(table: str, payload: StructuredProposeRequest):
        _validate_artifact_segment(table)
        _validate_artifact_segment(payload.domain)
        row = structured_registry.get_table(table, domain=payload.domain)
        if row is None:
            raise HTTPException(status_code=404, detail=f"table '{table}' not found")
        try:
            result = await asyncio.to_thread(
                propose_table_semantics, row["id"], payload.domain,
                steps=payload.steps, redo=payload.redo, model=payload.model,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _camelize(result)

    @app.post("/api/structured/propose-all")
    async def api_structured_propose_all(payload: StructuredProposeAllRequest):
        _validate_artifact_segment(payload.domain)
        tables = structured_registry.list_tables([payload.domain])
        if not payload.redo:
            tables = [
                t for t in tables
                if t.get("grain_status") != "ok" or t.get("bridge_status") != "ok"
                or t.get("mapping_status") != "ok"
            ]
        _bulk_classify_progress[payload.domain] = {"done": 0, "total": len(tables)}
        results = []
        try:
            for t in tables:
                try:
                    r = await asyncio.to_thread(
                        propose_table_semantics, t["id"], payload.domain,
                        redo=payload.redo, model=payload.model,
                    )
                    results.append({"table": t["table_name"], **r})
                except Exception as exc:
                    results.append({"table": t["table_name"], "error": str(exc)})
                _bulk_classify_progress[payload.domain]["done"] += 1
        finally:
            _bulk_classify_progress.pop(payload.domain, None)
        return _camelize({"domain": payload.domain, "results": results})

    @app.get("/api/structured/propose-all/progress")
    async def api_structured_propose_all_progress(domain: str):
        return _camelize(_bulk_classify_progress.get(domain, {"done": 0, "total": 0}))
```

Note the spec's sketch (§12.2/§12.3) called `propose_table_semantics(table, domain, ...)` with the table *name* as the first arg; the real function (Task 3) takes `table_id`, matching every other admin-ui route in this file (`api_resume_extract` etc. all resolve to an id/row first) — `row["id"]` / `t["id"]` above is the correction, not a deviation from intent.

Also note the `try/finally` around the propose-all loop (not in the spec's sketch, which pops progress unconditionally after the loop): if `propose_table_semantics` itself raises unexpectedly for one table's *ValueError* case (e.g., a genuinely malformed request), the original sketch's un-guarded `_bulk_classify_progress.pop(...)` after the loop would never run, leaving a stale progress entry forever. The `try/finally` closes that leak; every per-table failure is already caught inside the loop via the `except Exception` there, so this only matters for a failure between iterations (e.g. `list_tables` raising) — a narrow but real robustness gap worth closing while writing this code, not a spec deviation.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -v`
Expected: PASS — full file (existing structured/jobs/snapshot tests + the new ones from Step 1).

- [ ] **Step 6: Run the full webui test suite for regressions**

Run: `uv run --group dev pytest test/test_webui_admin_api.py test/test_webui_app.py test/test_webui_benchmark_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add artmind/webui/dashboard_routes.py test/test_webui_admin_api.py
git commit -m "feat(admin-ui): expose bridge_columns and add classify/classify-all routes"
```

---

### Task 8: Admin-ui frontend — Classify UI

**Files:**
- Modify: `artmind/webui/templates/dashboard.html:350-366` (Tables panel)
- Modify: `artmind/webui/static/dashboard.js:588-675` (`refreshStructuredTables`)

No automated test for this task (vanilla-JS DOM wiring, no existing JS test harness in this repo) — verify manually per Step 4 below using the browser preview tools.

- [ ] **Step 1: Add markup — Classify column header + classify-all button**

Edit `artmind/webui/templates/dashboard.html:350-366`:

```html
        <section class="dash-panel" id="structured-tables-panel">
          <h2>Tables</h2>
          <div class="dash-form stacked">
            <label>
              Domain
              <select id="structured-tables-domain"></select>
            </label>
            <button type="button" class="btn-secondary" id="structured-classify-all-btn">Classify all unclassified</button>
            <label>
              <input type="checkbox" id="structured-classify-all-redo"> Redo already-classified tables too
            </label>
            <span id="structured-classify-all-progress" class="dash-note"></span>
          </div>
          <div class="table-scroll">
            <table class="dash-table" id="structured-tables-table">
              <thead>
                <tr><th>Name</th><th>Domain</th><th>Rows</th><th>Version</th><th>Ingested</th><th>Classify</th></tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </section>
```

- [ ] **Step 2: Render pips per row + classification block in row-expand**

Edit `artmind/webui/static/dashboard.js`, inside `refreshStructuredTables` (lines 620-674), adding a sixth `<td>` for the pips and a classification block above the existing columns sub-table in the row-expand handler:

```javascript
  for (const t of tables) {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.appendChild(el("td", null, t.tableName));
    tr.appendChild(el("td", null, t.domain));
    tr.appendChild(el("td", null, fmtRowCount(t.rowCount)));
    tr.appendChild(el("td", null, String(t.version)));
    tr.appendChild(el("td", null, fmtTime(t.ingestedAt)));

    const classifyTd = el("td");
    classifyTd.appendChild(pip(t.grainStatus));
    classifyTd.appendChild(pip(t.bridgeStatus));
    classifyTd.appendChild(pip(t.mappingStatus));
    tr.appendChild(classifyTd);

    const detailTr = document.createElement("tr");
    const detailTd = el("td");
    detailTd.colSpan = 6;
    detailTd.style.display = "none";
    detailTr.appendChild(detailTd);

    tr.addEventListener("click", async () => {
      const showing = detailTd.style.display !== "none";
      if (showing) {
        detailTd.style.display = "none";
        return;
      }
      detailTd.style.display = "";
      detailTd.innerHTML = "Loading…";
      try {
        const schema = await api(
          `/api/structured/tables/${encodeURIComponent(t.tableName)}/schema?domain=${encodeURIComponent(t.domain)}`
        );
        detailTd.innerHTML = "";

        const classifyBlock = el("div", "tool-card");
        // grain_reason is never persisted (only returned transiently from the
        // propose call itself, matching §4's "no persisted error/reasoning
        // text" convention) -- only the confirmed grain value survives on the
        // row, so that's all this view can show.
        classifyBlock.appendChild(el("div", "tool-head", `Grain: ${schema.grain} (${schema.grainConfirmed ? "confirmed" : "proposed"})`));
        const bridgeNote = (schema.bridgeColumns || []).length
          ? `Bridge columns: ${schema.bridgeColumns.map((b) => `${b.column} (${b.confirmed ? "confirmed" : "proposed"})`).join(", ")}`
          : "Bridge columns: none";
        classifyBlock.appendChild(el("div", "dash-note", bridgeNote));

        const stepChecks = el("div", "dash-form");
        const stepBoxes = {};
        for (const step of ["grain", "bridge", "mapping"]) {
          const label = el("label");
          const cb = document.createElement("input");
          cb.type = "checkbox";
          cb.checked = true;
          stepBoxes[step] = cb;
          label.appendChild(cb);
          label.appendChild(document.createTextNode(` ${step}`));
          stepChecks.appendChild(label);
        }
        const redoLabel = el("label");
        const redoCb = document.createElement("input");
        redoCb.type = "checkbox";
        redoLabel.appendChild(redoCb);
        redoLabel.appendChild(document.createTextNode(" redo"));
        stepChecks.appendChild(redoLabel);
        classifyBlock.appendChild(stepChecks);

        const classifyBtn = el("button", "btn-link", "Classify");
        classifyBtn.addEventListener("click", async (event) => {
          event.stopPropagation();
          const steps = Object.entries(stepBoxes).filter(([, cb]) => cb.checked).map(([s]) => s);
          classifyBtn.disabled = true;
          classifyBtn.textContent = "Classifying…";
          try {
            await api(`/api/structured/tables/${encodeURIComponent(t.tableName)}/propose`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ domain: t.domain, steps, redo: redoCb.checked }),
            });
            detailTd.style.display = "none";
            await refreshStructuredTables();
          } catch (err) {
            alert(`Classify failed: ${err.message}`);
            classifyBtn.disabled = false;
            classifyBtn.textContent = "Classify";
          }
        });
        classifyBlock.appendChild(classifyBtn);
        detailTd.appendChild(classifyBlock);

        const colTable = el("table", "dash-table");
        const thead = document.createElement("thead");
        thead.innerHTML = "<tr><th>Column</th><th>Type</th><th>Mapping</th></tr>";
        colTable.appendChild(thead);
        const colBody = document.createElement("tbody");
        const mappingByColumn = {};
        for (const m of schema.mappings || []) {
          mappingByColumn[m.column] = `${m.entityClass} (${m.confirmed ? "confirmed" : "proposed"})`;
        }
        for (const c of schema.columns || []) {
          const ctr = document.createElement("tr");
          ctr.appendChild(el("td", null, c.name));
          ctr.appendChild(el("td", null, c.dtype));
          ctr.appendChild(el("td", "dash-note", mappingByColumn[c.name] || "—"));
          colBody.appendChild(ctr);
        }
        colTable.appendChild(colBody);
        detailTd.appendChild(colTable);
      } catch (err) {
        detailTd.innerHTML = "";
        detailTd.appendChild(el("div", "dash-note", `Failed to load schema: ${err.message}`));
      }
    });

    tbody.appendChild(tr);
    tbody.appendChild(detailTr);
  }
```

Also bump the empty-state `td.colSpan` a few lines above (line 614) from `5` to `6` to match the new column count:

```javascript
    const td = el("td", "dash-empty", "No structured tables registered yet.");
    td.colSpan = 6;
```

- [ ] **Step 3: Wire the "Classify all unclassified" button**

Add after the `refreshStructuredTables` function definition in `dashboard.js` (after the closing brace at the line following Step 2's edits):

```javascript
document.getElementById("structured-classify-all-btn").addEventListener("click", async () => {
  const domain = structuredTablesDomainEl.value;
  if (!domain) {
    alert("Select a domain first.");
    return;
  }
  const redo = document.getElementById("structured-classify-all-redo").checked;
  const btn = document.getElementById("structured-classify-all-btn");
  const progressEl = document.getElementById("structured-classify-all-progress");
  btn.disabled = true;
  let polling = true;
  const poll = async () => {
    while (polling) {
      try {
        const p = await api(`/api/structured/propose-all/progress?domain=${encodeURIComponent(domain)}`);
        if (p.total) progressEl.textContent = `Classifying ${p.done}/${p.total}…`;
      } catch (err) {
        // best-effort progress readout only
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  };
  poll();
  try {
    await api("/api/structured/propose-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, redo }),
    });
    progressEl.textContent = "Done.";
  } catch (err) {
    alert(`Classify all failed: ${err.message}`);
  } finally {
    polling = false;
    btn.disabled = false;
    await refreshStructuredTables();
  }
});
```

- [ ] **Step 4: Verify manually in the browser**

Start the admin UI (check `justfile` for the recipe, e.g. `just admin-ui` or `uv run artmind admin-ui`) and use the preview tools:

```bash
uv run artmind admin-ui
```

Then, with the browser preview tools: navigate to the dashboard, open the "Structured data" tab, ingest or select a domain with at least one table, confirm:
- The Tables table shows a 6th "Classify" column with three pips per row.
- Clicking a row expands the classification block (grain + reason, bridge columns, step checkboxes, Classify button) above the existing columns sub-table.
- Clicking "Classify" disables the button, shows "Classifying…", and re-renders the row with updated pips on success.
- "Classify all unclassified" shows a polling "Classifying N/M…" label and refreshes the table on completion.

Take a screenshot of the expanded row and the classify-all flow to confirm visually before moving on.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/templates/dashboard.html artmind/webui/static/dashboard.js
git commit -m "feat(admin-ui): add per-table classify/redo and bulk classify-all to Structured data tab"
```

---

### Task 9: `artmind-ingestion-helper` skill — structured-tables section

**Files:**
- Modify: `artmind/skills/artmind-ingestion-helper/SKILL.md` (edit the source — **not** `.claude/skills/...`, which is a symlink)

- [ ] **Step 1: Add a new situation letter and a gotchas row**

Edit `artmind/skills/artmind-ingestion-helper/SKILL.md`, inserting a new `### I.` section right before the `## Full Pipeline Reference` header (currently line 244):

```markdown
### I. A structured table's classification is stuck, or you want to reclassify it

Structured tables (CSV/XLSX ingested via `artmind ingest sync FILE --domain DOMAIN` when
the file is tabular) go through three independent classification steps after they're
registered: `grain` (what the rows denote), `bridge` (which columns' values are worth
searching the graph for), and `mapping` (which columns denote instances of which graph
entity class). Each tracks its own status — `grain_status`/`bridge_status`/`mapping_status`,
one of `pending`/`ok`/`failed` — visible via:

```bash
artmind db schema TABLE --domain DOMAIN
```

**A step shows `failed`, or never got attempted (`pending`) on an old table:**
```bash
artmind db propose TABLE --domain DOMAIN
```
By default this re-attempts every step whose status isn't `ok` yet — exactly the same
"resume only what's broken" shape as `extract-kg` for a document's chunks (Situation D.1).

**You only want to re-run one specific step** (e.g. just re-check mappings after editing
the domain schema):
```bash
artmind db propose TABLE --domain DOMAIN --step mapping
```

**A step already succeeded but you want a second opinion anyway** (schema edited, or the
first proposal looked wrong):
```bash
artmind db propose TABLE --domain DOMAIN --step mapping --redo
```

**The `mapping` step specifically fails with "no schema file" or "no entities_prompt":**
The domain has no schema YAML under `domains/schemas/`, or it's a dotted sub-domain whose
parent hasn't been harmonized yet. Point the user at `/artmind-create-schema` to create one,
or `artmind domains harmonize` if the schema exists at a parent domain.

**Table stuck at `grain_status`/`bridge_status`='pending' forever:** first registration
proposes all three steps automatically — if it never got attempted, either the table
predates this pipeline (ingested before this feature shipped — every pre-existing table
starts at `'pending'` for all three, since the migration can't retroactively know whether a
step already effectively happened) or an unreachable LLM silently failed at ingest time
(the ingest hook is best-effort and only logs a warning, never fails the load). Either way,
`db propose TABLE --domain DOMAIN` is the fix.
```

Also add a row to the `## Common Gotchas` table (line 269-280):

```markdown
| A structured table's `mapping_status`/`bridge_status`/`grain_status` shows `failed`, or a table ingested long ago is stuck at `pending` | Best-effort LLM call failed at ingest time (unreachable model), or the table predates this classification pipeline | `artmind db propose TABLE --domain DOMAIN` — resumes only the steps not already `ok`. See Situation I. |
```

- [ ] **Step 2: Regenerate the CLI guide's routing check**

Run: `uv run --group dev pytest test/test_cli_guide.py -v`
Expected: PASS unchanged — this task doesn't add/remove any CLI command, only edits a skill file, so `test_cli_guide.py`'s "every command is routed" check is unaffected. Running it here is a sanity check that nothing in Task 4's CLI edit was accidentally left unrouted.

- [ ] **Step 3: Push the skill edit to the run folder**

```bash
artmind init
```

This re-seeds `~/.artmind/.claude/skills/artmind-ingestion-helper/` from the source in `artmind/skills/` (per CLAUDE.md's "skills reach the chat UI only through the run folder" — editing the checkout alone doesn't reach the chat UI agent).

- [ ] **Step 4: Commit**

```bash
git add artmind/skills/artmind-ingestion-helper/SKILL.md
git commit -m "docs(skill): teach artmind-ingestion-helper about structured-table classification status"
```

---

### Task 10: `docs/CAPABILITIES.md` — rows 5.4/5.5 + new admin-ui row

**Do not start this task until Tasks 1-9 are built and verified** (per CLAUDE.md's own convention: the doc should describe what's true, not what's planned).

**Files:**
- Modify: `docs/CAPABILITIES.md` rows 5.4/5.5 and their grounding notes (~line 671-672, ~line 730-739); possibly a new row near 10.5 for the admin-ui widget.

**Note:** `docs/CAPABILITIES.md` already has uncommitted changes in the working tree from before this plan started (grounding section 5's rows 5.1-5.3/5.6-5.10 with fresh ✓ marks and new grounding notes — visible via `git diff docs/CAPABILITIES.md` against `HEAD` before this branch existed). Those edits are unrelated prior work living in the working tree, not part of this feature — **do not discard or overwrite them**. Layer this task's 5.4/5.5 edits on top of the current working-tree content (re-read the file fresh at the start of this task rather than assuming the shape described in earlier tasks' exploration notes, since it may have moved further in the meantime).

- [ ] **Step 1: Re-read the current file state**

```bash
git diff docs/CAPABILITIES.md | head -5
```

Confirm whether the pre-existing uncommitted section-5 grounding work is still present, committed, or has moved. Read the live `docs/CAPABILITIES.md` around rows 5.1-5.10 and their grounding notes before editing, rather than trusting this plan's earlier exploration snapshot.

- [ ] **Step 2: Update row 5.4's checkmark and statement**

Change row 5.4 from (no ✓, "LLM-proposed" claimed but false) to ✓ with accurate wording, e.g.:

```
| 5.4 | ✓ | Semantic mappings | Columns are mapped to graph entity classes via a propose → confirm lifecycle (set / confirm / clear), with genuinely LLM-proposed candidates judged against the domain schema — no dependency on the domain already having extracted graph entities. | `artmind db mappings`, `db propose` (`semantics.py:propose_mapping`) |
```

- [ ] **Step 3: Update row 5.5's reference anchor**

If row 5.5 doesn't already mention `propose_table_semantics`/the per-step status columns, update its Reference anchor to include them, e.g.:

```
| 5.5 | ✓ | Table grain semantics | What a table's rows denote — instance, lookup, or normative — is proposed and confirmable, with per-step run-status tracking (grain/bridge/mapping) mirroring KG extraction's per-chunk steps. | `artmind db grain`, `db propose --step grain` (`semantics.py:propose_semantics`, `propose_table_semantics`) |
```

- [ ] **Step 4: Add a grounding note for 5.4**

Add a `**5.4 Semantic mappings**` subsection (there wasn't one before — 5.4 was the only ungrounded row in section 5) in the same *Why it matters* / *Test hint* shape as the existing 5.3/5.5/5.6 notes, describing: the schema-driven mechanism, the chicken-and-egg gap it closes (a domain with tables but no ingested documents can now still get mapping proposals), and the resumable per-step status model. Model it on the existing 5.5 grounding note's structure and length.

- [ ] **Step 5: Update 5.5's grounding note for the new step-status/resumability model**

Extend the existing 5.5 grounding note (whichever line it currently sits at) to mention: run-status tracking (`grain_status`/`bridge_status`/`mapping_status`), `db propose --step`/`--redo`, and the new-column-only auto-retrigger on a replace-mode refresh.

- [ ] **Step 6: Consider a 10.5-adjacent row for the admin-ui widget**

Read the current 10.5 row ("Admin console" statement) and its surrounding rows. If it doesn't yet enumerate structured-table classification visibility/actions, add a new row (10.5.x or the next free number in that section) describing the per-table classify/redo action and bulk classify-all, with reference anchor pointing at the new `/api/structured/tables/{table}/propose` and `/api/structured/propose-all` routes.

- [ ] **Step 7: Commit**

```bash
git add docs/CAPABILITIES.md
git commit -m "docs: ground CAPABILITIES.md 5.4/5.5 against the new schema-driven classification pipeline"
```

---

## Final verification (per CLAUDE.md's testing traps)

Before considering this done, per CLAUDE.md's "Testing implications" section — hermetic pytest passing does not prove the CLI works end-to-end:

- [ ] Run the full hermetic suite: `just dev-test` (expect all green, ~9s, no Neo4j/network).
- [ ] Stop any running daemon: `just dev-stop-daemons` (a stale `serve` daemon would otherwise mask every code change made in this plan).
- [ ] Re-install so the edit is live everywhere: `just dev-install` (stops daemons, `uv tool install --force --editable .`, `artmind init` — this also re-seeds the skill from Task 9 into the run folder).
- [ ] Against a real Neo4j, with `ARTMIND_NO_PROXY=1`: ingest a fresh structured file into a domain with a real schema, confirm all three `*_status` columns land `'ok'` via `artmind db schema TABLE --domain DOMAIN`; then `db propose TABLE --domain DOMAIN --step mapping --redo` and confirm it re-runs; then do a replace-mode refresh that adds a column and confirm only that column gets a bridge/mapping proposal.
- [ ] Confirm the admin-ui's Structured data tab renders correctly against this same real data (Task 8's manual verification, if not already done with a live backend).
