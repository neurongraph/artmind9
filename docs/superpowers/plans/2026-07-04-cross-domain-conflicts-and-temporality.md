# Cross-Domain Conflicts & Temporality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give artmind (1) cross-domain retrieval — repeatable `--domain`, a centralized domain predicate, a `domains-overview` router, and a skill that routes/adjudicates across sibling domains — plus non-destructive materialized conflict detection; and (2) a temporal layer — canonical bitemporal properties, `--asOf` filtering, per-document auto-normalization at ingest, and document supersession that resolves version-history false positives while preserving genuine cross-domain conflicts.

**Architecture:** Domain scoping stays a node/rel property in one Neo4j DB; cross-domain is a CLI/skill-layer change built on one new `domain_predicate()` builder that replaces ~30 inline predicates. Conflicts are entity-anchored, chunk-evidenced `Conflict` nodes created only via a two-phase dry-run/apply command whose candidate pairing blocks by `entity_class` then uses the existing `entity_embedding` ANN index (never a brute-force cross-product). Temporality adds additive canonical properties (`valid_from`/`valid_to`/`event_at`/`ingested_at`) written by a normalization stage that runs automatically per document after `write_to_graph()` and standalone for backfill; supersession sets `valid_to` and feeds a `superseded` adjudicator verdict so the conflict definition sharpens to "overlapping valid-time intervals where neither supersedes the other." `refine-graph` and `detect-conflicts` stay explicit-call-only.

**Tech Stack:** Python 3.11+, Click CLI, Neo4j (Cypher, APOC, vector + range indexes), Ollama LLM (`call_llm`/`parse_json_response`), PyYAML, pytest, `uv`.

---

## File Map

**Modified:**
- `artmind/graph_query.py` — new `normalize_domains()`, `domain_predicate()`, `asof_predicate()`; all predicates rewritten to `$domains`/`IN`; `execute_pattern`/metadata/listing accept domain lists + `as_of`; new `domains_overview()`, `list_conflicts()`, `list_timeline()`; `.domain`/temporal projections in patterns 2/3/4/10.
- `artmind/vector_query.py` — 6 predicates → `domain_predicate`; accept domain lists; `candidateK` scaled by domain count; `as_of` filter.
- `artmind/text2cypher.py` — domains list + prompt rule to `IN $domains`; Phase 2 adds Conflict schema; T1 adds `--asOf` rule; T2 adds SUPERSEDES.
- `artmind/cli.py` — `_parse_domains()`; 16 query `--domain` → `multiple=True`; `--asOf` on query commands; new `query domains-overview`, `query graph conflicts`, `query graph timeline`, `ingest detect-conflicts`, `ingest normalize-time`, `ingest supersede`; `refine-graph --allow-cross-domain-merge`.
- `artmind/setup.py` — `Conflict.id` constraint + `Conflict.status` index; temporal range indexes; setup summary.
- `artmind/refine_graph.py` — `allow_cross_domain_merge` guard + cross-domain cluster skip/report; write `RefineRun` marker on apply.
- `artmind/ingest.py` — auto-hook `normalize_ingested_document()` after `write_to_graph()` in `ingest_to_kg()`.
- `skills/artmind-query/SKILL.md` — protocol rewrite to Route → Discover → Resolve → Retrieve → Ground → Adjudicate.
- `domains/schemas/banking_policy_schema.yaml`, `banking_reference_schema.yaml`, `banking_sop_guides_schema.yaml`, `personal_journal_schema.yaml`, `project_governance_schema.yaml`, `fiction_schema.yaml`, `sales_collateral_schema.yaml` — add `temporal:` block (and effective_date/version prompt to banking_sop_guides).

**Created:**
- `artmind/conflicts.py` — candidate pairing (class-blocking → ANN → difflib tie-break), evidence gathering, LLM adjudication, MERGE-only materialization, orchestrator, refine-precondition check.
- `artmind/temporal.py` — document-date lifting, canonical property normalization, deterministic + bounded-LLM date parsing, supersession application/detection.
- `tests/test_domain_predicate.py`, `tests/test_conflicts.py`, `tests/test_temporal.py`, `tests/test_supersession.py`, `tests/test_ingest_hooks.py`.

**Sequencing rationale:** Phase T1 is independent of Phase 1, but Phase 1 goes first because both touch the same central predicate area of `graph_query.py` and the same 16 CLI options. Phase 1 centralizes the domain predicate and flips CLI to `multiple=True`; T1 then composes `asof_predicate` onto the already-centralized `domain_predicate` and adds `--asOf` to the already-repeatable options. Reversing the order would mean adding `--asOf` to single-domain commands and immediately reworking them for multi-domain.

---

# Phase 1 — Cross-Domain Retrieval

## Task 1.1: Domain predicate builder in `graph_query.py`

**Files:**
- Modify: `artmind/graph_query.py` (after line 26, before `PATTERN_REQUIRED_OPTIONS`)
- Test: `tests/test_domain_predicate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_domain_predicate.py`:

```python
"""Domain predicate + normalization unit tests (no Neo4j)."""
from artmind.graph_query import normalize_domains, domain_predicate


def test_normalize_single_string():
    assert normalize_domains("banking_policy") == ["banking_policy"]


def test_normalize_comma_split_and_strip():
    assert normalize_domains("a, b ,c") == ["a", "b", "c"]


def test_normalize_sequence_flattens_and_dedupes():
    assert normalize_domains(["a,b", "b", "c"]) == ["a", "b", "c"]


def test_normalize_empty_raises():
    import pytest
    with pytest.raises(ValueError):
        normalize_domains("")


def test_domain_predicate_shape():
    pred = domain_predicate("e")
    assert pred == (
        "(e.domain IN $domains OR any(d IN $domains WHERE e.domain STARTS WITH (d + '.')))"
    )


def test_domain_predicate_custom_var_and_param():
    pred = domain_predicate("node", param="doms")
    assert "node.domain IN $doms" in pred
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_domain_predicate.py -v`
Expected: FAIL with `ImportError: cannot import name 'normalize_domains'`.

- [ ] **Step 3: Implement the builders**

In `artmind/graph_query.py`, immediately after `sanitize_lucene_query` (line 26), add:

```python
from typing import Sequence


def normalize_domains(value: "str | Sequence[str]") -> list[str]:
    """Flatten a str or sequence of domain strings into a deduped, stripped list.

    Each element may itself be comma-separated. Order is preserved.
    Raises ValueError if the result is empty.
    """
    raw: list[str] = [value] if isinstance(value, str) else list(value)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        for part in str(item).split(","):
            d = part.strip()
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    if not out:
        raise ValueError("At least one --domain is required")
    return out


def domain_predicate(var: str, param: str = "domains") -> str:
    """Cypher WHERE fragment scoping `var` to any of the domains in $param.

    A one-element list is semantically identical to the old single-domain
    predicate (exact match OR sub-domain rollup via STARTS WITH).
    """
    return (
        f"({var}.domain IN ${param} "
        f"OR any(d IN ${param} WHERE {var}.domain STARTS WITH (d + '.')))"
    )
```

Add `Sequence` to the existing `from typing import Any` line (make it `from typing import Any, Sequence`) and remove the local re-import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_domain_predicate.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/graph_query.py tests/test_domain_predicate.py
git commit -m "feat: add normalize_domains and domain_predicate builders

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.2: Rewrite `graph_query.py` read functions to domain lists

**Files:**
- Modify: `artmind/graph_query.py` — `graph_metadata`, `structural_metadata`, `entity_listing`, `_pattern_query`, `execute_pattern`

- [ ] **Step 1: Rewrite `graph_metadata` (lines 147–180)**

Replace the whole function with:

```python
def graph_metadata(domains: "str | Sequence[str]") -> dict:
    domains = normalize_domains(domains)
    cypher = f"""
    CALL () {{
      MATCH (n)
      WHERE {domain_predicate("n")}
      UNWIND labels(n) AS label
      WITH label, keys(n) AS nodeKeys, n.type AS typeVal
      UNWIND nodeKeys AS propName
      RETURN "nodes" AS category,
             label AS name,
             collect(DISTINCT propName) AS propertyNames,
             collect(DISTINCT typeVal) AS distinctTypes,
             null AS connections
    UNION
      MATCH (s)-[r]->(e)
      WHERE {domain_predicate("s")}
        AND {domain_predicate("e")}
      WITH type(r) AS relType, labels(s) AS fromLabels, labels(e) AS toLabels, keys(r) AS relKeys
      UNWIND relKeys AS propName
      RETURN "relationships" AS category,
             relType AS name,
             collect(DISTINCT propName) AS propertyNames,
             null AS distinctTypes,
             collect(DISTINCT {{from: fromLabels, to: toLabels}}) AS connections
    }}
    RETURN category, name, propertyNames, distinctTypes, connections
    ORDER BY category, name
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "metadata",
        "rows": _run_read_query(cypher, {"domains": domains}),
    }
```

- [ ] **Step 2: Add the `_domain_output` helper**

Immediately above `graph_metadata`, add:

```python
def _domain_output(domains: list[str]) -> dict:
    """Back-compat output keys: always 'domains'; add 'domain' when exactly one."""
    out: dict = {"domains": domains}
    if len(domains) == 1:
        out["domain"] = domains[0]
    return out
```

- [ ] **Step 3: Rewrite `structural_metadata` (lines 183–239)**

