# Review — vault git transport, phase 1

- **Range:** `master..vault-git-transport-phase1` (10 commits, d4c26f7..b2dc84d)
- **Spec axis:** `docs/superpowers/specs/2026-09-26-vault-git-transport-design.md` §5 R1, §6 A1, §6 A5;
  plan `docs/superpowers/plans/2026-09-26-vault-git-transport-phase1.md` (Tasks 1–10)
- **Standards axis:** `CLAUDE.md` plus the Fowler smell baseline (`code-review` skill)
- **Reviewed:** 2026-09-26, automated run (scheduled task `vault-git-phase1-review-phase2-kickoff`)

## Verdict: **ready to merge**

No blocking findings. All 10 plan tasks landed, each with its planned commit message. R1, A1 and
A5 are implemented. The suite is green. The findings below are non-blocking; findings 1–4 are
worth fixing before or alongside phase 2.

## Checks run directly

| Check | Result |
|---|---|
| `uv run --group dev pytest test/ -q` | **2173 passed, 14 skipped**, 1 unrelated Starlette deprecation warning (35 s) |
| `test/test_no_git_writes.py` exists, `ALLOWED` empty | Yes. `ALLOWED: set[str] = set()` (test/test_no_git_writes.py:31) |
| `grep -rn "commit_paths\|maybe_push\|remove_paths\|ARTMIND_VAULT_GIT_PUSH" artmind utils` | No hits |
| `sync()` reads replay and structured inputs from `git archive <head>` | Yes. `_materialize` (artmind/vault_sync.py:151) feeds both track B (:392) and track A (:420). `import_structured_text` and `ingest._write_to_neo4j` read only the directory they are given; neither falls back to `KG_DIR` or `STRUCTURED_TEXT_DIR` |
| `preflight()` runs for every mode | Yes. artmind/vault_sync.py:344 runs before the `bootstrap_synced` (:346) and `dry_run` (:363) branches |
| Sidecar carried only when the live `chunks.json` is byte-identical to the committed one | Yes. artmind/vault_sync.py:189. Tests cover both the copy case and the drop case |
| Cursor advances only on full success | Yes. artmind/vault_sync.py:454. Failure paths are tested |
| Tests assert on content sent, not counts (CLAUDE.md §3) | Yes. `test/test_vault_sync.py` records the observations passed to `_write_to_neo4j` and the CSV and manifest bytes passed to `import_structured_text` |
| `docs/vault.md`, the ingestion-helper skill and the `vault sync` docstring are updated and consistent | Yes |

## Findings (most severe first)

### 1. Medium: track B reads the table mapping and schema from the working tree, not `head`
`artmind/vault_sync.py:403` (`find_mappings` → `paths.TABLE_MAPPINGS_DIR`) and `:408`
(`load_schema` → `paths.DOMAIN_SCHEMAS_DIR`). A1 says *"Every input `vault sync` replays is read at
the fixed `head`"*. Inside a vault, `ARTMIND_HOME` places `domains/` under the vault, so an
uncommitted or half-merged mapping edit would be applied. **Fix:** materialise the matching mapping
YAML from `head` into the scratch dir, or record this as an explicit A1 exception in the spec.
Schemas are also overwritten by `init` (CLAUDE.md §2), so the mapping is the more important of the
two.

### 2. Medium: `git archive` is not byte-identical to `git show`
`artmind/vault_sync.py:164`. The spec names `git show head:<path>`. `git archive` applies
`.gitattributes` `export-ignore`/`export-subst` and working-tree conversion (eol, smudge filters
such as LFS), so the snapshot can differ from the committed blobs. This is harmless today because
artmind writes no such attributes (`grep export-ignore|filter=lfs` finds nothing). **Phase 2 (R3)
must not add `export-ignore`, `export-subst`, `text`/`eol` or `filter=` to paths under
`.artmind/data/**`.** Alternatively, pass `--worktree-attributes` with a neutralising attributes
file, or switch to `git cat-file --batch`.

