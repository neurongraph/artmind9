# POOLE+ Entity Standardization & Hierarchical Domains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize entity classes to POOLE base types across all 6 domain schemas, add hierarchical domain filtering so querying `fiction` returns `fiction.thriller` entities, and implement `artmind domains harmonize` to keep child schemas in sync with parents.

**Architecture:** Schema YAMLs are updated directly; a one-off migration script renames Neo4j entity_class properties and labels. All read-path Cypher gains a STARTS WITH expansion on the domain filter. The harmonizer operates on raw YAML file text to preserve `|` literal block scalar formatting.

**Tech Stack:** Python, PyYAML, Neo4j Cypher (APOC), Click CLI, pytest

---

## File Map

**Modified:**
- `domains/schemas/fiction_schema.yaml` — CHARACTER→PERSON rename, `entity_types` field
- `domains/schemas/personal_journal_schema.yaml` — PLACE→LOCATION rename, add OBJECT/ORGANIZATION, `entity_types`
- `domains/schemas/technical_paper_schema.yaml` — AUTHOR→PERSON rename, add ORGANIZATION/LOCATION/OBJECT, `entity_types`
- `domains/schemas/general_schema.yaml` — add PERSON/OBJECT/ORGANIZATION/LOCATION, `entity_types`
- `domains/schemas/project_governance_schema.yaml` — add OBJECT/LOCATION, `entity_types`
- `domains/schemas/sales_collateral_schema.yaml` — add all 5 POOLE types, `entity_types`
- `artmind/graph_query.py` — expanded domain filter on all read queries
- `artmind/vector_query.py` — expanded domain filter
- `artmind/update.py` — expanded domain filter in `find_candidates`
- `artmind/cli.py` — `domains list` hierarchy display + `domains harmonize` command

**Created:**
- `scripts/migrate_poole.py` — one-off Neo4j migration
- `artmind/harmonizer.py` — block extraction and patching logic
- `tests/__init__.py`
- `tests/test_harmonizer.py`
- `tests/test_domain_filter.py`

---

## Task 1: Update `fiction_schema.yaml` — CHARACTER→PERSON + `entity_types`

**Files:**
- Modify: `domains/schemas/fiction_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  Open `domains/schemas/fiction_schema.yaml`. After the `description:` line and before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - PERSON
    - LOCATION
    - OBJECT
    - EVENT
    - ORGANIZATION
    - CONCEPT
  ```

- [ ] **Step 2: Rename CHARACTER→PERSON in `entities_prompt`**

  In the `entities_prompt:` block make these replacements:

  - Header: `  CHARACTER` → `  PERSON`
  - Description line: `Any person who appears, is named, or is meaningfully referenced in the story. Include characters who are talked about but do not appear directly.` (keep as-is — already describes people)
  - Example type values line: keep as-is
  - Output FORMAT comment line: `// CHARACTER | LOCATION | OBJECT | EVENT | CONCEPT` → `// PERSON | LOCATION | OBJECT | EVENT | CONCEPT`
  - Quality checklist line: `□ All named characters captured (including those only mentioned)?` → `□ All named persons captured (including those only mentioned)?`

- [ ] **Step 3: Rename CHARACTER→PERSON in `properties_prompt`**

  - `For CHARACTER, consider:` → `For PERSON, consider:`
  - All property hint lines under `For CHARACTER` stay the same content (they already use generic language).

- [ ] **Step 4: Rename CHARACTER→PERSON in `relationships_prompt`**

  Do a global replace of `CHARACTER` with `PERSON` in the relationships_prompt block:
  - `CHARACTER ↔ CHARACTER:` → `PERSON ↔ PERSON:`
  - `CHARACTER ↔ LOCATION:` → `PERSON ↔ LOCATION:`
  - `CHARACTER ↔ OBJECT:` → `PERSON ↔ OBJECT:`
  - `CHARACTER ↔ EVENT:` → `PERSON ↔ EVENT:`
  - `ORGANIZATION ↔ CHARACTER:` → `ORGANIZATION ↔ PERSON:`

- [ ] **Step 5: Verify YAML is valid**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/fiction_schema.yaml'))
  print('entity_types:', d['entity_types'])
  assert 'PERSON' in d['entities_prompt']
  assert 'CHARACTER' not in d['entities_prompt']
  assert 'PERSON' in d['properties_prompt']
  assert 'CHARACTER' not in d['properties_prompt']
  assert 'PERSON' in d['relationships_prompt']
  assert 'CHARACTER' not in d['relationships_prompt']
  print('OK')
  "
  ```
  Expected: prints entity_types list and `OK`.

- [ ] **Step 6: Commit**

  ```bash
  git add domains/schemas/fiction_schema.yaml
  git commit -m "feat: fiction schema — rename CHARACTER to PERSON, add entity_types"
  ```

---

## Task 2: Update `personal_journal_schema.yaml` — PLACE→LOCATION + OBJECT/ORGANIZATION + `entity_types`

**Files:**
- Modify: `domains/schemas/personal_journal_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  After `description:`, before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - PERSON
    - LOCATION
    - EVENT
    - ACTIVITY
    - EMOTION
    - PLAN
    - OBJECT
    - ORGANIZATION
  ```

- [ ] **Step 2: Rename PLACE→LOCATION in `entities_prompt`**

  - Header `  PLACE` → `  LOCATION`
  - Update description: `Any location mentioned or visited — rooms, buildings, neighbourhoods, cities, or institutions. Include places only referenced in plans or memories.`
  - Keep example type values: `home | workplace | school | restaurant | park | hospital | shop | neighbourhood | city | transit`
  - Output FORMAT comment: `// PERSON | PLACE | EVENT | ACTIVITY | EMOTION | PLAN` → `// PERSON | LOCATION | EVENT | ACTIVITY | EMOTION | PLAN | OBJECT | ORGANIZATION`

- [ ] **Step 3: Add OBJECT and ORGANIZATION entity blocks to `entities_prompt`**

  Insert these two blocks just before the `EXTRACTION RULES` separator line (i.e., before `  ━━━` + `  EXTRACTION RULES:`):

  ```
    OBJECT
      Any physical item, possession, or tangible thing that plays a role in the journal entry — gifts, household items, vehicles, or objects with emotional significance.
      example type values: gift | vehicle | household_item | food | document | clothing | possession | device

    ORGANIZATION
      Any formal or informal group, institution, company, or collective mentioned in the journal entry.
      example type values: employer | school | club | community_group | government | healthcare | business | religious_group
  ```

