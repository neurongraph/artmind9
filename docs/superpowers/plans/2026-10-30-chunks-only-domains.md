# Chunks-only domains (`graph_extraction: false`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A domain whose schema says `graph_extraction: false` ingests documents to `:DocChunk` + embeddings only (no LLM extraction), and queries over it go straight to `vector-text`.

**Architecture:** One reader, `schema_validate.graph_extraction_enabled`, plus `domain_graph_extraction(domain)` for call sites that have only a domain name. `extract_kg` branches once on it. The audit, `domains-overview` and `query plan` read the same helper. `table2graph` is untouched.

**Tech Stack:** Python 3.14, Click, PyYAML, pytest. Spec: `docs/superpowers/specs/2026-10-30-chunks-only-domains-design.md`.

**Test commands:** targeted files only (`uv run --group dev pytest test/<file> -v`); the full suite stalls on this Mac.

---

## File map

- Modify `artmind/schema_validate.py`: flag helpers, validation error, warnings.
- Modify `artmind/cli.py`: `domains validate` prints the new warnings.
- Modify `artmind/ingest.py`: `extract_kg` chunks-only branch.
- Modify `artmind/staging_audit.py`: skip chunks-only domains.
- Modify `artmind/graph_query.py`: `domains_overview` reports the flag and `chunks` store.
- Modify `artmind/query_plan/planner.py`: force the `search` route.
- Modify docs: `CLAUDE.md`, three skills under `artmind/skills/`.
- Create tests: `test/test_chunks_only_flag.py`, `test/test_chunks_only_extract.py`, `test/test_chunks_only_query.py`.

---

### Task 1: The flag helpers and validation

**Files:** modify `artmind/schema_validate.py`, `artmind/cli.py`; test `test/test_chunks_only_flag.py`.

- [ ] **Step 1: Write the failing tests** (`test/test_chunks_only_flag.py`)

```python
"""`graph_extraction: false` marks a chunks-only domain (spec 2026-10-30)."""
import artmind.schema_validate as sv

META = {"kinds": {"entity": {}}, "reserved_prefix": "_"}


def test_enabled_unless_literally_false():
    assert sv.graph_extraction_enabled({}) is True
    assert sv.graph_extraction_enabled(None) is True
    assert sv.graph_extraction_enabled({"graph_extraction": True}) is True
    assert sv.graph_extraction_enabled({"graph_extraction": False}) is False


def test_a_non_boolean_value_is_a_validation_error():
    errors = sv.validate_schema({"name": "n", "graph_extraction": "no"}, META)
    assert any("graph_extraction" in e and "true or false" in e for e in errors)
    assert sv.validate_schema({"name": "n", "graph_extraction": False}, META) == []


def test_ignored_keys_warn_only_when_off():
    schema = {"name": "n", "graph_extraction": False, "entity_types": {"A": {}}, "property_aliases": {"x": "y"}}
    warnings = sv.graph_extraction_warnings(schema, "n")
    assert len(warnings) == 2 and all("ignored" in w for w in warnings)
    assert sv.graph_extraction_warnings({"name": "n", "entity_types": {"A": {}}}, "n") == []


def test_domain_lookup_reads_that_domains_own_file_and_never_inherits(tmp_path, monkeypatch):
    (tmp_path / "docs_schema.yaml").write_text("name: docs\ngraph_extraction: false\n")
    (tmp_path / "docs.child_schema.yaml").write_text("name: docs.child\n")
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", tmp_path)
    assert sv.domain_graph_extraction("docs") is False
    assert sv.domain_graph_extraction("docs.child") is True      # not inherited
    assert sv.domain_graph_extraction("missing") is True         # no schema: default
    (tmp_path / "bad_schema.yaml").write_text("name: [unclosed")
    assert sv.domain_graph_extraction("bad") is True             # unreadable: default
```

- [ ] **Step 2: Run, expect FAIL** (`AttributeError: graph_extraction_enabled`)

Run: `uv run --group dev pytest test/test_chunks_only_flag.py -v`