Change the signature to `def structural_metadata(domains: "str | Sequence[str]") -> dict:`, add `domains = normalize_domains(domains)` as the first line, make the cypher an f-string, and replace every `(x.domain = $domain OR x.domain STARTS WITH ($domain + '.'))` occurrence with the matching `{domain_predicate("d")}` / `{domain_predicate("c")}` / `{domain_predicate("u")}` / `{domain_predicate("e")}` call (one per UNION arm, matching the arm's node variable). Escape the literal `{...}` map projections by doubling braces. Change the return to:

```python
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "structural_metadata",
        "rows": _run_read_query(cypher, {"domains": domains}),
    }
```

- [ ] **Step 4: Rewrite `entity_listing` (lines 242–272)**

```python
def entity_listing(
    domains: "str | Sequence[str]",
    name_filter: str | None = None,
    count_all: bool = False,
) -> dict:
    domains = normalize_domains(domains)
    cypher = f"""
    MATCH (n:Entity)
    WHERE {domain_predicate("n")} AND n.name IS NOT NULL
      AND ($nameFilter IS NULL OR toLower(n.name) CONTAINS toLower($nameFilter))
    UNWIND labels(n) AS label
    WITH label, n.type AS type, collect(DISTINCT n.name) AS names
    RETURN label, collect({{type: type, names: names}}) AS typeGroups
    ORDER BY label
    """
    result: dict = {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "entity_listing",
        "rows": _run_read_query(cypher, {"domains": domains, "nameFilter": name_filter}),
    }
    if name_filter is not None:
        result["name_filter"] = name_filter
    if count_all:
        count_cypher = f"""
        MATCH (n:Entity)
        WHERE {domain_predicate("n")} AND n.name IS NOT NULL
        RETURN count(DISTINCT n) AS total
        """
        count_rows = _run_read_query(count_cypher, {"domains": domains})
        result["total_entities"] = count_rows[0]["total"] if count_rows else 0
    return result
```

- [ ] **Step 5: Rewrite `_pattern_query` predicates and param dicts**

In `_pattern_query` (lines 341–558), for every branch:
- Replace each literal `(x.domain = $domain OR x.domain STARTS WITH ($domain + '.'))` with `{domain_predicate("x")}` (the branch is already an f-string for pattern1/4/5/8/9; for pattern2/3/6/7/10 convert the string to an f-string and double any existing literal `{` `}` used in map projections/`reduce`).
- Replace every `{"domain": parameters["domain"]}` and `cypher_params = {"domain": parameters["domain"]}` with `{"domains": parameters["domains"]}`.
- In patterns 2, 3, 4, 10, add `.domain` to the chunk/document projection so multi-domain rows are attributable: change `chunk { .id, .name, .doc_id, source_type: 'document' }` to `chunk { .id, .name, .doc_id, .domain, source_type: 'document' }` (pattern2/3/4) and the pattern10 `chunk { .id, .name, .doc_id, .text }` to `chunk { .id, .name, .doc_id, .domain, .text }`; add `.domain` to pattern10's `d { .id, .name, .path }` → `d { .id, .name, .path, .domain }`.

Example — pattern1 becomes:

```python
    if pattern == "pattern1":
        label = parameters["entityClass"]
        return (
            f"""
            MATCH (e:{label})
            WHERE {domain_predicate("e")}
            RETURN e {{.*, label: labels(e)}} AS entityData
            ORDER BY e.name
            LIMIT $limit
            """,
            {"domains": parameters["domains"], "limit": parameters.get("limit", 200)},
        )
```

- [ ] **Step 6: Update `normalize_pattern_parameters` and `execute_pattern`**

`normalize_pattern_parameters` (line 300) currently strips `None`/`()`. The `domains` key is a list — leave it untouched (it is never `None`/`()`). Rewrite `execute_pattern` (lines 561–583):

```python
def execute_pattern(
    domains: "str | Sequence[str]",
    pattern: str,
    question: str | None = None,
    **parameters,
) -> dict:
    domains = normalize_domains(domains)
    params = normalize_pattern_parameters(pattern, {"domains": domains, **parameters})
    validate_pattern_parameters(pattern, params)
    cypher, cypher_params = _pattern_query(pattern, params)
    output_parameters = {
        key: value
        for key, value in params.items()
        if key != "domains" and value is not None
    }
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "pattern",
        "pattern": pattern,
        "question": question,
        "parameters": output_parameters,
        "rows": strip_embeddings(_run_read_query(cypher, cypher_params)),
    }
```

- [ ] **Step 7: Verify no raw single-domain predicate remains**

Run: `grep -n "= \$domain\b" artmind/graph_query.py`
Expected: zero matches.

- [ ] **Step 8: Regression test — one-element list matches old set**

Add to `tests/test_domain_predicate.py`:

```python
def test_pattern1_cypher_uses_in_domains():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 10}
    )
    assert "e.domain IN $domains" in cypher
    assert params["domains"] == ["fiction"]
    assert "= $domain" not in cypher
```

Run: `uv run pytest tests/test_domain_predicate.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add artmind/graph_query.py tests/test_domain_predicate.py
git commit -m "feat: graph_query read functions accept domain lists via domain_predicate

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.3: `domains_overview()` + `list_conflicts()` stubs in `graph_query.py`

**Files:**
- Modify: `artmind/graph_query.py` (append after `execute_pattern`)

- [ ] **Step 1: Add `domains_overview()`**

```python
def domains_overview() -> dict:
    """One aggregation grouped by n.domain: doc names/counts, entity counts, top classes.

    The cheap routing input that maps an area ("banking") to concrete sibling domains.
    """
    cypher = """
    CALL () {
      MATCH (d:Document)
      RETURN d.domain AS domain, 'documents' AS k,
             count(d) AS c, collect(DISTINCT d.name)[0..25] AS names
    UNION
      MATCH (e:Entity)
      RETURN e.domain AS domain, 'entities' AS k, count(e) AS c, null AS names
    UNION
      MATCH (e:Entity)
      WITH e.domain AS domain, e.entity_class AS cls, count(*) AS n
      ORDER BY n DESC
      WITH domain, collect(cls)[0..8] AS top
      RETURN domain, 'top_classes' AS k, 0 AS c, top AS names
    }
    WITH domain, collect({k: k, c: c, names: names}) AS parts
    RETURN domain, parts
    ORDER BY domain
    """
    rows = _run_read_query(cypher, {})
    overview: dict = {}
    for row in rows:
        d = row["domain"]
        entry = overview.setdefault(d, {"domain": d})
        for p in row["parts"]:
            if p["k"] == "documents":
                entry["document_count"] = p["c"]
                entry["documents"] = p["names"]
            elif p["k"] == "entities":
                entry["entity_count"] = p["c"]
            elif p["k"] == "top_classes":
                entry["top_classes"] = p["names"]
    return {
        "query_type": "graph",
        "command": "domains_overview",
        "domains": sorted(overview.keys()),
        "rows": [overview[k] for k in sorted(overview.keys())],
    }
```

- [ ] **Step 2: Add `list_conflicts()`**

```python
def list_conflicts(
    domains: "str | Sequence[str]",
    entity_ids: "Sequence[str] | None" = None,
    entity_name: str | None = None,
    status: str = "open",
) -> dict:
    """Read materialized Conflict nodes scoped to the given domains.

    status='all' returns every status; otherwise filters Conflict.status.
    """
    domains = normalize_domains(domains)
    entity_ids = list(entity_ids or [])
    cypher = f"""
    MATCH (co:Conflict)
    WHERE any(d IN $domains WHERE d IN co.domains)
      AND ($status = 'all' OR co.status = $status)
    OPTIONAL MATCH (co)-[:CONFLICT_OF]->(e:Entity)
    WITH co, collect(DISTINCT e {{ .id, .name, .entity_class, .domain }}) AS entities
    WHERE ($entityIds = [] OR any(e IN entities WHERE e.id IN $entityIds))
      AND ($entityName IS NULL OR any(e IN entities WHERE toLower(e.name) CONTAINS toLower($entityName)))
    OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
    RETURN co {{ .* }} AS conflict, entities,
           collect(DISTINCT {{ side: ev.side, chunk_id: c.id, doc_id: c.doc_id, domain: c.domain, text: c.text }}) AS evidence
    ORDER BY co.severity DESC, co.detected_at DESC
    """
    return {
        **_domain_output(domains),
        "query_type": "graph",
        "command": "conflicts",
        "status": status,
        "rows": _run_read_query(
            cypher,
            {"domains": domains, "entityIds": entity_ids, "entityName": entity_name, "status": status},
        ),
    }
```

- [ ] **Step 3: Verify imports load**

Run: `uv run python -c "from artmind.graph_query import domains_overview, list_conflicts; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add artmind/graph_query.py
git commit -m "feat: add domains_overview and list_conflicts read functions

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.4: Multi-domain `vector_query.py`

**Files:**
- Modify: `artmind/vector_query.py`

- [ ] **Step 1: Update `vector_search` signature and cypher**

Change `def vector_search(domain: str, ...)` to `def vector_search(domains, topK: int = 5) -> dict:` — actually keep the `question` param. New signature:

```python
def vector_search(domains, question: str, topK: int = 5) -> dict:
    from artmind.graph_query import normalize_domains, domain_predicate, _domain_output
    domains = normalize_domains(domains)
    embedding = embed_question(question)
    n = len(domains)
```

Replace both `WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))` lines with `WHERE {domain_predicate("node")}` (convert the cypher strings to f-strings; the map projections `node { ... }` contain no `{` needing escaping except the projection braces — double them). Change the params block:

```python
    params = {
        "domains": domains,
        "embedding": embedding,
        "topK": int(topK),
        "candidateK": max(int(topK) * 5 * n, int(topK)),
    }
```

And the return uses `**_domain_output(domains)` instead of `"domain": domain`.

- [ ] **Step 2: Update `full_text_search`**

Same treatment: signature `def full_text_search(domains, question: str, topK: int = 5) -> dict:`, normalize, `domain_predicate("node")` in both arms, params `"domains": domains`, output `**_domain_output(domains)`.

- [ ] **Step 3: Update `entity_resolve`**

Signature `def entity_resolve(domains, reference: str, topK: int = 5) -> dict:`, normalize domains, `n = len(domains)`, replace both predicates with `domain_predicate` forms, pass `domains=domains` and `candidateK=max(int(topK) * 5 * n, int(topK))` into `session.run`, output `**_domain_output(domains)`.

- [ ] **Step 4: Update `vector_text_search`**

```python
def vector_text_search(domains, question: str, topK: int = 5) -> dict:
    from artmind.graph_query import normalize_domains, _domain_output
    domains = normalize_domains(domains)
    vector_results = vector_search(domains, question, topK)
    text_results = full_text_search(domains, question, topK)
    combined_rows = _rrf_combine(vector_results["rows"], text_results["rows"], topK)
    return {
        **_domain_output(domains),
        "query_type": "vector_text",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": combined_rows,
    }
```

- [ ] **Step 5: Verify no single-domain predicate remains**

Run: `grep -n "= \$domain\b" artmind/vector_query.py`
Expected: zero matches.

- [ ] **Step 6: Commit**

```bash
git add artmind/vector_query.py
git commit -m "feat: vector_query accepts domain lists and scales candidateK by domain count

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.5: Multi-domain `text2cypher.py`

**Files:**
- Modify: `artmind/text2cypher.py`

- [ ] **Step 1: Update `build_text2cypher_prompt`**

Change signature to `def build_text2cypher_prompt(question, schema_info, entities_info, domains: list[str]) -> str:`. Replace the domain rule (line 100–102) with:

```
- Always scope results to the domains by including a WHERE clause:
    (n.domain IN $domains OR any(d IN $domains WHERE n.domain STARTS WITH (d + '.')))
  Apply this filter to every unbound node in the MATCH pattern.
```

Replace `(domain: {domain})` interpolations with `(domains: {domains})`, and the final JSON-key instruction:

```
- "parameters": a JSON object of query parameters (always include "domains": {domains})
```

- [ ] **Step 2: Update `generate_cypher`**

```python
def generate_cypher(question: str, domains, model: str | None = None) -> dict:
    from artmind.graph_query import normalize_domains
    domains = normalize_domains(domains)
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    metadata = graph_metadata(domains)
    listing = entity_listing(domains)
    schema_info = _schema_summary(metadata)
    entities_info = _entities_summary(listing)
    prompt = build_text2cypher_prompt(question, schema_info, entities_info, domains)
    raw = call_llm(resolved_model, prompt)
    parsed = parse_json_response(raw)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    cypher = parsed.get("cypher", "")
    parameters = parsed.get("parameters", {})
    if not cypher:
        raise ValueError("LLM did not return a Cypher query")
    parameters.setdefault("domains", domains)
    validate_read_only(cypher)
    logger.info("text2cypher generated: {}", cypher)
    return {"cypher": cypher, "parameters": parameters}
```

- [ ] **Step 3: Update `execute_text2cypher`**

Change signature to `def execute_text2cypher(question, domains, model=None, dry_run=False) -> dict:`, add `from artmind.graph_query import normalize_domains, _domain_output` + `domains = normalize_domains(domains)`, call `generate_cypher(question, domains, model)`, and build output with `**_domain_output(domains)` instead of `"domain": domain`. Keep the `{k: v for k, v in parameters.items() if k != "domains"}` filter.

- [ ] **Step 4: Verify import**

Run: `uv run python -c "from artmind.text2cypher import execute_text2cypher; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add artmind/text2cypher.py
git commit -m "feat: text2cypher accepts domain lists and IN \$domains prompt rule

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.6: CLI — repeatable `--domain`, `_parse_domains`, `domains-overview`

**Files:**
- Modify: `artmind/cli.py`

- [ ] **Step 1: Add `_parse_domains` helper**

Near the other private helpers at the top of `cli.py` (after imports), add:

```python
def _parse_domains(values: "tuple[str, ...]") -> list[str]:
    """Flatten repeatable/comma-split --domain values into a deduped list."""
    from artmind.graph_query import normalize_domains
    return normalize_domains(list(values))
```

- [ ] **Step 2: Flip every query `--domain` to repeatable and thread the list**

For each of the 16 query commands (`metadata`, `structural-metadata`, `entity-listing`, `pattern1`–`pattern10`, `text2cypher`, `vector-text`, `entity-resolve`), change:

```python
@click.option("--domain", required=True, help="Domain to query")
def cmd(domain: str, ...):
    ... graph_query.X(domain, ...)
```

to:

```python
@click.option("--domain", "domain", required=True, multiple=True, help="Domain to query (repeatable; comma-splittable)")
def cmd(domain: tuple, ...):
    domains = _parse_domains(domain)
    ... graph_query.X(domains, ...)
```

Concretely: `graph_metadata_cmd` calls `graph_query.graph_metadata(domains)`; `graph_structural_metadata_cmd` → `structural_metadata(domains)`; `graph_entity_listing_cmd` → `entity_listing(domains, ...)`; `vector_text` → `vector_query.vector_text_search(domains, question, top_k)`; `query_entity_resolve` → `vector_query.entity_resolve(domains, reference, top_k)`; `graph_text2cypher` → `text2cypher.execute_text2cypher(question=question, domains=domains, dry_run=dry_run)`.

- [ ] **Step 3: Update `_run_graph_pattern`**

```python
def _run_graph_pattern(
    pattern: str, domains: list[str], compact: bool, question: str | None, **kwargs
) -> None:
    try:
        result = graph_query.execute_pattern(
            domains=domains, pattern=pattern, question=question, **kwargs
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(result, compact)
```

In each `graph_patternN` body, add `domains = _parse_domains(domain)` as the first line and pass `domains` to `_run_graph_pattern`.

- [ ] **Step 4: Register `query domains-overview`**

After the `query` group definition (line ~689), add:

```python
@query.command("domains-overview")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def query_domains_overview(compact: bool) -> None:
    """Per-domain routing summary: doc names/counts, entity counts, top classes."""
    _echo_json(graph_query.domains_overview(), compact)
```

- [ ] **Step 5: Smoke test**

Run: `uv run artmind query vector-text --domain banking_policy --domain banking_sop_guides --topK 4 --compact "who can approve a fee reversal after a customer complaint"`
Expected: JSON with `"domains": ["banking_policy","banking_sop_guides"]` and chunks whose `document.domain` spans both domains.

Run: `uv run artmind query domains-overview --compact`
Expected: JSON listing each domain with `document_count`, `entity_count`, `top_classes`.

- [ ] **Step 6: Single-domain regression**

Run: `uv run artmind query graph entity-listing --domain fiction --countAll --compact`
Expected: JSON containing both `"domain": "fiction"` and `"domains": ["fiction"]` and `total_entities`.

- [ ] **Step 7: Commit**

```bash
git add artmind/cli.py
git commit -m "feat: repeatable --domain across 16 query commands + domains-overview

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 1.7: SKILL.md — Route + query-time Adjudicate (Phase-1 slice)

**Files:**
- Modify: `skills/artmind-query/SKILL.md`

- [ ] **Step 1: Replace the protocol header and add Route**

Change the section heading `## The Query Protocol: Discover → Resolve → Retrieve → Ground` to `## The Query Protocol: Route → Discover → Resolve → Retrieve → Ground → Adjudicate` and insert a new step **0. Route** before Discover:

```markdown
### 0. Route — pick the domain set

Policies and the SOPs/matrices about the same subject live in DIFFERENT sibling
domains by design (e.g. `banking_policy` vs `banking_sop_guides`). Before answering:

```bash
uv run artmind query domains-overview --compact
```

- If the user names an exact single small domain, use it and skip to Discover.
- If the user names an AREA ("banking", "our policies"), or more than one domain
  is plausible, or listings look large, launch ONE sub-agent that runs
  `domains-overview` + per-domain `structural-metadata --compact` + `entity-resolve`,
  and returns ONLY a compact routing report:
  `{domains, resolved_entities:[{id,name,class,domain}], relevant_classes, relevant_rel_types}`.
  Main context never sees the raw listings.
- Pass `--domain` once per selected domain on every subsequent command; a single
  command call now spans all of them.
```

- [ ] **Step 2: Update Retrieve/Ground for attribution**

In step 3 (Retrieve) routing notes, replace the bullet "All commands are domain-rolled-up..." with:

```markdown
- All commands accept repeatable `--domain` (comma-splittable) and roll sub-domains up.
  Rows carry `.domain` on chunks/documents — every fact you state must be attributed
  to BOTH its document name AND its domain.
```

In step 4 (Ground), change the example to `--domain <d1> --domain <d2>` and note both-domain retrieval.

- [ ] **Step 3: Add the Adjudicate step**

After step 4 (Ground), add:

```markdown
### 5. Adjudicate — surface disagreements, never blend

After grounding, compare quantitative/authority claims across the retrieved
documents and domains (no extra LLM calls — the evidence is already in context).
When two sources disagree, surface BOTH claims with BOTH provenances in this format:

> Sources disagree: policy_complaints.md (banking_policy) says X; escalation_matrix.md
> (banking_sop_guides) says Y.

Never average, reconcile silently, or drop one side. If retrieval returned only one
side, re-run Ground with the sibling domains from Route before concluding.
```

- [ ] **Step 4: Update the Fallback Ladder**

Add as the new first rung: `0. Thin results in the chosen domain → re-run with sibling domains from Route before concluding data is absent.`

- [ ] **Step 5: Verify the file parses as markdown and mentions the new steps**

Run: `grep -c "Route\|Adjudicate\|domains-overview" skills/artmind-query/SKILL.md`
Expected: a count ≥ 4.

- [ ] **Step 6: Commit**

```bash
git add skills/artmind-query/SKILL.md
git commit -m "docs: artmind-query skill gains Route and query-time Adjudicate steps

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

# Phase T1 — Temporal Mechanics

## Task T1.1: Temporal range indexes in `setup.py`

**Files:**
- Modify: `artmind/setup.py`

- [ ] **Step 1: Add range indexes**

In `_setup_neo4j`, after the domain single-property indexes (line 35), add:

```python
    # ── Temporal range indexes (canonical valid-time / event-time) ────────────
    session.run("CREATE INDEX entity_valid_from IF NOT EXISTS FOR (n:Entity) ON (n.valid_from)")
    session.run("CREATE INDEX entity_valid_to IF NOT EXISTS FOR (n:Entity) ON (n.valid_to)")
    session.run("CREATE INDEX entity_event_at IF NOT EXISTS FOR (n:Entity) ON (n.event_at)")
    session.run("CREATE INDEX chunk_valid_to IF NOT EXISTS FOR (n:DocChunk) ON (n.valid_to)")
    session.run("CREATE INDEX document_valid_from IF NOT EXISTS FOR (n:Document) ON (n.valid_from)")
    session.run("CREATE INDEX document_valid_to IF NOT EXISTS FOR (n:Document) ON (n.valid_to)")
```

- [ ] **Step 2: Add to setup summary**

In `setup_all`'s returned `"neo4j_indexes"` list, append the six index names.

- [ ] **Step 3: Run setup**

Run: `uv run artmind setup`
Expected: JSON summary listing the new `entity_valid_from`, `document_valid_from`, etc.

- [ ] **Step 4: Commit**

```bash
git add artmind/setup.py
git commit -m "feat: add temporal range indexes for valid_from/valid_to/event_at

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.2: `temporal.py` — deterministic date parsing + document lifting

**Files:**
- Create: `artmind/temporal.py`
- Test: `tests/test_temporal.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_temporal.py`:

```python
"""Deterministic date parsing and document-date lifting (no Neo4j)."""
from artmind.temporal import parse_iso, lift_document_dates


def test_parse_iso_full_date():
    assert parse_iso("2026-06-01") == "2026-06-01"


def test_parse_iso_human_date():
    assert parse_iso("15 March 2026") == "2026-03-15"


def test_parse_iso_partial_year():
    assert parse_iso("2026") == "2026"


def test_parse_iso_unparseable_returns_none():
    assert parse_iso("early spring") is None


def test_lift_document_dates_from_header():
    md = "# Policy\n\n**Effective Date:** 2026-06-01\n\n**Version:** 3.0\n\nBody."
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["valid_from"] == "2026-06-01"
    assert out["version"] == "3.0"
    assert out["time_source"] == "header"


def test_lift_document_dates_frontmatter_fallback():
    mapping = {"valid_from": ["Effective Date"]}
    out = lift_document_dates("Body with no header", {"date": "2024-01-01"}, mapping)
    assert out["valid_from"] == "2024-01-01"
    assert out["time_source"] == "frontmatter"


def test_lift_document_dates_from_metadata_table():
    # Real corpus format (verified against banking_document_corpus/policies/*.md):
    # a markdown "| Field | Value |" table, NOT colon-delimited prose.
    md = (
        "# Policy\n\n"
        "## Metadata\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Version | 3.0 |\n\n"
        "Body."
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["valid_from"] == "2026-06-01"
    assert out["version"] == "3.0"
    assert out["time_source"] == "header"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_temporal.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.temporal'`.

**Note (added after plan review 2026-07-04):** the two tests above are deliberately different formats. Verified directly against the actual corpus (`banking_document_corpus/policies/policy_complaints_v3.md` and its siblings): every document in this corpus uses the `| Field | Value |` metadata table, not `**Label:** value` prose. Running the original colon-only regex against the real fixture returns no match — confirmed by executing it against the file before writing this plan. Step 3 below implements both branches; do not drop the table branch as an optimization.

- [ ] **Step 3: Implement parsing + lifting**

Create `artmind/temporal.py`:

```python
"""Temporal normalization: canonical valid-time / event-time properties.

Non-destructive: original domain-named properties are never touched; canonical
`valid_from`/`valid_to`/`event_at` are additive copies. Idempotent on re-run.
"""
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response
from paths import DOMAIN_SCHEMAS_DIR, MARKDOWNS_DIR

_MONTHS = {
    m.lower(): i for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], start=1)
}
_ISO_FULL_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_PARTIAL_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?$")
_DMY_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$")
_MDY_RE = re.compile(r"^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$")


def parse_iso(value: str | None) -> str | None:
    """Deterministically parse a date-ish string to ISO-8601 (date), else None.

    Accepts full ISO, partial ISO (year / year-month), '15 March 2026',
    'March 15, 2026'. Returns the input unchanged for partial ISO.
    """
    if not value:
        return None
    v = value.strip()
    if _ISO_FULL_RE.match(v):
        return _ISO_FULL_RE.match(v).group(0)
    if _ISO_PARTIAL_RE.match(v):
        return v
    m = _DMY_RE.match(v)
    if m and m.group(2).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = _MDY_RE.match(v)
    if m and m.group(1).lower() in _MONTHS:
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None


def _find_header_value(md_text: str, labels: list[str]) -> str | None:
    """Find a label's value in the markdown body.

    Tries two formats, in order, since the real corpus uses the table form
    exclusively (verified against banking_document_corpus/policies/*.md —
    every document uses a "| Field | Value |" metadata table, never colon
    prose):
      1. Markdown table row: "| Label | Value |"
      2. Colon-delimited prose: "**Label:** value" / "Label: value"
    """
    for label in labels:
        table_pat = re.compile(
            r"^\|\s*" + re.escape(label) + r"\s*\|\s*(.+?)\s*\|\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = table_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
        prose_pat = re.compile(
            r"^\**\s*" + re.escape(label) + r"\s*:\**\s*(.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        m = prose_pat.search(md_text)
        if m:
            return m.group(1).strip().strip("*").strip()
    return None


def lift_document_dates(md_text: str, frontmatter: dict, mapping: dict) -> dict:
    """Return canonical document props from header labels / frontmatter.

    mapping example: {"valid_from": ["Effective Date"], "version": ["Version"]}
    Records time_source: 'header' | 'frontmatter'.
    """
    out: dict = {}
    source = None
    for canon, labels in mapping.items():
        raw = _find_header_value(md_text, labels)
        if raw is not None:
            source = source or "header"
        else:
            for lbl in labels:
                if frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl):
                    raw = str(frontmatter.get(lbl.lower().replace(" ", "_")) or frontmatter.get(lbl))
                    source = source or "frontmatter"
                    break
        if raw is None and canon == "valid_from" and frontmatter.get("date"):
            raw = str(frontmatter["date"])
            source = source or "frontmatter"
        if raw is None:
            continue
        if canon == "version":
            out["version"] = raw
        else:
            iso = parse_iso(raw)
            out[canon] = iso if iso else raw
    if out:
        out["time_source"] = source or "header"
    return out


def load_schema(domain: str) -> dict:
    path = DOMAIN_SCHEMAS_DIR / f"{domain}_schema.yaml"
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_temporal.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py tests/test_temporal.py
git commit -m "feat: temporal date parsing and document-date lifting

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.3: `temporal.py` — entity normalization + Neo4j writer

**Files:**
- Modify: `artmind/temporal.py`
- Test: `tests/test_temporal.py`

- [ ] **Step 1: Write failing test for entity mapping**

Append to `tests/test_temporal.py`:

```python
from artmind.temporal import canonical_entity_dates


def test_canonical_entity_dates_valid_from():
    schema_entities = {"POLICY": {"valid_from": "effective_date"}}
    entity = {"entity_class": "POLICY", "properties": {"effective_date": "2026-01-15"}}
    out = canonical_entity_dates(entity, schema_entities, anchor=None)
    assert out["valid_from"] == "2026-01-15"
    assert out["time_source"] == "property"


def test_canonical_entity_dates_event_at():
    schema_entities = {"EVENT": {"event_at": "date_or_time"}}
    entity = {"entity_class": "EVENT", "properties": {"date_or_time": "15 March 2026"}}
    out = canonical_entity_dates(entity, schema_entities, anchor=None)
    assert out["event_at"] == "2026-03-15"


def test_canonical_entity_dates_no_mapping_returns_empty():
    entity = {"entity_class": "PERSON", "properties": {"name": "x"}}
    assert canonical_entity_dates(entity, {}, anchor=None) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_temporal.py::test_canonical_entity_dates_valid_from -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `canonical_entity_dates` + the Neo4j normalization writer**

Append to `artmind/temporal.py`:

```python
def canonical_entity_dates(entity: dict, schema_entities: dict, anchor: str | None) -> dict:
    """Map an entity's schema-declared date property to canonical props.

    schema_entities: {ENTITY_CLASS: {canonical_key: domain_property_name, ...}}
    Returns {} when the class has no temporal mapping or the value is absent.
    Deterministic parse only; unparseable values are left for the LLM leftover pass.
    """
    cls = entity.get("entity_class", "")
    mapping = schema_entities.get(cls)
    if not mapping:
        return {}
    props = entity.get("properties", entity)
    out: dict = {}
    unresolved: dict = {}
    for canon, domain_prop in mapping.items():
        raw = props.get(domain_prop)
        if raw is None:
            continue
        iso = parse_iso(str(raw))
        if iso:
            out[canon] = iso
        else:
            unresolved[canon] = str(raw)
    if out:
        out["time_source"] = "property"
    if unresolved:
        out["_unresolved"] = unresolved
        out["_anchor"] = anchor
    return out


def _temporal_mapping(schema: dict) -> tuple[dict, dict, str | None]:
    """Return (document_mapping, entities_mapping, relative_anchor) from a schema."""
    t = schema.get("temporal") or {}
    return t.get("document", {}), t.get("entities", {}), t.get("relative_anchor")


def normalize_time(domain: str, dry_run: bool = False) -> dict:
    """Backfill canonical temporal properties for every document in a domain.

    Additive + idempotent. Reads each Document's markdown for header dates and
    each Entity's schema-mapped property. Returns counts (deterministic vs llm).
    """
    schema = load_schema(domain)
    doc_map, ent_map, anchor = _temporal_mapping(schema)
    stats = {"domain": domain, "documents": 0, "entities": 0,
             "deterministic": 0, "llm": 0, "dry_run": dry_run}
    with neo4j_session() as session:
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain = $domain RETURN d.id AS id, d.name AS name, d.path AS path",
            domain=domain,
        ).data()
        for doc in docs:
            md_file = MARKDOWNS_DIR / f"{Path(doc['name']).stem}.md"
            md_text, fm = "", {}
            if md_file.exists():
                from artmind.ingest import _parse_md_frontmatter
                fm, md_text = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
            lifted = lift_document_dates(md_text, fm, doc_map) if doc_map else {}
            if lifted:
                stats["documents"] += 1
                stats["deterministic"] += 1
                if not dry_run:
                    session.run(
                        "MATCH (d:Document {id:$id}) SET d += $props, d.ingested_at = coalesce(d.ingested_at, $now)",
                        id=doc["id"], props=lifted,
                        now=datetime.now(timezone.utc).isoformat(),
                    )
        if ent_map:
            ents = session.run(
                "MATCH (e:Entity) WHERE e.domain = $domain RETURN e.id AS id, e.entity_class AS entity_class, properties(e) AS properties",
                domain=domain,
            ).data()
            for e in ents:
                canon = canonical_entity_dates(e, ent_map, anchor)
                clean = {k: v for k, v in canon.items() if not k.startswith("_")}
                if clean:
                    stats["entities"] += 1
                    stats["deterministic"] += 1
                    if not dry_run:
                        session.run(
                            "MATCH (e:Entity {id:$id}) SET e += $props",
                            id=e["id"], props=clean,
                        )
    logger.info("normalize_time({}): {}", domain, stats)
    return stats


def normalize_ingested_document(doc_kg_dir: Path, domain: str) -> dict:
    """Per-document normalization hook — runs after write_to_graph() at ingest time.

    Additive-only, idempotent, single-document scope; no dry-run gate.
    """
    import json
    schema = load_schema(domain)
    doc_map, ent_map, anchor = _temporal_mapping(schema)
    if not (doc_map or ent_map):
        return {"domain": domain, "skipped": "no temporal block"}
    try:
        document = json.loads((doc_kg_dir / "document.json").read_text(encoding="utf-8"))
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("normalize_ingested_document: could not load JSON: {}", e)
        return {"domain": domain, "error": str(e)}
    md_file = MARKDOWNS_DIR / f"{Path(document['name']).stem}.md"
    md_text, fm = "", {}
    if md_file.exists():
        from artmind.ingest import _parse_md_frontmatter
        fm, md_text = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
    lifted = lift_document_dates(md_text, fm, doc_map) if doc_map else {}
    written = {"documents": 0, "entities": 0}
    with neo4j_session() as session:
        if lifted:
            session.run(
                "MATCH (d:Document {id:$id}) SET d += $props, d.ingested_at = coalesce(d.ingested_at, $now)",
                id=document["id"], props=lifted, now=datetime.now(timezone.utc).isoformat(),
            )
            written["documents"] = 1
        for e in entities:
            canon = canonical_entity_dates(e, ent_map, anchor)
            clean = {k: v for k, v in canon.items() if not k.startswith("_")}
            if clean:
                session.run("MATCH (e:Entity {id:$id}) SET e += $props", id=e["id"], props=clean)
                written["entities"] += 1
    logger.info("normalize_ingested_document({}): {}", document.get("name"), written)
    return {"domain": domain, **written}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_temporal.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py tests/test_temporal.py
git commit -m "feat: entity temporal normalization + standalone and per-document writers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.4: Auto-hook `normalize-time` into `ingest_to_kg()`

**Files:**
- Modify: `artmind/ingest.py:1138-1141`
- Test: `tests/test_ingest_hooks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest_hooks.py`:

```python
"""ingest_to_kg auto-chains normalize-time but NOT refine-graph/detect-conflicts."""
import inspect
import artmind.ingest as ing


def test_ingest_to_kg_calls_normalize_after_write():
    src = inspect.getsource(ing.ingest_to_kg)
    assert "normalize_ingested_document" in src
    assert src.index("write_to_graph") < src.index("normalize_ingested_document")


def test_ingest_to_kg_does_not_call_refine_or_detect():
    src = inspect.getsource(ing.ingest_to_kg)
    assert "refine_graph" not in src
    assert "detect_conflicts" not in src
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ingest_hooks.py -v`
Expected: FAIL on `test_ingest_to_kg_calls_normalize_after_write`.

- [ ] **Step 3: Add the hook**

In `artmind/ingest.py`, replace the tail of `ingest_to_kg` (lines 1138–1141):

```python
    doc_kg_dir = extract_kg(file_result, domain, text_model, embed_model)
    if doc_kg_dir is None:
        return False
    ok = write_to_graph(doc_kg_dir)
    if ok:
        # Auto-chain temporal normalization (per-document, additive, idempotent).
        # NOT refine-graph / detect-conflicts — those are explicit-call-only
        # cross-domain judgment operations gated by dry-run/apply workflows.
        try:
            from artmind.temporal import normalize_ingested_document
            normalize_ingested_document(doc_kg_dir, domain)
        except Exception as e:
            logger.warning("normalize-time hook failed for {}: {}", doc_kg_dir, e)
    return ok
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ingest_hooks.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py tests/test_ingest_hooks.py
git commit -m "feat: auto-normalize temporal properties after write_to_graph in ingest_to_kg

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.5: `--asOf` filter in `graph_query.py` + `vector_query.py`

**Files:**
- Modify: `artmind/graph_query.py`, `artmind/vector_query.py`
- Test: `tests/test_temporal.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_temporal.py`:

```python
def test_asof_predicate_shape():
    from artmind.graph_query import asof_predicate
    pred = asof_predicate("e")
    assert "$asOf IS NULL" in pred
    assert "e.valid_from IS NULL OR e.valid_from <= $asOf" in pred
    assert "e.valid_to IS NULL OR e.valid_to > $asOf" in pred


def test_pattern1_cypher_includes_asof_when_requested():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": "2026-07-04"}
    )
    assert "$asOf" in cypher
    assert params["asOf"] == "2026-07-04"


def test_pattern1_cypher_omits_asof_when_none():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": None}
    )
    assert "$asOf" not in cypher
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_temporal.py::test_asof_predicate_shape -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Add `asof_predicate` beside `domain_predicate`**

In `artmind/graph_query.py`, after `domain_predicate`, add:

```python
def asof_predicate(var: str, param: str = "asOf") -> str:
    """NULL-safe valid-time filter. Untimed nodes are always visible.

    Emitted only when the caller requests as-of filtering.
    """
    return (
        f"(${param} IS NULL OR "
        f"(({var}.valid_from IS NULL OR {var}.valid_from <= ${param}) "
        f"AND ({var}.valid_to IS NULL OR {var}.valid_to > ${param})))"
    )
```

- [ ] **Step 4: Thread `as_of` through `execute_pattern` and `_pattern_query`**

In `execute_pattern`, add parameter `as_of: str | None = None` and include it: `params = normalize_pattern_parameters(pattern, {"domains": domains, "asOf": as_of, **parameters})`. In `normalize_pattern_parameters`, the `None`-strip already drops `asOf` when `None` — keep that. In `_pattern_query`, for each pattern that filters entities (`pattern1,2,3,4,5,8,9`) append, when `parameters.get("asOf")`, an extra `AND {asof_predicate("e")}` (using the branch's primary entity variable) to the WHERE clause, and add `"asOf": parameters["asOf"]` to that branch's cypher_params. Implement with a small local helper at the top of `_pattern_query`:

```python
    as_of = parameters.get("asOf")
    def _asof(var: str) -> str:
        return f"\n              AND {asof_predicate(var)}" if as_of else ""
```

Then in e.g. pattern1: `WHERE {domain_predicate("e")}{_asof("e")}` and cypher_params gains `**({"asOf": as_of} if as_of else {})`.

- [ ] **Step 5: Add `as_of` to `graph_metadata` and `entity_listing`**

Give `graph_metadata(domains, as_of=None)` and `entity_listing(domains, name_filter=None, count_all=False, as_of=None)` an optional as-of filter using the same `_asof` pattern (append `AND asof_predicate(...)` and pass `asOf` only when set). Also add `valid_from`/`valid_to`/`superseded_by` to pattern10's document/chunk projections and to `structural_metadata`'s Document arm so agents see currency at Discover time.

- [ ] **Step 6: Add `as_of` to `vector_query.vector_search` and `full_text_search`**

Add param `as_of: str | None = None`; when set, append `AND {asof_predicate("node")}` to the chunk arm and pass `"asOf": as_of`. Thread `as_of` through `vector_text_search`, `entity_resolve`.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_temporal.py tests/test_domain_predicate.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add artmind/graph_query.py artmind/vector_query.py tests/test_temporal.py
git commit -m "feat: --asOf valid-time filtering across graph and vector queries

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.6: CLI — `--asOf` options + `ingest normalize-time`

**Files:**
- Modify: `artmind/cli.py`

- [ ] **Step 1: Add `--asOf` to query commands**

To each `graph_patternN`, `metadata`, `structural-metadata`, `entity-listing`, `vector-text`, `entity-resolve` command, add:

```python
@click.option("--asOf", "as_of", default=None, help="Valid-time filter: ISO date; nodes without valid-time always shown")
```

Thread `as_of=as_of` into the corresponding call (`execute_pattern(..., as_of=as_of)`, `graph_metadata(domains, as_of=as_of)`, `vector_text_search(domains, question, top_k, as_of=as_of)`, etc.). Update `_run_graph_pattern` to accept and forward `as_of`.

- [ ] **Step 2: Register `ingest normalize-time`**

After `ingest_refine_graph` (line ~681), add:

```python
@ingest.command("normalize-time")
@click.option("--domain", required=True, help="Domain to backfill canonical temporal properties for")
@click.option("--dry-run", is_flag=True, help="Compute counts only; do not write")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_normalize_time(domain: str, dry_run: bool, compact: bool) -> None:
    """Backfill canonical valid_from/valid_to/event_at from schema temporal mappings.

    Additive and idempotent. Runs automatically per document at ingest; use this
    to backfill pre-existing documents or after editing a schema's temporal block.
    """
    _setup_logger()
    from artmind.temporal import normalize_time
    _echo_json(normalize_time(domain, dry_run=dry_run), compact)
```

- [ ] **Step 3: Verify commands register**

Run: `uv run artmind ingest normalize-time --help` and `uv run artmind query graph pattern1 --help`
Expected: `normalize-time` help shows `--dry-run`; pattern1 help shows `--asOf`.

- [ ] **Step 4: `--asOf` regression (no temporal data → identical results)**

Run: `uv run artmind query graph entity-listing --domain fiction --asOf 2026-07-04 --compact`
Expected: same rows as without `--asOf` (NULL-safe filter; fiction has no valid-time yet).

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py
git commit -m "feat: --asOf query options and ingest normalize-time command

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.7: Add `temporal:` blocks to 7 schemas

**Files:**
- Modify: `domains/schemas/banking_policy_schema.yaml`, `banking_reference_schema.yaml`, `banking_sop_guides_schema.yaml`, `personal_journal_schema.yaml`, `project_governance_schema.yaml`, `fiction_schema.yaml`, `sales_collateral_schema.yaml`

- [ ] **Step 1: banking_policy**

At top level (after `description:`), add:

```yaml
temporal:
  document:
    valid_from: [Effective Date, effective_date]
    version: [Version]
  entities:
    POLICY:                { valid_from: effective_date }
    REGULATORY_REFERENCE:  { valid_from: effective_date }
  relative_anchor: document.valid_from
```

- [ ] **Step 2: banking_reference**

```yaml
temporal:
  document:
    valid_from: [Effective Date, effective_date]
    version: [Version]
  entities:
    RATE_ENTRY:  { valid_from: effective_date }
  relative_anchor: document.valid_from
```

- [ ] **Step 3: banking_sop_guides — add effective_date/version prompt, then block**

In the `properties_prompt` block, in the `For PROCESS, consider:` list (locate the PROCESS property hints), add two hint lines:
```
      - effective_date (when this procedure version takes effect; ISO date if stated)
      - version (document version, if stated)
```
Then add the top-level block:

```yaml
temporal:
  document:
    valid_from: [Effective Date, effective_date, Last Updated]
    version: [Version]
  entities:
    PROCESS:  { valid_from: effective_date }
  relative_anchor: document.valid_from
```

- [ ] **Step 4: personal_journal**

```yaml
temporal:
  document:
    valid_from: [Date, date]
  entities:
    EVENT:     { event_at: date_or_time }
    ACTIVITY:  { event_at: date_or_time }
  relative_anchor: document.valid_from
```

- [ ] **Step 5: project_governance**

```yaml
temporal:
  document:
    valid_from: [Date, date]
  entities:
    MILESTONE:  { event_at: target_date }
    ACTION:     { event_at: due_date }
    PROJECT:    { valid_from: start_date, valid_to: end_date }
  relative_anchor: document.valid_from
```

- [ ] **Step 6: fiction**

```yaml
temporal:
  entities:
    EVENT:  { event_at: date_or_time }
```

- [ ] **Step 7: sales_collateral**

```yaml
temporal:
  document:
    valid_from: [Date, date]
  entities:
    EVENT:  { event_at: date_or_period }
  relative_anchor: document.valid_from
```

- [ ] **Step 8: Verify all 7 parse and carry the block**

Run:
```bash
uv run python -c "
import yaml
for s in ['banking_policy','banking_reference','banking_sop_guides','personal_journal','project_governance','fiction','sales_collateral']:
    d = yaml.safe_load(open(f'domains/schemas/{s}_schema.yaml'))
    assert 'temporal' in d, s
    print(s, 'OK', list(d['temporal'].get('entities', {}).keys()))
"
```
Expected: each prints `OK` with its mapped entity classes.

- [ ] **Step 9: Commit**

```bash
git add domains/schemas/banking_policy_schema.yaml domains/schemas/banking_reference_schema.yaml domains/schemas/banking_sop_guides_schema.yaml domains/schemas/personal_journal_schema.yaml domains/schemas/project_governance_schema.yaml domains/schemas/fiction_schema.yaml domains/schemas/sales_collateral_schema.yaml
git commit -m "feat: add temporal: blocks to 7 domain schemas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.8: text2cypher `--asOf` rule + SKILL.md temporal guidance

**Files:**
- Modify: `artmind/text2cypher.py`, `skills/artmind-query/SKILL.md`

- [ ] **Step 1: Add the asOf rule to the prompt**

In `build_text2cypher_prompt` RULES, add:

```
- If the question is present-tense ("who CAN approve…"), add a validity filter to
  timed nodes: ($asOf IS NULL OR ((n.valid_from IS NULL OR n.valid_from <= $asOf)
  AND (n.valid_to IS NULL OR n.valid_to > $asOf))); include "asOf" in parameters.
  For historical questions ("what WAS the limit in 2024?") set asOf to that date.
```

- [ ] **Step 2: SKILL.md temporal note**

In the Discover step, add: "Document/chunk rows and `metadata` now carry `valid_from`/`valid_to`/`superseded_by` — use them to judge document currency." In the Retrieve routing notes add: "Add `--asOf today` for present-tense questions ('who can approve…'); drop it for historical ones. Untimed knowledge is always visible."

- [ ] **Step 3: Verify**

Run: `grep -c "asOf" skills/artmind-query/SKILL.md artmind/text2cypher.py`
Expected: nonzero counts in both files.

- [ ] **Step 4: Commit**

```bash
git add artmind/text2cypher.py skills/artmind-query/SKILL.md
git commit -m "docs: text2cypher asOf rule and skill temporal guidance

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T1.9: Backfill + document-lifting verification

**Files:** (no code changes — verification against the live graph)

- [ ] **Step 1: Backfill banking_policy**

Run: `uv run artmind ingest normalize-time --domain banking_policy --compact`
Expected: JSON with `deterministic > 0` and `documents >= 1`; `dry_run: false`.

- [ ] **Step 2: Confirm Document.valid_from lifted with header source**

Run:
```bash
uv run artmind query graph text2cypher --domain banking_policy --dry-run --compact "list documents with their valid_from and time_source"
```
Then run the generated read query, or use `metadata`. Expected: at least one Document with `valid_from` set and `time_source: 'header'` (e.g. `policy_complaints.md`, Effective Date 2026-01-15).

- [ ] **Step 3: Confirm dateless fallback**

Pick a domain document with no header/frontmatter date and confirm it has no `valid_from` (or `time_source:'mtime'` if you later extend to mtime); assert normalize did not crash on it.

- [ ] **Step 4: Commit (verification note only — no file changes)**

No commit needed; record results in the execution log.

---

# Phase 2 — Materialized Conflicts

## Task 2.1: `Conflict` constraint + index in `setup.py`

**Files:**
- Modify: `artmind/setup.py`

- [ ] **Step 1: Add constraint + index**

In `_setup_neo4j`, after the uniqueness constraints (line 16), add:

```python
    session.run(
        "CREATE CONSTRAINT conflict_id IF NOT EXISTS FOR (n:Conflict) REQUIRE n.id IS UNIQUE"
    )
```

And after the temporal indexes (Task T1.1), add:

```python
    session.run("CREATE INDEX conflict_status IF NOT EXISTS FOR (n:Conflict) ON (n.status)")
```

- [ ] **Step 2: Update summary**

Add `"conflict_id"` to `neo4j_constraints` and `"conflict_status"` to `neo4j_indexes`.

- [ ] **Step 3: Run setup**

Run: `uv run artmind setup`
Expected: summary lists `conflict_id` and `conflict_status`.

- [ ] **Step 4: Commit**

```bash
git add artmind/setup.py
git commit -m "feat: Conflict.id uniqueness constraint and status index

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.2: `refine-graph` cross-domain guard + `RefineRun` marker

**Files:**
- Modify: `artmind/refine_graph.py`, `artmind/cli.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_conflicts.py` (shared file for Phase 2 unit tests) with the guard test first:

```python
"""Conflict detection + refine-graph guard unit tests (no Neo4j unless noted)."""
import inspect
import artmind.refine_graph as rg


def test_refine_graph_accepts_allow_cross_domain_merge():
    sig = inspect.signature(rg.refine_graph)
    assert "allow_cross_domain_merge" in sig.parameters


def test_apply_merges_writes_refine_run_marker():
    src = inspect.getsource(rg)
    assert "RefineRun" in src
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_conflicts.py -v`
Expected: FAIL on `test_refine_graph_accepts_allow_cross_domain_merge`.

- [ ] **Step 3: Add the guard + domain tagging + marker**

In `refine_graph.py`, add a helper and modify `refine_graph`:

```python
def _entity_domains(session, names: list[str]) -> dict[str, set[str]]:
    """Map each entity name to the set of domains it appears in."""
    rows = session.run(
        "MATCH (e:Entity) WHERE e.name IN $names RETURN e.name AS name, collect(DISTINCT e.domain) AS domains",
        names=names,
    ).data()
    return {r["name"]: set(r["domains"]) for r in rows}


def _record_refine_run(session, domains: list[str]) -> None:
    for d in domains:
        session.run(
            "MERGE (r:RefineRun {domain:$d}) SET r.at = $at",
            d=d, at=__import__("datetime").datetime.utcnow().isoformat(),
        )
```

Change `refine_graph` signature to add `allow_cross_domain_merge: bool = False` (after `dry_run`). After computing `report["proposed_merges"]` and before applying, when `domain is None` and not `allow_cross_domain_merge`, drop any merge whose alias and canonical span different domains and record them:

```python
    if domain is None and not allow_cross_domain_merge and report["proposed_merges"]:
        with neo4j_session() as session:
            names = set(report["proposed_merges"].keys()) | set(report["proposed_merges"].values())
            dmap = _entity_domains(session, list(names))
        kept, skipped = {}, {}
        for alias, canonical in report["proposed_merges"].items():
            spans = dmap.get(alias, set()) | dmap.get(canonical, set())
            if len(spans) > 1:
                skipped[alias] = canonical
            else:
                kept[alias] = canonical
        report["proposed_merges"] = kept
        report["skipped_cross_domain"] = skipped
        if skipped:
            logger.info("Skipped {} cross-domain merge cluster(s): {}", len(skipped), skipped)
```

In `apply_merges`, after the loop, record the run:

```python
        run_domains = sorted({domain}) if domain else sorted(
            {d for a in proposed_merges for d in (str(a),)} or []
        )
```

Simpler: pass the target domains explicitly. In `refine_graph`, after a successful apply, add:

```python
    if not dry_run and report["proposed_merges"]:
        stats = apply_merges(report["proposed_merges"], domain)
        report["stats"] = stats
        with neo4j_session() as session:
            _record_refine_run(session, [domain] if domain else sorted({
                dm for name in (set(report["proposed_merges"]) | set(report["proposed_merges"].values()))
                for dm in _entity_domains(session, [name]).get(name, {name})
            }))
```

(Replace the existing final apply block accordingly.)

- [ ] **Step 4: Add the CLI flag**

In `ingest_refine_graph`, add:

```python
@click.option("--allow-cross-domain-merge", "allow_cross_domain_merge", is_flag=True, default=False,
              help="Allow merging same-named entities across domains (default: skip and report them)")
```

Add `allow_cross_domain_merge: bool` to the function params and pass `allow_cross_domain_merge=allow_cross_domain_merge` into `refine_graph(...)`. In the output block, print skipped clusters when present:

```python
    skipped = report.get("skipped_cross_domain", {})
    if skipped:
        click.echo(f"Skipped {len(skipped)} cross-domain merge cluster(s) (use --allow-cross-domain-merge to merge): {skipped}")
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_conflicts.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add artmind/refine_graph.py artmind/cli.py tests/test_conflicts.py
git commit -m "feat: refine-graph cross-domain merge guard + RefineRun marker

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.3: `conflicts.py` — candidate pairing (class-block → ANN → difflib)

**Files:**
- Create: `artmind/conflicts.py`
- Test: `tests/test_conflicts.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_conflicts.py`:

```python
from artmind.conflicts import conflict_id, _name_ratio


def test_conflict_id_is_order_independent():
    a = conflict_id("idB", "idA", "fee reversal approval limit")
    b = conflict_id("idA", "idB", "fee reversal approval limit")
    assert a == b


def test_conflict_id_differs_by_aspect():
    assert conflict_id("idA", "idB", "aspect one") != conflict_id("idA", "idB", "aspect two")


def test_name_ratio_high_for_similar():
    assert _name_ratio("Fee Reversal", "fee reversal") > 0.9


def test_name_ratio_low_for_different():
    assert _name_ratio("Fee Reversal", "Sanctions List") < 0.5
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_conflicts.py -k "conflict_id or name_ratio" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.conflicts'`.

- [ ] **Step 3: Implement pairing core**

Create `artmind/conflicts.py`:

```python
"""Non-destructive cross-domain conflict detection.

Candidate pairing is NOT a brute-force cross-product: block by entity_class,
generate candidates via the entity_embedding ANN index (top-k per entity,
restricted to the other domain(s)), and use difflib name ratio only as a
secondary tie-break on the ANN shortlist. Materialization only ever CREATEs
annotations (Conflict nodes + CONFLICTS_WITH/CONFLICT_OF/EVIDENCE edges).
"""
import difflib
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.ingest import _call_llm_text, _parse_json_response


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def conflict_id(id_a: str, id_b: str, aspect: str) -> str:
    lo, hi = sorted([id_a, id_b])
    return hashlib.sha1(f"{lo}|{hi}|{_slug(aspect)}".encode("utf-8")).hexdigest()


def _name_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_refine_precondition(session, domains: list[str]) -> list[str]:
    """Return the subset of domains with NO recorded refine-graph run."""
    rows = session.run(
        "MATCH (r:RefineRun) WHERE r.domain IN $domains RETURN collect(r.domain) AS done",
        domains=domains,
    ).single()
    done = set(rows["done"]) if rows else set()
    return [d for d in domains if d not in done]


def candidate_pairs(
    domains: list[str],
    name_filter: str | None,
    sim_threshold: float,
    max_pairs: int,
    top_k: int = 10,
) -> list[dict]:
    """Generate cross-domain candidate entity pairs.

    1. Fetch entities with embeddings, grouped by (domain, entity_class).
    2. For each entity in domain A, ANN-query the entity_embedding index for the
       top_k nearest entities of the SAME class restricted to the OTHER domains.
    3. Keep pairs with cosine score >= sim_threshold; difflib name ratio is a
       secondary tie-break added to the sort key, never the primary generator.
    Deterministic dedupe by (min_id,max_id); truncated to max_pairs.
    """
    seen: set[tuple[str, str]] = set()
    scored: list[tuple[float, float, dict]] = []
    with neo4j_session() as session:
        fetch = """
        MATCH (e:Entity)
        WHERE e.domain IN $domains AND e.embedding IS NOT NULL AND e.name IS NOT NULL
          AND ($nameFilter IS NULL OR toLower(e.name) CONTAINS toLower($nameFilter))
        RETURN e.id AS id, e.name AS name, e.entity_class AS entity_class,
               e.domain AS domain, e.embedding AS embedding
        """
        sources = session.run(
            fetch, domains=domains, nameFilter=name_filter
        ).data()
        for src in sources:
            others = [d for d in domains if d != src["domain"]] or domains
            neighbors = session.run(
                """
                CALL db.index.vector.queryNodes('entity_embedding', $k, $embedding)
                YIELD node, score
                WHERE node.domain IN $others
                  AND node.entity_class = $cls
                  AND node.id <> $srcId
                RETURN node.id AS id, node.name AS name, node.domain AS domain, score
                """,
                k=top_k, embedding=src["embedding"], others=others,
                cls=src["entity_class"], srcId=src["id"],
            ).data()
            for nb in neighbors:
                if nb["score"] < sim_threshold:
                    continue
                key = tuple(sorted([src["id"], nb["id"]]))
                if key in seen:
                    continue
                seen.add(key)
                nr = _name_ratio(src["name"], nb["name"])
                scored.append((
                    nb["score"], nr,
                    {
                        "id_a": src["id"], "name_a": src["name"], "domain_a": src["domain"],
                        "id_b": nb["id"], "name_b": nb["name"], "domain_b": nb["domain"],
                        "entity_class": src["entity_class"],
                        "sim": round(nb["score"], 4), "name_ratio": round(nr, 4),
                    },
                ))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item[2] for item in scored[:max_pairs]]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_conflicts.py -k "conflict_id or name_ratio" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/conflicts.py tests/test_conflicts.py
git commit -m "feat: conflicts candidate pairing via class-block + ANN + difflib tie-break

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.4: `conflicts.py` — evidence, adjudication, materialization, orchestrator

**Files:**
- Modify: `artmind/conflicts.py`
- Test: `tests/test_conflicts.py`

- [ ] **Step 1: Write failing tests for the JSON parsing of a verdict**

Append to `tests/test_conflicts.py`:

```python
from artmind.conflicts import _verdict_from_raw


def test_verdict_conflicting_claims():
    raw = '{"verdict":"conflicting_claims","aspect":"fee reversal approval limit","claim_a":"CEO >£500","claim_b":"Manager £1,000","severity":"high"}'
    v = _verdict_from_raw(raw)
    assert v["verdict"] == "conflicting_claims"
    assert v["severity"] == "high"


def test_verdict_defaults_to_unrelated_on_garbage():
    v = _verdict_from_raw("not json at all")
    assert v["verdict"] == "unrelated"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_conflicts.py -k verdict -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the rest of `conflicts.py`**

Append to `artmind/conflicts.py`:

```python
_ADJUDICATE_PROMPT = """You are a conflict-detection assistant for a knowledge graph.
Two entities from different domains may describe the same real-world thing and may
make contradictory quantitative or authority claims.

ENTITY A ({domain_a}) — {name_a}
Evidence A:
{evidence_a}

ENTITY B ({domain_b}) — {name_b}
Evidence B:
{evidence_b}

Decide the relationship. Return ONLY JSON with these keys:
- "verdict": one of "same_entity_consistent" | "conflicting_claims" | "unrelated"
- "aspect": short phrase naming the disputed dimension (e.g. "fee reversal approval limit")
- "claim_a": A's specific claim on that aspect (short)
- "claim_b": B's specific claim on that aspect (short)
- "severity": "high" | "medium" | "low"
Only return "conflicting_claims" when the two claims genuinely cannot both be true.
JSON only:"""


def gather_evidence(session, entity_id: str, max_chunks: int) -> list[dict]:
    """Top-k MENTIONS chunks for an entity, truncated for bounded LLM cost."""
    return session.run(
        """
        MATCH (c:DocChunk)-[:MENTIONS]->(e:Entity {id:$id})
        RETURN c.id AS id, c.doc_id AS doc_id, c.name AS name, c.domain AS domain,
               left(c.text, 1200) AS text
        LIMIT $k
        """,
        id=entity_id, k=max_chunks,
    ).data()


def _verdict_from_raw(raw: str) -> dict:
    default = {"verdict": "unrelated", "aspect": "", "claim_a": "", "claim_b": "", "severity": "low"}
    try:
        parsed = _parse_json_response(raw)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            return default
        v = parsed.get("verdict", "unrelated")
        if v not in ("same_entity_consistent", "conflicting_claims", "unrelated", "superseded"):
            v = "unrelated"
        return {
            "verdict": v,
            "aspect": str(parsed.get("aspect", "")),
            "claim_a": str(parsed.get("claim_a", "")),
            "claim_b": str(parsed.get("claim_b", "")),
            "severity": parsed.get("severity", "low") if parsed.get("severity") in ("high", "medium", "low") else "low",
        }
    except Exception:
        return default


def llm_adjudicate(pair: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> dict:
    prompt = _ADJUDICATE_PROMPT.format(
        domain_a=pair["domain_a"], name_a=pair["name_a"],
        domain_b=pair["domain_b"], name_b=pair["name_b"],
        evidence_a="\n".join(f"- {c['text']}" for c in evidence_a) or "(none)",
        evidence_b="\n".join(f"- {c['text']}" for c in evidence_b) or "(none)",
    )
    try:
        raw = _call_llm_text(model, prompt)
    except Exception as e:
        logger.warning("adjudicate LLM failed for {}: {}", pair.get("aspect"), e)
        return {"verdict": "unrelated", "aspect": "", "claim_a": "", "claim_b": "", "severity": "low"}
    return _verdict_from_raw(raw)


def materialize(session, pair: dict, verdict: dict, evidence_a: list[dict], evidence_b: list[dict], model: str) -> str | None:
    """MERGE-only write of a Conflict + edges. Returns conflict id or None."""
    if verdict["verdict"] != "conflicting_claims":
        return None
    cid = conflict_id(pair["id_a"], pair["id_b"], verdict["aspect"] or pair["entity_class"])
    domains = sorted({pair["domain_a"], pair["domain_b"]})
    session.run(
        """
        MERGE (co:Conflict {id:$id})
        ON CREATE SET co.aspect=$aspect, co.claim_a=$claim_a, co.claim_b=$claim_b,
                      co.severity=$severity, co.status='open', co.domains=$domains,
                      co.detected_at=$now, co.detected_by_model=$model
        WITH co
        MATCH (a:Entity {id:$idA}), (b:Entity {id:$idB})
        MERGE (co)-[:CONFLICT_OF]->(a)
        MERGE (co)-[:CONFLICT_OF]->(b)
        MERGE (a)-[ra:CONFLICTS_WITH]->(b) SET ra.conflict_id=$id, ra.aspect=$aspect
        MERGE (b)-[rb:CONFLICTS_WITH]->(a) SET rb.conflict_id=$id, rb.aspect=$aspect
        """,
        id=cid, aspect=verdict["aspect"], claim_a=verdict["claim_a"], claim_b=verdict["claim_b"],
        severity=verdict["severity"], domains=domains,
        now=datetime.now(timezone.utc).isoformat(), model=model,
        idA=pair["id_a"], idB=pair["id_b"],
    )
    for side, chunks in (("a", evidence_a), ("b", evidence_b)):
        for c in chunks:
            session.run(
                """
                MATCH (co:Conflict {id:$id}), (c:DocChunk {id:$cid})
                MERGE (co)-[e:EVIDENCE {side:$side}]->(c)
                """,
                id=cid, cid=c["id"], side=side,
            )
    return cid


def detect_conflicts(
    domains: list[str],
    name_filter: str | None = None,
    sim_threshold: float = 0.75,
    max_pairs: int = 200,
    max_chunks_per_side: int = 2,
    model: str = "",
    dry_run: bool = False,
    output_file: Path | None = None,
    from_file: Path | None = None,
) -> dict:
    """Two-phase orchestrator mirroring refine-graph's dry-run/apply workflow."""
    report: dict = {"domains": domains, "candidates": 0, "llm_calls": 0,
                    "conflicts": [], "stats": {}, "candidate_seconds": 0.0, "llm_seconds": 0.0}

    if from_file:
        data = json.loads(Path(from_file).read_text(encoding="utf-8"))
        with neo4j_session() as session:
            for item in data.get("conflicts", []):
                cid = materialize(session, item["pair"], item["verdict"],
                                  item["evidence_a"], item["evidence_b"], item.get("model", model))
                if cid:
                    report["conflicts"].append(cid)
        report["stats"] = {"materialized": len(report["conflicts"])}
        return report

    import time
    # Precondition: warn if a target domain has no recorded refine-graph run.
    with neo4j_session() as session:
        missing = check_refine_precondition(session, domains)
    if missing:
        logger.warning(
            "detect-conflicts: no refine-graph run recorded for {} — run intra-domain "
            "refine-graph first, or candidate pairing will operate on raw chunk-level duplicates",
            missing,
        )
        report["warning_missing_refine"] = missing

    t0 = time.monotonic()
    pairs = candidate_pairs(domains, name_filter, sim_threshold, max_pairs)
    report["candidate_seconds"] = round(time.monotonic() - t0, 3)
    report["candidates"] = len(pairs)

    t1 = time.monotonic()
    proposals: list[dict] = []
    with neo4j_session() as session:
        for pair in pairs:
            ev_a = gather_evidence(session, pair["id_a"], max_chunks_per_side)
            ev_b = gather_evidence(session, pair["id_b"], max_chunks_per_side)
            verdict = llm_adjudicate(pair, ev_a, ev_b, model)
            report["llm_calls"] += 1
            if verdict["verdict"] == "conflicting_claims":
                proposals.append({"pair": pair, "verdict": verdict,
                                  "evidence_a": ev_a, "evidence_b": ev_b, "model": model})
    report["llm_seconds"] = round(time.monotonic() - t1, 3)
    report["proposals"] = proposals

    if output_file:
        Path(output_file).write_text(
            json.dumps({"domains": domains, "conflicts": proposals}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    if not dry_run and proposals:
        with neo4j_session() as session:
            for item in proposals:
                cid = materialize(session, item["pair"], item["verdict"],
                                  item["evidence_a"], item["evidence_b"], model)
                if cid:
                    report["conflicts"].append(cid)
        report["stats"] = {"materialized": len(report["conflicts"])}

    logger.info(
        "detect-conflicts: candidates={} llm_calls={} materialized={} (cand={}s llm={}s)",
        report["candidates"], report["llm_calls"], len(report["conflicts"]),
        report["candidate_seconds"], report["llm_seconds"],
    )
    return report
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_conflicts.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add artmind/conflicts.py tests/test_conflicts.py
git commit -m "feat: conflict evidence/adjudication/materialization + orchestrator

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.5: CLI — `ingest detect-conflicts` + `query graph conflicts`

**Files:**
- Modify: `artmind/cli.py`

- [ ] **Step 1: Register `ingest detect-conflicts`**

After `ingest_normalize_time`, add:

```python
@ingest.command("detect-conflicts")
@click.option("--domain", "domain", required=True, multiple=True, help="Target domain(s) (repeatable; 1=intra-domain, 2+=cross-domain)")
@click.option("--nameFilter", "name_filter", default=None, help="Restrict to entities whose name contains this")
@click.option("--simThreshold", "sim_threshold", type=float, default=0.75, show_default=True, help="Min cosine similarity for a candidate")
@click.option("--maxPairs", "max_pairs", type=int, default=200, show_default=True, help="Hard cap on candidate pairs (bounds LLM cost)")
@click.option("--maxChunksPerSide", "max_chunks", type=int, default=2, show_default=True, help="Evidence chunks per side")
@click.option("--model", default=None, help="Adjudication LLM model (default: env)")
@click.option("--dry-run", is_flag=True, help="Compute proposals + write --output; do NOT materialize")
@click.option("--output", "output_file", default=None, type=click.Path(), help="Write proposals JSON here")
@click.option("--from-file", "from_file", default=None, type=click.Path(exists=True), help="Materialize proposals from a prior dry-run file")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_detect_conflicts(domain, name_filter, sim_threshold, max_pairs, max_chunks, model, dry_run, output_file, from_file, compact):
    """Detect non-destructive conflicts between entities across domains.

    \b
    Precondition: run intra-domain `refine-graph` (no --allow-cross-domain-merge)
    on each target domain first, so pairing operates on deduplicated entities.
    Workflow:
      1. artmind ingest detect-conflicts --domain A --domain B --dry-run --output conflicts.json
      2. Review conflicts.json
      3. artmind ingest detect-conflicts --domain A --domain B --from-file conflicts.json
    """
    _setup_logger()
    from artmind.conflicts import detect_conflicts
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    domains = _parse_domains(domain)
    report = detect_conflicts(
        domains=domains, name_filter=name_filter, sim_threshold=sim_threshold,
        max_pairs=max_pairs, max_chunks_per_side=max_chunks, model=resolved_model,
        dry_run=dry_run, output_file=Path(output_file) if output_file else None,
        from_file=Path(from_file) if from_file else None,
    )
    _echo_json(report, compact)
```

- [ ] **Step 2: Register `query graph conflicts`**

After `graph_text2cypher` (line ~948), add under the `graph` group:

```python
@graph.command("conflicts")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s) (repeatable)")
@click.option("--entityId", "entity_id", multiple=True, help="Filter to conflicts touching this entity id (repeatable)")
@click.option("--entityName", "entity_name", default=None, help="Filter to conflicts touching an entity whose name contains this")
@click.option("--status", type=click.Choice(["open", "resolved", "dismissed", "all"]), default="open", show_default=True)
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_conflicts(domain, entity_id, entity_name, status, compact):
    """List materialized Conflict nodes scoped to the given domains."""
    domains = _parse_domains(domain)
    _echo_json(
        graph_query.list_conflicts(domains, entity_ids=list(entity_id), entity_name=entity_name, status=status),
        compact,
    )
