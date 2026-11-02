# CLI Uniformity (P1a) Implementation Plan

**Goal:** One JSON envelope from every non-server command, one camelCase spelling per option (old spellings kept as hidden aliases), `--dryRun` plus a common `effect` block on every tier-1 write, distinct exit codes, all enforced by hermetic contract tests over the Click tree.

**Spec:** `docs/superpowers/specs/2026-11-02-cli-uniformity-design.md` (read it first). Context: `2026-11-02-retrieval-curation-architecture-design.md` §6.2, §9.

**Branch / worktree:** `feat/cli-uniformity` in `.claude/worktrees/cli-uniformity`. Never merge to master; no PR unless asked.

## Ground rules

- Tests live in `test/` (singular), all text synthetic (public checkout).
- Test first in every task: write the failing test, see it fail, implement, see it pass.
- After each task: `env -u ANTHROPIC_BASE_URL uv run --group dev pytest test/ -q -m "not slow_docling"` (= `just dev-test`, ~3.5 min) is green, then one conventional commit ending with `Co-Authored-By: Claude Code <noreply@anthropic.com>`.
- Any CLI output change fails `test/test_plugin_fixtures.py` first: regenerate with `ARTMIND_REGEN_PLUGIN_FIXTURES=1`, then `just obsidian-plugin-test` (done in the task that changes output).
- Dry runs: assert on the Cypher and parameters sent (`test/_neo4j_recorder.py`): no `MERGE|CREATE|SET|DELETE|REMOVE`, no file written, zero LLM calls. Never counts alone.
- Never remove or rename a payload key. Old option spellings keep working as hidden aliases that print one `artmind:` stderr line.
- Never run `artmind setup`/`init` here. Live probes use `ARTMIND_NO_PROXY=1`; unset `ANTHROPIC_BASE_URL` for LLM-calling commands.
- Every new/changed command stays routed in `COMMAND_GROUPS` (`test_cli_guide.py`); `routes.yaml` templates are validated against the Click tree.

## Contract test scaffolding

`test/test_cli_conventions.py` is created in Task 1 with the four tree-walking checks of spec §4 (1: `--compact`, 2: no kebab-case visible option, 3: `WRITE_COMMANDS` have `--dryRun` and tier-1 have a covering test module, 4: no deprecated spelling in callers) plus the write-helper approximation (5). Each check starts with a module-level `_PENDING_*` set listing the commands that do not comply yet; every later task deletes the entries it fixes. The final task asserts all `_PENDING_*` sets are empty (they are removed).

## Step 1 — module, envelope, `--compact`

### Task 1: `artmind/cli_conventions.py` + contract scaffolding
- Test first: `test/test_cli_conventions_unit.py` (`Effect.to_json`, `envelope` key order/replace/wrap rules, `EFFECT_KINDS` vocabulary, sample cap 10, `EXIT_*` values, `aliased_option` accepts old name and prints one stderr note while the old name is hidden from help, `dry_run_option`), and `test/test_cli_conventions.py` with `_PENDING_*` sets.
- Implement the module: `compact_option`, `dry_run_option`, `aliased_option`, `envelope`, `Effect`, `EFFECT_KINDS`, `SERVER_COMMANDS`, `INTERACTIVE_COMMANDS`, `WRITE_COMMANDS` (tier 1 and 2), `DEPRECATED_ALIASES`, `EXIT_*`.

### Task 2: envelope in `_echo_json`, `@compact_option` everywhere it exists
- Test first: `_echo_json` through a throwaway command: envelope keys first, existing keys untouched and in order, `command` replaced by the Click path, non-list `warnings` wrapped, domain-guard warnings still merged, trace `observe_output` still sees the payload.
- Replace the 131 inline `--compact` decorators by `@compact_option` (mechanical), `_echo_json` calls `envelope`. Regenerate plugin fixtures; `just obsidian-plugin-test`.

### Task 3: `rows_total` / `truncated` on capped listings
- Audit commands with `--limit`, `--topK`, `--maxRows`, `--maxNames`, `--maxConnections`; list the ones lacking the pair in this file's "Audit results" section; test first per command (payload has both keys, `truncated` true when the cap cut rows); one commit per command group.

## Step 2 — text-only commands to JSON on stdout, text on stderr (+ `--compact`)

One commit per group; test first = a CliRunner test per command (stdout parses as one JSON envelope, previous text is on stderr unchanged, `--compact` accepted).

- Task 4: `domains` (`list`, `add`, `delete`, `entities-prompt` / `properties-prompt` / `relationships-prompt` with a `prompt` key, `validate`, `harmonize`).
- Task 5: `ingest refine-graph`, `ingest write-to-graph`, `ingest pull-kg`.
- Task 6: `ingest sync`, `ingest extract-kg` (helper-printed output).
- Task 7: `init`, `setup`, `vault new`.
- Task 8: `review attach|prompt|regressions|status`.
- Task 9: `session close|initiate`, `topic delete`, `traces feedback`.
- Task 10: `update draft|confirm|history|export`.
- `db connect` is interactive (exempt); servers exempt.

## Step 3 — aliased renames (spec §3.3)

- Task 11: `--dry-run` to `--dryRun` on the 11 commands (alias via `dry_run_option`); test: both spellings parse to the same value, old prints one stderr note, hidden from `--help`.
- Task 12: other nine renames (`--stage-only`, `--from-file`, `--repo-path`, `--allow-cross-domain-merge`, `--include-skipped`, `--no-narrate`, `--min-score`, `--rebuild-projection`, `--acp-cmd`/`--base-url`).
- Task 13: move every in-repo caller (skills, `justfile`, `routes.yaml`, plugin `src/` to `--dryRun`, `artmind/webui`, living docs listed in spec §3.3); contract check 4 turned on; plugin tests and fixtures.

## Step 4 — exit codes

- Task 14: test first with a fake driver raising `ServiceUnavailable`, `AuthError`, `SessionExpired`, DuckDB IO error, embedding/LLM connection errors: exit 3 and one `artmind: <store> unreachable at <redacted address>: <reason>` stderr line; an unrelated exception keeps traceback and exit 1; same through the `serve` test client. Root-group `invoke` override.

## Step 5 — `Effect` and tier-1 dry runs (one commit per command)

Each task: test first with the recorder (no write Cypher, no file, no LLM call; `effect` counts equal the real run's), implement the shared planning step, add `--dryRun` and `effect` (real run also carries `effect`). Then flip the command out of `_PENDING_WRITE`.

- Task 15: `sameas approve`, `sameas reject`.
- Task 16: `ingest resolve-conflict`.
- Task 17: `docs retire`, `docs restore`, `docs archive`.
- Task 18: `views save`, `views delete`.
- Task 19: `topic delete`, `traces prune`.
- Task 20: `projection rebuild`.
- Task 21: `ingest supersede`.
- Task 22: `update confirm`, `update retract`.
- Task 23: `ingest sync` (files by tier, LLM call estimate, `estimated: true` for binaries).
- Task 24: existing dry runs gain `effect`: `projection synthesize`, `topic build`, `topic refresh`, `ingest table2graph`, `ingest retime`, `ingest summarise`, `vault sync`.
- Task 25: tier-2 `WRITE_COMMANDS` get a `--dryRun` flag only where spec says the test checks the flag exists (flag listed; behaviour later). Contract checks 3 and 5 turned on.

## Step 6 — docs

- Task 26: `CLAUDE.md` conventions (envelope, `--dryRun`, exit codes, aliases), group docstrings, skills, `docs/INSTALL.md`; remove the `_PENDING_*` scaffolding; final full run, plugin tests, push.

## Audit results

(filled in by Task 3)