- [ ] **Step 3: Implement** in `artmind/schema_validate.py`

Add before `validate_schema`:

```python
def graph_extraction_enabled(schema: dict | None) -> bool:
    """False only for a schema that says `graph_extraction: false`: its domain is
    chunks-only (DocChunks + embeddings, no entity extraction). The one reader
    of the key (spec 2026-10-30-chunks-only-domains-design.md)."""
    return (schema or {}).get("graph_extraction", True) is not False


def domain_graph_extraction(domain: str) -> bool:
    """`graph_extraction_enabled` for `domain`'s own schema file. A dotted child
    does not inherit its parent's flag; a missing or unreadable schema is a
    normal (graph) domain."""
    path = Path(DOMAIN_SCHEMAS_DIR) / f"{domain}_schema.yaml"
    try:
        schema = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return True
    return graph_extraction_enabled(schema if isinstance(schema, dict) else {})


def graph_extraction_warnings(schema: dict, schema_name: str = "") -> list[str]:
    """Keys a chunks-only schema declares that nothing will read."""
    if graph_extraction_enabled(schema):
        return []
    label = schema_name or schema.get("name", "<unnamed>")
    return [
        f"{label}: graph_extraction is false, so '{key}' is ignored (no entities are extracted)"
        for key in ("entity_types", "document_entity", "property_aliases") if schema.get(key)
    ]
```

In `validate_schema`, right after `label = ...` and before `entity_types = schema.get("entity_types")`:

```python
    if "graph_extraction" in schema and not isinstance(schema["graph_extraction"], bool):
        errors.append(f"{label}.graph_extraction: must be true or false, not {schema['graph_extraction']!r}")
```

In `warn_all`, replace `warnings = reserved_relationship_warnings(schema, name)` with
`warnings = reserved_relationship_warnings(schema, name) + graph_extraction_warnings(schema, name)`.

In `artmind/cli.py` `domains_validate`: import `graph_extraction_warnings` with the others, and change the single-domain line to
`warnings = {domain: reserved_relationship_warnings(data, domain) + graph_extraction_warnings(data, domain)}`.

- [ ] **Step 4: Run, expect PASS**; also `uv run --group dev pytest test/test_schema_validate.py -v`.
- [ ] **Step 5: Commit** `feat(schema): graph_extraction flag helpers, validation and warnings`

---

### Task 2: `extract_kg` skips every LLM step

**Files:** modify `artmind/ingest.py`; test `test/test_chunks_only_extract.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""extract_kg in a `graph_extraction: false` domain (spec 2026-10-30). Hermetic:
any LLM seam raises, so a call fails the test. Synthetic text only."""
import json

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted


@pytest.fixture()
def chunks_only(ingest_env, monkeypatch, tmp_path):
    vault, _ = ingest_env
    schemas = vault / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ndescription: Search only.\ngraph_extraction: false\n")
    monkeypatch.setattr(ing, "DOMAIN_SCHEMAS_DIR", schemas)
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")

    def forbidden(*a, **k):
        raise AssertionError("an LLM seam was called in a chunks-only domain")

    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    for name in ("_llm_extract", "_summarise_document", "canonicalize_document"):
        monkeypatch.setattr(ing, name, forbidden)
    import artmind.canonicalize as canon
    monkeypatch.setattr(canon, "retrieve_vocabulary", forbidden)
    monkeypatch.setattr(canon, "retrieve_property_vocabulary", forbidden)
    return vault


def _note(vault, text="A synthetic note about Alpha meeting Beta.\n" * 3):
    note = vault / "notes" / "a.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text, encoding="utf-8")
    return note


def test_chunks_and_embeddings_are_staged_with_no_extraction(chunks_only):
    result = ing.ingest_file(_note(chunks_only), "m", "notes")

    folder = ing.extract_kg(result, "notes", max_workers=1)

    assert folder is not None
    assert json.loads((folder / "observations.json").read_text()) == []
    assert json.loads((folder / "relationships.json").read_text()) == []
    chunks = json.loads((folder / "chunks.json").read_text())
    assert len(chunks) == 1 and "Alpha" in chunks[0]["text"]
    assert list(ing.read_embedding_sidecar(folder).values()) == [[0.0, 0.1]]
    status = ing._get_chunk_statuses(result["sha256"])[1]
    assert (status["entities_status"], status["properties_status"], status["relationships_status"]) == ("skipped",) * 3
    assert not (folder / ing.DOCUMENT_CONTEXT_FILE).exists()


def test_reextracting_a_previously_extracted_note_stages_empty_instead_of_refusing(chunks_only):
    note = _note(chunks_only)
    first = ing.ingest_file(note, "m", "notes")
    folder = stage_as_extracted(ing.KG_DIR, first, domain="notes")
    (folder / "observations.json").write_text(json.dumps([{"id": "o1", "key": "alpha", "name": "Alpha"}]))
    result = ing.ingest_file(note, "m", "notes", reextract=True)
    result["reextract"] = True

    assert ing.extract_kg(result, "notes", max_workers=1) == folder

    assert json.loads((folder / "observations.json").read_text()) == []
    assert "kg_error" not in result
```

