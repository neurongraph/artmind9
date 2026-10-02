# One-command vault provisioning: `bootstrap.sh` + `artmind vault new`

Date: 2026-10-02
Status: approved design, not yet implemented

## Problem

Creating an artmind vault today takes ~15 manual steps across four tools
(brew, gh, neo4j-manager, artmind, Obsidian), with values copied by hand
between them (bolt port, password, remote URL). Checking those steps against
the code showed several are redundant or broken in the documented order:

| Manual step | Reality |
|---|---|
| `brew install uv just gh` | Also needs `node` (`just dev-install` → `obsidian-plugin-build` → `npm ci`), `colima` + `docker` (neo4j-manager), and `gh auth login` |
| `neo4j-manager tui`, copy port + password | `neo4j-manager create <name> --json --wait` returns both and waits until bolt accepts connections |
| `artmind init --interactive` | Prompts for a full Neo4j URI, not a port; also prompts for a remote, making the later `git remote add` redundant |
| `just obsidian-plugin-install <vault>` before opening Obsidian | Fails: the recipe requires `.obsidian/`. `artmind init` also installs + enables the plugin, but only when `.obsidian/` exists |
| Install Git / Unhide / VSCode Editor by hand | All are in Obsidian's official registry; their release assets can be downloaded and enabled by a script |
| Set the LLM key | Missing from the list entirely; `~/.artmind/config.env` is seeded with defaults and extraction fails without a working provider |

## Goal

Two commands take a fresh Mac to a fully ready Obsidian vault:

```bash
bash scripts/bootstrap.sh          # once per machine
artmind vault new my_work          # once per vault (or: --join on a second machine)
```

The one step left manual is Obsidian's own: "Open folder as vault" and
"Turn on community plugins". Obsidian stores that consent itself; artmind does
not flip it (`install_obsidian_plugin` docstring, `artmind/setup.py`).

## Non-goals

- Linux/Windows support for `bootstrap.sh` (macOS + Homebrew only).
- Registering the vault in Obsidian's `obsidian.json` or bypassing the
  community-plugins consent prompt.
- Validating that the configured LLM is reachable (only that its required
  credential is set).
- AuraDB provisioning. AuraDB is supported only as "bring your own
  connection" via flags.

## 1. `scripts/bootstrap.sh` — per machine

Bash, idempotent: every step checks before acting and skips when done.

1. Require macOS and Homebrew; `brew install` whichever of
   `uv just gh node colima docker` are missing.