```

- [ ] **Step 3: Verify registration**

Run: `uv run artmind ingest detect-conflicts --help` and `uv run artmind query graph conflicts --help`
Expected: both show their options; `detect-conflicts` shows `--dry-run`, `--from-file`, `--simThreshold`.

- [ ] **Step 4: Commit**

```bash
git add artmind/cli.py
git commit -m "feat: ingest detect-conflicts + query graph conflicts commands

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.6: text2cypher Conflict schema + SKILL.md materialized Adjudicate + no-hook test

**Files:**
- Modify: `artmind/text2cypher.py`, `skills/artmind-query/SKILL.md`, `tests/test_ingest_hooks.py`

- [ ] **Step 1: Extend `STRUCTURAL_SCHEMA`**

In `text2cypher.py`, add to `STRUCTURAL_SCHEMA`:

```
  Node :Conflict  properties=[id, aspect, claim_a, claim_b, severity, status, domains, detected_at]
  Relationship (:Conflict)-[:CONFLICT_OF]->(:Entity)      — both sides of a conflict
  Relationship (:Conflict)-[:EVIDENCE {side}]->(:DocChunk) — competing claim text
  Relationship (:Entity)-[:CONFLICTS_WITH {conflict_id, aspect}]->(:Entity)
```

- [ ] **Step 2: SKILL.md — Adjudicate consults materialized conflicts**