- [ ] **Step 2: Run, expect FAIL** (`AssertionError: an LLM seam was called`)

Run: `uv run --group dev pytest test/test_chunks_only_extract.py -v`

- [ ] **Step 3: Implement** in `extract_kg` (`artmind/ingest.py`)

1. Import: add `graph_extraction_enabled` to the existing `from artmind.schema_validate import ...` if one exists at module level, else import inside the function next to `schema_parse_error`. After `schema = load_schema(...)`:
   `chunks_only = not graph_extraction_enabled(schema)`
2. Wrap the two vocabulary `try:` blocks in `if not chunks_only:` (`vocabulary` and `property_vocabulary` keep their `[]`/`{}` initial values).
3. Change `if context_settings.enabled:` (the metadata and summary block) to `if context_settings.enabled and not chunks_only:`.
4. In `_process_chunk`, immediately before `entities_status = status.get("entities_status", "pending")`:

```python
        if chunks_only:
            # No graph in this domain (spec 2026-10-30): chunk text and vector
            # only. The three steps are recorded `skipped`, which resume and
            # the failed-step guard already treat as done.
            data["entities"], data["properties"], data["relationships"] = [], [], []
            chunk_json.write_text(
                json.dumps(strip_embeddings(data), indent=2, ensure_ascii=False), encoding="utf-8",
            )
            for step in ("entities", "properties", "relationships"):
                _update_chunk_step(doc_sha256, seq, step, "skipped")
            return
```
5. Canonicalisation: change `if not names_supplied:` to
   `if chunks_only:\n        canonical_names = {}\n    elif not names_supplied:`.
6. Document-entity owners: `owners = [] if chunks_only else _document_entity.own_entities(...)` (keep the existing arguments).
7. Loss guard: `if stash is not None and not all_observations and not chunks_only:`.

- [ ] **Step 4: Run, expect PASS**; then the neighbours: `uv run --group dev pytest test/test_extraction_loss_guard.py test/test_ingest_reextract.py test/test_document_context_wiring.py test/test_ingest_extract_kg_retry.py -v`
- [ ] **Step 5: Commit** `feat(ingest): chunks-only extraction for graph_extraction: false domains`

---

### Task 3: Audit, overview, plan routing

**Files:** modify `artmind/staging_audit.py`, `artmind/graph_query.py`, `artmind/query_plan/planner.py`; test `test/test_chunks_only_query.py`.

- [ ] **Step 1: Write the failing tests**

