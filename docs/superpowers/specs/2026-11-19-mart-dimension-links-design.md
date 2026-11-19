# Mart: a fact's dimension members from edges, then declared properties, then the same chunk

Status: draft for review (2026-11-19). Parent: `2026-11-10-mart-design.md` (§7 wholes, §5 dimensions).
Public checkout: counts, ids and generic examples only.

## 1. Problem

The mart links each fact (an occurrent entity: EVENT, ACTIVITY, STATE_CHANGE) to dimension members (PERSON,
LOCATION) through one source only for places, and two for people:

| Dimension | Source today | Where |
|---|---|---|
| place | `RELATES_TO` edge from the fact to a LOCATION | `mart/graph.py` `bridges()`, `mart/tables.py` `assemble()` (`place_of`) |
| person | the same edges, **plus** the fact's `participants` text matched to names, aliases and `collectives` | `mart/wholes.py` `_people()` |

A fact with no edge to a place has no `place_key`, so `home_away` is null and a trip built from it loses its
places and days. Measured on brain01, of 445 trip-type EVENTs: 277 have a LOCATION edge, **168 do not** (60 of
those carry the place as text in their `location` property, 40 of which name an existing LOCATION entity). A trip
then shows only the edged days' places, and a day whose events all lack edges does not count as away.

The person path is better (edges plus `participants`), but it reads only the `participants` property, so
ACTIVITY and PLAN (which declare `who_involved`) get people from edges alone, and an unlisted collective phrase
("five other group members") links nobody.

So the defect is not "location". It is that **a fact's dimension members come from one fixed source per
dimension**, and the properties that carry the same information are read for one dimension and one property name.

## 2. Rule

For each fact and each dimension, collect members from three sources, in order, keeping each source's members as
its own role so a reader can tell how a link was made:

1. **Edge.** A `RELATES_TO` edge from the fact to an entity of the dimension's class (as today). Role: the
   edge's `rel_type`.
2. **Declared property.** The fact class's properties the declaration names for that dimension (§3). Each value
   (a string or each item of a list) is matched to a member by normalised name or alias, as `_people` does now;
   a value matching more than one member links none. Collectives apply to the person dimension's properties.
   Role: `property:<name>`.
3. **Same chunk.** Only when 1 and 2 gave the fact no member of that dimension: the dimension entities extracted
   from the same chunks as the fact. Role: `same_chunk`. Off unless the declaration opts in per dimension,
   because a chunk that names many places gives every event in it all of them.

Rules apply per (fact, dimension), so a fact with a place edge still gets its people from `participants`.

**What "the fact's place" is.** `fact_happening.place_key` (one value) stays the first member by source order
(edge, then property, then same chunk), so `home_away` and every view of it keep their meaning. `bridge_place`
and `bridge_person` hold every member with its role.

## 3. Declaration

Each dimension gains `from_properties`: per fact class, the property names that name members of it. Existing
declarations keep working: with no `from_properties`, the person dimension reads `participants` as today and the
place dimension reads nothing (edges only, as today).

```yaml
dimensions:
  person:
    class: PERSON
    from_properties: {EVENT: [participants], ACTIVITY: [who_involved], PLAN: [who_involved]}
    collectives: {...}            # unchanged; applies to person properties
  place:
    class: LOCATION
    from_properties: {EVENT: [location], ACTIVITY: [location]}
    same_chunk: true              # opt in to source 3 for this dimension
```

`domains validate` (`mart/declaration.py` `validate_declaration`) checks each named class is in the schema and
each property is declared on that class (error), and warns when `same_chunk` is on for a dimension whose class
has more than N members per chunk on average (measured at validate time is out of scope; the warning is static:
"same_chunk links every member named in the chunk").

## 4. Where the changes go

| File | Change |
|---|---|
| `artmind/mart/declaration.py` | `PersonDim` / `PlaceDim` gain `from_properties: dict[str, list[str]]` and `same_chunk: bool`; parse them; validate classes and properties against the schema; default person `from_properties` to `{<each fact class>: [participants]}` when absent, so old declarations keep today's behaviour. |
| `artmind/mart/graph.py` | `happenings()` returns the fact's declared dimension properties (all names in either `from_properties`) instead of the fixed `participants`; new `same_chunk_members(domains, fact_classes, dim_class)`: per fact, the entities of `dim_class` extracted from the fact's chunks (only called when a dimension opts in). |
| `artmind/mart/links.py` (new) | Pure: `link_members(fact, dim, edges, members, collectives) -> [(member_key, role)]` applying §2's order; name/alias index and collectives moved here from `wholes._people`. |
| `artmind/mart/tables.py` | `assemble()` builds `bridge_person` / `bridge_place` from `links.link_members` for every fact; `place_of` = first place member by source order; roles kept on bridge rows. |
| `artmind/mart/wholes.py` | `_people()` reads `bridge_person` (already linked), no own text matching; trips unchanged otherwise. |
| `artmind/mart/metadata.py` | Describe the new `role` values on `bridge_*` for text2sql. |
| Vault declaration (`<vault>/.artmind/domains/mart/personal_journal.yaml`) | Add `from_properties` and `same_chunk` for place; add collective phrases ("group members", "other family members"). A user file: edited in the vault, committed there. |
| Tests | `test_mart_graph.py` (the Cypher and params `happenings` and `same_chunk_members` send), `test_mart_tables.py` (bridge rows and roles per source; `place_of` order), new `test_mart_links.py` (order, ambiguity, collectives, list values, a fact with an edge still getting property members), `test_mart_declaration.py` (parse, defaults, validation errors), `test_mart_wholes.py` (a trip day whose events have no edges is away through the property source). |

No change to the graph, extraction or the schema.

## 5. Effect on brain01 (expected, to be measured after the build)

- The trips that today lose places (three of the eleven lose a whole day's places: a middle day, a first day and a
  last day) gain them through `location` (source 2) or the same chunk (source 3).
- The villa day keeps the narrator's son through the collective phrase.
- Measure: for the 11 trips with the son (Q15 plus the two the label omits), places and days in
  `mart_whole_trip` before and after; the number of facts with a `place_key` before and after; no trip gains a
  person who is not in its notes (spot-check 10).

## 6. Out of scope

- Re-extraction to add the missing edges (the information is already in properties and chunks).
- The ORGANIZATION or OBJECT dimensions (none declared); the rule is written so a later dimension takes the same
  three sources.
