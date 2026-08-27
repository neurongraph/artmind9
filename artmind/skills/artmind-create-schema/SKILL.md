---
name: artmind-create-schema
description: Creates a new domain schema YAML for the artmind knowledge graph system. Given a domain name and example documents, produces a fully-specified schema declaring entity classes, their properties, and their relationships — the structured data every extraction prompt is assembled from at runtime.
---

# artmind Schema Creator

Use this skill to author a new `domains/schemas/{name}_schema.yaml` for the
artmind system. A domain schema declares entity classes, their properties,
and their relationships as **structured data** — `artmind/prompt_builder.py`
assembles all three extraction prompts (entities, properties, relationships)
from this declaration plus the shared boilerplate in `domains/meta.yaml` at
runtime. You never write prompt text; you declare what the domain contains.

## Required Inputs

- `domain`: The domain name (lowercase, underscored — e.g. `fiction`, `personal_journal`). Ask if not provided.
- `description`: A one-sentence summary of the kinds of documents this domain covers. Ask if not provided.
- `sample documents`: One or more representative documents from the domain. Read them carefully before designing anything.

If sample documents have not been provided, ask the user to supply at least one before proceeding. Schema quality depends entirely on grounding entity classes in real content.

## The meta-schema contract

Every schema must satisfy `domains/meta.yaml`'s contract, enforced by
`artmind domains validate` (run automatically by `artmind init`, and you
should run it yourself before considering a schema done):

- **`entity_types` is a map**, `CLASS_NAME: {kind, description, type_examples,
  properties, relates_to, guidance}` — never a list. A list is the
  pre-redesign format and is rejected outright.
- **`kind` is mandatory on every class**, and must be `recurrent` or
  `occurrent` (see below). A class missing it fails validation loudly rather
  than defaulting silently.
- **No class name, property name, or `relates_to` target may start with
  `_`** — that prefix is reserved for artmind's own computed fields
  (`_id`, `_domain`, `_valid_from`, ...). A schema that declares one is
  rejected.

## Reference Assets

This skill ships with two worked examples in `assets/` — **live copies of
the real, currently-shipping schemas**, not illustrative fakes:

| File | Purpose |
|---|---|
| `assets/fiction_schema.yaml` | Gold-standard template — study its `entity_types` map structure: how `kind`, `properties`, and `relates_to` are declared per class |
| `assets/fiction_extract.md` | The kind of document that produced the fiction schema |
| `assets/personal_journal_schema.yaml` | Second example — same structure applied to a very different domain, including a `temporal:` block |
| `assets/personal_journal_extract.md` | The kind of journal entry that produced the personal_journal schema |