```python
"""Query side of chunks-only domains (spec 2026-10-30)."""
import json
from datetime import date

import pytest

import artmind.schema_validate as sv
from artmind import graph_query
from artmind.query_plan import planner
from artmind.staging_audit import empty_extractions


@pytest.fixture()
def flagged(tmp_path, monkeypatch):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "notes_schema.yaml").write_text("name: notes\ngraph_extraction: false\n")
    monkeypatch.setattr(sv, "DOMAIN_SCHEMAS_DIR", schemas)


def test_audit_skips_chunks_only_domains(tmp_path, flagged):
    for domain in ("notes", "journal"):
        folder = tmp_path / "kg" / domain / "a"
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"path": f"{domain}.md", "_domain": domain}))
        (folder / "chunks.json").write_text(json.dumps([{"id": "c", "text": "x" * 300}]))
        (folder / "observations.json").write_text("[]")

    found = empty_extractions(tmp_path / "kg")

    assert [n["domain"] for n in found["notes"]] == ["journal"]


def test_overview_reports_the_flag_and_a_chunks_store(monkeypatch, flagged):
    monkeypatch.setattr(graph_query, "_run_read_query", lambda cypher, params: [
        {"domain": "notes", "parts": [{"k": "documents", "c": 4, "names": ["a.md"]}]},
        {"domain": "journal", "parts": [{"k": "documents", "c": 2, "names": ["b.md"]},
                                        {"k": "entities", "c": 9, "names": None}]},
    ])
    import artmind.structured.registry as registry
    monkeypatch.setattr(registry, "list_tables", lambda *a, **k: [])

    by_domain = {r["domain"]: r for r in graph_query.domains_overview()["rows"]}

    assert by_domain["notes"]["graph_extraction"] is False
    assert by_domain["notes"]["stores"] == ["chunks"]
    assert "graph_extraction" not in by_domain["journal"]
    assert by_domain["journal"]["stores"] == ["graph"]


def test_chunks_only_scope_helper():
    rows = [{"domain": "notes", "graph_extraction": False},
            {"domain": "notes.sub", "graph_extraction": False},
            {"domain": "journal"}]
    assert planner._chunks_only(["notes"], rows) is True
    assert planner._chunks_only(["notes", "journal"], rows) is False
    assert planner._chunks_only(["unknown"], rows) is False
    assert planner._chunks_only([], rows) is False
```

Add to `test/test_query_plan_planner.py` (it owns the `world` fixture):

```python
def test_a_chunks_only_domain_is_planned_as_search_with_a_warning(world):
    world["overview"][0]["graph_extraction"] = False
    plan = _plan("How many visits did Ada make to Lakeport in 2025?")
    assert plan["route"]["id"] == "search"
    assert plan["view_match"] is None
    assert any("vector-text" in s["cmd"] for s in plan["steps"] + plan["suggested_steps"])
    assert any("graph_extraction" in w for w in plan["warnings"])


def test_a_mixed_scope_keeps_normal_routing(world):
    world["overview"][0]["graph_extraction"] = False
    world["overview"].append({"domain": "work", "document_count": 1, "documents": ["w.md"], "top_classes": ["PERSON"]})
    plan = _plan("How many visits did Ada make to Lakeport in 2025?", domains=("journal", "work"))
    assert not any("graph_extraction" in w for w in plan["warnings"])
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run --group dev pytest test/test_chunks_only_query.py test/test_query_plan_planner.py -v`

- [ ] **Step 3: Implement**

`artmind/staging_audit.py`: `from artmind.schema_validate import domain_graph_extraction`; in the domain loop, after the `_in_family` check:
`if not domain_graph_extraction(domain_dir.name): continue  # empty is correct in a chunks-only domain`

`artmind/graph_query.py` `domains_overview`: replace the final `for entry in overview.values(): entry["stores"] = [...]` with the same list assigned to `stores`, followed by

```python
        if isinstance(entry.get("domain"), str):
            from artmind.schema_validate import domain_graph_extraction

            if not domain_graph_extraction(entry["domain"]):
                entry["graph_extraction"] = False
                stores = ["chunks" if s == "graph" else s for s in stores]
        entry["stores"] = stores
```

`artmind/query_plan/planner.py`:

