# Onboarding review

A step-by-step account of what a new user currently goes through to get from
"clone the repo" to "ask the chat UI a question", each step checked against
the code as it stands on `master` (`99f37ed`, post `feat/vault` merge). Findings
are cited to source; nothing here is guessed.

Severity/priority reflects impact on a first-time user, not implementation
effort.

## Summary table

| # | Step | Issue | Priority | Status |
|---|---|---|---|---|
| 1 | Install via `gh repo clone` / `git clone` | Not packaged for PyPI (`pip install`/`uv install`) | Low | Confirmed, still true |
| 2 | Install prerequisites (just, Ollama + models, Neo4j) | Thought undocumented | Low | **Already fixed** — README now has a full Prerequisites section |
| 3 | `just dev-install` should seed `~/.artmind` | Machine-wide LLM config never gets seeded under the name the vault model actually reads | **High** | **Fixed** — `artmind init` now migrates/reports it |
| 4 | `artmind init` + manual vault setup steps | No interactive script, no schema picker, no generated README, `.artmind_ignore` proposal conflicts with the vault's actual ignore design | Medium | Partially already better than described; partially an intentional design rejection |
| 5 | Trigger ingestion of a new/edited file | Unclear which command to run; relationship to `git commit` unclear | Medium | Confirmed gap, but there is a clear answer today |
| 6 | `artmind setup` (first Neo4j connection) | Raw driver errors, no AuraDB guidance, no APOC check | Medium | Confirmed, two separate gaps |
| 7 | `artmind chat-ui` / `artmind admin-ui` first launch | Static analysis suggested agent `cwd` couldn't find vault skills | ~~High~~ None | **Retracted after live testing** — chat-ui launched from a real vault works correctly with `artmind-query`; the doc-code cwd mismatch doesn't manifest |

---

## 1. Installation via `git clone`

```bash
cd $HOME && gh repo clone neurongraph/artmind9
```

**Issue:** no PyPI package; every user builds from source.

**Priority:** Low.

**Solution:** package and publish so `pip install artmind` / `uv install artmind`
works. [README.md:77-81](../README.md) still documents `git clone` + `uv sync`
as the only path.

---

## 2. Installing prerequisites

Originally flagged as undocumented (`just`, Ollama + pulled models, an
OpenRouter key). **This is already fixed** in the current README:
[README.md:36-71](../README.md) has a full Prerequisites table (Python
version, `uv`, Ollama, Neo4j + APOC, `just`), the exact `ollama pull` commands
for the default models, and a one-line Docker command for Neo4j with the APOC
plugin enabled. If this was tested against an older checkout, it's worth
re-checking against current `master` — nothing further to do here unless the
live experience still disagrees with the doc.

---

## 3. `just dev-install` doesn't seed usable machine config

```bash
just dev-install
```

**Issue, confirmed and worse than originally suspected:** it's not merely
"missing a config.env" — it's a **name mismatch** between what gets seeded and
what the vault model reads.

- `just dev-install` runs `scaffold_run_folder()`
  ([setup.py:76](../artmind/setup.py)), which seeds `~/.artmind/.env` (the
  *legacy* run-folder path) from `env.example` — but only when `ARTMIND_HOME`
  is **not** vault-resident, i.e. only the very first time, before any vault
  exists.
- The vault model reads machine-wide identity (LLM provider, API keys,
  embedding model) from **`~/.artmind/config.env`** — a different file
  ([paths.py:72-79](../paths.py), [docs/vault.md:370-382](vault.md)).
- Nothing ever creates `~/.artmind/config.env`. `artmind init` only writes the
  **per-vault** `config.env` (Neo4j settings only —
  [setup.py:226-248](../artmind/setup.py)).

**Result:** the first vault anyone creates has Neo4j placeholders and
**nothing else** — no LLM provider, no API key, no model — and the agent
simply fails, with nothing pointing at the cause.

This exact gap was independently written up in `docs/vault.md`'s "Known gaps"
section, including the manual fix:

```bash
grep -vE '^(ARTMIND_KG_NEO4J_|ARTMIND_DATA_DIR|ARTMIND_VAULT_DIR|ARTMIND_ARCHIVE_DIR)' ~/.artmind/.env > ~/.artmind/config.env && chmod 600 ~/.artmind/config.env
```

**Priority:** High — this blocked every first-time vault, silently.

**Status: Fixed, at the root — `just dev-install` itself now creates
`~/.artmind/config.env`.** `setup.ensure_machine_config()` runs from
`scaffold_run_folder()` (`just dev-install` / `artmind setup`) *and* from
`scaffold_vault()` (`artmind init`), whichever runs first, in three cases:

