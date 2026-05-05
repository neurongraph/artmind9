---
name: artmind-create-schema
description: Creates a new domain schema YAML for the artmind knowledge graph system. Given a domain name and example documents, produces a fully-specified schema with entity classes, properties guidance, and relationship patterns tuned to the domain's content.
---

# artmind Schema Creator

Use this skill to author a new `domains/schemas/{name}_schema.yaml` for the artmind system. A domain schema drives all three stages of artmind's ingestion pipeline — entity extraction, property enrichment, and relationship extraction — so the quality of the schema directly determines the quality of the knowledge graph.

## Required Inputs

- `domain`: The domain name (lowercase, underscored — e.g. `fiction`, `personal_journal`). Ask if not provided.
- `description`: A one-sentence summary of the kinds of documents this domain covers. Ask if not provided.
- `sample documents`: One or more representative documents from the domain. Read them carefully before designing anything.

If sample documents have not been provided, ask the user to supply at least one before proceeding. Schema quality depends entirely on grounding entity classes in real content.

## Reference Assets

This skill ships with two worked examples in `assets/`. Read them before writing the new schema:

| File | Purpose |
|---|---|
| `assets/fiction_schema.yaml` | Gold-standard template — study its structure, prompt style, and visual conventions |
| `assets/fiction_extract.md` | The kind of document that produced the fiction schema |
| `assets/personal_journal_schema.yaml` | Second example — same template applied to a very different domain |
| `assets/personal_journal_extract.md` | The kind of journal entry that produced the personal_journal schema |

Read at least `fiction_schema.yaml` and one extract before writing. The two schemas together show how the same template adapts across very different content.

## Process

### Step 1 — Read the reference template

Read `assets/fiction_schema.yaml`. Internalise:
- The three-section YAML structure (`entities_prompt`, `properties_prompt`, `relationships_prompt`)
- The visual conventions (━━━ dividers, □ checklist items, `__text__` / `__list_of_entities__` anchors)
- The `{text}` and `{entities_list}` placeholder positions
- The tone: precise, analytical, domain-specific — not generic

Also read `assets/personal_journal_schema.yaml` to see how the same template adapts when the domain is personal and narrative rather than formal and dramatic.

### Step 2 — Read the sample documents

Read all provided sample documents. As you read, note:
- **What kinds of things appear?** People, places, events, systems, ideas, objects, organisations, emotions?
- **What makes kinds distinct?** A CHARACTER is different from a LOCATION is different from an EVENT — each participates in different relationships and warrants different properties.
- **What relationships are most common?** Who acts on what? What contains what? What causes what?
- **What questions would a domain expert ask?** ("Who is connected to X?" "What happened at Y?" "What does Z achieve?") — these drive property design.
- **What vocabulary is specific to this domain?** Use domain-native verb phrases in rel_type values.

### Step 3 — Design entity classes

A good schema has **5–8 entity classes**. More than 8 creates noise; fewer than 5 loses resolution.

For each candidate class, ask:
1. Is this a fundamentally different *kind of thing* from the other classes?
2. Does it participate in meaningfully different relationships?
3. Would a domain expert want to query it separately?

If yes to all three, it earns its own class.

**Class definition structure (one block per class in the prompt):**

```
CLASS_NAME
  One sentence on what qualifies — who/what is included, important edge cases.
  example type values: subtype_a | subtype_b | subtype_c | ...
```

Types are fine-grained subcategories within a class. They need not be exhaustive — the extraction rules tell the LLM it can add more.

**Common mistakes:**
- Classes that are too abstract (`THING`) — each class must be specific enough to have distinct relationship patterns
- Classes that are really subtypes of another class (`VILLAIN` when `CHARACTER` with type `antagonist` would do)
- Missing the "implicit" important class — e.g. in fiction, CONCEPT (themes, mysteries) is easy to forget but valuable; in governance docs, DECISION is easy to overlook

### Step 4 — Write `entities_prompt`

Follow the fiction schema structure exactly:

