# CLI uniformity (P1a) — design

Date: 2026-11-02. Status: **draft for review**. Parent:
`2026-11-02-retrieval-curation-architecture-design.md` §6.2 (rules C1–C7), phase P1a. Needs
no trace data: everything here comes from the Click tree and the code, audited 2026-11-02
(161 leaf commands).

## 1. Problem

The layers being built above the CLI (the `query run` executor's guards, the admin-UI Inbox
previews, the plugin) read command output. Today that output has no common shape:

- **32 commands have no `--compact`.** Three are servers. 23 print human text (`domains`
  ×8, `ingest refine-graph`, `ingest write-to-graph`, `init`, `setup`, `vault new`, `review
  attach|prompt|regressions|status`, `session close|initiate`, `topic delete`,
  `traces feedback`, `update export|history`). Four print through other helpers (`db connect`,
  `ingest sync`, `ingest extract-kg`, `ingest pull-kg`), and `update draft`/`confirm` print
  JSON but take no `--compact`.
- **Dry run is spelled two ways:** `--dry-run` on 11 commands, `--dryRun` on 5. Ten other
  options are kebab-case against the camelCase convention (§4.2 lists them).
- **The writes operators decide on most have no dry run:** `sameas approve`/`reject`,
  `ingest resolve-conflict`, `docs retire`/`restore`/`archive`, `ingest supersede`,
  `projection rebuild`, `update confirm`/`retract`, `views save`/`delete`, `topic delete`,
  `ingest sync`. The 16 that have one report it in five shapes (`dry_run: true` plus
  command-specific counts, or nothing marking it at all, as in `ingest table2graph`).
- **`command` in the payload is inconsistent:** `"projection_synthesize"`,
  `"ingest table2graph"`, `"traces_stats"`, or absent. No code reads it (checked: the plugin
  and `traces` read the span's command, not the payload's).
- **`warnings` and `next`** exist on some payloads (the domain guard, H1–H4), not others.
- **Exit codes:** Click gives 2 for usage errors and 1 for `ClickException`. A Neo4j outage
  surfaces as an uncaught driver exception (exit 1 with a traceback), so a caller cannot tell
  "store down" from "bad input".

## 2. Goals and non-goals

**Goals.** One JSON envelope from every non-server command; one spelling per option; a
`--dryRun` with a common `effect` block on every write in tier 1; distinct exit codes; all
of it enforced by hermetic contract tests over the Click tree, so a new command cannot
regress it.

**Non-goals.** Renaming commands or merging them (C7 waits for trace data); changing any
command's behaviour or existing payload keys; `--domain` on `views show`/`topics show` (C5)
and the plan executor's use of exit codes (both land with P3); historical spec documents
(they keep the spellings of their day).

## 3. Design

### 3.1 One module owns the conventions

New `artmind/cli_conventions.py`, stdlib + Click only:

| Name | What |
|---|---|
| `compact_option` | the `--compact` decorator (today written out inline on every command) |
| `dry_run_option` | `--dryRun`, with `--dry-run` as a deprecated alias (§3.3) |
| `aliased_option(new, *old)` | any renamed option: new name shown in help, old names accepted and noted |
| `envelope(payload, ctx)` | fills the envelope keys (§3.2) |
| `Effect` | a dataclass for the `effect` block, with `to_json()` (§3.4) |
| `SERVER_COMMANDS` | `serve`, `chat-ui`, `admin-ui`: exempt from C1 |
| `INTERACTIVE_COMMANDS` | `db connect` (hands the terminal to a SQL shell): exempt from C1 |
| `WRITE_COMMANDS` | command path → tier (1 or 2); the contract test's list (§3.4) |
| `EXIT_*` | `EXIT_OK = 0`, `EXIT_INPUT = 1`, `EXIT_USAGE = 2`, `EXIT_STORE = 3` |

`cli.py` imports from it. It is the single place a later reader looks to learn the rules.

### 3.2 C1 + C4: the envelope

`_echo_json` is already the one output path for 131 call sites. It calls
`envelope(payload, ctx)` before printing:

| Key | Rule |
|---|---|
| `command` | always the canonical Click path, space-separated (`"projection synthesize"`). An existing value is replaced (nothing reads it, §1). |
| `warnings` | list; `[]` when absent. A non-list value is wrapped into a one-item list. |
| `next` | string or `null`; `null` when absent. |