1. `~/.artmind/config.env` already exists → left alone.
2. it's missing but an older install's legacy `~/.artmind/.env` holds real
   settings → migrated (stripping the vault-scoped keys, same filter as the
   manual fix above), chmod 600 — so a returning user's actual settings win
   over a bare template.
3. neither exists (the genuinely-fresh-machine case this request was about)
   → **seeded straight from the repo's `artmind/env.example`**, filtered the
   same way, chmod 600 — so a bare `just dev-install` alone leaves a working,
   if default-filled, `~/.artmind/config.env`, with no `artmind init` step
   required first.

`init` prints whichever happened (`Machine:  migrated ...` /
`Machine:  seeded ...` / a warning if truly nothing was available to seed
from).

See [setup.py:`ensure_machine_config`](../artmind/setup.py) and
[cli.py's `init` command](../artmind/cli.py) for the implementation, and
[docs/vault.md](vault.md)'s "Machine-level config" section for the updated
writeup. Also fixed as part of this: `test/conftest.py` now redirects `HOME`/
`USERPROFILE` for the whole suite, since `MACHINE_CONFIG_DIR`/`MACHINE_CONFIG_ENV`
are keyed on `Path.home()` directly and the existing `ARTMIND_HOME` redirect
didn't reach them.

---

## 4. Vault initialization is manual and multi-step

```
artmind init
# review ~/.artmind/.env
# edit .artmind/config.env for neo4j config
artmind setup
# edit .artmind/vault.yaml to map folders to domains
```

**Partially already better than described:** `artmind init` (`scaffold_vault`,
[setup.py:251+](../artmind/setup.py)) is already one command, not seven — it
creates `.artmind/`, seeds starter schemas, symlinks skills, writes both
`config.env` and a commented starter `vault.yaml` in a single run, and prints
next steps inline ([cli.py:3253-3264](../artmind/cli.py)):

```
Vault:    <root>
Schemas:  general, personal_journal
Skills:   5 linked
Manifest: <root>/.artmind/vault.yaml

Next:
  $EDITOR <root>/.artmind/config.env   # Neo4j connection
  artmind setup                        # graph constraints + indexes
  $EDITOR <root>/.artmind/vault.yaml   # map folders to domains
```

**Still missing**, matching the original request:

- No interactive schema picker — `init` always seeds `general` +
  `personal_journal` ([`STARTER_SCHEMAS`](../artmind/setup.py)), full stop.
- No generated `README.md` inside the new vault explaining the special
  folders, `.gitignore` behavior, or command reference.

**On the proposed `.artmind_ignore` file** — this one is a design tension, not
an oversight. `docs/vault.md` explicitly rejects a separate ignore mechanism:

> "An unmapped path is simply not mapped ... no separate ignore mechanism, one
> concept instead of two." — [vault.md:255-256](vault.md)

Only `_Inbox` is hard-coded as never walked
([ingest.py:85](../artmind/ingest.py), `NEVER_WALKED`). `_external_docs/` is
excluded from ingestion **only by convention** — it's simply unmapped in the
starter `vault.yaml`. A broad mapping (`path: "**"`) would walk into
`_external_docs/` and re-ingest already-ingested external documents as if they
were new vault-native files, silently duplicating them. That's worth
documenting explicitly (in the generated vault README, if one gets built)
rather than solved with a second ignore file that contradicts the "one
concept" design.

**Priority:** Medium.

**Solution:** keep the one-concept ignore design; add the missing pieces —
`--schemas` flag or interactive picker on `init`, and a generated
`<vault>/README.md` (or `.artmind/README.md`) covering special folders,
git/gitignore behavior, and the command reference.

---

## 5. Which command triggers ingestion of a new/edited file?

**Confirmed gap, but there is a clear answer today.** The command is:

```bash
artmind ingest sync .
```

run from the vault root. It walks every path mapped in `vault.yaml`
(unmapped paths, including `_Inbox/`, are never touched — see §4) and is
**safe to re-run**: a file whose content hasn't changed hits a cheap
`no_op`/`metadata_only` path rather than re-running LLM extraction
([ingest.py:975](../artmind/ingest.py)), so looping it after every edit isn't
wasteful.

Two things are still genuinely missing:

- **No preview.** `ingest sync` has no `--dry-run` / status command that lists
  what *would* be ingested before committing to a (possibly costly) run — I
  checked every option on `ingest_sync`
  ([cli.py:580-612](../artmind/cli.py)) and there is no such flag.
- **Git commit has no relationship to ingestion at all, today.** `vault.yaml`
  accepts `trigger: commit`, but it's accepted-and-ignored —
  [docs/vault.md:267-271](vault.md) states plainly "only `manual` does
  anything today." `ingest sync` walks live working-tree files; it does not
  look at git history, staged changes, or commits in any way. So: committing
  your vault's notes is your own repo hygiene (or the Obsidian Git plugin's
  job), entirely independent of when ingestion happens, until the `commit`
  trigger is implemented.

