# Studio: guided editing of views and the mart declaration

Date: 2026-11-20. Status: draft for review.

## 1. Purpose and scope

Operators edit a vault's YAML by hand. The files are validated only when a command runs,
and some edits (a changed view) are refused without a `version` bump. **Studio** is a new
top-level section of the admin console (beside Console and Dashboard) for editing them
through forms with live validation, a diff and a test run.

First version: **views** (`<ARTMIND_HOME>/domains/views/<name>/{view.yaml,SKILL.md}`) and
**mart declarations** (`<ARTMIND_HOME>/domains/mart/<domain>.yaml`). Schemas, topic plans,
table mappings, insights config and metadata come later, each as a new typed editor on the
same shell. Out of scope: AI drafting of Cypher, a visual query builder, editing in the
Obsidian plugin.

## 2. Rules Studio follows

1. **The CLI is the only write path.** Studio never writes a vault YAML itself. Validate,
   Test and Save call `artmind` commands in process through the `_cli` helper Curate uses
   (`curate_routes._cli`: `CliRunner`, `--compact`). A rule lives in one place, so the web
   UI and the terminal cannot disagree about what is valid.
2. **Studio never commits.** Commits stay with the Obsidian Git plugin. Save writes the file
   and reports it; Studio shows what is still uncommitted (section 6).
3. Every write takes `dry_run` and returns the command's `effect` block (CLI uniformity).
4. The data-model gate applies: write routes depend on it and answer 409 when the vault is
   behind.

## 3. Architecture

```
static/studio.js            shell: rail, draft handling, validate/diff/test/save bar
static/studio_views.js      views editor
static/studio_mart.js       mart editor
webui/studio_routes.py      /api/studio/* (thin; runs CLI commands)
artmind/mart/declaration_cli.py   new: `artmind mart declaration validate|save`
```

The left rail lists Views and Mart declarations with state badges (valid, invalid, draft,
saved-not-committed). The main pane is the selected file's editor. A **Raw YAML** toggle
shows the draft as text; form and text stay in sync (text that no longer parses keeps the
form read-only with the parse error).

### Drafts

A draft is the only state Studio owns. It is kept in the browser and mirrored to
`<ARTMIND_HOME>/logs/studio-drafts/<type>/<name>/` (gitignored `logs/`, so a refresh or a
restart loses nothing and the vault repo never sees it). The live file is untouched until
Save. A draft records the live file's content hash when it was opened; if the file changed
on disk since, Save refuses and shows a three-way notice (reload, or overwrite).

## 4. Views editor

Form fields map one to one onto `view.yaml` (format reference
`artmind/skills/artmind-create-view/references/view_format.md`): name, version, domains,
summary, examples (1-5), params (type-specific fields, entity class picker from the
domain's schema), `max_rows`, presentation (format and table columns/headers, columns
offered from the Cypher's last `RETURN ... AS` aliases) and provenance (read-only).
**Cypher** is a code box, never a form. Beside it: Run (the Test action) and "Open in
Explore → Graph" (the existing drawer). `SKILL.md` is a second tab (frontmatter `name`
must equal the view name).

| Action | Command |
|---|---|
| Validate (debounced) | `artmind views validate <draft dir>` |
| Test | `artmind views test <draft dir>` (rows, truncation, ambiguous or no-match entities, `warnings`) |
| Save | `artmind views save <draft dir>` after a `--dryRun` preview |
| Delete | `artmind views delete <name>` after a `--dryRun` preview |

Save auto-bumps `version` in the draft when the live view's Cypher, params or presentation
differ (the store refuses an unbumped change and a downgrade). The bump is shown in the
diff before it is saved. Validation errors name the rule and field; the form marks the
field.

## 5. Mart editor

The declaration has no write command today (it is hand-edited; `domains validate` and
`mart build` read it). This spec adds one (tier 1, listed in `WRITE_COMMANDS`):

- `artmind mart declaration validate --domain D [--file PATH]`: read-only. Runs
  `load_declaration` and `validate_declaration` against the domain's schema; JSON
  `errors` and `warnings`, each with the YAML path it concerns (the existing messages
  already start with it, e.g. `dimensions.type.families_by_name`).
- `artmind mart declaration save --domain D --file PATH [--dryRun]`: validates, refuses on
  errors, writes `<DOMAIN_MART_DIR>/<D>.yaml` atomically (temp file in `logs/`), and
  refuses a file whose `version` is lower than the live one. It never bumps the version
  itself. `--dryRun`
  returns `effect` (what changes, `reversible`, and that a `mart build` is needed for the
  tables to follow; `stale` lists nothing it cannot know).