```python
def _chunks_only(selected: list[str], overview: list[dict]) -> bool:
    """Every overview row the selection covers is a chunks-only domain."""
    rows = [r for r in overview
            if any(r.get("domain") == d or str(r.get("domain")).startswith(d + ".") for d in selected)]
    return bool(selected) and bool(rows) and all(r.get("graph_extraction") is False for r in rows)
```

In `plan()`, after `selected, source, ... = _choose_domains(...)`:

```python
    chunks_only = _chunks_only(selected, overview)
    if chunks_only:
        warnings.append(
            f"{', '.join(selected)} builds no graph (graph_extraction: false): planned as a search of the notes"
        )
```

Then: guard the view match with `if not chunks_only and view_answer is not None and ...`; guard the view stage with `if view_answer is not None and not chunks_only:`; and in the route branch use
`route_obj = next(r for r in all_routes if r.id == ("search" if chunks_only else route_answer.choice))` and
`route_id, route_conf = route_obj.id, (1.0 if chunks_only else route_answer.confidence)`.

- [ ] **Step 4: Run, expect PASS**; then `uv run --group dev pytest test/test_staging_audit.py test/test_graph_query.py test/test_query_plan_planner.py test/test_query_plan_cli.py -v`
- [ ] **Step 5: Commit** `feat(query): chunks-only domains in audit, overview and query plan`

---

### Task 4: Docs and skills

**Files:** `CLAUDE.md`, `artmind/skills/artmind-query/SKILL.md`, `artmind/skills/artmind-create-schema/SKILL.md`, `artmind/skills/artmind-ingestion-helper/SKILL.md`.

- [ ] **Step 1:** `CLAUDE.md`: add a repo-layout row: `graph_extraction: false` in a domain's schema makes it chunks-only. `extract_kg` makes no LLM call (vocabulary, summary, entities/properties/relationships, canonicalisation all skipped), stages chunks + embeddings with empty observations, bypasses the empty-result loss guard. A dotted child does not inherit it. `domains-overview` reports `graph_extraction: false` and store `chunks`; `query plan` plans such a domain as `search`; `ingest empty-extractions` skips it. `table2graph` does not read the flag.
- [ ] **Step 2:** `artmind-query/SKILL.md`: under "The first two commands", add: if `domains-overview` shows `graph_extraction: false` (store `chunks`) for a domain, skip `views list` and every graph, text2cypher and insights command for it; use `artmind query vector-text` only.
- [ ] **Step 3:** `artmind-create-schema/SKILL.md`: a short subsection before "Step 9": a vector-search-only domain needs `name`, `description` and `graph_extraction: false`, no `entity_types`; show the 3-line YAML; say a dotted child must set it itself.
- [ ] **Step 4:** `artmind-ingestion-helper/SKILL.md`: a short Situation K: how to tell a chunks-only domain (`domains-overview`), that `ingest sync` there makes no LLM calls, and that switching an existing domain to it (or back) needs `ingest sync --reextract` to retire (or rebuild) entities.
- [ ] **Step 5: Commit** `docs: chunks-only domains (graph_extraction: false)`

---

### Task 5: Verify

- [ ] Run: `uv run --group dev pytest test/test_chunks_only_flag.py test/test_chunks_only_extract.py test/test_chunks_only_query.py test/test_schema_validate.py test/test_staging_audit.py test/test_graph_query.py test/test_query_plan_planner.py test/test_extraction_loss_guard.py test/test_ingest_reextract.py -v`. Expect all pass.
- [ ] `uv run artmind domains validate` prints no new ERROR for shipped schemas.
- [ ] Not covered by unit tests, to be done by the operator against a live Neo4j with `ARTMIND_NO_PROXY=1` (spec "Verification beyond unit tests"): the commit of empty observations retiring prior entities, and `vector-text` over a real chunks-only domain.

## Spec coverage

Flag + validation: Task 1. Documents/`extract_kg`, switching, loss-guard bypass: Task 2 (retiring entities on commit relies on the existing `_retract_prior_version`; only the staged-empty half is unit-tested). Audit, overview, plan: Task 3. Skill/doc updates: Task 4. `table2graph` unchanged: no task, by design.
