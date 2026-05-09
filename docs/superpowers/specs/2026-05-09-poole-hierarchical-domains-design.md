# Design: POOLE+ Entity Standardization & Hierarchical Domains

**Date:** 2026-05-09  
**Status:** Approved

---

## Overview

Two related changes to the artmind schema and domain system:

1. **POOLE+ standardization** — standardize entity classes across all domains using the PERSON, OBJECT, ORGANIZATION, LOCATION, EVENT base set, with domain-specific extras as "+"
2. **Hierarchical domains** — support dot-separated domain names (`fiction.thriller`) where sub-domains inherit parent scope at query time, with a harmonizer CLI to keep schemas in sync

---

## Feature 1: POOLE+ Entity Standardization

### Conservative Renames

Three entity class renames across three domains:

| Domain | Old class | New class |
|---|---|---|
| fiction | CHARACTER | PERSON |
| technical_paper | AUTHOR | PERSON |
| personal_journal | PLACE | LOCATION |

All other domain-specific entity types (METHOD, FINDING, MILESTONE, CLIENT, OFFERING, etc.) are untouched — they remain as "+" extras.

### POOLE Base Types Added to All Domains

Each domain gains the full POOLE set where types are missing:

| Domain | Added POOLE types |
|---|---|
| general | PERSON, OBJECT, ORGANIZATION, LOCATION (EVENT already present) |
| technical_paper | ORGANIZATION, LOCATION, OBJECT |
| fiction | none (all 5 present after rename) |
| personal_journal | OBJECT, ORGANIZATION |
| project_governance | OBJECT, LOCATION |
| sales_collateral | PERSON, OBJECT, ORGANIZATION, LOCATION, EVENT |

### Schema File Changes

All 6 YAML files in `domains/schemas/` are updated:
- Rename entity types in `entities_prompt` text (e.g., `CHARACTER` → `PERSON` in fiction)
- Rename corresponding references in `properties_prompt` text (e.g., `For CHARACTER, consider:` → `For PERSON, consider:` in fiction)
- Add missing POOLE types to `entities_prompt` with appropriate descriptions
- Add the new structured `entity_types` list field (see harmonizer section) to all 6 schemas

### One-Off Migration Script

`scripts/migrate_poole.py` — runs three Cypher statements against the live graph:

```cypher
MATCH (e:Entity {domain: 'fiction', entity_class: 'CHARACTER'})
SET e.entity_class = 'PERSON'

MATCH (e:Entity {domain: 'technical_paper', entity_class: 'AUTHOR'})
SET e.entity_class = 'PERSON'

MATCH (e:Entity {domain: 'personal_journal', entity_class: 'PLACE'})
SET e.entity_class = 'LOCATION'
```

Script connects using the same Neo4j config as the rest of artmind and prints counts of updated nodes per statement. Run once, then discard.

---

## Feature 2: Hierarchical Domains

### Domain Naming Convention

Sub-domains use dot-separated names: `fiction.thriller`, `fiction.historical`, `technical_paper.ml`. Any depth is valid (`a.b.c`), though one level of nesting covers most use cases. A domain with a dot in its name is a child; everything before the last dot is its parent.

### Query-Time Filter Change

All **read queries** expand the domain filter from equality to prefix match:

```cypher
-- Before
WHERE n.domain = $domain

-- After
WHERE n.domain = $domain OR n.domain STARTS WITH ($domain + '.')
```

This means querying `fiction` returns entities tagged `fiction` and `fiction.thriller`, `fiction.historical`, etc. Querying `fiction.thriller` returns only `fiction.thriller` entities.

**Write operations (ingest, update) keep exact equality** — a document ingested into `fiction.thriller` is tagged `fiction.thriller`, not `fiction`. The expanded filter is read-only.

### Affected Files

- `artmind/graph_query.py` — all pattern queries
- `artmind/vector_query.py` — vector and full-text search filters
- `artmind/ingest.py` — read queries only (entity lookup before upsert); write Cyphers unchanged
- `artmind/update.py` — candidate entity search query

### Sub-Domain Schema Files

Sub-domain schemas are complete standalone YAML files:
- Named `{parent}.{child}_schema.yaml` (e.g., `fiction.thriller_schema.yaml`)
- Stored alongside parent schemas in `domains/schemas/`
- The `name` field inside the YAML is the full dotted name (e.g., `fiction.thriller`)
- They duplicate all parent entity types plus add sub-domain extras
- No inheritance logic at load time — schemas load identically to existing flat schemas

### CLI Domain Listing

`artmind domains list` renders the hierarchy indented:

```
fiction
  fiction.thriller
  fiction.historical
technical_paper
  technical_paper.ml
```

---

## Feature 3: Schema Harmonizer

### Command

```
artmind domains harmonize [--domain DOMAIN] [--dry-run]
```

- No `--domain`: checks all child schemas against their respective parents
- `--domain fiction.thriller`: checks only that specific child
- `--dry-run`: prints what would change without modifying any files

### Structured `entity_types` Field

Each schema YAML gains a new top-level field used as the source of truth for diffing:

```yaml
entity_types:
  - PERSON
  - LOCATION
  - OBJECT
  - EVENT
  - ORGANIZATION
  - CONCEPT
```

This field does **not** affect ingestion or LLM prompts — it is metadata for tooling only. Added to all 6 existing schemas as part of this change.

### What Gets Synced

Each entity type has three text blocks across the three prompts. When a parent entity type is missing from a child, the harmonizer copies all three:

| Prompt | Block pattern |
|---|---|
| `entities_prompt` | All-caps entity type header block: `PERSON\n  <description>\n  example type values: ...` |
| `properties_prompt` | `For PERSON, consider:\n  - <property hints>` block |
| `relationships_prompt` | All `PERSON ↔ X:` and `X ↔ PERSON:` lines within the common rel_type section |

Block boundaries are reliably parseable by regex given the consistent formatting in all existing schemas.

### Algorithm

1. Discover child schemas — any schema whose `name` field contains `.`
2. Resolve parent — everything before the last `.` in the child's name
3. Diff `entity_types` lists: find types in parent but absent from child
4. For each missing type:
   a. Extract the entity block from parent `entities_prompt` and append to child `entities_prompt`
   b. Extract the `For TYPE, consider:` block from parent `properties_prompt` and append to child `properties_prompt`
   c. Extract all `TYPE ↔ X:` and `X ↔ TYPE:` rel lines from parent `relationships_prompt` and merge into child `relationships_prompt`
   d. Add the type to child's `entity_types` list
5. Write updated child YAML
6. If parent schema does not exist (orphaned child), report an error and skip

### Constraints

- Harmonizer only **adds** missing parent types to children — never removes child extras
- Copies block text verbatim from parent — no prompt rewriting
- `--dry-run` shows a diff of what would change in each prompt section without writing

### Output

```
fiction.thriller  added entity types: [PERSON, LOCATION]
fiction.historical  already in sync
technical_paper.ml  parent 'technical_paper' not found — skipped
```

### CLI Placement

Registered under `artmind domains` alongside existing subcommands (`list`, `add`, `delete`, `entities_prompt`).

---

## Out of Scope

- Cross-domain relationships
- Automatic harmonizer runs on schema save (manual CLI only)
- More than one level of nesting handled specially (dot-prefix logic works for any depth generically)
- Migrating data for domains other than the three listed renames