- [ ] **Step 4: Rename PLACE→LOCATION in `properties_prompt`**

  - `For PLACE, consider:` → `For LOCATION, consider:`

- [ ] **Step 5: Add OBJECT and ORGANIZATION property blocks to `properties_prompt`**

  Insert before `  KEY RULES FOR PROPERTIES:`:

  ```
    For OBJECT, consider:
      - what_it_is (brief description)
      - who_owns_or_uses_it
      - emotional_significance (if any)
      - narrative_role (why it matters in this entry)

    For ORGANIZATION, consider:
      - org_type (employer | school | healthcare | etc.)
      - relationship_to_writer (works_at | member_of | attended | etc.)
      - sentiment (positive | negative | neutral | mixed)
      - frequency_of_mention (if notable)
  ```

- [ ] **Step 6: Rename PLACE→LOCATION in `relationships_prompt`**

  Replace all `PLACE` with `LOCATION` in the relationships section.

- [ ] **Step 7: Add OBJECT and ORGANIZATION relationship blocks to `relationships_prompt`**

  Insert before the `EXTRACTION RULES` separator:

  ```
    PERSON ↔ OBJECT:
      owns, uses, gifts, receives, loses, finds, mentions

    EVENT ↔ OBJECT:
      involves, uses, concerns

    PERSON ↔ ORGANIZATION:
      works_at, attends, member_of, visited, associated_with, left, joined

    EVENT ↔ ORGANIZATION:
      held_at, organised_by, involves
  ```

- [ ] **Step 8: Verify**

  ```bash
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/personal_journal_schema.yaml'))
  print('entity_types:', d['entity_types'])
  assert 'LOCATION' in d['entities_prompt'] and 'PLACE' not in d['entities_prompt']
  assert 'OBJECT' in d['entities_prompt']
  assert 'ORGANIZATION' in d['entities_prompt']
  print('OK')
  "
  ```

- [ ] **Step 9: Commit**

  ```bash
  git add domains/schemas/personal_journal_schema.yaml
  git commit -m "feat: personal_journal schema — PLACE→LOCATION, add OBJECT/ORGANIZATION, entity_types"
  ```

---

## Task 3: Update `technical_paper_schema.yaml` — AUTHOR→PERSON + ORGANIZATION/LOCATION/OBJECT + `entity_types`

**Files:**
- Modify: `domains/schemas/technical_paper_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  After `description:`, before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - CONCEPT
    - METHOD
    - MODEL
    - FINDING
    - DATASET
    - PERSON
    - METRIC
    - APPLICATION
    - ORGANIZATION
    - LOCATION
    - OBJECT
  ```

- [ ] **Step 2: Rename AUTHOR→PERSON in `entities_prompt`**

  - Header `  AUTHOR` → `  PERSON`
  - Description: keep as-is (`Named researchers, academics, or practitioners...`) — still accurate for PERSON in academic context
  - Output FORMAT comment: update to include PERSON instead of AUTHOR, and add ORGANIZATION/LOCATION/OBJECT

- [ ] **Step 3: Add ORGANIZATION, LOCATION, OBJECT entity blocks to `entities_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    ORGANIZATION
      Any research institution, company, university, or formal group associated with the work — as producer, funder, or named collaborator.
      example type values: university | research_lab | company | consortium | standards_body | funding_agency | government_body

    LOCATION
      Any geographic location, country, or region referenced in the paper — where research was conducted, datasets collected, or systems deployed.
      example type values: country | city | institution_location | deployment_region | research_site

    OBJECT
      Any physical piece of hardware, equipment, device, or tangible system described or used in the research.
      example type values: hardware | device | equipment | sensor | compute_cluster | physical_system | robot
  ```

- [ ] **Step 4: Rename AUTHOR→PERSON in `properties_prompt`**

  - `For AUTHOR, consider:` → `For PERSON, consider:`

- [ ] **Step 5: Add ORGANIZATION, LOCATION, OBJECT property blocks to `properties_prompt`**

  Insert before `  KEY RULES FOR PROPERTIES:`:

  ```
    For ORGANIZATION, consider:
      - org_type (university | company | research_lab | etc.)
      - country_or_region (if mentioned)
      - role_in_paper (conducted_research | funded | collaborated | cited)
      - affiliation_of (which authors are affiliated)

    For LOCATION, consider:
      - country_or_region
      - relevance (where experiments ran | where data collected | deployment target)
      - specific_site (if named)

    For OBJECT, consider:
      - hardware_type (GPU | sensor | robot | etc.)
      - specifications (if mentioned)
      - role_in_paper (used_for | evaluated_on | described)
      - manufacturer (if mentioned)
  ```

- [ ] **Step 6: Rename AUTHOR→PERSON in `relationships_prompt`**

  Replace `AUTHOR ↔` and `↔ AUTHOR` with `PERSON ↔` and `↔ PERSON`.

- [ ] **Step 7: Add ORGANIZATION, LOCATION, OBJECT relationship blocks to `relationships_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    PERSON ↔ ORGANIZATION:
      affiliated_with, works_at, funded_by, collaborates_with

    ORGANIZATION ↔ MODEL:
      developed, published, maintains, contributed_to

    ORGANIZATION ↔ DATASET:
      created, maintains, funded, released

    ORGANIZATION ↔ CONCEPT:
      pioneered, advocates, researches

    OBJECT ↔ METHOD:
      used_in, enables, tested_with

    OBJECT ↔ MODEL:
      runs_on, evaluated_on, deployed_on

    LOCATION ↔ DATASET:
      source_of, collected_in, evaluated_in

    LOCATION ↔ ORGANIZATION:
      located_in, operates_in
  ```

- [ ] **Step 8: Verify**

  ```bash
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/technical_paper_schema.yaml'))
  print('entity_types:', d['entity_types'])
  assert 'PERSON' in d['entities_prompt'] and 'AUTHOR' not in d['entities_prompt']
  assert 'ORGANIZATION' in d['entities_prompt']
  assert 'LOCATION' in d['entities_prompt']
  assert 'OBJECT' in d['entities_prompt']
  print('OK')
  "
  ```

- [ ] **Step 9: Commit**

  ```bash
  git add domains/schemas/technical_paper_schema.yaml
  git commit -m "feat: technical_paper schema — AUTHOR→PERSON, add ORGANIZATION/LOCATION/OBJECT, entity_types"
  ```

---

## Task 4: Update `general_schema.yaml` — add PERSON/OBJECT/ORGANIZATION/LOCATION + `entity_types`