Read at least `fiction_schema.yaml` and one extract before writing. The two
schemas together show how the same structure adapts across very different
content — and since they're real files, `artmind domains entities-prompt
fiction` (etc.) shows you exactly what each one assembles into.

## Process

### Step 1 — Read a real schema's structure

Read `assets/fiction_schema.yaml` end to end. Internalise:

- `entity_types` is a map: each key is a class name, each value declares
  `kind`, `description`, `type_examples`, `properties`, `relates_to`, and an
  optional `guidance`.
- A property is `name: {hint: "...", temporal: valid_from}` — `hint` is
  optional free text guiding the extractor, `temporal` is present only on
  the one or two properties that carry the entity's own date (see Step 7).
- `relates_to` is `OtherClass: [rel_type, rel_type, ...]`, declared from one
  side of the pair only (fiction's `PERSON.relates_to.LOCATION` covers
  `PERSON ↔ LOCATION`; `LOCATION` doesn't repeat it back).
- A `relates_to` entry can carry an inline properties hint as part of the
  string itself when one rel_type needs it — see `PERSON.relates_to.EVENT`'s
  `'participates_in (properties: {role: investigator|victim|...})'`.
- Top-level `guidance: {entities: "...", properties: "...", relationships:
  "..."}` (see fiction's bottom section) is for a judgment call that applies
  across the whole domain, not to one class — used sparingly.

There is no `entities_prompt`/`properties_prompt`/`relationships_prompt`
field anywhere, and no `{text}`/`{entities_list}` placeholder for you to
manage — those are runtime concerns `prompt_builder.py` and `extraction.py`
own entirely.

Also read `assets/personal_journal_schema.yaml` to see the same structure
applied to a personal/narrative domain, plus a worked `temporal:` block.

### Step 2 — Read the sample documents

Read all provided sample documents. As you read, note:
- **What kinds of things appear?** People, places, events, systems, ideas, objects, organisations, emotions?
- **What makes kinds distinct?** A PERSON is different from a LOCATION is different from an EVENT — each participates in different relationships and warrants different properties.
- **What relationships are most common?** Who acts on what? What contains what? What causes what?
- **What questions would a domain expert ask?** ("Who is connected to X?" "What happened at Y?" "What does Z achieve?") — these drive property design.
- **What vocabulary is specific to this domain?** Use domain-native verb phrases in rel_type values.

### Step 3 — Design entity classes, and assign `kind` to each

**Start from the POOLE+ base types.** `PERSON`, `LOCATION`, `ORGANIZATION`, `OBJECT`, and
`EVENT` are universal — every built-in schema maps its domain's people, places, groups,
things, and happenings onto these five before adding anything domain-specific (a fiction
character is a `PERSON`, a fictional setting is a `LOCATION`, not a bespoke `CHARACTER`/
`PLACE`). Only introduce a new class for a kind of thing that doesn't fit any of the five.

A good schema has **5–8 entity classes total**, POOLE+ base types included. More than 8
creates noise; fewer than 5 loses resolution.

For each candidate *domain-specific* class (beyond the POOLE+ base types), ask:
1. Is this a fundamentally different *kind of thing* from the other classes?
2. Does it participate in meaningfully different relationships?
3. Would a domain expert want to query it separately?

If yes to all three, it earns its own class.

**Every class needs a `kind` — this is not optional and the validator fails
without it.** Ask, for each class: does an instance persist and change over
time, or is it a completed point event?

- **`recurrent`** — persists and can change (a rate, a policy, a role, most
  people/places/organizations). Two documents disagreeing about a recurrent
  property may be temporal variation, not conflict.
- **`occurrent`** — a completed point event (an incident, a filed report, a
  decision made once). Two documents disagreeing about an occurrent
  property is always a conflict — a finished event's attributes don't drift.

**The name-is-identity rule — the single biggest lever on extraction
quality**: a `recurrent` class's name must never embed a measurement, an
amount, a percentage, or a date. `"SmartSaver Account Tier 2 Rate"` is
right; `"SmartSaver Account Tier 2 Rate — 4.60% AER, effective 2026-02-01"`
is wrong — that value belongs in a property, and a name carrying it can
never be recognised as the same thing again in the next document. An
`occurrent` class is the opposite: the date or a distinguishing identifier
belongs IN the name, because that's what tells one occurrence apart from
another (`meta.yaml`'s `kind_naming_rules` renders both instructions into
the actual extraction prompt, so this reaches the model, not just you).

**Common mistakes:**
- Classes that are too abstract (`THING`) — each class must be specific enough to have distinct relationship patterns
- Classes that are really subtypes of another class (`VILLAIN` when `PERSON` with type `antagonist` would do)
- Missing the "implicit" important class — e.g. in fiction, CONCEPT (themes, mysteries) is easy to forget but valuable; in governance docs, DECISION is easy to overlook
- Getting `kind` backwards — a class whose instances are individually dated events (`AUDIT_FINDING`, `TRANSACTION`) is occurrent even if the *class* sounds ongoing; a class that sounds like an event but actually persists and gets updated (`RISK_METRIC`, tracked over time) is recurrent

### Step 4 — Declare each class

One map entry per class in `entity_types`:

```yaml
entity_types:
  CLASS_NAME:
    kind: recurrent | occurrent
    description: One or two sentences — who/what is included, important edge cases.
    type_examples:
    - subtype_a
    - subtype_b