In the Adjudicate step, prepend:

```markdown
First check for already-materialized conflicts on the resolved entity ids:

```bash
uv run artmind query graph conflicts --domain <d1> --domain <d2> --entityId <id> --compact
```

If a `Conflict` exists, surface its `claim_a`/`claim_b` with their EVIDENCE
provenance. Then independently compare the retrieved claims (below) to catch
conflicts introduced by new documents since the last detect-conflicts run.
```

- [ ] **Step 3: Strengthen the no-hook test**

Append to `tests/test_ingest_hooks.py`:

```python
def test_ingest_sync_and_async_do_not_auto_detect_conflicts():
    import artmind.cli as cli
    src = inspect.getsource(cli.ingest_sync) + inspect.getsource(cli.ingest_async)
    assert "detect_conflicts" not in src
    assert "detect-conflicts" not in src
    assert "refine_graph(" not in src
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ingest_hooks.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/text2cypher.py skills/artmind-query/SKILL.md tests/test_ingest_hooks.py
git commit -m "feat: text2cypher Conflict schema, skill materialized-conflict adjudicate, no-hook test

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2.7: End-to-end conflict pipeline verification (cross-domain conflict)

**Files:** (verification — no code changes)

- [ ] **Step 1: Intra-domain refine (precondition)**

Run:
```bash
uv run artmind ingest refine-graph --domain banking_policy --dry-run --output /tmp/rp.json
uv run artmind ingest refine-graph --domain banking_policy --from-file /tmp/rp.json
uv run artmind ingest refine-graph --domain banking_sop_guides --dry-run --output /tmp/rs.json
uv run artmind ingest refine-graph --domain banking_sop_guides --from-file /tmp/rs.json
```
Expected: each apply prints merge stats; `RefineRun` markers now exist for both domains.

- [ ] **Step 2: Dry-run detect-conflicts (no precondition warning)**

Run:
```bash
uv run artmind ingest detect-conflicts --domain banking_policy --domain banking_sop_guides --dry-run --output /tmp/conflicts.json --compact
```
Expected: JSON with `candidates < 50`, `llm_calls == candidates`, `candidate_seconds` and `llm_seconds` reported separately, and NO `warning_missing_refine`. `/tmp/conflicts.json` contains a fee-reversal-authority proposal with `claim_a`≈policy tiers (>£500 CEO) and `claim_b`≈matrix (Manager £1,000).

- [ ] **Step 3: Apply and read back**

Run:
```bash
uv run artmind ingest detect-conflicts --domain banking_policy --domain banking_sop_guides --from-file /tmp/conflicts.json --compact
uv run artmind query graph conflicts --domain banking_policy --domain banking_sop_guides --compact
```
Expected: `conflicts` returns the fee-reversal conflict with EVIDENCE chunks from both `policy_complaints.md` and `escalation_matrix.md`.

- [ ] **Step 4: Idempotency + cost/scale regression**

Re-run Step 3's `--from-file`: expect no new Conflict ids (deterministic id → MERGE). Then benchmark candidate generation independent of LLM:
```bash
uv run artmind ingest detect-conflicts --domain banking_policy --domain banking_sop_guides --maxPairs 0 --dry-run --compact
```
Expected: `candidate_seconds` printed and small; confirms ANN pairing, not brute force, even against the full corpus.

- [ ] **Step 5: Merge-guard check**

Run `uv run artmind ingest refine-graph --dry-run --output /tmp/all.json` (no `--domain`, no flag): expect a same-named "Fee Reversal" cross-domain cluster reported under `skipped_cross_domain`. Record results in the execution log.

---

# Phase T2 — Supersession + `superseded` Verdict

## Task T2.1: `SUPERSEDES` model + `apply_supersession` in `temporal.py`

**Files:**
- Modify: `artmind/temporal.py`
- Test: `tests/test_supersession.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_supersession.py`:

```python
"""Supersession application + detection unit tests."""
import inspect
import artmind.temporal as t