**Files:**
- Modify: `domains/schemas/general_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  After `description:`, before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - ENTITY
    - CONCEPT
    - EVENT
    - CLAIM
    - METRIC
    - SOURCE
    - PERSON
    - OBJECT
    - ORGANIZATION
    - LOCATION
  ```

- [ ] **Step 2: Add PERSON, OBJECT, ORGANIZATION, LOCATION entity blocks to `entities_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    PERSON
      Any named individual referenced in the document — authors, subjects, experts, decision-makers, or people mentioned by name. Use when the entity is clearly an individual person rather than an organisation.
      example type values: author | subject | expert | decision_maker | official | researcher | spokesperson | executive

    OBJECT
      Any physical item, product, system, or tangible entity referenced in the document. Use when the entity is clearly a physical object rather than an organisation or abstract concept.
      example type values: product | system | device | tool | artifact | infrastructure | document_item

    ORGANIZATION
      Any company, institution, government body, NGO, or formal group referenced in the document. Use instead of ENTITY when the entity is clearly an organisation.
      example type values: company | government | institution | ngo | standards_body | association | regulator | partnership

    LOCATION
      Any geographic area, place, site, or address referenced in the document.
      example type values: country | city | region | site | office | facility | address | market
  ```

  Also update the OUTPUT FORMAT comment to include the new classes.

- [ ] **Step 3: Add PERSON, OBJECT, ORGANIZATION, LOCATION property blocks to `properties_prompt`**

  Insert before `  KEY RULES FOR PROPERTIES:`:

  ```
    For PERSON, consider:
      - role_in_document (author | subject | expert | official | etc.)
      - affiliation (organisation they belong to)
      - significance (why they are mentioned)
      - expertise (domain or field, if stated)

    For OBJECT, consider:
      - object_type (product | system | device | etc.)
      - description (brief physical or functional description)
      - significance_in_document (why it is mentioned)
      - owner_or_operator (if stated)

    For ORGANIZATION, consider:
      - org_type (company | government | institution | etc.)
      - role_in_document (subject | author | referenced | regulator)
      - size_or_scale (if mentioned)
      - geographic_scope (if mentioned)

    For LOCATION, consider:
      - location_type (country | city | region | site | etc.)
      - role_in_document (subject | setting | referenced)
      - geographic_context (continent or country it belongs to, if stated)
      - significance (why it is mentioned)
  ```

- [ ] **Step 4: Add PERSON, OBJECT, ORGANIZATION, LOCATION relationship blocks to `relationships_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    PERSON ↔ ORGANIZATION:
      works_at, founded, leads, cited_by, affiliated_with, represents, regulates

    PERSON ↔ EVENT:
      participated_in, caused, attended, announced, responded_to

    PERSON ↔ CLAIM:
      makes, challenges, supports, authored

    PERSON ↔ METRIC:
      cited, reported, achieved

    PERSON ↔ LOCATION:
      located_in, visited, operates_in, cited_from

    ORGANIZATION ↔ LOCATION:
      located_in, operates_in, regulates, serves

    ORGANIZATION ↔ OBJECT:
      owns, produces, operates, distributes

    OBJECT ↔ LOCATION:
      located_at, deployed_in, manufactured_in

    OBJECT ↔ METRIC:
      measured_by, characterised_by
  ```

- [ ] **Step 5: Verify**

  ```bash
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/general_schema.yaml'))
  print('entity_types:', d['entity_types'])
  for t in ['PERSON','OBJECT','ORGANIZATION','LOCATION']:
      assert t in d['entities_prompt'], f'Missing {t} in entities_prompt'
      assert t in d['properties_prompt'], f'Missing {t} in properties_prompt'
      assert t in d['relationships_prompt'], f'Missing {t} in relationships_prompt'
  print('OK')
  "
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add domains/schemas/general_schema.yaml
  git commit -m "feat: general schema — add PERSON/OBJECT/ORGANIZATION/LOCATION, entity_types"
  ```

---

## Task 5: Update `project_governance_schema.yaml` — add OBJECT/LOCATION + `entity_types`

**Files:**
- Modify: `domains/schemas/project_governance_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  After `description:`, before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - PROJECT
    - MILESTONE
    - PERSON
    - ORGANIZATION
    - RISK
    - ISSUE
    - DECISION
    - ACTION
    - TECHNOLOGY
    - OBJECT
    - LOCATION
  ```

- [ ] **Step 2: Add OBJECT and LOCATION entity blocks to `entities_prompt`**

  Insert before `EXTRACTION RULES` separator. Also update the OUTPUT FORMAT comment to include OBJECT and LOCATION.

  ```
    OBJECT
      Any physical item, document, deliverable, or tangible asset relevant to the project governance context.
      example type values: deliverable | document | tool | equipment | asset | resource | report | contract

    LOCATION
      Any place, site, office, or geographic area referenced in the governance context — where work is performed, teams are based, or delivery takes place.
      example type values: office | site | country | region | city | facility | remote_location | data_centre
  ```

- [ ] **Step 3: Add OBJECT and LOCATION property blocks to `properties_prompt`**

  Insert before `  KEY RULES FOR PROPERTIES:`:

  ```
    For OBJECT, consider:
      - object_type (deliverable | document | equipment | etc.)
      - owner_or_responsible (person or team)
      - status (in_progress | complete | pending | at_risk)
      - related_milestone (if applicable)
      - version (if versioned)

    For LOCATION, consider:
      - location_type (office | site | country | etc.)
      - relevance (where work happens | where team is based | delivery location)
      - timezone (if relevant to governance)
  ```

- [ ] **Step 4: Add OBJECT and LOCATION relationship blocks to `relationships_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    OBJECT ↔ PROJECT:
      produced_by, required_by, documents, part_of

    OBJECT ↔ MILESTONE:
      due_at, produced_for, marks_completion_of

    OBJECT ↔ PERSON:
      owned_by, authored_by, reviewed_by, approved_by

    LOCATION ↔ PROJECT:
      hosts, is_delivery_location_for, operates_in

    LOCATION ↔ PERSON:
      based_at, works_from, assigned_to

    LOCATION ↔ ORGANIZATION:
      headquartered_at, operates_from, office_of
  ```

- [ ] **Step 5: Verify**

  ```bash
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/project_governance_schema.yaml'))
  print('entity_types:', d['entity_types'])
  for t in ['OBJECT','LOCATION']:
      assert t in d['entities_prompt'], f'Missing {t}'
      assert t in d['properties_prompt'], f'Missing {t}'
      assert t in d['relationships_prompt'], f'Missing {t}'
  print('OK')
  "
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add domains/schemas/project_governance_schema.yaml
  git commit -m "feat: project_governance schema — add OBJECT/LOCATION, entity_types"
  ```