**Priority:** Medium.

**Solution:** document "the command is `artmind ingest sync .`, safe to
re-run, unrelated to git commits for now" somewhere a first-time user actually
sees it (the generated vault README from §4 is the natural place); add a
`--dry-run`/preview mode to `ingest sync`.

---

## 6. `artmind setup` — first Neo4j connection

**Confirmed, two separate gaps** ([setup.py:654-739](../artmind/setup.py),
[graph_query.py:199-212](../artmind/graph_query.py)):

1. **Raw driver errors on connection failure.** `neo4j_session()` does no
   exception handling at all; a bad URI, wrong password, or unreachable host
   propagates as the native `neo4j.exceptions` message, wrapped only in
   `click.ClickException(str(e))` by the CLI. The starter `config.env` ships
   with `ARTMIND_KG_NEO4J_PASSWORD=` **blank**
   ([setup.py:236](../artmind/setup.py)) — so a first-timer who runs `artmind
   setup` before editing the file gets a raw `Neo.ClientError.Security.
   Unauthorized`, not "edit `.artmind/config.env` first."
2. **No AuraDB guidance, no APOC check.** The starter template only shows a
   local-Docker-shaped URI (`neo4j://127.0.0.1:7687`) with no commented
   example for a hosted `neo4j+s://<id>.databases.neo4j.io` connection.
   Separately, `setup_all()` never checks for the APOC plugin — `setup`
   succeeds cleanly against an APOC-less Neo4j, and the failure only surfaces
   much later, deep into a real `ingest refine-graph` run, as a raw `Unknown
   function 'apoc.create.addLabels'` — far from the step that actually needed
   it.

**Priority:** Medium.

**Solution:** give `neo4j_session()`/`setup` friendlier error mapping for the
common first-run cases (auth failure, unreachable host); add an AuraDB example
to the starter `config.env` comments; have `setup` probe for APOC and report
its absence immediately rather than letting it surface downstream.

---

## 7. Launching `chat-ui` / `admin-ui` for the first time

**Originally flagged High from static analysis; live-tested and retracted.**
The static-analysis concern was real as far as it went:

- [webui/agent.py:27-29](../artmind/webui/agent.py) sets `RUN_FOLDER =
  ARTMIND_HOME` and builds `ClaudeAgentOptions(cwd=str(RUN_FOLDER),
  skills=[...])`.
- Inside a vault, `ARTMIND_HOME` resolves to **`<vault>/.artmind`**
  ([paths.py:52-57](../paths.py)) — not the vault root.
- Skills are symlinked at **`<vault>/.claude/skills`**, the vault **root**
  ([vault.py:133-136](../artmind/vault.py), `VaultLayout.skills_dir`).

From reading agent.py's own docstring alone ("resolved from `.claude/skills/`
relative to the agent's cwd"), this looked like `cwd = <vault>/.artmind`
would miss `<vault>/.claude/skills/` entirely — a plausible, high-severity
bug.

**It doesn't reproduce.** Ran `artmind chat-ui` from inside a real vault
(`~/Projects/a123`) against the re-ingested graph: it launched cleanly and
answered using the `artmind-query` skill correctly. So either the Claude
Agent SDK's skill discovery walks up parent directories looking for
`.claude/skills/` (the way Claude Code's own settings/`CLAUDE.md` discovery
does), rather than requiring an exact match at `cwd` — which would fully
reconcile the code as written with what was actually observed — or something
else compensates that wasn't found by reading the source alone. Either way:
**no code change needed here**, and the docstring's claim that cwd is exactly
the vault root, while apparently not literally true, isn't causing the
failure it predicted.

**Priority:** downgraded from High to none — working as tested.

**Lesson for this review:** static analysis flagged this correctly as *worth
checking* but was wrong about the actual runtime outcome. Every other finding
in this document that hasn't been explicitly marked "live-tested" carries the
same caveat and should be verified the same way before being treated as
confirmed.

**Separate, smaller doc gap, still open:** the README's Prerequisites table
never mentions that `chat-ui`/`admin-ui` need a working `claude` CLI login or
`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` — that requirement currently only
exists as a comment in
[webui/backends/__init__.py:34-42](../artmind/webui/backends/__init__.py). A
user who's only set up Ollama + OpenRouter per the README (for ingestion) has
no signal that the chat UI needs Anthropic credentials too.

---

## Open — steps not yet reviewed

Continuing this review would cover: querying via the `artmind-query`/
`artmind-update` skills once a vault has data, curation (`artmind-curate`,
the same-as review queue), and session snapshots (`session close`/`session
initiate`) for ephemeral Neo4j setups.
