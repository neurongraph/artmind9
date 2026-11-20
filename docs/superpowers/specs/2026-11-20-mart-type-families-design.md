# Mart type families: closed vocabulary and adding a family

Date: 2026-11-20. Status: **analysis only, nothing built; decision open** (§5). Parent spec:
`2026-11-10-mart-design.md` (§ dimensions). Related: the Curate → Mart review pane (`artmind/webui/static/curate_panes.js`,
`artmind/mart/review.py`). Public checkout: generic examples only.

## 1. Question

Reviewing the type dimension in the Curate → Mart pane raised two things: a type shows only one level
(`family`), and the `family` dropdown cannot take a new value. For example `school` should land under an
`education` family, and `education` is not offered.

## 2. How it works today

- **One level.** `LEVELS["type"] = ["family"]` (`artmind/mart/llm.py`). Place has five levels (venue, locality,
  city, region, country), person three (group, relation, side). `dim_type` in the mart is `(type, family)`.
  `school → education` is therefore a family *value*, not a second level under family.
- **Closed vocabulary.** The values offered are the keys of `dimensions.type.families` in
  `<run folder>/domains/mart/<domain>.yaml`. Three places refuse anything else:
  1. the LLM prompt (`llm.prompt`: "`family` must be one of …");
  2. the LLM answer check (`llm.parse` rejects a family outside the declared set);
  3. the review batch check (`review.check_levels`, behind `POST /api/curate/mart/batch`).
- **Consequence.** A type no declared family fits is placed in the nearest one (a `care` event under `health`, an
  `administrative` one under `work`), because nothing better exists. Wholes read `type_family`
  (`wholes.*.where.type_family`), so a wrong family can move a fact into or out of a trip or life event.
- **Order of decision** (unchanged): user record, then rules (`families` / `families_by_name`), then seeds, then
  the LLM. A user record beats a rule, so an accepted type keeps its family until changed.

## 3. Adding a family today (no code)

1. Add `education: [school, …]` under `dimensions.type.families` in the domain's mart YAML.
2. `artmind mart enrich --domain D` (the rule step places listed type values before the LLM is asked).
3. `artmind mart build --domain D` (no LLM).
4. Re-review pending types in the pane. Types already accepted keep their old family until changed.

`wholes` use a new family only if it is listed under their `where.type_family`.

## 4. Options

**A. A new family is just another family (small).** Add an "Add family…" choice at the bottom of the type
dropdown. It previews, then appends the family and its members to the mart YAML, and the batch check, rules and
LLM prompt all pick it up because they read the declaration. The pane notes that wholes ignore the family unless
listed. The YAML is a committed vault file, so this is a deliberate write with a preview (tier 1, `effect` block,
`--dryRun`), never a side effect of Accept. Needs a CLI command first (e.g. `mart family add`), then the route and
the pane, so the library owns the write as with every other write.

**B. A real second level under family (larger).** `LEVELS["type"]` gains a level; `dim_type` gains a column;
`mart/metadata.py` and text2sql descriptions change; the declaration, `domains validate`, rules and the LLM prompt
learn the second level; existing records have one level, so a migration or tolerant read is needed. Worth it only if
questions group by the finer level and the coarse one together.

## 5. Open decision

Is `education` just another family (A, recommended starting point), or a real hierarchy under family (B)? Build
neither until decided. If A: the CLI command, then the route, then the pane option, each with a dry-run test
asserting what would be written.
