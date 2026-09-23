# Table mapping reference (`ingest table2graph`)

A table mapping declares how the rows of a structured table (csv/xlsx ingested
with `artmind ingest sync`) become graph entities and relationships under a
domain schema. `artmind ingest table2graph TABLE` executes it deterministically,
with no LLM: the same table and mapping always produce the same graph.

**Where it lives:** `domains/table_mappings/<name>.yaml` in the run folder (or
vault) — a sibling of `domains/schemas/`, never inside it. `artmind init` does
not touch this directory, and `snapshot` curation archives it. One file per
kind of table; its `table:` glob picks which tables it applies to.

## Worked example

```yaml
table: hr_export_*                 # glob(s) over registered table names
domain: people                     # optional; must equal the table's domain
description: HR export, one row per employee.

null_values: ["--", "N/A", "No Value Available"]   # cells (and transform results) meaning "no value"
row_key: [EmployeeID]              # names each row's chunk (default: the table's
                                   # business key, else the row number)
row_text:                          # what each row's chunk text shows
  exclude: [Free Text Notes]       # (or include: [...] to whitelist)
document:
  valid_from: $ingested_at         # replace-mode snapshot date: $ingested_at or YYYY-MM-DD

entities:
  person:                          # an alias, used by templates and relationships
    class: PERSON                  # must be a class in the domain schema
    name: "{first_name} {last_name?} ({employee_id})"  # display name AND identity; {x?} = optional
    type: employee                 # optional template
    description: "Band {band} employee"                # optional template
    versions: latest               # temporal tables: latest (default) | all
    when:                          # optional: build only for rows passing ALL
    - {column: Status, not_in: [Terminated]}
    properties:
      employee_id: {column: EmployeeID, required: true}
      first_name:
        column: Employee Name                        # "LAST, First"
        transform: [{split: {sep: ",", index: 1}}, title]
      last_name:
        column: Employee Name
        transform: [{split: {sep: ",", index: 0}}, title]
      full_name: {template: "{first_name} {last_name?}"}
      band: Band                                     # shorthand for {column: Band}
      is_manager: {column: "Manager?", map: {Y: "yes", N: "no"}}
      hire_date: {column: Hire Date, transform: date}

  review:
    class: PERFORMANCE_REVIEW
    name: "{year} Review - {person.name}"           # may use earlier entities
    properties:
      rating:
        column: Rating
        map: {Exceeds: high, Meets: core, Below: low}
        default: not_applicable                      # when the cell is a null token
      reviewed_on: {column: Review Date, transform: date}
      year:
        column: Review Date
        transform: year
        default: {column: $ingested_at, transform: year}   # fallback field

  manager:
    class: PERSON
    lookup:                        # resolve to ANOTHER row's entity
      entity: person               # ...the `person` built from that row
      column: Manager Name         # this row's value
      match_column: Employee Name  # compared against this column of every row
      prefer:                      # tie-breaks when several rows match
      - {column: "Manager?", equals: "Y"}
      on_unresolved: stub          # stub | skip  (value matches no row)
      on_ambiguous: skip           # skip | stub | first  (still >1 after prefer)
    # Only used for a stub (a manager with no row of their own):
    name: "{first_name} {last_name?}"
    properties:
      first_name: {column: Manager Name, transform: [{split: {sep: ",", index: 1}}, title]}
      last_name: {column: Manager Name, transform: [{split: {sep: ",", index: 0}}, title]}
      roles: {value: [people_manager]}

relationships:
- {source: person, rel_type: reports_to, target: manager}
- {source: person, rel_type: received, target: review}
```

## Fields

A property (and `default:`) is a **field spec** — a column name, or a map with
exactly one source:

| key | meaning |
|---|---|
| `column` | a table column, or a pseudo-column: `$ingested_at` (table ingest timestamp), `$table` (table name), `$valid_from` (the row's valid-from date) |
| `value` | a literal (scalar or list) |
| `template` | `"{prop} ..."` over this entity's **earlier** properties and earlier entities (`{alias.name}`, `{alias.prop}`); `{prop?}` is optional — it renders as nothing when empty (whitespace is then collapsed), where a missing required `{prop}` makes the whole value empty |
| `transform` | one name or a list, applied in order (below) |
| `map` | `{from: to}` applied after transforms; exact match, then case-insensitive; unmatched values pass through |
| `default` | used when the value is empty (a null token, or a transform yielded nothing): a literal, or a nested field spec |
| `required` | `true` — an empty value skips the whole entity for that row (counted in the report) |

Transforms: `strip`, `lower`, `upper`, `title` (only re-cases words that are
ALL CAPS or all lower, so `McDonald` survives), `string`, `number`, `integer`,
`boolean`, `date` (ISO `YYYY-MM-DD`), `year`, and parameterised
`{split: {sep: ",", index: 1}}`, `{round: 2}`, `{replace: {"%": ""}}`.

Conditions (`when`, `prefer`): `{column: C, equals: V}`, `{column: C, in: [...]}`,
`{column: C, not_in: [...]}`, `{column: C, present: true|false}`.

## Rules that matter

- **`name` is the identity.** The rendered name is the entity's aggregate key
  (case/whitespace-normalized only), so it must be unique per real-world thing
  and stable across refreshes. Put the natural key in it when the display part
  can collide — three different "Pooja Jain" rows need `"{...} ({talent_id})"`.
  Extraction strips a digit-bearing trailing parenthetical from names it
  *infers*; a mapping's declared name is never stripped.
- **Occurrent names carry their occurrence, not their values.** A yearly review
  is `"{year} Review - {person.name}"`; putting `{rating}` in the name would
  make a re-rated review a second entity.
- **Every property should be declared on the class in the schema.** Undeclared
  properties and rel_types still write, but `--dryRun` reports them as
  warnings: update the schema so queries and extraction prompts know them.
  Never map `name`, `type`, `description` or `_`-prefixed keys as properties.
- **Relationships connect aliases**, and their `rel_type` should appear in the
  schema's `relates_to` for that class pair. A row whose endpoint wasn't built
  is skipped and counted. A self-reference (someone their own manager) is
  dropped.
- **Lookups** run after every row's other entities exist, so any entity can be
  a lookup target; nothing but relationships (and the lookup's own stub) may
  reference a lookup. Resolution matches normalized (case/space) values. A stub
  has no natural key, so its name is name-only — it will not merge with the
  same person later seen with a key; curate that with same-as.
- **Temporal (SCD-2) tables:** every row version is projected; `versions:
  latest` keeps one observation per identity (the newest version that
  produced it — a 2025 review survives a 2026 refresh with its last values),
  `all` keeps every version (use on a `recurrent` class to record temporal
  variation). A relationship is asserted only by the row version whose
  endpoint observation was kept, so a changed manager replaces the old edge.
- **Order matters** within `properties` (templates see earlier ones) and among
  non-lookup `entities` (templates see earlier entities).

## Verify

```bash
artmind db schema TABLE --compact                 # columns + value profiles
artmind db sql 'SELECT "Col", count(*) FROM <domain>__<table> GROUP BY 1'
artmind ingest table2graph TABLE --dryRun         # every error at once; counts; warnings
artmind ingest table2graph TABLE                  # commit (re-run replaces)
artmind query entity-resolve "Some Name" --domain DOMAIN
```