### 3. Low: preflight refuses on any conflicted file, not just `.artmind/data/**`
`artmind/vault_sync.py:100`. A5 says *"unresolved conflicts under `.artmind/data/**`"*. A
conflicted human note therefore blocks sync. This is stricter than the spec, which is safe but
surprising. Separately, `.split()` on `git diff --name-only` output breaks note names that contain
spaces; use `.splitlines()`. **Fix:** scope the diff with `-- .artmind/data` (or document the
stricter rule) and use `splitlines()`.

### 4. Low: stale `justfile` comment (hard violation of CLAUDE.md §4)
`justfile:141` still says *"remove it from the graph AND the vault (git rm + commit)"*. Archive no
longer runs `git rm` or commits. CLAUDE.md §4 requires the docstring, skill and justfile to be
updated together.

### 5. Low: `manifest.json` missing at `head` gives a raw git error
`artmind/vault_sync.py:392`. If a CSV is committed without the manifest, `git archive` fails with
`pathspec … did not match`. Sync still refuses and does not advance the cursor, so this is not a
correctness bug, but the message is unhelpful and no test covers it. Phase 2's R5 (one metadata
file per table, with a legacy `manifest.json` fallback) changes this path anyway, so fix it there.

### 6. Low: no real-repo "HEAD unchanged" test for structured export and the worker
Spec §11: *"after ingest, archive, structured export and `vault sync` … HEAD and index are
unchanged"*. Ingest sync, archive and `vault sync` have real-repo tests. Structured export
(`artmind/structured/pipeline.py`) and the worker are covered only by the source-scan guard.

### 7. Low (smell): three copies of the worker-pid liveness check
`artmind/vault_sync.py:62` (`_worker_running`), `artmind/worker.py:24` and `artmind/cli.py:108`.
The copies have already diverged: only the new one treats `PermissionError` as "alive". Extract one
`worker_alive()` helper. Also, `_worker_running` uses the process-wide `paths.WORKER_PID_FILE`
rather than a path derived from `vault_dir`. That is correct only when the resolved data dir is
this vault's, which holds for normal in-vault invocation.

### 8. Info: interim `table__*` churn (expected)
`artmind/vault_sync.py:450`. Regenerated `table__*` folders now stay uncommitted, so Obsidian Git
will commit them. The next sync on another machine will replay them, and they may conflict across
machines. The plan names this as the expected interim state until R4 gitignores `table__*` in
phase 2.

### 9. Nits
- `utils/functions.py:77` annotates `cmd: "str | list[str]"`, but `test/test_run_command.py`
  passes a `Path` element (the implementation calls `str()` on each element). Widen the annotation
  to `Sequence[str | os.PathLike]`.
- `test/test_ingest_sync_cli.py` has a comment that refers to `commit_paths`, which this branch
  deletes. Reword it to state the current reason.
- `_carry_sidecar` (`artmind/vault_sync.py:176`) holds ingest's staging-layout knowledge and would
  sit more naturally in `artmind/ingest.py`, next to `EMBEDDING_SIDECAR`.
- Scope additions that are fine to keep: the `CHERRY_PICK_HEAD` preflight check (`:93`), and the
  iCloud/Dropbox and cron-pull advice in `docs/vault.md`, which is consistent with spec §8 and §9.

## Standards axis (summary)
One hard violation (finding 4). The rest are judgement-call smells: duplicated pid check (finding
7), type drift (finding 9), speculative `ALLOWED` scaffolding in the guard test (acceptable: it is
reserved for phase 5 `vault resolve` and is empty), and the stale comment (finding 9). All new
tests are in `test/`. No CLI options were added.

## Spec axis (summary)
No blocking findings. Partial on A1 (finding 1) and §11 (finding 6). A5 is stricter than the spec
(finding 3). The implementation mechanism differs from the spec's `git show` (finding 2).