---

## Task 6: Update `sales_collateral_schema.yaml` — add all 5 POOLE types + `entity_types`

**Files:**
- Modify: `domains/schemas/sales_collateral_schema.yaml`

- [ ] **Step 1: Add `entity_types` field**

  After `description:`, before `entities_prompt:`, insert:

  ```yaml
  entity_types:
    - CLIENT
    - OFFERING
    - CAPABILITY
    - USE_CASE
    - BENEFIT
    - CHALLENGE
    - TECHNOLOGY
    - DIFFERENTIATOR
    - PERSON
    - OBJECT
    - ORGANIZATION
    - LOCATION
    - EVENT
  ```

- [ ] **Step 2: Add all 5 POOLE entity blocks to `entities_prompt`**

  Insert before `EXTRACTION RULES` separator. Also update the OUTPUT FORMAT comment.

  ```
    PERSON
      Any named individual referenced in the sales document — decision-makers, champions, customer contacts, or named representatives.
      example type values: decision_maker | champion | stakeholder | contact | executive | buyer | influencer | sponsor

    OBJECT
      Any physical product, hardware, or tangible item relevant to the sales context — distinct from a named TECHNOLOGY platform.
      example type values: product | hardware | device | appliance | tool | physical_system

    ORGANIZATION
      Any company, institution, partner, or formal group referenced beyond the CLIENT — vendors, regulators, industry bodies, or competitors.
      example type values: vendor | partner | regulator | competitor | analyst_firm | industry_body | association | reseller

    LOCATION
      Any geographic area, market, country, or site mentioned in the sales context.
      example type values: market | country | region | office | site | territory | deployment_location

    EVENT
      Any named occurrence, launch, announcement, or industry event referenced in the material.
      example type values: product_launch | conference | announcement | demo | pilot | deadline | proof_of_concept | deal_event
  ```

- [ ] **Step 3: Add all 5 POOLE property blocks to `properties_prompt`**

  Insert before `  KEY RULES FOR PROPERTIES:`:

  ```
    For PERSON, consider:
      - role_title (their job title or function)
      - influence_level (decision_maker | influencer | champion | etc.)
      - known_pain_points (list, if mentioned)
      - relationship_to_deal (sponsor | blocker | neutral | evaluator)

    For OBJECT, consider:
      - product_category
      - key_features (list)
      - compatibility (what it works with)
      - price_point (if mentioned)

    For ORGANIZATION, consider:
      - org_type (partner | competitor | regulator | etc.)
      - relationship_to_offering (complements | competes | distributes | regulates)
      - geographic_presence (if mentioned)

    For LOCATION, consider:
      - market_type (geographic | industry | segment)
      - strategic_importance (core | expansion | emerging)
      - regulatory_environment (if relevant)

    For EVENT, consider:
      - event_type (launch | conference | pilot | etc.)
      - date_or_period (if mentioned)
      - relevance_to_deal (opportunity | milestone | deadline)
      - key_participants (if mentioned)
  ```

- [ ] **Step 4: Add all 5 POOLE relationship blocks to `relationships_prompt`**

  Insert before `EXTRACTION RULES` separator:

  ```
    PERSON ↔ CLIENT:
      represents, sponsors, is_champion_at, evaluates_for, signs_off

    PERSON ↔ OFFERING:
      evaluates, champions, objected_to, approved

    PERSON ↔ ORGANIZATION:
      works_at, leads, represents, part_of

    ORGANIZATION ↔ OFFERING:
      distributes, competes_with, validates, integrates_with, regulates

    ORGANIZATION ↔ CLIENT:
      partners_with, competes_with, supplies, regulates

    OBJECT ↔ OFFERING:
      part_of, enables, required_for, competes_with, integrates_with

    OBJECT ↔ TECHNOLOGY:
      uses, implements, runs_on

    LOCATION ↔ CLIENT:
      is_market_for, operates_in, regulatory_jurisdiction_of

    LOCATION ↔ OFFERING:
      available_in, deployed_in, targeted_at

    EVENT ↔ OFFERING:
      launched_at, demonstrated_at, evaluated_at, piloted_for

    EVENT ↔ CLIENT:
      involves, triggered, milestone_for
  ```

- [ ] **Step 5: Verify**

  ```bash
  python -c "
  import yaml
  d = yaml.safe_load(open('domains/schemas/sales_collateral_schema.yaml'))
  print('entity_types:', d['entity_types'])
  for t in ['PERSON','OBJECT','ORGANIZATION','LOCATION','EVENT']:
      assert t in d['entities_prompt'], f'Missing {t} in entities_prompt'
      assert t in d['properties_prompt'], f'Missing {t} in properties_prompt'
      assert t in d['relationships_prompt'], f'Missing {t} in relationships_prompt'
  print('OK')
  "
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add domains/schemas/sales_collateral_schema.yaml
  git commit -m "feat: sales_collateral schema — add all 5 POOLE types, entity_types"
  ```

---

## Task 7: Create `scripts/migrate_poole.py` — one-off Neo4j migration

**Files:**
- Create: `scripts/migrate_poole.py`

- [ ] **Step 1: Create the scripts directory and migration file**

  ```bash
  mkdir -p /Users/surjitdas/Projects/artmind9/scripts
  ```

  Create `scripts/migrate_poole.py`:

  ```python
  #!/usr/bin/env python3
  """One-off migration: rename entity_class values and Neo4j labels to POOLE+ standard.

  Renames:
    fiction domain:         CHARACTER → PERSON
    technical_paper domain: AUTHOR    → PERSON
    personal_journal domain: PLACE   → LOCATION

  Run once: uv run python scripts/migrate_poole.py
  """
  import sys
  from pathlib import Path

  sys.path.insert(0, str(Path(__file__).parent.parent))

  from artmind.graph_query import neo4j_session

  RENAMES = [
      ("fiction",          "CHARACTER", "PERSON"),
      ("technical_paper",  "AUTHOR",    "PERSON"),
      ("personal_journal", "PLACE",     "LOCATION"),
  ]


  def migrate() -> None:
      with neo4j_session() as session:
          for domain, old_class, new_class in RENAMES:
              result = session.run(
                  """
                  MATCH (e:Entity {domain: $domain, entity_class: $old_class})
                  CALL apoc.create.addLabels(e, [$new_class]) YIELD node
                  WITH node
                  CALL apoc.create.removeLabels(node, [$old_class]) YIELD node AS n
                  SET n.entity_class = $new_class
                  RETURN count(n) AS updated
                  """,
                  domain=domain,
                  old_class=old_class,
                  new_class=new_class,
              ).single()
              count = result["updated"] if result else 0
              print(f"  {domain}: {old_class} → {new_class}: {count} node(s) updated")


  if __name__ == "__main__":
      print("Running POOLE+ entity class migration...")
      migrate()
      print("Done.")
  ```