2. `gh auth status || gh auth login`, then `gh auth setup-git` (so `git push`
   over https uses gh's credentials). Install Obsidian (`brew install --cask
   obsidian`) if `/Applications/Obsidian.app` is missing. Warn if
   `git config --global user.name`/`user.email` are unset.
3. `colima status || colima start`.
4. Clone `neurongraph/artmind9` and `neurongraph/neo4j-manager` into
   `${ARTMIND_SRC:-$HOME/projects}` unless already present (an existing
   `neo4j_manager` directory counts as present).
5. `just dev-install` in artmind9; `just install` in neo4j-manager.
6. Seed `~/.artmind/config.env` if absent, by calling
   `artmind.setup.ensure_machine_config()` with the installed tool's Python
   (`"$(uv tool dir)/artmind9/bin/python" -c ...`). Pure filesystem; no Neo4j.
7. Print the two next steps:
   ```
   1. Edit ~/.artmind/config.env   # LLM provider, model, API key, embeddings -- shared by every vault
   2. artmind vault new <name>     # new vault   (or: artmind vault new <name> --join  on a second machine)
   ```

How the user gets the script before the repo is cloned is a README
one-liner (`gh repo clone` after `brew install gh && gh auth login`, or
`gh api` raw fetch); the script tolerates being run from inside or outside a
checkout.

## 2. `artmind vault new NAME` — per vault

### CLI surface

```
artmind vault new NAME [--join] [--dir PATH] [--githubOwner OWNER] [--localOnly]
                       [--neo4jUri URI --neo4jUser USER --neo4jPassword PW [--neo4jDatabase DB]]
                       [--noPlugins] [--noOpen] [--yes]
```

- `NAME` is the vault name, the folder name, the GitHub repo name and the
  neo4j-manager instance name. Must match `[A-Za-z0-9._-]+`; otherwise rejected
  (never sanitized, so the four names always agree).
- `--dir` defaults to `~/artmind_vaults/NAME`.
- Remote is **derived, not prompted**: `<owner>/<NAME>`, where owner is
  `--githubOwner` or the active gh account (`gh api user --jq .login`).
  New vaults create a **private** repo.
- `--join` joins an existing vault (second machine): the repo must exist.
- `--localOnly` skips GitHub entirely (throwaway/test vaults). Not combinable
  with `--join`.
- `--neo4jUri/...` use a given connection (e.g. AuraDB) instead of creating a
  neo4j-manager instance. All of `--neo4jUri`, `--neo4jUser`,
  `--neo4jPassword` are required together.
- `--yes` skips the confirmation prompt. No other prompts exist.

### Steps

| # | Step | New vault | `--join` |
|---|---|---|---|
| 1 | Pre-flight | all checks below; one confirmation summary | same |
| 2 | Machine config | nothing to do: pre-flight required `~/.artmind/config.env` to exist (seeded by `bootstrap.sh`) and checked its credential | same |
| 3 | Neo4j | `neo4j-manager create NAME --json --wait` (skipped with `--neo4jUri`) | same (this machine's own instance) |
| 4 | Folder | `mkdir`, `git init`, `mkdir .obsidian` | `gh repo clone OWNER/NAME DIR`, `mkdir -p .obsidian` |
| 5 | Scaffold | `scaffold_vault(root, config_answers={neo4j_uri: bolt_url, neo4j_username: user, neo4j_password: password, neo4j_database: "neo4j"})` — installs + enables the artmind plugin because `.obsidian/` exists | same |
| 6 | Community plugins | install, enable, seed settings (§3); skipped with `--noPlugins` | same |
| 7 | Graph schema | `setup_all()` anchored at the vault | same |
| 8 | Publish | each sub-step idempotent: commit if HEAD is missing or the tree is dirty (`git add -A && git commit -m "artmind: initialise vault NAME"`); `gh repo create OWNER/NAME --private` unless it exists; `git remote add origin https://github.com/OWNER/NAME.git` unless `origin` exists; `git push -u origin HEAD` (commit only with `--localOnly`) | skipped |
| 9 | Bookmark | `vault sync --bootstrapSynced` (empty graph, one commit: already in sync) | `vault sync --bootstrapEmpty` (rebuild this machine's graph from the commits) |
| 10 | Hand-off | `open -a Obsidian` (unless `--noOpen`); print "Open folder as vault → DIR → Turn on community plugins" | same |

Steps 7 and 9 run as **child processes** (`python -m artmind setup`,
`python -m artmind vault sync ...`) with `cwd=DIR` and `ARTMIND_VAULT=DIR`, not
in-process: `paths.py` resolves the active vault and loads its `config.env`
once at import, so an in-process call would use the vault (or run folder) the
command was started from. The child's environment drops every key loaded from
`paths.LOADED_ENV_FILES` plus `ARTMIND_HOME`, `ARTMIND_DATA_DIR`,
`ARTMIND_VAULT_DIR` — otherwise an inherited `ARTMIND_KG_NEO4J_URI` beats the
new vault's own config (`load_dotenv(override=False)`).

The repo is created last (step 8) so a failure in any earlier step never
leaves an orphan repo on GitHub.

### Pre-flight checks

All checks run, and every failure is reported together in one error.

| Check | New vault | `--join` |
|---|---|---|
| `NAME` valid | `[A-Za-z0-9._-]+` | same |
| machine config | `~/.artmind/config.env` exists (else: run `bootstrap.sh`) and the configured LLM provider's required credential is non-empty | same |
| git identity | `git config user.name` and `user.email` set (step 8 commits) | not needed |
| tools on PATH | `git`; `gh` (authenticated) unless `--localOnly`; `neo4j-manager` unless `--neo4jUri` | `git`, `gh`, `neo4j-manager` unless `--neo4jUri` |
| GitHub `OWNER/NAME` (`gh repo view`) | must **not** exist → else suggest `--join` or another name | must exist |
| folder `DIR` | absent or empty | absent or empty |
| neo4j instance `NAME` (`neo4j-manager status NAME --json`) | must not exist → else suggest `neo4j-manager remove NAME` or another name (it may hold another vault's data) | same |

Then one confirmation (skipped by `--yes`):

```
Create vault my_work:
  folder   ~/artmind_vaults/my_work
  neo4j    my_work  (new local instance, via neo4j-manager)
  github   neurongraph/my_work  (new, private)
Proceed? [Y/n]
```

### Resume

`vault new` writes `~/.artmind/vault_new/NAME.json` (machine-level, outside
the vault, so nothing to gitignore and it exists before the folder does),
recording the resolved plan (owner, mode, instance name,
neo4j source) and each completed step. On re-run with the same `NAME`:

- a folder, neo4j instance or repo recorded there as created by this run
  is **resumable**, not a clash;
- completed steps are skipped; the run continues from the first incomplete
  step, re-reading neo4j credentials via `neo4j-manager status NAME --json`;
- a re-run with a different mode/owner than recorded is an error.

The state file is deleted on success. `vault new` never deletes a folder,
neo4j instance or repo itself. Each step failure prints the step name, the
underlying error, and "fix the cause and re-run `artmind vault new NAME ...`".

The state file is written before each step starts (intent) and updated when
it completes, so a crash mid-`neo4j-manager create` still leaves the instance
attributable to this run on re-run.

## 3. Community plugins

Package data file `artmind/obsidian/community_plugins.yaml` — taken from the
`my_work` vault, whose plugins are all in the official registry:

| id | registry repo | seeded `data.json` |
|---|---|---|
| `obsidian-git` | vinzent03/obsidian-git | none (plugin defaults) |
| `unhide` | polyipseity/obsidian-unhide | `my_work`'s: `showHiddenFiles`, `showConfigurationFolder`, `showingRules` (`+/`, hide `.git`, hide `.venv`), notice settings |
| `vscode-editor` | sunxvming/obsidian-vscode-editor | `my_work`'s: the 15-extension list incl. `json`, `yaml`, `env`, plus editor options |
| `ghostty-terminal` | lavs9/obsidian-ghostty-terminal | none |

The YAML holds only `id` and optional `data`; the repo is resolved at run
time from `obsidianmd/obsidian-releases/community-plugins.json`. For each
plugin, `artmind/obsidian_community.py`:

1. resolves `id → repo`;
2. downloads the latest release's `main.js`, `manifest.json`, and
   `styles.css` if the release has it, into `.obsidian/plugins/<id>/`;
3. writes `data.json` only if absent;
4. appends `id` to `.obsidian/community-plugins.json` (same read/append
   logic as `install_obsidian_plugin`; factor that into a shared helper).

Network failure is a warning per plugin, not a fatal error; the summary lists
what was skipped. The artmind plugin's own `data.json` is never seeded (it
holds machine state such as `adminUiPid`).

## 4. Deletions and redundancy

After `vault new`, each command has one job:

| Command | Job | Change |
|---|---|---|
| `artmind vault new` | create or join a vault | new |
| `artmind init` | converge an existing vault: upgrade `.gitignore` block, plugin, skills, `vault_id` | keep; rewrite its "Next:" footer to point new vaults at `vault new` |
| `artmind init --interactive` | — | **delete** with its prompt code (`cli.py`, the `config_is_fresh` prompt block) |
| `artmind init --remote` | — | **delete** |
| `scaffold_vault(git_remote=)` + `"git_remote"` result key | — | **delete** (`gh repo create --remote origin` sets the remote) |
| `vault_git.add_remote` | — | **delete** (no callers left) |
| `scaffold_vault(config_answers=)` | — | keep (`vault new` uses it) |
| `artmind setup` | (re)apply graph constraints/indexes after upgrades or a Neo4j switch | keep |
| `vault sync --bootstrapEmpty/--bootstrapSynced` | first bookmark / recovery | keep |
| `just obsidian-plugin-install` | plugin dev loop | keep; remove from setup docs |

Tests removed with them: the `--interactive`/`--remote` cases in
`test/test_vault_cli.py`, `test/test_vault_git.py::test_add_remote_*`,
`test/test_vault_scaffold.py::test_scaffold_*git_remote*`.

## 5. Docs

- `docs/vault.md`: replace "`--interactive` and `--remote`" with a
  "`vault new`" section; fix the `init` section.
- `docs/USER_GUIDE_TWO_LAPTOPS.md` §2: laptop A = `vault new NAME`,
  laptop B = `vault new NAME --join`.
- `docs/INSTALL.md`, `README.md`: `bootstrap.sh` → edit
  `~/.artmind/config.env` → `vault new`.
- `artmind/skills/artmind-ingestion-helper/SKILL.md`: drop `--interactive`.
- `vault` group docstring and `COMMAND_GROUPS` in `cli.py`
  (`test/test_cli_guide.py` enforces routing).

## 6. Code layout

- `artmind/vault_new.py` — the plan (resolved names, mode), pre-flight,
  state file, and one function per step. Every external command goes through
  one `run(cmd) -> CompletedProcess` seam; HTTP goes through one `fetch(url)`
  seam.
- `artmind/obsidian_community.py` — registry lookup, download, enable, seed.
- `artmind/obsidian/community_plugins.yaml` — package data (check
  `pyproject.toml` package-data globs include it).
- `artmind/cli.py` — thin `vault new` command.
- `scripts/bootstrap.sh`.

## 7. Testing

Hermetic, in `test/`, via `CliRunner` with the `run`/`fetch` seams faked.
Per CLAUDE.md §3, assert on the **commands and arguments actually sent** and
on files written, never on a success summary alone:

- new vault: `neo4j-manager create NAME --json --wait` sent; the vault's
  `config.env` holds the returned `bolt_url`/`user`/`password`;
  `gh repo create OWNER/NAME --private --source . --remote origin --push` sent
  after the commit; `bootstrap_synced=True` passed to sync.
- `--join`: `gh repo clone` sent, no `gh repo create`, `bootstrap_empty=True`.
- each pre-flight clash (repo, folder, instance, bad name) and that several
  are reported together; `--join` with a missing repo.
- resume: fail at each step, re-run, assert completed steps are not re-sent
  and recorded resources are not reported as clashes; mode mismatch errors.
- `--localOnly`, `--neo4jUri` (no `neo4j-manager` call), `--noPlugins`.
- machine config: missing provider credential fails pre-flight.
- community plugins: registry resolution, the three files written, `data.json`
  not overwritten, `community-plugins.json` appended once, network failure
  is a warning.
- deletions: `init --interactive`/`--remote` are no longer options.

End-to-end (manual): `bootstrap.sh` on this machine, then
`artmind vault new demo --localOnly`, then a real `vault new NAME`; then
`vault new NAME --join --dir /tmp/NAME-b --neo4jUri ...` against a second
neo4j-manager instance created by hand (the default instance name `NAME` is
already taken on the same machine — a real laptop B has no such clash).