1. **Opening sentence:** `You are a Knowledge Graph extraction engine specialised in {domain} analysis.`
2. **ENTITY TYPES section:** one block per class, with description and example type values
3. **EXTRACTION RULES section:** keep all five standard rules (completeness, canonical names, description quality, context snippets, no hallucination) — **adapt the examples to the domain**
4. **OUTPUT FORMAT section:** use the standard JSON shape; adapt the `id` prefix examples to this domain (e.g. `char_001`, `loc_002` for fiction vs. `per_001`, `plc_002` for journal)
5. **QUALITY CHECKLIST section:** tailor each □ item to what matters most for this domain

End the prompt with the two anchors exactly as shown:
```
  __text__

  {text}
```

### Step 5 — Write `properties_prompt`

Properties use an open schema — no fixed fields. The guidance is organised per entity class.

For each class, write a `For CLASS_NAME, consider:` block with:
- 6–10 property suggestions as indented bullet points
- Each suggestion: a property name and brief explanation of what it should contain
- Use the framing: *"If a domain expert asked a question about this entity, what structured facts would help answer it?"*

Include the standard `KEY RULES FOR PROPERTIES` section (7 rules — copy from fiction schema, they are universal).

End with the standard OUTPUT block and QUALITY CHECKLIST, then the two anchors:
```
  __list_of_entities__
  {entities_list}

  __text__
  {text}
```

### Step 6 — Write `relationships_prompt`

The relationships prompt defines the graph's edge vocabulary.

Structure:
1. Opening sentence (same pattern as other prompts)
2. **RELATIONSHIP STRUCTURE section** explaining the pattern shape with a code example
3. **COMMON rel_type VALUES section:** one block per meaningful class-pair, listing rel_type strings
4. **EXTRACTION RULES section:** keep the standard rules, adapt examples to domain
5. **OUTPUT section:** copy the fixed JSON shape from fiction schema exactly
6. **QUALITY CHECKLIST section:** tailor □ items to this domain's characteristic relationship patterns

**Relationship design principles:**
- Cover every meaningful cross-class pairing, not just same-class
- Use domain-native verb phrases: `resides_at` not `is_at`; `trained_on` not `uses`
- Direction should read naturally: `(CHARACTER)-[VISITS]->(LOCATION)` not the reverse
- Offer 6–12 rel_types per class-pair — specific enough to be meaningful, not so many they overlap
- Add a `properties` guidance note for at least the 3–4 most important rel_types in the domain

End with the two anchors:
```
  __list_of_entities__
  {entities_list}

  __text__
  {text}
```

### Step 7 — Save and verify

Write the completed schema to:
```
domains/schemas/{domain_name}_schema.yaml
```

Then verify:
- The file is valid YAML (no unescaped special characters inside block scalars)
- `{text}` appears at the end of `entities_prompt`
- `{entities_list}` and `{text}` both appear at the end of `properties_prompt` and `relationships_prompt`
- The `name:` field at the top matches the filename stem

## YAML Structure Reference

```yaml
name: {domain_name}
description: {one-sentence description of documents in this domain}

entities_prompt: |
  ...prompt text...

  __text__

  {text}

properties_prompt: |
  ...prompt text...

  __list_of_entities__
  {entities_list}

  __text__
  {text}

relationships_prompt: |
  ...prompt text...

  __list_of_entities__
  {entities_list}

  __text__
  {text}
```

The `|` block scalar is required for all three prompts — they are multi-line strings. All content inside a prompt must be indented consistently (2 spaces from the `|` level).

## Final Quality Checklist

```
□ name and description fields at the top of the YAML
□ All three prompts present: entities_prompt, properties_prompt, relationships_prompt
□ Each prompt opens with the domain-specialised analyst sentence
□ entities_prompt defines 5–8 entity classes, each with description and example type values
□ entities_prompt ends with __text__ anchor and {text} placeholder
□ properties_prompt has a "For CLASS:" block for every entity class
□ properties_prompt ends with {entities_list} and {text} placeholders
□ relationships_prompt covers all major class-pair combinations
□ relationships_prompt ends with {entities_list} and {text} placeholders
□ Domain-specific vocabulary used throughout (no generic placeholders left in)
□ YAML parses without errors
```