- [ ] **Step 2: Verify the script is importable (dry check)**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  uv run python -c "import ast; ast.parse(open('scripts/migrate_poole.py').read()); print('syntax OK')"
  ```
  Expected: `syntax OK`

- [ ] **Step 3: Commit**

  ```bash
  git add scripts/migrate_poole.py
  git commit -m "feat: add POOLE+ one-off Neo4j migration script"
  ```

---

## Task 8: Expand domain filter in `artmind/graph_query.py`

**Files:**
- Modify: `artmind/graph_query.py`

Every `WHERE n.domain = $domain` (single-node) becomes:
```cypher
WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.'))
```
Every double-node filter `WHERE s.domain = $domain AND e.domain = $domain` becomes:
```cypher
WHERE (s.domain = $domain OR s.domain STARTS WITH ($domain + '.'))
  AND (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
```

- [ ] **Step 1: Update `graph_metadata` (both filters)**

  Change:
  ```python
      MATCH (n)
      WHERE n.domain = $domain
  ```
  To:
  ```python
      MATCH (n)
      WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.'))
  ```
  And change:
  ```python
      WHERE s.domain = $domain AND e.domain = $domain
  ```
  To:
  ```python
      WHERE (s.domain = $domain OR s.domain STARTS WITH ($domain + '.'))
        AND (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 2: Update `entity_listing` (both WHERE clauses)**

  Change `WHERE n.domain = $domain AND n.name IS NOT NULL` to:
  ```python
  WHERE (n.domain = $domain OR n.domain STARTS WITH ($domain + '.')) AND n.name IS NOT NULL
  ```
  Change count_cypher filter the same way.

- [ ] **Step 3: Update pattern1**

  Change `WHERE e.domain = $domain` to:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 4: Update pattern2**

  Change `WHERE e.domain = $domain` to:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 5: Update pattern3 (two filters)**

  Change `WHERE e.domain = $domain` (first) and `WHERE t.domain = $domain` (second) both:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```
  ```python
  WHERE (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 6: Update pattern4 (two filters)**

  Change `WHERE e.domain = $domain` and `WHERE t.domain = $domain`:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
    AND toLower(e.name) CONTAINS toLower($entityName)
  ```
  ```python
  WHERE (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 7: Update pattern5 (both modes, two filters each)**

  All `e.domain = $domain AND t.domain = $domain` (and their sub-query variants) become:
  ```python
  (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  AND (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 8: Update pattern6 (two filters)**

  Change `WHERE e1.domain = $domain AND e2.domain = $domain` to:
  ```python
  WHERE (e1.domain = $domain OR e1.domain STARTS WITH ($domain + '.'))
    AND (e2.domain = $domain OR e2.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 9: Update pattern7**

  Change `WHERE e.domain = $domain` to:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 10: Update pattern8 (two filters)**

  Change `WHERE e.domain = $domain AND t.domain = $domain` to:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
    AND (t.domain = $domain OR t.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 11: Update pattern9**

  Change `WHERE e.domain = $domain` to:
  ```python
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 12: Verify no raw `= $domain` remains in read queries**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  grep -n "\.domain = \$domain" artmind/graph_query.py
  ```
  Expected: zero matches (all replaced with the expanded form).

- [ ] **Step 13: Commit**

  ```bash
  git add artmind/graph_query.py
  git commit -m "feat: expand domain filter to include sub-domains in graph_query.py"
  ```

---

## Task 9: Expand domain filter in `artmind/vector_query.py` and `artmind/update.py`

**Files:**
- Modify: `artmind/vector_query.py`
- Modify: `artmind/update.py`

- [ ] **Step 1: Update `vector_query.py` — `cypher_chunks` in `vector_search`**

  Change `WHERE node.domain = $domain` to:
  ```cypher
  WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 2: Update `vector_query.py` — `cypher_chats` in `vector_search`**

  Same replacement.

- [ ] **Step 3: Update `vector_query.py` — `cypher_chunks` in `full_text_search`**

  Change `WHERE node.domain = $domain AND {keyword_conditions}` to:
  ```python
  WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.')) AND {keyword_conditions}
  ```

- [ ] **Step 4: Update `vector_query.py` — `cypher_chats` in `full_text_search`**

  Same pattern.

- [ ] **Step 5: Update `vector_query.py` — `_full_text_fallback_chunks`**

  Change `WHERE node.domain = $domain` to expanded form.

- [ ] **Step 6: Update `vector_query.py` — `_full_text_fallback_chats`**

  Change `WHERE node.domain = $domain` to expanded form.

- [ ] **Step 7: Update `update.py` — `cypher_domain` in `find_candidates`**

  Change `WHERE e.domain = $domain` to:
  ```cypher
  WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  ```

- [ ] **Step 8: Verify**

  ```bash
  grep -n "\.domain = \$domain" artmind/vector_query.py artmind/update.py
  ```
  Expected: zero matches.

- [ ] **Step 9: Commit**

  ```bash
  git add artmind/vector_query.py artmind/update.py
  git commit -m "feat: expand domain filter to include sub-domains in vector_query.py and update.py"
  ```

---

## Task 10: Update `artmind/cli.py` — `domains list` hierarchy display

**Files:**
- Modify: `artmind/cli.py`

- [ ] **Step 1: Update `list_domains` command**

  Replace the current `list_domains` implementation:

  ```python
  @domains.command("list")
  def list_domains():
      """List all available domain schemas."""
      domains_list = _get_available_domains()
      for d in domains_list:
          click.echo(d)
  ```

  With:

  ```python
  @domains.command("list")
  def list_domains():
      """List all available domain schemas, showing hierarchy."""
      all_domains = sorted(_get_available_domains())
      parents = [d for d in all_domains if '.' not in d]
      children = [d for d in all_domains if '.' in d]

      shown = set()
      for parent in parents:
          click.echo(parent)
          shown.add(parent)
          for child in children:
              if child.startswith(parent + '.'):
                  click.echo(f"  {child}")
                  shown.add(child)

      # orphaned children (parent not in schema list)
      for child in children:
          if child not in shown:
              click.echo(child)
  ```

- [ ] **Step 2: Verify manually**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  uv run artmind domains list
  ```
  Expected: flat domains listed normally (no children exist yet), no errors.

- [ ] **Step 3: Commit**

  ```bash
  git add artmind/cli.py
  git commit -m "feat: domains list shows hierarchical indented view for sub-domains"
  ```

---

## Task 11: Create `artmind/harmonizer.py` — block extraction and patching logic

**Files:**
- Create: `artmind/harmonizer.py`

- [ ] **Step 1: Write the harmonizer module**

  Create `artmind/harmonizer.py`:

  ```python
  """Schema harmonizer: sync child domain schemas against their parent."""
  import re
  from dataclasses import dataclass, field
  from pathlib import Path

  import yaml

  from paths import DOMAIN_SCHEMAS_DIR


  @dataclass
  class HarmonizeResult:
      domain: str
      status: str           # "in_sync" | "updated" | "dry_run" | "error"
      added: list[str] = field(default_factory=list)
      error: str = ""


  # ── block extraction ──────────────────────────────────────────────────────────


  def _split_entity_blocks(prompt: str) -> dict[str, str]:
      """Return {ENTITY_TYPE: block_text} from entities_prompt.

      Each block runs from the type header line up to (not including) the next
      header or the EXTRACTION RULES separator.
      """
      header_re = re.compile(r'^  ([A-Z_][A-Z0-9_]*)$', re.MULTILINE)
      headers = list(header_re.finditer(prompt))
      section_end_idx = prompt.find('\n  EXTRACTION RULES:')
      if section_end_idx == -1:
          section_end_idx = len(prompt)

      result: dict[str, str] = {}
      for i, match in enumerate(headers):
          start = match.start()
          if start >= section_end_idx:
              break
          next_start = headers[i + 1].start() if i + 1 < len(headers) else section_end_idx
          end = min(next_start, section_end_idx)
          result[match.group(1)] = prompt[start:end].rstrip()
      return result


  def _split_property_blocks(prompt: str) -> dict[str, str]:
      """Return {ENTITY_TYPE: block_text} from properties_prompt."""
      header_re = re.compile(r'^  For ([A-Z_][A-Z0-9_]*), consider:$', re.MULTILINE)
      headers = list(header_re.finditer(prompt))
      key_rules_idx = prompt.find('\n  KEY RULES FOR PROPERTIES:')
      if key_rules_idx == -1:
          key_rules_idx = len(prompt)

      result: dict[str, str] = {}
      for i, match in enumerate(headers):
          start = match.start()
          next_start = headers[i + 1].start() if i + 1 < len(headers) else key_rules_idx
          end = min(next_start, key_rules_idx)
          result[match.group(1)] = prompt[start:end].rstrip()
      return result


  def _split_relationship_blocks(prompt: str) -> dict[str, list[str]]:
      """Return {ENTITY_TYPE: [block, ...]} from relationships_prompt.

      Each entity type maps to ALL relationship blocks it appears in (both sides of ↔).
      """
      block_re = re.compile(
          r'^  ([A-Z_][A-Z0-9_]*) ↔ ([A-Z_][A-Z0-9_]*):\n(?:    [^\n]+\n?)+',
          re.MULTILINE,
      )
      result: dict[str, list[str]] = {}
      for m in block_re.finditer(prompt):
          block = m.group(0).rstrip()
          t1, t2 = m.group(1), m.group(2)
          result.setdefault(t1, []).append(block)
          if t2 != t1:
              result.setdefault(t2, []).append(block)
      return result


  # ── injection helpers ─────────────────────────────────────────────────────────


  def _insert_before(text: str, marker: str, content: str) -> str:
      """Insert `content` immediately before `marker` in `text`. Appends if not found."""
      idx = text.find(marker)
      if idx == -1:
          return text + '\n\n' + content
      return text[:idx] + content + '\n\n' + text[idx:]


  def _inject_entity_blocks(prompt: str, blocks: list[str]) -> str:
      """Append entity blocks before the EXTRACTION RULES separator."""
      return _insert_before(prompt, '\n  EXTRACTION RULES:', '\n\n' + '\n\n'.join(blocks))


  def _inject_property_blocks(prompt: str, blocks: list[str]) -> str:
      """Append property blocks before the KEY RULES section."""
      return _insert_before(prompt, '\n  KEY RULES FOR PROPERTIES:', '\n\n' + '\n\n'.join(blocks))


  def _inject_relationship_blocks(prompt: str, blocks: list[str]) -> str:
      """Append relationship blocks before the EXTRACTION RULES separator."""
      return _insert_before(prompt, '\n  EXTRACTION RULES:', '\n\n' + '\n\n'.join(blocks))


  # ── core harmonize logic ──────────────────────────────────────────────────────


  def _load_schema_raw(schema_path: Path) -> tuple[dict, str]:
      """Return (parsed_dict, raw_file_text)."""
      raw = schema_path.read_text(encoding='utf-8')
      return yaml.safe_load(raw), raw


  def _write_schema(schema_path: Path, data: dict, raw: str) -> None:
      """Write updated schema back, preserving YAML literal block scalars.

      Strategy: update the three prompt fields by string-replacing their content
      in the raw file, and patch the entity_types list via raw text insertion.
      The rest of the file (name, description) is unchanged.
      """
      # Update entity_types block in raw text
      new_types_block = 'entity_types:\n' + ''.join(
          f'  - {t}\n' for t in data['entity_types']
      )
      # Replace existing entity_types block or insert before entities_prompt
      et_re = re.compile(r'^entity_types:\n(?:  - [^\n]+\n)+', re.MULTILINE)
      if et_re.search(raw):
          raw = et_re.sub(new_types_block, raw)
      else:
          raw = raw.replace('\nentities_prompt:', '\n' + new_types_block + '\nentities_prompt:')

      # Replace prompt sections by finding the key and replacing its value
      for key in ('entities_prompt', 'properties_prompt', 'relationships_prompt'):
          new_val = data[key]
          # Find `key: |\n` and replace until next top-level key or EOF
          section_re = re.compile(
              r'^(' + key + r': \|)\n((?:[ \t][^\n]*\n|\n)*)',
              re.MULTILINE,
          )
          def _replacer(m, val=new_val):
              # Indent each line of the prompt value with 2 spaces
              indented = '\n'.join('  ' + ln if ln else '' for ln in val.splitlines())
              return m.group(1) + '\n' + indented + '\n'
          raw = section_re.sub(_replacer, raw, count=1)

      schema_path.write_text(raw, encoding='utf-8')


  def harmonize_schema(
      child_name: str,
      dry_run: bool = False,
  ) -> HarmonizeResult:
      """Harmonize a single child schema against its parent."""
      parent_name = child_name.rsplit('.', 1)[0]
      child_path = DOMAIN_SCHEMAS_DIR / f'{child_name}_schema.yaml'
      parent_path = DOMAIN_SCHEMAS_DIR / f'{parent_name}_schema.yaml'

      if not child_path.exists():
          return HarmonizeResult(child_name, 'error', error=f'Child schema not found: {child_path}')
      if not parent_path.exists():
          return HarmonizeResult(child_name, 'error', error=f'Parent schema not found: {parent_path}')

      child, child_raw = _load_schema_raw(child_path)
      parent, _ = _load_schema_raw(parent_path)

      parent_types = set(parent.get('entity_types', []))
      child_types = set(child.get('entity_types', []))
      missing = parent_types - child_types

      if not missing:
          return HarmonizeResult(child_name, 'in_sync')

      # Extract blocks for missing types from parent
      parent_entity_blocks = _split_entity_blocks(parent['entities_prompt'])
      parent_prop_blocks = _split_property_blocks(parent['properties_prompt'])
      parent_rel_blocks = _split_relationship_blocks(parent['relationships_prompt'])

      entities_to_add = [parent_entity_blocks[t] for t in sorted(missing) if t in parent_entity_blocks]
      props_to_add = [parent_prop_blocks[t] for t in sorted(missing) if t in parent_prop_blocks]
      rels_to_add = []
      seen_rel_blocks: set[str] = set()
      for t in sorted(missing):
          for block in parent_rel_blocks.get(t, []):
              if block not in seen_rel_blocks:
                  rels_to_add.append(block)
                  seen_rel_blocks.add(block)

      if dry_run:
          return HarmonizeResult(child_name, 'dry_run', added=sorted(missing))

      # Patch the child's prompt strings in-memory
      if entities_to_add:
          child['entities_prompt'] = _inject_entity_blocks(child['entities_prompt'], entities_to_add)
      if props_to_add:
          child['properties_prompt'] = _inject_property_blocks(child['properties_prompt'], props_to_add)
      if rels_to_add:
          child['relationships_prompt'] = _inject_relationship_blocks(child['relationships_prompt'], rels_to_add)

      child['entity_types'] = sorted(child_types | missing)

      _write_schema(child_path, child, child_raw)

      return HarmonizeResult(child_name, 'updated', added=sorted(missing))


  def harmonize_all(dry_run: bool = False) -> list[HarmonizeResult]:
      """Harmonize all child schemas found in DOMAIN_SCHEMAS_DIR."""
      results = []
      for schema_file in sorted(DOMAIN_SCHEMAS_DIR.glob('*_schema.yaml')):
          data = yaml.safe_load(schema_file.read_text(encoding='utf-8'))
          name = data.get('name', '')
          if '.' in name:
              results.append(harmonize_schema(name, dry_run=dry_run))
      return results
  ```

- [ ] **Step 2: Verify the module imports cleanly**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  uv run python -c "from artmind.harmonizer import harmonize_all; print('import OK')"
  ```
  Expected: `import OK`

- [ ] **Step 3: Commit**

  ```bash
  git add artmind/harmonizer.py
  git commit -m "feat: add harmonizer module for schema block extraction and patching"
  ```

---

## Task 12: Add `domains harmonize` command to `artmind/cli.py`

**Files:**
- Modify: `artmind/cli.py`

- [ ] **Step 1: Add the import**

  Add to the top of `cli.py` imports section:

  ```python
  from artmind.harmonizer import harmonize_all, harmonize_schema
  ```

- [ ] **Step 2: Add the `harmonize` command under the `domains` group**

  Insert after the `relationships_prompt` command (around line 208), before the `ingest` group:

  ```python
  @domains.command("harmonize")
  @click.option("--domain", default=None, help="Child domain to harmonize (e.g. fiction.thriller). Default: all children.")
  @click.option("--dry-run", is_flag=True, help="Show what would change without writing.")
  def domains_harmonize(domain: str | None, dry_run: bool):
      """Sync child domain schemas against their parent's entity types.

      Copies any entity, property, and relationship blocks that exist in the
      parent but are missing from the child. Never removes child-specific extras.
      """
      if domain:
          if '.' not in domain:
              raise click.ClickException(f"'{domain}' has no parent (no '.' in name). Only child domains can be harmonized.")
          results = [harmonize_schema(domain, dry_run=dry_run)]
      else:
          results = harmonize_all(dry_run=dry_run)

      if not results:
          click.echo("No child schemas found.")
          return

      for r in results:
          if r.status == 'error':
              click.echo(f"{r.domain}  ERROR: {r.error}")
          elif r.status == 'in_sync':
              click.echo(f"{r.domain}  already in sync")
          elif r.status == 'dry_run':
              click.echo(f"{r.domain}  would add entity types: {r.added}")
          else:
              click.echo(f"{r.domain}  added entity types: {r.added}")
  ```

- [ ] **Step 3: Verify the command is registered**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  uv run artmind domains --help
  ```
  Expected: `harmonize` appears in the list of commands.

- [ ] **Step 4: Smoke test with no child schemas (should print "No child schemas found.")**

  ```bash
  uv run artmind domains harmonize
  ```
  Expected: `No child schemas found.`

- [ ] **Step 5: Commit**

  ```bash
  git add artmind/cli.py
  git commit -m "feat: add 'artmind domains harmonize' CLI command"
  ```

---

## Task 13: Tests

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_harmonizer.py`
- Create: `tests/test_domain_filter.py`

- [ ] **Step 1: Create test infrastructure**

  ```bash
  mkdir -p /Users/surjitdas/Projects/artmind9/tests
  touch /Users/surjitdas/Projects/artmind9/tests/__init__.py
  ```

- [ ] **Step 2: Write failing harmonizer tests**

  Create `tests/test_harmonizer.py`:

  ```python
  """Tests for artmind.harmonizer block extraction and patching logic."""
  import pytest
  from artmind.harmonizer import (
      _split_entity_blocks,
      _split_property_blocks,
      _split_relationship_blocks,
      _inject_entity_blocks,
      _inject_property_blocks,
      _inject_relationship_blocks,
  )

  ENTITIES_PROMPT = """\
  You are an extractor.

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ENTITY TYPES YOU MUST EXTRACT:
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Use ONLY these entity_classes.

    PERSON
      A named individual.
      example type values: author | subject

    LOCATION
      A place.
      example type values: country | city

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  EXTRACTION RULES:
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Be complete.
  """

  PROPERTIES_PROMPT = """\
  You are an extractor.

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PROPERTIES MAP GUIDANCE:
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    For PERSON, consider:
      - role
      - affiliation

    For LOCATION, consider:
      - country
      - city

  KEY RULES FOR PROPERTIES:
    1. Be clear.
  """

  RELATIONSHIPS_PROMPT = """\
  You are an extractor.

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  COMMON rel_type VALUES:
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    PERSON ↔ LOCATION:
      visited, lives_in, works_at

    PERSON ↔ PERSON:
      knows, works_with

  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  EXTRACTION RULES:
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Be explicit.
  """


  def test_split_entity_blocks_finds_person():
      blocks = _split_entity_blocks(ENTITIES_PROMPT)
      assert 'PERSON' in blocks
      assert 'A named individual.' in blocks['PERSON']


  def test_split_entity_blocks_finds_location():
      blocks = _split_entity_blocks(ENTITIES_PROMPT)
      assert 'LOCATION' in blocks
      assert 'A place.' in blocks['LOCATION']


  def test_split_entity_blocks_does_not_find_missing_type():
      blocks = _split_entity_blocks(ENTITIES_PROMPT)
      assert 'EVENT' not in blocks


  def test_split_property_blocks_finds_person():
      blocks = _split_property_blocks(PROPERTIES_PROMPT)
      assert 'PERSON' in blocks
      assert '- role' in blocks['PERSON']


  def test_split_property_blocks_finds_location():
      blocks = _split_property_blocks(PROPERTIES_PROMPT)
      assert 'LOCATION' in blocks
      assert '- country' in blocks['LOCATION']


  def test_split_relationship_blocks_person_appears_in_both_blocks():
      blocks = _split_relationship_blocks(RELATIONSHIPS_PROMPT)
      assert 'PERSON' in blocks
      assert len(blocks['PERSON']) == 2  # PERSON ↔ LOCATION and PERSON ↔ PERSON


  def test_split_relationship_blocks_location_one_block():
      blocks = _split_relationship_blocks(RELATIONSHIPS_PROMPT)
      assert 'LOCATION' in blocks
      assert len(blocks['LOCATION']) == 1  # Only PERSON ↔ LOCATION


  def test_inject_entity_blocks_appears_before_extraction_rules():
      new_block = "  EVENT\n    A happening.\n    example type values: meeting"
      result = _inject_entity_blocks(ENTITIES_PROMPT, [new_block])
      assert 'EVENT' in result
      assert result.index('EVENT') < result.index('EXTRACTION RULES')


  def test_inject_property_blocks_appears_before_key_rules():
      new_block = "  For EVENT, consider:\n    - date\n    - participants"
      result = _inject_property_blocks(PROPERTIES_PROMPT, [new_block])
      assert 'For EVENT' in result
      assert result.index('For EVENT') < result.index('KEY RULES FOR PROPERTIES')


  def test_inject_relationship_blocks_appears_before_extraction_rules():
      new_block = "  EVENT ↔ PERSON:\n    involves, triggers"
      result = _inject_relationship_blocks(RELATIONSHIPS_PROMPT, [new_block])
      assert 'EVENT ↔ PERSON' in result
      assert result.index('EVENT ↔ PERSON') < result.index('EXTRACTION RULES')
  ```

- [ ] **Step 3: Run the failing tests**

  ```bash
  cd /Users/surjitdas/Projects/artmind9
  uv run pytest tests/test_harmonizer.py -v
  ```
  Expected: FAIL (harmonizer module not yet written — but it was written in Task 11, so these should actually PASS if Task 11 is done first).

- [ ] **Step 4: Write domain filter tests**

  Create `tests/test_domain_filter.py`:

  ```python
  """Tests that domain filters in Cypher queries use the expanded STARTS WITH form."""
  import inspect
  import artmind.graph_query as gq
  import artmind.vector_query as vq
  import artmind.update as upd


  def _get_pattern_cypher(pattern: str, **kwargs) -> str:
      """Extract the Cypher string for a given pattern without hitting Neo4j."""
      fake_params = {
          'domain': 'fiction',
          'entityClass': 'PERSON',
          'entityName': 'Holmes',
          'entityNameList': ['Holmes'],
          'entityClass1': 'PERSON',
          'entityClass2': 'LOCATION',
          'entityName1': 'Holmes',
          'entityName2': 'London',
          'searchTerm': 'detective',
          'topN': 5,
          'limit': 10,
          'mode': 'shortest',
          **kwargs,
      }
      cypher, _ = gq._pattern_query(pattern, fake_params)
      return cypher


  def test_pattern1_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern1')
      assert 'STARTS WITH' in cypher
      assert "= $domain" not in cypher.replace("STARTS WITH", "")


  def test_pattern2_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern2')
      assert 'STARTS WITH' in cypher


  def test_pattern3_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern3')
      assert 'STARTS WITH' in cypher


  def test_pattern4_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern4')
      assert 'STARTS WITH' in cypher


  def test_pattern7_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern7')
      assert 'STARTS WITH' in cypher


  def test_pattern9_uses_expanded_domain_filter():
      cypher = _get_pattern_cypher('pattern9')
      assert 'STARTS WITH' in cypher


  def test_find_candidates_cypher_uses_expanded_filter():
      src = inspect.getsource(upd.find_candidates)
      assert 'STARTS WITH' in src


  def test_vector_search_cypher_uses_expanded_filter():
      src = inspect.getsource(vq.vector_search)
      assert 'STARTS WITH' in src


  def test_full_text_search_cypher_uses_expanded_filter():
      src = inspect.getsource(vq.full_text_search)
      assert 'STARTS WITH' in src
  ```

- [ ] **Step 5: Run all tests**

  ```bash
  uv run pytest tests/ -v
  ```
  Expected: all tests PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add tests/__init__.py tests/test_harmonizer.py tests/test_domain_filter.py
  git commit -m "test: add harmonizer block extraction tests and domain filter coverage"
  ```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Task 1–6: All 6 schemas updated with entity_types + POOLE renames/additions
- ✅ Task 7: Migration script for CHARACTER/AUTHOR/PLACE renames in Neo4j
- ✅ Tasks 8–9: Domain filter expanded in graph_query, vector_query, update (read paths only; ingest write paths unchanged by design)
- ✅ Task 10: domains list shows hierarchy
- ✅ Tasks 11–12: harmonizer module + CLI command
- ✅ Task 13: Tests for harmonizer logic and domain filter presence

**No placeholders or TBDs present.**

**Type consistency:** `HarmonizeResult` defined in harmonizer.py, imported directly in cli.py. `harmonize_schema` / `harmonize_all` signatures match CLI usage.