```

`type_examples` are fine-grained subcategories within the class. They need
not be exhaustive — `meta.yaml`'s shared prompt tells the extractor it may
add more.

### Step 5 — Declare properties per class

Properties are an open schema — no fixed fields beyond `entity_common_properties`
(`name`, `aliases`, `entity_class`, `type`, `description`, `context`, always
present regardless of what you declare). For each class:

```yaml
    properties:
      property_name:
        hint: what this should contain, and its expected FORMAT
      another_property: {}   # no hint needed — the name is self-explanatory
```

6–10 properties per class is typical. Frame each one by asking: *"If a
domain expert asked a question about this entity, what structured fact
would help answer it?"*

**A hint that pins a format extracts identically across chunks; one that
doesn't, doesn't.** This is the single most consequential thing to get
right, found the hard way on a live corpus: `rate_value: {hint: "numeric,
e.g., 4.50"}` came back identical from four separate chunks: `balance_max:
{hint: "upper bound of balance range"}` — no format named — came back as
`£50,000`, `50000`, and `£50k` from the *same* document, because nothing
told the extractor which representation to use. Before finalizing a hint on
any property whose value could be written more than one way (currency,
percentages, dates, ranges), name the exact format with a worked example.

**Key rules:**
1. Use clear, readable key names (`fee_amount`, not `amt`).
2. Use a list-typed property for multiple values, not repeated keys.
3. Nest only when genuinely needed — most properties are flat scalars.
4. If a property feels forced or uncertain, leave it out; the extractor is
   told to omit a property rather than write `null`/`"unknown"`.
5. If a hint is really a qualifier attached to a specific case rather than
   an atomic property (e.g. "the example from the document verbatim, but
   only if it's for THIS tier"), write that qualifier into the property's
   own `hint` rather than folding it into the property name.

### Step 6 — Declare relates_to per class

```yaml
    relates_to:
      OtherClass:
      - rel_type_one
      - rel_type_two
```

Declare each meaningful cross-class pairing from **one side only** — don't
repeat `OtherClass.relates_to.CLASS_NAME` back the other way (`prompt_builder.
relationship_pairs` already treats the pair as undirected for the prompt's
COMMON rel_type VALUES listing; direction in the actual extracted edge comes
from the text, not from which side declared it).

- Use domain-native verb phrases: `resides_at` not `is_at`; `trained_on` not `uses`.
- Offer 6–12 rel_types per class-pair — specific enough to be meaningful, not so many they overlap.
- Where one rel_type needs its own properties, say so inline in the string
  itself: `'participates_in (properties: {role: investigator|victim|...})'`
  — see fiction's `PERSON.relates_to.EVENT` for the real example.
- A same-class self-relation (`RATE_ENTRY.relates_to.RATE_ENTRY`) is
  legitimate — banking.reference uses it for `higher_tier_than` between
  adjacent rate tiers.

### Step 7 — The temporal block, and per-property temporal tagging

Two independent things, both optional:

**1. A property carrying the entity's own date**, tagged inline where you
already declared it:

```yaml
    properties:
      effective_date:
        hint: when this rate applies from
        temporal: valid_from
```

`temporal` accepts exactly two values: `valid_from` and `valid_to` — nothing
else. This is what lets one document's own dated properties override the
document's own date for that specific fact (`docs/projection-pipeline.md`'s
"two valid-time axes"). Tag it on **any** class regardless of `kind` — an
`occurrent` class needs this just as much as a `recurrent` one; there is no
separate `event_at` axis. A property tagged anything else (including the
retired `event_at`) is silently ignored by the observation builder, not an
error — so a typo here fails silently. Double-check the two spellings.

**2. A `temporal:` block at the schema's top level**, for the *document's*
own date (not an entity's):

```yaml
temporal:
  document:
    valid_from: [Effective Date, effective_date]
  relative_anchor: document.valid_from
```

Include this only when documents in this domain carry their own date
(a "Version"/"Effective Date" header) or express dates relative to
themselves (a journal entry's "today"). `relative_anchor` names which
document-level date resolves a relative phrase like "next Tuesday." Both are
optional — omit the whole block for a domain with no natural document date.

**Inheritance, if this is a child schema** (e.g. `banking.risk_governance`
under `banking`): the child's `temporal:` block is deep-merged underneath
its parent's automatically at load time — you only need to declare what the
child adds or overrides. This happens whether or not you ever run
`domains harmonize`; harmonize only copies missing `entity_types` classes,
never `temporal:`.

### Step 8 — Domain-wide guidance (optional)

For a judgment call that applies across the whole domain rather than to one
class, use the top-level `guidance:` map instead of repeating it in every
class's own `guidance`:

```yaml
guidance:
  entities: Capture every rate tier for each product, even ones the document only tabulates.
  relationships: Character arcs (how a relationship between two PERSON entities evolves) are captured with multiple edges across the story, not collapsed into one.
```

Each of the three keys (`entities`, `properties`, `relationships`) is
rendered into its matching prompt, after the universal rules and after every
class's own guidance — reach for it only when the instruction doesn't
belong to a single class.

### Step 9 — Save and verify

Write the completed schema to:
```
domains/schemas/{domain_name}_schema.yaml
```

Then verify, in order:

```bash
artmind domains validate --domain {domain_name}
```
Fails loudly on a missing `kind`, a list-form `entity_types`, or a reserved
`_`-prefixed name — fix everything it reports before moving on.

```bash
artmind domains entities-prompt {domain_name}
artmind domains properties-prompt {domain_name}
artmind domains relationships-prompt {domain_name}
```
These print the **exact prompt text** extraction will use, assembled at
runtime from what you just wrote — read all three and confirm each class,
property hint, and rel_type list appears where you expect. This is the real
verification step: if something reads wrong here, it will read wrong to the
extractor too.

If this is a child schema in an existing family and you want it to inherit
classes it didn't declare:
```bash
artmind domains harmonize --domain {domain_name} --dry-run   # preview what would be added
artmind domains harmonize --domain {domain_name}
```

Finally, a light manual check:
- The `name:` field at the top matches the filename stem.
- `subject`/`persona` are set if the defaults (description-derived subject,
  "a subject-matter analyst") don't fit this domain's voice.

## YAML Structure Reference

```yaml
name: {domain_name}
description: {one-sentence description of documents in this domain}
subject: {optional — feeds {{SUBJECT}} in the assembled prompt; defaults to description[:120]}
persona: {optional — feeds {{PERSONA}}; defaults to "a subject-matter analyst"}

temporal:                              # optional — only if documents carry their own date
  document:
    valid_from: [Label, alt_label]
  relative_anchor: document.valid_from

entity_types:
  CLASS_NAME:
    kind: recurrent | occurrent        # mandatory
    description: One or two sentences.
    type_examples:
    - subtype_a
    - subtype_b
    properties:
      property_name:
        hint: what this should contain, and its expected format
        temporal: valid_from           # optional, only on the class's own date property
      another_property: {}
    relates_to:
      OtherClass:
      - rel_type_one
      - rel_type_two
    guidance: Optional class-specific instruction, rendered after the naming rule.

guidance:                              # optional — schema-wide, not per-class
  entities: ...
  properties: ...
  relationships: ...
```

## Final Quality Checklist

```
□ name and description fields at the top of the YAML
□ entity_types is a MAP (CLASS: {...}), never a list
□ every class declares kind: recurrent or occurrent — no exceptions
□ no class/property/relates_to name starts with an underscore
□ recurrent class names carry no measurement, amount, percentage, or date
□ occurrent class names DO carry a distinguishing date/identifier
□ 5–8 entity classes total, POOLE+ base types included
□ every non-obvious property's hint pins a FORMAT when the value could be
  written more than one way (currency, dates, ranges, percentages)
□ at most one or two properties per class tagged temporal: valid_from/valid_to
  — spelled exactly that way, nothing else (not event_at)
□ relates_to covers all major class-pair combinations, declared from one side only
□ domain-specific vocabulary used throughout (no generic placeholders left in)
□ `artmind domains validate --domain {name}` reports no violations
□ `artmind domains entities-prompt/properties-prompt/relationships-prompt {name}`
  read correctly end to end
```