Every other key passes through untouched, in its original order, after the three envelope
keys. Listings that cap their output (`--limit`, `--topK`, `--maxRows`, `--maxNames`,
`--maxConnections`) carry `rows_total` and `truncated` beside their list. Most already do
(H3, patterns 8/10, text2cypher). The plan lists the rest from a per-command audit, and each
gains them in its own commit.

**Text-only commands.** Each of the 23 human-text commands (and the four helper-printed ones) gets a JSON payload on stdout and
`--compact`. Where the text is the point (`init`, `setup`, `vault new`, `session initiate`,
`domains *-prompt`, `review prompt`, `update export`'s file list), the text goes to
**stderr** unchanged and stdout carries a summary (`{"command", "warnings", "next", …the
facts the text stated}`). A prompt-printing command puts the prompt in a `prompt` key. A
person at a terminal sees the same text as before plus one JSON line; a script gets JSON.

**Review:** text-to-stderr keeps every human-facing message. The alternative, a `--json`
flag on those commands, would leave two output modes to test forever.

### 3.3 C2: one spelling, camelCase

| Today | Becomes | Commands |
|---|---|---|
| `--dry-run` | `--dryRun` | domains harmonize, ingest detect-conflicts, detect-supersession, refine-graph, summarise, projection synthesize, query graph text2cypher, query text2sql, sameas propose, topic build, topic refresh |
| `--stage-only` | `--stageOnly` | ingest sync, ingest async |
| `--from-file` | `--fromFile` | ingest detect-conflicts, ingest refine-graph |
| `--repo-path` | `--repoPath` | ingest pull-kg |
| `--allow-cross-domain-merge` | `--allowCrossDomainMerge` | ingest refine-graph |
| `--include-skipped` | `--includeSkipped` | ingest retry-job |
| `--no-narrate` | `--noNarrate` | insights refresh |
| `--min-score` | `--minScore` | insights same-as |
| `--rebuild-projection` | `--rebuildProjection` | session initiate, snapshot restore |
| `--acp-cmd`, `--base-url` | `--acpCmd`, `--baseUrl` | chat-ui, admin-ui |

- The old spelling stays accepted. It is hidden from help, and its use writes one stderr
  line: `artmind: --dry-run is now --dryRun; the old spelling is removed after <version>`.
  The note goes to stderr, so JSON on stdout is unaffected and the plugin's parsing too.
- Removal: in the first release after every in-repo caller has moved (the contract test in
  §5 fails while any caller in the checkout uses an old spelling) and at least 30 days have
  passed. It is its own commit, announced in the release note.
- In-repo callers updated in the same change: skills (`--dry-run` 16, `--from-file` 6,
  `--repo-path` 2, `--include-skipped` 2, `--no-narrate` 1), `justfile` (15), the plugin
  (`projection synthesize --dry-run`, 6 sites in `src/`), the web UI (`--rebuild-projection`
  in snapshot restore, `--base-url`), `routes.yaml`, and the living docs (`CLAUDE.md`,
  `docs/INSTALL.md`, `docs/vault.md`, `docs/USER_GUIDE_TWO_LAPTOPS.md`, `docs/CAPABILITIES.md`).

### 3.4 C3: `--dryRun` and the `effect` block on every write

**The rule.** A command in `WRITE_COMMANDS` takes `--dryRun`. With it the command:

1. runs its own planning step: the same code that decides what to change, up to the first
   write;
2. writes nothing: no graph, staging, registry, curation record, vault file, cache, log
   other than the normal stderr, and **no LLM call**;
3. prints the normal envelope with `"dry_run": true`, no `rows` key (the H1 rule), its
   existing dry-run fields if it had any, and `effect`.

Without `--dryRun` the same command adds `effect` to its normal output too, describing what
it did. One shape then serves both "what will happen" and "what happened".

**The `effect` block:**

```json
"effect": {
  "summary": "Merge 3 entities into 'Acme Ltd' (ORGANISATION, banking); 47 observations move.",
  "changes": [
    {"kind": "entity_merge", "count": 3,
     "sample": [{"_id": "…", "name": "…", "entity_class": "ORGANISATION", "domain": "banking"}]}
  ],
  "stale": {"topics": ["acme-ltd"], "views": []},
  "llm": {"calls": 0, "model": null, "est_cost_usd": null},
  "reversible": true,
  "undo": "remove the group from same_as.yaml, then artmind projection rebuild --domain banking",
  "travels": true,
  "records": [".artmind/same_as.yaml"]
}
```

| Field | Rule |
|---|---|
| `summary` | One sentence, plain words, the counts that matter. The UI's card title. |
| `changes` | List of `{kind, count, sample}`; `sample` at most 10 items. `kind` from a fixed vocabulary in `cli_conventions`: `entity_create`, `entity_merge`, `entity_unmerge`, `entity_delete`, `entity_change`, `observation_move`, `document_ingest`, `document_reextract`, `document_retire`, `document_restore`, `document_supersede`, `conflict_resolve`, `conflict_dismiss`, `summary_write`, `description_write`, `view_save`, `view_delete`, `topic_write`, `topic_delete`, `table_change`, `trace_delete`, `fact_write`, `fact_retract`. A new kind is added there, with a test. |
| `stale.topics` | Topic pages whose `sources` or entity anchor the change touches (read from page frontmatter via `topics.store.list_pages`; the P1 `:TopicPage` index makes it one query later). |
| `stale.views` | Views whose params or Cypher name a class the change deletes or renames (schema changes only); `[]` otherwise. |
| `llm` | Calls the real run would make; `est_cost_usd` only when the model's price is configured (a new optional `ARTMIND_LLM_PRICES` file), else `null`. |
| `reversible`, `undo` | Whether, and the exact command or file edit that reverses it. |
| `travels`, `records` | Whether `vault sync` carries it to other machines, and the vault-relative files it writes. |

**Tier 1 (this spec):** `sameas approve`, `sameas reject`, `ingest resolve-conflict`,
`docs retire`, `docs restore`, `docs archive`, `ingest supersede`, `projection rebuild`,
`projection synthesize`, `update confirm`, `update retract`, `views save`, `views delete`,
`topic build`, `topic refresh`, `topic delete`, `ingest sync`, `ingest table2graph`,
`ingest retime`, `ingest summarise`, `vault sync`, `traces prune`. Those that have a dry run
keep its fields and gain `effect`.

**Tier 2 (listed in `WRITE_COMMANDS`, built later, the test only checks the flag exists):**
`db grain`, `db mappings set|confirm|clear`, `db bridge confirm|clear`, `db propose`,
`db refresh`, `db restore`, `db restore-text`, `docs reindex`, `docs restore-from-archive`,
`ingest write-to-graph`, `ingest pull-kg`, `ingest async`, `ingest retry-job`,
`ingest embed-entities`, `ingest embed-passages`, `snapshot restore`, `review new`,
`review attach`, `insights refresh`, `domains add|delete|harmonize`, `vault migrate-frontmatter`.

**Review: `ingest sync --dryRun`.** It reports the files it would ingest, by tier
(`content` / `metadata` / unchanged), and the LLM calls (chunks × steps + summaries) without
converting anything. A binary needing conversion is counted by page or slide estimate and
says so (`"estimated": true`), since its chunk count is unknown until docling runs.

### 3.5 C6: exit codes

| Code | Meaning | Source |
|---|---|---|
| 0 | Ran; an empty or low-confidence result is still 0 | — |
| 1 | Bad input the user can fix: unknown id, invalid YAML, a refused precondition | `ClickException` (today's behaviour) |
| 2 | Usage: unknown option, missing argument | Click (today's behaviour) |
| 3 | A store is unreachable or refuses the connection: Neo4j, the structured store, the embedding service, the configured LLM | new |

The root group gets a small `invoke` override (in `cli_conventions`, applied to `cli`). It
catches `neo4j.exceptions.ServiceUnavailable`, `AuthError` and `SessionExpired`, DuckDB
connection/IO errors, and the embedding/LLM clients' connection errors. It prints one line,
`artmind: <store> unreachable at <redacted address>: <reason>`, to stderr, and exits 3.
Everything else is unchanged: an unexpected exception keeps its traceback and exit 1.
`serve` returns the CliRunner exit code to `_entry`, which already exits with it, so proxied
calls get the same codes. An LLM failure inside a command that already handles it (the
extraction loss guard, classifier fallback) is not an exit 3; only an uncaught connection
failure is.

### 3.6 Payload changes the plugin will see

Envelope keys are added to every payload. `command` changes value where it existed.
`projection synthesize` and `ingest table2graph` gain `effect`. No key is removed or
renamed. The plugin reads specific keys, so it is unaffected, and its fixtures are
regenerated (`ARTMIND_REGEN_PLUGIN_FIXTURES=1`), then `just obsidian-plugin-test`. The
plugin's own calls move to `--dryRun` in the same change.

## 4. Testing

**Contract tests** (hermetic, new `test/test_cli_conventions.py`, walking the Click tree as
`test_cli_guide.py` does):

1. Every leaf command outside `SERVER_COMMANDS` and `INTERACTIVE_COMMANDS` has `--compact`.
2. No visible option name contains `-` after the leading `--`. Hidden aliases are listed in
   `cli_conventions.DEPRECATED_ALIASES`.
3. Every `WRITE_COMMANDS` entry exists in the tree and has `--dryRun`. Every tier-1 entry has
   a test module that covers its `effect`, found by a naming convention.
4. No file in `artmind/skills/`, `justfile`, `artmind/query_plan/routes.yaml`,
   `obsidian/artmind-obsidian/src/` or the living docs uses a deprecated spelling.
5. A command that is new since the audit and touches the graph, staging or vault files is in
   `WRITE_COMMANDS`. This is checked approximately: the test lists every command whose module
   imports a write helper (`_commit_document_tx`, `lifecycle`, `sameas` writers, `topics.store.write`)
   and fails on any not in the list or in an explicit `READ_ONLY` allow-list.

**Envelope:** `_echo_json` unit tests (keys added, existing keys untouched, order,
non-list `warnings` wrapped); one CliRunner test per former text-only command (JSON on
stdout, text on stderr).

**Dry runs:** for each tier-1 command, a test with the `test/_neo4j_recorder.py` session
asserts that `--dryRun` sends **no write Cypher** (no `MERGE`, `CREATE`, `SET`, `DELETE`,
`REMOVE`), writes no file under a temp vault, and makes no LLM call (mock asserts zero
calls). The test also checks that the `effect` counts equal the counts the real run reports.
Counts alone are never the assertion (CLAUDE.md §3): the recorded Cypher and parameters are.

**Exit codes:** a fake driver raising each store exception → exit 3 and the one-line
message, in process and through a `serve` test client.

**Aliases:** the old spelling still parses to the same value and prints the stderr note
once.

All test text synthetic (the checkout is public).

## 5. Rollout

One branch, commits in this order, each green on `just dev-test`:

1. `cli_conventions` module, `compact_option`, envelope in `_echo_json`; plugin fixtures
   regenerated.
2. Text-only commands to JSON + stderr (one commit per command group).
3. Aliased renames (§3.3) with every in-repo caller moved; the deprecated-spelling test on.
4. Exit codes.
5. `Effect` and tier-1 dry runs, one commit per command, starting with those that need no
   new planning code (`sameas approve`/`reject`, `ingest resolve-conflict`, `docs retire`/
   `restore`, `views save`/`delete`, `topic delete`, `traces prune`), then `projection
   rebuild`, `ingest supersede`, `update confirm`/`retract`, `ingest sync`, and finally the
   existing dry runs gaining `effect`.
6. Docs: `CLAUDE.md` conventions section (envelope, `--dryRun`, exit codes), group docstrings,
   skills, `docs/INSTALL.md`.

Timing: merged **between** collection rounds, never during one
(architecture §9). Afterwards: `just dev-install`; restart `serve`, `chat-ui`, `admin-ui`
from inside each vault (a daemon started earlier keeps the old spellings and envelope).

## 6. Risks

- **A dry run that drifts from the real run.** Both share the planning step, and the test
  compares `effect` counts across them.
- **Scripts parsing human text** (the 23 text-only commands). The text moves to stderr
  unchanged, so a script reading stderr loses nothing. A script reading stdout gets JSON,
  which the release note calls out.
- **The approximate write-command check flags false positives.** The `READ_ONLY` allow-list
  is explicit and reviewed.
- **Stale daemons answer the old way.** Covered by the rollout restart.

## 7. Open items

- `ARTMIND_LLM_PRICES`: a small YAML of `model: {input, output}` per 1M tokens, or always
  `null` until someone needs it. Recommend `null` in v1; the UI shows calls, not dollars.
- Whether `domains *-prompt` (prompt-printing for debugging) should keep raw text on stdout
  for piping into files. Recommend JSON with a `prompt` key, plus `--raw` only if asked for.