def test_apply_supersession_signature():
    sig = inspect.signature(t.apply_supersession)
    for p in ("newer_doc_id", "older_doc_id", "scope", "effective"):
        assert p in sig.parameters


def test_detect_supersession_notice_parses_version():
    md = (
        "## Supersession Notice\n\n"
        "This document (Version 3.0) supersedes Version 2.0 "
        "(Effective Date: 2026-01-15), effective 2026-06-01.\n"
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"


def test_detect_supersession_notice_parses_intervening_words():
    # Real fixture phrasing (banking_document_corpus/policies/policy_complaints_v3.md,
    # line 24): "supersedes and replaces Version 2.0" — words between "supersedes"
    # and "Version" broke a tight `supersedes?\s+Version` regex during plan review
    # (verified by running that regex against the actual file: no match). This test
    # locks in the real phrasing so a regression can't reintroduce the tight version.
    md = (
        "**This policy (Version 3.0, effective 2026-06-01) supersedes and replaces "
        "Version 2.0 (effective 2026-01-15) in full.**"
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"


def test_detect_supersession_notice_ignores_metadata_table_dates():
    # Reproduces a second real bug found during plan review: the actual document
    # body includes a "## Metadata" table BEFORE the "## Supersession Notice"
    # section, and that table's "| Supersedes | Version 2.0 (Effective Date
    # 2026-01-15) |" row also contains the words "Supersedes" and "Effective Date".
    # An unscoped whole-body regex search picks up THAT date (2026-01-15, the OLD
    # version's date) instead of the Supersession Notice section's own date
    # (2026-06-01) — verified by running the whole-document search against the
    # real fixture file: it returned 2026-01-15, the wrong value. Parsing must be
    # scoped to the "## Supersession Notice" section, not the whole document body.
    md = (
        "## Metadata\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Version | 3.0 |\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Supersedes | Version 2.0 (Effective Date 2026-01-15) |\n\n"
        "## Supersession Notice\n\n"
        "**This policy (Version 3.0, effective 2026-06-01) supersedes and replaces "
        "Version 2.0 (effective 2026-01-15) in full.**\n\n"
        "## Executive Summary\n\nBody."
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_supersession.py -v`
Expected: FAIL with `AttributeError`/`ImportError`.

- [ ] **Step 3: Implement supersession application + notice parsing**

Append to `artmind/temporal.py`:

```python
_SUPERSEDES_VER_RE = re.compile(r"supersedes?.*?Version\s+([0-9.]+)", re.IGNORECASE)
_EFFECTIVE_RE = re.compile(r"effective(?:\s+Date)?[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4})", re.IGNORECASE)
_NOTICE_SECTION_RE = re.compile(r"##\s*Supersession Notice\s*\n(.*?)(?=\n##|\n---|\Z)", re.IGNORECASE | re.DOTALL)


def parse_supersession_notice(md_text: str) -> dict | None:
    """Parse an explicit 'Supersession Notice' section for the superseded version + effective date.

    Scoped to the "## Supersession Notice" section when present, NOT the whole
    document body. Verified against the real fixture (banking_document_corpus/
    policies/policy_complaints_v3.md): the document's own "## Metadata" table
    contains an earlier "| Supersedes | Version 2.0 (Effective Date 2026-01-15) |"
    row — an unscoped whole-body search matches that row's OLD date instead of the
    Supersession Notice section's actual effective date. Falls back to the whole
    body only when no such heading exists, so hand-written notices without the
    exact heading still parse.
    """
    section_match = _NOTICE_SECTION_RE.search(md_text)
    scope = section_match.group(1) if section_match else md_text
    m = _SUPERSEDES_VER_RE.search(scope)
    if not m:
        return None
    eff = None
    em = _EFFECTIVE_RE.search(scope)
    if em:
        eff = parse_iso(em.group(1))
    return {"superseded_version": m.group(1).strip(), "effective": eff}


def apply_supersession(
    newer_doc_id: str,
    older_doc_id: str,
    scope: str = "document",
    effective: str | None = None,
    detected_by: str = "manual",
) -> dict:
    """Create (:Document)-[:SUPERSEDES]->(:Document) and set valid_to on the older side.

    Document scope also stamps valid_to on the older document's chunks — this is
    what makes --asOf queries exclude stale content automatically. Idempotent.
    """
    with neo4j_session() as session:
        session.run(
            """
            MATCH (newer:Document {id:$newer}), (older:Document {id:$older})
            MERGE (newer)-[s:SUPERSEDES {scope:$scope}]->(older)
            SET s.effective=$effective, s.detected_by=$detectedBy
            SET older.valid_to = coalesce($effective, older.valid_to),
                older.superseded_by = newer.id
            """,
            newer=newer_doc_id, older=older_doc_id, scope=scope,
            effective=effective, detectedBy=detected_by,
        )
        if scope == "document" and effective:
            session.run(
                "MATCH (c:DocChunk {doc_id:$older}) SET c.valid_to = coalesce($effective, c.valid_to)",
                older=older_doc_id, effective=effective,
            )
    logger.info("supersession: {} supersedes {} (scope={}, effective={})", newer_doc_id, older_doc_id, scope, effective)
    return {"newer": newer_doc_id, "older": older_doc_id, "scope": scope, "effective": effective}


def detect_supersession(domain: str, dry_run: bool = False) -> dict:
    """Scan each document's markdown for an explicit Supersession Notice and apply it.

    Matches the superseded Version against another Document in the same domain
    (via lifted `version`). Additive; safe to re-run.
    """
    report: dict = {"domain": domain, "applied": [], "dry_run": dry_run}
    with neo4j_session() as session:
        docs = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain RETURN d.id AS id, d.name AS name, d.version AS version",
            domain=domain,
        ).data()
    by_version = {str(d["version"]): d for d in docs if d.get("version")}
    for d in docs:
        md_file = MARKDOWNS_DIR / f"{Path(d['name']).stem}.md"
        if not md_file.exists():
            continue
        from artmind.ingest import _parse_md_frontmatter
        _, body = _parse_md_frontmatter(md_file.read_text(encoding="utf-8"))
        notice = parse_supersession_notice(body)
        if not notice:
            continue
        older = by_version.get(notice["superseded_version"])
        if not older or older["id"] == d["id"]:
            continue
        report["applied"].append({"newer": d["id"], "older": older["id"], "effective": notice["effective"]})
        if not dry_run:
            apply_supersession(d["id"], older["id"], "document", notice["effective"], detected_by="notice")
    return report
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_supersession.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/temporal.py tests/test_supersession.py
git commit -m "feat: SUPERSEDES model, apply_supersession, notice detection

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T2.2: `superseded` verdict in `conflicts.py` (depends on Phase 2 `conflicts.py`)

**Files:**
- Modify: `artmind/conflicts.py`
- Test: `tests/test_conflicts.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_conflicts.py`:

```python
def test_verdict_superseded_recognized():
    from artmind.conflicts import _verdict_from_raw
    raw = '{"verdict":"superseded","aspect":"x","claim_a":"old","claim_b":"new","severity":"low"}'
    assert _verdict_from_raw(raw)["verdict"] == "superseded"


def test_materialize_superseded_creates_supersedes_not_conflict(monkeypatch):
    import artmind.conflicts as c
    calls = {"supersede": 0}
    def fake_apply(newer_doc_id, older_doc_id, scope="document", effective=None, detected_by="adjudicator"):
        calls["supersede"] += 1
        return {}
    monkeypatch.setattr(c, "apply_supersession", fake_apply, raising=False)
    # verdict=superseded must route to supersession, returning None for Conflict id
    class FakeSession:
        def run(self, *a, **k):
            class R:
                def single(self_inner): return {"a": "docA", "b": "docB"}
                def data(self_inner): return [{"a": "docA", "b": "docB"}]
            return R()
    pair = {"id_a": "eA", "id_b": "eB", "domain_a": "d", "domain_b": "d", "entity_class": "POLICY",
            "name_a": "x", "name_b": "x"}
    verdict = {"verdict": "superseded", "aspect": "x", "claim_a": "old", "claim_b": "new", "severity": "low"}
    cid = c.materialize(FakeSession(), pair, verdict, [], [], "m")
    assert cid is None
    assert calls["supersede"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_conflicts.py -k superseded -v`
Expected: FAIL (`materialize` currently returns None without calling supersession).

- [ ] **Step 3: Route the `superseded` verdict in `materialize`**

In `conflicts.py`, add the import at top: `from artmind.temporal import apply_supersession`. Extend the adjudicate prompt to pass document validity so the model can choose `superseded`: in `_ADJUDICATE_PROMPT`, add a line before "Decide the relationship":

```
Document A valid_from={valid_from_a} version={version_a}; Document B valid_from={valid_from_b} version={version_b}.
If one side is a NEWER REVISION OF THE SAME AUTHORITY (same document lineage, later
valid_from/version), return verdict "superseded" instead of "conflicting_claims".
```

Add those four keys to the `.format(...)` call in `llm_adjudicate`, sourcing them from the evidence chunks' documents (query `Document.valid_from`/`version` for each side inside `gather_evidence` or a small lookup; pass empty strings when unknown). At the top of `materialize`, handle the new verdict:

```python
    if verdict["verdict"] == "superseded":
        # Resolve entity ids to their documents and record SUPERSEDES + valid_to.
        rec = session.run(
            """
            MATCH (a:Entity {id:$idA})<-[:MENTIONS]-(:DocChunk)-[:PART_OF]->(da:Document)
            MATCH (b:Entity {id:$idB})<-[:MENTIONS]-(:DocChunk)-[:PART_OF]->(db:Document)
            RETURN da.id AS a, da.valid_from AS af, db.id AS b, db.valid_from AS bf
            LIMIT 1
            """,
            idA=pair["id_a"], idB=pair["id_b"],
        ).single()
        if rec and rec["a"] and rec["b"]:
            # newer = later valid_from
            if (rec["bf"] or "") >= (rec["af"] or ""):
                apply_supersession(rec["b"], rec["a"], "document", rec["bf"], detected_by="adjudicator")
            else:
                apply_supersession(rec["a"], rec["b"], "document", rec["af"], detected_by="adjudicator")
        return None
    if verdict["verdict"] != "conflicting_claims":
        return None
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_conflicts.py -k superseded -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add artmind/conflicts.py tests/test_conflicts.py
git commit -m "feat: superseded adjudicator verdict routes to SUPERSEDES not Conflict

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T2.3: CLI — `ingest supersede` (manual) + `query graph timeline`

**Files:**
- Modify: `artmind/cli.py`, `artmind/graph_query.py`

- [ ] **Step 1: Add `list_timeline` to `graph_query.py`**

Append:

```python
def list_timeline(domains: "str | Sequence[str]", entity_id: str) -> dict:
    """An entity's events/state-changes/supersessions ordered by event_at/valid_from."""
    domains = normalize_domains(domains)
    cypher = f"""
    MATCH (e:Entity {{id:$entityId}})
    WHERE {domain_predicate("e")}
    OPTIONAL MATCH (e)-[r]-(rel:Entity)
    WITH e, collect(DISTINCT {{
        type: type(r), name: rel.name,
        event_at: rel.event_at, valid_from: rel.valid_from, valid_to: rel.valid_to
    }}) AS related
    RETURN e {{ .id, .name, .entity_class, .event_at, .valid_from, .valid_to }} AS entity,
           [x IN related WHERE x.event_at IS NOT NULL OR x.valid_from IS NOT NULL] AS timeline
    """
    rows = _run_read_query(cypher, {"domains": domains, "entityId": entity_id})
    for row in rows:
        row["timeline"] = sorted(
            row.get("timeline", []),
            key=lambda x: (x.get("event_at") or x.get("valid_from") or ""),
        )
    return {**_domain_output(domains), "query_type": "graph", "command": "timeline", "rows": rows}
```

- [ ] **Step 2: Register `query graph timeline`**

```python
@graph.command("timeline")
@click.option("--domain", "domain", required=True, multiple=True, help="Domain(s)")
@click.option("--entityId", "entity_id", required=True, help="Entity id whose timeline to render")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def graph_timeline(domain, entity_id, compact):
    """Events/state-changes/supersessions for an entity, ordered by time."""
    domains = _parse_domains(domain)
    _echo_json(graph_query.list_timeline(domains, entity_id), compact)
```

- [ ] **Step 3: Register `ingest supersede` + `ingest detect-supersession`**

```python
@ingest.command("supersede")
@click.option("--domain", required=True, help="Domain of both documents")
@click.option("--newer", "newer_name", required=True, help="Newer document name")
@click.option("--older", "older_name", required=True, help="Superseded document name")
@click.option("--scope", type=click.Choice(["document", "section", "clause"]), default="document", show_default=True)
@click.option("--effective", default=None, help="ISO date the supersession takes effect")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_supersede(domain, newer_name, older_name, scope, effective, compact):
    """Manually assert that one document supersedes another (sets SUPERSEDES + valid_to)."""
    _setup_logger()
    from artmind.temporal import apply_supersession
    from artmind.graph_query import neo4j_session
    with neo4j_session() as session:
        ids = session.run(
            "MATCH (d:Document) WHERE d.domain=$domain AND d.name IN [$n,$o] RETURN d.name AS name, d.id AS id",
            domain=domain, n=newer_name, o=older_name,
        ).data()
    by_name = {r["name"]: r["id"] for r in ids}
    if newer_name not in by_name or older_name not in by_name:
        raise click.ClickException(f"Could not resolve both documents in domain {domain}: found {list(by_name)}")
    _echo_json(apply_supersession(by_name[newer_name], by_name[older_name], scope, effective), compact)


@ingest.command("detect-supersession")
@click.option("--domain", required=True, help="Domain to scan for explicit Supersession Notice sections")
@click.option("--dry-run", is_flag=True, help="Report matches without writing")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def ingest_detect_supersession(domain, dry_run, compact):
    """Scan documents for explicit Supersession Notice sections and apply SUPERSEDES edges."""
    _setup_logger()
    from artmind.temporal import detect_supersession
    _echo_json(detect_supersession(domain, dry_run=dry_run), compact)
```

- [ ] **Step 4: Verify registration**

Run: `uv run artmind query graph timeline --help` and `uv run artmind ingest supersede --help` and `uv run artmind ingest detect-supersession --help`
Expected: all three print help.

- [ ] **Step 5: Commit**

```bash
git add artmind/cli.py artmind/graph_query.py
git commit -m "feat: query graph timeline + ingest supersede/detect-supersession

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T2.4: text2cypher SUPERSEDES schema + SKILL.md time-qualified Adjudicate

**Files:**
- Modify: `artmind/text2cypher.py`, `skills/artmind-query/SKILL.md`

- [ ] **Step 1: Add SUPERSEDES to `STRUCTURAL_SCHEMA`**

```
  Relationship (:Document)-[:SUPERSEDES {scope, effective}]->(:Document)  — newer replaces older
  Timed nodes carry valid_from/valid_to; superseded docs also carry superseded_by.
```

- [ ] **Step 2: SKILL.md — time-qualified Adjudicate**

In the Adjudicate step, add:

```markdown
Qualify claims by time: report present-tense answers "as of <date>, source A says X".
A claim whose document is superseded (has `superseded_by` / a `valid_to` in the past)
is HISTORY, not a live disagreement — say so. A conflict is genuine only when both
documents' valid-time intervals overlap and neither supersedes the other.
```

- [ ] **Step 3: Verify**

Run: `grep -c "SUPERSEDES\|superseded" artmind/text2cypher.py skills/artmind-query/SKILL.md`
Expected: nonzero in both.

- [ ] **Step 4: Commit**

```bash
git add artmind/text2cypher.py skills/artmind-query/SKILL.md
git commit -m "docs: text2cypher SUPERSEDES schema + skill time-qualified adjudicate

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task T2.5: End-to-end supersession + fixture ingestion (paired assertion)

**Files:** (verification — no code changes)

- [ ] **Step 1: Ingest the v3 fixture**

Run:
```bash
uv run artmind ingest sync banking_document_corpus/policies/policy_complaints_v3.md --domain banking_policy
```
Expected: ingest completes ok; the auto normalize-time hook fires, lifting `Document.valid_from = 2026-06-01`, `version = 3.0`. Confirm:
```bash
uv run artmind query graph pattern10 --domain banking_policy --documentName "policy_complaints_v3" --compact
```
shows the document with `valid_from` `2026-06-01`.

- [ ] **Step 2: Detect + apply supersession**

Run:
```bash
uv run artmind ingest detect-supersession --domain banking_policy --dry-run --compact
uv run artmind ingest detect-supersession --domain banking_policy --compact
```
Expected: an `applied` entry with `newer` = v3.0 doc, `older` = v2.0 (`policy_complaints.md`), `effective` `2026-06-01`. After apply, v2.0 has `valid_to = 2026-06-01` and `superseded_by` set; its chunks carry `valid_to`.

- [ ] **Step 3: Assertion A — conflict survives supersession**

Re-run detect-conflicts (Phase 2) including the v3 doc, then:
```bash
uv run artmind query graph conflicts --domain banking_policy --domain banking_sop_guides --status open --compact
```
Expected: the cross-domain fee-reversal conflict (v3.0 vs `escalation_matrix.md`, both currently in force) is STILL an open Conflict — supersession did NOT dismiss it. There is NO open Conflict between v2.0 and v3.0 (same lineage → SUPERSEDES, not Conflict).

- [ ] **Step 4: Assertion B — intra-document inconsistency resolved + asOf currency**

Run:
```bash
uv run artmind query vector-text --domain banking_policy --asOf 2026-07-04 --topK 6 --compact "manager and director thresholds for complaint compensation"
```
Expected: `--asOf 2026-07-04` returns only v3.0's unified thresholds (Manager <£500 / Director £500–2,000 / CEO >£2,000); v2.0's contradictory Escalation-Matrix-vs-Compensation-Framework numbers are excluded (v2.0 `valid_to` in the past). Without `--asOf`, both appear with v2.0 marked superseded.

- [ ] **Step 5: Assertion C — skill acceptance**

Via the artmind-query skill (Route → … → Adjudicate), ask "Who can approve a £700 fee reversal after a customer complaint?" Expected answer: cites both `policy_complaints_v3.md` (banking_policy) and `escalation_matrix.md` (banking_sop_guides) WITH domains, states both threshold schemes, and explicitly flags the contradiction using the mandated both-sides format — never blends. Record all results in the execution log.

---

# Phase 3 — `banking_*` → `banking.*` Migration Tooling (optional/later)

## Task 3.1: Migration script for hierarchical banking rollup

**Files:**
- Create: `scripts/migrate_banking_hierarchy.py`
- Test: `tests/test_domain_predicate.py` (rollup assertion)

- [ ] **Step 1: Write the rollup assertion test**

Append to `tests/test_domain_predicate.py`:

```python
def test_domain_predicate_rolls_up_subdomains_semantics():
    # A one-element list "banking" must match "banking.policy" via STARTS WITH.
    pred = domain_predicate("e")
    assert "STARTS WITH (d + '.')" in pred
```

Run: `uv run pytest tests/test_domain_predicate.py -k rolls_up -v`
Expected: PASS.

- [ ] **Step 2: Create the migration script**

Create `scripts/migrate_banking_hierarchy.py`:

```python
#!/usr/bin/env python3
"""Optional migration: rename flat banking_* domains to hierarchical banking.* so
the existing STARTS WITH rollup lets `--domain banking` span all siblings.

Renames the .domain property on Document/DocChunk/UserChat/Entity/Conflict nodes
and moves schema files. Idempotent; dry-run by default.

  uv run python scripts/migrate_banking_hierarchy.py --apply
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from artmind.graph_query import neo4j_session

RENAMES = {
    "banking_policy": "banking.policy",
    "banking_reference": "banking.reference",
    "banking_sop_guides": "banking.sop_guides",
    "banking_products": "banking.products",
    "banking_organization": "banking.organization",
    "banking_communications": "banking.communications",
    "banking_risk_governance": "banking.risk_governance",
}


def migrate(apply: bool) -> None:
    with neo4j_session() as session:
        for old, new in RENAMES.items():
            count = session.run(
                "MATCH (n) WHERE n.domain = $old RETURN count(n) AS c", old=old
            ).single()["c"]
            print(f"  {old} -> {new}: {count} node(s)")
            if apply and count:
                session.run(
                    "MATCH (n) WHERE n.domain = $old SET n.domain = $new",
                    old=old, new=new,
                )
                # Conflict.domains is a list property — update in place.
                session.run(
                    "MATCH (co:Conflict) WHERE $old IN co.domains "
                    "SET co.domains = [d IN co.domains | CASE WHEN d=$old THEN $new ELSE d END]",
                    old=old, new=new,
                )
    print("Applied." if apply else "Dry-run only. Re-run with --apply to write.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    migrate(ap.parse_args().apply)
```

- [ ] **Step 3: Syntax check**

Run: `uv run python -c "import ast; ast.parse(open('scripts/migrate_banking_hierarchy.py').read()); print('syntax OK')"`
Expected: `syntax OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_banking_hierarchy.py tests/test_domain_predicate.py
git commit -m "feat: optional banking_* -> banking.* hierarchy migration script

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

# Phase T3 — State-Change Reification (optional/later)

## Task T3.1: `STATE_CHANGE` guidance in journaling schemas + create-schema temporal step

**Files:**
- Modify: `domains/schemas/personal_journal_schema.yaml`, `domains/schemas/fiction_schema.yaml`, `domains/schemas/project_governance_schema.yaml`
- Modify: `skills/../artmind-create-schema` skill (temporal step)

- [ ] **Step 1: Add STATE_CHANGE entity guidance to personal_journal**

In `personal_journal_schema.yaml` `entities_prompt`, before the EXTRACTION RULES separator, add:

```
  STATE_CHANGE
    A change in an entity's state, status, or relationship over time — extract when
    an entry records that something became different from before (a mood shift, a
    relationship change, a plan starting or ending). Carry the date it happened.
    example type values: mood_shift | relationship_change | plan_started | plan_ended | status_change
```

In `properties_prompt`, before KEY RULES, add:

```
  For STATE_CHANGE, consider:
    - what_changed (the entity or relationship affected)
    - from_state
    - to_state
    - date_or_time (when the change occurred — anchored to the entry date if relative)
```

In `relationships_prompt`, before the separator, add:

```
  STATE_CHANGE ↔ PERSON:
    state_of, affects, involves

  STATE_CHANGE ↔ EVENT:
    triggered_by, resulted_from
```

Add STATE_CHANGE to the schema's `temporal.entities`:

```yaml
    STATE_CHANGE:  { event_at: date_or_time }
```

- [ ] **Step 2: Mirror for fiction and project_governance**

Add an equivalent `STATE_CHANGE` entity/property/relationship block to `fiction_schema.yaml` (fiction §287 already has the multi-edge convention; this gives it a canonical class) and `project_governance_schema.yaml`, and add `STATE_CHANGE: { event_at: date_or_time }` (fiction) / `STATE_CHANGE: { event_at: due_date }` (governance) to each `temporal.entities`.

- [ ] **Step 3: Add a temporal step to `artmind-create-schema`**

In the `artmind-create-schema` skill file, add a step: "Ask about the domain's temporal semantics — which entity classes carry a validity start (`valid_from`), an end (`valid_to`), or an event moment (`event_at`), and what those dates are called in the domain — then emit a `temporal:` block mapping each domain property onto the canonical timeline, plus `relative_anchor` when the domain uses relative dates." Note that `domains harmonize` propagates a parent `temporal:` block to children.

- [ ] **Step 4: Verify schemas parse and carry STATE_CHANGE mapping**

Run:
```bash
uv run python -c "
import yaml
for s in ['personal_journal','fiction','project_governance']:
    d = yaml.safe_load(open(f'domains/schemas/{s}_schema.yaml'))
    assert 'STATE_CHANGE' in d['temporal']['entities'], s
    print(s, 'OK')
"
```
Expected: each prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add domains/schemas/personal_journal_schema.yaml domains/schemas/fiction_schema.yaml domains/schemas/project_governance_schema.yaml
git commit -m "feat: STATE_CHANGE reification guidance + create-schema temporal step

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Journaling timeline verification**

Ingest two journal entries where an entity's state changes, then:
```bash
uv run artmind query graph timeline --domain personal_journal --entityId <id> --compact
```
Expected: ordered state changes with resolved absolute `event_at` (relative "today" anchored to each entry's date). Record results.

---

# Self-Review

**1. Spec coverage — cross-domain/conflicts spec (`2026-07-03-cross-domain-...`):**
- Q1 multi-domain CLI on centralized predicate + `domains-overview` → Tasks 1.1, 1.2, 1.3, 1.6. ✅
- Q2 Discover-as-sub-agent in SKILL.md only → Task 1.7 (Route step, sub-agent rule). ✅
- Q3 materialized `detect-conflicts` (class-block → ANN → difflib) + query-time Adjudicate → Tasks 2.3, 2.4, 2.5, 2.6. ✅
- Data model (`Conflict`, `CONFLICT_OF`, `EVIDENCE`, `CONFLICTS_WITH`) → Task 2.4 `materialize`; constraint/index → Task 2.1. ✅
- CLI surface (repeatable `--domain`, `refine-graph --allow-cross-domain-merge`, `domains-overview`, `graph conflicts`, `detect-conflicts`) → Tasks 1.6, 2.2, 2.5. ✅
- Per-file changes (graph_query/vector_query/text2cypher/cli/refine_graph/setup/conflicts/SKILL) → Tasks 1.2–1.7, 2.1–2.6. ✅
- Scale check + ANN + precondition (`RefineRun`, `check_refine_precondition`, `candidate_seconds`) → Tasks 2.2, 2.3, 2.4, 2.7. ✅
- Pipeline automation: explicit-call-only, no ingest hook → Task 2.6 no-hook test + T1.4 test. ✅
- Verification 1–6 → Tasks 1.2 (regression), 1.6, 2.7, T2.5. ✅

**Spec coverage — temporality spec (`2026-07-03-temporality-design.md`):**
- §3 two timelines + canonical props + `SUPERSEDES` + range indexes → Tasks T1.1, T1.2, T1.3, T2.1. ✅
- §4 `--asOf` central builder, timeline command, pattern10/metadata currency, text2cypher rule → Tasks T1.5, T1.6, T1.8, T2.3. ✅
- §6 `temporal:` block in 7 schemas (banking_sop_guides effective_date/version prompt added) → Task T1.7. ✅
- §7 normalization invoked two ways (auto per-document + standalone backfill) → Tasks T1.3, T1.4, T1.6. ✅
- §8 conflict interaction: `superseded` verdict, resolution path, sharper definition, time-qualified answers → Tasks T2.2, T2.4. ✅
- §9 phasing T1/T2/T3 → Phases T1, T2, T3. ✅
- §10 verification 1–5 → Tasks T1.9, T1.6 (asOf regression), T2.5, T3.1. ✅

**2. Placeholder scan:** No "TBD"/"implement later"/"add error handling"/"similar to Task N" — every code step contains complete code; every schema block is spelled out; every test has real assertions. ✅

**3. Type/signature consistency (cross-task):**
- `normalize_domains(value) -> list[str]`, `domain_predicate(var, param="domains")`, `asof_predicate(var, param="asOf")` — defined Task 1.1/T1.5, used identically everywhere.
- `execute_pattern(domains, pattern, question=None, as_of=None, **parameters)` — Task 1.2 defines, T1.5 adds `as_of`, CLI 1.6/T1.6 calls with `domains=`/`as_of=`.
- `graph_query` read fns all take `domains` (Tasks 1.2/1.3/T2.3) and CLI passes `_parse_domains(domain)` (list) — consistent.
- `conflicts.detect_conflicts(domains, name_filter, sim_threshold, max_pairs, max_chunks_per_side, model, dry_run, output_file, from_file)` — Task 2.4 defines; CLI 2.5 calls with matching kwargs (`max_chunks_per_side=max_chunks`). ✅
- `conflict_id(id_a, id_b, aspect)`, `_name_ratio`, `_verdict_from_raw`, `materialize(session, pair, verdict, evidence_a, evidence_b, model)` — defined Tasks 2.3/2.4, extended (not renamed) in T2.2. ✅
- `temporal.apply_supersession(newer_doc_id, older_doc_id, scope, effective, detected_by)`, `parse_supersession_notice`, `detect_supersession`, `normalize_ingested_document`, `normalize_time`, `canonical_entity_dates`, `parse_iso`, `lift_document_dates` — defined T1.2/T1.3/T2.1, imported by conflicts.py (T2.2) and cli.py (T1.6/T2.3) with matching names. ✅
- `refine_graph(..., allow_cross_domain_merge=False)` — Task 2.2 param added and CLI passes it; `RefineRun` marker consumed by `check_refine_precondition` (Task 2.3). ✅

**Fixes applied inline during review:** `materialize` early-returns `None` for both `superseded` and non-`conflicting_claims` verdicts (Task T2.2) so the Phase-2 signature stays stable when T2 extends it; `list_conflicts` filters `Conflict.domains` (list) with `any(d IN $domains WHERE d IN co.domains)` rather than the scalar `domain_predicate`, matching the list-valued `domains` property written by `materialize`; the Phase-3 migration updates `Conflict.domains` list entries too.

**Post-plan verification (2026-07-04, before execution started):** the two items the planning agent flagged as unverified were checked directly rather than left as assumptions:

1. **Neo4j syntax/semantics** (`CALL () { ... }` UNION subqueries in `domains_overview()`/`graph_metadata()`, list-comprehension `SET` rewrite in the Phase 3 migration) — tested live against the actual project Neo4j instance (Neo4j Kernel 2026.04.0 Enterprise, Cypher 5/25). Both work exactly as written; no plan change needed.
2. **Header-label / supersession-notice parsing against the real fixture** — this uncovered two actual bugs, now fixed in the plan above, not just a casing ambiguity:
   - `_find_header_value()` originally only matched colon-delimited prose (`**Label:** value`); the entire corpus uses `| Field | Value |` markdown tables instead. Running the original regex against `policy_complaints_v3.md` returned no match. Fixed: Task T1.2 Step 3 now tries the table form first, prose second, with a regression test (`test_lift_document_dates_from_metadata_table`) using the real table format.
   - `_SUPERSEDES_VER_RE` originally required "supersedes" immediately followed by "Version"; the real fixture says "supersedes **and replaces** Version 2.0". Fixed with a lazy `.*?` — regression test `test_detect_supersession_notice_parses_intervening_words`.
   - A second, more subtle bug surfaced only by running the fix against the *whole* real document (not an isolated snippet): the document's own `## Metadata` table contains a `| Supersedes | Version 2.0 (Effective Date 2026-01-15) |` row *before* the actual `## Supersession Notice` section, and an unscoped whole-body search for "effective" matched that row's OLD date (2026-01-15) instead of the notice section's real effective date (2026-06-01). Fixed: `parse_supersession_notice()` now isolates the `## Supersession Notice` section first via `_NOTICE_SECTION_RE` and searches only within it, falling back to the whole body only when no such heading exists. Regression test `test_detect_supersession_notice_ignores_metadata_table_dates` locks this in. Verified end-to-end against the actual fixture file: returns `{"superseded_version": "2.0", "effective": "2026-06-01"}`, correctly.

Both fixes are now baked into Tasks T1.2 and T2.1 above, with tests that use real corpus formatting (not synthetic best-case strings), so Tasks T1.9 and T2.5's verification steps will exercise the corrected logic rather than rediscovering these bugs mid-execution.