Form sections follow the declaration: `home` (city, away place types), `facts`
(happening and mention classes), `dimensions` (person, place, type with families and
`families_by_name`, `from_properties`, `same_chunk`, time calendar) and `wholes`.
Class and property pickers are filled from the domain's schema, so most "not in the domain
schema" errors cannot be made. Inline messages come from the validate command. Test is
`artmind mart status --domain D` (coverage of the saved declaration); the first version
adds no build dry run, and a saved change takes effect on the next `mart build`.

### Type families

Background (from the Curate → Mart review of brain01's type proposals):

- **One level.** `LEVELS["type"] = ["family"]` (`artmind/mart/llm.py`). Place has five levels, person three.
  `dim_type` is `(type, family)`, so `school → education` is a family *value*, not a second level.
- **Closed vocabulary.** The offered values are the keys of `dimensions.type.families` in the domain's mart
  YAML. Three places refuse anything else: the LLM prompt, the LLM answer check (`llm.parse`), and the review
  batch check (`review.check_levels`). So a type no family fits gets the nearest one (a `care` event under
  `health`, an `administrative` one under `work`).
- **Wholes read it.** `wholes.*.where.type_family` decides what joins a trip or a life event, so a wrong family
  moves facts.
- **Decision order:** user record, then rules (`families` / `families_by_name`), then seeds, then the LLM. An
  accepted type keeps its family until changed.

The editor's type section lists each family with its member values and `families_by_name` words, and shows the
`wholes.*.where.type_family` lists beside it, so it is visible whether a new family joins any whole.

**Adding a family** (e.g. `education: [school]`) is a declaration edit, made here, not in the review pane. The
save preview's `effect` says what follows: `mart enrich` (rules place the listed values before the LLM), then
`mart build`, and that accepted types keep their old family. Validation warns on a family no whole lists, and
errors on a `families_by_name` family that is not declared (existing check). Until this editor exists, the manual
route is: add the family under `dimensions.type.families`, run `mart enrich`, run `mart build`, then re-review
pending types.

The Curate → Mart type dropdown ends with "Add family…", a link to this section (`?tab=studio&mart=<domain>&section=type`,
a proposed URL). The pane writes no YAML itself.

**Open decision:** is a new type family just another family (the case above), or does a real second level under
family belong here? The second level would need a new `LEVELS` entry, a `dim_type` column, metadata for text2sql,
and a tolerant read of one-level records. Build neither until decided; the editor's form would follow the
declaration if the second level is built.

## 6. Uncommitted badge

`GET /api/studio/files` returns each file with `git_state`: `clean`, `modified`,
`untracked`, read with `git status --porcelain -- <path>` in the vault (read-only; no git
write anywhere). If the vault is not a git repo the badge is absent. After Save the file
shows "saved, not committed" until the Obsidian Git plugin commits it.

## 7. Routes

All under `/api/studio/`, JSON, thin: `files` (list), `draft` (open/save/discard a draft),
`validate`, `test`, `save` (`dry_run` flag; 409 behind the data-model gate), `delete`
(views). Each runs the commands in section 4-5 and returns the command's payload
unchanged, plus `draft_hash` and `live_hash`. Text is rendered with `textContent` only.
Not traced, logs paths and lengths, never the Cypher.

## 8. Errors

CLI `ClickException` text is shown on the field or the action bar. Exit code 3 (store
failure, e.g. Neo4j unreachable for a view test) shows "Neo4j is not reachable" and keeps
the draft. A save that loses the hash race (section 3) is refused with a diff.

## 9. Testing

- `test_mart_declaration_cli.py`: validate and save, asserting the file written (and not
  written on error or `--dryRun`), refusal of a version downgrade, `effect` block.
  `test_cli_conventions.py` covers the new command (`WRITE_COMMANDS`, `--dryRun`).
- `test_studio_routes.py` with `TestClient`: each route drives the CLI through a stubbed
  `_cli` and asserts on the arguments sent; 409 behind the gate; hash-race refusal;
  `git_state` from a temp repo.
- Draft round trip (form to YAML to form) for every view param type and every mart
  section, in `studio.js` tests if the admin UI has a JS harness, else via the routes.
- Plugin fixtures: the new command's JSON is in the plugin fixture set only if the plugin
  calls it (it does not).

## 10. Docs touched

CLAUDE.md layout table (Studio row, `declaration_cli`), `artmind-create-view` and mart
skills (mention Studio as an alternative to hand editing), `justfile` has nothing to add,
`cli_guide` grouping for the new command (`test_cli_guide.py` fails otherwise).

## 11. Not decided here

Which later YAML type comes next; whether Studio replaces the read-only Views tab under
Explore (kept for now); whether type families need a second level (§"Type families"). All separate decisions.
